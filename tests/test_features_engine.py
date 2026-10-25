import gzip
import json
import random
from dataclasses import replace
from datetime import date

import pytest

from flowforge.data.ticks import NS
from flowforge.features import parity, replay
from flowforge.features.config import FeatureConfig
from flowforge.features.context import PriorDay
from flowforge.features.engine import FeatureEngine
from flowforge.features.sample import make_ticks, session_open_ns, write_ticks_dir

DAY = date(2026, 10, 23)


def ordered(trades, quotes):
    events = [(t.ts, 0, "t", t) for t in trades] + [(q.ts, 1, "q", q) for q in quotes]
    return [(k, r) for _ts, _rank, k, r in sorted(events, key=lambda e: (e[0], e[1]))]


def run_engine(events, symbol="AAPL", config=None, setup=None):
    e = FeatureEngine(config)
    if setup:
        setup(e)
    for kind, rec in events:
        (e.on_trade if kind == "t" else e.on_quote)(symbol, rec)
    e.flush()
    return e.records


@pytest.fixture(scope="module")
def sample():
    return make_ticks(DAY, minutes=20)


@pytest.fixture(scope="module")
def records(sample):
    return run_engine(ordered(*sample))


def test_bars_cover_the_session_on_every_timeframe(records):
    bars = [r for r in records if r["type"] == "bar"]
    by_tf = {tf: [b for b in bars if b["tf"] == tf] for tf in (60, 300, 1800)}
    # quotes start 09:25, trades end 09:50 -> 25 one-minute bars, 5 five-minute, 2 thirty-minute
    assert [len(by_tf[tf]) for tf in (60, 300, 1800)] == [25, 5, 2]
    assert by_tf[60][0]["start"] == "2026-10-23T13:25:00.000000000Z"
    assert by_tf[60][0]["trades"] == 0 and by_tf[60][0]["nbbo"]["updates"] > 0
    # Every timeframe adds up to the same volume and the same final CVD.
    vols = {tf: sum(b["volume"] for b in by_tf[tf]) for tf in by_tf}
    assert len(set(vols.values())) == 1
    assert len({by_tf[tf][-1]["cvd"] for tf in by_tf}) == 1
    b = by_tf[300][2]
    assert b["delta"] == b["buy"] - b["sell"] and b["row"] == 0.05
    assert sum(sum(cell) for cell in b["footprint"].values()) <= b["volume"]
    assert 0 < b["off_exchange_share"] < 1 and b["context"]["vwap"] is not None


def test_day_stats_and_planted_events(sample, records):
    trades, _ = sample
    stats = next(r for r in records if r["type"] == "day_stats")
    assert stats["trades"] == len(trades) and stats["no_volume_prints"] == 1  # the M print
    assert stats["late_events_total"] == 0 and stats["unknown_conditions"] == {}
    assert stats["nbbo_locked"] > 0 and stats["nbbo_crossed"] > 0
    assert stats["method_quote"] > stats["method_tick"] > 0
    planted_iso = sum(1 for t in trades if t.conditions == ("F",)) // 4
    sweeps = [r for r in records if r["type"] == "sweep"]
    assert len(sweeps) == planted_iso == stats["sweeps"]
    assert all(s["side"] == 1 and s["venues"] == ["K", "P", "Q", "Z"] and s["shares"] == 3200 for s in sweeps)
    big = [r for r in records if r["type"] == "large_print"]
    assert len(big) == sum(1 for t in trades if t.size == 15_000) == stats["large_prints"]
    assert all(r["exchange"] == "D" and r["side"] == -1 for r in big)
    levels = [r for r in records if r["type"] == "bar"][-1]["context"]["levels"]
    assert "large_print" in levels


def test_live_jitter_inside_the_window_changes_nothing(sample, records):
    """Shuffle arrival order within 20 ms buckets (window is 50 ms): same output."""
    events = ordered(*sample)
    rng = random.Random(3)
    buckets: dict[int, list] = {}
    for ev in events:
        buckets.setdefault(ev[1].ts // 20_000_000, []).append(ev)
    shuffled = []
    for key in sorted(buckets):
        chunk = buckets[key]
        rng.shuffle(chunk)
        shuffled += chunk
    assert shuffled != events
    assert run_engine(shuffled) == records


def test_late_events_are_counted_not_processed(sample):
    events = ordered(*sample)
    late = events.pop(5000)
    events.insert(9000, late)
    out = run_engine(events, config=FeatureConfig(reorder_window_ms=1))
    assert next(r for r in out if r["type"] == "day_stats")["late_events_total"] == 1


def test_two_days_reset_session_state(sample):
    trades, quotes = sample
    shift = 3 * 24 * 3600 * NS  # Monday 10-26
    day2 = [replace(t, ts=t.ts + shift) for t in trades], [replace(q, ts=q.ts + shift) for q in quotes]
    prior = PriorDay(251.0, 249.0, 250.0, 5_000_000)

    def setup(e):
        e.seed_prior_day("AAPL", "2026-10-26", prior)
        e.mark_gap("AAPL", session_open_ns(date(2026, 10, 26)), session_open_ns(date(2026, 10, 26)) + 60 * NS)

    out = run_engine(ordered(*sample) + ordered(*day2), setup=setup)
    stats = [r for r in out if r["type"] == "day_stats"]
    assert [s["day"] for s in stats] == ["2026-10-23", "2026-10-26"]
    assert stats[1]["vpin_bucket"] == 25_000 and stats[0]["vpin_bucket"] == 200_000
    day2_bars = [r for r in out if r["type"] == "bar" and r["start"].startswith("2026-10-26")]
    assert day2_bars[0]["context"]["prior_day"] == {"high": 251.0, "low": 249.0, "close": 250.0}
    first_cvd = next(b for b in day2_bars if b["tf"] == 60 and b["trades"])
    assert abs(first_cvd["cvd"]) <= first_cvd["volume"]  # CVD restarted
    assert [b["gap"] for b in day2_bars if b["tf"] == 60][5:7] == [True, False]


@pytest.fixture(scope="module")
def ticks_dir(tmp_path_factory, sample):
    root = tmp_path_factory.mktemp("ticks")
    write_ticks_dir(root, "AAPL", DAY, *sample)
    earlier = make_ticks(date(2026, 10, 22), minutes=5, seed=1)
    from flowforge.data.alpaca_ticks import _write_raw_trades

    _write_raw_trades(root / "raw" / "AAPL" / "2026-10-22" / "trades_0930.csv.gz", earlier[0])
    return root


def test_replay_reads_catalog_and_raw_conditions(ticks_dir, sample):
    trades, _ = sample
    prior = replay.prior_day(ticks_dir, "AAPL", DAY)
    assert prior is not None and prior.volume > 0 and prior.low <= prior.close <= prior.high
    assert replay.prior_day(ticks_dir, "AAPL", date(2026, 10, 22)) is None
    d = replay.load_day(ticks_dir, "AAPL", DAY)
    assert len(d.conditions) == len(trades) and d.end - d.start == (6 * 60 + 35) * 60 * NS
    assert replay.trading_days(ticks_dir, "AAPL") == [date(2026, 10, 22), DAY]
    out: list[dict] = []
    counts = replay.replay_day(ticks_dir, "AAPL", DAY, FeatureConfig(), out.append, minutes=10)
    assert counts["trades"] > 0 and out[-1]["type"] == "day_stats"
    assert out[-1]["trades"] == counts["trades"] and out[-1]["vpin_bucket"] == prior.volume // 200
    assert "unknown_conditions" in out[-1]


def test_replay_cli_writes_features_and_summary(ticks_dir, tmp_path):
    out = tmp_path / "features"
    assert (
        replay.main(
            ["--ticks", str(ticks_dir), "--out", str(out), "--symbols", "AAPL", "--days", "2026-10-23"]
        )
        == 0
    )
    with gzip.open(out / "AAPL" / "2026-10-23.jsonl.gz", "rt") as fh:
        lines = [json.loads(line) for line in fh]
    assert lines[-1]["type"] == "day_stats"
    text = (out / "summary.md").read_text()
    assert "| AAPL | 2026-10-23 |" in text and "Unknown condition codes seen: none." in text
    row = json.loads((out / "summary.json").read_text())[0]
    assert row["quote_rule"] > 0.9 and row["sweeps"] > 0 and row["off_exchange_share"] > 0


def test_parity_gate_three_paths_identical(ticks_dir, tmp_path):
    """Phase 3 exit gate on sample ticks: replay, Nautilus backtest and the live client agree."""
    code = parity.main(
        [
            "--ticks",
            str(ticks_dir),
            "--symbol",
            "AAPL",
            "--day",
            "2026-10-23",
            "--minutes",
            "8",
            "--out",
            str(tmp_path),
        ]
    )
    report = json.loads((tmp_path / "parity-AAPL-2026-10-23.json").read_text())
    assert code == 0 and report["identical"], report.get("first_difference")
    assert report["records"]["live"] == report["records"]["replay"] > 10
    counts = report["live_counts"]
    assert counts["sent"] == counts["received"] and counts["sent"]["trades"] > 1000


def test_compare_reports_the_first_difference():
    a = [{"x": 1}, {"x": 2}]
    r = parity.compare({"replay": a, "live": [{"x": 1}, {"x": 3}], "backtest": a[:1]})
    assert not r["identical"] and r["first_difference"]["index"] == 1
    assert r["first_difference"]["backtest"] is None and r["first_difference"]["live"] == {"x": 3}
    r["minutes"], r["symbol"], r["day"], r["events"] = None, "SPY", "2026-10-23", 5
    r["seconds"] = {"replay": 1, "live": 2, "backtest": 3}
    assert "DIFFERENT" in parity.describe(r) and "First difference" in parity.describe(r)


def test_thresholds_adapt_to_the_prior_day(sample):
    """The prior day's 10th-largest print and 20th-largest ISO cluster set today's filters."""
    from flowforge.data.ticks import Trade

    cfg = FeatureConfig(sweeps_per_day=2, sweep_gap_ms=5)
    iso = [
        Trade(0, 10.0, 100, "Q", ("F",), 1, "C"),
        Trade(1_000_000, 10.0, 100, "P", ("F",), 2, "C"),  # cluster 1: $2,000, 2 venues
        Trade(50_000_000, 10.0, 500, "Q", ("F",), 3, "C"),  # single venue: not a sweep
        Trade(90_000_000, 10.0, 300, "Q", ("F", "I"), 4, "C"),
        Trade(91_000_000, 10.0, 300, "K", ("F",), 5, "C"),  # cluster 2: $6,000
        Trade(92_000_000, 10.0, 999, "K", (" ",), 6, "C"),  # not ISO: ignored
    ]
    assert replay.iso_cluster_notionals(iso, cfg) == [6000.0, 2000.0]

    events = ordered(*sample)
    strict = PriorDay(251.0, 249.0, 250.0, 5_000_000, large_print_usd=1e9, sweep_usd=1e9)
    out = run_engine(events, setup=lambda e: e.seed_prior_day("AAPL", "2026-10-23", strict))
    stats = next(r for r in out if r["type"] == "day_stats")
    assert "large_prints" not in stats and "sweeps" not in stats
    loose = PriorDay(251.0, 249.0, 250.0, 5_000_000, large_print_usd=1.0, sweep_usd=1.0)
    out = run_engine(events, setup=lambda e: e.seed_prior_day("AAPL", "2026-10-23", loose))
    stats = next(r for r in out if r["type"] == "day_stats")
    planted = sum(1 for t in sample[0] if t.size == 15_000)
    assert stats["large_prints"] == planted and stats["sweeps"] > 0  # floors still apply


def test_prior_day_sets_threshold_fields(ticks_dir):
    prior = replay.prior_day(ticks_dir, "AAPL", DAY, FeatureConfig(large_prints_per_day=3, sweeps_per_day=1))
    assert prior.large_print_usd is not None and prior.large_print_usd >= 15_000 * 249
    assert prior.sweep_usd is None or prior.sweep_usd > 0
    assert (
        replay.prior_day(ticks_dir, "AAPL", DAY, FeatureConfig(large_prints_per_day=10**9)).large_print_usd
        is None
    )
