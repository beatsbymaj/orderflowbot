"""Alpaca real-time stock stream: connect, authenticate, subscribe, parse, reconnect.

Protocol from Alpaca's docs (checked 2026-10-25, docs/research/10-alpaca-tick-history.md):
the server greets with {"T":"success","msg":"connected"}; the client sends
{"action":"auth",...} and gets "authenticated"; then {"action":"subscribe",...}.
Frames are JSON arrays. Corrections and cancel/errors arrive with a trades subscription.

This module knows nothing about Nautilus: it turns frames into records and hands them
to a callback with the local receive time. It never sends anything but auth and
(un)subscribe messages, and it never touches the trading API.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from flowforge.data.alpaca_bars import AlpacaKeys
from flowforge.data.ticks import NS, Quote, Trade, parse_ts_ns

STREAM_URLS = {
    "sip": "wss://stream.data.alpaca.markets/v2/sip",  # needs Algo Trader Plus
    "iex": "wss://stream.data.alpaca.markets/v2/iex",  # free plan, IEX only (~2-5% of volume)
    "delayed_sip": "wss://stream.data.alpaca.markets/v2/delayed_sip",
    "test": "wss://stream.data.alpaca.markets/v2/test",  # symbol FAKEPACA, always on
}
# Errors that reconnecting can't fix; FlowForge stops and reports instead (CLAUDE.md rule 7).
FATAL_CODES = {
    400: "invalid syntax",
    401: "not authenticated",
    402: "auth failed (check the keys in .env)",
    403: "already authenticated",
    404: "auth timeout",
    405: "symbol limit exceeded",
    406: "connection limit exceeded (another program is using this Alpaca stream)",
    409: "insufficient subscription (this feed needs a paid plan)",
    410: "invalid subscribe action for this feed",
}
SLOW_CLIENT = 407
CHANNELS = ("trades", "quotes", "statuses", "lulds")


class StreamFatalError(RuntimeError):
    """The stream refused us in a way a reconnect won't fix."""


@dataclass(frozen=True, slots=True)
class Correction:
    symbol: str
    ts: int
    exchange: str
    original_id: int
    original_price: float
    original_size: int
    corrected_id: int
    corrected_price: float
    corrected_size: int
    corrected_conditions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CancelError:
    symbol: str
    ts: int
    exchange: str
    exchange_id: int
    price: float
    size: int
    action: str  # "C" cancel, "E" error

    @property
    def trade_id(self) -> str:
        return f"{self.exchange}-{self.exchange_id}"


@dataclass(frozen=True, slots=True)
class Luld:
    symbol: str
    ts: int
    limit_up: float
    limit_down: float
    indicator: str


@dataclass(frozen=True, slots=True)
class Status:
    symbol: str
    ts: int
    code: str
    message: str
    reason_code: str
    reason: str
    tape: str


@dataclass(frozen=True, slots=True)
class SymbolTrade:
    symbol: str
    trade: Trade


@dataclass(frozen=True, slots=True)
class SymbolQuote:
    symbol: str
    quote: Quote


Record = SymbolTrade | SymbolQuote | Correction | CancelError | Luld | Status


def parse_record(msg: dict, quote_size_multiplier: int = 1) -> Record | None:
    """One stream message to a record; None for control messages and unknown types."""
    kind = msg.get("T")
    if kind == "t":
        return SymbolTrade(msg["S"], Trade.from_alpaca(msg))
    if kind == "q":
        return SymbolQuote(msg["S"], Quote.from_alpaca(msg, quote_size_multiplier))
    if kind == "c":
        return Correction(
            msg["S"],
            parse_ts_ns(msg["t"]),
            msg["x"],
            int(msg["oi"]),
            float(msg["op"]),
            int(msg["os"]),
            int(msg["ci"]),
            float(msg["cp"]),
            int(msg["cs"]),
            tuple(msg.get("cc") or ()),
        )
    if kind == "x":
        return CancelError(
            msg["S"], parse_ts_ns(msg["t"]), msg["x"], int(msg["i"]), float(msg["p"]), int(msg["s"]), msg["a"]
        )
    if kind == "l":
        return Luld(msg["S"], parse_ts_ns(msg["t"]), float(msg["u"]), float(msg["d"]), msg.get("i", ""))
    if kind == "s":
        return Status(
            msg["S"],
            parse_ts_ns(msg["t"]),
            str(msg.get("sc", "")),
            msg.get("sm", ""),
            str(msg.get("rc", "")),
            msg.get("rm", ""),
            msg.get("z", ""),
        )
    return None


@dataclass
class ClockMonitor:
    """Compares each event's exchange time with our clock when it arrives.

    `lag` = receive time - event time. A large lag means stale data or a slow link; a
    negative lag beyond `max_ahead_ns` means our clock is behind (clock skew).
    """

    max_lag_ns: int = 2 * NS
    max_ahead_ns: int = NS // 4
    count: int = 0
    stale: int = 0
    ahead: int = 0
    max_lag: int = 0
    min_lag: int = 0
    _sum: int = field(default=0, repr=False)

    def observe(self, event_ts: int, recv_ts: int) -> str | None:
        lag = recv_ts - event_ts
        if self.count == 0:
            self.max_lag = self.min_lag = lag
        self.count += 1
        self._sum += lag
        self.max_lag = max(self.max_lag, lag)
        self.min_lag = min(self.min_lag, lag)
        if lag > self.max_lag_ns:
            self.stale += 1
            return "stale"
        if -lag > self.max_ahead_ns:
            self.ahead += 1
            return "clock_skew"
        return None

    def summary(self) -> dict:
        mean = self._sum / self.count if self.count else 0
        return {
            "events": self.count,
            "stale": self.stale,
            "clock_skew": self.ahead,
            "mean_lag_ms": round(mean / 1e6, 3),
            "max_lag_ms": round(self.max_lag / 1e6, 3),
            "min_lag_ms": round(self.min_lag / 1e6, 3),
        }


class Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...
    async def close(self) -> None: ...


Connect = Callable[[str], Awaitable[Socket]]


async def _websockets_connect(url: str) -> Socket:  # pragma: no cover - real network
    from websockets.asyncio.client import connect

    return await connect(url, max_size=2**24, ping_interval=20, ping_timeout=20)


@dataclass
class StreamCallbacks:
    on_record: Callable[[Record, int], None]
    on_outage: Callable[[int, int], None] = lambda start, end: None
    on_notice: Callable[[str, dict], None] = lambda kind, detail: None


class AlpacaStream:
    """One connection to one Alpaca stream, reconnecting with backoff until stopped."""

    def __init__(
        self,
        url: str,
        keys: AlpacaKeys,
        callbacks: StreamCallbacks,
        connect: Connect = _websockets_connect,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        quote_size_multiplier: int = 1,
        max_backoff_s: float = 30.0,
    ) -> None:
        self.url = url
        self._keys = keys
        self._cb = callbacks
        self._connect = connect
        self._clock_ns = clock_ns
        self._sleep = sleep
        self._multiplier = quote_size_multiplier
        self._max_backoff = max_backoff_s
        self._socket: Socket | None = None
        self._stopping = False
        self.subscriptions: dict[str, set[str]] = {c: set() for c in CHANNELS}
        self.clock = ClockMonitor()
        self.connects = 0

    def __repr__(self) -> str:  # keys never appear in logs
        return f"AlpacaStream(url={self.url!r})"

    async def subscribe(self, channel: str, symbol: str) -> None:
        if symbol not in self.subscriptions[channel]:
            self.subscriptions[channel].add(symbol)
            await self._send_subscription("subscribe", {channel: [symbol]})

    async def unsubscribe(self, channel: str, symbol: str) -> None:
        if symbol in self.subscriptions[channel]:
            self.subscriptions[channel].discard(symbol)
            await self._send_subscription("unsubscribe", {channel: [symbol]})

    async def _send_subscription(self, action: str, channels: dict[str, list[str]]) -> None:
        if self._socket is not None:
            await self._socket.send(json.dumps({"action": action} | channels))

    async def stop(self) -> None:
        self._stopping = True
        if self._socket is not None:
            await self._socket.close()

    async def run(self) -> None:
        """Stay connected until stop(). Raises StreamFatalError on refusals."""
        backoff = 1.0
        outage_start: int | None = None
        while not self._stopping:
            try:
                await self._open()
                if outage_start is not None:
                    self._cb.on_outage(outage_start, self._clock_ns())
                    outage_start = None
                backoff = 1.0
                await self._read_forever()
            except StreamFatalError:
                raise
            except Exception as exc:  # network drops, server closes, bad frames
                if self._stopping:
                    break
                self._cb.on_notice("disconnected", {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                if self._socket is not None:
                    try:
                        await self._socket.close()
                    except Exception:  # noqa: S110 - already broken; nothing more to do
                        pass
                    self._socket = None
            if self._stopping:
                break
            if outage_start is None:
                outage_start = self._clock_ns()
            await self._sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def _open(self) -> None:
        socket = await self._connect(self.url)
        self._socket = socket
        self._expect(await self._recv(), "connected")
        await socket.send(
            json.dumps({"action": "auth", "key": self._keys.key_id, "secret": self._keys.secret})
        )
        self._expect(await self._recv(), "authenticated")
        self.connects += 1
        active = {c: sorted(s) for c, s in self.subscriptions.items() if s}
        if active:
            await socket.send(json.dumps({"action": "subscribe"} | active))
        self._cb.on_notice("connected", {"url": self.url, "connects": self.connects})

    async def _recv(self) -> list[dict]:
        assert self._socket is not None
        frame = await self._socket.recv()
        messages = json.loads(frame)
        return messages if isinstance(messages, list) else [messages]

    def _expect(self, messages: list[dict], msg: str) -> None:
        for m in messages:
            self._check_error(m)
            if m.get("T") == "success" and m.get("msg") == msg:
                return
        raise ConnectionError(f"expected {msg!r}, got {messages!r}")

    def _check_error(self, m: dict) -> None:
        if m.get("T") != "error":
            return
        code = int(m.get("code", 0))
        if code in FATAL_CODES:
            raise StreamFatalError(f"Alpaca stream error {code}: {FATAL_CODES[code]}")
        if code == SLOW_CLIENT:
            self._cb.on_notice("slow_client", m)
            return
        raise ConnectionError(f"Alpaca stream error {code}: {m.get('msg', '')}")

    async def _read_forever(self) -> None:
        while not self._stopping:
            messages = await self._recv()
            recv_ts = self._clock_ns()
            for m in messages:
                self._check_error(m)
                if m.get("T") == "subscription":
                    self._cb.on_notice("subscription", {c: m.get(c, []) for c in CHANNELS})
                    continue
                record = parse_record(m, self._multiplier)
                if record is None:
                    continue
                flag = self.clock.observe(_record_ts(record), recv_ts)
                if flag:
                    self._cb.on_notice(
                        flag,
                        {"symbol": getattr(record, "symbol", ""), "lag_ns": recv_ts - _record_ts(record)},
                    )
                self._cb.on_record(record, recv_ts)


def _record_ts(record: Record) -> int:
    if isinstance(record, SymbolTrade):
        return record.trade.ts
    if isinstance(record, SymbolQuote):
        return record.quote.ts
    return record.ts
