"""Order lifecycle for guarded trades: what to do on every broker event.

One `Trade` is one approved entry. The manager turns broker events into actions for the
Nautilus adapter to carry out. It is pure Python and never talks to the broker itself,
so every fault path can be unit-tested.

How a trade runs (ADR-0013):
1. Entry: one marketable limit buy, immediate-or-cancel. It fills, partly fills, or
   cancels at once, so the number of shares to protect is known immediately.
2. Protection: as soon as the entry is done, one stop-market sell for exactly the shares
   held is sent to the broker, where it survives a FlowForge crash. If the broker hasn't
   accepted it within STOP_ACK_TIMEOUT_S, or rejects or cancels it, the trade is
   flattened.
3. Target: FlowForge watches the bid; at the target it flattens. There is never more
   than one live sell order per trade, so no broker behaviour (one-cancels-other
   timing, partial-fill handling of bracket children) can sell more than is held.
4. Flatten: cancel every live order of the trade, and sell only after the cancels are
   confirmed, so a late stop fill can't turn the sale into a short. If cancels aren't
   confirmed in FLATTEN_CANCEL_TIMEOUT_S, the kill switch goes on and a human is asked to
   look; the stop stays at the broker meanwhile. Exits are market orders, retried
   MAX_EXIT_ATTEMPTS times.

Also: duplicate fills (same fill id) are ignored; a fill for an unknown order or an
overfill halts new entries; rejected entries count toward the error halt in RiskState;
broker positions are compared with ours every tick and a mismatch lasting
RECONCILE_GRACE checks halts new entries.

Why not a native bracket: on the Nautilus simulated venue the bracket's children are
released at the full order size on the first partial fill (the venue then refuses the
oversized stop), or not at all until the parent completes (shares unprotected). IBKR's
own partial-fill handling for bracket children is not documented (UNVERIFIED,
docs/research/13). The design above doesn't depend on either.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from flowforge.risk.calendar import NS
from flowforge.risk.guard import EntryPlan
from flowforge.risk.state import RiskState

# Claude defaults (not in risk_limits.yaml), documented in ADR-0013.
ENTRY_TIMEOUT_S = 5  # an IOC entry should be done at once; cancel it if a venue lets it rest
STOP_ACK_TIMEOUT_S = 5  # shares held without a confirmed stop for 5 s: flatten
FLATTEN_CANCEL_TIMEOUT_S = 10  # cancels not confirmed in 10 s: kill switch + alert
EXIT_RETRY_S = 2
MAX_EXIT_ATTEMPTS = 3
RECONCILE_GRACE = 3  # consecutive mismatched checks before halting


class Role(StrEnum):
    ENTRY = "entry"
    STOP = "stop"
    EXIT = "exit"


class Phase(StrEnum):
    ENTERING = "entering"  # entry live
    PROTECTING = "protecting"  # shares held, stop sent but not yet accepted
    OPEN = "open"  # shares held, stop working at the broker
    FLATTENING = "flattening"  # waiting for cancels before selling
    EXITING = "exiting"  # our sell is working
    CLOSED = "closed"
    FAILED = "failed"  # entry never filled


# -- actions ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Cancel:
    order_id: str
    reason: str


@dataclass(frozen=True)
class PlaceStop:
    intent_id: str
    symbol: str
    quantity: int
    stop_price: float


@dataclass(frozen=True)
class Resize:
    order_id: str
    quantity: int
    reason: str


@dataclass(frozen=True)
class SubmitExit:
    intent_id: str
    symbol: str
    quantity: int
    reason: str


@dataclass(frozen=True)
class Alert:
    level: str  # info | warning | critical
    message: str
    symbol: str = ""


@dataclass(frozen=True)
class EngageKill:
    reason: str


Action = Cancel | PlaceStop | Resize | SubmitExit | Alert | EngageKill


@dataclass
class Trade:
    intent_id: str
    symbol: str
    planned_qty: int
    limit_price: float
    stop_price: float
    target_price: float
    entry_id: str
    submitted_ns: int
    phase: Phase = Phase.ENTERING
    stop_id: str | None = None
    stop_qty: int = 0
    stop_sent_ns: int = 0
    stop_acked: bool = False
    filled_qty: int = 0
    sold_qty: int = 0
    entry_done: bool = False
    entry_cancel_sent: bool = False
    live: set[str] = field(default_factory=set)
    exit_ids: list[str] = field(default_factory=list)
    pending_cancels: set[str] = field(default_factory=set)
    flatten_reason: str | None = None
    flatten_started_ns: int = 0
    flatten_timed_out: bool = False
    exit_attempts: int = 0
    exit_retry_ns: int | None = None

    @property
    def position(self) -> int:
        return self.filled_qty - self.sold_qty

    @property
    def done(self) -> bool:
        return self.phase in (Phase.CLOSED, Phase.FAILED)


class OrderManager:
    def __init__(self, state: RiskState) -> None:
        self.state = state
        self.trades: dict[str, Trade] = {}
        self._orders: dict[str, tuple[str, Role]] = {}  # order id -> (intent id, role)
        self._fills: set[str] = set()
        self._mismatch: dict[str, int] = {}

    # -- bookkeeping ------------------------------------------------------------------
    def open_trade(self, intent_id: str, plan: EntryPlan, entry_id: str, now_ns: int) -> Trade:
        trade = Trade(
            intent_id,
            plan.symbol,
            plan.quantity,
            plan.limit_price,
            plan.stop_price,
            plan.target_price,
            entry_id,
            now_ns,
        )
        trade.live = {entry_id}
        self.trades[intent_id] = trade
        self._orders[entry_id] = (intent_id, Role.ENTRY)
        return trade

    def register_stop(self, intent_id: str, order_id: str, quantity: int, now_ns: int) -> None:
        trade = self.trades[intent_id]
        trade.stop_id, trade.stop_qty, trade.stop_sent_ns, trade.stop_acked = (
            order_id,
            quantity,
            now_ns,
            False,
        )
        trade.live.add(order_id)
        self._orders[order_id] = (intent_id, Role.STOP)

    def register_exit(self, intent_id: str, order_id: str) -> None:
        trade = self.trades[intent_id]
        trade.exit_ids.append(order_id)
        trade.live.add(order_id)
        trade.phase = Phase.EXITING
        self._orders[order_id] = (intent_id, Role.EXIT)

    def lookup(self, order_id: str) -> tuple[Trade, Role] | None:
        found = self._orders.get(order_id)
        return (self.trades[found[0]], found[1]) if found else None

    def active(self) -> list[Trade]:
        return [t for t in self.trades.values() if not t.done]

    def positions(self) -> dict[str, int]:
        held: dict[str, int] = {}
        for t in self.trades.values():
            if t.position:
                held[t.symbol] = held.get(t.symbol, 0) + t.position
        return held

    def pending_entries(self) -> dict[str, float]:
        """Notional of entry shares still working, by symbol."""
        return {
            t.symbol: (t.planned_qty - t.filled_qty) * t.limit_price
            for t in self.active()
            if not t.entry_done and t.planned_qty > t.filled_qty
        }

    # -- events -----------------------------------------------------------------------
    def on_accepted(self, order_id: str, now_ns: int) -> list[Action]:
        found = self.lookup(order_id)
        if found is None:
            return [Alert("warning", f"accepted event for unknown order {order_id}")]
        trade, role = found
        if role == Role.ENTRY:
            self.state.record_accept()
        elif role == Role.STOP and order_id == trade.stop_id:
            trade.stop_acked = True
            if trade.phase == Phase.PROTECTING:
                trade.phase = Phase.OPEN
        return []

    def on_rejected(self, order_id: str, reason: str, now_ns: int) -> list[Action]:
        found = self.lookup(order_id)
        if found is None:
            return [Alert("warning", f"reject for unknown order {order_id}: {reason}")]
        trade, role = found
        trade.live.discard(order_id)
        trade.pending_cancels.discard(order_id)
        if role == Role.ENTRY:
            actions: list[Action] = [Alert("warning", f"entry rejected: {reason}", trade.symbol)]
            if self.state.record_reject(reason):
                actions.append(Alert("critical", "too many rejected entries: new entries halted for the day"))
            return actions + self._entry_finished(trade, now_ns)
        if role == Role.STOP:
            return self._stop_lost(trade, f"stop rejected: {reason}", now_ns)
        return self._exit_failed(trade, f"exit rejected: {reason}", now_ns)

    def on_canceled(self, order_id: str, now_ns: int) -> list[Action]:
        found = self.lookup(order_id)
        if found is None:
            return []
        trade, role = found
        trade.live.discard(order_id)
        requested = order_id in trade.pending_cancels
        trade.pending_cancels.discard(order_id)
        if trade.done:
            return []
        if role == Role.ENTRY:
            # IOC remainder, our timeout cancel, or a flatten cancel
            return self._entry_finished(trade, now_ns)
        if trade.phase == Phase.FLATTENING:
            return self._after_cancel(trade, now_ns)
        if role == Role.STOP and not requested:
            return self._stop_lost(trade, "stop cancelled or expired outside FlowForge", now_ns)
        if role == Role.EXIT:
            return self._exit_failed(trade, "exit cancelled or expired", now_ns)
        return []

    def on_cancel_rejected(self, order_id: str, reason: str) -> list[Action]:
        found = self.lookup(order_id)
        if found is None:
            return []
        # Usually the order filled first; its fill event clears it. Otherwise the
        # flatten timeout escalates. Never sell while it might still be live.
        return [Alert("warning", f"cancel refused for {order_id}: {reason}", found[0].symbol)]

    def on_updated(self, order_id: str, quantity: int) -> None:
        found = self.lookup(order_id)
        if found is not None and order_id == found[0].stop_id:
            found[0].stop_qty = quantity

    def on_filled(
        self, order_id: str, fill_id: str, quantity: int, now_ns: int, complete: bool
    ) -> list[Action]:
        """`complete` is True when this fill leaves the order fully filled."""
        if fill_id in self._fills:
            return [Alert("info", f"duplicate fill {fill_id} ignored")]
        self._fills.add(fill_id)
        found = self.lookup(order_id)
        if found is None:
            reason = f"fill for unknown order {order_id}"
            self.state.halt_day("errors", reason)
            return [Alert("critical", reason + ": new entries halted, check the account")]
        trade, role = found
        if complete:
            trade.live.discard(order_id)
            trade.pending_cancels.discard(order_id)

        if role == Role.ENTRY:
            trade.filled_qty += quantity
            if trade.filled_qty > trade.planned_qty:
                self.state.halt_day("errors", f"overfill on {trade.symbol}")
                alert = Alert("critical", f"overfill: {trade.filled_qty} > {trade.planned_qty}", trade.symbol)
                trade.entry_done = trade.entry_done or complete
                return [alert, *self.flatten(trade, "overfill", now_ns)]
            return self._entry_finished(trade, now_ns) if complete else []

        trade.sold_qty += quantity
        if role == Role.STOP:
            trade.stop_qty = max(0, trade.stop_qty - quantity)
        if trade.position < 0:
            reason = f"short {-trade.position} shares of {trade.symbol}: sold more than held"
            return [Alert("critical", reason, trade.symbol), EngageKill(reason)]
        if trade.position > 0:
            return []
        actions: list[Action] = []
        if not trade.entry_done and not trade.entry_cancel_sent and trade.entry_id in trade.live:
            # Sold everything while the entry is somehow still working: don't buy more.
            trade.entry_cancel_sent = True
            trade.pending_cancels.add(trade.entry_id)
            actions.append(Cancel(trade.entry_id, f"{role} filled while entry working"))
        for other in sorted(trade.live - {trade.entry_id}):
            trade.pending_cancels.add(other)
            actions.append(Cancel(other, "trade over"))
        if trade.entry_done and not trade.live:
            trade.phase = Phase.CLOSED
        return actions

    def on_price(self, symbol: str, bid: float, now_ns: int) -> list[Action]:
        """Target check, called on every quote."""
        actions: list[Action] = []
        for t in self.active():
            if t.symbol == symbol and t.phase == Phase.OPEN and bid >= t.target_price:
                actions += self.flatten(t, f"target {t.target_price} reached (bid {bid})", now_ns)
        return actions

    # -- timers -----------------------------------------------------------------------
    def on_tick(self, now_ns: int) -> list[Action]:
        actions: list[Action] = []
        for t in self.active():
            if t.phase == Phase.ENTERING and not t.entry_cancel_sent:
                if now_ns - t.submitted_ns >= ENTRY_TIMEOUT_S * NS:
                    t.entry_cancel_sent = True
                    actions.append(Cancel(t.entry_id, f"entry not done in {ENTRY_TIMEOUT_S} s"))
            if t.phase == Phase.PROTECTING and now_ns - t.stop_sent_ns >= STOP_ACK_TIMEOUT_S * NS:
                reason = f"stop not confirmed by the broker in {STOP_ACK_TIMEOUT_S} s"
                actions += [
                    Alert("critical", reason + ": flattening", t.symbol),
                    *self.flatten(t, reason, now_ns),
                ]
            if t.phase == Phase.FLATTENING and t.pending_cancels and not t.flatten_timed_out:
                if now_ns - t.flatten_started_ns >= FLATTEN_CANCEL_TIMEOUT_S * NS:
                    t.flatten_timed_out = True
                    reason = (
                        f"could not confirm cancels for {t.symbol} within {FLATTEN_CANCEL_TIMEOUT_S} s; "
                        "not selling while a sell order may be live. Check the account."
                    )
                    actions += [Alert("critical", reason, t.symbol), EngageKill(reason)]
            if t.exit_retry_ns is not None and now_ns >= t.exit_retry_ns:
                t.exit_retry_ns = None
                actions.append(SubmitExit(t.intent_id, t.symbol, t.position, t.flatten_reason or "retry"))
        return actions

    # -- flatten ----------------------------------------------------------------------
    def flatten(self, trade: Trade, reason: str, now_ns: int) -> list[Action]:
        if trade.done or trade.phase in (Phase.FLATTENING, Phase.EXITING):
            return []
        trade.phase = Phase.FLATTENING
        trade.flatten_reason = reason
        trade.flatten_started_ns = now_ns
        actions: list[Action] = []
        for order_id in sorted(trade.live):
            trade.pending_cancels.add(order_id)
            actions.append(Cancel(order_id, reason))
        if trade.entry_id in trade.pending_cancels:
            trade.entry_cancel_sent = True
        return actions + self._after_cancel(trade, now_ns)

    def flatten_all(self, reason: str, now_ns: int) -> list[Action]:
        return [a for t in self.active() for a in self.flatten(t, reason, now_ns)]

    # -- reconciliation ---------------------------------------------------------------
    def reconcile(self, broker_positions: Mapping[str, int]) -> list[Action]:
        ours = self.positions()
        actions: list[Action] = []
        for symbol in sorted(set(ours) | set(broker_positions)):
            mine, theirs = ours.get(symbol, 0), broker_positions.get(symbol, 0)
            if mine == theirs:
                self._mismatch.pop(symbol, None)
                continue
            self._mismatch[symbol] = self._mismatch.get(symbol, 0) + 1
            if self._mismatch[symbol] == RECONCILE_GRACE:
                # The node can't safely manage shares it doesn't know about (or a position
                # it thinks it has but the broker doesn't). The kill switch hands the
                # account to the watchdog, which cancels everything and sells what is held.
                reason = f"position mismatch on {symbol}: ours {mine}, broker {theirs}"
                self.state.halt_day("errors", reason)
                actions += [Alert("critical", reason + ": kill switch on", symbol), EngageKill(reason)]
        return actions

    # -- internals --------------------------------------------------------------------
    def _entry_finished(self, trade: Trade, now_ns: int) -> list[Action]:
        """The entry is done (filled, rejected or cancelled): protect what we hold."""
        trade.entry_done = True
        trade.live.discard(trade.entry_id)
        if trade.phase == Phase.FLATTENING:
            return self._after_cancel(trade, now_ns)
        if trade.position == 0:
            trade.phase = Phase.CLOSED if trade.filled_qty else Phase.FAILED
            return []
        if trade.stop_id is None:
            trade.phase = Phase.PROTECTING
            return [PlaceStop(trade.intent_id, trade.symbol, trade.position, trade.stop_price)]
        if trade.stop_id in trade.live and trade.stop_qty != trade.position:
            return [Resize(trade.stop_id, trade.position, "match shares held")]
        return []

    def _stop_lost(self, trade: Trade, reason: str, now_ns: int) -> list[Action]:
        if trade.phase == Phase.FLATTENING:
            return self._after_cancel(trade, now_ns)
        if trade.position == 0:
            return [Alert("warning", reason, trade.symbol)]
        alert = Alert("critical", f"{reason}: position unprotected, flattening", trade.symbol)
        return [alert, *self.flatten(trade, reason, now_ns)]

    def _after_cancel(self, trade: Trade, now_ns: int) -> list[Action]:
        if trade.pending_cancels or trade.live - set(trade.exit_ids):
            return []
        if trade.position > 0:
            trade.exit_attempts += 1
            reason = trade.flatten_reason or "flatten"
            return [SubmitExit(trade.intent_id, trade.symbol, trade.position, reason)]
        trade.phase = Phase.CLOSED if trade.filled_qty else Phase.FAILED
        return []

    def _exit_failed(self, trade: Trade, reason: str, now_ns: int) -> list[Action]:
        if trade.position <= 0:
            return []
        if trade.exit_attempts >= MAX_EXIT_ATTEMPTS:
            msg = (
                f"{reason}; gave up after {trade.exit_attempts} attempts, {trade.position} shares still held"
            )
            return [Alert("critical", msg, trade.symbol), EngageKill(msg)]
        trade.exit_attempts += 1
        trade.exit_retry_ns = now_ns + EXIT_RETRY_S * NS
        return [Alert("critical", f"{reason}; retrying in {EXIT_RETRY_S} s", trade.symbol)]
