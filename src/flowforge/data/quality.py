"""Data-quality checks and gap detection for downloaded ticks.

Gap rule: Alpaca builds minute bars from the same SIP trades. A bar's trade count `n`
counts every trade except those whose conditions don't update volume (M official close,
Q official open, 9 corrected consolidated close; Alpaca market-data FAQ, checked
2026-10-25). So for each minute, trades we hold that update volume should equal `n`.
Fewer means we're missing trades: that minute is a gap.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from flowforge.data.ticks import NS, Quote, Trade

MINUTE_NS = 60 * NS
NO_VOLUME_CONDITIONS = frozenset({"M", "Q", "9"})


@dataclass(frozen=True)
class MinuteBar:
    """The two bar fields the gap check needs: trade count and volume."""

    start: int  # ns, minute start
    trades: int
    volume: int


def minute_of(ts: int) -> int:
    return ts - ts % MINUTE_NS


def counts_toward_bar(trade: Trade) -> bool:
    return not trade.dropped and not NO_VOLUME_CONDITIONS.intersection(trade.conditions)


def minute_counts(trades: Iterable[Trade]) -> dict[int, tuple[int, int]]:
    """Per minute: (trade count, volume) of trades that count toward Alpaca's bars."""
    out: dict[int, list[int]] = {}
    for t in trades:
        if counts_toward_bar(t):
            row = out.setdefault(minute_of(t.ts), [0, 0])
            row[0] += 1
            row[1] += t.size
    return {m: (c, v) for m, (c, v) in out.items()}


@dataclass
class BarComparison:
    minutes_checked: int = 0
    minutes_matched: int = 0
    gap_minutes: list[int] = field(default_factory=list)  # we hold fewer trades than the bar
    extra_minutes: list[int] = field(default_factory=list)  # we hold more (e.g. late cancels)
    missing_trades: int = 0
    tick_volume: int = 0
    bar_volume: int = 0


def compare_with_bars(
    trades: Iterable[Trade], bars: Iterable[MinuteBar], start: int, end: int
) -> BarComparison:
    """Compare trades against minute bars for minutes in [start, end)."""
    counts = minute_counts(trades)
    bar_by_minute = {b.start: b for b in bars if start <= b.start < end}
    result = BarComparison()
    for minute in sorted(set(bar_by_minute) | {m for m in counts if start <= m < end}):
        bar = bar_by_minute.get(minute)
        want = bar.trades if bar else 0
        have, volume = counts.get(minute, (0, 0))
        result.minutes_checked += 1
        result.tick_volume += volume
        result.bar_volume += bar.volume if bar else 0
        if have == want:
            result.minutes_matched += 1
        elif have < want:
            result.gap_minutes.append(minute)
            result.missing_trades += want - have
        else:
            result.extra_minutes.append(minute)
    return result


def merge_minutes(minutes: Iterable[int]) -> list[tuple[int, int]]:
    """Join gap minutes into [start, end) ranges."""
    ranges: list[list[int]] = []
    for m in sorted(set(minutes)):
        if ranges and ranges[-1][1] == m:
            ranges[-1][1] = m + MINUTE_NS
        else:
            ranges.append([m, m + MINUTE_NS])
    return [(a, b) for a, b in ranges]


def merge_trades(*batches: Iterable[Trade]) -> list[Trade]:
    """Union of trade batches, one copy per (exchange, id), in time order."""
    seen: dict[tuple[str, int], Trade] = {}
    for batch in batches:
        for t in batch:
            seen.setdefault(t.key, t)
    return sorted(seen.values(), key=lambda t: t.ts)


def trade_checks(trades: Sequence[Trade]) -> dict:
    keys = Counter(t.key for t in trades)
    updates = Counter(t.update for t in trades if t.update)
    conditions = Counter(c for t in trades for c in t.conditions)
    return {
        "count": len(trades),
        "in_catalog": sum(1 for t in trades if not t.dropped),
        "duplicate_ids": sum(n - 1 for n in keys.values() if n > 1),
        "out_of_order": _out_of_order(t.ts for t in trades),
        "bad_price": sum(1 for t in trades if t.price <= 0),
        "zero_size": sum(1 for t in trades if t.size <= 0),
        "sub_penny": sum(1 for t in trades if round(t.price * 100, 6) % 1 != 0),
        "updates": dict(updates),
        "conditions": dict(conditions.most_common(15)),
    }


def quote_checks(quotes: Sequence[Quote], start: int, end: int, max_gap_ns: int) -> dict:
    """Crossed/locked/empty NBBO counts and silences longer than max_gap_ns within [start, end)."""
    gaps = []
    previous = start
    for q in quotes:
        if q.ts > previous and q.ts - previous > max_gap_ns:
            gaps.append((previous, q.ts))
        previous = max(previous, q.ts)
    if quotes and end - previous > max_gap_ns:
        gaps.append((previous, end))
    if not quotes:
        gaps.append((start, end))
    both = [q for q in quotes if q.bid_price > 0 and q.ask_price > 0]
    return {
        "count": len(quotes),
        "out_of_order": _out_of_order(q.ts for q in quotes),
        "crossed": sum(1 for q in both if q.bid_price > q.ask_price),
        "locked": sum(1 for q in both if q.bid_price == q.ask_price),
        "no_bid": sum(1 for q in quotes if q.bid_price <= 0),
        "no_ask": sum(1 for q in quotes if q.ask_price <= 0),
        "silences": gaps,
    }


def _out_of_order(stamps: Iterable[int]) -> int:
    bad = 0
    last = None
    for ts in stamps:
        if last is not None and ts < last:
            bad += 1
        last = ts if last is None else max(last, ts)
    return bad


def add_counts(total: dict, part: dict) -> dict:
    """Sum two check dicts (numbers add, nested counters add, lists concatenate)."""
    out = dict(total)
    for k, v in part.items():
        if isinstance(v, dict):
            merged = Counter(out.get(k, {}))
            merged.update(v)
            out[k] = dict(merged)
        elif isinstance(v, list):
            out[k] = list(out.get(k, [])) + v
        else:
            out[k] = out.get(k, 0) + v
    return out
