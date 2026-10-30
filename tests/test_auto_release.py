"""ops/auto_release.py: the kill switch turns itself off only when IBKR shows the paper account
empty and the switch wasn't pulled by a human (ADR-0017)."""

from __future__ import annotations

import json

import pytest
from conftest import REPO_LIMITS

from flowforge.ops import auto_release as ar
from flowforge.ops import paper_drills as pd
from flowforge.ops.ib_paper_check import CheckReport, IbSettings
from flowforge.risk.killswitch import KillSwitch

PAPER = IbSettings("127.0.0.1", 4002, 11, "DU1234567", "paper")


def report(positions=0, orders=0, connected=True, problems=()):
    r = CheckReport("2026-11-02T13:00:00Z", "DU***567", connected=connected, problems=list(problems))
    r.open_positions = [{"instrument": "SPY.XNAS"}] * positions
    r.open_orders = [{"instrument": "SPY.XNAS"}] * orders
    return r


@pytest.mark.parametrize(
    ("enabled", "engaged", "by", "rep", "release", "why"),
    [
        (True, False, None, None, False, "already off"),
        (False, True, "watchdog", report(), False, "auto release is off"),
        (True, True, "operator", report(), False, "only the operator"),
        (True, True, "dashboard", report(), False, "only the operator"),
        (True, True, None, report(), False, "unknown"),
        (True, True, "watchdog", None, False, "no check ran"),
        (True, True, "watchdog", report(connected=False), False, "IBKR check failed"),
        (True, True, "watchdog", report(problems=["no balances"]), False, "no balances"),
        (True, True, "node:FF", report(positions=1), False, "1 positions"),
        (True, True, "node:FF", report(orders=2), False, "2 open orders"),
        (True, True, "node:FF", report(), True, "0 positions and 0 open orders"),
        (True, True, "watchdog", report(), True, "turned on by watchdog"),
    ],
)
def test_decide(enabled, engaged, by, rep, release, why):
    d = ar.decide(enabled, engaged, by, rep)
    assert d.release is release and why in d.reason


def test_engaged_by(tmp_path):
    kill = KillSwitch(tmp_path / "KILL")
    assert kill.engaged_by() is None
    kill.engage("drill", "watchdog")
    assert kill.engaged_by() == "watchdog"
    (tmp_path / "KILL").write_text("not json")
    assert kill.engaged_by() is None  # unreadable: treated as a human's, never auto released
    (tmp_path / "KILL").write_text(json.dumps({"by": ""}))
    assert kill.engaged_by() is None


def limits_file(tmp_path, enabled: bool):
    text = REPO_LIMITS.read_text()
    assert "auto_release_when_flat: true" in text  # the operator's 2026-10-30 choice
    path = tmp_path / "limits.yaml"
    path.write_text(
        text.replace("auto_release_when_flat: true", f"auto_release_when_flat: {str(enabled).lower()}")
    )
    return path


@pytest.fixture
def paper_env(monkeypatch):
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    monkeypatch.setenv("IB_PORT", "4004")
    monkeypatch.setenv("IB_TRADING_MODE", "paper")


def test_run_releases_a_watchdog_kill_on_an_empty_account(tmp_path, paper_env):
    kill = KillSwitch(tmp_path / "KILL")
    kill.engage("node silent", "watchdog")
    seen = []
    d = ar.run(
        REPO_LIMITS,
        tmp_path / "KILL",
        tmp_path / "j.jsonl",
        check=lambda s, secs, lim: seen.append(s) or report(),
    )
    assert d.release and not kill.is_engaged()
    assert seen[0].trading_mode == "paper"
    row = json.loads((tmp_path / "j.jsonl").read_text())
    assert row["released"] is True and "node silent" in row["kill_switch"]


def test_run_keeps_it_on_while_shares_are_open(tmp_path, paper_env):
    KillSwitch(tmp_path / "KILL").engage("drill", "watchdog")
    d = ar.run(REPO_LIMITS, tmp_path / "KILL", tmp_path / "j.jsonl", check=lambda *a: report(positions=1))
    assert not d.release and KillSwitch(tmp_path / "KILL").is_engaged()
    assert json.loads((tmp_path / "j.jsonl").read_text())["released"] is False


def test_run_never_connects_for_an_operator_kill_or_when_disabled(tmp_path, paper_env):
    def boom(*a):
        raise AssertionError("must not connect")

    KillSwitch(tmp_path / "KILL").engage("lunch", "operator")
    assert not ar.run(REPO_LIMITS, tmp_path / "KILL", tmp_path / "j.jsonl", check=boom).release
    KillSwitch(tmp_path / "KILL").engage("drill", "watchdog")
    off = limits_file(tmp_path, False)
    assert not ar.run(off, tmp_path / "KILL", tmp_path / "j.jsonl", check=boom).release
    assert KillSwitch(tmp_path / "KILL").is_engaged()


def test_run_with_the_switch_off_does_nothing(tmp_path, paper_env):
    d = ar.run(REPO_LIMITS, tmp_path / "KILL", tmp_path / "j.jsonl", check=lambda *a: report())
    assert d.reason == "kill switch already off" and not (tmp_path / "j.jsonl").exists()


def test_main_refuses_anything_but_paper(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IB_ACCOUNT_ID", "U1234567")
    monkeypatch.setenv("IB_PORT", "4001")
    KillSwitch(tmp_path / "KILL").engage("drill", "watchdog")
    args = ["--risk-limits", str(REPO_LIMITS), "--kill-file", str(tmp_path / "KILL")]
    assert ar.main([*args, "--journal", str(tmp_path / "j.jsonl")]) == 2
    assert "refusing" in capsys.readouterr().err and KillSwitch(tmp_path / "KILL").is_engaged()


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    args = [
        "--risk-limits",
        str(REPO_LIMITS),
        "--kill-file",
        str(tmp_path / "KILL"),
        "--journal",
        str(tmp_path / "j"),
    ]
    assert ar.main(args) == 0 and "already off" in capsys.readouterr().out
    KillSwitch(tmp_path / "KILL").engage("lunch", "operator")
    assert ar.main(args) == 1 and "left as it is" in capsys.readouterr().out
    monkeypatch.setattr(ar, "run", lambda *a: ar.Decision(True, "ok"))
    assert ar.main(args) == 0 and "Kill switch OFF" in capsys.readouterr().out


# -- paper_drills all -------------------------------------------------------------------------
def passing(name):
    rep = pd.DrillReport(name, "2026-11-02T14:00:00Z")
    rep.check("fine", True)
    return rep.finish()


def fake_drills(monkeypatch, fail=None):
    ran = []

    def order(settings, drill, seconds):
        ran.append(drill)
        rep = passing(drill)
        if drill == fail:
            rep.check("bad", False)
            rep.finish()
        return rep, []

    monkeypatch.setattr(pd, "run_instruments", lambda s: ran.append("instruments") or passing("instruments"))
    monkeypatch.setattr(pd, "run_order_drill", order)
    monkeypatch.setattr(pd, "run_crash_then", lambda s, d: order(s, d, 0))
    return ran


def test_all_runs_every_drill_releasing_between(monkeypatch, tmp_path):
    ran = fake_drills(monkeypatch)
    releases = []
    reps = pd.run_all(
        PAPER,
        tmp_path,
        60,
        check=lambda *a: report(),
        release=lambda lim: releases.append(lim) or ar.Decision(True, "drill kill, account empty"),
    )
    assert ran == list(pd.DRILLS) and all(r.passed for r in reps)
    assert len(releases) == len(pd.DRILLS)  # before each order drill, and once after restart


def test_all_stops_at_the_first_failure(monkeypatch, tmp_path):
    ran = fake_drills(monkeypatch, fail="stop")
    ok = lambda lim: ar.Decision(False, "kill switch already off")  # noqa: E731
    reps = pd.run_all(PAPER, tmp_path, 60, check=lambda *a: report(), release=ok)
    assert ran == ["instruments", "round-trip", "stop"] and not reps[-1].passed


@pytest.mark.parametrize(
    ("rep", "decision", "why"),
    [
        (report(positions=1), ar.Decision(True, ""), "account not empty"),
        (report(connected=False), ar.Decision(True, ""), "IBKR check failed"),
        (report(), ar.Decision(False, "turned on by operator"), "kill switch on"),
    ],
)
def test_all_will_not_start_a_drill_unless_ready(monkeypatch, tmp_path, rep, decision, why):
    ran = fake_drills(monkeypatch)
    reps = pd.run_all(PAPER, tmp_path, 60, check=lambda *a: rep, release=lambda lim: decision)
    assert ran == ["instruments"] and why in reps[-1].checks[0]["detail"]
    assert next(tmp_path.glob("drill-round-trip-*.md"))


def test_main_all(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    monkeypatch.setattr(pd, "run_all", lambda s, out, secs: [passing("instruments")])
    assert pd.main(["all", "--out", str(tmp_path)]) == 1
    assert not (tmp_path / pd.PASSED_FILE).exists()
    monkeypatch.setattr(pd, "run_all", lambda s, out, secs: [passing(d) for d in pd.DRILLS])
    assert pd.main(["all", "--out", str(tmp_path)]) == 0
    assert "All drills passed." in capsys.readouterr().out
    assert json.loads((tmp_path / pd.PASSED_FILE).read_text())["passed_at"]
