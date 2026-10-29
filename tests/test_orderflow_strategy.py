"""OrderFlowStrategy on the Nautilus simulated venue: signal -> plan -> (Jev) -> RiskGuard -> orders."""

from __future__ import annotations

import json
from concurrent.futures import Future

from conftest import et_ns
from sim import path, run_sim, to_data
from test_signals import bar, level

from flowforge.gate.jev import PINNED_MODEL, QUESTION_ID, GateDecision, JevConfig, JevGate
from flowforge.strategies.orderflow import OrderFlowStrategy, setups_from_text

LV = level(low=99.70, high=99.80)


class ScriptedFlow(OrderFlowStrategy):
    """Feeds feature records at given times (as the FeatureActor would)."""

    def __init__(self, *args, script=None, **kwargs):
        self.extra = kwargs.pop("flow_kwargs", {})
        super().__init__(*args, **(kwargs | self.extra))
        self.feed = sorted(script or [], key=lambda x: x[0])

    def _due(self):
        now = self.clock.timestamp_ns()
        while self.feed and self.feed[0][0] <= now:
            self.on_feature(self.feed.pop(0)[1])

    def on_market(self, symbol):
        self._due()
        super().on_market(symbol)

    def on_risk_tick(self, now):
        self._due()
        super().on_risk_tick(now)


def flow(**flow_kwargs):
    class Flow(ScriptedFlow):
        def __init__(self, *a, **k):
            super().__init__(*a, flow_kwargs=flow_kwargs, **k)

    return Flow


def records():
    return [
        (et_ns(10, 30), bar(10, 30, 100.0, 100.3, 100.1, levels=[LV])),
        (et_ns(10, 35), bar(10, 35, 99.75, 100.1, 99.95, levels=[LV])),
    ]


def market(up=True):
    # 99.98/100.00 until 10:36, then up to 101.5 (target) or down through the stop.
    def price(i):
        if i < 360:
            return 99.98
        return 99.98 + (0.01 if up else -0.01) * min(i - 360, 200)

    return to_data("SPY", path(et_ns(10, 30), 900, price, step_s=1.0))


def entries(res):
    return [r for r in res.events("order_submit") if r["role"] == "entry"]


def test_signal_becomes_a_guarded_trade_and_exits_at_the_target(limits, tmp_path):
    res = run_sim(limits, market(), records(), tmp_path, strategy_cls=flow(setups={"H1": ("atr", "2r")}))
    sig = res.events("signal")
    assert [s["setup"] for s in sig] == ["H1"] and sig[0]["enabled"]
    (decision,) = res.events("risk_decision")
    assert decision["allowed"]
    assert len(entries(res)) == 1
    sells = [r for r in res.events("fill") if r["side"] == "SELL"]
    assert sells and sells[-1]["price"] >= 100.0 + 2 * 0.75 - 0.05  # 2R target, ATR stop 0.75
    assert all(p.is_closed for p in res.positions)


def test_disabled_setups_are_logged_but_never_traded(limits, tmp_path):
    res = run_sim(limits, market(), records(), tmp_path, strategy_cls=flow(setups={"H4": ("zone", "volume")}))
    assert res.events("signal")[0]["enabled"] is False
    assert res.events("risk_decision") == [] and entries(res) == []


def test_no_plan_no_trade(limits, tmp_path):
    far = [
        (t, r | {"context": r["context"] | {"prior_day": {"high": 100.5, "low": 100.0, "close": 100}}})
        for t, r in records()
    ]
    res = run_sim(limits, market(), far, tmp_path, strategy_cls=flow(setups={"H1": ("zone", "2r")}))
    assert res.events("signal_skipped")[0]["why"].startswith("no plan")
    assert entries(res) == []


def fake_transport(p):
    body = json.dumps({"model": PINNED_MODEL, "answers": {QUESTION_ID: {"type": "noul", "noul": p}}}).encode()
    return lambda *a: (200, body)


def test_jev_veto_blocks_and_approval_lets_risk_guard_decide(limits, tmp_path):
    veto = JevGate(JevConfig(enabled=True, shadow=False), api_key="k", transport=fake_transport(0.2))
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "a",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=veto, gate_blocking=True),
    )
    (d,) = res.events("jev_decision")
    assert d["allow"] is False and d["reason"] == "below threshold" and entries(res) == []
    ok = JevGate(JevConfig(enabled=True, shadow=False), api_key="k", transport=fake_transport(0.9))
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "b",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=ok, gate_blocking=True),
    )
    assert res.events("jev_decision")[0]["allow"] and len(entries(res)) == 1
    shadow = JevGate(JevConfig(enabled=True, shadow=True), api_key="k", transport=fake_transport(0.2))
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "c",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=shadow, gate_blocking=True),
    )
    d = res.events("jev_decision")[0]
    assert d["vetoed_in_shadow"] and len(entries(res)) == 1  # logged, not obeyed


class FakeGate:
    def __init__(self, decision=None, shadow=False):
        self.config = JevConfig(enabled=True, shadow=shadow)
        self.decision = decision

    def submit(self, facts):
        f = Future()
        if self.decision is not None:
            f.set_result(self.decision)
        return f


def test_async_gate_answer_in_time_enters_and_a_late_one_is_no_trade(limits, tmp_path):
    quick = FakeGate(GateDecision(True, 0.9, 20.0, "ok", PINNED_MODEL))
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "a",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=quick),
    )
    assert len(entries(res)) == 1
    never = FakeGate(None)
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "b",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=never),
    )
    (d,) = res.events("jev_decision")
    assert d["allow"] is False and d["reason"] == "timeout (node clock)" and entries(res) == []
    shadow_late = FakeGate(None, shadow=True)
    res = run_sim(
        limits,
        market(),
        records(),
        tmp_path / "c",
        strategy_cls=flow(setups={"H1": ("atr", "2r")}, gate=shadow_late),
    )
    assert len(entries(res)) == 1


def test_setups_from_text():
    assert setups_from_text("H1:atr/2r, H4") == {"H1": ("atr", "2r"), "H4": ("zone", "volume")}
    assert setups_from_text("") == {}


def test_node_writes_equity_marks_heartbeat_equity_and_the_tape(limits, tmp_path):
    lp = {
        "type": "large_print",
        "symbol": "SPY",
        "ts": "2026-10-27T10:31:00",
        "side": 1,
        "size": 9000,
        "notional": 900000.0,
        "price": 100.0,
    }
    script = records() + [(et_ns(10, 31), bar(10, 31, 99.9, 100.1, 100.0, tf=60)), (et_ns(10, 31), lp)]
    tape = tmp_path / "tape.json"
    res = run_sim(limits, market(), script, tmp_path, strategy_cls=flow(setups={}, tape_path=str(tape)))
    marks = res.events("equity_mark")
    assert len(marks) >= 10 and all(m["day_start_equity"] == 10_000.0 for m in marks)
    gaps = {b["ts_ns"] - a["ts_ns"] for a, b in zip(marks, marks[1:], strict=False)}
    assert gaps == {60 * 1_000_000_000}
    beat = json.loads((tmp_path / "heartbeat.json").read_text())
    assert beat["equity"] == 10_000.0 and beat["day_start_equity"] == 10_000.0
    snap = json.loads(tape.read_text())["symbols"]["SPY"]
    assert snap["bars"][0]["close"] == 100.0 and snap["events"][0]["kind"] == "large print"
