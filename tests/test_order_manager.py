"""Order lifecycle fault tests on the pure order manager (no broker, no Nautilus)."""

import pytest
from conftest import DAY

from flowforge.risk.calendar import NS
from flowforge.risk.guard import EntryPlan
from flowforge.risk.orders import (
    ENTRY_TIMEOUT_S,
    EXIT_RETRY_S,
    FLATTEN_CANCEL_TIMEOUT_S,
    MAX_EXIT_ATTEMPTS,
    RECONCILE_GRACE,
    STOP_ACK_TIMEOUT_S,
    Alert,
    Cancel,
    OrderManager,
    Phase,
    PlaceStop,
    Resize,
    SubmitExit,
)
from flowforge.risk.state import MAX_CONSECUTIVE_REJECTS, RiskState

T0 = 1_000 * NS


@pytest.fixture
def om(limits):
    state = RiskState(limits)
    state.start_day(DAY, 10_000.0)
    return OrderManager(state)


def kinds(actions):
    return [type(a).__name__ for a in actions]


def enter(om, intent="t1", qty=10):
    return om.open_trade(intent, EntryPlan("SPY", qty, 100.12, 99.0, 102.0), f"{intent}-E", T0)


def protect(om, t, filled=None, ack=True):
    """Fill the entry (fully, or partly then the IOC remainder cancels) and place the stop."""
    filled = filled or t.planned_qty
    if filled == t.planned_qty:
        actions = om.on_filled(t.entry_id, f"{t.intent_id}-f", filled, T0, True)
    else:
        om.on_filled(t.entry_id, f"{t.intent_id}-f", filled, T0, False)
        actions = om.on_canceled(t.entry_id, T0)
    assert actions == [PlaceStop(t.intent_id, "SPY", filled, 99.0)]
    assert t.phase == Phase.PROTECTING
    om.register_stop(t.intent_id, f"{t.intent_id}-S", filled, T0)
    if ack:
        assert om.on_accepted(f"{t.intent_id}-S", T0) == []
        assert t.phase == Phase.OPEN
    return t


def exit_fill(om, t, n=1):
    oid = f"{t.intent_id}-X{n}"
    om.register_exit(t.intent_id, oid)
    return om.on_filled(oid, f"{oid}-f", t.position, T0, True)


# -- normal life --------------------------------------------------------------------------
def test_entry_fill_places_a_stop_for_exactly_the_shares_held(om):
    t = protect(om, enter(om))
    assert om.positions() == {"SPY": 10} and t.live == {"t1-S"}


def test_target_is_watched_by_flowforge_one_sell_at_a_time(om):
    t = protect(om, enter(om))
    assert om.on_price("SPY", 101.99, T0) == []
    assert om.on_price("QQQ", 500.0, T0) == []
    assert om.on_price("SPY", 102.00, T0) == [Cancel("t1-S", "target 102.0 reached (bid 102.0)")]
    assert om.on_price("SPY", 102.50, T0) == []  # already flattening
    assert om.on_canceled("t1-S", T0) == [SubmitExit("t1", "SPY", 10, "target 102.0 reached (bid 102.0)")]
    assert exit_fill(om, t) == []
    assert t.phase == Phase.CLOSED and om.positions() == {}


def test_stop_fill_closes_the_trade(om):
    t = protect(om, enter(om))
    assert om.on_filled("t1-S", "fs", 10, T0, True) == []
    assert t.phase == Phase.CLOSED


def test_stop_partial_fills_keep_the_trade_open_until_flat(om):
    t = protect(om, enter(om))
    assert om.on_filled("t1-S", "fs1", 4, T0, False) == []
    assert t.position == 6 and t.stop_qty == 6 and t.phase == Phase.OPEN
    om.on_filled("t1-S", "fs2", 6, T0, True)
    assert t.phase == Phase.CLOSED


# -- partial fills -----------------------------------------------------------------------
def test_partial_ioc_entry_protects_only_what_was_bought(om):
    t = protect(om, enter(om), filled=4)
    assert t.stop_qty == 4 and om.positions() == {"SPY": 4}
    assert om.pending_entries() == {}


def test_pending_entry_notional_while_entering(om):
    enter(om)
    assert om.pending_entries() == {"SPY": pytest.approx(10 * 100.12)}


def test_entry_that_rests_is_cancelled_after_timeout(om):
    t = enter(om)
    assert om.on_tick(T0 + ENTRY_TIMEOUT_S * NS - 1) == []
    assert om.on_tick(T0 + ENTRY_TIMEOUT_S * NS) == [Cancel("t1-E", f"entry not done in {ENTRY_TIMEOUT_S} s")]
    assert om.on_tick(T0 + 2 * ENTRY_TIMEOUT_S * NS) == []  # asked once
    assert om.on_canceled("t1-E", T0) == []
    assert t.phase == Phase.FAILED


def test_existing_stop_is_resized_if_more_shares_arrive(om):
    t = protect(om, enter(om), filled=4)
    # a late fill report for the entry (venue sent it after the IOC cancel)
    t.entry_done = True
    t.filled_qty += 2
    assert om._entry_finished(t, T0) == [Resize("t1-S", 6, "match shares held")]
    om.on_updated("t1-S", 6)
    assert om._entry_finished(t, T0) == []
    om.on_updated("t1-E", 1)  # entry quantity updates are not tracked
    assert t.stop_qty == 6


# -- stop problems ------------------------------------------------------------------------
def test_stop_rejected_flattens_at_once(om):
    t = protect(om, enter(om), ack=False)
    actions = om.on_rejected("t1-S", "stop price in the market", T0)
    assert kinds(actions) == ["Alert", "SubmitExit"]
    assert actions[0].level == "critical" and actions[1].quantity == 10
    exit_fill(om, t)
    assert t.phase == Phase.CLOSED


def test_stop_not_confirmed_in_time_flattens(om):
    protect(om, enter(om), ack=False)
    assert om.on_tick(T0 + STOP_ACK_TIMEOUT_S * NS - 1) == []
    actions = om.on_tick(T0 + STOP_ACK_TIMEOUT_S * NS)
    assert kinds(actions) == ["Alert", "Cancel"]
    assert om.on_canceled("t1-S", T0)[0].quantity == 10


def test_stop_cancelled_outside_flowforge_flattens(om):
    protect(om, enter(om))
    assert kinds(om.on_canceled("t1-S", T0)) == ["Alert", "SubmitExit"]


def test_stop_lost_with_nothing_held_is_only_a_warning(om):
    t = protect(om, enter(om))
    t.sold_qty = t.filled_qty  # e.g. sold by an exit we already counted
    assert om._stop_lost(t, "late", T0) == [Alert("warning", "late", "SPY")]


# -- flattening ---------------------------------------------------------------------------
def test_never_sells_while_a_cancel_is_unconfirmed(om):
    t = protect(om, enter(om))
    assert om.flatten_all("kill switch", T0) == [Cancel("t1-S", "kill switch")]
    assert om.flatten_all("again", T0) == []
    assert om.on_cancel_rejected("t1-S", "order already filled")[0].level == "warning"
    assert om.on_tick(T0 + FLATTEN_CANCEL_TIMEOUT_S * NS - 1) == []
    assert kinds(om.on_tick(T0 + FLATTEN_CANCEL_TIMEOUT_S * NS)) == ["Alert", "EngageKill"]
    assert om.on_tick(T0 + 2 * FLATTEN_CANCEL_TIMEOUT_S * NS) == []
    assert om.on_filled("t1-S", "fs", 10, T0, True) == []  # it had in fact filled
    assert t.phase == Phase.CLOSED and om.positions() == {}


def test_stop_fill_racing_a_flatten_does_not_double_sell(om):
    t = protect(om, enter(om))
    om.flatten(t, "end of day", T0)
    assert om.on_filled("t1-S", "fs", 10, T0, True) == []
    assert t.phase == Phase.CLOSED
    assert not any(isinstance(a, SubmitExit) for a in om.on_tick(T0 + NS))


def test_flatten_while_entering_cancels_then_sells_what_filled(om):
    t = enter(om)
    om.on_filled("t1-E", "f1", 3, T0, False)
    assert om.flatten(t, "kill", T0) == [Cancel("t1-E", "kill")]
    om.on_filled("t1-E", "f2", 2, T0, False)  # lands before the cancel
    assert om.on_canceled("t1-E", T0) == [SubmitExit("t1", "SPY", 5, "kill")]


def test_flatten_unfilled_entry_just_cancels(om):
    t = enter(om)
    om.flatten(t, "kill", T0)
    assert om.on_canceled("t1-E", T0) == []
    assert t.phase == Phase.FAILED
    assert om.flatten(t, "kill", T0) == []


def test_entry_completing_during_flatten_is_sold(om):
    t = enter(om)
    om.flatten(t, "kill", T0)
    assert om.on_filled("t1-E", "f1", 10, T0, True) == [SubmitExit("t1", "SPY", 10, "kill")]


def test_flatten_while_protecting_cancels_the_unacked_stop(om):
    t = protect(om, enter(om), ack=False)
    assert om.flatten(t, "kill", T0) == [Cancel("t1-S", "kill")]
    assert om.on_accepted("t1-S", T0) == []  # late ack doesn't reopen it
    assert t.phase == Phase.FLATTENING
    assert om.on_canceled("t1-S", T0) == [SubmitExit("t1", "SPY", 10, "kill")]


# -- rejects, duplicates, unknowns ---------------------------------------------------------
def test_rejected_entries_trip_the_error_halt(om):
    for i in range(MAX_CONSECUTIVE_REJECTS):
        t = enter(om, intent=f"t{i}")
        actions = om.on_rejected(t.entry_id, "insufficient buying power", T0)
        assert t.phase == Phase.FAILED
    assert actions[1] == Alert("critical", "too many rejected entries: new entries halted for the day")
    assert "entries rejected in a row" in om.state.halt_reason()


def test_accepted_entry_resets_the_reject_count(om):
    om.on_rejected(enter(om, intent="a").entry_id, "x", T0)
    enter(om, intent="b")
    om.on_accepted("b-E", T0)
    assert om.state.consecutive_rejects == 0


def test_duplicate_and_unknown_fills(om):
    t = protect(om, enter(om))
    assert om.on_filled("t1-E", "t1-f", 10, T0, True) == [Alert("info", "duplicate fill t1-f ignored")]
    assert om.positions() == {"SPY": 10} and t.phase == Phase.OPEN
    actions = om.on_filled("nope", "f77", 5, T0, True)
    assert actions[0].level == "critical"
    assert "fill for unknown order nope" in om.state.halt_reason()


def test_overfill_halts_and_flattens(om):
    t = enter(om)
    actions = om.on_filled("t1-E", "f1", 12, T0, True)
    assert actions[0].level == "critical" and "overfill" in om.state.halt_reason()
    assert actions[-1] == SubmitExit("t1", "SPY", 12, "overfill")
    assert t.phase == Phase.FLATTENING


def test_selling_more_than_held_engages_kill(om):
    protect(om, enter(om), filled=4)
    assert kinds(om.on_filled("t1-S", "fs", 5, T0, True)) == ["Alert", "EngageKill"]


def test_sold_out_while_entry_still_working_cancels_the_entry(om):
    t = enter(om)
    om.on_filled("t1-E", "f1", 4, T0, False)
    # a manual stop someone placed in our name (or a venue quirk) sells the 4 shares
    om.register_stop("t1", "t1-S", 4, T0)
    assert om.on_filled("t1-S", "fs", 4, T0, True) == [Cancel("t1-E", "stop filled while entry working")]
    assert om.on_canceled("t1-E", T0) == []
    assert t.phase == Phase.CLOSED


def test_exit_rejected_is_retried_then_escalated(om):
    t = protect(om, enter(om))
    om.on_canceled("t1-S", T0)  # outside cancel -> exit 1
    om.register_exit("t1", "x1")
    for attempt in range(1, MAX_EXIT_ATTEMPTS):
        assert om.on_rejected(f"x{attempt}", "halted", T0)[0].level == "critical"
        assert om.on_tick(T0 + EXIT_RETRY_S * NS - 1) == []
        retry = om.on_tick(T0 + EXIT_RETRY_S * NS)
        assert retry == [SubmitExit("t1", "SPY", 10, "stop cancelled or expired outside FlowForge")]
        om.register_exit("t1", f"x{attempt + 1}")
    assert kinds(om.on_canceled(f"x{MAX_EXIT_ATTEMPTS}", T0)) == ["Alert", "EngageKill"]
    assert t.position == 10


def test_exit_failure_with_nothing_held_is_ignored(om):
    t = protect(om, enter(om))
    om.flatten(t, "x", T0)
    om.on_canceled("t1-S", T0)
    exit_fill(om, t)
    assert om._exit_failed(t, "late", T0) == []


def test_reconcile_mismatch_halts_after_grace(om):
    protect(om, enter(om))
    for _ in range(RECONCILE_GRACE - 1):
        assert om.reconcile({"SPY": 7}) == []
    assert om.reconcile({"SPY": 10}) == []  # recovered: counter resets
    for _ in range(RECONCILE_GRACE - 1):
        om.reconcile({"SPY": 10, "TSLA": 3})
    actions = om.reconcile({"SPY": 10, "TSLA": 3})
    assert kinds(actions) == ["Alert", "EngageKill"] and "ours 0, broker 3" in actions[0].message
    assert om.reconcile({"SPY": 10, "TSLA": 3}) == []  # reported once


def test_events_for_unknown_orders_are_harmless(om):
    assert om.on_accepted("zz", T0)[0].level == "warning"
    assert om.on_rejected("zz", "r", T0)[0].level == "warning"
    assert om.on_canceled("zz", T0) == []
    assert om.on_cancel_rejected("zz", "r") == []
    om.on_updated("zz", 3)
    assert om.lookup("zz") is None


def test_late_events_after_close_are_ignored(om):
    t = protect(om, enter(om))
    om.on_filled("t1-S", "fs", 10, T0, True)
    assert om.on_canceled("t1-S", T0) == []
    assert t.done
