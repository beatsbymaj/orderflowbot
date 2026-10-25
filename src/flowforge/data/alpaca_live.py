"""Watch Alpaca's live stream through a Nautilus node for a while and report what arrived.

Read-only: one data client (Alpaca) and one counting actor. No execution client, no
strategies, so nothing can place an order.

Usage:
    python -m flowforge.data.alpaca_live --feed test --symbols FAKEPACA --seconds 60
    python -m flowforge.data.alpaca_live --feed iex --symbols SPY,AAPL --seconds 300

Feeds: "test" (always on, fake data, free), "iex" (free keys, IEX only, market hours),
"sip" (needs Algo Trader Plus). Writes reports/live-<time>.json and data/live/journal.jsonl.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nautilus_trader.common import DataActor, Environment
from nautilus_trader.live import LiveNode
from nautilus_trader.model import TraderId

from flowforge.data.alpaca_bars import AlpacaError, AlpacaKeys
from flowforge.data.alpaca_data_client import AlpacaDataClientConfig, AlpacaDataClientFactory
from flowforge.data.ticks import instrument_id

CLIENT_NAME = "ALPACA"


class TapeCounter(DataActor):
    """Subscribes to trades, quotes and statuses and counts them per symbol."""

    def __init__(self, symbols: list[str]) -> None:
        super().__init__()
        self.symbols = symbols
        self.counts: dict[str, dict[str, int]] = {
            s: {"trades": 0, "quotes": 0, "statuses": 0} for s in symbols
        }
        self.last: dict[str, str] = {}

    def on_start(self) -> None:
        for symbol in self.symbols:
            iid = instrument_id(symbol)
            self.subscribe_trades(iid)
            self.subscribe_quotes(iid)
            self.subscribe_instrument_status(iid)

    def on_trade(self, trade: Any) -> None:
        self.counts[trade.instrument_id.symbol.value]["trades"] += 1
        self.last[trade.instrument_id.symbol.value] = str(trade)

    def on_quote(self, quote: Any) -> None:
        self.counts[quote.instrument_id.symbol.value]["quotes"] += 1

    def on_instrument_status(self, status: Any) -> None:
        self.counts[status.instrument_id.symbol.value]["statuses"] += 1


def build_node(config: AlpacaDataClientConfig, factory: Any = None) -> LiveNode:
    return (
        LiveNode.builder("alpaca-live", TraderId("FLOWFORGE-DATA"), Environment.LIVE)
        .with_reconciliation(False)
        .with_delay_post_stop_secs(0)
        .with_timeout_disconnection_secs(5)
        .add_data_client(CLIENT_NAME, factory or AlpacaDataClientFactory(), config)
        .build()
    )


async def watch(node: LiveNode, seconds: float) -> None:
    task = asyncio.create_task(node.run_async())
    try:
        await asyncio.sleep(seconds)
    finally:
        node.handle().stop()
        await task


def run(symbols: list[str], seconds: float, config: AlpacaDataClientConfig, factory: Any = None) -> dict:
    node = build_node(config, factory)
    counter = TapeCounter(symbols)
    node.add_actor(counter)
    started = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        asyncio.run(watch(node, seconds))
    finally:
        node.dispose()
    return {
        "started": started,
        "feed": config.feed,
        "seconds": seconds,
        "per_symbol": counter.counts,
        "last_trade": counter.last,
    }


def journal_summary(path: Path) -> dict:
    """Totals from the journal's last stop line plus every outage and fatal error."""
    out: dict = {"outages": [], "fatal": None, "stop": None}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["kind"] == "outage":
            out["outages"].append(event)
        elif event["kind"] == "fatal":
            out["fatal"] = event["error"]
        elif event["kind"] == "stop":
            out["stop"] = {"counts": event["counts"], "clock": event["clock"]}
        elif event["kind"] == "start":
            out = {"outages": [], "fatal": None, "stop": None}  # only the latest run
    return out


def describe(report: dict) -> str:
    lines = [f"Watched the Alpaca {report['feed']} stream for {report['seconds']:.0f} seconds."]
    for symbol, c in report["per_symbol"].items():
        lines.append(
            f"  {symbol}: {c['trades']:,} trades, {c['quotes']:,} quotes, {c['statuses']} status messages"
        )
    j = report["journal"]
    if j["fatal"]:
        lines.append(f"  STOPPED BY ALPACA: {j['fatal']}")
    if j["stop"]:
        clock = j["stop"]["clock"]
        lines.append(
            f"  Delay from exchange to us: average {clock['mean_lag_ms']} ms, "
            f"worst {clock['max_lag_ms']} ms; "
            f"{clock['stale']} late, {clock['clock_skew']} with our clock behind."
        )
    lines.append(f"  Disconnects: {len(j['outages'])}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--feed", choices=["test", "iex", "sip", "delayed_sip"], default="test")
    parser.add_argument("--symbols", default="FAKEPACA")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--journal", type=Path, default=Path("data/live/journal.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    config = AlpacaDataClientConfig(feed=args.feed, journal_path=str(args.journal))
    try:
        AlpacaKeys.from_env()  # fail early, in plain words, before the node starts
        report = run(symbols, args.seconds, config)
    except AlpacaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    report["journal"] = journal_summary(args.journal)
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"live-{report['started'][:19].replace(':', '')}.json"
    path.write_text(json.dumps(report, indent=2))
    print(describe(report))
    print(f"Report: {path}")
    if report["journal"]["fatal"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
