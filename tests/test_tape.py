import asyncio
from types import SimpleNamespace

from flowforge.data import alpaca_data_client as dc
from flowforge.data.tape import TapeTrade, split_conditions, tape_data, tape_data_type
from flowforge.data.ticks import Trade


class FakeStream:
    def __init__(self):
        self.calls = []

    async def subscribe(self, channel, symbol):
        self.calls.append(("sub", channel, symbol))

    async def unsubscribe(self, channel, symbol):
        self.calls.append(("unsub", channel, symbol))


def command(data_type=None, symbol=None):
    iid = SimpleNamespace(symbol=SimpleNamespace(value=symbol)) if symbol else None
    return SimpleNamespace(data_type=data_type, instrument_id=iid)


def test_tape_trade_round_trip():
    trade = Trade(5, 101.25, 300, "D", (" ", "F"), 77, "C")
    data = tape_data("SPY", trade, 9)
    assert data.ts_event == 5 and data.ts_init == 9 and isinstance(data.data, TapeTrade)
    assert data.data.to_trade() == trade
    assert split_conditions("") == () and split_conditions(" ") == (" ",)
    assert tape_data_type("SPY") == tape_data_type("SPY") != tape_data_type("QQQ")


def test_client_tape_subscriptions_share_the_trades_channel():
    stream = FakeStream()
    client = SimpleNamespace(stream=stream, _tape_symbols=set(), _tick_symbols=set())
    C = dc.AlpacaDataClient

    async def go():
        await C._subscribe(client, command(tape_data_type("SPY")))
        await C._subscribe(client, command(SimpleNamespace(type_name="Other", metadata={})))  # ignored
        await C._subscribe_trades(client, command(symbol="SPY"))
        await C._unsubscribe(client, command(tape_data_type("SPY")))  # trade ticks still wanted
        await C._unsubscribe_trades(client, command(symbol="SPY"))
        await C._subscribe(client, command(tape_data_type("QQQ")))
        await C._unsubscribe(client, command(tape_data_type("QQQ")))
        await C._unsubscribe(client, command(SimpleNamespace(type_name="Other", metadata={})))

    asyncio.run(go())
    assert stream.calls == [
        ("sub", "trades", "SPY"),
        ("sub", "trades", "SPY"),
        ("unsub", "trades", "SPY"),
        ("sub", "trades", "QQQ"),
        ("unsub", "trades", "QQQ"),
    ]
    assert client._tape_symbols == set() and client._tick_symbols == set()
