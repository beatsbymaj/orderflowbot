"""The paper node's start-up decisions (ops/paper_node.py). Nothing here connects."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from flowforge.ops import paper_node as pn
from flowforge.ops.ib_paper_check import IbSettings, NotPaperError

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
PAPER = IbSettings("127.0.0.1", 4002, 11, "DU1234567", "paper")
TODAY = date(2026, 10, 27)


def walkforward(tmp_path, passes=("H4",), config="zone/volume"):
    path = tmp_path / "walkforward.json"
    oos = {s: {"passes": s in passes, "trades": 40} for s in ("H1", "H2", "H3", "H4", "H5", *passes)}
    folds = [
        {"chosen": {s: {"config": "book/book"} for s in oos}},
        {"chosen": {s: {"config": config} for s in oos}},
    ]
    path.write_text(json.dumps({"oos": oos, "folds": folds}))
    return path


def paths(tmp_path, wf=None) -> pn.Paths:
    (tmp_path / "config").mkdir(exist_ok=True)
    for name in ("risk_limits.yaml", "jev.yaml"):
        shutil.copy(REPO_CONFIG / name, tmp_path / "config" / name)
    return pn.Paths(
        risk_limits=tmp_path / "config" / "risk_limits.yaml",
        jev=tmp_path / "config" / "jev.yaml",
        walkforward=wf or tmp_path / "missing.json",
        ticks=tmp_path / "ticks",
        state=tmp_path / "state" / "risk_state.json",
        journal=tmp_path / "journal" / "node",
        run_dir=tmp_path / "run",
    )


def test_only_passing_setups_trade_with_the_last_folds_stop_and_target(tmp_path):
    wf = walkforward(tmp_path, passes=("H1", "H4"))
    setups, notes = pn.setups_for_paper(wf, None)
    assert setups == {"H1": ("zone", "volume"), "H4": ("zone", "volume")} and notes == []
    assert pn.setups_for_paper(wf, ["H4"])[0] == {"H4": ("zone", "volume")}
    with pytest.raises(pn.PaperRefused, match="H2 did not pass"):
        pn.setups_for_paper(wf, ["H2"])


def test_nothing_passed_or_no_result_means_shadow(tmp_path):
    setups, notes = pn.setups_for_paper(walkforward(tmp_path, passes=()), None)
    assert setups == {} and "shadow" in notes[0]
    setups, notes = pn.setups_for_paper(tmp_path / "nope.json", None)
    assert setups == {} and "no walk-forward result" in notes[0]
    with pytest.raises(pn.PaperRefused, match="no setup may be enabled"):
        pn.setups_for_paper(tmp_path / "nope.json", ["H4"])


def test_broken_walkforward_results_are_refused(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(pn.PaperRefused, match="unreadable"):
        pn.setups_for_paper(bad, None)
    with pytest.raises(pn.PaperRefused, match="no stop/target"):
        pn.setups_for_paper(walkforward(tmp_path, config=""), None)
    with pytest.raises(pn.PaperRefused, match="can't trade it yet"):
        pn.setups_for_paper(walkforward(tmp_path, passes=("B1",)), None)


def test_plan_refuses_live_settings_and_the_wrong_feed(tmp_path):
    p = paths(tmp_path)
    live = IbSettings("127.0.0.1", 4001, 11, "U1234567", "live")
    with pytest.raises(NotPaperError):
        pn.make_plan(live, p, ["SPY"], "sip", None, False, TODAY)
    with pytest.raises(pn.PaperRefused, match="not a live market feed"):
        pn.make_plan(PAPER, p, ["SPY"], "test", None, False, TODAY)
    with pytest.raises(pn.PaperRefused, match="--allow-iex"):
        pn.make_plan(PAPER, p, ["SPY"], "iex", None, False, TODAY)
    plan = pn.make_plan(PAPER, p, ["SPY"], "iex", None, True, TODAY)
    assert plan.shadow and plan.account == "DU*****67"
    assert any("IEX-only" in n for n in plan.notes)
    assert plan.adv == {"SPY": 0.0} and any("entries refused" in n for n in plan.notes)
    assert plan.as_dict()["shadow"] is True


def test_volume_figure_from_the_last_downloaded_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    pn.prior_session.cache_clear()
    monkeypatch.setattr(
        "flowforge.features.replay.prior_day", lambda ticks, s, d: SimpleNamespace(close=100.0, volume=5)
    )
    assert pn.daily_dollar_volume(tmp_path, "SPY", TODAY) == 500.0

    def broken(ticks, s, d):
        raise OSError("damaged")

    pn.prior_session.cache_clear()
    monkeypatch.setattr("flowforge.features.replay.prior_day", broken)
    assert pn.daily_dollar_volume(tmp_path, "SPY", TODAY) == 0.0
    pn.prior_session.cache_clear()


def test_build_node_routes_to_ib_and_keeps_the_risk_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "test-id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "test-secret")
    plan = pn.Plan(["SPY", "QQQ"], "iex", {}, {}, "")
    node = pn.build_node(PAPER, plan, paths(tmp_path))
    try:
        assert not node.is_running and node.cache.strategy_ids() == []
    finally:
        node.dispose()
    cfg = pn.instrument_provider_config(["SPY"])
    assert {str(i) for i in cfg.load_ids} == {"SPY.XNAS"} and cfg.symbol_to_mic_venue == {"SPY": "XNAS"}
    with pytest.raises(NotPaperError):
        pn.build_node(IbSettings("h", 4001, 1, "U1", "live"), plan, paths(tmp_path))


def test_build_trading_wires_features_into_the_guarded_strategy(tmp_path):
    p = paths(tmp_path, walkforward(tmp_path))
    plan = pn.Plan(["SPY"], "sip", {"H4": ("zone", "volume")}, {"SPY": 30e9}, "DU*****67")
    actor, strategy, journal = pn.build_trading(plan, p, TODAY)
    assert strategy.venue.value == "IB" and strategy.heartbeat_path == p.heartbeat
    assert strategy.adv == {"SPY": 30e9} and strategy.gate is None
    assert actor.client_id == "ALPACA" and actor._on_record == strategy.on_feature
    journal.close()


def test_main_preflight_prints_the_plan_and_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    shutil.copytree(REPO_CONFIG, tmp_path / "config")
    monkeypatch.setenv("IB_ACCOUNT_ID", "DU1234567")
    assert pn.main(["--preflight", "--feed", "iex", "--allow-iex", "--symbols", "SPY"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["shadow"] is True and out["symbols"] == ["SPY"]
    assert pn.main(["--preflight", "--feed", "iex"]) == 2
    assert "refusing to start" in capsys.readouterr().err
    monkeypatch.setenv("IB_PORT", "4001")
    assert pn.main(["--preflight", "--allow-iex", "--feed", "iex"]) == 2


def test_run_journals_start_and_stop(tmp_path, monkeypatch):
    p = paths(tmp_path)
    plan = pn.Plan(["SPY"], "sip", {}, {"SPY": 1.0}, "DU*****67")

    class FakeNode:
        added: list = []

        def add_actor(self, a):
            self.added.append(a)

        def add_strategy(self, s):
            self.added.append(s)

        def dispose(self):
            self.added.append("disposed")

    node = FakeNode()
    monkeypatch.setattr(pn, "build_node", lambda *a: node)

    async def quick(n, seconds):
        return None

    monkeypatch.setattr(pn, "_run_until_stopped", quick)
    assert pn.run(PAPER, plan, p, TODAY, 1) == 0
    lines = [json.loads(x) for f in p.journal.glob("*.jsonl") for x in f.read_text().splitlines()]
    assert [r["event"] for r in lines] == ["paper_node_start", "paper_node_stop"]
    assert lines[0]["shadow"] is True and node.added[-1] == "disposed"

    async def boom(n, seconds):
        raise RuntimeError("gateway gone")

    monkeypatch.setattr(pn, "_run_until_stopped", boom)
    with pytest.raises(RuntimeError):
        pn.run(PAPER, plan, p, TODAY, 1)
    lines = [json.loads(x) for f in p.journal.glob("*.jsonl") for x in f.read_text().splitlines()]
    assert any(r["event"] == "error" and "gateway gone" in r["error"] for r in lines)


def test_run_until_stopped_stops_the_node_after_the_time_limit():
    import asyncio

    class Handle:
        def __init__(self, node):
            self.node = node

        def stop(self):
            self.node.stopped.set()

    class Node:
        def __init__(self):
            self.stopped = asyncio.Event()

        def handle(self):
            return Handle(self)

        async def run_async(self):
            await self.stopped.wait()

    async def go():
        n = Node()
        await pn._run_until_stopped(n, 0.05)
        assert n.stopped.is_set()
        n2 = Node()
        n2.stopped.set()
        await pn._run_until_stopped(n2, None)

    asyncio.run(go())
