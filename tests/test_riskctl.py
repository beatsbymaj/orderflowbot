import json
from datetime import date

from conftest import REPO_LIMITS

from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.ops import riskctl
from flowforge.risk.state import Halt, RiskState


def run(tmp_path, *args, capsys=None):
    base = ["--risk-limits", str(REPO_LIMITS), "--state", str(tmp_path / "s.json")]
    base += ["--heartbeat", str(tmp_path / "hb.json"), "--kill-file", str(tmp_path / "KILL")]
    return riskctl.main([*args, *base])


def last_json(out: str) -> dict:
    return json.loads(out[out.index("{") :])


def test_kill_release_cycle(tmp_path, capsys):
    assert run(tmp_path, "status") == 0
    assert last_json(capsys.readouterr().out)["kill_switch"] == "off"
    run(tmp_path, "kill", "--reason", "lunch")
    out = capsys.readouterr().out
    assert "Kill switch ON" in out and last_json(out)["kill_switch"].startswith("lunch (by operator")
    assert run(tmp_path, "release", "--yes") == 0
    assert "Kill switch OFF." in capsys.readouterr().out
    run(tmp_path, "release", "--yes")
    assert "already off" in capsys.readouterr().out


def test_release_needs_confirmation(tmp_path, monkeypatch, capsys):
    run(tmp_path, "kill")
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert run(tmp_path, "release") == 1
    assert (tmp_path / "KILL").exists()

    def eof(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert run(tmp_path, "reenable") == 1
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert run(tmp_path, "release") == 0 and not (tmp_path / "KILL").exists()


def test_reenable_clears_weekly_halt_and_status_shows_node(tmp_path, limits, capsys):
    state = RiskState(limits, tmp_path / "s.json")
    state.start_day(date(2026, 10, 27), 10_000.0)
    state.weekly_halt = Halt("weekly", "weekly drawdown 5.1% >= 5%", "t", "2026-10-27")
    state.save()
    write_heartbeat(tmp_path / "hb.json", Heartbeat(0, "node", {"SPY": 9}, 1))
    run(tmp_path, "reenable", "--yes")
    out = capsys.readouterr().out
    assert "Weekly halt cleared." in out
    status = last_json(out)
    assert status["halt"] == "none" and status["node"]["positions"] == {"SPY": 9}
    run(tmp_path, "reenable", "--yes")
    assert "There was no weekly halt." in capsys.readouterr().out
