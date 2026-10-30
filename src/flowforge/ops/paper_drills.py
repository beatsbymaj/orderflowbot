"""IBKR paper drills (Phase 7): prove on the real paper gateway what Phase 4 proved on the
simulator.

Each drill trades at most 1 share of SPY, and only through GuardedStrategy -> RiskGuard ->
the Nautilus RiskEngine, with the node's real risk state and kill switch (so drills count
toward the trades-per-day and day-trade limits like any trade). Paper only: the same
guards as ib_paper_check refuse anything else. Run them with the trading node and the
watchdog service stopped (docs/runbooks/paper-drills.md).

    instruments  connect, load SPY/QQQ/AAPL/MSFT/NVDA from IBKR, check ids and tick size; no orders
    round-trip   buy 1 share (IOC), see the stop accepted at IBKR, hold 20 s, sell; latencies
    stop         buy 1 share with a stop 2 cents under the bid and wait for it to trigger
    takeover     buy 1 share, crash the node, let the watchdog cancel the stop and sell
    restart      buy 1 share, crash the node, start a fresh node on the shares it doesn't know:
                 it must halt and turn the kill switch on; then the watchdog flattens
    all          the five above in that order, one report each. Before every drill IBKR must
                 show the account empty; a kill switch a drill turned on is turned off again
                 by ops.auto_release (ADR-0017). Stops at the first failure.

Every drill writes reports/paper/drill-<name>-<time>.json and .md and journals to
data/journal/drills/. A drill that turns the kill switch on leaves it on: release it with
`riskctl release` after reading the report (that is part of the drill), or let `all` do it
through ops.auto_release when risk_limits.yaml allows it.

Usage:
    python -m flowforge.ops.paper_drills instruments
    python -m flowforge.ops.paper_drills round-trip
    python -m flowforge.ops.paper_drills all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from flowforge.execution.strategy import GuardedStrategy
from flowforge.ops.ib_paper_check import IB_VENUE, IbSettings, assert_paper
from flowforge.risk.calendar import NS
from flowforge.risk.guard import EntryRequest
from flowforge.risk.orders import Phase

DRILL_SYMBOL = "SPY"
DRILLS = ("instruments", "round-trip", "stop", "takeover", "restart")
PASSED_FILE = "drills-passed.json"
MAX_TRIES = 3  # an IOC can miss in a fast market; try again a few seconds later
RETRY_S = 5
STOP_ACK_MAX_S = 5.0  # ADR-0013: the stop must be at the broker within 5 s of the fill
FRESH_QUOTE_S = 2
WARM_MINUTES = 2  # finished minutes of tape before the first entry


# -- the strategy every order drill runs ------------------------------------------------------
class DrillStrategy(GuardedStrategy):
    """Buys 1 share through RiskGuard and walks it through one drill.

    drill: "round-trip" (hold, then sell), "stop" (wait for the stop), "crash" (once the
    stop is accepted, call `crash`: the node dies with shares and a resting stop), or
    "observe" (no orders: a restarted node watching shares it doesn't know)."""

    def __init__(
        self,
        symbols: list[str],
        guard: Any,
        universe: Any,
        journal: Any = None,
        *,
        drill: str = "round-trip",
        hold_s: float = 20,
        stop_gap: float | None = None,  # dollars under the bid; default 0.5 % (round-trip, crash)
        give_up_s: float = 600,
        crash: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbols, guard, universe, journal, **kwargs)
        self.symbol = symbols[0]
        self.drill = drill
        self.hold_s = hold_s
        self.stop_gap = stop_gap if stop_gap is not None else (0.02 if drill == "stop" else None)
        self.give_up_s = give_up_s
        self.crash = crash or (lambda: os._exit(9))  # no cleanup, like a power cut
        self.tries = 0
        self.intent: str | None = None
        self.next_try_ns = 0
        self.protected_ns: int | None = None
        self.stop_crossed_ns: int | None = None
        self.outcome: str | None = None  # set when the drill is over

    def _try_entry(self, now: int) -> None:
        st = self.market[self.symbol]
        q = st.quote
        if q is None or now - q.ts_ns > FRESH_QUOTE_S * NS or now < self.next_try_ns:
            return
        if len(st.volumes) < WARM_MINUTES:  # RiskGuard sizes by recent 1-minute volume
            return
        gap = self.stop_gap if self.stop_gap is not None else max(0.05, round(q.bid * 0.005, 2))
        self.tries += 1
        self.intent = f"drill-{self.drill}-{now}"
        stop, target = round(q.bid - gap, 2), round(q.ask * 1.01, 2)
        self._journal("drill_entry", intent=self.intent, try_no=self.tries, bid=q.bid, ask=q.ask, stop=stop)
        decision = self.request_entry(EntryRequest(self.intent, self.symbol, stop, target, max_quantity=1))
        if not decision.allowed:
            self._finish("refused by risk: " + "; ".join(decision.reasons))

    def _finish(self, outcome: str) -> None:
        if self.outcome is None:
            self.outcome = outcome
            self._journal("drill_done", drill=self.drill, outcome=outcome)

    def on_market(self, symbol: str) -> None:
        trade = self.manager.trades.get(self.intent) if self.intent else None
        q = self.market[symbol].quote
        if (
            trade is not None
            and self.stop_crossed_ns is None
            and trade.stop_id is not None
            and q.bid <= trade.stop_price
        ):
            self.stop_crossed_ns = self.clock.timestamp_ns()
            self._journal("drill_stop_crossed", intent=self.intent, bid=q.bid, stop=trade.stop_price)

    def on_risk_tick(self, now: int) -> None:
        if self.outcome is not None or self.drill == "observe":
            return
        trade = self.manager.trades.get(self.intent) if self.intent else None
        if trade is None:
            self._try_entry(now)
            return
        if trade.phase == Phase.FAILED:  # the IOC didn't fill: nothing held
            if self.tries >= MAX_TRIES:
                self._finish(f"entry not filled after {self.tries} tries")
            else:
                self.intent, self.next_try_ns = None, now + RETRY_S * NS
            return
        if trade.phase == Phase.OPEN and trade.stop_acked and self.protected_ns is None:
            self.protected_ns = now
            self._journal("drill_protected", intent=self.intent, stop=trade.stop_price)
            if self.drill == "crash":
                self._journal("drill_crash", intent=self.intent)
                self.journal.close()
                self.crash()
                return
        if trade.done:
            self._finish("closed")
            return
        held_s = (now - self.protected_ns) / NS if self.protected_ns is not None else 0
        if self.drill == "round-trip" and self.protected_ns is not None and held_s >= self.hold_s:
            if trade.phase == Phase.OPEN:
                self.request_exit(self.intent, "drill done")
        elif self.drill == "stop" and self.protected_ns is not None and held_s >= self.give_up_s:
            if trade.phase == Phase.OPEN:
                self._journal("drill_stop_timeout", intent=self.intent, waited_s=held_s)
                self.request_exit(self.intent, "drill: stop never triggered")


# -- reading a drill back from its journal ------------------------------------------------------
@dataclass
class DrillReport:
    drill: str
    started: str
    passed: bool = False
    checks: list[dict] = field(default_factory=list)
    timings_ms: dict[str, float | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})

    def finish(self) -> DrillReport:
        self.passed = bool(self.checks) and all(c["ok"] is not False for c in self.checks)
        return self

    def markdown(self) -> str:
        lines = [
            f"# Paper drill: {self.drill}",
            "",
            f"Started {self.started}. **{'PASS' if self.passed else 'FAIL'}**",
            "",
        ]
        lines += ["| Check | Result | Detail |", "|---|---|---|"]
        for c in self.checks:
            mark = {True: "ok", False: "**FAIL**", None: "info"}[c["ok"]]
            lines.append(f"| {c['name']} | {mark} | {c['detail']} |")
        if self.timings_ms:
            lines += ["", "| Timing | ms |", "|---|---|"]
            lines += [f"| {k} | {'' if v is None else f'{v:.0f}'} |" for k, v in self.timings_ms.items()]
        if self.notes:
            lines += ["", *[f"- {n}" for n in self.notes]]
        return "\n".join(lines) + "\n"


def _ms(a: dict | None, b: dict | None) -> float | None:
    return (int(b["ts_ns"]) - int(a["ts_ns"])) / 1e6 if a and b else None


def _first(records: list[dict], event: str, **match: Any) -> dict | None:
    return next(
        (r for r in records if r["event"] == event and all(r.get(k) == v for k, v in match.items())), None
    )


def order_timings(records: list[dict], intent: str) -> dict[str, float | None]:
    """Entry submit -> accepted -> filled, fill -> stop accepted, exit submit -> filled."""
    sub = _first(records, "order_submit", intent=intent, role="entry")
    entry = sub["order"] if sub else None
    stop_sub = _first(records, "stop_submit", intent=intent)
    stop = stop_sub["order"] if stop_sub else None
    exit_sub = _first(records, "exit_submit", intent=intent)
    ex = exit_sub["order"] if exit_sub else None
    entry_fill = _first(records, "fill", order=entry) if entry else None
    return {
        "entry submit -> accepted": _ms(sub, _first(records, "order_accepted", order=entry))
        if entry
        else None,
        "entry submit -> filled": _ms(sub, entry_fill),
        "entry fill -> stop accepted": _ms(entry_fill, _first(records, "order_accepted", order=stop))
        if stop
        else None,
        "exit submit -> filled": _ms(exit_sub, _first(records, "fill", order=ex)) if ex else None,
    }


def order_drill_report(
    drill: str, started: str, records: list[dict], positions: dict[str, int]
) -> DrillReport:
    rep = DrillReport(drill, started)
    entries = [r for r in records if r["event"] == "drill_entry"]
    intent = entries[-1]["intent"] if entries else None
    done = _first(records, "drill_done")
    rep.check(
        "Entry sent through RiskGuard",
        bool(intent),
        f"{len(entries)} tries" if entries else "no quote / no entry",
    )
    if intent is None:
        return rep.finish()
    decision = _first(records, "risk_decision", intent=intent)
    rep.check(
        "RiskGuard allowed 1 share",
        bool(decision and decision.get("allowed")),
        "; ".join(decision.get("reasons", [])) if decision else "",
    )
    rep.timings_ms = order_timings(records, intent)
    fills = [r for r in records if r["event"] == "fill"]
    bought = sum(r["quantity"] for r in fills if r.get("side") == "BUY")
    sold = sum(r["quantity"] for r in fills if r.get("side") == "SELL")
    rep.check("IOC entry filled", bought == 1, f"bought {bought}")
    ack = rep.timings_ms.get("entry fill -> stop accepted")
    rep.check(
        "Stop accepted at IBKR within 5 s",
        ack is not None and ack <= STOP_ACK_MAX_S * 1000,
        "" if ack is None else f"{ack:.0f} ms",
    )
    rejects = [r for r in records if r["event"] in ("order_rejected", "order_denied")]
    rep.check("No rejected or denied orders", not rejects, "; ".join(str(r.get("reason")) for r in rejects))
    if drill in ("round-trip", "stop"):
        rep.check("Sold exactly what was bought", sold == bought, f"sold {sold}")
        rep.check(
            "Flat at the end", not positions.get(DRILL_SYMBOL), f"position {positions.get(DRILL_SYMBOL, 0)}"
        )
        rep.check(
            "Drill finished",
            bool(done) and done.get("outcome") == "closed",
            done.get("outcome", "") if done else "timed out",
        )
    if drill == "stop":
        crossed = _first(records, "drill_stop_crossed", intent=intent)
        stop_order = (_first(records, "stop_submit", intent=intent) or {}).get("order")
        stop_fill = _first(records, "fill", order=stop_order) if stop_order else None
        rep.check(
            "Stop triggered at IBKR",
            stop_fill is not None,
            "" if stop_fill else "sold by the timeout exit instead",
        )
        rep.timings_ms["bid at stop -> stop filled"] = _ms(crossed, stop_fill)
        if crossed and stop_fill:
            slip = round(crossed["stop"] - stop_fill["price"], 4)
            rep.notes.append(
                f"stop {crossed['stop']:.2f}, filled {stop_fill['price']:.4f}"
                f" (slippage {slip:+.4f} per share)"
            )
    alerts = [r for r in records if r["event"] == "alert" and r.get("level") == "critical"]
    if drill in ("round-trip", "stop"):
        rep.check("No critical alerts", not alerts, "; ".join(r["message"] for r in alerts))
    return rep.finish()


# -- running drills on the paper gateway --------------------------------------------------------
@dataclass
class DrillPaths:
    journal: Path = Path("data/journal/drills")
    reports: Path = Path("reports/paper")


def _journal_records(journal: Any) -> list[dict]:
    return sorted(journal.records, key=lambda r: int(r["ts_ns"]))


def run_order_drill(
    settings: IbSettings, drill: str, seconds: float, paths: Any = None
) -> tuple[DrillReport, list[dict]]:
    """Start the paper node's data + IB clients with a DrillStrategy instead of the signals."""
    from flowforge.execution.journal import Journal
    from flowforge.ops import paper_node as pn
    from flowforge.risk.calendar import et_date

    assert_paper(settings)
    node_paths = paths or pn.Paths()
    today = et_date(time.time_ns())
    plan = pn.Plan([DRILL_SYMBOL], os.environ.get("ALPACA_FEED", "iex"), {}, {}, "")
    plan.adv = {DRILL_SYMBOL: pn.daily_dollar_volume(node_paths.ticks, DRILL_SYMBOL, today)}
    node = pn.build_node(settings, plan, node_paths)
    guard, limits_universe = _guard(node_paths)
    journal = Journal(DrillPaths.journal, keep=True)
    strategy = DrillStrategy(
        [DRILL_SYMBOL],
        guard,
        limits_universe,
        journal,
        drill=drill,
        avg_daily_dollar_volume=plan.adv,
        heartbeat_path=node_paths.heartbeat,
        venue=IB_VENUE,
        trader_name=pn.TRADER_ID,
        order_id_tag="FF",
    )
    node.add_strategy(strategy)
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    journal.write(time.time_ns(), "drill_start", drill=drill, feed=plan.feed)

    async def go() -> None:
        task = asyncio.create_task(node.run_async())
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and strategy.outcome is None:
            await asyncio.sleep(0.5)
        await asyncio.sleep(3)  # let the last events arrive
        node.handle().stop()
        await task

    try:
        asyncio.run(go())
        positions = strategy.broker_positions()
    finally:
        node.dispose()
    records = _journal_records(journal)
    journal.close()
    return order_drill_report(drill, started, records, positions), records


def _guard(paths: Any) -> tuple[Any, dict]:
    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.risk.guard import InstrumentInfo, RiskGuard
    from flowforge.risk.killswitch import KillSwitch
    from flowforge.risk.state import RiskState

    limits = load_risk_limits(paths.risk_limits).limits
    guard = RiskGuard(limits, RiskState(limits, paths.state), KillSwitch(limits.kill_switch.file_flag))
    return guard, {DRILL_SYMBOL: InstrumentInfo(DRILL_SYMBOL)}


def instruments_report(cache: Any, symbols: list[str], started: str) -> DrillReport:
    from flowforge.data.ticks import instrument_id

    rep = DrillReport("instruments", started)
    for s in symbols:
        inst = cache.instrument(instrument_id(s))
        if inst is None:
            rep.check(
                f"{s} loaded as {instrument_id(s)}", False, "not in the cache: the venue mapping didn't work"
            )
            continue
        tick = float(inst.price_increment.as_double())
        rep.check(f"{s} loaded as {inst.id}", True, f"tick {tick}, lot {inst.lot_size}")
        rep.check(f"{s} tick size 0.01", abs(tick - 0.01) < 1e-9, str(tick))
        info = getattr(inst, "info", None)
        if info:
            rep.notes.append(f"{s}: {json.dumps(info, default=str)[:400]}")
    return rep.finish()


def run_instruments(settings: IbSettings, seconds: float = 45.0) -> DrillReport:
    from flowforge.ops import ib_paper_check as chk
    from flowforge.ops import paper_node as pn

    assert_paper(settings)
    symbols = pn.DEFAULT_SYMBOLS.split(",")
    plan = pn.Plan(symbols, "iex", {}, {}, "")
    node = pn.build_node(settings, plan, pn.Paths())
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        asyncio.run(chk._run_for(node, seconds))
        return instruments_report(node.cache, symbols, started)
    finally:
        node.dispose()


def _child(args: list[str], timeout_s: float) -> int:
    """Run a drill step in its own process, so it can die like a crashed node."""
    cmd = [sys.executable, "-m", "flowforge.ops.paper_drills", *args]
    try:
        return subprocess.run(cmd, timeout=timeout_s, check=False).returncode  # noqa: S603 - our own module
    except subprocess.TimeoutExpired:
        return -1


def _drill_records(since_ns: int) -> list[dict]:
    from flowforge.ops.ledger import read_journal

    read = read_journal(DrillPaths.journal.parent, None, ["drills"])
    return [r for r in read.records if int(r["ts_ns"]) >= since_ns]


def watchdog_until_flat(
    settings: IbSettings, wait_s: float = 90.0, flattener: Any = None
) -> tuple[Any, float | None]:
    """Tick the real watchdog once a second until it has flattened (or `wait_s` runs out).
    Returns its FlattenReport and the seconds it took to decide."""
    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.execution.journal import Journal
    from flowforge.ops import paper_node as pn
    from flowforge.ops import watchdog as wd
    from flowforge.risk.killswitch import KillSwitch

    assert_paper(settings)
    limits = load_risk_limits(pn.Paths().risk_limits).limits
    reports: list[Any] = []

    def flatten(reason: str) -> Any:
        rep = (flattener or wd.flatten_via_ib)(reason)
        reports.append(rep)
        return rep

    dog = wd.Watchdog(
        KillSwitch(limits.kill_switch.file_flag),
        pn.Paths().heartbeat,
        limits.kill_switch.flatten_on_kill,
        flatten,
        Journal(DrillPaths.journal),
    )
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        decision = dog.tick()
        if decision.action == "flatten":
            return reports[-1], time.monotonic() - t0
        time.sleep(1)
    return None, None


def run_crash_then(settings: IbSettings, after: str) -> tuple[DrillReport, list[dict]]:
    """takeover / restart: a child node buys 1 share and dies with its stop resting at IBKR."""
    from flowforge.ops import watchdog as wd

    assert_paper(settings)
    since = time.time_ns()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rep = DrillReport(after, started)
    code = _child(["_crash"], 900)
    crashed_at = time.monotonic()
    records = _drill_records(since)
    crash = _first(records, "drill_crash")
    rep.check(
        "Node bought 1 share, stop accepted, then crashed",
        crash is not None and code == 9,
        f"exit code {code}",
    )
    if crash is None:
        return rep.finish(), records
    intent = crash["intent"]
    rep.timings_ms = order_timings(records, intent)

    if after == "restart":
        observed = time.time_ns()
        code = _child(["_observe", "--seconds", "60"], 180)
        late = [r for r in _drill_records(observed)]
        mismatch = next(
            (r for r in late if r["event"] == "alert" and "position mismatch" in r.get("message", "")), None
        )
        kill = _first(late, "kill_engaged")
        sent = [r for r in late if r["event"] in ("order_submit", "stop_submit", "exit_submit")]
        rep.check(
            "Restarted node saw shares it doesn't know",
            mismatch is not None,
            mismatch["message"] if mismatch else "no mismatch alert",
        )
        rep.check(
            "It halted and turned the kill switch on",
            kill is not None,
            kill.get("reason", "") if kill else "",
        )
        rep.check("It sent no orders of its own", not sent, f"{len(sent)} orders" if sent else "")
        if mismatch:
            rep.timings_ms["restart -> mismatch found"] = (int(mismatch["ts_ns"]) - observed) / 1e6
        records += late

    flat, took = watchdog_until_flat(settings)
    rep.check("Watchdog flattened", flat is not None, "" if flat else "it never decided to flatten")
    if flat is not None:
        rep.timings_ms["watchdog decision"] = round(took * 1000) if took is not None else None
        rep.timings_ms["crash -> watchdog flattened"] = round((time.monotonic() - crashed_at) * 1000)
        rep.check("It cancelled the node's stop", len(flat.cancelled) >= 1, ", ".join(flat.cancelled))
        rep.check("It sold exactly 1 share", flat.sold == {DRILL_SYMBOL: 1}, json.dumps(flat.sold))
        rep.check(
            "Account flat afterwards",
            flat.flat,
            "; ".join(flat.problems) or json.dumps(flat.remaining_positions),
        )
    rep.notes.append(
        "The kill switch is now ON (the watchdog turns it on when it takes over). Read this report, then"
        " release it: docker compose run --rm riskctl release"
    )
    rep.notes.append(
        f"Watchdog limits: heartbeat stale after {wd.HEARTBEAT_STALE_S} s, kill grace {wd.KILL_GRACE_S} s."
    )
    return rep.finish(), records + _drill_records(time.time_ns() - 120 * NS)


def write_report(rep: DrillReport, out: Path, records: list[dict] | None = None) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stem = f"drill-{rep.drill}-{rep.started.replace(':', '')}"
    (out / f"{stem}.json").write_text(
        json.dumps(asdict(rep) | {"records": records or []}, indent=2, default=str)
    )
    path = out / f"{stem}.md"
    path.write_text(rep.markdown(), encoding="utf-8")
    return path


def _ready(settings: IbSettings, check: Any, release: Any) -> str | None:
    """None when the next drill may start: account empty at IBKR and the kill switch off."""
    from flowforge.ops.ib_paper_check import run_check
    from flowforge.ops.paper_node import Paths

    risk_limits = Paths().risk_limits
    report = (check or run_check)(settings, 30.0, risk_limits)
    if not report.ok:
        return "IBKR check failed: " + "; ".join(report.problems)
    if report.open_positions or report.open_orders:
        return (
            f"account not empty ({len(report.open_positions)} positions, "
            f"{len(report.open_orders)} open orders)"
        )
    if release is None:
        from flowforge.ops.auto_release import run as release
    decision = release(risk_limits)
    if decision.release or decision.reason == "kill switch already off":
        return None
    return f"kill switch on: {decision.reason}"


def run_all(
    settings: IbSettings, out: Path, seconds: float, check: Any = None, release: Any = None
) -> list[DrillReport]:
    """Every drill in DRILLS order, stopping at the first that fails or can't start."""
    done: list[DrillReport] = []
    for drill in DRILLS:
        blocked = _ready(settings, check, release) if drill != "instruments" else None
        if blocked:
            rep = DrillReport(drill, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            rep.check("Ready to start", False, blocked)
            done.append(rep.finish())
            write_report(rep, out)
            break
        if drill == "instruments":
            rep, records = run_instruments(settings), []
        elif drill in ("round-trip", "stop"):
            rep, records = run_order_drill(settings, drill, seconds)
        else:
            rep, records = run_crash_then(settings, drill)
        print(f"report: {write_report(rep, out, records)}")
        done.append(rep)
        if not rep.passed:
            break
    if done and done[-1].passed and len(done) == len(DRILLS):
        _ready(settings, check, release)  # the restart drill leaves the switch on; same rule
    return done


def main(argv: list[str] | None = None) -> int:
    from flowforge.ops.ib_paper_check import NotPaperError, settings_from_env

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("drill", choices=[*DRILLS, "all", "_crash", "_observe"])
    p.add_argument("--seconds", type=float, default=600.0, help="give up after this long")
    p.add_argument("--out", type=Path, default=DrillPaths.reports)
    a = p.parse_args(argv)
    try:
        settings = settings_from_env()
        assert_paper(settings)
    except NotPaperError as exc:
        print(f"paper_drills: {exc}", file=sys.stderr)
        return 2

    if a.drill in ("_crash", "_observe"):  # steps run as child processes by takeover/restart
        run_order_drill(settings, "crash" if a.drill == "_crash" else "observe", a.seconds)
        return 0  # the crash step only gets here if it never crashed
    if a.drill == "all":
        reps = run_all(settings, a.out, a.seconds)
        for rep in reps:
            print(f"{rep.drill}: {'PASS' if rep.passed else 'FAIL'}")
        ok = len(reps) == len(DRILLS) and all(r.passed for r in reps)
        print("All drills passed." if ok else "Drills stopped: read the last report.")
        if ok:  # the morning script runs the drills until this file exists
            (a.out / PASSED_FILE).write_text(json.dumps({"passed_at": reps[-1].started}))
        return 0 if ok else 1
    records: list[dict] = []
    if a.drill == "instruments":
        rep = run_instruments(settings)
    elif a.drill in ("round-trip", "stop"):
        rep, records = run_order_drill(settings, a.drill, a.seconds)
    else:
        rep, records = run_crash_then(settings, a.drill)
    path = write_report(rep, a.out, records)
    print(rep.markdown())
    print(f"report: {path}")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
