"""NBBO quotes for the fast simulator, read lazily in 15-minute chunks.

A trade only needs the quotes from its signal to its exit, usually minutes, so chunks are
loaded on demand from the Phase 2 catalog and the most recent ones kept in memory.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from flowforge.data.ticks import NS, instrument_id

CHUNK_NS = 15 * 60 * NS
Q = tuple[int, float, float]  # ts ns, bid, ask
Chunk = tuple[array, array, array]  # ts ('q'), bid ('d'), ask ('d'): ~24 bytes a quote


class QuoteSource(Protocol):
    def chunk(self, symbol: str, start: int) -> Chunk:
        """Quotes with start <= ts < start + CHUNK_NS, in time order (start is chunk-aligned)."""
        ...


def to_chunk(rows: list[Q]) -> Chunk:
    rows.sort(key=lambda r: r[0])
    ts = array("q", (r[0] for r in rows))
    return ts, array("d", (r[1] for r in rows)), array("d", (r[2] for r in rows))


def iter_from(source: QuoteSource, symbol: str, start: int, end: int) -> Iterator[Q]:
    """Quotes with start <= ts < end."""
    a = start - start % CHUNK_NS
    while a < end:
        ts, bid, ask = source.chunk(symbol, a)
        for i in range(bisect_left(ts, start), len(ts)):
            if ts[i] >= end:
                return
            yield ts[i], bid[i], ask[i]
        a += CHUNK_NS


def last_before(source: QuoteSource, symbol: str, ts: int, lookback: int) -> Q | None:
    """The last quote at or before ts, no older than lookback ns."""
    a = ts - ts % CHUNK_NS
    while a + CHUNK_NS > ts - lookback:
        times, bid, ask = source.chunk(symbol, a)
        i = bisect_right(times, ts) - 1
        if i >= 0:
            return (times[i], bid[i], ask[i]) if times[i] >= ts - lookback else None
        a -= CHUNK_NS
    return None


class MemoryQuotes:
    """Quotes held in a list per symbol (tests, small runs)."""

    def __init__(self, quotes: dict[str, list[Q]]) -> None:
        self.chunks: dict[tuple[str, int], list[Q]] = {}
        for symbol, rows in quotes.items():
            for q in rows:
                self.chunks.setdefault((symbol, q[0] - q[0] % CHUNK_NS), []).append(q)
        self.chunks = {k: to_chunk(v) for k, v in self.chunks.items()}
        self.empty = to_chunk([])

    def chunk(self, symbol: str, start: int) -> Chunk:
        return self.chunks.get((symbol, start), self.empty)


class CatalogQuotes:
    """Quotes from the Nautilus Parquet catalog (data/ticks/catalog)."""

    def __init__(self, ticks: Path, keep: int = 30) -> None:
        from nautilus_trader.persistence import ParquetDataCatalog

        self.catalog = ParquetDataCatalog(str(ticks / "catalog"))
        self.keep = keep
        self.cache: OrderedDict[tuple[str, int], Chunk] = OrderedDict()

    def chunk(self, symbol: str, start: int) -> Chunk:
        key = (symbol, start)
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.move_to_end(key)
            return hit
        ticks = self.catalog.query_quote_ticks([str(instrument_id(symbol))], start, start + CHUNK_NS - 1)
        rows = to_chunk([(q.ts_event, q.bid_price.as_double(), q.ask_price.as_double()) for q in ticks])
        self.cache[key] = rows
        if len(self.cache) > self.keep:
            self.cache.popitem(last=False)
        return rows
