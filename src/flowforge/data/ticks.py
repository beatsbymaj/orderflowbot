"""Alpaca tick records (trades and NBBO quotes) and their Nautilus forms.

Field meanings are from Alpaca's API reference (checked 2026-10-25,
docs/research/10-alpaca-tick-history.md):
- trade: t (RFC-3339, ns), x exchange, p price, s size, c conditions, i exchange trade id,
  z tape, u optional update ("canceled", "incorrect", "corrected")
- quote: t, ax/ap/as ask exchange/price/size, bx/bp/bs bid, c conditions, z tape.
  Quote sizes are shares from 2025-11-03; before that they were round lots.

TradeTick has no fields for venue or conditions, so the trade id is "<exchange>-<id>"
(unique: Alpaca ids are per exchange) and the full record is kept in the raw trade files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from nautilus_trader.model import (
    AggressorSide,
    Currency,
    Equity,
    InstrumentId,
    Price,
    Quantity,
    QuoteTick,
    Symbol,
    TradeId,
    TradeTick,
)

VENUE = "XNAS"  # same simulated venue as the bar backtest; routing is not modelled
# Off-exchange prints can be sub-penny (e.g. midpoint 100.005), so ticks keep 6 decimals.
PRICE_PRECISION = 6
NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# Alpaca switched quote sizes from round lots to shares on this date (API reference).
QUOTE_SIZES_IN_SHARES_FROM = date(2025, 11, 3)
ROUND_LOT = 100  # pre-2025-11-03 round lot for the stocks we trade; see ADR-0010
DROPPED_UPDATES = frozenset({"canceled", "incorrect"})


def parse_ts_ns(text: str) -> int:
    """RFC-3339 timestamp (Z or +hh:mm, up to 9 fraction digits) to integer ns since epoch."""
    offset = timedelta(0)
    if text.endswith("Z"):
        body = text[:-1]
    elif len(text) > 6 and text[-6] in "+-" and text[-3] == ":":
        body = text[:-6]
        sign = 1 if text[-6] == "+" else -1
        offset = sign * timedelta(hours=int(text[-5:-3]), minutes=int(text[-2:]))
    else:
        raise ValueError(f"timestamp without a time zone: {text!r}")
    frac_ns = 0
    if "." in body:
        body, frac = body.split(".", 1)
        if not (frac.isdigit() and len(frac) <= 9):
            raise ValueError(f"bad fractional seconds in {text!r}")
        frac_ns = int(frac.ljust(9, "0"))
    whole = datetime.fromisoformat(body).replace(tzinfo=UTC) - offset
    return (whole - EPOCH) // timedelta(seconds=1) * NS + frac_ns


def format_ts_ns(ts: int) -> str:
    seconds, frac = divmod(ts, NS)
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:09d}Z"


@dataclass(frozen=True, slots=True)
class Trade:
    ts: int
    price: float
    size: int
    exchange: str
    conditions: tuple[str, ...]
    exchange_id: int
    tape: str
    update: str = ""

    @classmethod
    def from_alpaca(cls, raw: dict) -> Trade:
        return cls(
            ts=parse_ts_ns(raw["t"]),
            price=float(raw["p"]),
            size=int(raw["s"]),
            exchange=raw["x"],
            conditions=tuple(raw.get("c") or ()),
            exchange_id=int(raw["i"]),
            tape=raw.get("z", ""),
            update=raw.get("u", "") or "",
        )

    @property
    def key(self) -> tuple[str, int]:
        return (self.exchange, self.exchange_id)

    @property
    def trade_id(self) -> str:
        return f"{self.exchange}-{self.exchange_id}"

    @property
    def dropped(self) -> bool:
        """Canceled or flagged incorrect by the exchange: kept in raw files, not in the catalog."""
        return self.update in DROPPED_UPDATES


@dataclass(frozen=True, slots=True)
class Quote:
    ts: int
    bid_price: float
    bid_size: int
    bid_exchange: str
    ask_price: float
    ask_size: int
    ask_exchange: str
    conditions: tuple[str, ...]
    tape: str

    @classmethod
    def from_alpaca(cls, raw: dict, size_multiplier: int = 1) -> Quote:
        return cls(
            ts=parse_ts_ns(raw["t"]),
            bid_price=float(raw["bp"]),
            bid_size=int(raw["bs"]) * size_multiplier,
            bid_exchange=raw.get("bx", ""),
            ask_price=float(raw["ap"]),
            ask_size=int(raw["as"]) * size_multiplier,
            ask_exchange=raw.get("ax", ""),
            conditions=tuple(raw.get("c") or ()),
            tape=raw.get("z", ""),
        )


def quote_size_multiplier(day: date) -> int:
    """Turn pre-2025-11-03 round-lot quote sizes into shares."""
    return 1 if day >= QUOTE_SIZES_IN_SHARES_FROM else ROUND_LOT


def instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId.from_str(f"{symbol}.{VENUE}")


def make_tick_equity(symbol: str) -> Equity:
    return Equity(
        instrument_id(symbol),
        Symbol(symbol),
        Currency.from_str("USD"),
        PRICE_PRECISION,
        Price.from_str("0.000001"),
        0,
        0,
        lot_size=Quantity.from_int(1),
    )


def to_trade_tick(trade: Trade, iid: InstrumentId, ts_init: int | None = None) -> TradeTick:
    # Aggressor side is decided later by the Phase 3 classifier, never guessed here.
    return TradeTick(
        iid,
        Price(trade.price, PRICE_PRECISION),
        Quantity(trade.size, 0),
        AggressorSide.NO_AGGRESSOR,
        TradeId(trade.trade_id),
        trade.ts,
        trade.ts if ts_init is None else ts_init,
    )


def to_quote_tick(quote: Quote, iid: InstrumentId, ts_init: int | None = None) -> QuoteTick:
    return QuoteTick(
        iid,
        Price(quote.bid_price, PRICE_PRECISION),
        Price(quote.ask_price, PRICE_PRECISION),
        Quantity(quote.bid_size, 0),
        Quantity(quote.ask_size, 0),
        quote.ts,
        quote.ts if ts_init is None else ts_init,
    )
