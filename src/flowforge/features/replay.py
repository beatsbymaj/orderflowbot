"""Run the feature engine over downloaded ticks (Phase 2 catalog + raw trade files).

Two replay paths, both offline and read-only:
- `replay_day`: catalog quotes and trades straight into the engine.
- `backtest_day`: the same ticks through a Nautilus BacktestEngine and the FeatureActor.

Usage:
    python -m flowforge.features.replay --ticks data/ticks --out data/features --symbols SPY,AAPL

Writes data/features/<SYMBOL>/<day>.jsonl.gz (one record per line) and summary.md.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader.persistence import ParquetDataCatalog

from flowforge.data.tape import split_conditions
from flowforge.data.ticks import NS, Trade, instrument_id, make_tick_equity, parse_ts_ns
from flowforge.features import conditions as cond
from flowforge.features.actor import ConditionsLookup, FeatureActor, quote_from_tick, trade_from_tick
from flowforge.features.config import FeatureConfig
from flowforge.features.context import PriorDay
from flowforge.features.engine import FeatureEngine

ET = ZoneInfo("America/New_York")
CHUNK_NS = 15 * 60 * NS
QUOTE_LEAD = timedelta(minutes=5)
QUOTE_NS = int(QUOTE_LEAD.total_seconds()) * NS


@dataclass(frozen=True)
class DayData:
    symbol: str
    day: date
    start: int  # first quote (09:25 ET)
    end: int  # session close
    gaps: list[tuple[int, int]]
    conditions: dict


def _et(day: date, at: time) -> int:
    return int(datetime.combine(day, at, ET).timestamp()) * NS


def raw_trades(ticks: Path, symbol: str, day: date) -> list[Trade]:
    """Every raw trade of the day (incl. canceled), backfill files replacing earlier copies."""
    folder = ticks / "raw" / symbol / day.isoformat()
    files = sorted(folder.glob("trades_*.csv.gz")) + sorted(folder.glob("backfill_*.csv.gz"))
    by_key: dict[tuple[str, int], Trade] = {}
    for path in files:
        with gzip.open(path, "rt", newline="") as fh:
            for row in csv.DictReader(fh):
                t = Trade(
                    ts=int(row["ts_ns"]),
                    price=float(row["price"]),
                    size=int(row["size"]),
                    exchange=row["exchange"],
                    conditions=split_conditions(row["conditions"]),
                    exchange_id=int(row["exchange_id"]),
                    tape=row["tape"],
                    update=row["update"],
                )
                by_key[t.key] = t
    return sorted(by_key.values(), key=lambda t: (t.ts, t.key))


def load_day(ticks: Path, symbol: str, day: date) -> DayData:
    open_ns, close_ns = _et(day, time(9, 30)), _et(day, time(16, 0))
    gaps: list[tuple[int, int]] = []
    report = ticks / "quality" / f"{symbol}_{day.isoformat()}.json"
    if report.exists():
        data = json.loads(report.read_text())
        open_ns = parse_ts_ns(_rfc(data["session"]["open"]))
        close_ns = parse_ts_ns(_rfc(data["session"]["close"]))
        gaps = [
            (parse_ts_ns(_rfc(g["start"])), parse_ts_ns(_rfc(g["end"])))
            for g in data.get("gaps", [])
            if g.get("kind") == "trades" and not g.get("filled")
        ]
    lookup = {
        t.trade_id: (t.exchange, t.conditions, t.exchange_id, t.tape) for t in raw_trades(ticks, symbol, day)
    }
    return DayData(symbol, day, open_ns - QUOTE_NS, close_ns, gaps, lookup)


def _rfc(text: str) -> str:
    """Quality reports hold ET times like 2026-10-23T09:30:00-04:00."""
    return text if text.endswith("Z") or text[-6] in "+-" else text + "Z"


def prior_day(ticks: Path, symbol: str, day: date) -> PriorDay | None:
    """High, low, close and volume of the last downloaded session before `day`."""
    folder = ticks / "raw" / symbol
    days = (
        sorted(d.name for d in folder.iterdir() if d.is_dir() and d.name < day.isoformat())
        if folder.exists()
        else []
    )
    if not days:
        return None
    trades = [t for t in raw_trades(ticks, symbol, date.fromisoformat(days[-1])) if not t.dropped]
    priced = [t for t in trades if cond.market_priced(t.conditions)]
    if not priced:
        return None
    official = [t for t in trades if "M" in t.conditions]  # official close print, if any
    close = official[-1].price if official else priced[-1].price
    volume = sum(t.size for t in trades if cond.counts_volume(t.conditions))
    return PriorDay(max(t.price for t in priced), min(t.price for t in priced), close, volume)


def iter_ticks(
    catalog: ParquetDataCatalog, d: DayData, minutes: int | None = None
) -> Iterator[tuple[list, list]]:
    """(quote ticks, trade ticks) per 15-minute chunk, so a whole day never sits in memory.
    `minutes` stops that many minutes after the open."""
    ident = [str(instrument_id(d.symbol))]
    end = d.end if minutes is None else min(d.end, d.start + QUOTE_NS + minutes * 60 * NS)
    for a in range(d.start, end, CHUNK_NS):
        b = min(a + CHUNK_NS, end) - 1
        yield catalog.query_quote_ticks(ident, a, b), catalog.query_trade_ticks(ident, a, b)


def merged(quotes: list, trades: list, lookup: ConditionsLookup) -> Iterator[tuple[str, object]]:
    """One chunk in timestamp order, trades first on ties, as plain records."""
    qs = (("q", q.ts_event, 1, i, quote_from_tick(q)) for i, q in enumerate(quotes))
    ts = (("t", t.ts_event, 0, i, trade_from_tick(t, lookup)[0]) for i, t in enumerate(trades))
    for kind, _ts, _rank, _i, rec in heapq.merge(qs, ts, key=lambda e: (e[1], e[2], e[3])):
        yield kind, rec


def _seed(engine: FeatureEngine, ticks: Path, d: DayData, config: FeatureConfig) -> None:
    prior = prior_day(ticks, d.symbol, d.day)
    if prior is not None:
        engine.seed_prior_day(d.symbol, d.day.isoformat(), prior)
    for a, b in d.gaps:
        engine.mark_gap(d.symbol, a, b)


def replay_day(
    ticks: Path,
    symbol: str,
    day: date,
    config: FeatureConfig,
    sink: Callable[[dict], None],
    minutes: int | None = None,
) -> dict:
    """Path A: catalog -> engine."""
    d = load_day(ticks, symbol, day)
    catalog = ParquetDataCatalog(str(ticks / "catalog"))
    engine = FeatureEngine(config, sink)
    _seed(engine, ticks, d, config)
    counts = {"trades": 0, "quotes": 0}
    for quotes, trades in iter_ticks(catalog, d, minutes):
        for kind, rec in merged(quotes, trades, d.conditions):
            if kind == "q":
                counts["quotes"] += 1
                engine.on_quote(symbol, rec)
            else:
                counts["trades"] += 1
                engine.on_trade(symbol, rec)
    engine.flush()
    return counts


def backtest_day(
    ticks: Path,
    symbol: str,
    day: date,
    config: FeatureConfig,
    sink: Callable[[dict], None],
    minutes: int | None = None,
) -> dict:
    """Path B: catalog -> Nautilus BacktestEngine -> FeatureActor. No venue orders exist."""
    from nautilus_trader.backtest import BacktestEngine, BacktestEngineConfig
    from nautilus_trader.common import LoggerConfig, LogLevel
    from nautilus_trader.execution import FixedFeeModel
    from nautilus_trader.model import AccountType, Money, OmsType, TraderId, Venue

    from flowforge.data.ticks import VENUE

    d = load_day(ticks, symbol, day)
    catalog = ParquetDataCatalog(str(ticks / "catalog"))
    engine = BacktestEngine(
        BacktestEngineConfig(
            trader_id=TraderId("FLOWFORGE-FEATURES"), logging=LoggerConfig(stdout_level=LogLevel.WARNING)
        )
    )
    # The venue only exists because Nautilus needs one; no strategy is added, so no orders.
    zero = Money.from_str("0 USD")
    engine.add_venue(
        Venue(VENUE),
        OmsType.NETTING,
        AccountType.CASH,
        [Money.from_str("1 USD")],
        fee_model=FixedFeeModel(zero),
    )
    engine.add_instrument(make_tick_equity(symbol))
    actor = FeatureActor([symbol], config, conditions=d.conditions, on_record=sink)
    _seed(actor.engine, ticks, d, config)
    engine.add_actor(actor)
    for quotes, trades in iter_ticks(catalog, d, minutes):
        if not quotes and not trades:
            continue
        engine.add_data(quotes + trades)
        engine.run(streaming=True)
        engine.clear_data()
    engine.end()
    engine.dispose()
    return dict(actor.received)


def trading_days(ticks: Path, symbol: str) -> list[date]:
    folder = ticks / "raw" / symbol
    return (
        sorted(date.fromisoformat(p.name) for p in folder.iterdir() if p.is_dir()) if folder.exists() else []
    )


# -- report ----------------------------------------------------------------------------
def day_summary(records: list[dict]) -> dict:
    stats = next((r for r in records if r["type"] == "day_stats"), {})
    bars5 = [r for r in records if r["type"] == "bar" and r["tf"] == 300 and r["trades"]]
    vpins = [r["vpin"] for r in bars5 if r["vpin"] is not None]
    priced = stats.get("method_quote", 0) + stats.get("method_tick", 0) + stats.get("method_none", 0)
    return {
        "trades": stats.get("trades", 0),
        "quote_rule": _share(stats.get("method_quote", 0), priced),
        "tick_test": _share(stats.get("method_tick", 0), priced),
        "no_side": _share(stats.get("method_none", 0), priced),
        "nbbo_locked_or_crossed_at_trade": _share(
            stats.get("nbbo_locked", 0) + stats.get("nbbo_crossed", 0), priced
        ),
        "large_prints": stats.get("large_prints", 0),
        "sweeps": stats.get("sweeps", 0),
        "absorption_5m": sum(1 for r in bars5 if r["absorption"]),
        "stacked_5m": sum(1 for r in bars5 if r["stacked_buy"] or r["stacked_sell"]),
        "off_exchange_share": _share(
            sum(r["off_exchange_share"] * r["volume"] for r in bars5), sum(r["volume"] for r in bars5)
        ),
        "vpin_mean": round(sum(vpins) / len(vpins), 4) if vpins else None,
        "unknown_conditions": stats.get("unknown_conditions", {}),
        "late_events": stats.get("late_events_total", 0),
        "gap_bars_5m": sum(1 for r in bars5 if r["gap"]),
    }


def _share(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def write_summary(out: Path, rows: list[dict]) -> Path:
    lines = [
        "# Feature replay summary",
        "",
        "Made by `python -m flowforge.features.replay`. Shares are of trades that add volume.",
        "",
        "| Symbol | Day | Trades | Quote rule | Tick test | No side | NBBO locked/crossed | Large prints"
        " | Sweeps | 5m absorption bars | 5m stacked bars | Off-exchange | VPIN mean | Late |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    cols = ("quote_rule", "tick_test", "no_side", "nbbo_locked_or_crossed_at_trade", "large_prints", "sweeps")
    cols += ("absorption_5m", "stacked_5m", "off_exchange_share", "vpin_mean", "late_events")
    for r in rows:
        cells = [r["symbol"], r["day"], f"{r['trades']:,}"] + [str(r[c]) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    unknown = {k for r in rows for k in r["unknown_conditions"]}
    lines += ["", f"Unknown condition codes seen: {', '.join(sorted(unknown)) or 'none'}."]
    path = out / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    (out / "summary.json").write_text(json.dumps(rows, indent=2))
    return path


def run(
    ticks: Path, out: Path, symbols: list[str], days: list[date] | None, config: FeatureConfig
) -> list[dict]:
    rows = []
    for symbol in symbols:
        for day in days or trading_days(ticks, symbol):
            records: list[dict] = []
            replay_day(ticks, symbol, day, config, records.append)
            path = out / symbol / f"{day.isoformat()}.jsonl.gz"
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r) + "\n")
            row = {"symbol": symbol, "day": day.isoformat()} | day_summary(records)
            rows.append(row)
            print(
                f"{symbol} {day}: {row['trades']:,} trades, {row['large_prints']} large prints, "
                f"{row['sweeps']} sweeps -> {path}",
                flush=True,
            )
    write_summary(out, rows)
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticks", type=Path, default=Path("data/ticks"))
    p.add_argument("--out", type=Path, default=Path("data/features"))
    p.add_argument("--symbols", default="SPY,QQQ,AAPL,MSFT,NVDA")
    p.add_argument("--days", default="all", help="'all' or comma-separated YYYY-MM-DD")
    a = p.parse_args(argv)
    days = None if a.days == "all" else [date.fromisoformat(x) for x in a.days.split(",")]
    a.out.mkdir(parents=True, exist_ok=True)
    rows = run(a.ticks, a.out, a.symbols.split(","), days, FeatureConfig())
    print(f"{len(rows)} symbol-days. Summary: {a.out / 'summary.md'}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
