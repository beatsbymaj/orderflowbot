"""Phase 5 walk-forward on a made-up market where H1 always works and H2 never does."""

from __future__ import annotations

import gzip
import json
from datetime import date

from conftest import et_ns

from flowforge.data.ticks import format_ts_ns
from flowforge.research import walkforward as wf
from flowforge.research.quotes import MemoryQuotes
from flowforge.signals.setups import Signal

NS = 1_000_000_000


def days(n=40, start="2026-08-03"):
    return wf.expected_days(start, "2026-12-31")[:n]


def make_market(n_days=40):
    quotes = []
    sds = []
    for i, d in enumerate(days(n_days)):
        day = date.fromisoformat(d)
        ends = [et_ns(h, m, day=day) for h in range(9, 16) for m in range(0, 60, 5)]
        ends = [e for e in ends if et_ns(9, 35, day=day) <= e <= et_ns(15, 45, day=day)]
        for e in ends:
            quotes += [(e - NS // 2, 99.58, 99.60), (e + NS // 10, 99.59, 99.61)]
        sigs = []
        for k, (h, m) in enumerate(((10, 32), (13, 2))):  # off the 5-min grid, so no clash
            ts = et_ns(h, m, day=day)
            lose = k == 1 and i % 5 == 0
            ask = 99.60 + 0.01 * k
            quotes += [(ts - NS // 2, ask - 0.02, ask), (ts + NS // 10, ask - 0.01, ask + 0.01)]
            quotes += [(ts + 60 * NS, 98.0, 98.02) if lose else (ts + 60 * NS, 101.2, 101.22)]
            quotes += [(ts + 61 * NS, 99.58, 99.60)]
            sigs.append(Signal("H1", "SPY", ts, d, 99.0, 99.3, 0.05, 5.0, (101.0, 101.5), (), "win"))
        for h, m in ((11, 2), (14, 2)):
            ts = et_ns(h, m, day=day)
            quotes += [
                (ts - NS // 2, 99.58, 99.60),
                (ts + NS // 10, 99.59, 99.61),
                (ts + 30 * NS, 98.0, 98.02),
            ]
            quotes += [(ts + 31 * NS, 99.58, 99.60)]
            sigs.append(Signal("H2", "SPY", ts, d, 99.0, 99.3, 0.05, 5.0, (101.0,), (), "lose"))
        sds.append(wf.StockDay("SPY", d, sigs, ends, 5.0, 0.05))
    return sds, MemoryQuotes({"SPY": quotes})


def test_walkforward_passes_the_real_edge_and_fails_the_rest(limits, tmp_path):
    sds, quotes = make_market()
    res = wf.run(sds, quotes, limits, reps=500)
    assert len(res.folds) == 2 and res.folds[0]["test"] == [res.days[20], res.days[29]]
    h1, h2 = res.oos["H1"], res.oos["H2"]
    assert h1["trades"] == 40 and h1["mean_r"] > 1
    assert h1["passes"], h1["checks"]
    assert not h2["passes"] and h2["mean_r"] < 0 and h2["exit_reasons"] == {"stop": 40}
    assert res.oos["H3"]["trades"] == 0 and not res.oos["H3"]["passes"]
    assert res.control["trades"] > 0
    # Folds with no train trades fall back to the book default.
    assert res.folds[0]["chosen"]["H3"]["config"] == "zone/volume"
    acct = res.account["all_hypotheses"]
    # Cash account (the repo setting): no day-trade count, so more than legacy PDT's 3 per 5 days.
    assert "legacy PDT day-trade limit" not in acct["trades_skipped"]
    assert acct["trades_taken"] > 3 * 5
    assert res.account["passing_only"]["trades_taken"] > 0
    path = wf.write(res, tmp_path, ["SPY 2026-08-01"])
    text = path.read_text()
    assert "Passed the pre-registered bar: H1" in text and "Missing stock-days" in text
    data = json.loads((tmp_path / "walkforward.json").read_text())
    assert data["oos"]["H1"]["passes"] is True
    assert (tmp_path / "trades.csv").read_text().count("\n") == 1 + 80


def test_nothing_passes_on_a_flat_market(limits, tmp_path):
    sds, quotes = make_market(30)
    for sd in sds:
        sd.signals = [s for s in sd.signals if s.setup == "H2"]
    res = wf.run(sds, quotes, limits, reps=200)
    assert not any(o["passes"] for o in res.oos.values())
    assert "No idea passed the bar" in wf.render(res, [])
    assert res.account["passing_only"] is None


def test_choose_needs_ten_train_trades_and_prefers_the_book_default_on_ties():
    def t(r, day="d1"):
        return wf.Trade("H1", "SPY", day, "", 0, 0, 1, 1, 0, 0, 0, 1, "eod", 0, 0, 0, r)

    few = {("atr", "2r"): [t(5.0)] * 9}
    assert wf.choose(few, {"d1"}) == ("zone", "volume")
    tie = {("atr", "2r"): [t(1.0)] * 10, ("zone", "volume"): [t(1.0)] * 10}
    assert wf.choose(tie, {"d1"}) == ("zone", "volume")
    best = {("atr", "2r"): [t(2.0)] * 10, ("zone", "volume"): [t(1.0)] * 10}
    assert wf.choose(best, {"d1"}) == ("atr", "2r")
    assert len(wf.folds([str(i) for i in range(60)])) == 4


def test_stock_day_from_feature_files(tmp_path):
    day = date(2026, 10, 27)
    ctx = {"day": day.isoformat(), "prior_day": {"high": 102.0, "low": 97.0, "close": 100.0}, "levels": {}}
    recs = [
        {
            "type": "bar",
            "symbol": "SPY",
            "tf": 300,
            "end": format_ts_ns(et_ns(10, 35, day=day)),
            "trades": 5,
            "row": 0.05,
            "open": 100,
            "low": 99.9,
            "high": 100.1,
            "close": 100,
            "gap": False,
            "context": ctx,
        },
        {
            "type": "bar",
            "symbol": "SPY",
            "tf": 300,
            "end": format_ts_ns(et_ns(9, 30, day=day)),
            "trades": 5,
            "row": 0.05,
            "open": 100,
            "low": 99.9,
            "high": 100.1,
            "close": 100,
            "gap": False,
            "context": ctx,
        },
        {"type": "day_stats", "symbol": "SPY"},
    ]
    folder = tmp_path / "SPY"
    folder.mkdir()
    with gzip.open(folder / f"{day.isoformat()}.jsonl.gz", "wt") as fh:
        for r in recs:
            fh.write(json.dumps(r) + "\n")
    (out,) = wf.load_stock_days(tmp_path, ["SPY"], None)
    assert out.atr == 5.0 and out.row == 0.05 and len(out.bar_ends) == 1 and out.signals == []
    assert wf.load_stock_days(tmp_path, ["SPY"], ["2026-10-28"]) == []
    assert wf.main(["--features", str(tmp_path / "none"), "--out", str(tmp_path / "r")]) == 1


def test_cli_end_to_end_on_stored_ticks(tmp_path, monkeypatch, capsys):
    from flowforge.features.config import FeatureConfig
    from flowforge.features.replay import run as replay
    from flowforge.features.sample import make_ticks, write_ticks_dir
    from flowforge.research.quotes import CatalogQuotes, last_before

    day = date(2026, 10, 23)
    ticks, feats, out = tmp_path / "ticks", tmp_path / "features", tmp_path / "report"
    write_ticks_dir(ticks, "AAPL", day, *make_ticks(day, minutes=30))
    replay(ticks, feats, ["AAPL"], None, FeatureConfig())
    q = CatalogQuotes(ticks, keep=1)
    assert last_before(q, "AAPL", et_ns(9, 50, day=day), 60 * NS) is not None
    assert last_before(q, "AAPL", et_ns(9, 50, day=day), 60 * NS) is not None  # cached
    last_before(q, "AAPL", et_ns(10, 50, day=day), NS)  # evicts
    args = ["--features", str(feats), "--ticks", str(ticks), "--out", str(out), "--symbols", "AAPL"]
    assert wf.main(args + ["--first", "2026-10-23", "--last", "2026-10-26"]) == 0
    text = (out / "walkforward.md").read_text()
    assert "AAPL 2026-10-26" in text and "Jev gate" in text
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert wf.main(args + ["--first", "2026-10-23", "--last", "2026-10-23", "--jev"]) == 0
    assert "gate test is skipped" in capsys.readouterr().out
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")  # no trades, so nothing is sent
    assert wf.main(args + ["--first", "2026-10-23", "--last", "2026-10-23", "--jev"]) == 0
    assert "H4: not kept" in (out / "walkforward.md").read_text()


def test_feature_replay_runs_symbols_in_parallel(tmp_path):
    from flowforge.features.config import FeatureConfig
    from flowforge.features.replay import run as replay
    from flowforge.features.sample import make_ticks, write_ticks_dir

    day = date(2026, 10, 23)
    for sym, seed in (("AAPL", 1), ("MSFT", 2)):
        write_ticks_dir(tmp_path / "ticks", sym, day, *make_ticks(day, minutes=10, seed=seed))
    rows = replay(tmp_path / "ticks", tmp_path / "f", ["AAPL", "MSFT"], None, FeatureConfig(), jobs=2)
    assert [r["symbol"] for r in rows] == ["AAPL", "MSFT"]
    assert (tmp_path / "f" / "MSFT" / "2026-10-23.jsonl.gz").exists()


def test_account_replay_cash_account_caps_buys_at_settled_cash(limits):
    from conftest import with_limits

    from flowforge.config.risk_limits import PdtRegime
    from flowforge.research.fastsim import Trade

    def trade(i, day="2026-10-27"):
        start = et_ns(10, 0) + i * 120 * 10**9
        return Trade(
            "H1",
            f"S{i}",
            day,
            "c",
            start,
            start,
            100.0,
            1,
            99.0,
            102.0,
            start + 60 * 10**9,
            100.5,
            "target",
            0.5,
            0.0,
            0.5,
            0.5,
        )

    many = [trade(i) for i in range(14)]
    trading = {"max_trades_per_day": 50}
    cash = with_limits(limits, "trading", pdt_regime=PdtRegime.CASH, **trading)
    margin = with_limits(limits, "trading", pdt_regime=PdtRegime.INTRADAY_MARGIN, **trading)
    c, m = wf.account_replay(many, cash), wf.account_replay(many, margin)
    assert c["trades_skipped"].get("settled cash used up for the day", 0) >= 1
    assert "settled cash used up for the day" not in m["trades_skipped"]
    assert "legacy PDT day-trade limit" not in c["trades_skipped"]


def test_phase5b_study_holds_winners_to_the_close_and_counts_28_trials(limits, tmp_path):
    """B1 rises all day (hold beats 2R); B2 falls to its stop. The 5b grid, default and control apply."""
    quotes, qqq, sds = [], [], []
    for d in days(40):
        day = date.fromisoformat(d)
        ends = [et_ns(h, m, day=day) for h in range(9, 16) for m in range(0, 60, 5)]
        ends = [e for e in ends if et_ns(9, 35, day=day) <= e <= et_ns(15, 45, day=day)]
        for e in ends:
            quotes += [(e - NS // 2, 99.58, 99.60), (e + NS // 10, 99.59, 99.61)]
        win, lose = et_ns(10, 7, day=day), et_ns(11, 7, day=day)
        quotes += [(win - NS // 2, 99.58, 99.60), (win + NS // 10, 99.59, 99.61)]
        quotes += [(et_ns(15, 54, day=day), 104.0, 104.02)]  # held to 15:55: +4.4 vs 1.35 risk
        qqq += [
            (lose - NS // 2, 99.58, 99.60),
            (lose + NS // 10, 99.59, 99.61),
            (lose + 30 * NS, 97.0, 97.02),
        ]
        b1 = [Signal("B1", "SPY", win, d, 98.3, 99.6, 0.05, 5.0, (), (), "drive")]
        b2 = [Signal("B2", "QQQ", lose, d, 98.3, 99.6, 0.05, 5.0, (), (), "fail")]
        sds += [wf.StockDay("SPY", d, b1, ends, 5.0, 0.05), wf.StockDay("QQQ", d, b2, [], 5.0, 0.05)]
    seen = []
    res = wf.run(
        sds,
        MemoryQuotes({"SPY": quotes, "QQQ": qqq}),
        limits,
        reps=300,
        study=wf.PHASE5B,
        on_trade=seen.append,
    )
    assert {t.setup for t in seen} >= {"B1", "B2", "CTRL"}  # every simulated trade, as it happened
    with wf.LiveTrades(tmp_path / wf.LIVE_TRADES) as live:
        live.write(seen[0])
    row = json.loads((tmp_path / wf.LIVE_TRADES).read_text())
    assert row["setup"] == seen[0].setup and set(row) == set(wf.LiveTrades.FIELDS)
    assert wf.PHASE5B.n_trials == 28 and set(res.oos) == {"B1", "B2", "B3", "B4"}
    assert res.folds[0]["chosen"]["B1"]["config"] == "level/hold"
    assert res.folds[0]["chosen"]["B3"]["config"] == "level/hold"  # no trades: the default
    assert res.oos["B1"]["exit_reasons"] == {"eod": 20} and res.oos["B1"]["mean_r"] > 2
    assert not res.oos["B2"]["passes"] and res.oos["B2"]["mean_r"] < 0
    assert res.control["trades"] > 0
    path = wf.write(res, tmp_path, [])
    text = path.read_text()
    assert text.startswith("# Phase 5b walk-forward results") and "docs/research/17" in text
    assert "B1 Opening drive" in text
    data = json.loads((tmp_path / "walkforward.json").read_text())
    assert data["study"] == "Phase 5b" and data["oos"]["B1"]["trades"] == 20
    assert ",," in (tmp_path / "trades.csv").read_text()  # held trades have no target


def test_cli_picks_the_study_defaults(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        wf, "load_stock_days", lambda f, s, d, study: seen.update(f=f, d=d, study=study) or []
    )
    monkeypatch.chdir(tmp_path)
    assert wf.main(["--study", "5b"]) == 1
    assert str(seen["f"]) == "data/features-b" and seen["study"] is wf.PHASE5B
    assert seen["d"][0] == "2026-04-01" and seen["d"][-1] == "2026-07-29"
