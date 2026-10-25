import asyncio
import json

import pytest
from nautilus_trader.model import MarketStatusAction

from flowforge.data import alpaca_data_client as dc
from flowforge.data import alpaca_live as live
from flowforge.data import alpaca_stream as st
from flowforge.data.alpaca_bars import AlpacaKeys
from flowforge.data.ticks import NS, format_ts_ns

KEYS = AlpacaKeys("id", "secret")


class ScriptedSocket:
    """Handshake, then sends the scripted frames once the trades subscription arrives."""

    def __init__(self, frames, drop_after=False):
        self.out = [[{"T": "success", "msg": "connected"}], [{"T": "success", "msg": "authenticated"}]]
        self.frames = frames
        self.sent = []
        self.closed = False
        self.drop_after = drop_after

    async def send(self, message):
        msg = json.loads(message)
        self.sent.append(msg)
        if msg.get("action") == "subscribe" and "trades" in msg:
            self.out += self.frames
            self.frames = []

    async def close(self):
        self.closed = True

    async def recv(self):
        while not self.out:
            if self.closed or (
                self.drop_after and not self.frames and self.sent[-1].get("action") == "subscribe"
            ):
                raise ConnectionError("closed")
            await asyncio.sleep(0.01)
        return json.dumps(self.out.pop(0))


def now_ts(delta_s=0.0):
    import time

    return format_ts_ns(time.time_ns() + int(delta_s * NS))


def frames_for(symbol):
    return [
        [
            {"T": "t", "S": symbol, "i": 1, "x": "V", "p": 10.0, "s": 5, "c": ["@"], "t": now_ts(), "z": "C"},
            {
                "T": "t",
                "S": symbol,
                "i": 2,
                "x": "V",
                "p": 10.0,
                "s": 5,
                "c": ["@"],
                "t": now_ts(),
                "z": "C",
                "u": "canceled",
            },
            {
                "T": "q",
                "S": symbol,
                "bx": "Q",
                "bp": 9.99,
                "bs": 100,
                "ax": "P",
                "ap": 10.01,
                "as": 200,
                "c": ["R"],
                "t": now_ts(),
                "z": "C",
            },
            {
                "T": "s",
                "S": symbol,
                "sc": "H",
                "sm": "Trading Halt",
                "rc": "T1",
                "rm": "News Pending",
                "t": now_ts(),
                "z": "C",
            },
            {
                "T": "s",
                "S": symbol,
                "sc": "7",
                "sm": "Market Imbalance Buy",
                "rc": "",
                "rm": "",
                "t": now_ts(),
                "z": "A",
            },
            {"T": "l", "S": symbol, "u": 11.0, "d": 9.0, "i": "B", "t": now_ts(), "z": "C"},
            {
                "T": "c",
                "S": symbol,
                "x": "V",
                "oi": 1,
                "op": 10.0,
                "os": 5,
                "oc": [],
                "ci": 3,
                "cp": 10.1,
                "cs": 5,
                "cc": ["@"],
                "t": now_ts(),
                "z": "C",
            },
            {"T": "x", "S": symbol, "i": 1, "x": "V", "p": 10.0, "s": 5, "a": "C", "t": now_ts(), "z": "C"},
            {
                "T": "t",
                "S": symbol,
                "i": 4,
                "x": "V",
                "p": 10.0,
                "s": 5,
                "c": ["@"],
                "t": now_ts(-10),
                "z": "C",
            },
        ]
    ]


class FakeFactory(dc.AlpacaDataClientFactory):
    def __init__(self, socket):
        self.socket = socket
        self.clients = []

    def create(self, *, name, config, cache, clock):
        async def connect(url):
            if self.socket.closed:  # reconnect: a fresh, quiet connection
                self.socket = ScriptedSocket([])
            return self.socket

        stream = st.AlpacaStream(dc.STREAM_URLS[config.feed], KEYS, None, connect=connect)
        client = dc.AlpacaDataClient(
            name=name, config=config, cache=cache, clock=clock, stream=stream, keys=KEYS
        )
        self.clients.append(client)
        return client


def test_live_node_turns_stream_records_into_nautilus_data(tmp_path):
    socket = ScriptedSocket(frames_for("FAKEPACA"))
    factory = FakeFactory(socket)
    journal = tmp_path / "journal.jsonl"
    config = dc.AlpacaDataClientConfig(feed="test", journal_path=str(journal))
    report = live.run(["FAKEPACA"], 1.5, config, factory)

    counts = report["per_symbol"]["FAKEPACA"]
    assert counts == {"trades": 2, "quotes": 1, "statuses": 1}  # canceled trade and imbalance not passed on
    assert "V-1" in report["last_trade"]["FAKEPACA"] or "V-4" in report["last_trade"]["FAKEPACA"]
    assert {"action": "subscribe", "trades": ["FAKEPACA"]} in socket.sent
    assert {"action": "subscribe", "lulds": ["FAKEPACA"]} in socket.sent

    events = [json.loads(line) for line in journal.read_text().splitlines()]
    kinds = [e["kind"] for e in events]
    for kind in ("start", "status", "luld", "correction", "cancel_error", "stale", "stop"):
        assert kind in kinds, kind
    stop = events[-1]
    assert stop["counts"]["trades"] == 3 and stop["clock"]["stale"] == 1
    assert "secret" not in journal.read_text()

    summary = live.journal_summary(journal)
    assert summary["fatal"] is None and summary["stop"]["counts"]["quotes"] == 1
    report["journal"] = summary
    text = live.describe(report)
    assert "FAKEPACA: 2 trades, 1 quotes, 1 status messages" in text and "Disconnects: 0." in text


def test_instrument_status_mapping():
    halt = st.Status("SPY", 5, "H", "Trading Halt", "T1", "News", "C")
    status = dc._instrument_status(halt, dc.STATUS_ACTIONS["H"], 9)
    assert status.action == MarketStatusAction.HALT and status.is_trading is False
    assert status.ts_event == 5 and status.ts_init == 9 and "T1" in status.reason
    ssr = dc._instrument_status(st.Status("SPY", 5, "E", "SSR", "", "", "A"), dc.STATUS_ACTIONS["E"], 9)
    assert ssr.is_trading is None


def test_client_rejects_unknown_feed():
    with pytest.raises(ValueError, match="unknown Alpaca feed"):
        dc.AlpacaDataClientConfig(feed="nope")


def test_fatal_stream_error_is_journaled(tmp_path):
    class Refusing(ScriptedSocket):
        async def send(self, message):
            await super().send(message)
            if json.loads(message).get("action") == "auth":
                self.out = [[{"T": "error", "code": 409, "msg": "insufficient subscription"}]]

    journal = tmp_path / "j.jsonl"
    sock = Refusing([])
    sock.out = [[{"T": "success", "msg": "connected"}]]
    live.run(
        ["SPY"], 0.5, dc.AlpacaDataClientConfig(feed="sip", journal_path=str(journal)), FakeFactory(sock)
    )
    summary = live.journal_summary(journal)
    assert "paid plan" in summary["fatal"]


def test_outage_is_journaled_and_backfilled(tmp_path, monkeypatch):
    calls = []

    def fake_backfill(settings, symbol, start, end):
        calls.append((symbol, start < end))
        if symbol == "BAD":
            raise RuntimeError("boom")
        return {"trades": 1}

    from flowforge.data import alpaca_ticks

    monkeypatch.setattr(alpaca_ticks, "backfill_window", fake_backfill)
    journal = tmp_path / "j.jsonl"
    config = dc.AlpacaDataClientConfig(
        feed="iex", journal_path=str(journal), backfill_delay_s=0, ticks_out=str(tmp_path / "ticks")
    )
    socket = ScriptedSocket(frames_for("SPY"), drop_after=True)
    factory = FakeFactory(socket)
    live.run(["SPY", "BAD"], 2.5, config, factory)
    kinds = [json.loads(line)["kind"] for line in journal.read_text().splitlines()]
    assert "outage" in kinds and "disconnected" in kinds
    assert ("SPY", True) in calls and "backfill" in kinds and "backfill_failed" in kinds


def test_journal_summary_missing_file_and_main_errors(tmp_path, monkeypatch, capsys):
    assert live.journal_summary(tmp_path / "none.jsonl") == {"outages": [], "fatal": None, "stop": None}
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    assert live.main(["--seconds", "0"]) == 1
    assert "ALPACA_API_KEY_ID" in capsys.readouterr().err


def test_main_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "id")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")
    socket = ScriptedSocket(frames_for("FAKEPACA"))
    monkeypatch.setattr(live, "AlpacaDataClientFactory", lambda: FakeFactory(socket))
    code = live.main(["--seconds", "1", "--journal", str(tmp_path / "j.jsonl"), "--out", str(tmp_path / "r")])
    out = capsys.readouterr().out
    assert code == 0 and "Watched the Alpaca test stream" in out
    assert list((tmp_path / "r").glob("live-*.json"))
