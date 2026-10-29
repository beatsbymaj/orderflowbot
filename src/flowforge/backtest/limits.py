"""Deterministic entry checks and position sizing for backtests, from config/risk_limits.yaml.

This is not the Phase 4 RiskGuard. It applies the same operator limits inside a backtest
so results reflect what the account would have been allowed to do. Nothing here learns,
adapts or can be overridden by a strategy: limits come only from the loaded YAML.

Not modelled with 1-minute bars (no quotes): max_spread_bps, max_slippage_bps,
max_price_deviation_bps and max_data_staleness_ms. min_avg_daily_dollar_volume isn't
checked either, because IEX volume is a small slice of the market.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from flowforge.config.risk_limits import PdtRegime, RiskLimits

PDT_EQUITY_THRESHOLD = 25_000.0  # legacy FINRA rule 4210 pattern day trader minimum
PDT_WINDOW_DAYS = 5


@dataclass(frozen=True)
class EntryDecision:
    allowed: bool
    reason: str
    quantity: int = 0
    sizing: dict = field(default_factory=dict)


@dataclass
class LimitBook:
    """Tracks account state the limits depend on. One instance per backtest run."""

    limits: RiskLimits
    starting_equity: float
    realized: float = 0.0
    day: date | None = None
    day_start_equity: float = 0.0
    week: tuple[int, int] | None = None
    week_peak_equity: float = 0.0
    trades_today: int = 0
    spent_today: float = 0.0  # cash account: buys today, paid from settled cash
    halted_today: bool = False
    halted_this_week: bool = False
    open_notional: dict[str, float] = field(default_factory=dict)
    trading_days: deque = field(default_factory=lambda: deque(maxlen=PDT_WINDOW_DAYS))
    day_trades: list[date] = field(default_factory=list)

    @property
    def cash_equity(self) -> float:
        return self.starting_equity + self.realized

    def start_day(self, day: date) -> None:
        self.day = day
        self.trading_days.append(day)
        self.day_start_equity = self.cash_equity
        self.trades_today = 0
        self.spent_today = 0.0
        self.halted_today = False
        week = day.isocalendar()[:2]
        if week != self.week:
            self.week = week
            self.week_peak_equity = self.cash_equity
            self.halted_this_week = False

    def day_trades_in_window(self) -> int:
        window = set(self.trading_days)
        return sum(1 for d in self.day_trades if d in window)

    def check_entry(
        self,
        symbol: str,
        now: datetime,
        session_open: datetime,
        session_close: datetime,
        price: float,
        stop: float | None,
        avg_minute_volume: float,
        equity: float,
    ) -> EntryDecision:
        lim = self.limits
        s = lim.session
        if self.halted_this_week:
            return EntryDecision(False, "weekly drawdown halt")
        if self.halted_today:
            return EntryDecision(False, "daily loss halt")
        if now < session_open + timedelta(minutes=s.no_entry_first_minutes):
            return EntryDecision(False, "inside no-entry window after the open")
        if now > session_close - timedelta(minutes=s.no_entry_last_minutes):
            return EntryDecision(False, "inside no-entry window before the close")
        if stop is None or not stop < price:
            return EntryDecision(False, "no valid stop below entry")
        if price < lim.universe.min_price:
            return EntryDecision(False, "price below universe minimum")
        if symbol in self.open_notional:
            return EntryDecision(False, "already holding this symbol")
        if len(self.open_notional) >= lim.portfolio.max_concurrent_positions:
            return EntryDecision(False, "max concurrent positions reached")
        if self.trades_today >= lim.trading.max_trades_per_day:
            return EntryDecision(False, "max trades per day reached")
        if (
            lim.trading.pdt_regime == PdtRegime.LEGACY_PDT
            and equity < PDT_EQUITY_THRESHOLD
            and self.day_trades_in_window() >= lim.trading.legacy_pdt_max_day_trades
        ):
            return EntryDecision(False, "pattern day trader limit")

        per_share_risk = price - stop
        open_total = sum(self.open_notional.values())
        sizing = {
            "by_risk": math.floor(equity * lim.per_trade.max_risk_pct / 100 / per_share_risk),
            "by_notional": math.floor(equity * lim.per_trade.max_position_notional_pct / 100 / price),
            "by_gross": math.floor(
                max(0.0, equity * lim.portfolio.max_gross_exposure_pct / 100 - open_total) / price
            ),
            "by_volume": math.floor(avg_minute_volume * lim.per_trade.max_pct_of_recent_volume / 100),
        }
        if lim.trading.pdt_regime == PdtRegime.CASH:  # sales settle T+1: one turn of cash a day
            sizing["by_settled_cash"] = math.floor(max(0.0, self.day_start_equity - self.spent_today) / price)
        qty = min(sizing.values())
        if qty < 1:
            return EntryDecision(False, "size rounds to zero shares", 0, sizing)
        return EntryDecision(True, "ok", qty, sizing)

    def record_entry(self, symbol: str, notional: float) -> None:
        self.open_notional[symbol] = notional
        self.trades_today += 1
        self.spent_today += notional
        # Every position is flattened the same day, so each entry is a day trade.
        assert self.day is not None
        self.day_trades.append(self.day)

    def record_exit(self, symbol: str, realized_pnl: float) -> None:
        self.open_notional.pop(symbol, None)
        self.realized += realized_pnl

    def check_loss_limits(self, equity: float) -> str | None:
        """Call on every bar with marked-to-market equity. Returns the limit breached, if any."""
        self.week_peak_equity = max(self.week_peak_equity, equity)
        lim = self.limits.loss
        if not self.halted_this_week and self.week_peak_equity > 0:
            if (self.week_peak_equity - equity) / self.week_peak_equity * 100 >= lim.max_weekly_drawdown_pct:
                self.halted_this_week = True
                self.halted_today = True
                return "weekly"
        if not self.halted_today and self.day_start_equity > 0:
            if (self.day_start_equity - equity) / self.day_start_equity * 100 >= lim.max_daily_loss_pct:
                self.halted_today = True
                return "daily"
        return None
