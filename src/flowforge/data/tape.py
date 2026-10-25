"""Full Alpaca trades on the Nautilus message bus.

Nautilus `TradeTick` has no venue or sale-condition fields, and the Phase 3 features need
both (ISO sweeps, off-exchange share, which prints get an aggressor side). So the live
Alpaca client also publishes each trade as custom data, `TapeTrade`, to actors that
subscribe to `tape_data_type(symbol)`. In replay the same fields come from the raw trade
files, joined on trade id (ADR-0010).
"""

from __future__ import annotations

from nautilus_trader.model import CustomData, DataType
from nautilus_trader.model.custom import customdataclass

from flowforge.data.ticks import Trade

TAPE_TRADE = "TapeTrade"


@customdataclass
class TapeTrade:
    symbol: str = ""
    price: float = 0.0
    size: int = 0
    exchange: str = ""
    conditions: str = ""  # codes joined with "|", as in the raw trade files
    exchange_id: int = 0
    tape: str = ""

    def to_trade(self) -> Trade:
        return Trade(
            ts=self.ts_event,
            price=self.price,
            size=self.size,
            exchange=self.exchange,
            conditions=split_conditions(self.conditions),
            exchange_id=self.exchange_id,
            tape=self.tape,
        )


def split_conditions(text: str) -> tuple[str, ...]:
    return tuple(text.split("|")) if text else ()


def tape_data_type(symbol: str) -> DataType:
    return DataType(TAPE_TRADE, metadata={"symbol": symbol})


def tape_data(symbol: str, trade: Trade, ts_init: int) -> CustomData:
    record = TapeTrade(
        trade.ts,
        ts_init,
        symbol,
        trade.price,
        trade.size,
        trade.exchange,
        "|".join(trade.conditions),
        trade.exchange_id,
        trade.tape,
    )
    return CustomData(tape_data_type(symbol), record)
