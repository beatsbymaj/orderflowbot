"""Opening range breakout, long only, on 1-minute bars. A practice strategy for the backtest
engine, not an order-flow setup and not a validated edge.

Rules, per symbol per day:
1. The opening range is the high and low of the first `range_minutes` of regular hours.
2. The first bar that closes above the range high, before `entry_cutoff`, is the signal.
3. Entry is a market buy with a stop just under the range low and a target at
   `target_r` times the risk. Size and permission come from LimitBook (risk_limits.yaml).
4. Anything still open `exit_before_close_minutes` before the scheduled close is sold.
One signal per symbol per day, taken or not.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from nautilus_trader.model import OrderSide, OrderType, Price, Quantity
from nautilus_trader.trading import Strategy, StrategyConfig

from flowforge.backtest.bars import ET, RTH_OPEN, bar_type, instrument_id
from flowforge.backtest.limits import LimitBook

VOLUME_LOOKBACK_BARS = 30


@dataclass(frozen=True)
class OrbParams:
    range_minutes: int = 15
    min_range_bars: int = 10  # IEX can skip quiet minutes; skip the day if too few bars
    entry_cutoff: time = time(12, 0)
    target_r: float = 2.0
    exit_before_close_minutes: int = 15


@dataclass
class _SymbolDay:
    range_high: float = 0.0
    range_low: float = float("inf")
    range_bars: int = 0
    signalled: bool = False


@dataclass
class _Held:
    quantity: float
    entry_price: float
    entry_time: datetime
    commission: float = 0.0
    exit_reason: str | None = None


@dataclass
class OrbRecorder:
    """What the run produced: closed trades, the decision journal and an equity series."""

    trades: list[dict] = field(default_factory=list)
    journal: list[dict] = field(default_factory=list)
    equity: list[tuple[datetime, float]] = field(default_factory=list)


def _ts(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1e9, UTC).astimezone(ET)


class OpeningRangeBreakout(Strategy):
    def __new__(cls, *args, **kwargs):  # the native base only accepts a config argument
        return super().__new__(cls)

    def __init__(
        self,
        symbols: list[str],
        params: OrbParams,
        book: LimitBook,
        session_closes: dict[date, datetime],
        recorder: OrbRecorder,
    ) -> None:
        super().__init__(StrategyConfig(order_id_tag="ORB", log_events=False, log_commands=False))
        self.symbols = symbols
        self.params = params
        self.book = book
        self.closes = session_closes
        self.rec = recorder
        self._day: date | None = None
        self._flattened_today = False
        self._sym_day: dict[str, _SymbolDay] = {}
        self._volumes = {s: deque(maxlen=VOLUME_LOOKBACK_BARS) for s in symbols}
        self._last_close: dict[str, float] = {}
        self._held: dict[str, _Held] = {}

    # --- helpers -------------------------------------------------------------------
    def _log(self, when: datetime, event: str, **fields) -> None:
        self.rec.journal.append({"ts": when.isoformat(), "event": event, **fields})

    def _equity(self) -> float:
        unrealized = sum((self._last_close[s] - h.entry_price) * h.quantity for s, h in self._held.items())
        return self.book.cash_equity + unrealized

    def _flatten(self, when: datetime, reason: str) -> None:
        for s in self.symbols:
            self.cancel_all_orders(instrument_id(s))
        for s, held in self._held.items():
            held.exit_reason = reason
            self.close_all_positions(instrument_id(s))
        self._log(when, "flatten", reason=reason, symbols=sorted(self._held))

    def _new_day(self, day: date, when: datetime) -> None:
        if self._held:  # should never happen: everything is flattened before the close
            self._log(when, "error", detail="positions carried overnight", symbols=sorted(self._held))
        self._day = day
        self._flattened_today = False
        self._sym_day = {s: _SymbolDay() for s in self.symbols}
        self.book.start_day(day)

    # --- Nautilus callbacks ----------------------------------------------------------
    def on_start(self) -> None:
        for s in self.symbols:
            self.subscribe_bars(bar_type(s))

    def on_bar(self, bar) -> None:
        sym = bar.bar_type.instrument_id.symbol.value
        end = _ts(bar.ts_event)
        start = end - timedelta(minutes=1)
        if start.date() != self._day:
            self._new_day(start.date(), end)
        close = float(bar.close)
        self._last_close[sym] = close
        self._volumes[sym].append(float(bar.volume))

        equity = self._equity()
        self.rec.equity.append((end, equity))
        breach = self.book.check_loss_limits(equity)
        if breach:
            self._log(end, "risk_halt", limit=breach, equity=round(equity, 2))
            if self._held:
                self._flatten(end, f"{breach} loss limit")

        session_close = self.closes[start.date()]
        if end >= session_close - timedelta(minutes=self.params.exit_before_close_minutes):
            if not self._flattened_today:
                self._flattened_today = True
                if self._held:
                    self._flatten(end, "end of day")
            return

        session_open = datetime.combine(start.date(), RTH_OPEN, tzinfo=ET)
        state = self._sym_day[sym]
        if start < session_open + timedelta(minutes=self.params.range_minutes):
            state.range_high = max(state.range_high, float(bar.high))
            state.range_low = min(state.range_low, float(bar.low))
            state.range_bars += 1
            return
        if state.signalled or state.range_bars < self.params.min_range_bars:
            return
        if end.time() > self.params.entry_cutoff or close <= state.range_high:
            return

        state.signalled = True
        stop = round(state.range_low - 0.01, 2)
        avg_vol = sum(self._volumes[sym]) / len(self._volumes[sym])
        decision = self.book.check_entry(sym, end, session_open, session_close, close, stop, avg_vol, equity)
        self._log(
            end,
            "signal",
            symbol=sym,
            price=close,
            range_high=state.range_high,
            range_low=state.range_low,
            stop=stop,
            allowed=decision.allowed,
            reason=decision.reason,
            quantity=decision.quantity,
            sizing=decision.sizing,
        )
        if not decision.allowed:
            return
        target = round(close + self.params.target_r * (close - stop), 2)
        orders = self.order_factory.bracket(
            instrument_id(sym),
            OrderSide.BUY,
            Quantity.from_int(decision.quantity),
            sl_trigger_price=Price(stop, 2),
            tp_price=Price(target, 2),
        )
        self.submit_order_list(orders)
        self.book.record_entry(sym, decision.quantity * close)
        self._log(end, "order", symbol=sym, side="BUY", quantity=decision.quantity, stop=stop, target=target)

    def on_order_filled(self, event) -> None:
        sym = event.instrument_id.symbol.value
        when = _ts(event.ts_event)
        commission = float(event.commission.as_double()) if event.commission else 0.0
        self._log(
            when,
            "fill",
            symbol=sym,
            side=event.order_side.name,
            order_type=event.order_type.name,
            quantity=float(event.last_qty),
            price=float(event.last_px),
            commission=commission,
        )
        held = self._held.get(sym)
        if event.order_side == OrderSide.BUY:
            if held is None:
                self._held[sym] = held = _Held(float(event.last_qty), float(event.last_px), when)
            held.commission += commission
        elif held is not None:
            held.commission += commission
            if held.exit_reason is None:
                held.exit_reason = {OrderType.STOP_MARKET: "stop", OrderType.LIMIT: "target"}.get(
                    event.order_type, event.order_type.name.lower()
                )

    def on_position_closed(self, event) -> None:
        sym = event.instrument_id.symbol.value
        held = self._held.pop(sym)
        pnl = float(event.realized_pnl.as_double())
        self.book.record_exit(sym, pnl)
        exit_time = _ts(event.ts_closed)
        self.rec.trades.append(
            {
                "symbol": sym,
                "entry_time": held.entry_time.isoformat(),
                "exit_time": exit_time.isoformat(),
                "quantity": held.quantity,
                "entry_price": float(event.avg_px_open),
                "exit_price": float(event.avg_px_close),
                "commission": round(held.commission, 2),
                "pnl": round(pnl, 2),
                "exit_reason": held.exit_reason,
            }
        )
        self._log(exit_time, "position_closed", symbol=sym, pnl=round(pnl, 2), reason=held.exit_reason)

    def _entry_failed(self, event, kind: str) -> None:
        sym = event.instrument_id.symbol.value
        self._log(_ts(event.ts_event), kind, symbol=sym, detail=str(event.reason))
        if sym not in self._held:  # entry never filled: free the slot, keep the trade count
            self.book.open_notional.pop(sym, None)

    def on_order_denied(self, event) -> None:
        self._entry_failed(event, "order_denied")

    def on_order_rejected(self, event) -> None:
        self._entry_failed(event, "order_rejected")
