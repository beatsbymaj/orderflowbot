"""Session context: VWAP, volume profile, opening range, prior day, and price levels.

Levels come from the book's zones: stacked imbalances, multiple nodes (same high-volume
row in 2+ consecutive bars), unfinished business, absorption, and large prints. Each level
counts how many whole bars stayed away from it and how many times price came back, so a
strategy can apply the book's "left by 1-2 bars, trade the first pullback" rule.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from flowforge.data.ticks import format_ts_ns
from flowforge.features.footprint import _px


@dataclass(frozen=True)
class PriorDay:
    high: float
    low: float
    close: float
    volume: int
    large_print_usd: float | None = None  # Nth-largest print notional (trades filter)
    sweep_usd: float | None = None  # Nth-largest ISO sweep notional


@dataclass
class Level:
    kind: str
    tf: int
    low: int
    high: int
    side: int  # +1 support-ish (buyers), -1 resistance-ish (sellers), 0 neither
    created: int
    away_bars: int = 0
    touches: int = 0
    active: bool = True

    def update(self, bar_low: int, bar_high: int) -> None:
        """One finished bar of this level's timeframe, after the level was made."""
        if bar_low <= self.high and bar_high >= self.low:
            if self.kind == "unfinished":
                self.active = False  # revisited: the auction is finished
            elif self.away_bars:
                self.touches += 1
            self.away_bars = 0
        else:
            self.away_bars += 1

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "tf": self.tf,
            "low": _px(self.low),
            "high": _px(self.high),
            "side": self.side,
            "created": format_ts_ns(self.created),
            "away_bars": self.away_bars,
            "touches": self.touches,
        }


@dataclass
class Session:
    day: str
    open_ns: int  # 09:30 ET
    or_end_ns: int
    prior: PriorDay | None
    max_levels: int
    value_area: float
    row: int = 0
    profile: dict[int, int] = field(default_factory=dict)
    pv: int = 0
    p2v: int = 0
    v: int = 0
    high: int = 0
    low: int = 0
    or_high: int = 0
    or_low: int = 0
    cvd: int = 0
    levels: list[Level] = field(default_factory=list)
    last_pocs: dict[int, deque] = field(default_factory=dict)

    def add_trade(self, ts: int, price: int, size: int) -> None:
        """A market-priced trade (signed delta is added separately)."""
        self.profile[price // self.row] = self.profile.get(price // self.row, 0) + size
        self.pv += price * size
        self.p2v += price * price * size
        self.v += size
        self.high = max(self.high, price)
        self.low = min(self.low, price) if self.low else price
        if self.open_ns <= ts < self.or_end_ns:
            self.or_high = max(self.or_high, price)
            self.or_low = min(self.or_low, price) if self.or_low else price

    def add_level(self, level: Level) -> None:
        self.levels.append(level)

    def on_bar(self, tf: int, bar_low: int, bar_high: int, poc_row: int | None, end: int) -> None:
        """Update levels of this timeframe, then look for a multiple node."""
        if bar_high:
            for level in self.levels:
                if level.tf == tf and level.active and level.created < end:
                    level.update(bar_low, bar_high)
        pocs = self.last_pocs.setdefault(tf, deque(maxlen=2))
        pocs.append(poc_row)
        if len(pocs) == 2 and pocs[0] is not None and pocs[0] == pocs[1]:
            row = pocs[1]
            for level in self.levels:
                if level.kind == "multiple_node" and level.tf == tf and level.low == row * self.row:
                    break
            else:
                self.add_level(Level("multiple_node", tf, row * self.row, (row + 1) * self.row - 1, 0, end))

    def value_area_rows(self) -> tuple[int, int, int] | None:
        """POC, VAL, VAH rows: grow from the POC toward the heavier neighbour until 70%."""
        if not self.profile:
            return None
        rows = sorted(self.profile)
        vols = [self.profile[r] for r in rows]
        poc_i = max(range(len(rows)), key=lambda i: (vols[i], -i))
        lo = hi = poc_i
        total, target = vols[poc_i], self.value_area * sum(vols)
        while total < target and (lo > 0 or hi < len(rows) - 1):
            down = vols[lo - 1] if lo > 0 else -1
            up = vols[hi + 1] if hi < len(rows) - 1 else -1
            if up >= down:
                hi += 1
                total += vols[hi]
            else:
                lo -= 1
                total += vols[lo]
        return rows[poc_i], rows[lo], rows[hi]

    def snapshot(self, now: int) -> dict:
        out: dict = {"day": self.day, "session_high": None, "session_low": None, "vwap": None}
        if self.v:
            vwap = self.pv / self.v
            sigma = math.sqrt(max(self.p2v / self.v - vwap * vwap, 0.0))
            out |= {
                "session_high": _px(self.high),
                "session_low": _px(self.low),
                "vwap": _px(vwap),
                "vwap_sigma": _px(sigma),
            }
            poc, val, vah = self.value_area_rows()
            out |= {
                "poc": _px(poc * self.row),
                "val": _px(val * self.row),
                "vah": _px((vah + 1) * self.row),
            }
        out["opening_range"] = (
            {"high": _px(self.or_high), "low": _px(self.or_low), "complete": now >= self.or_end_ns}
            if self.or_high
            else None
        )
        out["prior_day"] = (
            {"high": self.prior.high, "low": self.prior.low, "close": self.prior.close}
            if self.prior
            else None
        )
        by_kind: dict[str, list[dict]] = {}
        for level in self.levels:
            if level.active:
                by_kind.setdefault(level.kind, []).append(level.as_dict())
        out["levels"] = {k: v[-self.max_levels :] for k, v in sorted(by_kind.items())}
        return out
