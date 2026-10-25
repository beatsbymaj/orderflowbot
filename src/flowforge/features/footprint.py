"""One footprint bar: Bid x Ask volume per price row, plus tape and NBBO statistics.

Book terms (Trader Dale, project reference): the Ask column is aggressive buyers (and
passive sellers), the Bid column aggressive sellers (and passive buyers). Delta = Ask - Bid.
Prices are integer micro-dollars; rows are price // row height.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

from flowforge.features.classify import Side
from flowforge.features.config import FeatureConfig

BID, ASK, NONE = 0, 1, 2


@dataclass
class QuoteClock:
    """Time-weighted NBBO statistics over one bar."""

    t: int
    bid: int = 0
    ask: int = 0
    bid_size: int = 0
    ask_size: int = 0
    updates: int = 0
    mid_ups: int = 0
    mid_downs: int = 0
    ok_ns: int = 0
    locked_ns: int = 0
    crossed_ns: int = 0
    missing_ns: int = 0
    spread_ns: int = 0  # sum of spread (micro-dollars) x ns, while the NBBO is valid
    spread_bps_ns: float = 0.0
    size_imbalance_ns: float = 0.0
    first_mid: int = 0
    last_mid: int = 0

    def advance(self, ts: int) -> None:
        dt = ts - self.t
        if dt <= 0:
            return
        if self.bid <= 0 or self.ask <= 0:
            self.missing_ns += dt
        elif self.ask == self.bid:
            self.locked_ns += dt
        elif self.ask < self.bid:
            self.crossed_ns += dt
        else:
            self.ok_ns += dt
            spread = self.ask - self.bid
            self.spread_ns += spread * dt
            self.spread_bps_ns += spread * 20_000 / (self.ask + self.bid) * dt
            sizes = self.bid_size + self.ask_size
            if sizes:
                self.size_imbalance_ns += (self.bid_size - self.ask_size) / sizes * dt
        self.t = ts

    def update(self, ts: int, bid: int, ask: int, bid_size: int, ask_size: int) -> None:
        self.advance(ts)
        self.updates += 1
        if bid > 0 and ask > 0:
            mid2 = bid + ask
            if self.last_mid:
                if mid2 > self.last_mid:
                    self.mid_ups += 1
                elif mid2 < self.last_mid:
                    self.mid_downs += 1
            if not self.first_mid:
                self.first_mid = mid2
            self.last_mid = mid2
        self.bid, self.ask, self.bid_size, self.ask_size = bid, ask, bid_size, ask_size

    def carry(self, start: int) -> QuoteClock:
        """A fresh clock for the next bar that starts from this bar's last NBBO."""
        mid = self.last_mid
        return QuoteClock(
            start, self.bid, self.ask, self.bid_size, self.ask_size, first_mid=mid, last_mid=mid
        )

    def summary(self) -> dict:
        span = self.ok_ns + self.locked_ns + self.crossed_ns + self.missing_ns
        return {
            "updates": self.updates,
            "mid_ups": self.mid_ups,
            "mid_downs": self.mid_downs,
            "mid_change": _px((self.last_mid - self.first_mid) / 2) if self.first_mid else None,
            "spread_avg": _px(self.spread_ns / self.ok_ns) if self.ok_ns else None,
            "spread_bps_avg": round(self.spread_bps_ns / self.ok_ns, 3) if self.ok_ns else None,
            "size_imbalance_avg": round(self.size_imbalance_ns / self.ok_ns, 4) if self.ok_ns else None,
            "locked_share": round(self.locked_ns / span, 4) if span else None,
            "crossed_share": round(self.crossed_ns / span, 4) if span else None,
            "missing_share": round(self.missing_ns / span, 4) if span else None,
        }


def _px(u: float) -> float:
    return round(u / 1_000_000, 6)


@dataclass
class History:
    """What later bars of the same timeframe need from finished ones (trades > 0 only)."""

    size: int
    cells: deque = field(default_factory=deque)  # (sum of non-empty Bid/Ask cells, count)
    ranges: deque = field(default_factory=deque)  # rows from low to high
    highs: deque = field(default_factory=deque)
    lows: deque = field(default_factory=deque)
    cvds: deque = field(default_factory=deque)
    trades: deque = field(default_factory=deque)

    def avg_cell(self) -> float | None:
        n = sum(c for _, c in self.cells)
        return sum(s for s, _ in self.cells) / n if n else None

    def push(self, bar: Bar, cells: tuple[int, int], cvd: int) -> None:
        for q, v in (
            (self.cells, cells),
            (self.ranges, bar.high_row - bar.low_row + 1),
            (self.highs, bar.high),
            (self.lows, bar.low),
            (self.cvds, cvd),
            (self.trades, bar.trades),
        ):
            q.append(v)
            if len(q) > self.size:
                q.popleft()


@dataclass
class Bar:
    start: int
    end: int
    quotes: QuoteClock
    row: int = 0  # row height, micro-dollars; set by the first market-priced trade
    rows: dict[int, list[int]] = field(default_factory=dict)
    open: int = 0
    high: int = 0
    low: int = 0
    close: int = 0
    volume: int = 0
    trades: int = 0
    buy: int = 0
    sell: int = 0
    unsided: int = 0
    off_exchange: int = 0
    iso: int = 0
    odd_lot: int = 0
    methods: dict[str, int] = field(default_factory=dict)
    per_second: dict[int, int] = field(default_factory=dict)
    large_prints: int = 0
    large_print_volume: int = 0
    sweeps: int = 0
    sweep_volume: int = 0
    gap: bool = False

    @property
    def high_row(self) -> int:
        return max(self.rows) if self.rows else 0

    @property
    def low_row(self) -> int:
        return min(self.rows) if self.rows else 0

    def add(self, ts: int, price: int, size: int, side: Side, method: str, flags: dict) -> None:
        self.volume += size
        self.trades += 1
        self.methods[method] = self.methods.get(method, 0) + 1
        sec = ts // 1_000_000_000
        self.per_second[sec] = self.per_second.get(sec, 0) + 1
        if flags["off_exchange"]:
            self.off_exchange += size
        if flags["iso"]:
            self.iso += size
        if flags["odd_lot"]:
            self.odd_lot += size
        if side is Side.BUY:
            self.buy += size
        elif side is Side.SELL:
            self.sell += size
        else:
            self.unsided += size
        if not flags["market_priced"]:
            return
        if not self.open:
            self.open = self.high = self.low = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        cell = self.rows.setdefault(price // self.row, [0, 0, 0])
        cell[ASK if side is Side.BUY else BID if side is Side.SELL else NONE] += size

    # -- finished-bar analysis ----------------------------------------------------------
    def analyse(self, cfg: FeatureConfig, hist: History, cvd: int) -> tuple[dict, tuple[int, int]]:
        rows = self.rows
        cells = [v for r in rows.values() for v in r[:2] if v]
        avg_cell = hist.avg_cell()
        ordered = sorted(rows)
        poc = max(ordered, key=lambda r: (sum(rows[r]), -r)) if rows else None
        buys, sells = self._imbalances(cfg)
        out: dict = {
            "poc": self._row_px(poc) if poc is not None else None,
            "imbalance_buy": [self._row_px(r) for r in buys],
            "imbalance_sell": [self._row_px(r) for r in sells],
            "stacked_buy": [[self._row_px(a), self._row_px(b)] for a, b in _runs(buys, cfg.stacked_min)],
            "stacked_sell": [[self._row_px(a), self._row_px(b)] for a, b in _runs(sells, cfg.stacked_min)],
            "unfinished_high": bool(rows) and rows[self.high_row][BID] > 0,
            "unfinished_low": bool(rows) and rows[self.low_row][ASK] > 0,
            "avg_cell": round(avg_cell, 2) if avg_cell is not None else None,
            "big_bid": [],
            "big_ask": [],
            "absorption": [],
            "exhaustion": 0,
        }
        if avg_cell is not None and rows:
            big = cfg.big_cell_multiple * avg_cell
            heavy = cfg.heavy_cell_multiple * avg_cell
            out["big_bid"] = [self._row_px(r) for r in ordered if rows[r][BID] >= big]
            out["big_ask"] = [self._row_px(r) for r in ordered if rows[r][ASK] >= big]
            stalled = (self.high_row - self.low_row + 1) <= statistics.median(hist.ranges)
            if stalled:
                out["absorption"] = [
                    self._row_px(r) for r in ordered if rows[r][BID] >= heavy and rows[r][ASK] >= heavy
                ]
        if rows and hist.highs:
            look = cfg.extreme_lookback
            highs, lows, cvds = list(hist.highs)[-look:], list(hist.lows)[-look:], list(hist.cvds)[-look:]
            mean_row = sum(r[BID] + r[ASK] for r in rows.values()) / len(rows)
            thin = cfg.exhaustion_fraction * mean_row
            new_high, new_low = self.high > max(highs), self.low < min(lows)
            if new_high and rows[self.high_row][ASK] <= thin:
                out["exhaustion"] = -1  # buyers ran out at a new high
            elif new_low and rows[self.low_row][BID] <= thin:
                out["exhaustion"] = 1  # sellers ran out at a new low
            out["cvd_divergence"] = (-1 if new_high and cvd < max(cvds) else 0) + (
                1 if new_low and cvd > min(cvds) else 0
            )
        else:
            out["cvd_divergence"] = 0
        return out, (sum(cells), len(cells))

    def _row_px(self, row: int) -> float:
        return _px(row * self.row)

    def _imbalances(self, cfg: FeatureConfig) -> tuple[list[int], list[int]]:
        """Diagonal (book): Ask at row P vs Bid at P-1; Bid at P vs Ask at P+1."""
        rows, ratio, floor = self.rows, cfg.imbalance_ratio, cfg.imbalance_min_shares
        buys, sells = [], []
        for r in sorted(rows):
            ask = rows[r][ASK]
            below = rows.get(r - 1, (0, 0, 0))[BID]
            if ask >= floor and ask >= ratio * below:
                buys.append(r)
            bid = rows[r][BID]
            above = rows.get(r + 1, (0, 0, 0))[ASK]
            if bid >= floor and bid >= ratio * above:
                sells.append(r)
        return buys, sells

    def summary(self, cvd: int, hist: History) -> dict:
        seconds = (self.end - self.start) / 1_000_000_000
        mean_trades = sum(hist.trades) / len(hist.trades) if hist.trades else None
        return {
            "open": _px(self.open) if self.trades else None,
            "high": _px(self.high) if self.rows else None,
            "low": _px(self.low) if self.rows else None,
            "close": _px(self.close) if self.rows else None,
            "row": _px(self.row) if self.row else None,
            "volume": self.volume,
            "trades": self.trades,
            "buy": self.buy,
            "sell": self.sell,
            "unsided": self.unsided,
            "delta": self.buy - self.sell,
            "cvd": cvd,
            "delta_divergence": (-1 if self.close > self.open and self.buy < self.sell else 0)
            + (1 if self.close < self.open and self.buy > self.sell else 0),
            "methods": dict(sorted(self.methods.items())),
            "off_exchange_share": round(self.off_exchange / self.volume, 4) if self.volume else None,
            "iso_share": round(self.iso / self.volume, 4) if self.volume else None,
            "odd_lot_share": round(self.odd_lot / self.volume, 4) if self.volume else None,
            "trades_per_s": round(self.trades / seconds, 3),
            "shares_per_s": round(self.volume / seconds, 1),
            "max_trades_1s": max(self.per_second.values(), default=0),
            "speed_ratio": round(self.trades / mean_trades, 3) if mean_trades else None,
            "large_prints": self.large_prints,
            "large_print_volume": self.large_print_volume,
            "sweeps": self.sweeps,
            "sweep_volume": self.sweep_volume,
            "gap": self.gap,
            "footprint": {
                f"{_px(r * self.row):.6f}": self.rows[r] for r in sorted(self.rows)
            },  # [bid, ask, unsided]
        }


def _runs(rows: list[int], min_len: int) -> list[tuple[int, int]]:
    """Runs of >= min_len consecutive rows, as (first, last)."""
    out = []
    start = prev = None
    for r in rows:
        if prev is not None and r == prev + 1:
            prev = r
            continue
        if start is not None and prev - start + 1 >= min_len:
            out.append((start, prev))
        start = prev = r
    if start is not None and prev - start + 1 >= min_len:
        out.append((start, prev))
    return out
