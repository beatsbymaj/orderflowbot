import asyncio
import json

import pytest

from flowforge.data import alpaca_stream as st
from flowforge.data.alpaca_bars import AlpacaKeys
from flowforge.data.ticks import NS, format_ts_ns

KEYS = AlpacaKeys("id", "secret")
T0 = 1_792_503_000 * NS  # 2026-10-20 13:30:00Z


def ts(offset_ns=0):
    return format_ts_ns(T0 + offset_ns)


CONNECTED = [{"T": "success", "msg": "connected"}]
AUTHED = [{"T": "success", "msg": "authenticated"}]


class FakeSocket:
    """Replays scripted frames; raises ConnectionError when the script runs out."""

    def __init__(self, frames, hold=False):
        self.frames = [json.dumps(f) for f in frames]
        self.sent = []
        self.closed = False
        self.hold = hold  # wait instead of dropping when out of frames

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self):
        await asyncio.sleep(0)
        if self.frames:
            return self.frames.pop(0)
        if self.hold:
            while not self.closed:
                await asyncio.sleep(0.01)
        raise ConnectionError("socket closed")

    async def close(self):
        self.closed = True


class Recorder:
    def __init__(self):
        self.records, self.outages, self.notices = [], [], []
        self.callbacks = st.StreamCallbacks(
            lambda r, t: self.records.append((r, t)),
            lambda a, b: self.outages.append((a, b)),
            lambda k, d: self.notices.append((k, d)),
        )


def make_stream(sockets, rec, clock=None, sleeps=None):
    sockets = list(sockets)

    async def connect(url):
        if not sockets:
            raise OSError("no more sockets")
        s = sockets.pop(0)
        if isinstance(s, Exception):
            raise s
        return s

    async def sleep(seconds):
        (sleeps if sleeps is not None else []).append(seconds)
        await asyncio.sleep(0)  # let the test's own task run

    return st.AlpacaStream(
        "wss://x",
        KEYS,
        rec.callbacks,
        connect=connect,
        clock_ns=clock or (lambda: T0 + NS // 10),
        sleep=sleep,
    )


def test_parse_every_record_type():
    trade = st.parse_record(
        {"T": "t", "S": "SPY", "i": 7, "x": "V", "p": 500.1, "s": 10, "c": ["@"], "t": ts(), "z": "B"}
    )
    assert trade.symbol == "SPY" and trade.trade.trade_id == "V-7" and trade.trade.ts == T0
    q = {
        "T": "q",
        "S": "SPY",
        "bx": "Q",
        "bp": 1,
        "bs": 2,
        "ax": "P",
        "ap": 2,
        "as": 3,
        "c": ["R"],
        "t": ts(),
        "z": "B",
    }
    assert st.parse_record(q, 100).quote.ask_size == 300
    corr = st.parse_record(
        {
            "T": "c", "S": "SPY", "x": "V", "oi": 1, "op": 1.0, "os": 5, "oc": [], "ci": 2, "cp": 1.1,
            "cs": 6, "cc": ["@"], "t": ts(), "z": "B",
        }
    )  # fmt: skip
    assert corr.corrected_id == 2 and corr.corrected_conditions == ("@",)
    cancel = st.parse_record(
        {"T": "x", "S": "SPY", "i": 3, "x": "D", "p": 1.0, "s": 5, "a": "C", "t": ts(), "z": "B"}
    )
    assert cancel.trade_id == "D-3" and cancel.action == "C"
    luld = st.parse_record({"T": "l", "S": "SPY", "u": 510.0, "d": 490.0, "i": "B", "t": ts(), "z": "B"})
    assert luld.limit_up == 510.0
    status = st.parse_record(
        {"T": "s", "S": "SPY", "sc": "H", "sm": "Trading Halt", "rc": "T1", "rm": "News", "t": ts(), "z": "C"}
    )
    assert status.code == "H" and status.reason_code == "T1"
    assert st.parse_record({"T": "b", "S": "SPY"}) is None


def test_clock_monitor_flags_stale_and_skew():
    m = st.ClockMonitor(max_lag_ns=100, max_ahead_ns=10)
    assert m.observe(0, 50) is None
    assert m.observe(0, 150) == "stale"
    assert m.observe(100, 80) == "clock_skew"
    s = m.summary()
    assert s["events"] == 3 and s["stale"] == 1 and s["clock_skew"] == 1 and s["max_lag_ms"] == 0.0
    assert st.ClockMonitor().summary()["mean_lag_ms"] == 0


def test_stream_authenticates_subscribes_and_delivers():
    rec = Recorder()
    frames = [
        CONNECTED,
        AUTHED,
        [{"T": "subscription", "trades": ["SPY"], "quotes": []}],
        [{"T": "t", "S": "SPY", "i": 1, "x": "V", "p": 1.0, "s": 1, "c": [], "t": ts(), "z": "B"}],
    ]
    sock = FakeSocket(frames, hold=True)
    stream = make_stream([sock], rec)

    async def scenario():
        await stream.subscribe("trades", "SPY")  # before connecting: remembered, sent on open
        task = asyncio.create_task(stream.run())
        while not rec.records:
            await asyncio.sleep(0.01)
        await stream.subscribe("quotes", "SPY")
        await stream.subscribe("quotes", "SPY")  # duplicate is ignored
        await stream.unsubscribe("quotes", "SPY")
        await stream.unsubscribe("quotes", "SPY")
        await stream.stop()
        await task

    asyncio.run(scenario())
    assert sock.sent[0] == {"action": "auth", "key": "id", "secret": "secret"}
    assert sock.sent[1] == {"action": "subscribe", "trades": ["SPY"]}
    assert sock.sent[2:] == [
        {"action": "subscribe", "quotes": ["SPY"]},
        {"action": "unsubscribe", "quotes": ["SPY"]},
    ]
    record, recv = rec.records[0]
    assert record.trade.trade_id == "V-1" and recv == T0 + NS // 10
    assert ("subscription", {"trades": ["SPY"], "quotes": [], "statuses": [], "lulds": []}) in rec.notices
    assert stream.clock.count == 1 and "secret" not in repr(stream)


def test_stream_reconnects_and_reports_the_outage():
    rec = Recorder()
    clock = iter(range(T0, T0 + 100 * NS, NS))
    sleeps = []
    first = FakeSocket([CONNECTED, AUTHED, [{"T": "error", "code": 407, "msg": "slow client"}]])
    second = FakeSocket([CONNECTED, AUTHED], hold=True)
    stream = make_stream([first, OSError("refused"), second], rec, clock=lambda: next(clock), sleeps=sleeps)

    async def scenario():
        task = asyncio.create_task(stream.run())
        while stream.connects < 2:
            await asyncio.sleep(0.01)
        await stream.stop()
        await task

    asyncio.run(scenario())
    assert sleeps == [1.0, 2.0]  # exponential backoff
    assert len(rec.outages) == 1 and rec.outages[0][1] > rec.outages[0][0]
    kinds = [k for k, _ in rec.notices]
    assert "slow_client" in kinds and kinds.count("disconnected") == 2 and first.closed


@pytest.mark.parametrize(
    ("frames", "match"),
    [
        ([CONNECTED, [{"T": "error", "code": 402, "msg": "auth failed"}]], "auth failed"),
        ([CONNECTED, AUTHED, [{"T": "error", "code": 409, "msg": "insufficient subscription"}]], "paid plan"),
        (
            [CONNECTED, AUTHED, [{"T": "error", "code": 406, "msg": "connection limit exceeded"}]],
            "another program",
        ),
    ],
)
def test_stream_stops_on_fatal_errors(frames, match):
    rec = Recorder()
    stream = make_stream([FakeSocket(frames)], rec)
    with pytest.raises(st.StreamFatalError, match=match):
        asyncio.run(stream.run())


def test_stream_treats_unexpected_handshake_and_unknown_errors_as_disconnects():
    rec = Recorder()
    socks = [
        FakeSocket([[{"T": "success", "msg": "hello"}]]),
        FakeSocket([CONNECTED, AUTHED, [{"T": "error", "code": 500, "msg": "internal"}]]),
    ]
    stream = make_stream(socks, rec)

    async def scenario():
        task = asyncio.create_task(stream.run())
        while len([k for k, _ in rec.notices if k == "disconnected"]) < 3:
            await asyncio.sleep(0.01)
        await stream.stop()
        await task

    asyncio.run(scenario())
    errors = [d["error"] for k, d in rec.notices if k == "disconnected"]
    assert "expected 'connected'" in errors[0] and "500" in errors[1]


def test_stale_records_raise_a_notice():
    rec = Recorder()
    frames = [
        CONNECTED,
        AUTHED,
        [{"T": "l", "S": "SPY", "u": 2.0, "d": 1.0, "i": "B", "t": ts(-5 * NS), "z": "B"}],
    ]
    stream = make_stream([FakeSocket(frames, hold=True)], rec)

    async def scenario():
        task = asyncio.create_task(stream.run())
        while not rec.records:
            await asyncio.sleep(0.01)
        await stream.stop()
        await task

    asyncio.run(scenario())
    assert rec.notices[-1][0] == "stale" and rec.notices[-1][1]["symbol"] == "SPY"
