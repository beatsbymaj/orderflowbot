"""Paper drills (ops/paper_drills.py): the drill strategy on the simulated venue, and the
reports read back from its journal. The real runs are on the IBKR paper gateway."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import et_ns
from sim import path, run_sim, to_data

from flowforge.ops import paper_drills as pd
from flowforge.ops.ib_paper_check import IbSettings
from flowforge.ops.watchdog import FlattenReport

T = et_ns(10, 0)
WARM = et_ns(9, 50)
PAPER = IbSettings("127.0.0.1", 4002, 11, "DU1234567", "paper")


def drill_cls(drill, **extra):
    class Drill(pd.DrillStrategy):
        def __init__(self, *args, script=None, **kwargs):
            super().__init__(*args, drill=drill, **(extra | kwargs))

    return Drill


def ticks(after, seconds_after=240):
    return path(WARM, 600, lambda i: 100.0) + path(T, seconds_after, after)


def run(limits, tmp_path, drill, price=lambda i: 100.0, **extra):
    r = run_sim(limits, to_data("SPY", ticks(price)), [], tmp_path, strategy_cls=drill_cls(drill, **extra))
    records = sorted(r.journal.records, key=lambda x: x["ts_ns"])
    return r, records


def test_round_trip_buys_one_share_protects_it_and_sells(limits, tmp_path):
    r, records = run(limits, tmp_path, "round-trip", hold_s=20)
    assert r.strategy.outcome == "closed"
    rep = pd.order_drill_report("round-trip", "t", records, r.strategy.broker_positions())
    assert rep.passed, rep.markdown()
    assert rep.timings_ms["entry submit -> filled"] is not None
    assert rep.timings_ms["exit submit -> filled"] is not None
    fills = [x for x in records if x["event"] == "fill"]
    assert [(f["side"], f["quantity"]) for f in fills] == [("BUY", 1), ("SELL", 1)]
    assert "| Stop accepted at IBKR within 5 s | ok |" in rep.markdown()


def test_stop_drill_waits_for_the_stop_and_times_how_long_it_took(limits, tmp_path):
    r, records = run(limits, tmp_path, "stop", price=lambda i: 100.0 if i < 30 else 99.90)
    rep = pd.order_drill_report("stop", "t", records, r.strategy.broker_positions())
    assert rep.passed, rep.markdown()
    assert rep.timings_ms["bid at stop -> stop filled"] is not None
    assert any("slippage" in n for n in rep.notes)
    assert r.strategy.stop_gap == 0.02


def test_stop_drill_gives_up_and_sells_when_the_stop_never_triggers(limits, tmp_path):
    r, records = run(limits, tmp_path, "stop", give_up_s=30)
    rep = pd.order_drill_report("stop", "t", records, r.strategy.broker_positions())
    assert not rep.passed
    assert [c for c in rep.checks if c["name"] == "Stop triggered at IBKR"][0]["ok"] is False
    assert any(x["event"] == "drill_stop_timeout" for x in records)
    assert r.strategy.broker_positions() == {}


def test_crash_drill_dies_only_once_the_stop_is_at_the_broker(limits, tmp_path):
    calls = []
    r, records = run(limits, tmp_path, "crash", crash=lambda: calls.append("crash"))
    assert calls == ["crash"]
    events = [x["event"] for x in records]
    assert events.index("drill_protected") < events.index("drill_crash")
    assert r.strategy.broker_positions() == {"SPY": 1}  # crashed holding, stop resting


def test_observe_sends_nothing(limits, tmp_path):
    r, records = run(limits, tmp_path, "observe")
    assert not [x for x in records if x["event"] in ("order_submit", "risk_decision")]


def test_ioc_miss_is_retried_then_given_up(limits, tmp_path, monkeypatch):
    from flowforge.risk.guard import RiskDecision
    from flowforge.risk.orders import Phase

    class Missing(drill_cls("round-trip")):
        def request_entry(self, req):  # as if each IOC reached IBKR and missed
            self.manager.trades[req.intent_id] = SimpleNamespace(phase=Phase.FAILED, done=True, position=0)
            return RiskDecision(True, ())

    monkeypatch.setattr(pd, "RETRY_S", 1)
    r = run_sim(limits, to_data("SPY", ticks(lambda i: 100.0)), [], tmp_path, strategy_cls=Missing)
    assert r.strategy.tries == pd.MAX_TRIES
    assert r.strategy.outcome == f"entry not filled after {pd.MAX_TRIES} tries"


def test_refused_entry_ends_the_drill(limits, tmp_path):
    from conftest import with_limits

    tight = with_limits(limits, "trading", max_trades_per_day=0)
    r = run_sim(
        tight, to_data("SPY", ticks(lambda i: 100.0)), [], tmp_path, strategy_cls=drill_cls("round-trip")
    )
    assert r.strategy.outcome.startswith("refused by risk")
    rep = pd.order_drill_report("round-trip", "t", r.journal.records, {})
    assert not rep.passed


def test_report_without_any_entry_fails():
    rep = pd.order_drill_report("round-trip", "t", [], {})
    assert not rep.passed and rep.checks[0]["detail"] == "no quote / no entry"
    assert "FAIL" in rep.markdown()


def test_instruments_report():
    def inst(tick):
        return SimpleNamespace(
            id="SPY.XNAS", price_increment=SimpleNamespace(as_double=lambda: tick), lot_size=1, info={"x": 1}
        )

    cache = SimpleNamespace(instrument=lambda iid: inst(0.01) if str(iid).startswith("SPY") else None)
    rep = pd.instruments_report(cache, ["SPY", "QQQ"], "t")
    assert not rep.passed
    names = {c["name"]: c["ok"] for c in rep.checks}
    assert names["SPY tick size 0.01"] is True and names["QQQ loaded as QQQ.XNAS"] is False
    cache = SimpleNamespace(instrument=lambda iid: inst(0.0001))
    assert not pd.instruments_report(cache, ["SPY"], "t").passed


def _crash_records(intent="drill-crash-1"):
    return [
        {"event": "drill_entry", "intent": intent, "ts_ns": 1, "ts": "2026-10-27T14:00:00"},
        {"event": "order_submit", "intent": intent, "role": "entry", "order": "O-1", "ts_ns": 2, "ts": "x"},
        {
            "event": "fill",
            "order": "O-1",
            "side": "BUY",
            "quantity": 1,
            "price": 100.0,
            "ts_ns": 3,
            "ts": "x",
        },
        {"event": "stop_submit", "intent": intent, "order": "O-2", "ts_ns": 4, "ts": "x"},
        {"event": "order_accepted", "order": "O-2", "ts_ns": 5, "ts": "x"},
        {"event": "drill_crash", "intent": intent, "ts_ns": 6, "ts": "x"},
    ]


@pytest.mark.parametrize("after", ["takeover", "restart"])
def test_crash_then_watchdog(monkeypatch, after):
    later = [
        {
            "event": "alert",
            "level": "critical",
            "message": "position mismatch on SPY: ours 0, broker 1",
            "ts_ns": 10**19,
        },
        {"event": "kill_engaged", "reason": "position mismatch", "ts_ns": 10**19 + 1},
    ]
    codes = iter([9, 0])
    monkeypatch.setattr(pd, "_child", lambda args, timeout: next(codes))
    reads = iter([_crash_records(), later] if after == "restart" else [_crash_records()])
    monkeypatch.setattr(pd, "_drill_records", lambda since: next(reads, []))
    flat = FlattenReport(0, cancelled=["O-2"], sold={"SPY": 1})
    monkeypatch.setattr(pd, "watchdog_until_flat", lambda s: (flat, 16.0))
    rep, _ = pd.run_crash_then(PAPER, after)
    assert rep.passed, rep.markdown()
    assert rep.timings_ms["watchdog decision"] == 16000
    assert any("riskctl release" in n for n in rep.notes)
    if after == "restart":
        assert any(c["name"] == "It sent no orders of its own" for c in rep.checks)


def test_crash_that_never_crashed_fails(monkeypatch):
    monkeypatch.setattr(pd, "_child", lambda args, timeout: 0)
    monkeypatch.setattr(pd, "_drill_records", lambda since: [])
    rep, _ = pd.run_crash_then(PAPER, "takeover")
    assert not rep.passed and rep.checks[0]["detail"] == "exit code 0"


def test_watchdog_that_never_flattens_fails(monkeypatch):
    monkeypatch.setattr(pd, "_child", lambda args, timeout: 9)
    monkeypatch.setattr(pd, "_drill_records", lambda since: _crash_records())
    monkeypatch.setattr(pd, "watchdog_until_flat", lambda s: (None, None))
    rep, _ = pd.run_crash_then(PAPER, "takeover")
    assert not rep.passed


def test_drills_refuse_anything_but_paper(monkeypatch, capsys):
    monkeypatch.setenv("IB_ACCOUNT_ID", "U1234567")
    monkeypatch.setenv("IB_PORT", "4001")
    assert pd.main(["round-trip"]) == 2
    assert "refusing" in capsys.readouterr().err


def test_main_writes_report(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    rep = pd.DrillReport("instruments", "2026-10-27T14:00:00Z")
    rep.check("SPY loaded", True)
    monkeypatch.setattr(pd, "run_instruments", lambda s: rep.finish())
    assert pd.main(["instruments", "--out", str(tmp_path)]) == 0
    saved = json.loads(next(tmp_path.glob("drill-instruments-*.json")).read_text())
    assert saved["passed"] is True and "report:" in capsys.readouterr().out


def test_child_runs_this_module(monkeypatch):
    seen = {}

    def fake_run(cmd, timeout, check):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr(pd.subprocess, "run", fake_run)
    assert pd._child(["_crash"], 5) == 9 and seen["cmd"][-2:] == ["flowforge.ops.paper_drills", "_crash"]

    def slow(cmd, timeout, check):
        raise pd.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(pd.subprocess, "run", slow)
    assert pd._child(["_crash"], 5) == -1


def test_entry_on_an_instrument_the_broker_never_sent_is_refused(limits, tmp_path):
    from flowforge.risk.guard import EntryRequest

    r = run_sim(
        limits, to_data("SPY", ticks(lambda i: 100.0)), [(T, EntryRequest("x", "QQQ", 99.0, 102.0))], tmp_path
    )
    assert r.strategy.decisions[0].reasons == ("instrument not loaded from the broker",)
    assert [x for x in r.journal.records if x["event"] == "risk_decision"][0]["allowed"] is False
