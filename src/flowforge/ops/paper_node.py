"""The paper trading node: Alpaca data -> features -> OrderFlowStrategy -> RiskGuard -> IBKR paper.

One Nautilus LiveNode with the Alpaca data client (trades, NBBO, halts), the IB execution
client, the FeatureActor and OrderFlowStrategy (GuardedStrategy). Every order goes through
RiskGuard and then the Nautilus RiskEngine (never bypassed). It refuses to start unless the
IB settings say paper (port, account "DU...", IB_TRADING_MODE=paper), the same guards as
ib_paper_check. There is no live-money path in this file.

Which setups trade is not a choice made here: only setups that passed the Phase 5
walk-forward (reports/phase5/walkforward.json) can be enabled, each with the stop/target
the last training fold picked. With no passing setup the node runs in shadow: it computes
features, journals every signal as "not enabled", and places no entries (ADR-0016).

Usage:
    python -m flowforge.ops.paper_node --preflight          # checks only, no connection
    python -m flowforge.ops.paper_node                      # run until stopped (SIGTERM)
    python -m flowforge.ops.paper_node --setups H4 --feed sip

Settings come from the environment (.env): IB_HOST, IB_PORT, IB_CLIENT_ID, IB_ACCOUNT_ID,
IB_TRADING_MODE, ALPACA_API_KEY_ID, ALPACA_API_SECRET_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import os
import signal
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from flowforge.data.ticks import VENUE, instrument_id
from flowforge.ops.ib_paper_check import IB_VENUE, IbSettings, NotPaperError, assert_paper, mask_account
from flowforge.signals.setups import SETUPS

TRADER_ID = "FLOWFORGE-001"
DATA_CLIENT = "ALPACA"
# The IB data client only loads instruments into the cache (see build_node). It needs its own IB
# connection id; the node's id + 100 stays clear of the node/watchdog (11) and the check (91).
IB_DATA_CLIENT_ID_OFFSET = 100
DEFAULT_SYMBOLS = "SPY,QQQ,AAPL,MSFT,NVDA"  # the Phase 5 study names
LIVE_FEEDS = ("sip", "iex")


class PaperRefused(RuntimeError):
    """The node won't start: a setting or an input says it isn't safe or isn't allowed."""


@dataclass
class Paths:
    risk_limits: Path = Path("config/risk_limits.yaml")
    jev: Path = Path("config/jev.yaml")
    walkforward: Path = Path("reports/phase5/walkforward.json")
    ticks: Path = Path("data/ticks")
    state: Path = Path("data/state/risk_state.json")
    journal: Path = Path("data/journal/node")
    run_dir: Path = Path(os.environ.get("FLOWFORGE_RUN_DIR", "/var/run/flowforge"))

    @property
    def heartbeat(self) -> Path:
        return self.run_dir / "heartbeat.json"

    @property
    def tape(self) -> Path:
        return self.run_dir / "tape.json"


@dataclass
class Plan:
    """What the node will do, decided before it connects. Journaled at start."""

    symbols: list[str]
    feed: str
    setups: dict[str, tuple[str, str]]  # enabled setups -> (stop mode, target mode)
    adv: dict[str, float]  # average daily dollar volume per symbol (0 = refuse entries)
    account: str  # masked
    notes: list[str] = field(default_factory=list)

    @property
    def shadow(self) -> bool:
        return not self.setups

    def as_dict(self) -> dict:
        return asdict(self) | {"shadow": self.shadow}


# -- which setups may trade --------------------------------------------------------------------
def setups_for_paper(
    walkforward: Path, requested: list[str] | None
) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Setups that passed the walk-forward, with the last fold's chosen stop/target.

    `requested` narrows the list; asking for a setup that didn't pass is refused."""
    notes: list[str] = []
    if not walkforward.exists():
        if requested:
            raise PaperRefused(f"no walk-forward result at {walkforward}: no setup may be enabled")
        return {}, [f"no walk-forward result at {walkforward}: shadow mode, no entries"]
    try:
        data = json.loads(walkforward.read_text(encoding="utf-8"))
        oos, folds = data["oos"], data["folds"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PaperRefused(f"walk-forward result {walkforward} is unreadable: {exc}") from exc
    passed = sorted(s for s, o in oos.items() if isinstance(o, dict) and o.get("passes"))
    if requested:
        refused = [s for s in requested if s not in passed]
        if refused:
            raise PaperRefused(
                f"{', '.join(refused)} did not pass the walk-forward; only {passed or 'none'} may trade"
            )
        passed = [s for s in passed if s in requested]
    unknown = [s for s in passed if s not in SETUPS]
    if unknown:  # e.g. a Phase 5b setup: the paper strategy only knows H1-H5 so far
        raise PaperRefused(f"{', '.join(unknown)} passed, but the paper strategy can't trade it yet")
    out: dict[str, tuple[str, str]] = {}
    for s in passed:
        chosen = (folds[-1].get("chosen") or {}).get(s, {}).get("config") if folds else None
        if not chosen or "/" not in chosen:
            raise PaperRefused(f"walk-forward result has no stop/target for {s} in its last fold")
        stop, _, target = chosen.partition("/")
        out[s] = (stop, target)
    if not out:
        notes.append("no setup passed the walk-forward: shadow mode, no entries")
    return out, notes


@functools.lru_cache(maxsize=32)
def prior_session(ticks: Path, symbol: str, today: date) -> Any:
    """The last downloaded session before today (levels, close, volume, size thresholds), or
    None. Read once: it serves both the volume figure and the feature engine's seed."""
    from flowforge.features.replay import prior_day

    try:
        return prior_day(ticks, symbol, today)
    except Exception:  # noqa: BLE001 - a damaged download means no prior day, not a crash
        return None


def daily_dollar_volume(ticks: Path, symbol: str, today: date) -> float:
    """The last downloaded session's close x volume; 0 when nothing is downloaded, which
    makes RiskGuard refuse entries on that name (fail closed)."""
    prior = prior_session(ticks, symbol, today)
    return float(prior.close * prior.volume) if prior else 0.0


def make_plan(
    settings: IbSettings,
    paths: Paths,
    symbols: list[str],
    feed: str,
    requested: list[str] | None,
    allow_iex: bool,
    today: date,
) -> Plan:
    assert_paper(settings)
    if feed not in LIVE_FEEDS:
        raise PaperRefused(f"feed {feed!r} is not a live market feed; use one of {LIVE_FEEDS}")
    notes: list[str] = []
    if feed == "iex":
        if not allow_iex:
            raise PaperRefused(
                "feed 'iex' sees only IEX trades (a few % of volume) while the walk-forward used"
                " SIP; pass --allow-iex to run anyway (results won't be comparable)"
            )
        notes.append("IEX-only feed: features and results are not comparable with the SIP walk-forward")
    setups, more = setups_for_paper(paths.walkforward, requested)
    notes += more
    adv = {s: daily_dollar_volume(paths.ticks, s, today) for s in symbols}
    notes += [f"{s}: no downloaded prior session, entries refused" for s, v in adv.items() if v <= 0]
    return Plan(symbols, feed, setups, adv, mask_account(settings.account_id), notes)


# -- the node ------------------------------------------------------------------------------------
def instrument_provider_config(symbols: list[str]) -> Any:
    """IB instruments with the same ids as the Alpaca data (SYMBOL.XNAS), so quotes, features
    and orders all name one instrument. UNVERIFIED on a live gateway until drill D1 passes
    (docs/research/16-ib-paper-execution.md)."""
    from nautilus_trader.adapters.interactive_brokers import InteractiveBrokersInstrumentProviderConfig

    return InteractiveBrokersInstrumentProviderConfig(
        load_ids={instrument_id(s) for s in symbols},
        convert_exchange_to_mic_venue=True,
        symbol_to_mic_venue={s: VENUE for s in symbols},
    )


def build_node(settings: IbSettings, plan: Plan, paths: Paths, alpaca_factory: Any = None) -> Any:
    from nautilus_trader.adapters.interactive_brokers import (
        InteractiveBrokersDataClientConfig,
        InteractiveBrokersDataClientFactory,
        InteractiveBrokersExecutionClientConfig,
        InteractiveBrokersExecutionClientFactory,
    )
    from nautilus_trader.common import Environment
    from nautilus_trader.live import LiveNode, RoutingConfig
    from nautilus_trader.model import TraderId

    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.data.alpaca_data_client import AlpacaDataClientConfig, AlpacaDataClientFactory
    from flowforge.nautilus_config import risk_engine_config

    assert_paper(settings)  # again, right before anything can connect
    limits = load_risk_limits(paths.risk_limits).limits
    exec_config = InteractiveBrokersExecutionClientConfig(
        host=settings.host,
        port=settings.port,
        client_id=settings.client_id,
        account_id=settings.account_id,
        instrument_provider=instrument_provider_config(plan.symbols),
    )
    # Only the IB data client puts its loaded instruments into the cache; the exec client keeps
    # them to itself (drill D1 failed 2026-10-29 without this). It registers for default routing,
    # but every subscription names SYMBOL.XNAS, which routes to the Alpaca client's venue.
    ib_data_config = InteractiveBrokersDataClientConfig(
        host=settings.host,
        port=settings.port,
        client_id=settings.client_id + IB_DATA_CLIENT_ID_OFFSET,
        instrument_provider=instrument_provider_config(plan.symbols),
    )
    data_config = AlpacaDataClientConfig(feed=plan.feed, journal_path="data/live/journal.jsonl")
    return (
        LiveNode.builder("flowforge-paper", TraderId(TRADER_ID), Environment.LIVE)
        .with_risk_engine_config(risk_engine_config(limits))
        .with_reconciliation(True)
        .add_data_client(DATA_CLIENT, alpaca_factory or AlpacaDataClientFactory(), data_config)
        .add_data_client(IB_VENUE, InteractiveBrokersDataClientFactory(), ib_data_config)
        # The IB client is the only execution client, so every order (SYMBOL.XNAS) goes to it.
        .add_exec_client(
            IB_VENUE, InteractiveBrokersExecutionClientFactory(), exec_config, RoutingConfig(default=True)
        )
        .build()
    )


def build_trading(plan: Plan, paths: Paths, today: date) -> tuple[Any, Any, Any]:
    """FeatureActor -> OrderFlowStrategy, wired as in the backtests (research/crosscheck.py)."""
    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.execution.journal import Journal
    from flowforge.features.actor import FeatureActor
    from flowforge.features.config import FeatureConfig
    from flowforge.gate.jev import JevGate, load_config
    from flowforge.risk.guard import InstrumentInfo, RiskGuard
    from flowforge.risk.killswitch import KillSwitch
    from flowforge.risk.state import RiskState
    from flowforge.strategies.orderflow import OrderFlowStrategy

    limits = load_risk_limits(paths.risk_limits).limits
    guard = RiskGuard(limits, RiskState(limits, paths.state), KillSwitch(limits.kill_switch.file_flag))
    journal = Journal(paths.journal)
    jev = load_config(paths.jev)
    strategy = OrderFlowStrategy(
        plan.symbols,
        guard,
        {s: InstrumentInfo(s) for s in plan.symbols},
        journal,
        setups=plan.setups,
        gate=JevGate(jev) if jev.enabled else None,
        tape_path=str(paths.tape),
        avg_daily_dollar_volume=plan.adv,
        heartbeat_path=paths.heartbeat,
        venue=IB_VENUE,  # the IB account's venue, for equity and cash
        trader_name=TRADER_ID,
    )
    config = FeatureConfig()
    actor = FeatureActor(plan.symbols, config, client_id=DATA_CLIENT, on_record=strategy.on_feature)
    for s in plan.symbols:
        prior = prior_session(paths.ticks, s, today)
        if prior is not None:
            actor.engine.seed_prior_day(s, today.isoformat(), prior)
    return actor, strategy, journal


async def _run_until_stopped(node: Any, seconds: float | None) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, node.handle().stop)
        except (NotImplementedError, RuntimeError):  # Windows without Docker; Ctrl+C still works
            pass
    task = asyncio.create_task(node.run_async())
    if seconds is None:
        await task
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except TimeoutError:
        node.handle().stop()
        await task


def run(settings: IbSettings, plan: Plan, paths: Paths, today: date, seconds: float | None = None) -> int:
    node = build_node(settings, plan, paths)
    actor, strategy, journal = build_trading(plan, paths, today)
    journal.write(_now_ns(), "paper_node_start", **plan.as_dict())
    node.add_actor(actor)
    node.add_strategy(strategy)
    try:
        asyncio.run(_run_until_stopped(node, seconds))
    except Exception as exc:
        journal.write(_now_ns(), "error", where="paper_node", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        journal.write(_now_ns(), "paper_node_stop")
        node.dispose()
        journal.close()
    return 0


def _now_ns() -> int:
    import time

    return time.time_ns()


def main(argv: list[str] | None = None) -> int:
    from flowforge.ops.ib_paper_check import settings_from_env
    from flowforge.risk.calendar import et_date

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    p.add_argument("--feed", default=os.environ.get("ALPACA_FEED", "iex"), choices=["sip", "iex", "test"])
    p.add_argument("--allow-iex", action="store_true", help="run on the IEX-only feed (not comparable)")
    p.add_argument("--setups", default="", help="narrow the walk-forward's passing setups, e.g. H4")
    p.add_argument("--preflight", action="store_true", help="print the plan and exit; connects to nothing")
    p.add_argument("--seconds", type=float, default=None, help="stop after this long (drills)")
    p.add_argument("--walkforward", type=Path, default=Paths.walkforward)
    a = p.parse_args(argv)

    paths = Paths(walkforward=a.walkforward)
    today = et_date(_now_ns())
    try:
        settings = settings_from_env()
        requested = [s.strip() for s in a.setups.split(",") if s.strip()] or None
        plan = make_plan(settings, paths, a.symbols.split(","), a.feed, requested, a.allow_iex, today)
    except (NotPaperError, PaperRefused) as exc:
        print(f"paper_node: refusing to start: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(plan.as_dict(), indent=2))
    if a.preflight:
        return 0
    return run(settings, plan, paths, today, a.seconds)


if __name__ == "__main__":
    sys.exit(main())
