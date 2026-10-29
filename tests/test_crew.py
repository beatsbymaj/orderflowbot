"""The crew: every agent's state is read from files, and only real work counts as working."""

from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient
from test_dashboard_api import REPO_CONFIG, at, demo  # noqa: F401 (the fixture)

from flowforge.api.app import ApiPaths, create_app
from flowforge.api.crew import CrewInputs, build_crew, crew_summary

RUNNING = CrewInputs(
    live=True,
    node_age_s=1.0,
    node_fresh_s=15,
    tape_age_s=2.0,
    tape_stale_s=30,
    watchdog_age_s=3.0,
    gateway_up=True,
    kill_on=False,
    halt=None,
    jev_enabled=True,
    jev_shadow=True,
    alert_channels=["ntfy"],
    counts={
        "signal": 3,
        "signal_skipped": 1,
        "jev_decision": 3,
        "risk_decision": 4,
        "order_submit": 2,
        "fill": 1,
    },
    last_record_age_s=5.0,
)


def states(i: CrewInputs) -> dict[str, str]:
    return {a.id: a.state for a in build_crew(i)}


def test_a_running_node_in_market_hours_has_its_chain_working():
    agents = {a.id: a for a in build_crew(RUNNING)}
    assert [a.state for a in agents.values()] == ["working"] * 7 + ["ready", "working"]
    assert agents["strategist"].detail == "3 signals, 1 skipped today"
    assert agents["executor"].detail == "2 orders, 1 fill today"
    assert agents["jev"].detail == "3 answers today, shadow: asks, never vetoes"
    assert crew_summary(build_crew(RUNNING))["line"] == "8 of 9 agents working"


def test_nothing_running_is_all_off_and_says_so():
    i = replace(
        RUNNING,
        node_age_s=None,
        tape_age_s=None,
        watchdog_age_s=None,
        gateway_up=False,
        jev_enabled=False,
        alert_channels=[],
        counts={},
        last_record_age_s=None,
    )
    assert set(states(i).values()) == {"off"}
    agents = {a.id: a for a in build_crew(i)}
    assert agents["executor"].detail == "IB Gateway not connected"
    assert agents["jev"].detail == "switched off until the tests say it helps"
    assert crew_summary(build_crew(i))["line"] == "Every agent is off: the bot isn't running"


def test_kill_switch_blocks_guard_and_executor_only():
    s = states(replace(RUNNING, kill_on=True))
    assert s["guard"] == s["executor"] == "blocked" and s["scout"] == "working"
    summary = crew_summary(build_crew(replace(RUNNING, kill_on=True)))
    assert summary["line"] == "Guard and Executor stopped by the kill switch or a halt"
    halted = {a.id: a for a in build_crew(replace(RUNNING, halt="daily loss limit"))}
    assert halted["guard"].detail == "halted: daily loss limit"


def test_market_closed_is_ready_not_working():
    i = replace(RUNNING, live=False, last_record_age_s=600.0, watchdog_age_s=40.0, jev_shadow=False)
    s = states(i)
    assert s["scout"] == s["tape"] == s["guard"] == s["executor"] == "ready"
    assert s["watchdog"] == "off" and s["archivist"] == "ready"
    agents = {a.id: a for a in build_crew(i)}
    assert agents["scout"].detail == "market closed" and agents["archivist"].detail == "last entry 10 min ago"
    assert agents["jev"].detail.endswith("can veto a signal")
    assert crew_summary(build_crew(i))["line"] == "Standing by: nothing to do right now"


def test_stale_node_and_quiet_tape():
    stale = {a.id: a for a in build_crew(replace(RUNNING, node_age_s=40.0))}
    assert stale["tape"].state == "off" and stale["tape"].detail == "no heartbeat for 40 s"
    assert stale["jev"].state == "off" and stale["archivist"].state == "off"
    down = {a.id: a for a in build_crew(replace(RUNNING, node_age_s=None, kill_on=True))}
    assert down["guard"].state == "blocked" and down["executor"].state == "off"
    quiet = {a.id: a for a in build_crew(replace(RUNNING, tape_age_s=90.0, last_record_age_s=None))}
    assert quiet["scout"].state == "ready" and quiet["scout"].detail == "waiting for the first trades"
    assert quiet["archivist"].detail == "nothing written today"


def test_crew_endpoint_reads_the_demo_files(demo):  # noqa: F811
    client, root, clock = demo
    c = client.get("/api/crew").json()
    s = {a["id"]: a["state"] for a in c["agents"]}
    assert s["scout"] == s["strategist"] == s["guard"] == s["executor"] == s["watchdog"] == "working"
    assert s["jev"] == "off" and s["herald"] == "ready"  # repo config: Jev off; demo env: ntfy set
    assert c["chain"][0] == "scout" and c["support"] == ["watchdog", "herald", "archivist"]
    (root / "run" / "KILL").write_text(json.dumps({"reason": "test", "by": "test", "ts_ns": clock["now"]}))
    s = {a["id"]: a["state"] for a in client.get("/api/crew").json()["agents"]}
    assert s["guard"] == "blocked"


def test_crew_endpoint_with_an_unreadable_jev_config(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "risk_limits.yaml").write_text((REPO_CONFIG / "risk_limits.yaml").read_text())
    (tmp_path / "config" / "jev.yaml").write_text("jev: {model: other}\n")
    paths = ApiPaths(root=tmp_path, run_dir=tmp_path / "run", kill_file=tmp_path / "run" / "KILL", env={})
    now = at(15, 0)
    client = TestClient(create_app(paths, clock=lambda: now, port_check=lambda h, p: False))
    c = client.get("/api/crew").json()
    assert {a["state"] for a in c["agents"]} == {"off"} and c["working"] == 0
