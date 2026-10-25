import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from flowforge.backtest import run
from flowforge.backtest.bars import ET, MinuteBar, regular_hours, session_closes
from flowforge.backtest.synthetic import make_bars, write_csv
from flowforge.config.risk_limits import load_risk_limits

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"
SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]


@pytest.fixture(scope="module")
def result():
    bars = []
    for i, s in enumerate(SYMBOLS):
        bars += make_bars(s, date(2025, 1, 6), 40, price=60 + 40 * i)
    return run.run_backtest(bars, SYMBOLS, load_risk_limits(REPO_CONFIG), 10_000.0)


def test_backtest_produces_trades_and_respects_limits(result):
    summary, rec = result
    assert summary["all"]["trades"] > 5
    assert summary["errors"] == 0
    assert summary["run"]["risk_limits_sha256"] == load_risk_limits(REPO_CONFIG).sha256
    trades = rec.trades
    # Every position opens and closes on the same day, inside regular hours.
    for t in trades:
        entry, exit_ = datetime.fromisoformat(t["entry_time"]), datetime.fromisoformat(t["exit_time"])
        assert entry.date() == exit_.date() and exit_.time() <= datetime.strptime("15:45", "%H:%M").time()
        assert t["quantity"] * t["entry_price"] <= 10_000 * 0.10 * 1.1  # notional cap, with gains
    # Legacy PDT under $25k: never more than 3 day trades in any 5 trading days.
    days = sorted({t["entry_time"][:10] for t in trades} | {d for d, _ in _days(rec)})
    per_day = Counter(t["entry_time"][:10] for t in trades)
    for i in range(len(days)):
        assert sum(per_day[d] for d in days[max(0, i - 4) : i + 1]) <= 3
    # Never more than three positions at once.
    events = sorted([(t["entry_time"], 1) for t in trades] + [(t["exit_time"], -1) for t in trades])
    open_now = 0
    for _, step in events:
        open_now += step
        assert open_now <= 3
    assert summary["signals_blocked"].get("pattern day trader limit", 0) > 0


def _days(rec):
    return {(when.date().isoformat(), None) for when, _ in rec.equity}


def test_journal_logs_signals_orders_and_fills(result):
    _, rec = result
    kinds = Counter(j["event"] for j in rec.journal)
    assert kinds["signal"] >= kinds["order"] > 0
    assert kinds["fill"] == 2 * len(rec.trades)
    assert kinds["position_closed"] == len(rec.trades)


def test_main_writes_reports(tmp_path, capsys):
    data = tmp_path / "bars"
    data.mkdir()
    for s in ["SPY", "QQQ"]:
        write_csv(make_bars(s, date(2025, 1, 6), 6), data / f"{s}_1min_iex.csv")
    (data / "manifest.json").write_text(json.dumps({"feed": "iex", "downloaded_at": "2025-01-20T00:00:00Z"}))
    out = tmp_path / "out"
    code = run.main(
        ["--data", str(data), "--symbols", "SPY,QQQ", "--limits", str(REPO_CONFIG), "--out", str(out)]
    )
    assert code == 0
    for name in ["summary.json", "trades.csv", "journal.jsonl", "report.html"]:
        assert (out / name).exists()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["run"]["feed"] == "iex" and summary["benchmark"]["symbol"] == "SPY"
    html = (out / "report.html").read_text()
    assert "No orders were sent anywhere" in html and "too few to trust" in html
    assert "Full report" in capsys.readouterr().out


def test_main_fails_without_data(tmp_path, capsys):
    assert run.main(["--data", str(tmp_path), "--limits", str(REPO_CONFIG)]) == 1
    assert "Download the data first" in capsys.readouterr().err


def test_refuses_limits_that_allow_shorts(tmp_path):
    text = REPO_CONFIG.read_text().replace("long_only: true", "long_only: false")
    path = tmp_path / "limits.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match="long only"):
        run.run_backtest([], ["SPY"], load_risk_limits(path), 10_000.0)


def test_benchmark_without_bars():
    assert run.benchmark([], "SPY", 10_000.0) is None


def test_regular_hours_and_half_days():
    def bar(local):
        return MinuteBar("SPY", local, 1, 1, 1, 1, 1)

    full = datetime(2025, 7, 2, 15, 59, tzinfo=ET)
    half = datetime(2025, 7, 3, 12, 59, tzinfo=ET)
    pre = datetime(2025, 7, 3, 9, 29, tzinfo=ET)
    kept = regular_hours([bar(half), bar(pre), bar(full)])
    assert [b.start for b in kept] == [full, half]
    closes = session_closes(kept)
    assert closes[date(2025, 7, 2)].hour == 16 and closes[date(2025, 7, 3)].hour == 13
    assert kept[0].end == full + timedelta(minutes=1)


def _crafted_day(day, path):
    """Bars for one day: flat 100.00 opening range, then closes following `path`."""
    start = datetime.combine(day, datetime.strptime("09:30", "%H:%M").time(), tzinfo=ET)
    closes = [100.0] * 15 + path
    closes += [closes[-1]] * (390 - len(closes))
    bars, prev = [], 100.0
    for i, c in enumerate(closes):
        hi, lo = max(prev, c) + (0.05 if i < 15 else 0.0), min(prev, c) - (0.05 if i < 15 else 0.0)
        bars.append(MinuteBar("SPY", start + timedelta(minutes=i), prev, hi, lo, c, 10_000))
        prev = c
    return bars


def _limits_file(tmp_path, **replacements):
    text = REPO_CONFIG.read_text().replace("pdt_regime: legacy_pdt", "pdt_regime: intraday_margin")
    for old, new in replacements.items():
        text = text.replace(old, new)
    path = tmp_path / "limits.yaml"
    path.write_text(text)
    return load_risk_limits(path)


def test_daily_loss_limit_flattens_before_the_stop(tmp_path):
    limits = _limits_file(tmp_path, **{"max_daily_loss_pct: 2 ": "max_daily_loss_pct: 0.05 "})
    path = [100.5, 100.3, 100.1, 99.98, 99.96, 99.95, 99.94]
    summary, rec = run.run_backtest(_crafted_day(date(2025, 3, 3), path), ["SPY"], limits, 10_000.0)
    assert summary["risk_halts"] == {"daily": 1}
    assert [t["exit_reason"] for t in rec.trades] == ["daily loss limit"]
    assert any(j["event"] == "flatten" and j["reason"] == "daily loss limit" for j in rec.journal)


def test_target_and_end_of_day_exits(tmp_path):
    limits = _limits_file(tmp_path)
    up = _crafted_day(date(2025, 3, 3), [100.5, 101.0, 101.5, 102.0, 102.5, 103.0])
    drift = _crafted_day(date(2025, 3, 4), [100.5, 100.6])
    summary, rec = run.run_backtest(up + drift, ["SPY"], limits, 10_000.0)
    assert [t["exit_reason"] for t in rec.trades] == ["target", "end of day"]
    assert rec.trades[1]["exit_time"][11:16] == "15:45"


def test_denied_entry_frees_the_slot(tmp_path):
    limits = _limits_file(tmp_path)
    strategy_rec = run.OrbRecorder()
    from flowforge.backtest.limits import LimitBook
    from flowforge.backtest.orb import OpeningRangeBreakout, OrbParams

    book = LimitBook(limits.limits, 10_000.0)
    strat = OpeningRangeBreakout(["SPY"], OrbParams(), book, {}, strategy_rec)
    book.start_day(date(2025, 3, 3))
    book.record_entry("SPY", 1_000.0)

    class Event:
        class instrument_id:  # noqa: N801
            class symbol:  # noqa: N801
                value = "SPY"

        ts_event = 1_741_012_200_000_000_000
        reason = "test denial"

    strat.on_order_denied(Event)
    strat.on_order_rejected(Event)
    assert book.open_notional == {} and book.trades_today == 1
    assert [j["event"] for j in strategy_rec.journal] == ["order_denied", "order_rejected"]
