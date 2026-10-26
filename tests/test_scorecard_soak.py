"""Phase 7 paper scorecard (ops/scorecard.py) and restart soak (ops/soak.py)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.ops import scorecard as sc
from flowforge.ops import soak
from flowforge.risk.calendar import ET, NS, is_trading_day


def at(d: date, hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=ET).timestamp()) * NS


def sessions(first: date, n: int) -> list[date]:
    out, d = [], first
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def trade(j: Journal, d: date, k: int, buy: float, sell: float, sym="SPY", stop=None) -> None:
    t = at(d, 10, 0) + k * 600 * NS
    intent = f"H4:{sym}:{t}"
    plan = {"stop_price": stop or buy - 0.5, "target_price": buy + 1, "quantity": 2}
    j.write(t, "risk_decision", intent=intent, symbol=sym, allowed=True, reasons=[], plan=plan)
    j.write(t + 1, "order_submit", intent=intent, symbol=sym, order=f"E{t}", role="entry", quantity=2)
    j.write(t + 2, "fill", order=f"E{t}", side="BUY", quantity=2, price=buy, commission=0.35)
    j.write(t + 3, "stop_submit", intent=intent, order=f"S{t}", quantity=2, stop=plan["stop_price"])
    j.write(t + 4, "fill", order=f"S{t}", side="SELL", quantity=2, price=sell, commission=0.35)


def paper_history(root, days=22, per_day=5, win=1.5, lose=-0.4):
    j = Journal(root / "node")
    for i, d in enumerate(sessions(date(2026, 10, 1), days)):
        j.write(at(d, 9, 25), "paper_node_start", shadow=False)
        for k in range(per_day):
            buy = 100.0
            trade(j, d, k, buy, buy + (win if (i + k) % 2 == 0 else lose))
        j.write(
            at(d, 15, 0), "equity_mark", equity=10_010.0, day_start_equity=10_000.0, week_peak_equity=10_020.0
        )
    j.close()
    return j


def test_a_good_record_meets_the_gate(tmp_path, limits):
    paper_history(tmp_path)
    card = sc.scorecard(tmp_path, limits)
    assert card["trades"] == 110 and card["passes"], sc.markdown(card)
    assert card["mean_net"] > 0 and card["bootstrap_p"] is not None
    assert "Meets the Phase 7 gate" in sc.markdown(card)


def test_too_short_too_few_and_losing_fail(tmp_path, limits):
    paper_history(tmp_path, days=5, per_day=2, win=-0.1)
    card = sc.scorecard(tmp_path, limits)
    failed = {c["name"] for c in card["checks"] if not c["ok"]}
    assert {"At least 4 weeks of paper", "At least 100 trades", "Positive net expectancy"} <= failed
    assert "Does not meet" in sc.markdown(card)


def test_empty_journal(tmp_path, limits):
    card = sc.scorecard(tmp_path, limits)
    assert card["trades"] == 0 and not card["passes"] and card["first_day"] is None


def test_violations_and_unreconciled_orders_fail_the_gate(tmp_path, limits):
    paper_history(tmp_path)
    d = date(2026, 11, 2)
    j = Journal(tmp_path / "node")
    t = at(d, 11, 0)
    j.write(t, "order_submit", intent="H4:QQQ:x", symbol="QQQ", order="X1", role="entry", quantity=1)
    j.write(t + 1, "fill", order="X1", side="BUY", quantity=1, price=500.0)
    j.write(t + 2, "exit_submit", intent="H4:QQQ:x", order="X2", quantity=3)
    j.write(t + 3, "fill", order="X2", side="SELL", quantity=3, price=500.0)
    j.write(t + 4, "fill", order="NOBODY", side="BUY", quantity=1, price=1.0)
    j.write(t + 5, "alert", level="critical", message="position mismatch on AAPL: ours 0, broker 5")
    j.write(t + 6, "order_denied", order="X3", reason="rate")
    j.write(t + 7, "equity_mark", equity=9_700.0, day_start_equity=10_000.0, week_peak_equity=10_300.0)
    j.close()
    card = sc.scorecard(tmp_path, limits)
    c = {x["name"]: x for x in card["checks"]}
    assert not c["Zero risk violations"]["ok"]
    assert "without an allowed risk decision" in c["Zero risk violations"]["detail"]
    assert "went short" in c["Zero risk violations"]["detail"]
    assert (
        not c["Zero unreconciled orders"]["ok"]
        and "1 position mismatches" in c["Zero unreconciled orders"]["detail"]
    )
    assert not c["Daily loss within limit"]["ok"] and not c["Weekly drawdown within limit"]["ok"]
    assert card["denied_by_nautilus"] == 1


def test_too_many_positions_is_a_violation(tmp_path, limits):
    j = Journal(tmp_path / "node")
    d = date(2026, 10, 27)
    for k, sym in enumerate(["SPY", "QQQ", "AAPL", "MSFT"]):
        t = at(d, 10, k)
        intent = f"H4:{sym}:{t}"
        j.write(t, "risk_decision", intent=intent, symbol=sym, allowed=True, reasons=[], plan={})
        j.write(t + 1, "order_submit", intent=intent, symbol=sym, order=f"E{k}", role="entry", quantity=1)
        j.write(t + 2, "fill", order=f"E{k}", side="BUY", quantity=1, price=100.0)
    j.close()
    card = sc.scorecard(tmp_path, limits, first_day="2026-10-01")
    assert any("4 positions open (limit 3)" in c["detail"] for c in card["checks"])


def test_scorecard_cli(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    paper_history(tmp_path / "journal")
    limits_path = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"
    out = tmp_path / "out"
    assert (
        sc.main(
            ["--journals", str(tmp_path / "journal"), "--risk-limits", str(limits_path), "--out", str(out)]
        )
        == 0
    )
    assert json.loads((out / "scorecard.json").read_text())["passes"] is True
    assert "Paper scorecard" in capsys.readouterr().out


# -- soak ----------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, log):
        self.log = log

    def send_signal(self, sig):
        self.log.append(sig)

    def wait(self, timeout):
        return 0

    def kill(self):
        self.log.append("kill")


def test_soak_restarts_clean_alternating_term_and_kill(tmp_path, monkeypatch):
    import signal

    paths = soak.SoakPaths(tmp_path / "hb.json", tmp_path / "journal" / "node", tmp_path / "KILL")
    log: list = []

    def launch(cmd):
        write_heartbeat(paths.heartbeat, Heartbeat(soak.wall_clock_ns() + NS, "node", {}, 0))
        Journal(paths.journal).write(soak.wall_clock_ns() + NS, "start")
        return FakeProc(log)

    cycles = soak.soak(["node"], paths, 3, (1, 2), launch=launch, sleep=lambda s: None)
    assert [c.stop for c in cycles] == ["term", "kill", "term"] and all(c.ok for c in cycles)
    assert log == [signal.SIGTERM, signal.SIGKILL, signal.SIGTERM]
    assert "3 of 3 restarts clean" in soak.markdown(cycles)


def test_soak_flags_problems_and_stops_when_not_flat(tmp_path, monkeypatch):
    paths = soak.SoakPaths(tmp_path / "hb.json", tmp_path / "journal" / "node", tmp_path / "KILL")
    monkeypatch.setattr(soak, "START_WITHIN_S", 0.01)
    monkeypatch.setattr(soak, "FLAT_WAIT_S", 0.01)
    cycles = soak.soak(["node"], paths, 3, (1, 1), launch=lambda cmd: FakeProc([]), sleep=lambda s: None)
    assert len(cycles) == 1 and cycles[0].stop == "skipped"
    assert any("no fresh heartbeat" in p for p in cycles[0].problems)


def test_soak_sees_mismatch_orders_and_kill_switch(tmp_path, monkeypatch):
    paths = soak.SoakPaths(tmp_path / "hb.json", tmp_path / "journal" / "node", tmp_path / "KILL")
    n = {"i": 0}

    def launch(cmd):
        n["i"] += 1
        now = soak.wall_clock_ns() + NS
        write_heartbeat(paths.heartbeat, Heartbeat(now, "node", {}, 2))
        Journal(paths.journal).write(now, "alert", level="critical", message="position mismatch on SPY")
        (tmp_path / "KILL").write_text(json.dumps({"reason": "x", "by": "node", "at": "t"}))
        return FakeProc([])

    monkeypatch.setattr(soak, "FLAT_WAIT_S", 0.01)
    cycles = soak.soak(["node"], paths, 2, (1, 1), launch=launch, sleep=lambda s: None)
    problems = " ".join(cycles[0].problems)
    assert "open orders after a flat restart" in problems and "mismatch" in problems
    assert "kill switch is on" in problems
