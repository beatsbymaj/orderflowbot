"""Put trades and quotes into one deterministic order before any feature sees them.

Live, trades and quotes arrive on separate channels and can be out of order by a few
milliseconds; in replay they come from files. Both paths push every event through this
buffer. An event is released once the newest timestamp seen is `window` past it, in this
order: timestamp, then trades before quotes at the same nanosecond (a quote stamped with
the trade's own time may already reflect it, so the trade uses the quote before), then
exchange + trade id for trades, then arrival order for quotes.

Nothing reads the wall clock, so the output depends only on the events. An event older than
what was already released is late: it is dropped and counted, never processed out of order.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import Any

TRADE = 0
QUOTE = 1


@dataclass(frozen=True, slots=True)
class Event:
    ts: int
    rank: int  # TRADE or QUOTE
    tie: tuple  # (exchange, id) for trades, (arrival,) for quotes
    payload: Any

    @property
    def key(self) -> tuple:
        return (self.ts, self.rank, self.tie)


class Sequencer:
    def __init__(self, window_ns: int) -> None:
        self.window_ns = window_ns
        self._heap: list[tuple[tuple, int, Event]] = []
        self._arrivals = 0
        self._newest = None
        self.released_key: tuple | None = None
        self.late = 0

    def push_trade(self, ts: int, exchange: str, exchange_id: int, payload: Any) -> list[Event]:
        return self._push(Event(ts, TRADE, (exchange, exchange_id), payload))

    def push_quote(self, ts: int, payload: Any) -> list[Event]:
        self._arrivals += 1
        return self._push(Event(ts, QUOTE, ("", self._arrivals), payload))

    def _push(self, event: Event) -> list[Event]:
        if self.released_key is not None and event.key <= self.released_key:
            self.late += 1
            return []
        self._arrivals += 1
        heapq.heappush(self._heap, (event.key, self._arrivals, event))
        if self._newest is None or event.ts > self._newest:
            self._newest = event.ts
        return self._release(self._newest - self.window_ns)

    def _release(self, upto: int) -> list[Event]:
        out = []
        while self._heap and self._heap[0][0][0] <= upto:
            out.append(heapq.heappop(self._heap)[2])
        if out:
            self.released_key = out[-1].key
        return out

    def flush(self) -> list[Event]:
        out = [heapq.heappop(self._heap)[2] for _ in range(len(self._heap))]
        if out:
            self.released_key = out[-1].key
        return out
