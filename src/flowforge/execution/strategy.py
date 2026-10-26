"""GuardedStrategy: the Nautilus base class every FlowForge strategy trades through.

Subclasses produce signals and call `request_entry`. Everything else lives here and is
the same in backtests and on the IBKR paper node:
- every entry is checked and sized by RiskGuard; refusals are journaled with reasons;
- approved entries go out as one immediate-or-cancel marketable limit buy; the stop-market
  sell for the shares actually bought follows at once and rests at the broker; targets are
  watched here (one live sell per trade, see risk/orders.py);
- every order event goes through OrderManager, whose actions are carried out here;
- a 1-second timer marks equity for the loss limits, watches the kill switch, flattens
  before the close, times out entries, reconciles positions and writes the heartbeat.

The Nautilus RiskEngine (bypass off, order rate from risk_limits.yaml) still sits behind
this as a second layer (DECISIONS.md D7).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from nautilus_trader.model import (
    ClientOrderId,
    Currency,
    MarketStatusAction,
    OrderSide,
    Quantity,
    TimeInForce,
    Venue,
)
from nautilus_trader.trading import Strategy, StrategyConfig

from flowforge.data.ticks import VENUE, instrument_id
from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.risk.calendar import NS, et_date, session_at
from flowforge.risk.guard import (
    AccountView,
    EntryRequest,
    InstrumentInfo,
    MarketView,
    QuoteView,
    RiskDecision,
    RiskGuard,
)
from flowforge.risk.orders import (
    Action,
    Alert,
    Cancel,
    EngageKill,
    OrderManager,
    PlaceStop,
    Resize,
    SubmitExit,
)

# Claude defaults (not in risk_limits.yaml), documented in ADR-0013.
FLATTEN_BEFORE_CLOSE_S = 5 * 60  # flatten_at_close: everything is sold 5 min before the bell
# Hand-off with the watchdog (ops/watchdog.py): the node flattens for the first
# NODE_KILL_WINDOW_S after it sees the kill switch; after that only the watchdog sends
# sells. A node whose 1-second tick stalled for FREEZE_S may have been taken over by the
# watchdog while frozen, so it stops sending orders and asks for a human.
NODE_KILL_WINDOW_S = 20
FREEZE_S = 10
VOLUME_LOOKBACK_MIN = 30
EQUITY_MARK_S = 60  # equity journaled once a minute for the dashboard's day curve
TIMER = "flowforge-risk-tick"
USD = Currency.from_str("USD")


class _Symbol:
    def __init__(self) -> None:
        self.quote: QuoteView | None = None
        self.halted = False
        self.resumed_at_ns: int | None = None
        self.minute: int | None = None
        self.minute_volume = 0.0
        self.volumes: deque[float] = deque(maxlen=VOLUME_LOOKBACK_MIN)
        self.stale_reported = False

    def avg_minute_volume(self) -> float:
        return sum(self.volumes) / len(self.volumes) if self.volumes else 0.0


class GuardedStrategy(Strategy):
    def __new__(cls, *args: Any, **kwargs: Any) -> GuardedStrategy:
        return super().__new__(cls)  # the native base only accepts a config argument

    def __init__(
        self,
        symbols: list[str],
        guard: RiskGuard,
        universe: Mapping[str, InstrumentInfo],
        journal: Journal | None = None,
        avg_daily_dollar_volume: Mapping[str, float] | None = None,
        heartbeat_path: str | Path | None = None,
        order_id_tag: str = "FF",
        venue: str = VENUE,
        trader_name: str = "flowforge",
    ) -> None:
        super().__init__(StrategyConfig(order_id_tag=order_id_tag, log_events=False, log_commands=False))
        self.symbols = symbols
        self.guard = guard
        self.risk_state = guard.state
        self.manager = OrderManager(guard.state)
        self.universe = universe
        self.journal = journal or Journal()
        self.adv = dict(avg_daily_dollar_volume or {})
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.venue = Venue(venue)
        self.trader_name = trader_name
        self.market: dict[str, _Symbol] = {s: _Symbol() for s in symbols}
        self.start_equity: float | None = None
        self._flattened_day = None
        self._kill_handled = False
        self._kill_seen_ns: int | None = None
        self._last_tick_ns: int | None = None
        self._last_mark_ns: int | None = None
        self.frozen = False

    # -- lifecycle --------------------------------------------------------------------
    def on_start(self) -> None:
        for s in self.symbols:
            iid = instrument_id(s)
            self.subscribe_quotes(iid)
            self.subscribe_trades(iid)
            self.subscribe_instrument_status(iid)
        self.clock.set_timer(TIMER, timedelta(seconds=1), callback=self._on_tick)
        self._journal("start", symbols=self.symbols, risk_limits_sha256=self._limits_hash())

    def on_stop(self) -> None:
        # Stops stay at the broker; the watchdog flattens if the node stays down in session.
        self._journal("stop", positions=self.manager.positions())
        self.journal.close()

    # -- market data ------------------------------------------------------------------
    def on_quote(self, quote: Any) -> None:
        sym = quote.instrument_id.symbol.value
        st = self.market.get(sym)
        if st is None:
            return
        st.quote = QuoteView(
            quote.bid_price.as_double(),
            quote.ask_price.as_double(),
            quote.bid_size.as_double(),
            quote.ask_size.as_double(),
            quote.ts_event,
        )
        if st.stale_reported:
            st.stale_reported = False
            self._journal("feed_restored", symbol=sym)
        self._run(self.manager.on_price(sym, st.quote.bid, self.clock.timestamp_ns()))
        self.on_market(sym)

    def on_trade(self, tick: Any) -> None:
        st = self.market.get(tick.instrument_id.symbol.value)
        if st is None:
            return
        minute = tick.ts_event // (60 * NS)
        if st.minute is not None and minute != st.minute:
            st.volumes.append(st.minute_volume)
            for _ in range(min(minute - st.minute - 1, VOLUME_LOOKBACK_MIN)):
                st.volumes.append(0.0)  # quiet minutes count as zero volume
            st.minute_volume = 0.0
        st.minute = minute
        st.minute_volume += tick.size.as_double()

    def on_instrument_status(self, status: Any) -> None:
        sym = status.instrument_id.symbol.value
        st = self.market.get(sym)
        if st is None:
            return
        if status.action in (MarketStatusAction.HALT, MarketStatusAction.PAUSE):
            st.halted = True
        elif status.action == MarketStatusAction.TRADING and st.halted:
            st.halted = False
            st.resumed_at_ns = status.ts_event
        self._journal("instrument_status", symbol=sym, action=str(status.action), reason=status.reason)

    def on_market(self, symbol: str) -> None:
        """Hook for subclasses: called after each quote for `symbol`."""

    def market_view(self, symbol: str) -> MarketView:
        st = self.market[symbol]
        return MarketView(
            symbol,
            st.quote,
            halted=st.halted,
            resumed_at_ns=st.resumed_at_ns,
            avg_minute_volume=st.avg_minute_volume(),
            avg_daily_dollar_volume=self.adv.get(symbol, 0.0),
        )

    # -- account ----------------------------------------------------------------------
    def _limits_hash(self) -> str | None:
        return self.guard.loaded.sha256 if self.guard.loaded else None

    def _mark(self, symbol: str) -> float | None:
        st = self.market.get(symbol)
        return st.quote.bid if st and st.quote else None

    def equity(self) -> float:
        """Starting equity plus realized and unrealized P&L (marked at the bid) since start."""
        if self.start_equity is None:
            account = self.portfolio.account(self.venue)
            self.start_equity = account.balance_total(USD).as_double() if account else 0.0
        pnl = 0.0
        for position in self.cache.positions(strategy_id=self.strategy_id):
            pnl += position.realized_pnl.as_double() if position.realized_pnl else 0.0
            if position.is_open:
                sym = position.instrument_id.symbol.value
                mark = self._mark(sym) or position.avg_px_open
                pnl += (mark - position.avg_px_open) * position.quantity.as_double()
        return self.start_equity + pnl

    def account_view(self) -> AccountView:
        equity = self.equity()
        account = self.portfolio.account(self.venue)
        cash = account.balance_free(USD).as_double() if account else 0.0
        held = self.manager.positions()
        notional = {s: q * (self._mark(s) or 0.0) for s, q in held.items()}
        return AccountView(equity, cash, held, notional, self.manager.pending_entries())

    def broker_positions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.cache.positions_open():
            sym = p.instrument_id.symbol.value
            out[sym] = out.get(sym, 0) + int(p.signed_qty)
        return out

    # -- entries ----------------------------------------------------------------------
    def request_entry(self, req: EntryRequest) -> RiskDecision:
        now = self.clock.timestamp_ns()
        self._ensure_day(now)
        decision = self.guard.check_entry(
            req,
            self.market_view(req.symbol) if req.symbol in self.market else MarketView(req.symbol, None),
            self.universe.get(req.symbol),
            self.account_view(),
            now,
        )
        self._journal("risk_decision", intent=req.intent_id, symbol=req.symbol, **decision.as_dict())
        if not decision.allowed:
            return decision
        plan = decision.plan
        iid = instrument_id(req.symbol)
        instrument = self.cache.instrument(iid)
        entry = self.order_factory.limit(
            iid,
            OrderSide.BUY,
            Quantity.from_int(plan.quantity),
            instrument.make_price(plan.limit_price),
            time_in_force=TimeInForce.IOC,
            tags=[f"intent={req.intent_id}", "entry"],
        )
        self.guard.commit_entry(req)
        self.manager.open_trade(req.intent_id, plan, str(entry.client_order_id), now)
        self._journal(
            "order_submit",
            intent=req.intent_id,
            symbol=req.symbol,
            order=str(entry.client_order_id),
            role="entry",
            quantity=plan.quantity,
            limit=plan.limit_price,
        )
        self.submit_order(entry)
        return decision

    def request_exit(self, intent_id: str, reason: str) -> None:
        """Signal exit: cancel the trade's orders, then sell what is held."""
        trade = self.manager.trades.get(intent_id)
        if trade is not None:
            self._journal("exit_request", intent=intent_id, reason=reason)
            self._run(self.manager.flatten(trade, reason, self.clock.timestamp_ns()))

    # -- order events -----------------------------------------------------------------
    def _id(self, event: Any) -> str:
        return str(event.client_order_id)

    def on_order_accepted(self, event: Any) -> None:
        self._journal("order_accepted", order=self._id(event))
        self._run(self.manager.on_accepted(self._id(event), event.ts_event))

    def on_order_rejected(self, event: Any) -> None:
        self._journal("order_rejected", order=self._id(event), reason=str(event.reason))
        self._run(self.manager.on_rejected(self._id(event), str(event.reason), event.ts_event))

    def on_order_denied(self, event: Any) -> None:
        self._journal("order_denied", order=self._id(event), reason=str(event.reason))
        self._run(self.manager.on_rejected(self._id(event), f"denied: {event.reason}", event.ts_event))

    def on_order_canceled(self, event: Any) -> None:
        self._journal("order_canceled", order=self._id(event))
        self._run(self.manager.on_canceled(self._id(event), event.ts_event))

    def on_order_expired(self, event: Any) -> None:
        self._journal("order_expired", order=self._id(event))
        self._run(self.manager.on_canceled(self._id(event), event.ts_event))

    def on_order_cancel_rejected(self, event: Any) -> None:
        self._journal("order_cancel_rejected", order=self._id(event), reason=str(event.reason))
        self._run(self.manager.on_cancel_rejected(self._id(event), str(event.reason)))

    def on_order_modify_rejected(self, event: Any) -> None:
        # A stop that can't be resized after a partial entry would sell more than held.
        reason = f"could not resize {self._id(event)}: {event.reason}"
        self._journal("order_modify_rejected", order=self._id(event), reason=str(event.reason))
        found = self.manager.lookup(self._id(event))
        if found is not None:
            self._run(
                [
                    Alert("critical", reason, found[0].symbol),
                    *self.manager.flatten(found[0], reason, event.ts_event),
                ]
            )

    def on_order_updated(self, event: Any) -> None:
        self._journal("order_updated", order=self._id(event), quantity=int(event.quantity.as_double()))
        self.manager.on_updated(self._id(event), int(event.quantity.as_double()))

    def on_order_filled(self, event: Any) -> None:
        order = self.cache.order(event.client_order_id)
        complete = order is not None and order.leaves_qty.as_double() == 0
        self._journal(
            "fill",
            order=self._id(event),
            fill_id=str(event.trade_id),
            side=str(event.order_side),
            quantity=int(event.last_qty.as_double()),
            price=event.last_px.as_double(),
            commission=event.commission.as_double() if event.commission else 0.0,
            complete=complete,
        )
        self._run(
            self.manager.on_filled(
                self._id(event),
                str(event.trade_id),
                int(event.last_qty.as_double()),
                event.ts_event,
                complete,
            )
        )

    def on_position_closed(self, event: Any) -> None:
        self._journal(
            "position_closed",
            symbol=event.instrument_id.symbol.value,
            realized_pnl=event.realized_pnl.as_double() if event.realized_pnl else 0.0,
        )

    # -- actions ----------------------------------------------------------------------
    def _run(self, actions: list[Action]) -> None:
        for action in actions:
            if isinstance(action, Cancel):
                order = self.cache.order(ClientOrderId(action.order_id))
                if order is None or order.is_closed:
                    continue
                blocked = self._sells_blocked()
                if blocked and order.side == OrderSide.SELL:
                    # Without the right to sell, keep the stop: never strip protection.
                    self.alert("critical", f"keeping {action.order_id}: {blocked}")
                    continue
                self._journal("cancel", order=action.order_id, reason=action.reason)
                self.cancel_order(order.client_order_id)
            elif isinstance(action, Resize):
                order = self.cache.order(ClientOrderId(action.order_id))
                if order is not None and not order.is_closed:
                    self._journal(
                        "resize", order=action.order_id, quantity=action.quantity, reason=action.reason
                    )
                    self.modify_order(order.client_order_id, quantity=Quantity.from_int(action.quantity))
            elif isinstance(action, PlaceStop | SubmitExit):
                blocked = self._sells_blocked()
                if blocked:
                    self.alert("critical", f"not sending {type(action).__name__}: {blocked}", action.symbol)
                elif isinstance(action, PlaceStop):
                    self._place_stop(action)
                else:
                    self._submit_exit(action)
            elif isinstance(action, Alert):
                self.alert(action.level, action.message, action.symbol)
            elif isinstance(action, EngageKill):
                self.engage_kill(action.reason)

    def _sells_blocked(self) -> str | None:
        if self.frozen:
            return "node was frozen; the watchdog may have acted. Check the account."
        now = self.clock.timestamp_ns()
        if self._kill_seen_ns is not None and now - self._kill_seen_ns > NODE_KILL_WINDOW_S * NS:
            return "kill switch on for over 20 s: the watchdog owns flattening now"
        return None

    def _place_stop(self, action: PlaceStop) -> None:
        trade = self.manager.trades[action.intent_id]
        check = self.guard.check_exit(action.symbol, action.quantity, trade.position)
        if not check.allowed:  # can't happen by construction; flatten rather than guess
            reason = f"stop refused: {'; '.join(check.reasons)}"
            self._run(
                [
                    Alert("critical", reason, action.symbol),
                    *self.manager.flatten(trade, reason, self.clock.timestamp_ns()),
                ]
            )
            return
        iid = instrument_id(action.symbol)
        order = self.order_factory.stop_market(
            iid,
            OrderSide.SELL,
            Quantity.from_int(action.quantity),
            self.cache.instrument(iid).make_price(action.stop_price),
            time_in_force=TimeInForce.DAY,
            reduce_only=True,
            tags=[f"intent={action.intent_id}", "stop"],
        )
        self.manager.register_stop(
            action.intent_id, str(order.client_order_id), action.quantity, self.clock.timestamp_ns()
        )
        self._journal(
            "stop_submit",
            intent=action.intent_id,
            order=str(order.client_order_id),
            quantity=action.quantity,
            stop=action.stop_price,
        )
        self.submit_order(order)

    def _submit_exit(self, action: SubmitExit) -> None:
        trade = self.manager.trades[action.intent_id]
        check = self.guard.check_exit(action.symbol, action.quantity, trade.position)
        if not check.allowed:
            self.alert("critical", f"exit refused: {'; '.join(check.reasons)}", action.symbol)
            return
        order = self.order_factory.market(
            instrument_id(action.symbol),
            OrderSide.SELL,
            Quantity.from_int(action.quantity),
            time_in_force=TimeInForce.DAY,
            reduce_only=True,
            tags=[f"intent={action.intent_id}", "exit"],
        )
        self.manager.register_exit(action.intent_id, str(order.client_order_id))
        self._journal(
            "exit_submit", intent=action.intent_id, order=str(order.client_order_id), **vars(action)
        )
        self.submit_order(order)

    def alert(self, level: str, message: str, symbol: str = "") -> None:
        self._journal("alert", level=level, message=message, symbol=symbol)
        log = (
            self.log.error
            if level == "critical"
            else self.log.warning
            if level == "warning"
            else self.log.info
        )
        log(f"[{level}] {symbol} {message}".strip())

    def engage_kill(self, reason: str) -> None:
        if self.guard.kill is not None:
            self.guard.kill.engage(reason, f"node:{self.trader_name}")
        if self._kill_seen_ns is None:
            self._kill_seen_ns = self.clock.timestamp_ns()
        self.risk_state.halt_day("kill", reason)
        self._journal("kill_engaged", reason=reason)

    def flatten_all(self, reason: str) -> None:
        now = self.clock.timestamp_ns()
        self._journal("flatten_all", reason=reason, positions=self.manager.positions())
        self._run(self.manager.flatten_all(reason, now))

    # -- the 1-second risk tick -------------------------------------------------------
    def _ensure_day(self, now: int) -> None:
        day = et_date(now)
        if self.risk_state.day != day:
            self.risk_state.start_day(day, self.equity())
            self._kill_handled = False
            self._journal("day_start", day=day.isoformat(), equity=self.risk_state.day_start_equity)

    def _on_tick(self, event: Any) -> None:
        now = event.ts_event
        if self._last_tick_ns is not None and now - self._last_tick_ns > FREEZE_S * NS and not self.frozen:
            self.frozen = True
            gap = (now - self._last_tick_ns) / NS
            self.risk_state.halt_day("errors", f"node froze for {gap:.0f} s")
            self.alert(
                "critical", f"node froze for {gap:.0f} s: no more orders from this node, check the account"
            )
        self._last_tick_ns = now
        self._ensure_day(now)
        equity = self.equity()
        breach = self.risk_state.on_equity(equity)
        if self._last_mark_ns is None or now - self._last_mark_ns >= EQUITY_MARK_S * NS:
            self._last_mark_ns = now
            self._journal(
                "equity_mark",
                equity=round(equity, 2),
                day_start_equity=round(self.risk_state.day_start_equity, 2),
                week_peak_equity=round(self.risk_state.week_peak_equity, 2),
            )
        if breach:
            reason = self.risk_state.halt_reason() or breach
            self.alert("critical", f"{breach} loss limit hit, equity {equity:.2f}: {reason}")
            self.flatten_all(f"{breach} loss limit")

        kill = self.guard.kill
        engaged = kill is not None and kill.is_engaged()
        if not engaged:
            self._kill_seen_ns = None
        elif self._kill_seen_ns is None:
            self._kill_seen_ns = now
        if engaged and not self._kill_handled:
            self._kill_handled = True
            reason = kill.reason() or "kill switch"
            self.risk_state.halt_day("kill", reason)
            self.alert("critical", f"kill switch on: {reason}")
            if self.guard.limits.kill_switch.flatten_on_kill:
                self.flatten_all("kill switch")

        session = session_at(now)
        if (
            session is not None
            and self.guard.limits.session.flatten_at_close
            and now >= session.close_ns - FLATTEN_BEFORE_CLOSE_S * NS
            and self._flattened_day != session.day
        ):
            self._flattened_day = session.day
            if self.manager.active():
                self.flatten_all("end of day")

        self._run(self.manager.on_tick(now))
        self._run(self.manager.reconcile(self.broker_positions()))
        self._check_feed(now)
        self.on_risk_tick(now)
        if self.heartbeat_path is not None:
            write_heartbeat(
                self.heartbeat_path,
                Heartbeat(
                    now,
                    self.trader_name,
                    self.broker_positions(),
                    len(self.cache.orders_open()),
                    self.risk_state.halt_reason(),
                    engaged,
                    round(equity, 2),
                    round(self.risk_state.day_start_equity, 2),
                ),
            )

    def on_risk_tick(self, now: int) -> None:
        """Hook for subclasses: called once a second after the risk checks."""

    def _check_feed(self, now: int) -> None:
        limit_ns = self.guard.limits.session.max_data_staleness_ms * 1_000_000
        held = self.manager.positions()
        for sym, st in self.market.items():
            if (
                sym in held
                and not st.stale_reported
                and (st.quote is None or now - st.quote.ts_ns > limit_ns)
            ):
                st.stale_reported = True
                self.alert("warning", "no fresh quotes while holding: broker stop still protects", sym)

    def _journal(self, event: str, **fields: Any) -> None:
        self.journal.write(self.clock.timestamp_ns(), event, **fields)
