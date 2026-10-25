"""Load 1-minute bar CSVs and turn them into Nautilus instruments and bars.

CSV rows carry the bar's *start* time (Alpaca's convention). Nautilus bars are stamped
at their *close* (start + 1 minute) so a strategy never sees a bar before it finished.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nautilus_trader.model import Bar, BarType, Currency, Equity, InstrumentId, Price, Quantity, Symbol

ET = ZoneInfo("America/New_York")
VENUE = "XNAS"  # one simulated venue for all symbols; routing is not modelled
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
ONE_MINUTE = timedelta(minutes=1)
_NS = 1_000_000_000


@dataclass(frozen=True)
class MinuteBar:
    symbol: str
    start: datetime  # timezone-aware, bar open time
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def end(self) -> datetime:
        return self.start + ONE_MINUTE


def read_bars_csv(path: Path, symbol: str) -> list[MinuteBar]:
    with path.open(newline="") as fh:
        return [
            MinuteBar(
                symbol=symbol,
                start=datetime.fromisoformat(row["ts_utc"].replace("Z", "+00:00")),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
            for row in csv.DictReader(fh)
        ]


def regular_hours(bars: Iterable[MinuteBar]) -> list[MinuteBar]:
    """Keep bars that start inside 09:30-16:00 New York time, in time order."""
    kept = [b for b in bars if RTH_OPEN <= b.start.astimezone(ET).time() < RTH_CLOSE]
    return sorted(kept, key=lambda b: b.start)


def session_closes(bars: Iterable[MinuteBar]) -> dict[date, datetime]:
    """Scheduled close per trading day: 13:00 on half days, otherwise 16:00 (ET).

    The schedule is public in advance (exchange holiday calendar), so using it is not
    look-ahead. It's inferred from the last bar of each day across all symbols.
    """
    last: dict[date, time] = {}
    for b in bars:
        local = b.start.astimezone(ET)
        last[local.date()] = max(last.get(local.date(), local.time()), local.time())
    return {
        d: datetime.combine(d, EARLY_CLOSE if t < time(13, 30) else RTH_CLOSE, tzinfo=ET)
        for d, t in last.items()
    }


def instrument_id(symbol: str) -> InstrumentId:
    return InstrumentId.from_str(f"{symbol}.{VENUE}")


def bar_type(symbol: str) -> BarType:
    return BarType.from_str(f"{symbol}.{VENUE}-1-MINUTE-LAST-EXTERNAL")


def make_equity(symbol: str) -> Equity:
    return Equity(
        instrument_id(symbol),
        Symbol(symbol),
        Currency.from_str("USD"),
        2,
        Price.from_str("0.01"),
        0,
        0,
        lot_size=Quantity.from_int(1),
    )


def to_nautilus(bars: Iterable[MinuteBar]) -> list[Bar]:
    out = []
    for b in bars:
        ts = int(b.end.timestamp()) * _NS
        out.append(
            Bar(
                bar_type(b.symbol),
                Price(b.open, 2),
                Price(max(b.high, b.open, b.close), 2),
                Price(min(b.low, b.open, b.close), 2),
                Price(b.close, 2),
                Quantity(b.volume, 0),
                ts,
                ts,
            )
        )
    return out
