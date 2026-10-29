import json

import pytest
from conftest import et_ns
from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
from nautilus_trader.common import LoggerConfig, LogLevel
from nautilus_trader.model import AccountType, Money, OmsType, OrderSide, Price, Quantity, TimeInForce, Venue
from nautilus_trader.trading import Strategy, StrategyConfig
from sim import path, to_data

from flowforge.backtest.fees import IbkrFixedFeeModel
from flowforge.data.ticks import VENUE, instrument_id, make_tick_equity
from flowforge.execution.heartbeat import Heartbeat, read_heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.ops import watchdog as wd
from flowforge.risk.calendar import NS
from flowforge.risk.killswitch import KillSwitch

NOW = et_ns(10, 0)


class Dog:
    def __init__(self, tmp_path, flatten_on_kill=True, report=None):
        self.calls = []
        self.kill = KillSwitch(tmp_path / "KILL")
        self.hb = tmp_path / "hb.json"
        self.report = report or wd.FlattenReport(0)

        def flattener(reason):
            self.calls.append(reason)
            return self.report

        self.journal = Journal()
        self.dog = wd.Watchdog(self.kill, self.hb, flatten_on_kill, flattener, self.journal)

    def beat(self, age_s=0, positions=None, orders=0):
        write_heartbeat(self.hb, Heartbeat(NOW - int(age_s * NS), "node", positions or {}, orders))


def test_quiet_when_node_is_healthy_or_flat(tmp_path):
    d = Dog(tmp_path)
    d.beat(positions={"SPY": 9}, orders=1)
    assert d.dog.tick(NOW).action == "none"
    d.beat(age_s=60)  # stale but flat: nothing to protect
    assert d.dog.tick(NOW).action == "none"
    assert d.calls == []


def test_silent_node_while_exposed_engages_kill_and_flattens(tmp_path):
    d = Dog(tmp_path)
    d.beat(age_s=wd.HEARTBEAT_STALE_S + 1, positions={"SPY": 9})
    decision = d.dog.tick(NOW)
    assert decision.action == "flatten" and decision.engage_kill
    assert d.kill.is_engaged() and d.calls == ["node silent for 16 s while exposed"]
    assert d.journal.events("watchdog_flatten_report")[0]["flat"] is True


def test_silent_node_outside_the_session_is_left_alone(tmp_path):
    d = Dog(tmp_path)
    write_heartbeat(d.hb, Heartbeat(et_ns(20, 0) - 60 * NS, "node", {"SPY": 9}, 1))
    assert d.dog.tick(et_ns(20, 0)).action == "none"


def test_missing_heartbeat_in_session_alerts_once(tmp_path):
    d = Dog(tmp_path)
    assert d.dog.tick(NOW).action == "alert"
    d.dog.tick(NOW + NS)
    assert len(d.journal.events("watchdog_alert")) == 1 and d.calls == []


def test_kill_gives_the_node_a_grace_period_first(tmp_path):
    d = Dog(tmp_path)
    d.kill.engage("operator", "test")
    d.beat(positions={"SPY": 9})
    assert d.dog.tick(NOW).action == "none"
    d.beat(age_s=-(wd.KILL_GRACE_S - 1), positions={"SPY": 9})
    assert d.dog.tick(NOW + (wd.KILL_GRACE_S - 1) * NS).action == "none"
    t = NOW + wd.KILL_GRACE_S * NS
    write_heartbeat(d.hb, Heartbeat(t, "node", {"SPY": 9}, 1))
    assert d.dog.tick(t).action == "flatten"


def test_kill_with_node_flat_needs_nothing(tmp_path):
    d = Dog(tmp_path)
    d.kill.engage("operator", "test")
    t = NOW + wd.KILL_GRACE_S * NS
    write_heartbeat(d.hb, Heartbeat(t, "node", {}, 0))
    d.dog.tick(NOW)
    assert d.dog.tick(t).action == "none"


def test_kill_with_dead_node_flattens_at_once_unless_disabled(tmp_path):
    d = Dog(tmp_path)
    d.kill.engage("operator", "test")
    assert d.dog.tick(NOW).action == "flatten"
    off = Dog(tmp_path / "off", flatten_on_kill=False)
    off.kill.engage("operator", "test")
    assert off.dog.tick(NOW).action == "none"


def test_reflatten_spacing_and_give_up(tmp_path):
    stuck = wd.FlattenReport(0, remaining_positions={"SPY": 9})
    d = Dog(tmp_path, report=stuck)
    d.kill.engage("operator", "test")
    t = NOW
    for _ in range(wd.MAX_FLATTEN_RUNS):
        assert d.dog.tick(t).action == "flatten"
        assert d.dog.tick(t + NS).action == "none"  # spaced out
        t += wd.REFLATTEN_S * NS
    assert d.dog.tick(t).action == "alert"
    assert d.dog.tick(t + wd.REFLATTEN_S * NS).action == "none"  # gave up, said so once
    assert len(d.calls) == wd.MAX_FLATTEN_RUNS
    d.kill.release()  # operator steps in: counters reset
    d.dog.tick(t)
    assert d.dog.runs == 0 and not d.dog.gave_up
    assert stuck.flat is False


def test_heartbeat_roundtrip_and_garbage(tmp_path):
    p = tmp_path / "hb.json"
    assert read_heartbeat(p) is None
    write_heartbeat(p, Heartbeat(5, "n", {"SPY": 3}, 2, "halted", True))
    assert read_heartbeat(p) == Heartbeat(5, "n", {"SPY": 3}, 2, "halted", True)
    p.write_text("{}")
    assert read_heartbeat(p) is None


# -- the flatten strategy on the simulated venue ------------------------------------------
class Holder(Strategy):
    """Stands in for a dead node: holds 9 shares with a resting stop and does nothing else."""

    def __new__(cls, *a, **k):
        return super().__new__(cls)

    def __init__(self, stop=99.0):
        super().__init__(StrategyConfig(order_id_tag="NODE"))
        self.stop = stop
        self.bought = False

    def on_start(self):
        self.subscribe_quotes(instrument_id("SPY"))

    def on_quote(self, q):
        if not self.bought:
            self.bought = True
            self.submit_order(self.order_factory.market(q.instrument_id, OrderSide.BUY, Quantity.from_int(9)))

    def on_order_filled(self, e):
        if e.order_side == OrderSide.BUY:
            stop = self.order_factory.stop_market(
                e.instrument_id,
                OrderSide.SELL,
                Quantity.from_int(9),
                Price(self.stop, 2),
                time_in_force=TimeInForce.DAY,
            )
            self.submit_order(stop, position_id=e.position_id)


def _engine(oms=OmsType.HEDGING, account=AccountType.CASH):
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggerConfig(stdout_level=LogLevel.ERROR)))
    engine.add_venue(
        Venue(VENUE),
        oms,
        account,
        [Money.from_str("10000 USD")],
        fee_model=IbkrFixedFeeModel(),
    )
    engine.add_instrument(make_tick_equity("SPY"))
    engine.add_data(to_data("SPY", path(NOW, 120, lambda i: 100.0)))
    return engine


@pytest.mark.parametrize(
    ("oms", "account"), [(OmsType.HEDGING, AccountType.CASH), (OmsType.NETTING, AccountType.MARGIN)]
)
def test_flatten_strategy_cancels_first_then_sells_exactly_the_position(oms, account):
    engine = _engine(oms, account)
    report = wd.FlattenReport(0)
    engine.add_strategy(Holder())
    engine.add_strategy(wd._flatten_strategy_class()(report, settle_s=5))
    engine.run()
    assert len(report.cancelled) == 1 and report.sold == {"SPY": 9}
    assert report.flat, report
    sells = [
        f
        for f in engine.cache.orders()
        if f.side == OrderSide.SELL and f.is_closed and f.filled_qty.as_double()
    ]
    assert len(sells) == 1  # the stop was cancelled, only the watchdog's sale filled
    engine.dispose()


def test_flatten_strategy_with_nothing_to_do():
    engine = _engine()
    report = wd.FlattenReport(0)
    engine.add_strategy(wd._flatten_strategy_class()(report, settle_s=1))
    engine.run()
    assert report.flat and report.sold == {} and report.cancelled == []
    engine.dispose()


def test_watchdog_alive_file_for_the_dashboard(tmp_path):
    from flowforge.ops.watchdog import Decision, write_alive

    write_alive(tmp_path / "run" / "watchdog.json", 7, Decision("alert", "why"))
    assert json.loads((tmp_path / "run" / "watchdog.json").read_text()) == {
        "ts_ns": 7,
        "action": "alert",
        "reason": "why",
    }
    blocker = tmp_path / "file"
    blocker.write_text("")
    write_alive(blocker / "watchdog.json", 8, Decision("none"))  # can't write: the watchdog carries on


def test_heartbeat_carries_equity_and_reads_old_files(tmp_path):
    p = tmp_path / "hb.json"
    write_heartbeat(p, Heartbeat(5, "n", {}, 0, None, False, 9_990.5, 10_000.0))
    assert read_heartbeat(p).equity == 9_990.5 and read_heartbeat(p).day_start_equity == 10_000.0
    p.write_text(json.dumps({"ts_ns": 5, "trader": "old"}))
    assert read_heartbeat(p).equity is None
