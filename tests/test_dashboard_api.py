"""Dashboard API: read-only views over the node's files and the engage-only kill switch."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowforge.api import __main__ as api_main
from flowforge.api.app import ApiPaths, create_app, research_summary, session_state
from flowforge.api.demo import beat, demo_day, keep_beating, write_demo
from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.risk.calendar import ET, NS

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
DEMO_NOW = datetime(2026, 10, 27, 15, 0, tzinfo=ET)


def at(hh, mm, d=27) -> int:
    return int(datetime(2026, 10, d, hh, mm, tzinfo=ET).timestamp()) * NS


@pytest.fixture
def demo(tmp_path):
    write_demo(tmp_path, REPO_CONFIG, DEMO_NOW)
    clock = {"now": at(15, 0)}
    beat_now(tmp_path, clock["now"])
    paths = ApiPaths(
        root=tmp_path,
        run_dir=tmp_path / "run",
        demo=True,
        kill_file=tmp_path / "run" / "KILL",
        env={"NTFY_TOPIC": "secret-topic"},
    )
    client = TestClient(create_app(paths, clock=lambda: clock["now"], port_check=lambda h, p: True))
    return client, tmp_path, clock


def beat_now(root: Path, now: int) -> None:
    beat(root, demo_day(DEMO_NOW))
    hb = json.loads((root / "run" / "heartbeat.json").read_text())
    hb["ts_ns"] = now
    (root / "run" / "heartbeat.json").write_text(json.dumps(hb))
    (root / "run" / "watchdog.json").write_text(json.dumps({"ts_ns": now, "action": "none"}))
    tape = json.loads((root / "run" / "tape.json").read_text())
    tape["ts_ns"] = now
    (root / "run" / "tape.json").write_text(json.dumps(tape))


def test_status_tells_one_story(demo):
    client, _, _ = demo
    s = client.get("/api/status").json()
    assert s["demo"] is True and s["day"] == "2026-10-27" and s["session"]["state"] == "open"
    assert s["finding"] == "Holding 1 position, with a stop at the broker."
    assert s["equity"] == 9995.85 and s["day_start_equity"] == 10_000.0
    budget = {b["key"]: (b["used"], b["limit"]) for b in s["budget"]}
    assert budget == {
        "daily_loss": (4.15, 200.0),
        "weekly_drawdown": (25.55, 501.07),
        "trades": (3, 10),
        "day_trades": (3, 3),
        "positions": (1, 3),
    }
    assert s["today"]["net"] == -3.99 and s["positions"][0]["unrealized"] == 0.84
    # one fact, one value: equity - day start = closed net + open (unrealized minus the open trade's fee)
    assert round(s["equity"] - s["day_start_equity"], 2) == round(s["today"]["net"] + 0.84 - 1.0, 2)
    assert len(s["equity_curve"]) == 301 and s["recent"][-1]["event"] == "risk_decision"
    assert [r["ts_ns"] for r in s["recent"]] == sorted(r["ts_ns"] for r in s["recent"])


def test_findings_in_order_of_danger(demo, tmp_path):
    client, root, clock = demo
    clock["now"] = at(15, 0) + 20 * NS
    assert "watchdog takes over" in client.get("/api/status").json()["finding"]
    (root / "run" / "heartbeat.json").unlink()
    s = client.get("/api/status").json()
    assert s["finding"].startswith("The trading node is not running") and s["node"] is None
    state = json.loads((root / "data" / "state" / "risk_state.json").read_text())
    state["daily_halt"] = {
        "kind": "daily",
        "reason": "daily loss 2.1% >= 2%",
        "since": "t",
        "day": "2026-10-27",
    }
    (root / "data" / "state" / "risk_state.json").write_text(json.dumps(state))
    assert client.get("/api/status").json()["finding"] == "Halted for the day: daily loss 2.1% >= 2%."
    r = client.post("/api/kill", json={"reason": "going out"}, headers={"X-FlowForge-Action": "engage-kill"})
    assert r.status_code == 200 and r.json()["already_on"] is False
    s = client.get("/api/status").json()
    assert s["kill"]["on"] and s["finding"].startswith("Kill switch is on: going out (by dashboard")


def test_flat_and_empty_days(tmp_path):
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    paths = ApiPaths(root=tmp_path, run_dir=tmp_path / "run", kill_file=tmp_path / "run" / "KILL", env={})
    client = TestClient(create_app(paths, clock=lambda: at(10, 0), port_check=lambda h, p: False))
    write_heartbeat(tmp_path / "run" / "heartbeat.json", Heartbeat(at(10, 0), "n", {}, 0))
    s = client.get("/api/status").json()
    assert s["finding"] == "Flat, no trades yet today." and s["equity"] is None and s["equity_curve"] == []
    assert [b["key"] for b in s["budget"]] == ["trades", "day_trades", "positions"]
    limits = tmp_path / "config" / "risk_limits.yaml"
    limits.write_text(limits.read_text().replace("pdt_regime: legacy_pdt", "pdt_regime: cash"))
    cash = TestClient(create_app(paths, clock=lambda: at(10, 0), port_check=lambda h, p: False))
    budget = cash.get("/api/status").json()["budget"]
    assert [b["key"] for b in budget] == ["trades", "unsettled", "positions"]
    assert budget[1]["used"] == 0 and budget[1]["limit"] is None
    limits.write_text(limits.read_text().replace("pdt_regime: cash", "pdt_regime: legacy_pdt"))
    j = Journal(tmp_path / "data" / "journal" / "node")
    j.write(
        at(9, 50), "risk_decision", intent="H4:SPY:1", allowed=True, plan={"stop_price": 9.0, "quantity": 1}
    )
    j.write(at(9, 50), "order_submit", intent="H4:SPY:1", symbol="SPY", order="E", role="entry", quantity=1)
    j.write(at(9, 51), "fill", order="E", quantity=1, price=10.0, commission=1.0)
    j.write(at(9, 51), "stop_submit", intent="H4:SPY:1", order="S", quantity=1)
    j.write(at(9, 55), "fill", order="S", quantity=1, price=9.0, commission=1.0)
    j.close()
    assert client.get("/api/status").json()["finding"] == "Flat. 1 trades today, -3.00 USD after costs."
    h = client.get("/api/health").json()
    states = {c["name"]: c["state"] for c in h["checks"]}
    assert h["overall"] == "bad" and states["IB Gateway"] == "bad" and states["Watchdog"] == "bad"
    assert states["Phone alerts"] == "warn" and states["Market data"] == "info"
    assert client.get("/api/tape").json() == {"available": False, "symbols": {}, "age_s": None}
    sig = client.get("/api/signals").json()
    assert sig["research"] is None and sig["signals"] == []
    st = client.get("/api/settings").json()
    assert (
        st["research_ran"] is False
        and st["paper_setups"] == []
        and st["alerts"] == {"ntfy": False, "telegram": False}
    )


def test_read_views(demo):
    client, root, clock = demo
    sig = client.get("/api/signals").json()
    assert sig["outcomes"] == {"refused by risk": 2, "in trade": 1, "traded": 2, "not enabled": 1}
    assert sig["research"]["passed"] == [] and len(sig["research"]["setups"]) == 5
    tr = client.get("/api/trades?day=2026-10-27").json()
    assert [t["symbol"] for t in tr["trades"]] == ["SPY", "QQQ", "MSFT"] and tr["unreconciled"] == []
    j = client.get("/api/journal").json()
    assert j["days"] == ["2026-10-27"] and j["events"]["equity_mark"] == 301 and len(j["rows"]) == 200
    assert j["rows"][0]["ts_ns"] >= j["rows"][1]["ts_ns"]
    assert client.get("/api/journal?exclude=equity_mark").json()["total"] == j["total"] - 301
    assert client.get("/api/journal?event=fill,signal&source=node").json()["total"] == 11
    assert client.get("/api/journal?q=AAPL&limit=1").json()["total"] == 3  # start, signal, refusal
    assert client.get("/api/journal?source=ops").json()["total"] == 1
    assert client.get("/api/journal?day=yesterday").status_code == 400
    t = client.get("/api/tape").json()
    assert t["available"] and not t["stale"] and t["symbols"]["MSFT"]["bars"][-1]["close"] == 431.62
    assert client.get("/api/approvals").json()["items"] == []
    assert client.get("/api/proposals").json()["items"] == []
    (root / "reports" / "proposals").mkdir()
    (root / "reports" / "proposals" / "p1.json").write_text(
        json.dumps({"title": "Tighter H4 zone", "token": "x"})
    )
    (root / "reports" / "proposals" / "bad.json").write_text("[")
    items = client.get("/api/proposals").json()["items"]
    assert items == [{"title": "Tighter H4 zone", "token": "[redacted]", "file": "p1.json"}]
    assert client.get("/api/reports").json()["items"][0]["kind"] == "premarket"
    h = client.get("/api/health").json()
    assert h["overall"] == "info" or h["overall"] == "ok"
    assert {c["name"]: c["state"] for c in h["checks"]}["Phone alerts"] == "ok"
    st = client.get("/api/settings").json()
    assert st["risk_limits"]["loss"]["max_daily_loss_pct"] == 2 and st["jev"]["enabled"] is False
    assert "secret-topic" not in json.dumps(st) and st["alerts"]["ntfy"] is True
    page = client.get("/")
    assert page.status_code == 200 and "FlowForge" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_health_flags_changed_limits_stale_tape_and_failed_alerts(demo):
    client, root, clock = demo
    Journal(root / "data" / "journal" / "node").write(at(9, 26), "start", risk_limits_sha256="old")
    clock["now"] = at(15, 0) + 40 * NS
    Journal(root / "data" / "journal" / "ops").write(
        at(15, 0), "alert_send_failed", channel="ntfy", error="HTTP 403"
    )
    with (root / "data" / "journal" / "node" / "2026-10-27.jsonl").open("a") as fh:
        fh.write("{torn\n")
    checks = {c["name"]: c for c in client.get("/api/health").json()["checks"]}
    assert checks["Risk limits"]["state"] == "bad" and "restart" in checks["Risk limits"]["detail"]
    assert checks["Market data"]["state"] == "warn" and checks["Trading node"]["state"] == "bad"
    assert checks["Alert delivery"]["detail"].endswith("HTTP 403") and checks["Journal"]["state"] == "warn"
    (root / "config" / "risk_limits.yaml").write_text("version: 99\n")
    assert {c["name"]: c["state"] for c in client.get("/api/health").json()["checks"]}["Risk limits"] == "bad"
    (root / "config" / "jev.yaml").write_text("jev:\n  model: other\n")
    (root / "run" / "watchdog.json").write_text(json.dumps({"ts_ns": 0}))


def test_settings_with_unreadable_jev_config(demo):
    client, root, _ = demo
    (root / "config" / "jev.yaml").write_text("jev:\n  model: other\n")
    assert client.get("/api/settings").json()["jev"] == {"valid": False, "error": "ValueError"}


def test_kill_is_engage_only_and_guarded(demo):
    client, root, _ = demo
    assert client.post("/api/kill", json={"reason": "x"}).status_code == 422  # reason too short
    assert client.post("/api/kill", json={"reason": "stop it"}).status_code == 400  # no header
    r = client.post(
        "/api/kill",
        json={"reason": "stop it"},
        headers={"X-FlowForge-Action": "engage-kill", "Origin": "https://evil.example"},
    )
    assert r.status_code == 403 and not (root / "run" / "KILL").exists()
    ok = client.post(
        "/api/kill",
        json={"reason": "stop it"},
        headers={"X-FlowForge-Action": "engage-kill", "Origin": "http://testserver"},
    )
    assert ok.status_code == 200 and (root / "run" / "KILL").exists()
    again = client.post(
        "/api/kill", json={"reason": "stop again"}, headers={"X-FlowForge-Action": "engage-kill"}
    )
    assert again.json()["already_on"] is True and "stop it" in again.json()["reason"]
    rec = [
        json.loads(x)
        for x in next((root / "data" / "journal" / "dashboard").glob("*.jsonl")).read_text().splitlines()
    ]
    assert [r["event"] for r in rec] == ["kill_engaged", "kill_engaged"]
    # nothing can turn it off, clear a halt or change a limit
    for method, url in (
        ("delete", "/api/kill"),
        ("post", "/api/release"),
        ("post", "/api/reenable"),
        ("put", "/api/settings"),
        ("post", "/api/settings"),
    ):
        assert getattr(client, method)(url).status_code in (404, 405)
    assert (root / "run" / "KILL").exists()


def test_session_states_and_research_without_a_file(tmp_path):
    assert session_state(at(9, 0))["state"] == "pre"
    assert session_state(at(16, 30))["state"] == "post"
    assert session_state(at(12, 0, d=31))["state"] == "closed"  # Saturday
    assert research_summary(tmp_path) is None
    (tmp_path / "phase5").mkdir()
    (tmp_path / "phase5" / "walkforward.json").write_text("[]")
    assert research_summary(tmp_path) is None


def test_demo_day_and_heartbeat_thread(tmp_path):
    assert demo_day(datetime(2026, 10, 27, 10, 0, tzinfo=ET)).isoformat() == "2026-10-26"
    assert demo_day(datetime(2026, 10, 26, 10, 0, tzinfo=ET)).isoformat() == "2026-10-23"  # Monday morning
    write_demo(tmp_path, REPO_CONFIG, DEMO_NOW)
    th = keep_beating(tmp_path, demo_day(DEMO_NOW), every_s=0.01)
    assert th.daemon and th.is_alive()


def test_entry_point_builds_the_app_without_serving(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.chdir(REPO_CONFIG.parent)
    assert api_main.main(["--demo", str(tmp_path / "demo"), "--port", "9999"]) == 0
    assert seen["port"] == 9999 and seen["host"] == "127.0.0.1"
    assert api_main.main(["--root", str(tmp_path), "--run-dir", str(tmp_path / "run")]) == 0


def test_paper_gate_view(demo):
    client, root, _ = demo
    d = client.get("/api/paper").json()
    assert d["scorecard"]["trades"] == 2 and d["scorecard"]["passes"] is False
    assert d["drills"] == [] and d["soaks"] == []
    paper = root / "reports" / "paper"
    (paper / "drill-round-trip-2026-10-27T150000Z.json").write_text(
        json.dumps(
            {
                "drill": "round-trip",
                "started": "2026-10-27T15:00:00Z",
                "passed": False,
                "checks": [{"name": "IOC entry filled", "ok": False}, {"name": "x", "ok": True}],
            }
        )
    )
    (paper / "drill-broken-x.json").write_text("[]")
    (paper / "soak-20261027T200000Z.json").write_text(json.dumps([{"problems": []}, {"problems": ["kill"]}]))
    d = client.get("/api/paper").json()
    assert d["drills"] == [
        {
            "drill": "round-trip",
            "started": "2026-10-27T15:00:00Z",
            "passed": False,
            "failing": ["IOC entry filled"],
        }
    ]
    assert d["soaks"] == [{"file": "soak-20261027T200000Z", "cycles": 2, "clean": 1}]
