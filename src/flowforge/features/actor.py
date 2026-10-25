"""Nautilus actor that runs the feature engine inside a node (live or backtest).

- Live: subscribes to NBBO quotes and to TapeTrade custom data from the Alpaca client
  (full trades with venue and conditions).
- Backtest: subscribes to quotes and trade ticks; venue and conditions are looked up by
  trade id in the raw trade files (`conditions` mapping), as ADR-0010 set up.

Read-only: the actor never sends orders. Records go to `on_record` and, if given, a
JSON-lines file.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nautilus_trader.common import DataActor
from nautilus_trader.model import ClientId

from flowforge.data.tape import tape_data_type
from flowforge.data.ticks import Quote, Trade, instrument_id
from flowforge.features.config import FeatureConfig
from flowforge.features.engine import FeatureEngine

# trade id -> (exchange, conditions, exchange id, tape)
ConditionsLookup = Mapping[str, tuple[str, tuple[str, ...], int, str]]


class FeatureActor(DataActor):
    def __new__(cls, *args: Any, **kwargs: Any) -> FeatureActor:
        return super().__new__(cls)  # the Rust base takes only an optional config

    def __init__(
        self,
        symbols: list[str],
        config: FeatureConfig | None = None,
        conditions: ConditionsLookup | None = None,
        client_id: str = "ALPACA",
        out_path: Path | None = None,
        on_record: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__()
        self.symbols = symbols
        self.conditions = conditions
        self.client_id = client_id
        self.out_path = out_path
        self._fh = None
        self._on_record = on_record
        self.received = {"trades": 0, "quotes": 0}
        self.missing_conditions = 0
        self.engine = FeatureEngine(config, self._emit)

    def _emit(self, record: dict) -> None:
        if self._fh is not None:
            self._fh.write(json.dumps(record) + "\n")
        if self._on_record is not None:
            self._on_record(record)

    def on_start(self) -> None:
        if self.out_path is not None:
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.out_path.open("w", encoding="utf-8")
        for symbol in self.symbols:
            iid = instrument_id(symbol)
            self.subscribe_quotes(iid)
            if self.conditions is None:
                self.subscribe_data(tape_data_type(symbol), ClientId(self.client_id))
            else:
                self.subscribe_trades(iid)

    def on_stop(self) -> None:
        self.engine.flush()
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def on_quote(self, quote: Any) -> None:
        self.received["quotes"] += 1
        self.engine.on_quote(quote.instrument_id.symbol.value, quote_from_tick(quote))

    def on_trade(self, tick: Any) -> None:
        """Backtest path: a TradeTick plus its raw-file record."""
        self.received["trades"] += 1
        trade, found = trade_from_tick(tick, self.conditions)
        self.missing_conditions += not found
        self.engine.on_trade(tick.instrument_id.symbol.value, trade)

    def on_data(self, data: Any) -> None:
        """Live path: TapeTrade custom data from the Alpaca client."""
        record = getattr(data, "data", data)
        self.received["trades"] += 1
        self.engine.on_trade(record.symbol, record.to_trade())


def quote_from_tick(quote: Any) -> Quote:
    return Quote(
        ts=quote.ts_event,
        bid_price=quote.bid_price.as_double(),
        bid_size=int(quote.bid_size.as_double()),
        bid_exchange="",
        ask_price=quote.ask_price.as_double(),
        ask_size=int(quote.ask_size.as_double()),
        ask_exchange="",
        conditions=(),
        tape="",
    )


def trade_from_tick(tick: Any, conditions: ConditionsLookup) -> tuple[Trade, bool]:
    """Rebuild the full trade from a TradeTick and the raw-file lookup. If the trade id is
    missing from the lookup, the venue still comes from the id and conditions are empty."""
    trade_id = tick.trade_id.value
    found = conditions.get(trade_id)
    if found is None:
        exchange, ident = trade_id.split("-", 1)
        exchange, conds, exchange_id, tape = exchange, (), int(ident), ""
    else:
        exchange, conds, exchange_id, tape = found
    price, size = tick.price.as_double(), int(tick.size.as_double())
    return Trade(tick.ts_event, price, size, exchange, conds, exchange_id, tape), found is not None
