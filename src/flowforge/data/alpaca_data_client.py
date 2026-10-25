"""Nautilus market data client for Alpaca's real-time stock stream.

Turns stream records into Nautilus data:
- trades -> TradeTick (aggressor NO_AGGRESSOR; Phase 3 classifies), canceled ones dropped;
  also TapeTrade custom data (venue + conditions) for actors that subscribe to it (data/tape.py)
- quotes -> QuoteTick (NBBO)
- trading statuses -> InstrumentStatus (halt, pause, resume, short-sale restriction)
Corrections, cancels, LULD bands, clock warnings and outages have no Nautilus type yet, so
they go to a JSON-lines journal (CLAUDE.md rule 6) for Phase 3/4 to consume.

After a disconnect the outage window is journaled and, once it is older than the plan's
delay (15 min on free keys), re-downloaded over REST into the catalog with the same
gap checks as the history download. Ticks from that backfill are never replayed into
the running node: strategies must treat an outage as missing data.

Keys come from the environment only; the config holds no secrets.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nautilus_trader.config import DataClientConfig
from nautilus_trader.live.clients import DataClientFactory, MarketDataClient
from nautilus_trader.model import InstrumentStatus, MarketStatusAction, Venue

from flowforge.data.alpaca_bars import AlpacaKeys
from flowforge.data.alpaca_stream import (
    STREAM_URLS,
    AlpacaStream,
    CancelError,
    Correction,
    Luld,
    Record,
    Status,
    StreamCallbacks,
    StreamFatalError,
    SymbolQuote,
    SymbolTrade,
)
from flowforge.data.tape import TAPE_TRADE, tape_data
from flowforge.data.ticks import NS, VENUE, format_ts_ns, instrument_id, to_quote_tick, to_trade_tick

# Alpaca trading status codes (CTA: tapes A/B; UTP: tapes C/O) to Nautilus actions.
STATUS_ACTIONS = {
    "2": MarketStatusAction.HALT,
    "3": MarketStatusAction.TRADING,
    "E": MarketStatusAction.SHORT_SELL_RESTRICTION_CHANGE,
    "F": MarketStatusAction.PAUSE,  # CTA "Limit Up-Limit Down"
    "H": MarketStatusAction.HALT,
    "Q": MarketStatusAction.QUOTING,
    "T": MarketStatusAction.TRADING,
    "P": MarketStatusAction.PAUSE,  # UTP "Volatility Trading Pause"
}
FREE_PLAN_DELAY_NS = 15 * 60 * NS  # SIP history must be 15 min old without a subscription


@dataclass(frozen=True)
class AlpacaDataClientConfig(DataClientConfig):
    feed: str = "iex"  # "sip" needs Algo Trader Plus; "test" is the always-on FAKEPACA stream
    journal_path: str = "data/live/journal.jsonl"
    # Live quote sizes: the real-time docs still say round lots, the REST docs say shares
    # since 2025-11-03. UNVERIFIED; compare against REST after the first session.
    quote_size_multiplier: int = 1
    backfill: bool = True
    backfill_delay_s: int = 15 * 60 + 60
    ticks_out: str = "data/ticks"

    def __post_init__(self) -> None:
        if self.feed not in STREAM_URLS:
            raise ValueError(f"unknown Alpaca feed {self.feed!r}; use one of {sorted(STREAM_URLS)}")


class Journal:
    """Append-only JSON lines: one object per event, each with a UTC timestamp."""

    def __init__(self, path: Path, clock_ns: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._clock_ns = clock_ns

    def write(self, kind: str, **fields: Any) -> None:
        line = {"ts": format_ts_ns(self._clock_ns()), "kind": kind} | fields
        self._fh.write(json.dumps(line, default=str) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class AlpacaDataClient(MarketDataClient):
    def __init__(
        self, *, stream: AlpacaStream | None = None, keys: AlpacaKeys | None = None, **kwargs: Any
    ) -> None:
        super().__init__(venue=Venue(VENUE), **kwargs)
        cfg: AlpacaDataClientConfig = self.config
        self._keys = keys or AlpacaKeys.from_env(dict(os.environ))
        self._journal = Journal(Path(cfg.journal_path), self.clock.timestamp_ns)
        callbacks = StreamCallbacks(self._on_record, self._on_outage, self._on_notice)
        self.stream = stream or AlpacaStream(
            STREAM_URLS[cfg.feed], self._keys, callbacks, quote_size_multiplier=cfg.quote_size_multiplier
        )
        if stream is not None:
            stream._cb = callbacks  # noqa: SLF001 - injected stream (tests) reports to this client
        self._task: asyncio.Task | None = None
        self.counts: dict[str, int] = {}
        self._tape_symbols: set[str] = set()
        self._tick_symbols: set[str] = set()
        self.outages: list[tuple[int, int]] = []
        self.fatal: str | None = None

    # -- lifecycle ---------------------------------------------------------------------
    async def _connect(self) -> None:
        self._journal.write("start", feed=self.config.feed, url=self.stream.url)
        self._task = self.create_task(self._run_stream(), "alpaca-stream")

    async def _run_stream(self) -> None:
        try:
            await self.stream.run()
        except StreamFatalError as exc:
            self.fatal = str(exc)
            self._journal.write("fatal", error=str(exc))
            print(f"ALPACA STREAM STOPPED: {exc}", flush=True)

    async def _disconnect(self) -> None:
        try:
            await self.stream.stop()
        except Exception as exc:  # still close the journal and let the node shut down
            self._journal.write("stop_error", error=f"{type(exc).__name__}: {exc}")
        if self._task is not None:
            await asyncio.wait({self._task}, timeout=5)
        self._journal.write("stop", counts=self.counts, clock=self.stream.clock.summary())
        self._journal.close()

    # -- subscriptions -----------------------------------------------------------------
    async def _subscribe_trades(self, command: Any) -> None:
        symbol = command.instrument_id.symbol.value
        self._tick_symbols.add(symbol)
        await self.stream.subscribe("trades", symbol)

    async def _subscribe(self, command: Any) -> None:
        """Custom data: only TapeTrade, which rides on the stream's trades channel."""
        symbol = _tape_symbol(command)
        if symbol is not None:
            self._tape_symbols.add(symbol)
            await self.stream.subscribe("trades", symbol)

    async def _unsubscribe(self, command: Any) -> None:
        symbol = _tape_symbol(command)
        if symbol is not None:
            self._tape_symbols.discard(symbol)
            if symbol not in self._tick_symbols:
                await self.stream.unsubscribe("trades", symbol)

    async def _subscribe_quotes(self, command: Any) -> None:
        await self.stream.subscribe("quotes", command.instrument_id.symbol.value)

    async def _subscribe_instrument_status(self, command: Any) -> None:
        symbol = command.instrument_id.symbol.value
        await self.stream.subscribe("statuses", symbol)
        await self.stream.subscribe("lulds", symbol)

    async def _unsubscribe_trades(self, command: Any) -> None:
        symbol = command.instrument_id.symbol.value
        self._tick_symbols.discard(symbol)
        if symbol not in self._tape_symbols:
            await self.stream.unsubscribe("trades", symbol)

    async def _unsubscribe_quotes(self, command: Any) -> None:
        await self.stream.unsubscribe("quotes", command.instrument_id.symbol.value)

    async def _unsubscribe_instrument_status(self, command: Any) -> None:
        symbol = command.instrument_id.symbol.value
        await self.stream.unsubscribe("statuses", symbol)
        await self.stream.unsubscribe("lulds", symbol)

    # -- stream events -----------------------------------------------------------------
    def _count(self, kind: str) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def _on_record(self, record: Record, recv_ts: int) -> None:
        if isinstance(record, SymbolTrade):
            self._count("trades")
            if not record.trade.dropped:
                self._handle_data(to_trade_tick(record.trade, instrument_id(record.symbol), recv_ts))
                if record.symbol in self._tape_symbols:
                    self._handle_data(tape_data(record.symbol, record.trade, recv_ts))
        elif isinstance(record, SymbolQuote):
            self._count("quotes")
            self._handle_data(to_quote_tick(record.quote, instrument_id(record.symbol), recv_ts))
        elif isinstance(record, Status):
            self._count("statuses")
            self._journal.write("status", **asdict(record))
            action = STATUS_ACTIONS.get(record.code)
            if action is not None:
                self._handle_data(_instrument_status(record, action, recv_ts))
        else:
            kind = {Correction: "correction", CancelError: "cancel_error", Luld: "luld"}[type(record)]
            self._count(kind)
            self._journal.write(kind, **asdict(record))

    def _on_outage(self, start: int, end: int) -> None:
        self.outages.append((start, end))
        self._journal.write(
            "outage", start=format_ts_ns(start), end=format_ts_ns(end), seconds=(end - start) / NS
        )
        if self.config.backfill and self.config.feed in ("sip", "iex"):
            self.create_task(self._backfill_later(start, end), "alpaca-backfill")

    def _on_notice(self, kind: str, detail: dict) -> None:
        self._count(kind)
        if kind in ("stale", "clock_skew") and self.counts[kind] > 100 and self.counts[kind] % 1000:
            return  # keep the journal readable during a burst; totals are in the stop line
        self._journal.write(kind, **detail)

    async def _backfill_later(self, start: int, end: int) -> None:
        """Re-download an outage over REST once the plan allows, into the catalog."""
        from flowforge.data import alpaca_ticks

        wait_ns = end + self.config.backfill_delay_s * NS - self.clock.timestamp_ns()
        if wait_ns > 0:
            await asyncio.sleep(wait_ns / NS)
        symbols = sorted(self.stream.subscriptions["trades"] | self.stream.subscriptions["quotes"])
        # History is always SIP: free keys may read it once it is 15 minutes old.
        settings = alpaca_ticks.Settings(self._keys, Path(self.config.ticks_out), feed="sip")
        for symbol in symbols:
            try:
                result = await asyncio.to_thread(alpaca_ticks.backfill_window, settings, symbol, start, end)
                self._journal.write("backfill", symbol=symbol, **result)
            except Exception as exc:  # report, don't crash the node
                self._journal.write("backfill_failed", symbol=symbol, error=f"{type(exc).__name__}: {exc}")


def _tape_symbol(command: Any) -> str | None:
    data_type = command.data_type
    if data_type.type_name != TAPE_TRADE:
        return None
    return (data_type.metadata or {}).get("symbol")


def _instrument_status(record: Status, action: MarketStatusAction, recv_ts: int) -> InstrumentStatus:
    halted = action in (MarketStatusAction.HALT, MarketStatusAction.PAUSE)
    return InstrumentStatus(
        instrument_id(record.symbol),
        action,
        record.ts,
        recv_ts,
        reason=f"{record.reason_code} {record.reason}".strip() or None,
        trading_event=f"{record.code} {record.message}".strip() or None,
        is_trading=None if action == MarketStatusAction.SHORT_SELL_RESTRICTION_CHANGE else not halted,
    )


class AlpacaDataClientFactory(DataClientFactory):
    @staticmethod
    def create(*, name: str, config: AlpacaDataClientConfig, cache: Any, clock: Any) -> AlpacaDataClient:
        return AlpacaDataClient(name=name, config=config, cache=cache, clock=clock)
