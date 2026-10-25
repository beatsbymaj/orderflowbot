"""Download past trades and NBBO quotes from Alpaca into the Nautilus Parquet catalog.

Read-only: only the market data API is called, never the trading API. Free keys work
because every session asked for is over 15 minutes old (Alpaca market-data FAQ).
Sources (checked 2026-10-25) are listed in docs/research/10-alpaca-tick-history.md.

For each symbol and trading day, in 15-minute windows of regular hours:
1. download trades, compare each minute with Alpaca's own minute bar (quality.py),
2. re-download any minute that comes up short (backfill) and check again,
3. download quotes (from 5 minutes before the open) and look for long silences,
4. write trades and quotes to the catalog, raw trades (with exchange and conditions) to
   gzip CSV, and a quality report per symbol-day that marks every gap still left.

Usage:
    python -m flowforge.data.alpaca_ticks --symbols SPY,QQQ --days 5

Keys come from ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY, sent only as headers.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader.model import NautilusDataType
from nautilus_trader.persistence import ParquetDataCatalog

from flowforge.data import quality
from flowforge.data.alpaca_bars import AlpacaError, AlpacaKeys, Fetch, _http_get_json, iter_bars
from flowforge.data.ticks import (
    NS,
    Quote,
    Trade,
    format_ts_ns,
    instrument_id,
    make_tick_equity,
    parse_ts_ns,
    quote_size_multiplier,
    to_quote_tick,
    to_trade_tick,
)

DATA_URL = "https://data.alpaca.markets/v2/stocks"
PAGE_LIMIT = 10_000
ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)
EARLY_CLOSE = dtime(13, 0)
REFERENCE_SYMBOL = "SPY"  # its minute bars tell us which days the market was open
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 5
RAW_TRADE_HEADER = ["ts_ns", "price", "size", "exchange", "conditions", "exchange_id", "tape", "update"]


class Throttle:
    """Wrap a fetch so calls stay under a per-minute limit and transient errors are retried."""

    def __init__(
        self,
        fetch: Fetch,
        calls_per_minute: int,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._interval = 60.0 / calls_per_minute
        self._sleep = sleep
        self._clock = clock
        self._next = 0.0
        self.calls = 0

    def __call__(self, url: str, headers: dict[str, str]) -> dict:
        for attempt in range(MAX_RETRIES + 1):
            wait = self._next - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._next = self._clock() + self._interval
            self.calls += 1
            try:
                return self._fetch(url, headers)
            except AlpacaError as exc:
                if exc.status not in RETRY_STATUSES or attempt == MAX_RETRIES:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt == MAX_RETRIES:
                    raise AlpacaError(f"network error after {MAX_RETRIES} retries: {exc}") from None
            self._sleep(2.0 ** (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover


def _rfc3339(ts: int) -> str:
    return format_ts_ns(ts).replace(".000000000Z", "Z")


def iter_ticks(
    kind: str, symbol: str, start: int, end: int, keys: AlpacaKeys, feed: str, fetch: Fetch
) -> Iterator[dict]:
    """Yield raw trade or quote dicts with start <= t < end, oldest first, across pages."""
    headers = {"APCA-API-KEY-ID": keys.key_id, "APCA-API-SECRET-KEY": keys.secret}
    params = {
        "symbols": symbol,
        "start": _rfc3339(start),
        "end": _rfc3339(end),  # Alpaca's end is inclusive; ticks at `end` are dropped below
        "limit": str(PAGE_LIMIT),
        "feed": feed,
        "sort": "asc",
    }
    page_token = None
    while True:
        query = dict(params, page_token=page_token) if page_token else params
        payload = fetch(f"{DATA_URL}/{kind}?{urllib.parse.urlencode(query)}", headers)
        if kind not in payload:
            raise AlpacaError(f"unexpected {kind} response for {symbol}: keys {sorted(payload)}")
        for raw in (payload[kind] or {}).get(symbol, []):
            if parse_ts_ns(raw["t"]) < end:
                yield raw
        page_token = payload.get("next_page_token")
        if not page_token:
            return


@dataclass(frozen=True)
class Session:
    day: date
    open: int  # ns
    close: int  # ns

    @property
    def early_close(self) -> bool:
        return datetime.fromtimestamp(self.close / NS, ET).time() == EARLY_CLOSE


def _et_ns(day: date, at: dtime) -> int:
    return int(datetime.combine(day, at, tzinfo=ET).timestamp()) * NS


def minute_bars(symbol: str, day: date, keys: AlpacaKeys, feed: str, fetch: Fetch) -> list[quality.MinuteBar]:
    """Unadjusted minute bars for one New York day (trades are never split-adjusted)."""
    bars = iter_bars(symbol, day, day + timedelta(days=1), keys, feed, fetch, lambda _s: None, "raw")
    return [quality.MinuteBar(parse_ts_ns(b["t"]), int(b.get("n", 0)), int(b["v"])) for b in bars]


def session_from_bars(day: date, bars: list[quality.MinuteBar]) -> Session | None:
    """Regular-hours session for a day, or None when the market was closed."""
    open_ns, close_ns = _et_ns(day, RTH_OPEN), _et_ns(day, RTH_CLOSE)
    rth = [b.start for b in bars if open_ns <= b.start < close_ns]
    if not rth:
        return None
    last = datetime.fromtimestamp(max(rth) / NS, ET).time()
    close = EARLY_CLOSE if last < dtime(13, 30) else RTH_CLOSE
    return Session(day, open_ns, _et_ns(day, close))


def find_sessions(
    keys: AlpacaKeys,
    feed: str,
    fetch: Fetch,
    days: int | None = None,
    before: date | None = None,
    start: date | None = None,
    end: date | None = None,
) -> list[Session]:
    """The last `days` sessions before `before`, or every session in [start, end]."""
    if start is not None and end is not None:
        candidates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        wanted = None
    else:
        before = before or datetime.now(ET).date()
        candidates = [before - timedelta(days=i) for i in range(1, 31)]
        wanted = days or 5
    sessions = []
    for day in candidates:
        if day.weekday() >= 5:
            continue
        session = session_from_bars(day, minute_bars(REFERENCE_SYMBOL, day, keys, feed, fetch))
        if session:
            sessions.append(session)
            if wanted and len(sessions) == wanted:
                break
    return sorted(sessions, key=lambda s: s.day)


@dataclass
class Settings:
    keys: AlpacaKeys
    out: Path
    feed: str = "sip"
    fetch: Fetch = _http_get_json
    window_minutes: int = 15
    quote_lead_minutes: int = 5
    max_quote_silence_seconds: int = 30
    force: bool = False
    catalog: ParquetDataCatalog | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.catalog is None:
            (self.out / "catalog").mkdir(parents=True, exist_ok=True)
            self.catalog = ParquetDataCatalog(str(self.out / "catalog"))

    def report_path(self, symbol: str, day: date) -> Path:
        return self.out / "quality" / f"{symbol}_{day.isoformat()}.json"

    def raw_path(self, symbol: str, day: date, window_start: int) -> Path:
        hhmm = datetime.fromtimestamp(window_start / NS, ET).strftime("%H%M")
        return self.out / "raw" / symbol / day.isoformat() / f"trades_{hhmm}.csv.gz"


def _windows(start: int, end: int, minutes: int) -> list[tuple[int, int]]:
    step = minutes * 60 * NS
    return [(a, min(a + step, end)) for a in range(start, end, step)]


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts / NS, UTC).astimezone(ET).isoformat(timespec="seconds")


def _fetch_trades(s: Settings, symbol: str, start: int, end: int) -> list[Trade]:
    return [Trade.from_alpaca(r) for r in iter_ticks("trades", symbol, start, end, s.keys, s.feed, s.fetch)]


def _fetch_quotes(s: Settings, symbol: str, start: int, end: int, multiplier: int) -> list[Quote]:
    raws = iter_ticks("quotes", symbol, start, end, s.keys, s.feed, s.fetch)
    return [Quote.from_alpaca(r, multiplier) for r in raws]


def _write_raw_trades(path: Path, trades: list[Trade]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(RAW_TRADE_HEADER)
        for t in trades:
            writer.writerow(
                [
                    t.ts,
                    repr(t.price),
                    t.size,
                    t.exchange,
                    "|".join(t.conditions),
                    t.exchange_id,
                    t.tape,
                    t.update,
                ]
            )


def ingest_trades_window(
    s: Settings, symbol: str, start: int, end: int, bars: list[quality.MinuteBar]
) -> tuple[list[Trade], quality.BarComparison, list[dict]]:
    """Download one window, backfill short minutes once, and return what is still missing."""
    trades = _fetch_trades(s, symbol, start, end)
    check = quality.compare_with_bars(trades, bars, start, end)
    gaps = []
    if check.gap_minutes:
        ranges = quality.merge_minutes(check.gap_minutes)
        refetched = [_fetch_trades(s, symbol, a, b) for a, b in ranges]
        trades = quality.merge_trades(trades, *refetched)
        after = quality.compare_with_bars(trades, bars, start, end)
        still = set(after.gap_minutes)
        for a, b in ranges:
            gaps.append(
                {
                    "kind": "trades",
                    "start": _iso(a),
                    "end": _iso(b),
                    "backfilled": True,
                    "filled": not any(a <= m < b for m in still),
                }
            )
        check = after
    return trades, check, gaps


def ingest_quotes_window(
    s: Settings, symbol: str, start: int, end: int, multiplier: int, check_from: int
) -> tuple[list[Quote], dict, list[dict]]:
    """Download quotes in [start, end); silences only count from check_from (the open)."""
    max_gap = s.max_quote_silence_seconds * NS
    quotes = _fetch_quotes(s, symbol, start, end, multiplier)
    checks = quality.quote_checks(quotes, check_from, end, max_gap)
    gaps = []
    if checks["silences"]:
        again = _fetch_quotes(s, symbol, start, end, multiplier)
        again_checks = quality.quote_checks(again, check_from, end, max_gap)
        if len(again) > len(quotes):
            quotes, checks = again, again_checks
        gaps = [
            {"kind": "quotes", "start": _iso(a), "end": _iso(b), "backfilled": True, "filled": False}
            for a, b in checks["silences"]
        ]
    checks["silences"] = len(checks["silences"])
    return quotes, checks, gaps


def ingest_day(s: Settings, symbol: str, session: Session) -> dict:
    """Download, check and store one symbol-day. Returns its quality report."""
    iid = instrument_id(symbol)
    catalog = s.catalog
    quote_start = session.open - s.quote_lead_minutes * 60 * NS
    # A rerun replaces the day instead of adding a second copy.
    catalog.delete_data_range(NautilusDataType.TradeTick, str(iid), session.open, session.close - 1)
    catalog.delete_data_range(NautilusDataType.QuoteTick, str(iid), quote_start, session.close - 1)
    catalog.write_instruments([make_tick_equity(symbol)])

    bars = minute_bars(symbol, session.day, s.keys, s.feed, s.fetch)
    multiplier = quote_size_multiplier(session.day)
    trade_stats: dict = {}
    quote_stats: dict = {}
    gaps: list[dict] = []
    totals = quality.BarComparison()
    for a, b in _windows(session.open, session.close, s.window_minutes):
        trades, check, trade_gaps = ingest_trades_window(s, symbol, a, b, bars)
        gaps += trade_gaps
        trade_stats = quality.add_counts(trade_stats, quality.trade_checks(trades))
        for name in ("minutes_checked", "minutes_matched", "missing_trades", "tick_volume", "bar_volume"):
            setattr(totals, name, getattr(totals, name) + getattr(check, name))
        totals.gap_minutes += check.gap_minutes
        totals.extra_minutes += check.extra_minutes
        _write_raw_trades(s.raw_path(symbol, session.day, a), trades)
        kept = [to_trade_tick(t, iid) for t in trades if not t.dropped]
        if kept:
            catalog.write_trade_ticks(kept, start=a, end=b - 1)

        qa = quote_start if a == session.open else a
        quotes, q_checks, quote_gaps = ingest_quotes_window(s, symbol, qa, b, multiplier, a)
        gaps += quote_gaps
        quote_stats = quality.add_counts(quote_stats, q_checks)
        if quotes:
            catalog.write_quote_ticks([to_quote_tick(q, iid) for q in quotes], start=qa, end=b - 1)

    unfilled_trade_gaps = [g for g in gaps if g["kind"] == "trades" and not g["filled"]]
    report = {
        "symbol": symbol,
        "day": session.day.isoformat(),
        "session": {
            "open": _iso(session.open),
            "close": _iso(session.close),
            "early_close": session.early_close,
        },
        "feed": s.feed,
        "status": "complete" if not unfilled_trade_gaps else "gaps",
        "trades": trade_stats,
        "quotes": quote_stats,
        "quote_size_unit": "shares" if multiplier == 1 else f"round lots x{multiplier}",
        "bar_check": {
            "minutes_checked": totals.minutes_checked,
            "minutes_matched": totals.minutes_matched,
            "short_minutes_after_backfill": len(totals.gap_minutes),
            "minutes_with_extra_trades": len(totals.extra_minutes),
            "missing_trades_after_backfill": totals.missing_trades,
            "tick_volume": totals.tick_volume,
            "bar_volume": totals.bar_volume,
        },
        "gaps": gaps,
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = s.report_path(symbol, session.day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    return report


def describe(report: dict) -> str:
    """One plain-words line per symbol-day."""
    bc = report["bar_check"]
    gaps = report["gaps"]
    fixed = sum(1 for g in gaps if g["filled"])
    text = (
        f"{report['symbol']} {report['day']}: {report['trades'].get('count', 0):,} trades, "
        f"{report['quotes'].get('count', 0):,} quotes. "
        f"Matched Alpaca's minute bars in {bc['minutes_matched']} of {bc['minutes_checked']} minutes. "
    )
    if not gaps:
        return text + "No gaps."
    return (
        text + f"{len(gaps)} gap(s) found and re-downloaded, {fixed} fixed, {len(gaps) - fixed} still marked."
    )


def write_summary(out: Path, reports: list[dict]) -> Path:
    lines = [
        "# Tick download summary",
        "",
        f"Made {datetime.now(UTC).isoformat(timespec='seconds')}. One line per symbol and day; "
        "details in the JSON file next to this one.",
        "",
    ]
    lines += [f"- {describe(r)}" for r in reports]
    path = out / "quality" / "summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return path


def download(s: Settings, symbols: list[str], sessions: list[Session]) -> list[dict]:
    reports = []
    for session in sessions:
        for symbol in symbols:
            path = s.report_path(symbol, session.day)
            if path.exists() and not s.force:
                report = json.loads(path.read_text())
                print(f"{symbol} {session.day}: already downloaded, skipping (use --force to redo)")
            else:
                print(f"{symbol} {session.day}: downloading...", flush=True)
                report = ingest_day(s, symbol, session)
                print("  " + describe(report), flush=True)
            reports.append(report)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", default="SPY,QQQ,AAPL,MSFT,NVDA")
    parser.add_argument("--days", type=int, default=5, help="last N trading days (default 5)")
    parser.add_argument("--start", type=date.fromisoformat, help="first day (use with --end)")
    parser.add_argument("--end", type=date.fromisoformat, help="last day (use with --start)")
    parser.add_argument("--feed", choices=["sip", "iex"], default="sip")
    parser.add_argument("--out", type=Path, default=Path("data/ticks"))
    parser.add_argument(
        "--calls-per-minute", type=int, default=180, help="free plan allows 200, Algo Trader Plus 10,000"
    )
    parser.add_argument("--force", action="store_true", help="download days again even if done")
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end go together")
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    try:
        keys = AlpacaKeys.from_env()
        fetch = Throttle(_http_get_json, args.calls_per_minute)
        sessions = find_sessions(keys, args.feed, fetch, args.days, start=args.start, end=args.end)
        if not sessions:
            raise AlpacaError("no trading days found in that range")
        print(f"Trading days: {', '.join(x.day.isoformat() for x in sessions)}")
        settings = Settings(keys, args.out, args.feed, fetch, force=args.force)
        reports = download(settings, symbols, sessions)
    except AlpacaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    summary = write_summary(args.out, reports)
    bad = [r for r in reports if r["status"] != "complete"]
    print(f"Done: {len(reports)} symbol-days, {fetch.calls} API calls. Summary: {summary}")
    if bad:
        print(f"WARNING: {len(bad)} symbol-day(s) still have trade gaps; see the summary.")
    return 0 if not bad else 2


if __name__ == "__main__":
    sys.exit(main())
