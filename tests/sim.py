"""Tick-level Nautilus backtest harness for the chaos tests.

Builds a simulated venue, synthetic NBBO quotes and trades from a price path, optional
instrument status events, and a ScriptedStrategy (a GuardedStrategy that sends entry
requests and runs test hooks at given times). Faults are injected through the data
(gaps, drops, halts, delayed or future timestamps) and the venue settings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.model import (
    AccountType,
    AggressorSide,
    InstrumentStatus,
    MarketStatusAction,
    Money,
    OmsType,
    Price,
    Quantity,
    QuoteTick,
    TradeId,
    TradeTick,
    Venue,
)
from nautilus_trader.risk import RiskEngineConfig

from flowforge.backtest.fees import IbkrFixedFeeModel
from flowforge.config.risk_limits import RiskLimits
from flowforge.data.ticks import VENUE, instrument_id, make_tick_equity
from flowforge.execution.journal import Journal
from flowforge.execution.models import fill_model, latency_model
from flowforge.execution.strategy import GuardedStrategy
from flowforge.risk.calendar import NS
from flowforge.risk.guard import EntryRequest, InstrumentInfo, RiskGuard
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

ADV = {"SPY": 30e9, "QQQ": 15e9, "AAPL": 10e9}


@dataclass
class Tick:
    ts_ns: int
    bid: float
    ask: float
    size: float = 5_000
    ts_event_offset_ns: int = 0  # >0: stamped in the future (clock skew); <0: delayed
    trade_size: float = 2_000


def path(start_ns: int, seconds: int, price: Callable[[int], float], step_s: float = 1.0, spread=0.02, **kw):
    """One quote (+ one trade at the bid) every `step_s` seconds, price(i) giving the bid."""
    out = []
    n = int(seconds / step_s)
    for i in range(n):
        bid = round(price(i), 2)
        out.append(Tick(start_ns + int(i * step_s * NS), bid, round(bid + spread, 2), **kw))
    return out


def to_data(symbol: str, ticks: list[Tick]) -> list:
    iid = instrument_id(symbol)
    data = []
    for n, t in enumerate(ticks):
        ts_event = t.ts_ns + t.ts_event_offset_ns
        data.append(
            QuoteTick(
                iid,
                Price(t.bid, 6),
                Price(t.ask, 6),
                Quantity(t.size, 0),
                Quantity(t.size, 0),
                ts_event,
                t.ts_ns,
            )
        )
        if t.trade_size:
            data.append(
                TradeTick(
                    iid,
                    Price(t.bid, 6),
                    Quantity(t.trade_size, 0),
                    AggressorSide.NO_AGGRESSOR,
                    TradeId(f"T-{symbol}-{n}"),
                    ts_event,
                    t.ts_ns,
                )
            )
    return data


def status(symbol: str, ts_ns: int, action: MarketStatusAction) -> InstrumentStatus:
    return InstrumentStatus(instrument_id(symbol), action, ts_ns, ts_ns, reason="test")


class ScriptedStrategy(GuardedStrategy):
    def __init__(self, *args, script: list[tuple[int, object]] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.script = sorted(script or [], key=lambda x: x[0])
        self.decisions = []

    def _due(self, now: int) -> None:
        while self.script and self.script[0][0] <= now:
            _, item = self.script.pop(0)
            if isinstance(item, EntryRequest):
                self.decisions.append(self.request_entry(item))
            else:
                item(self)

    def on_market(self, symbol: str) -> None:
        self._due(self.clock.timestamp_ns())

    def on_risk_tick(self, now: int) -> None:
        self._due(now)


@dataclass
class SimResult:
    strategy: ScriptedStrategy
    journal: Journal
    state: RiskState
    engine: BacktestEngine
    positions: list = field(default_factory=list)

    def events(self, name: str) -> list[dict]:
        return self.journal.events(name)

    def alerts(self, level: str | None = None) -> list[str]:
        return [r["message"] for r in self.events("alert") if level is None or r["level"] == level]


def run_sim(
    limits: RiskLimits,
    data: list,
    script: list,
    tmp_path: Path,
    symbols=("SPY",),
    equity: float = 10_000.0,
    strategy_cls=ScriptedStrategy,
    venue_kwargs: dict | None = None,
    extra_strategies: list | None = None,
    latency_ms: float = 150,
    fill=None,
) -> SimResult:
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(stdout_level=LogLevel.ERROR),
            risk_engine=RiskEngineConfig(
                bypass=False,
                max_order_submit_rate=limits.trading.max_order_submit_rate,
                max_order_modify_rate=limits.trading.max_order_submit_rate,
            ),
        )
    )
    engine.add_venue(
        Venue(VENUE),
        OmsType.NETTING,
        AccountType.CASH,
        [Money.from_str(f"{equity} USD")],
        fill_model=fill or fill_model(prob_slippage=0.0),
        fee_model=IbkrFixedFeeModel(),
        latency_model=latency_model(latency_ms),
        **(venue_kwargs or {}),
    )
    for s in symbols:
        engine.add_instrument(make_tick_equity(s))
    engine.add_data(data)
    state = RiskState(limits, tmp_path / "risk_state.json")
    kill = KillSwitch(tmp_path / "KILL")
    guard = RiskGuard(limits, state, kill)
    journal = Journal(tmp_path / "journal", keep=True)
    universe = {s: InstrumentInfo(s) for s in symbols}
    strat = strategy_cls(
        list(symbols),
        guard,
        universe,
        journal,
        avg_daily_dollar_volume=ADV,
        heartbeat_path=tmp_path / "heartbeat.json",
        script=script,
    )
    engine.add_strategy(strat)
    for extra in extra_strategies or []:
        engine.add_strategy(extra)
    engine.run()
    result = SimResult(strat, journal, state, engine, list(engine.cache.positions()))
    return result
