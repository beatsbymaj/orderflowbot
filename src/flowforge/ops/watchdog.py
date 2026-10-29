"""Watchdog: a separate process that can cancel and flatten without the trading node.

It reads two files every second: the kill switch flag and the node's heartbeat
(execution/heartbeat.py). It acts in two cases:

1. Kill switch on (and `flatten_on_kill`). The node gets the first KILL_GRACE_S to
   flatten by itself (it stops sending sells after 20 s, see execution/strategy.py). If
   its heartbeat then still shows shares or open orders at the broker, or the heartbeat
   is stale, the watchdog flattens.
2. Node silent: heartbeat older than HEARTBEAT_STALE_S during the session while it last
   showed shares or open orders. The watchdog turns the kill switch on (so a node that
   wakes up won't trade) and flattens.

Flattening connects to IB Gateway with the node's own client id, which only works once
the node's connection is gone. That is deliberate: IBKR lets a client cancel only the
orders placed under its own client id (reqGlobalCancel aside), so the node's stop orders
can only be cancelled from that id. While a live node holds the id, the watchdog can't
connect and alerts a human instead; the broker stops keep protecting the shares. It never
sells while any sell order it can see is still open, and never sells more than the
broker reports held.

Paper only: the same guards as the paper check refuse anything that isn't IBKR paper.

Usage:
    python -m flowforge.ops.watchdog            # loop forever
    python -m flowforge.ops.watchdog --once     # one check, for tests and drills
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from flowforge.execution.heartbeat import Heartbeat, read_heartbeat, wall_clock_ns
from flowforge.execution.journal import Journal
from flowforge.risk.calendar import NS, session_at
from flowforge.risk.killswitch import KillSwitch

HEARTBEAT_STALE_S = 15
KILL_GRACE_S = 30
REFLATTEN_S = 60
MAX_FLATTEN_RUNS = 3
SESSION_SLACK_S = 10 * 60  # keep watching for 10 min after the close
FLATTEN_CANCEL_WAIT_S = 15
FLATTEN_FILL_WAIT_S = 30
WATCHDOG_TRADER = "FLOWFORGE-WATCHDOG"
ALIVE_FILE = "watchdog.json"


def write_alive(path: Path, now_ns: int, decision: Decision) -> None:
    """The watchdog's own heartbeat: that it is running, and what it last decided."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"ts_ns": now_ns, "action": decision.action, "reason": decision.reason}))
        os.replace(tmp, path)
    except OSError:
        pass  # never let the dashboard's file stop the watchdog


@dataclass
class FlattenReport:
    started_ns: int
    cancelled: list[str] = field(default_factory=list)
    sold: dict[str, int] = field(default_factory=dict)
    remaining_positions: dict[str, int] = field(default_factory=dict)
    remaining_orders: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def flat(self) -> bool:
        return not self.remaining_positions and not self.remaining_orders and not self.problems


@dataclass(frozen=True)
class Decision:
    action: str  # none | flatten | alert
    reason: str = ""
    engage_kill: bool = False


class Watchdog:
    def __init__(
        self,
        kill: KillSwitch,
        heartbeat_path: str | Path,
        flatten_on_kill: bool,
        flattener: Callable[[str], FlattenReport],
        journal: Journal,
    ) -> None:
        self.kill = kill
        self.heartbeat_path = Path(heartbeat_path)
        self.flatten_on_kill = flatten_on_kill
        self.flattener = flattener
        self.journal = journal
        self.kill_seen_ns: int | None = None
        self.last_flatten_ns: int | None = None
        self.runs = 0
        self.gave_up = False
        self._reported: set[str] = set()

    # -- pure decision ------------------------------------------------------------------
    def decide(self, now_ns: int, engaged: bool, beat: Heartbeat | None) -> Decision:
        stale = beat is None or beat.age_s(now_ns) > HEARTBEAT_STALE_S
        exposed = beat is not None and (bool(beat.positions) or beat.open_orders > 0)
        session = session_at(now_ns)
        in_session = (
            session is not None and session.open_ns <= now_ns <= session.close_ns + SESSION_SLACK_S * NS
        )

        if self.gave_up:
            return Decision("none")
        if self.last_flatten_ns is not None and now_ns - self.last_flatten_ns < REFLATTEN_S * NS:
            return Decision("none")
        if self.runs >= MAX_FLATTEN_RUNS:
            self.gave_up = True
            return Decision(
                "alert", f"flattened {self.runs} times and the account still isn't flat: human needed"
            )

        if engaged and self.flatten_on_kill:
            if stale:
                return Decision("flatten", "kill switch on and the node is silent")
            waited = now_ns - (self.kill_seen_ns or now_ns)
            if waited >= KILL_GRACE_S * NS and exposed:
                return Decision(
                    "flatten", f"kill switch on for {KILL_GRACE_S} s and the node is still exposed"
                )
            return Decision("none")
        if not engaged and stale and in_session and (exposed or beat is None):
            if beat is None:
                return Decision("alert", "no heartbeat from the trading node during the session")
            age = beat.age_s(now_ns)
            return Decision("flatten", f"node silent for {age:.0f} s while exposed", engage_kill=True)
        return Decision("none")

    # -- one check ----------------------------------------------------------------------
    def tick(self, now_ns: int | None = None) -> Decision:
        now_ns = now_ns if now_ns is not None else wall_clock_ns()
        engaged = self.kill.is_engaged()
        if not engaged:
            self.kill_seen_ns = None
            if self.runs and self.last_flatten_ns is not None:
                self.runs, self.gave_up = 0, False  # operator released the switch: start over
        elif self.kill_seen_ns is None:
            self.kill_seen_ns = now_ns
        beat = read_heartbeat(self.heartbeat_path)
        decision = self.decide(now_ns, engaged, beat)
        if decision.action == "alert" and decision.reason not in self._reported:
            self._reported.add(decision.reason)
            self.journal.write(now_ns, "watchdog_alert", level="critical", message=decision.reason)
        elif decision.action == "flatten":
            if decision.engage_kill:
                self.kill.engage(decision.reason, "watchdog")
            self.journal.write(now_ns, "watchdog_flatten", reason=decision.reason)
            self.last_flatten_ns = now_ns
            self.runs += 1
            report = self.flattener(decision.reason)
            self.journal.write(now_ns, "watchdog_flatten_report", flat=report.flat, **asdict(report))
        return decision


# -- the Nautilus flatten run ---------------------------------------------------------------
def _flatten_strategy_class() -> type:
    from nautilus_trader.model import OrderSide, Quantity, TimeInForce
    from nautilus_trader.trading import Strategy, StrategyConfig

    class FlattenStrategy(Strategy):
        """Cancel every open order it can see, wait for the cancels, then sell longs."""

        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            return super().__new__(cls)

        def __init__(self, report: FlattenReport, settle_s: float = 5.0) -> None:
            super().__init__(StrategyConfig(order_id_tag="WD", log_events=False))
            self.report = report
            self.settle_s = settle_s
            self.phase = "settle"
            self.phase_ns = 0
            self.sold_ids: set[str] = set()
            self.retry: dict[str, tuple[Any, int]] = {}

        def on_start(self) -> None:
            self.phase_ns = self.clock.timestamp_ns()
            self.clock.set_timer("watchdog-flatten", timedelta(seconds=1), callback=self._step)

        def _open_orders(self) -> list:
            return [o for o in self.cache.orders_open() if str(o.client_order_id) not in self.sold_ids]

        def _step(self, event: Any) -> None:
            now = event.ts_event
            elapsed = (now - self.phase_ns) / NS
            if self.phase == "settle" and elapsed >= self.settle_s:  # let reconciliation finish
                for order in self._open_orders():
                    self.report.cancelled.append(str(order.client_order_id))
                for iid in {o.instrument_id for o in self._open_orders()}:
                    self.cancel_all_orders(iid, strategy_only=False)
                self.phase, self.phase_ns = "cancel", now
            elif self.phase == "cancel":
                still = self._open_orders()
                if still and elapsed >= FLATTEN_CANCEL_WAIT_S:
                    self.report.remaining_orders = [str(o.client_order_id) for o in still]
                    self.report.problems.append(
                        f"{len(still)} orders could not be cancelled (placed under another client id?); "
                        "not selling while they are live"
                    )
                    self.phase = "done"
                elif not still:
                    self._sell_longs()
                    self.phase, self.phase_ns = "sell", now
            elif self.phase == "sell" and (not _positions(self.cache) or elapsed >= FLATTEN_FILL_WAIT_S):
                self.phase = "done"
            if self.phase == "done":
                self.clock.cancel_timer("watchdog-flatten")
                self.report.remaining_positions = _positions(self.cache)

        def _sell_longs(self) -> None:
            for position in self.cache.positions_open():
                qty = int(position.signed_qty)
                sym = position.instrument_id.symbol.value
                if qty > _positions(self.cache).get(sym, 0):
                    qty = max(0, _positions(self.cache).get(sym, 0))  # never more than the account holds
                if qty == 0:
                    continue
                if qty < 0:
                    self.report.problems.append(f"short {-qty} {sym} found: long-only system, not covering")
                    continue
                order = self.order_factory.market(
                    position.instrument_id,
                    OrderSide.SELL,
                    Quantity.from_int(qty),
                    time_in_force=TimeInForce.DAY,
                    reduce_only=True,
                    tags=["watchdog-flatten"],
                )
                self.sold_ids.add(str(order.client_order_id))
                self.retry[str(order.client_order_id)] = (position.instrument_id, qty)
                self.submit_order(order, position_id=position.id)

        def on_order_filled(self, event: Any) -> None:
            if str(event.client_order_id) in self.sold_ids:
                sym = event.instrument_id.symbol.value
                self.report.sold[sym] = self.report.sold.get(sym, 0) + int(event.last_qty.as_double())

        def on_order_denied(self, event: Any) -> None:
            oid = str(event.client_order_id)
            if "INVALID_POSITION_ID" in str(event.reason) and oid in self.retry:
                # NETTING venues key positions by strategy, so the node's (or the
                # reconciled external) position can't be named from here. Sell the same
                # quantity without naming it; the broker nets it against the account.
                iid, qty = self.retry.pop(oid)
                order = self.order_factory.market(
                    iid,
                    OrderSide.SELL,
                    Quantity.from_int(qty),
                    time_in_force=TimeInForce.DAY,
                    tags=["watchdog-flatten"],
                )
                self.sold_ids.add(str(order.client_order_id))
                self.submit_order(order)
                return
            self.report.problems.append(f"sell {oid} denied: {event.reason}")

        def on_order_rejected(self, event: Any) -> None:
            self.report.problems.append(f"sell {event.client_order_id} rejected: {event.reason}")

    return FlattenStrategy


def _positions(cache: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in cache.positions_open():
        sym = p.instrument_id.symbol.value
        out[sym] = out.get(sym, 0) + int(p.signed_qty)
    return {s: q for s, q in out.items() if q}


def flatten_via_ib(reason: str, seconds: float = 75.0) -> FlattenReport:
    """Connect to IB Gateway as the node's client id and flatten. Paper only."""
    from nautilus_trader.adapters.interactive_brokers import (
        InteractiveBrokersExecutionClientConfig,
        InteractiveBrokersExecutionClientFactory,
    )
    from nautilus_trader.common import Environment
    from nautilus_trader.live import LiveNode
    from nautilus_trader.model import TraderId

    from flowforge.ops.ib_paper_check import IB_VENUE, assert_paper, settings_from_env

    settings = settings_from_env()
    assert_paper(settings)
    report = FlattenReport(started_ns=wall_clock_ns())
    exec_config = InteractiveBrokersExecutionClientConfig(
        host=settings.host,
        port=settings.port,
        client_id=settings.client_id,
        account_id=settings.account_id,
        fetch_all_open_orders=True,
    )
    node = (
        LiveNode.builder("watchdog-flatten", TraderId(WATCHDOG_TRADER), Environment.LIVE)
        .with_reconciliation(True)
        .add_exec_client(IB_VENUE, InteractiveBrokersExecutionClientFactory(), exec_config)
        .build()
    )
    node.add_strategy(_flatten_strategy_class()(report))

    async def run() -> None:
        task = asyncio.create_task(node.run_async())
        try:
            await asyncio.sleep(seconds)
        finally:
            node.handle().stop()
            await task

    try:
        asyncio.run(run())
    except Exception as exc:  # report, don't crash the watchdog loop
        report.problems.append(f"{type(exc).__name__}: {exc}")
    finally:
        node.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    from flowforge.config.risk_limits import load_risk_limits

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    parser.add_argument(
        "--heartbeat",
        type=Path,
        default=Path(os.environ.get("FLOWFORGE_HEARTBEAT", "/var/run/flowforge/heartbeat.json")),
    )
    parser.add_argument("--journal", type=Path, default=Path("data/journal/watchdog"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    alive = args.heartbeat.with_name(ALIVE_FILE)  # read by the dashboard's Health page

    limits = load_risk_limits(args.risk_limits).limits
    dog = Watchdog(
        KillSwitch(limits.kill_switch.file_flag),
        args.heartbeat,
        limits.kill_switch.flatten_on_kill,
        flatten_via_ib,
        Journal(args.journal),
    )
    while True:
        decision = dog.tick()
        write_alive(alive, wall_clock_ns(), decision)
        if decision.action != "none":
            print(json.dumps(asdict(decision)), flush=True)
        if args.once:
            return 0
        time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
