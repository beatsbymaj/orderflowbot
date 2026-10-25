"""Download historical 1-minute stock bars from Alpaca's market data API.

Read-only: this module only calls the market data endpoint, never the trading API.
Sources (checked 2026-10-25, docs/research/09-backtest-data-and-costs.md):
- https://docs.alpaca.markets/docs/about-market-data-api (free Basic plan: IEX feed,
  history since 2016, 200 historical calls/min)
- https://docs.alpaca.markets/docs/historical-stock-data-1 (feed parameter: iex, sip, ...)
- https://docs.alpaca.markets/us/docs/market-data-faq ("the `end` parameter must be at least
  15 minutes old to query SIP data without a subscription"), so free keys get full-market
  (SIP) history. Use --feed iex if a key is refused.

Usage:
    python -m flowforge.data.alpaca_bars --symbols SPY,QQQ --start 2024-10-01 --end 2026-10-01

Keys come from the environment: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY. They are
sent only as request headers and are never printed or written to disk.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
PAGE_LIMIT = 10_000
# Free plan allows 200 historical calls per minute; stay well under it.
MIN_SECONDS_BETWEEN_CALLS = 0.4
CSV_HEADER = ["ts_utc", "open", "high", "low", "close", "volume"]


class AlpacaError(RuntimeError):
    """Raised when the data API refuses a request or returns something unexpected."""


@dataclass(frozen=True)
class AlpacaKeys:
    key_id: str
    secret: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> AlpacaKeys:
        env = os.environ if env is None else env
        key_id = env.get("ALPACA_API_KEY_ID", "").strip()
        secret = env.get("ALPACA_API_SECRET_KEY", "").strip()
        if not key_id or not secret:
            raise AlpacaError("set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env")
        return cls(key_id, secret)

    def __repr__(self) -> str:  # never show the secret in logs or tracebacks
        return "AlpacaKeys(key_id=***, secret=***)"


Fetch = Callable[[str, dict[str, str]], dict]


def _http_get_json(url: str, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 (fixed https URL)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:300]
        raise AlpacaError(f"Alpaca data API returned HTTP {exc.code}: {body}") from None


def iter_bars(
    symbol: str,
    start: date,
    end: date,
    keys: AlpacaKeys,
    feed: str = "sip",
    fetch: Fetch = _http_get_json,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[dict]:
    """Yield raw Alpaca bar dicts for one symbol, oldest first, following page tokens."""
    headers = {"APCA-API-KEY-ID": keys.key_id, "APCA-API-SECRET-KEY": keys.secret}
    params = {
        "symbols": symbol,
        "timeframe": "1Min",
        "start": f"{start.isoformat()}T00:00:00Z",
        "end": f"{end.isoformat()}T00:00:00Z",
        "limit": str(PAGE_LIMIT),
        "adjustment": "split",
        "feed": feed,
        "sort": "asc",
    }
    page_token = None
    while True:
        query = dict(params, page_token=page_token) if page_token else params
        payload = fetch(f"{BARS_URL}?{urllib.parse.urlencode(query)}", headers)
        if "bars" not in payload:
            raise AlpacaError(f"unexpected response for {symbol}: keys {sorted(payload)}")
        yield from (payload["bars"] or {}).get(symbol, [])
        page_token = payload.get("next_page_token")
        if not page_token:
            return
        sleep(MIN_SECONDS_BETWEEN_CALLS)


def bars_csv_path(out_dir: Path, symbol: str, feed: str) -> Path:
    return out_dir / f"{symbol}_1min_{feed}.csv"


def download(
    symbols: list[str],
    start: date,
    end: date,
    out_dir: Path,
    keys: AlpacaKeys,
    feed: str = "sip",
    fetch: Fetch = _http_get_json,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Write one CSV per symbol plus a manifest. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for symbol in symbols:
        path = bars_csv_path(out_dir, symbol, feed)
        rows = 0
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            for bar in iter_bars(symbol, start, end, keys, feed, fetch, sleep):
                writer.writerow([bar["t"], bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]])
                rows += 1
        if rows == 0:
            raise AlpacaError(f"no bars returned for {symbol} between {start} and {end}")
        files[symbol] = {"path": path.name, "rows": rows}
        print(f"{symbol}: {rows} bars -> {path}")
    manifest = {
        "source": BARS_URL,
        "feed": feed,
        "timeframe": "1Min",
        "adjustment": "split",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": files,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", default="SPY,QQQ,AAPL,MSFT,NVDA")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2024, 10, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 1))
    parser.add_argument("--feed", choices=["sip", "iex"], default="sip")
    parser.add_argument("--out", type=Path, default=Path("data/bars"))
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    try:
        download(symbols, args.start, args.end, args.out, AlpacaKeys.from_env(), args.feed)
    except AlpacaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
