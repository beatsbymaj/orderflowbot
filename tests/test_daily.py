"""Pre-market check, end-of-day report and their schedule (ops/daily.py)."""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

from flowforge.api.demo import write_demo
from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.ops import daily
from flowforge.ops.daily import Paths, due, eod, premarket, publish, run_job
from flowforge.risk.calendar import ET, NS

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


def at(y, m, d, hh, mm) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp()) * NS


def paths(tmp_path: Path) -> Paths:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "run").mkdir(exist_ok=True)
    for name in ("risk_limits.yaml", "jev.yaml"):
        shutil.copy(REPO_CONFIG / name, tmp_path / "config" / name)
    return Paths(
        risk_limits=tmp_path / "config" / "risk_limits.yaml",
        jev=tmp_path / "config" / "jev.yaml",
        state=tmp_path / "data" / "state" / "risk_state.json",
        heartbeat=tmp_path / "run" / "heartbeat.json",
        journals=tmp_path / "data" / "journal",
        reports=tmp_path / "reports" / "daily",
        data=tmp_path,
        kill_file=tmp_path / "run" / "KILL",
    )


def checks(rep) -> dict:
    return {c.name: (c.ok, c.detail) for c in rep.checks}


def test_premarket_all_clear_then_each_problem(tmp_path):
    p = paths(tmp_path)
    now = at(2026, 10, 27, 9, 0)  # a Tuesday
    write_heartbeat(p.heartbeat, Heartbeat(now - 2 * NS, "node", {}, 0, None, False, 10_000.0, 10_000.0))
    p.watchdog.write_text(json.dumps({"ts_ns": now - NS, "action": "none"}))
    rep = premarket(p, now, port_check=lambda h, port: True)
    c = checks(rep)
    assert rep.level == "info" and "all clear" in rep.title()
    assert c["Trading day"][0] is True and c["Day trades left"] == (None, "3 of 3 in 5 days")
    assert c["Jev gate"] == (None, "off") and "IBKR weekly login" not in c

    p.kill_file.write_text(json.dumps({"reason": "testing", "by": "op", "at": "t"}))
    write_heartbeat(p.heartbeat, Heartbeat(now - 60 * NS, "node", {"SPY": 3}, 1))
    p.watchdog.write_text("not json")
    Journal(p.journals / "node").write(now - NS, "start", risk_limits_sha256="different")
    rep = premarket(p, now, port_check=lambda h, port: False)
    c = checks(rep)
    assert rep.level == "critical" and "problems" in rep.title()
    for name in ("Kill switch", "Trading node", "Overnight shares", "Watchdog", "IB Gateway", "Risk limits"):
        assert c[name][0] is False, name
    assert "restart the node" in c["Risk limits"][1]
    assert "| Kill switch | **PROBLEM** |" in rep.markdown() and "NO Kill switch" in rep.summary()


def test_premarket_monday_closed_day_missing_node_and_broken_files(tmp_path):
    p = paths(tmp_path)
    monday = at(2026, 10, 26, 9, 0)
    rep = premarket(p, monday, port_check=lambda h, port: True)
    c = checks(rep)
    assert c["IBKR weekly login"][0] is None and c["Trading node"][0] is False and c["Watchdog"][0] is False
    p.watchdog.write_text(json.dumps({"ts_ns": monday - 100 * NS}))
    assert checks(premarket(p, monday, port_check=lambda h, port: True))["Watchdog"][0] is False
    thanksgiving = at(2026, 11, 26, 9, 0)
    assert checks(premarket(p, thanksgiving, lambda h, port: True))["Trading day"][0] is None
    early = at(2026, 11, 27, 9, 0)
    assert "early close" in checks(premarket(p, early, lambda h, port: True))["Trading day"][1]
    p.jev.write_text("jev:\n  enabled: true\n  shadow: true\n")
    assert checks(premarket(p, monday, lambda h, port: True))["Jev gate"][1].startswith("on (shadow")
    p.jev.write_text("jev:\n  enabled: true\n  shadow: false\n")
    assert "can veto" in checks(premarket(p, monday, lambda h, port: True))["Jev gate"][1]
    p.jev.write_text("jev:\n  model: other\n")
    assert "unreadable" in checks(premarket(p, monday, lambda h, port: True))["Jev gate"][1]
    p.risk_limits.write_text("version: 99\n")
    rep = premarket(p, monday, lambda h, port: True)
    assert checks(rep)["Risk limits"][0] is False and rep.level == "critical"


def test_eod_from_the_demo_day_and_publishing(tmp_path):
    day = write_demo(tmp_path, REPO_CONFIG, datetime(2026, 10, 27, 15, 0, tzinfo=ET))
    p = paths(tmp_path)
    p.heartbeat = tmp_path / "run" / "heartbeat.json"
    now = at(2026, 10, 27, 16, 15)
    rep = eod(p, day, now)
    c = checks(rep)
    assert c["Net after costs"][1].startswith("-3.99 USD") and rep.numbers["trades"] == 2
    assert c["Trades still open"][0] is False and c["Flat at the broker"][0] is False
    assert c["Rejected orders"][0] is True and c["Unreconciled fills"][0] is True
    out = publish(rep, p, now)
    assert (
        out.name == "2026-10-27-eod.md"
        and json.loads(out.with_suffix(".json").read_text())["level"] == "critical"
    )
    ops = [json.loads(x) for x in next((p.journals / "ops").glob("*.jsonl")).read_text().splitlines()]
    assert ops[-1]["event"] == "eod_report" and ops[-1]["title"].startswith("FlowForge End of day 2026-10-27")
    p.heartbeat.unlink()
    with (p.journals / "node" / "2026-10-27.jsonl").open("a") as fh:
        fh.write("{broken\n")
    c = checks(eod(p, day, now))
    assert c["Flat at the broker"][0] is None and c["Journal"][0] is False
    write_heartbeat(p.heartbeat, Heartbeat(now, "n", {}, 0))
    assert checks(eod(p, date(2026, 10, 28), now))["Flat at the broker"] == (True, "yes")


def test_schedule_runs_each_job_once_per_trading_day(tmp_path):
    assert due(at(2026, 10, 27, 8, 59), {}) == []
    assert due(at(2026, 10, 27, 9, 0), {}) == ["premarket"]
    assert due(at(2026, 10, 27, 9, 0), {"premarket": "2026-10-27"}) == []
    assert due(at(2026, 10, 27, 16, 14), {"premarket": "2026-10-27"}) == []
    assert due(at(2026, 10, 27, 16, 15), {"premarket": "2026-10-27"}) == ["eod"]
    assert due(at(2026, 10, 27, 16, 30), {}) == ["eod"]  # started late: no pre-market after the close
    assert due(at(2026, 11, 27, 13, 15), {"premarket": "2026-11-27"}) == ["eod"]  # early close
    assert due(at(2026, 10, 31, 10, 0), {}) == []  # Saturday
    p = paths(tmp_path)
    rep = run_job("eod", p, at(2026, 10, 27, 16, 15))
    assert rep.kind == "eod" and (p.reports / "2026-10-27-eod.md").exists()
    card = json.loads((p.reports.parent / "paper" / "scorecard.json").read_text())
    assert card["trades"] == 0 and card["passes"] is False


def test_cli(monkeypatch, tmp_path, capsys):
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daily, "_port_open", lambda h, port, timeout=1.0: False)
    assert daily.main(["premarket"]) == 1  # no node, no watchdog, no gateway here
    assert "Pre-market check" in capsys.readouterr().out
    assert daily.main(["eod", "--day", "2026-10-27"]) in (0, 1)
    assert (tmp_path / "reports" / "daily" / "2026-10-27-eod.md").exists()
