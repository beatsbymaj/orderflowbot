"""Deterministic made-up ticks for tests and the offline parity demo. Not market data.

A random walk with an NBBO around it, plus the cases real data showed or the features
must handle: odd lots, off-exchange (D) and sub-penny prints, locked and crossed quotes,
an opening print, average-price and official-close prints, ISO sweeps over several venues,
and a few large prints.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from flowforge.data.ticks import NS, Quote, Trade

ET = ZoneInfo("America/New_York")
VENUES = ("Q", "N", "P", "K", "Z", "V", "D")


def session_open_ns(day: date) -> int:
    return int(datetime.combine(day, time(9, 30), ET).timestamp()) * NS


def make_ticks(
    day: date = date(2026, 10, 23), minutes: int = 40, price: float = 250.0, seed: int = 7
) -> tuple[list[Trade], list[Quote]]:
    rng = random.Random(seed)  # noqa: S311 - made-up test data, not security
    t0 = session_open_ns(day)
    trades: list[Trade] = []
    quotes: list[Quote] = []
    ids: dict[str, int] = {}

    def trade(ts: int, px: float, size: int, venue: str, conds: tuple[str, ...]) -> None:
        ids[venue] = ids.get(venue, 0) + 1
        trades.append(Trade(ts, round(px, 6), size, venue, conds, ids[venue], "C"))

    mid = price
    ts = t0 - 5 * 60 * NS
    while ts < t0:  # pre-open quotes, so the first trade has an NBBO
        quotes.append(Quote(ts, mid - 0.01, 300, "Q", mid + 0.01, 300, "P", ("R",), "C"))
        ts += 20 * NS
    trade(t0 + 1_000, mid, 5_000, "Q", ("O",))  # opening print: no aggressor
    ts = t0 + 2_000_000
    end = t0 + minutes * 60 * NS
    n = 0
    while ts < end:
        n += 1
        mid = round(mid + rng.choice((-0.01, 0.0, 0.0, 0.01)), 2)
        bid, ask = round(mid - 0.01, 2), round(mid + 0.01, 2)
        r = rng.random()
        if r < 0.02:
            ask = bid  # locked
        elif r < 0.03:
            bid, ask = ask, bid  # crossed
        quotes.append(
            Quote(ts, bid, rng.randint(1, 30) * 100, "Q", ask, rng.randint(1, 30) * 100, "P", ("R",), "C")
        )
        # Several trades per quote; the side drifts with the minute so delta has a shape.
        for k in range(rng.randint(1, 4)):
            tts = ts + (k + 1) * 50_000
            buyish = rng.random() < (0.65 if (tts - t0) // (60 * NS) % 4 < 2 else 0.35)
            px = ask if buyish else bid
            size = rng.choice((1, 7, 50, 100, 100, 200, 300, 500, 1_000, 2_000))
            venue = rng.choice(VENUES)
            conds: tuple[str, ...] = ("I",) if size < 100 else (" ",)
            if venue == "D" and rng.random() < 0.3:
                px = round((bid + ask) / 2 + 0.0001, 6)  # sub-penny midpoint print
            if rng.random() < 0.01:
                conds = ("W",)  # average price: off-market, unsided
                px = round(px - 0.37, 2)
            trade(tts, px, size, venue, conds)
        if n % 997 == 0:  # an ISO sweep over four venues, lifting the offer
            for j, venue in enumerate(("Q", "P", "K", "Z")):
                trade(ts + 500_000 + j * 200_000, ask + 0.01 * j, 800, venue, ("F",))
        if n % 1499 == 0:
            trade(ts + 900_000, bid, 15_000, "D", (" ",))  # large print on the bid
        ts += rng.randint(5, 120) * 1_000_000
    trade(end - NS, mid, 1, "Q", ("M",))  # official close: no volume
    trades.sort(key=lambda t: (t.ts, t.key))
    return trades, quotes


def write_ticks_dir(out, symbol: str, day: date, trades: list[Trade], quotes: list[Quote]) -> None:
    """Store sample ticks the way the Phase 2 download does: catalog, raw trades, quality report."""
    import json
    from pathlib import Path

    from nautilus_trader.persistence import ParquetDataCatalog

    from flowforge.data.alpaca_ticks import _write_raw_trades
    from flowforge.data.ticks import instrument_id, make_tick_equity, to_quote_tick, to_trade_tick

    out = Path(out)
    (out / "catalog").mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(out / "catalog"))
    iid = instrument_id(symbol)
    catalog.write_instruments([make_tick_equity(symbol)])
    kept = [to_trade_tick(t, iid) for t in trades if not t.dropped]
    catalog.write_trade_ticks(kept, start=kept[0].ts_event, end=kept[-1].ts_event)
    ticks = [to_quote_tick(q, iid) for q in quotes]
    catalog.write_quote_ticks(ticks, start=ticks[0].ts_event, end=ticks[-1].ts_event)
    _write_raw_trades(out / "raw" / symbol / day.isoformat() / "trades_0930.csv.gz", trades)
    open_ns = session_open_ns(day)
    report = {
        "session": {
            "open": datetime.fromtimestamp(open_ns / NS, ET).isoformat(timespec="seconds"),
            "close": datetime.combine(day, time(16, 0), ET).isoformat(timespec="seconds"),
        },
        "gaps": [],
    }
    (out / "quality").mkdir(parents=True, exist_ok=True)
    (out / "quality" / f"{symbol}_{day.isoformat()}.json").write_text(json.dumps(report))
