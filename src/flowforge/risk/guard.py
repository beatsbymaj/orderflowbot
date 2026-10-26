"""RiskGuard: the deterministic yes/no and size for every order FlowForge sends.

Every entry goes through `RiskGuard.check_entry`; every exit through `check_exit`. Both
are pure functions of the loaded limits, the risk state, the kill switch and the
snapshot the caller passes in. They never send orders and never change limits.

The guard collects every failed check (not only the first) so the journal shows the
full picture of each refusal. Sizing is the smallest of:
- by_risk: equity x max_risk_pct / (limit price - stop)
- by_notional: equity x max_position_notional_pct / limit price
- by_gross: room left under max_gross_exposure_pct, counting held and pending entries
- by_cash: cash available (cash account: no borrowing)
- by_volume: max_pct_of_recent_volume x average 1-minute volume
- the strategy's own cap, if it gave one
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from flowforge.config.risk_limits import LoadedRiskLimits, PdtRegime, RiskLimits
from flowforge.risk.calendar import NS, session_at
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

PDT_EQUITY_THRESHOLD = 25_000.0  # legacy FINRA rule 4210 pattern day trader minimum
# Claude defaults (not in risk_limits.yaml), documented in ADR-0013:
CLOCK_SKEW_TOLERANCE_MS = 250  # a quote stamped this far in the future means our clock is off
RESUME_COOLDOWN_S = 300  # no entries for 5 minutes after a halt or LULD pause ends
BPS = 10_000


@dataclass(frozen=True)
class QuoteView:
    bid: float
    ask: float
    bid_size: float
    ask_size: float
    ts_ns: int  # exchange/SIP timestamp of the NBBO update

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * BPS if self.mid > 0 else math.inf


@dataclass(frozen=True)
class MarketView:
    symbol: str
    quote: QuoteView | None
    halted: bool = False
    resumed_at_ns: int | None = None  # when the last halt/pause ended
    luld_lower: float | None = None
    luld_upper: float | None = None
    avg_minute_volume: float = 0.0  # shares, recent minutes
    avg_daily_dollar_volume: float = 0.0  # seeded before the open; 0 = unknown


@dataclass(frozen=True)
class InstrumentInfo:
    symbol: str
    tick_size: float = 0.01
    leveraged: bool = False
    inverse: bool = False


@dataclass(frozen=True)
class AccountView:
    equity: float  # marked to market
    cash_available: float
    positions: Mapping[str, int] = field(default_factory=dict)  # long shares held
    position_notional: Mapping[str, float] = field(default_factory=dict)
    pending_entries: Mapping[str, float] = field(default_factory=dict)  # symbol -> notional


@dataclass(frozen=True)
class EntryRequest:
    intent_id: str  # unique per signal; a repeat is refused as a duplicate
    symbol: str
    stop_price: float
    target_price: float  # Nautilus brackets need a take-profit; signal exits use request_exit
    side: str = "BUY"
    max_quantity: int | None = None


@dataclass(frozen=True)
class EntryPlan:
    symbol: str
    quantity: int
    limit_price: float  # marketable limit: ask plus at most max_slippage_bps
    stop_price: float
    target_price: float


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]
    plan: EntryPlan | None = None
    sizing: Mapping[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        plan = vars(self.plan) if self.plan else None
        return {
            "allowed": self.allowed,
            "reasons": list(self.reasons),
            "plan": plan,
            "sizing": dict(self.sizing),
        }


def round_down(price: float, tick: float) -> float:
    return round(math.floor(round(price / tick, 6)) * tick, 6)


class RiskGuard:
    def __init__(
        self,
        limits: LoadedRiskLimits | RiskLimits,
        state: RiskState,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self.loaded = limits if isinstance(limits, LoadedRiskLimits) else None
        self.limits = limits.limits if isinstance(limits, LoadedRiskLimits) else limits
        self.state = state
        self.kill = kill_switch

    # -- shared gates -----------------------------------------------------------------
    def blocking_reasons(self) -> list[str]:
        """Account-wide reasons no entry may be sent right now."""
        reasons = []
        if self.kill is not None and self.kill.is_engaged():
            reasons.append(f"kill switch on: {self.kill.reason()}")
        halt = self.state.halt_reason()
        if halt:
            reasons.append(halt)
        if self.loaded is not None and not self.loaded.unchanged_on_disk():
            reasons.append("risk limits file changed since startup: restart to load it")
        return reasons

    # -- entries ----------------------------------------------------------------------
    def check_entry(
        self,
        req: EntryRequest,
        market: MarketView,
        instrument: InstrumentInfo | None,
        account: AccountView,
        now_ns: int,
    ) -> RiskDecision:
        lim = self.limits
        reasons = self.blocking_reasons()

        # Direction and duplicates
        if req.side != "BUY":
            reasons.append(
                "long only: entries must be buys" if lim.portfolio.long_only else "only buys supported"
            )
        if req.intent_id in self.state.seen_intents:
            reasons.append(f"duplicate signal {req.intent_id}")
        if req.symbol in account.positions or req.symbol in account.pending_entries:
            reasons.append("already holding or entering this symbol")
        if market.symbol != req.symbol or (instrument is not None and instrument.symbol != req.symbol):
            reasons.append("market data is for a different symbol")

        # Universe
        if instrument is None:
            reasons.append("symbol is not in the trading universe")
        else:
            if instrument.leveraged and not lim.universe.allow_leveraged_etfs:
                reasons.append("leveraged ETFs not allowed")
            if instrument.inverse and not lim.universe.allow_inverse_etfs:
                reasons.append("inverse ETFs not allowed")
        if market.avg_daily_dollar_volume < lim.universe.min_avg_daily_dollar_volume:
            reasons.append("average daily dollar volume unknown or below minimum")

        # Session
        session = session_at(now_ns)
        if session is None:
            reasons.append("market closed today")
        else:
            s = lim.session
            if now_ns < session.open_ns or now_ns >= session.close_ns:
                reasons.append("outside regular hours")
            else:
                if now_ns < session.open_ns + s.no_entry_first_minutes * 60 * NS:
                    reasons.append("inside no-entry window after the open")
                if now_ns > session.close_ns - s.no_entry_last_minutes * 60 * NS:
                    reasons.append("inside no-entry window before the close")

        # Market state and data quality
        if market.halted:
            reasons.append("trading halted or paused")
        elif market.resumed_at_ns is not None and now_ns - market.resumed_at_ns < RESUME_COOLDOWN_S * NS:
            reasons.append("cooling down after a halt")
        q = market.quote
        if q is None:
            reasons.append("no quote")
        else:
            age_ms = (now_ns - q.ts_ns) / 1e6
            if age_ms > lim.session.max_data_staleness_ms:
                reasons.append(f"stale quote ({age_ms:.0f} ms old)")
            elif age_ms < -CLOCK_SKEW_TOLERANCE_MS:
                reasons.append(f"clock skew: quote is {-age_ms:.0f} ms in the future")
            if q.bid <= 0 or q.ask <= q.bid:
                reasons.append("locked, crossed or empty NBBO")
            elif q.spread_bps > lim.universe.max_spread_bps:
                reasons.append(f"spread {q.spread_bps:.1f} bps above {lim.universe.max_spread_bps}")
            if q.ask < lim.universe.min_price:
                reasons.append("price below universe minimum")

        # Account-level counts
        if len(account.positions) + len(account.pending_entries) >= lim.portfolio.max_concurrent_positions:
            reasons.append("max concurrent positions reached")
        if self.state.trades_today() >= lim.trading.max_trades_per_day:
            reasons.append("max trades per day reached")
        if (
            lim.trading.pdt_regime == PdtRegime.LEGACY_PDT
            and account.equity < PDT_EQUITY_THRESHOLD
            and self.state.day_trades_in_window() >= lim.trading.legacy_pdt_max_day_trades
        ):
            reasons.append("pattern day trader limit")

        # Prices: marketable limit, stop, bands
        if q is None or q.ask <= 0 or instrument is None:
            return RiskDecision(False, tuple(reasons))
        limit_price = round_down(q.ask * (1 + lim.per_trade.max_slippage_bps / BPS), instrument.tick_size)
        limit_price = max(limit_price, q.ask)
        if abs(limit_price - q.mid) / q.mid * BPS > lim.per_trade.max_price_deviation_bps:
            reasons.append("limit price too far from the NBBO mid")
        if not (0 < req.stop_price < q.bid):
            reasons.append("no valid stop below the bid")
        if not req.target_price > limit_price:
            reasons.append("target not above entry")
        if market.luld_upper is not None and limit_price >= market.luld_upper:
            reasons.append("entry at or above the LULD upper band")
        if market.luld_lower is not None and req.stop_price <= market.luld_lower:
            reasons.append("stop at or below the LULD lower band")

        sizing = self._size(req, limit_price, market, account)
        quantity = min(sizing.values()) if sizing else 0
        if quantity < 1:
            reasons.append("size rounds to zero shares")
        if reasons:
            return RiskDecision(False, tuple(reasons), sizing=sizing)
        plan = EntryPlan(req.symbol, quantity, limit_price, req.stop_price, req.target_price)
        return RiskDecision(True, (), plan, sizing)

    def _size(
        self, req: EntryRequest, price: float, market: MarketView, account: AccountView
    ) -> dict[str, int]:
        lim = self.limits
        per_share_risk = price - req.stop_price
        if per_share_risk <= 0:
            return {}
        committed = sum(account.position_notional.values()) + sum(account.pending_entries.values())
        sizing = {
            "by_risk": math.floor(account.equity * lim.per_trade.max_risk_pct / 100 / per_share_risk),
            "by_notional": math.floor(account.equity * lim.per_trade.max_position_notional_pct / 100 / price),
            "by_gross": math.floor(
                max(0.0, account.equity * lim.portfolio.max_gross_exposure_pct / 100 - committed) / price
            ),
            "by_cash": math.floor(
                max(0.0, account.cash_available - sum(account.pending_entries.values())) / price
            ),
            "by_volume": math.floor(market.avg_minute_volume * lim.per_trade.max_pct_of_recent_volume / 100),
        }
        if req.max_quantity is not None:
            sizing["by_strategy"] = req.max_quantity
        return {k: max(0, v) for k, v in sizing.items()}

    def commit_entry(self, req: EntryRequest) -> None:
        """Record an approved entry as sent. Call right before submitting it."""
        self.state.record_entry(req.intent_id)

    # -- exits ------------------------------------------------------------------------
    def check_exit(self, symbol: str, quantity: int, position_qty: int) -> RiskDecision:
        """Exits are always allowed (halts and the kill switch only block entries), but a
        sell may never be larger than the long position: that would open a short."""
        if quantity < 1:
            return RiskDecision(False, ("exit quantity must be at least one share",))
        if quantity > position_qty:
            return RiskDecision(False, (f"sell {quantity} > held {position_qty}: would go short",))
        return RiskDecision(True, ())
