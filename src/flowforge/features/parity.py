"""Phase 3 exit gate: the same ticks give identical features live and in replay.

Three paths over one symbol-day of downloaded ticks:
- replay:   catalog -> FeatureEngine
- backtest: catalog -> Nautilus BacktestEngine -> FeatureActor
- live:     the ticks re-sent as Alpaca stream messages through a scripted socket ->
            AlpacaDataClient -> Nautilus LiveNode -> FeatureActor (TapeTrade custom data)

The live path uses the real client, stream parser and node; only the network is replaced.
No API keys are used and nothing connects to Alpaca or a broker. Records are compared one
by one per symbol; the first difference is printed.

Usage:
    python -m flowforge.features.parity --ticks data/ticks --symbol SPY --day 2026-10-23
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path
from typing import Any

from nautilus_trader.persistence import ParquetDataCatalog

from flowforge.data.alpaca_bars import AlpacaKeys
from flowforge.data.alpaca_data_client import (
    STREAM_URLS,
    AlpacaDataClient,
    AlpacaDataClientConfig,
    AlpacaDataClientFactory,
)
from flowforge.data.alpaca_stream import AlpacaStream
from flowforge.data.ticks import Quote, Trade, format_ts_ns
from flowforge.features.actor import FeatureActor
from flowforge.features.config import FeatureConfig
from flowforge.features.replay import _seed, backtest_day, iter_ticks, load_day, merged, replay_day

FRAME_SIZE = 500  # messages per websocket frame
# Files are read far faster than the node consumes them; without a cap the unread messages
# pile up in memory (3+ GB for a day). Real Alpaca traffic is much slower than this.
MAX_BACKLOG = 20 * FRAME_SIZE


def trade_message(symbol: str, t: Trade) -> dict:
    return {
        "T": "t",
        "S": symbol,
        "i": t.exchange_id,
        "x": t.exchange,
        "p": t.price,
        "s": t.size,
        "c": list(t.conditions),
        "t": format_ts_ns(t.ts),
        "z": t.tape,
    }


def quote_message(symbol: str, q: Quote) -> dict:
    return {
        "T": "q",
        "S": symbol,
        "bx": q.bid_exchange,
        "bp": q.bid_price,
        "bs": q.bid_size,
        "ax": q.ask_exchange,
        "ap": q.ask_price,
        "as": q.ask_size,
        "c": list(q.conditions),
        "t": format_ts_ns(q.ts),
        "z": q.tape,
    }


class ReplaySocket:
    """Stands in for Alpaca's websocket: handshake, then the recorded frames (pulled lazily
    from `frames`) once the client has subscribed to trades and quotes for every symbol."""

    def __init__(self, frames: Iterator[str], symbols: list[str], backlog: Any = None) -> None:
        self._backlog = backlog or (lambda: 0)  # messages sent but not yet seen by the actor
        self._out = [
            json.dumps([{"T": "success", "msg": "connected"}]),
            json.dumps([{"T": "success", "msg": "authenticated"}]),
        ]
        self._frames = frames
        self._need = {("trades", s) for s in symbols} | {("quotes", s) for s in symbols}
        self.closed = False
        self.exhausted = False

    async def send(self, message: str) -> None:
        msg = json.loads(message)
        if msg.get("action") == "subscribe":
            for channel in ("trades", "quotes"):
                for symbol in msg.get(channel, []):
                    self._need.discard((channel, symbol))

    async def recv(self) -> str:
        while True:
            if self._out:
                return self._out.pop(0)
            if self.closed:
                raise ConnectionError("closed")
            if not self._need and not self.exhausted and self._backlog() < MAX_BACKLOG:
                frame = next(self._frames, None)
                if frame is not None:
                    return frame
                self.exhausted = True
            await asyncio.sleep(0.005)

    async def close(self) -> None:
        self.closed = True


class _Factory(AlpacaDataClientFactory):
    def __init__(self, socket: ReplaySocket) -> None:
        self.socket = socket

    def create(
        self, *, name: str, config: AlpacaDataClientConfig, cache: Any, clock: Any
    ) -> AlpacaDataClient:
        async def connect(_url: str) -> ReplaySocket:
            return self.socket

        keys = AlpacaKeys("replay", "replay")  # never sent anywhere: the socket is local
        stream = AlpacaStream(STREAM_URLS[config.feed], keys, None, connect=connect)
        return AlpacaDataClient(name=name, config=config, cache=cache, clock=clock, stream=stream, keys=keys)


def live_records(
    symbol: str,
    events: Iterable[tuple[str, object]],
    config: FeatureConfig,
    seed: Any = None,
    timeout_s: float = 7200,
) -> tuple[list[dict], dict]:
    """Send `events` (in order) through the live client and node; return the actor's records."""
    from nautilus_trader.common import Environment, LoggerConfig, LogLevel
    from nautilus_trader.live import LiveNode
    from nautilus_trader.model import TraderId

    sent = {"trades": 0, "quotes": 0}

    def frames() -> Iterator[str]:
        batch: list[dict] = []
        for kind, rec in events:
            if kind == "t":
                sent["trades"] += 1
                batch.append(trade_message(symbol, rec))
            else:
                sent["quotes"] += 1
                batch.append(quote_message(symbol, rec))
            if len(batch) == FRAME_SIZE:
                yield json.dumps(batch)
                batch = []
        if batch:
            yield json.dumps(batch)

    records: list[dict] = []
    actor_box: list[FeatureActor] = []

    def backlog() -> int:
        seen = actor_box[0].received if actor_box else {"trades": 0, "quotes": 0}
        return sent["trades"] + sent["quotes"] - seen["trades"] - seen["quotes"]

    socket = ReplaySocket(frames(), [symbol], backlog)
    with tempfile.TemporaryDirectory() as tmp:
        client_config = AlpacaDataClientConfig(
            feed="sip", journal_path=str(Path(tmp) / "journal.jsonl"), backfill=False
        )
        node = (
            LiveNode.builder("features-parity", TraderId("FLOWFORGE-PARITY"), Environment.LIVE)
            .with_logging(LoggerConfig(stdout_level=LogLevel.WARNING))
            .with_reconciliation(False)
            .with_delay_post_stop_secs(0)
            .with_timeout_disconnection_secs(5)
            .add_data_client("ALPACA", _Factory(socket), client_config)
            .build()
        )
        actor = FeatureActor([symbol], config, on_record=records.append)
        actor_box.append(actor)
        if seed is not None:
            seed(actor.engine)
        node.add_actor(actor)

        async def drive() -> None:
            task = asyncio.create_task(node.run_async())
            deadline = time.monotonic() + timeout_s
            while not (socket.exhausted and actor.received == sent) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            node.handle().stop()
            await task

        try:
            asyncio.run(drive())
        finally:
            node.dispose()
    return records, {"sent": sent, "received": dict(actor.received)}


def _digest(records: list[dict]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(json.dumps(r, sort_keys=True).encode())
    return h.hexdigest()[:16]


def compare(paths: dict[str, list[dict]]) -> dict:
    """Records per path, a short hash each, and the first record where any path differs."""
    names = list(paths)
    base = paths[names[0]]
    out: dict = {
        "records": {n: len(r) for n, r in paths.items()},
        "hash": {n: _digest(r) for n, r in paths.items()},
    }
    out["identical"] = len(set(out["hash"].values())) == 1
    if not out["identical"]:
        for i in range(max(len(r) for r in paths.values())):
            rows = {n: (paths[n][i] if i < len(paths[n]) else None) for n in names}
            if any(rows[n] != rows[names[0]] for n in names):
                out["first_difference"] = {"index": i, **{n: rows[n] for n in names}}
                break
    out["base_records"] = len(base)
    return out


def run(ticks: Path, symbol: str, day: date, config: FeatureConfig, minutes: int | None = None) -> dict:
    d = load_day(ticks, symbol, day)
    timings: dict[str, float] = {}

    t0 = time.monotonic()
    replay: list[dict] = []
    replay_day(ticks, symbol, day, config, replay.append, minutes=minutes)
    timings["replay"] = time.monotonic() - t0

    t0 = time.monotonic()
    backtest: list[dict] = []
    backtest_day(ticks, symbol, day, config, backtest.append, minutes=minutes)
    timings["backtest"] = time.monotonic() - t0

    t0 = time.monotonic()
    catalog = ParquetDataCatalog(str(ticks / "catalog"))
    events = (
        e for quotes, trades in iter_ticks(catalog, d, minutes) for e in merged(quotes, trades, d.conditions)
    )
    live, received = live_records(symbol, events, config, seed=lambda eng: _seed(eng, ticks, d, config))
    timings["live"] = time.monotonic() - t0

    result = compare({"replay": replay, "backtest": backtest, "live": live})
    result |= {
        "symbol": symbol,
        "day": day.isoformat(),
        "minutes": minutes,
        "events": received["sent"]["trades"] + received["sent"]["quotes"],
        "live_counts": received,
        "seconds": {k: round(v, 1) for k, v in timings.items()},
    }
    return result


def describe(r: dict) -> str:
    verdict = "IDENTICAL" if r["identical"] else "DIFFERENT"
    window = f"first {r['minutes']} min" if r["minutes"] else "whole session"
    lines = [
        f"{r['symbol']} {r['day']} ({window}, {r['events']:,} ticks): {verdict}",
        "  "
        + ", ".join(
            f"{n}: {c:,} records, hash {r['hash'][n]}, {r['seconds'][n]} s" for n, c in r["records"].items()
        ),
    ]
    if not r["identical"]:
        lines.append("  First difference: " + json.dumps(r["first_difference"])[:2000])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ticks", type=Path, default=Path("data/ticks"))
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--day", required=True, help="YYYY-MM-DD")
    p.add_argument(
        "--minutes", type=int, default=None, help="only the first N minutes (default: whole session)"
    )
    p.add_argument("--out", type=Path, default=Path("reports"))
    a = p.parse_args(argv)
    result = run(a.ticks, a.symbol, date.fromisoformat(a.day), FeatureConfig(), a.minutes)
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / f"parity-{a.symbol}-{a.day}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    print(describe(result))
    print(f"Report: {path}")
    return 0 if result["identical"] else 1


if __name__ == "__main__":
    sys.exit(main())
