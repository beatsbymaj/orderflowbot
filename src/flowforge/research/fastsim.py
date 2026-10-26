"""Fast trade simulator on NBBO quotes (docs/research/14, "Costs and fills").

One signal at a time: IOC marketable-limit entry 150 ms after the signal, stop and target
watched on the bid, everything flat at 15:55 ET. Much faster than a Nautilus backtest, so the
walk-forward grid can run; a Nautilus run of the chosen configurations checks it.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, time

from flowforge.data.ticks import NS
from flowforge.research.quotes import QuoteSource, iter_from, last_before
from flowforge.signals.setups import ET, Plan, Signal, plan

FLAT_AT = time(15, 55)


@dataclass(frozen=True)
class Costs:
    latency_ns: int = 150_000_000  # Phase 4 latency model
    slippage_bps: float = 10.0  # risk_limits per_trade.max_slippage_bps
    max_spread_bps: float = 10.0  # universe.max_spread_bps
    max_staleness_ns: int = 2 * NS  # session.max_data_staleness_ms
    per_share: float = 0.005  # IBKR Fixed, no per-order minimum in the "edge" measure
    sec_fee_rate: float = 0.0000206  # on sales
    risk_usd: float = 50.0  # 0.5% of $10,000


@dataclass(frozen=True)
class Trade:
    setup: str
    symbol: str
    day: str
    config: str
    signal_ts: int
    entry_ts: int
    entry: float
    shares: int
    stop: float
    target: float
    exit_ts: int
    exit: float
    exit_reason: str
    gross_usd: float
    cost_usd: float
    net_usd: float
    r: float  # net / (entry - stop) / shares

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Refusal:
    setup: str
    symbol: str
    signal_ts: int
    reason: str


def flat_ns(day: str) -> int:
    d = datetime.fromisoformat(day).date()
    return int(datetime.combine(d, FLAT_AT, ET).timestamp()) * NS


def _valid(bid: float, ask: float) -> bool:
    return bid > 0 and ask > bid


def _tick_down(x: float) -> float:
    return math.floor(round(x * 100, 6)) / 100


def simulate(
    sig: Signal, stop_mode: str, target_mode: str, quotes: QuoteSource, costs: Costs | None = None
) -> Trade | Refusal:
    costs = costs or Costs()
    config = f"{stop_mode}/{target_mode}"
    q = last_before(quotes, sig.symbol, sig.ts, costs.max_staleness_ns)
    if q is None:
        return Refusal(sig.setup, sig.symbol, sig.ts, "no fresh quote")
    _, bid, ask = q
    if not _valid(bid, ask):
        return Refusal(sig.setup, sig.symbol, sig.ts, "locked or crossed")
    if (ask - bid) / ((ask + bid) / 2) * 10_000 > costs.max_spread_bps:
        return Refusal(sig.setup, sig.symbol, sig.ts, "wide spread")
    p: Plan | None = plan(sig, ask, stop_mode, target_mode)
    if p is None:
        return Refusal(sig.setup, sig.symbol, sig.ts, "no plan")
    shares = int(costs.risk_usd // p.risk)
    if shares < 1:
        return Refusal(sig.setup, sig.symbol, sig.ts, "size zero")
    limit = _tick_down(ask * (1 + costs.slippage_bps / 10_000))
    arrive = sig.ts + costs.latency_ns
    a = last_before(quotes, sig.symbol, arrive, costs.max_staleness_ns)
    if a is None or not _valid(a[1], a[2]) or a[2] > limit:
        return Refusal(sig.setup, sig.symbol, sig.ts, "IOC not filled")
    entry = a[2]
    if entry <= p.stop:
        return Refusal(sig.setup, sig.symbol, sig.ts, "ask below stop at arrival")
    flat = flat_ns(sig.day)
    exit_ts, exit_px, why = None, None, "eod"
    for ts, b, _a in iter_from(quotes, sig.symbol, arrive + 1, flat):
        if b <= 0:
            continue
        if b <= p.stop:
            why = "stop"
        elif b >= p.target:
            why = "target"
        else:
            continue
        exit_ts = ts + costs.latency_ns
        break
    if exit_ts is None:
        exit_ts = flat + costs.latency_ns
    last = last_before(quotes, sig.symbol, exit_ts, exit_ts - a[0])  # newest quote since entry
    exit_px = last[1] if last is not None and last[1] > 0 else a[1]
    gross = (exit_px - entry) * shares
    cost = costs.per_share * shares * 2 + costs.sec_fee_rate * exit_px * shares
    net = gross - cost
    risk = (entry - p.stop) * shares
    return Trade(
        setup=sig.setup,
        symbol=sig.symbol,
        day=sig.day,
        config=config,
        signal_ts=sig.ts,
        entry_ts=arrive,
        entry=entry,
        shares=shares,
        stop=p.stop,
        target=p.target,
        exit_ts=exit_ts,
        exit=exit_px,
        exit_reason=why,
        gross_usd=round(gross, 4),
        cost_usd=round(cost, 4),
        net_usd=round(net, 4),
        r=round(net / risk, 6) if risk > 0 else 0.0,
    )
