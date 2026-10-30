"""The Lab screen reads research logs and Jev answers; it never changes them."""

from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from flowforge.api.app import ApiPaths, create_app
from flowforge.api.lab import jev_runs, lab_view, latest_run
from flowforge.risk.calendar import NS

NOW = 2_000_000_000.0


def touch(path, age_s):
    os.utime(path, (NOW - age_s, NOW - age_s))


def test_running_done_and_stopped_runs(tmp_path):
    assert latest_run(tmp_path, NOW) is None
    (tmp_path / "data").mkdir()
    log = tmp_path / "data" / "walkforward-jev.log"
    log.write_text("loading\nH1 zone/volume: 12 signals, 3 trades\nH2 atr/2r: 647 signals, 15 trades\n\n")
    touch(log, 10)
    r = latest_run(tmp_path, NOW)
    assert r["state"] == "running" and r["steps"] == 2 and r["last_step"]["setup"] == "H2"
    assert r["log"] == "data/walkforward-jev.log" and r["lines"][-1].startswith("H2")
    touch(log, 900)
    assert latest_run(tmp_path, NOW)["state"] == "stopped"
    log.write_text(log.read_text() + "Report: reports/phase5-jev/walkforward.md\n")
    touch(log, 900)
    r = latest_run(tmp_path, NOW)
    assert r["state"] == "done" and r["report"] == "reports/phase5-jev/walkforward.md"
    older = tmp_path / "data" / "walkforward-old.log"
    older.write_text("old\n")
    touch(older, 5000)
    assert latest_run(tmp_path, NOW)["log"] == "data/walkforward-jev.log"  # the newest log wins


def test_secrets_never_reach_the_screen(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "walkforward.log").write_text("TYPESAFE_API_KEY=abc123 oops\nBearer xyz\n")
    lines = latest_run(tmp_path, NOW)["lines"]
    assert not any("abc123" in ln or "xyz" in ln for ln in lines) and "[hidden]" in lines[0]


def test_jev_answers_are_counted_and_the_latest_shown_first(tmp_path):
    run = tmp_path / "reports" / "phase5-jev"
    run.mkdir(parents=True)
    rows = [
        {"id": f"s{i}", "p_yes": i / 10, "reason": "ok", "latency_ms": 100 + i, "model": "m"}
        for i in range(20)
    ]
    rows.append({"id": "late", "p_yes": None, "reason": "timeout", "latency_ms": None, "model": "m"})
    path = run / "jev_answers.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + '\n{"id": "half')
    touch(path, 3)
    (j,) = jev_runs(tmp_path, NOW)
    assert j["answers"] == 21 and j["answered"] == 20 and j["bad_lines"] == 1
    assert j["reasons"] == {"ok": 20, "timeout": 1} and j["mean_latency_ms"] == 109.5
    assert j["recent"][0]["id"] == "late" and len(j["recent"]) == 14 and "model" not in j["recent"][0]
    empty = tmp_path / "reports" / "other"
    empty.mkdir()
    (empty / "jev_answers.jsonl").write_text("")
    (empty / "walkforward.md").write_text("# done")
    touch(empty / "jev_answers.jsonl", 50)
    v = lab_view(tmp_path, NOW, 0.6)
    assert [x["run"] for x in v["jev"]] == ["phase5-jev", "other"] and v["jev"][1]["mean_p_yes"] is None
    assert v["reports"] == [{"run": "other", "age_s": v["reports"][0]["age_s"]}] and v["threshold"] == 0.6


def test_lab_endpoint(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "walkforward.log").write_text("H4 atr/volume: 5 signals, 1 trades\n")
    paths = ApiPaths(root=tmp_path, run_dir=tmp_path / "run", kill_file=tmp_path / "run" / "KILL", env={})
    client = TestClient(create_app(paths, clock=lambda: int(NOW * NS)))
    d = client.get("/api/lab").json()
    assert d["run"]["steps"] == 1 and d["jev"] == [] and "threshold" in d


def test_unreadable_files_are_skipped(tmp_path):
    from flowforge.api.lab import _age, _tail

    assert _tail(tmp_path / "nope.log", 5) == [] and _age(tmp_path / "nope.log", NOW) is None
    (tmp_path / "reports" / "odd" / "jev_answers.jsonl").mkdir(parents=True)  # a folder, not a file
    good = tmp_path / "reports" / "good"
    good.mkdir()
    (good / "jev_answers.jsonl").write_text(
        '5\n{"id": "a", "p_yes": 0.7, "reason": "ok", "latency_ms": 90}\n'
    )
    (j,) = jev_runs(tmp_path, NOW)
    assert j["run"] == "good" and j["answers"] == 1


def test_demo_lab_loops_through_a_pretend_run(tmp_path):
    from flowforge.api.demo import DEMO_STEPS, lab_tick

    for n in range(len(DEMO_STEPS) * 4 + 1):
        lab_tick(tmp_path, n)
    r = latest_run(tmp_path, NOW + 1e9)
    assert r["steps"] == len(DEMO_STEPS) and r["state"] == "done" and r["lines"][0].startswith("Demo run")
    (j,) = jev_runs(tmp_path, NOW)
    assert j["run"] == "demo-jev" and j["answers"] > 0 and "timeout" in j["reasons"]
    lab_tick(tmp_path, len(DEMO_STEPS) * 4 + 8)  # the loop starts over
    assert latest_run(tmp_path, NOW)["steps"] == 0
