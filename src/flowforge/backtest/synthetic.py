"""Made-up 1-minute bars for tests and demos. Never use these to judge a strategy."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta

from flowforge.backtest.bars import ET, MinuteBar
from flowforge.data.alpaca_bars import CSV_HEADER


def trading_days(start: date, count: int) -> list[date]:
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def make_day(symbol: str, day: date, price: float, rng: random.Random, minutes: int = 390) -> list[MinuteBar]:
    """One session of bars: a random walk with a random drift, so some days break out."""
    drift = rng.uniform(-0.0004, 0.0006)
    open_dt = datetime.combine(day, time(9, 30), tzinfo=ET)
    bars = []
    for i in range(minutes):
        o = price
        c = max(1.0, o * (1 + drift + rng.gauss(0, 0.0012)))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0004)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, 0.0004)))
        bars.append(
            MinuteBar(symbol, open_dt + timedelta(minutes=i), o, h, lo, c, rng.randint(2_000, 20_000))
        )
        price = c
    return bars


def make_bars(symbol: str, start: date, days: int, price: float = 100.0, seed: int = 7) -> list[MinuteBar]:
    rng = random.Random(f"{symbol}-{seed}")  # noqa: S311 (made-up prices, not security)
    out: list[MinuteBar] = []
    for day in trading_days(start, days):
        out.extend(make_day(symbol, day, price, rng))
        price = out[-1].close
    return out


def write_csv(bars: list[MinuteBar], path) -> None:
    lines = [",".join(CSV_HEADER)]
    lines += [
        f"{b.start.astimezone(UTC):%Y-%m-%dT%H:%M:%SZ},{b.open:.2f},{b.high:.2f},{b.low:.2f},"
        f"{b.close:.2f},{b.volume:.0f}"
        for b in bars
    ]
    path.write_text("\n".join(lines) + "\n")
