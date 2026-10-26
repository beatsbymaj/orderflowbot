"""Chaos tests on the Nautilus simulated venue (Phase 4 exit gate, ROADMAP.md).

Each test injects one fault through the data or the venue and checks that the guarded
strategy stays safe: no entry it shouldn't make, no position without a stop, no sale
bigger than the position, and a journal entry explaining what happened.
"""

from conftest import et_ns
from sim import path, run_sim, to_data

from flowforge.risk.guard import EntryRequest

T = et_ns(10, 0)  # entries happen at 10:00 ET on 2026-10-27
WARM = et_ns(9, 50)  # ten minutes of tape first, so the volume average exists


def flat_then(after, seconds_before=600, seconds_after=120, base=100.0):
    """Flat at `base` until T, then after(i) for each second."""
    pre = path(WARM, seconds_before, lambda i: base)
    post = path(T, seconds_after, after)
    return pre + post


def test_bracket_target_hit(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0 + 0.05 * i)
    r = run_sim(limits, to_data("SPY", ticks), [(T, EntryRequest("s1", "SPY", 99.0, 101.0))], tmp_path)
    d = r.strategy.decisions[0]
    assert d.allowed, d.reasons
    closed = r.events("position_closed")
    assert len(closed) == 1 and closed[0]["realized_pnl"] > 0
    assert r.strategy.manager.positions() == {}
    assert not r.alerts("critical")


def test_stop_gap_down_fills_through_and_the_loss_is_journaled(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0 if i < 5 else 98.50)  # gaps through the 99.00 stop
    r = run_sim(limits, to_data("SPY", ticks), [(T, EntryRequest("s1", "SPY", 99.0, 102.0))], tmp_path)
    fills = r.events("fill")
    assert [f["side"] for f in fills] == ["BUY", "SELL"]
    assert fills[1]["price"] == 98.50  # gap: filled at the bid, worse than the stop
    assert r.events("position_closed")[0]["realized_pnl"] < 0
    assert r.strategy.manager.positions() == {}


def test_feed_drop_blocks_entries_and_warns_while_holding(limits, tmp_path):
    ticks = path(WARM, 600, lambda i: 100.0) + path(T, 30, lambda i: 100.0)
    ticks += path(T + 120 * 10**9, 60, lambda i: 100.0)  # 90 s with no data at all
    script = [
        (T, EntryRequest("held", "SPY", 99.0, 102.0)),
        (T + 60 * 10**9, EntryRequest("blind", "SPY", 99.0, 102.0)),
    ]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path)
    first, blind = r.strategy.decisions
    assert first.allowed
    assert any(x.startswith("stale quote") for x in blind.reasons)
    assert "no fresh quotes while holding: broker stop still protects" in r.alerts("warning")
    assert r.events("feed_restored")


def test_stale_and_future_stamped_quotes_are_refused(limits, tmp_path):
    late = flat_then(lambda i: 100.0)
    for t in late[600:]:
        t.ts_event_offset_ns = -3 * 10**9  # quotes arrive 3 s after their exchange time
    r = run_sim(
        limits, to_data("SPY", late), [(T + 5 * 10**9, EntryRequest("a", "SPY", 99.0, 102.0))], tmp_path
    )
    assert any(x.startswith("stale quote") for x in r.strategy.decisions[0].reasons)

    skew = flat_then(lambda i: 100.0)
    for t in skew[600:]:
        t.ts_event_offset_ns = 1 * 10**9  # our clock is 1 s behind the exchange
    r = run_sim(
        limits, to_data("SPY", skew), [(T + 5 * 10**9, EntryRequest("b", "SPY", 99.0, 102.0))], tmp_path / "2"
    )
    assert any(x.startswith("clock skew") for x in r.strategy.decisions[0].reasons)


def test_halt_blocks_entries_then_cooldown_after_resume(limits, tmp_path):
    from nautilus_trader.model import MarketStatusAction
    from sim import status

    ticks = flat_then(lambda i: 100.0, seconds_after=900)
    data = to_data("SPY", ticks) + [
        status("SPY", T + 10 * 10**9, MarketStatusAction.HALT),
        status("SPY", T + 60 * 10**9, MarketStatusAction.TRADING),
    ]
    script = [
        (T + 20 * 10**9, EntryRequest("during", "SPY", 99.0, 102.0)),
        (T + 120 * 10**9, EntryRequest("cooling", "SPY", 99.0, 102.0)),
        (T + 400 * 10**9, EntryRequest("after", "SPY", 99.0, 102.0)),
    ]
    r = run_sim(limits, sorted(data, key=lambda d: d.ts_init), script, tmp_path)
    during, cooling, after = r.strategy.decisions
    assert "trading halted or paused" in during.reasons
    assert "cooling down after a halt" in cooling.reasons
    assert after.allowed, after.reasons


def test_duplicate_signal_sends_one_bracket(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0)
    script = [
        (T, EntryRequest("same", "SPY", 99.0, 102.0)),
        (T + 10**9, EntryRequest("same", "SPY", 99.0, 102.0)),
    ]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path)
    first, second = r.strategy.decisions
    assert first.allowed
    assert "duplicate signal same" in second.reasons
    assert len(r.events("order_submit")) == 1
    assert len([f for f in r.events("fill") if f["side"] == "BUY"]) == 1


def test_stop_rejected_after_entry_fill_is_flattened(limits, tmp_path):
    # The market gaps below the stop between our order and its fill, so the venue
    # rejects the stop child (its trigger is already through the market).
    ticks = flat_then(lambda i: 100.0 if i == 0 else 99.80, seconds_after=30)
    script = [(T, EntryRequest("s1", "SPY", 99.90, 102.0))]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path, venue_kwargs={"reject_stop_orders": True})
    assert r.events("order_rejected"), "the stop should have been rejected"
    assert any("position unprotected, flattening" in m for m in r.alerts("critical"))
    sides = [f["side"] for f in r.events("fill")]
    assert sides == ["BUY", "SELL"]
    assert r.strategy.manager.positions() == {}
    assert all(p.is_closed for p in r.positions)


def test_rejected_entries_halt_trading_for_the_day(limits, tmp_path):
    from sim import ScriptedStrategy

    from flowforge.risk.guard import AccountView

    class OverconfidentCash(ScriptedStrategy):
        """Believes it has $1M of cash, so the venue/RiskEngine refuses the orders."""

        def account_view(self):
            v = super().account_view()
            return AccountView(1_000_000.0, 1_000_000.0, v.positions, v.position_notional, v.pending_entries)

    ticks = flat_then(lambda i: 100.0, seconds_after=60)
    script = [(T + i * 2 * 10**9, EntryRequest(f"r{i}", "SPY", 99.0, 102.0)) for i in range(4)]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path, strategy_cls=OverconfidentCash)
    rejected = r.events("order_denied") + r.events("order_rejected")
    assert len(rejected) == 3  # the 4th is refused by the guard before any order is built
    assert any("too many rejected entries" in m for m in r.alerts("critical"))
    assert any("entries rejected in a row" in x for x in r.strategy.decisions[3].reasons)
    assert r.strategy.manager.positions() == {}


def test_kill_switch_file_flattens_and_blocks(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0, seconds_after=60)

    def pull_switch(strategy):
        strategy.guard.kill.engage("operator test", "pytest")

    script = [
        (T, EntryRequest("s1", "SPY", 99.0, 102.0)),
        (T + 10 * 10**9, pull_switch),
        (T + 20 * 10**9, EntryRequest("s2", "SPY", 99.0, 102.0)),
    ]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path)
    assert r.events("flatten_all")[0]["reason"] == "kill switch"
    assert [f["side"] for f in r.events("fill")] == ["BUY", "SELL"]
    assert r.strategy.manager.positions() == {}
    assert any(x.startswith("kill switch on: operator test") for x in r.strategy.decisions[1].reasons)
    # sold only after the stop was cancelled
    names = [e["event"] for e in r.journal.records]
    assert names.index("order_canceled") < names.index("exit_submit")


def test_daily_loss_limit_flattens_and_halts(limits, tmp_path):
    from conftest import with_limits

    tight = with_limits(limits, "loss", max_daily_loss_pct=0.03, max_weekly_drawdown_pct=5)
    ticks = flat_then(lambda i: 100.0 if i < 5 else 99.50, seconds_after=60)
    script = [
        (T, EntryRequest("s1", "SPY", 99.0, 102.0)),
        (T + 30 * 10**9, EntryRequest("s2", "SPY", 99.0, 102.0)),
    ]
    r = run_sim(tight, to_data("SPY", ticks), script, tmp_path)
    assert any("daily loss limit hit" in m for m in r.alerts("critical"))
    assert r.events("flatten_all")[0]["reason"] == "daily loss limit"
    assert r.strategy.manager.positions() == {}
    assert any(x.startswith("halted for the day: daily loss") for x in r.strategy.decisions[1].reasons)


def test_end_of_day_flatten(limits, tmp_path):
    start = et_ns(15, 30)
    ticks = path(start, 1_800, lambda i: 100.0)  # 15:30 to 16:00
    r = run_sim(
        limits, to_data("SPY", ticks), [(et_ns(15, 40), EntryRequest("late", "SPY", 99.0, 102.0))], tmp_path
    )
    assert r.strategy.decisions[0].allowed
    flat = r.events("flatten_all")
    assert flat and flat[0]["reason"] == "end of day" and flat[0]["ts"].startswith("2026-10-27T19:55:00")
    assert r.strategy.manager.positions() == {}


def test_partial_fill_protects_only_the_shares_bought(limits, tmp_path):
    from nautilus_trader.model import ClientOrderId

    # Only 4 shares offered at the ask and no prints at our price: the IOC entry gets 4.
    ticks = flat_then(lambda i: 100.0 if i < 2 else 100.50, seconds_after=60)
    for t in ticks[600:603]:
        t.size, t.trade_size = 4, 0
    venue = {"liquidity_consumption": True}
    r = run_sim(
        limits,
        to_data("SPY", ticks),
        [(T, EntryRequest("p1", "SPY", 99.0, 102.0))],
        tmp_path,
        venue_kwargs=venue,
    )
    buys = [f for f in r.events("fill") if f["side"] == "BUY"]
    assert sum(f["quantity"] for f in buys) == 4
    trade = r.strategy.manager.trades["p1"]
    assert trade.position == 4 and trade.phase == "open"
    stop = r.engine.cache.order(ClientOrderId(trade.stop_id))
    assert int(stop.quantity.as_double()) == 4 and stop.is_open
    assert not r.alerts("critical")


def test_unknown_position_in_the_account_halts_entries(limits, tmp_path):
    from nautilus_trader.model import OrderSide, Quantity
    from nautilus_trader.trading import Strategy, StrategyConfig

    from flowforge.data.ticks import instrument_id

    class Outsider(Strategy):
        """Someone buys in the same account outside FlowForge."""

        def __new__(cls, *a, **k):
            return super().__new__(cls)

        def __init__(self):
            super().__init__(StrategyConfig(order_id_tag="OUT"))
            self.done = False

        def on_start(self):
            self.subscribe_quotes(instrument_id("SPY"))

        def on_quote(self, q):
            if not self.done and q.ts_event >= T:
                self.done = True
                self.submit_order(
                    self.order_factory.market(q.instrument_id, OrderSide.BUY, Quantity.from_int(3))
                )

    ticks = flat_then(lambda i: 100.0, seconds_after=60)
    script = [(T + 30 * 10**9, EntryRequest("after", "SPY", 99.0, 102.0))]
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path, extra_strategies=[Outsider()])
    assert any("position mismatch on SPY: ours 0, broker 3" in m for m in r.alerts("critical"))
    assert any("position mismatch" in x for x in r.strategy.decisions[0].reasons)


def test_frozen_node_stops_sending_orders(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0, seconds_after=60)

    def freeze(strategy):  # pretend the process stalled for 20 s before this tick
        strategy._last_tick_ns -= 20 * 10**9

    script = [(T, EntryRequest("s1", "SPY", 99.0, 102.0)), (T + 10 * 10**9, freeze)]
    script.append((T + 15 * 10**9, lambda s: s.request_exit("s1", "signal exit")))
    r = run_sim(limits, to_data("SPY", ticks), script, tmp_path)
    assert any("node froze for" in m for m in r.alerts("critical"))
    assert any(m.startswith("keeping ") and "node was frozen" in m for m in r.alerts("critical"))
    assert [f["side"] for f in r.events("fill")] == ["BUY"]  # no sale from a frozen node
    from nautilus_trader.model import ClientOrderId

    stop = r.engine.cache.order(ClientOrderId(r.strategy.manager.trades["s1"].stop_id))
    assert stop.is_open  # and the stop stays in place
    assert any("node froze" in x for x in [r.state.halt_reason()])


def test_after_the_kill_window_only_the_watchdog_sells(limits, tmp_path):
    from conftest import with_limits

    no_flatten = with_limits(limits, "kill_switch", flatten_on_kill=False)
    ticks = flat_then(lambda i: 100.0, seconds_after=60)
    script = [
        (T, EntryRequest("s1", "SPY", 99.0, 102.0)),
        (T + 10 * 10**9, lambda s: s.guard.kill.engage("operator", "pytest")),
        (T + 40 * 10**9, lambda s: s.request_exit("s1", "late signal exit")),
    ]
    r = run_sim(no_flatten, to_data("SPY", ticks), script, tmp_path)
    assert any("the watchdog owns flattening now" in m for m in r.alerts("critical"))
    assert [f["side"] for f in r.events("fill")] == ["BUY"]
    assert not r.events("cancel")  # stop left in place for the watchdog


def test_sale_proceeds_are_recorded_as_unsettled_once(limits, tmp_path):
    ticks = flat_then(lambda i: 100.0 + 0.05 * i)
    r = run_sim(limits, to_data("SPY", ticks), [(T, EntryRequest("s1", "SPY", 99.0, 101.0))], tmp_path)
    closed = r.events("position_closed")
    assert len(closed) == 1
    sells = [u for u in r.state.unsettled]
    assert len(sells) >= 1 and all(u["settles"] == "2026-10-28" for u in sells)  # T+1
    assert r.state.unsettled_cash() == round(sum(u["amount"] for u in sells), 2) > 0
