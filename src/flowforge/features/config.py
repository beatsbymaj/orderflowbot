"""Feature settings. Every threshold here is a starting hypothesis, not a tested value.

The Trader Dale book (project reference) gives the shape of each rule; the numbers are
ours until Phase 5 walk-forward tests say otherwise. Change them by commit, like code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

USD = 1_000_000  # prices are integer micro-dollars inside the engine
NICE_ROWS = (10_000, 20_000, 50_000, 100_000, 250_000, 500_000, 1_000_000)  # $0.01 ... $1.00


@dataclass(frozen=True)
class FeatureConfig:
    # Footprint bars in seconds: 1-min for CumDelta, 5-min for entries, 30-min for levels (book).
    bar_seconds: tuple[int, ...] = (60, 300, 1800)
    # Events are held this long (exchange time) so quotes and trades from separate channels
    # are processed in timestamp order. Live latency cost = this window.
    reorder_window_ms: int = 50
    # Footprint row height: about this many basis points of the day's first price, rounded
    # up to a NICE_ROWS step (SPY ~$670 -> $0.10, AAPL ~$250 -> $0.05). Overrides in USD.
    row_bps: float = 1.0
    row_usd: dict[str, float] = field(default_factory=dict)
    # Diagonal imbalance (book): Ask at P vs Bid one row lower, >= 300%; 3+ stacked.
    imbalance_ratio: float = 3.0
    imbalance_min_shares: int = 1_000
    stacked_min: int = 3
    # "Unusually heavy" cell = this many times the average non-empty cell of the last N bars.
    lookback_bars: int = 20
    heavy_cell_multiple: float = 3.0  # absorption: both Bid and Ask heavy at one row
    big_cell_multiple: float = 6.0  # big limit order (book confirmation #1)
    # Absorption also needs the bar's range at or below the median range of the last N bars.
    # Exhaustion: new high (low) of the last `extreme_lookback` bars whose top (bottom) row has
    # aggressive volume <= exhaustion_fraction of the bar's average row.
    extreme_lookback: int = 6
    exhaustion_fraction: float = 0.1
    # Trades filter (book): single prints at or above this notional. Tune to ~5-10 a day.
    large_print_usd: float = 2_000_000.0
    large_print_usd_by_symbol: dict[str, float] = field(default_factory=dict)
    # Sweep: ISO prints on one side, each within `sweep_gap_ms` of the last, on >= 2 venues.
    sweep_gap_ms: int = 5
    sweep_min_venues: int = 2
    sweep_min_shares: int = 1_000
    # VPIN (Easley, Lopez de Prado, O'Hara 2012): volume buckets of 1/buckets_per_day of the
    # prior session's volume (or the fallback when it isn't known), averaged over `window`.
    vpin_buckets_per_day: int = 50
    vpin_window: int = 50
    vpin_bucket_fallback: int = 200_000
    # Context levels.
    opening_range_minutes: int = 30
    value_area: float = 0.70
    max_levels: int = 10  # newest N levels of each kind kept in snapshots

    def row_for(self, symbol: str, first_price_u: int) -> int:
        if symbol in self.row_usd:
            return round(self.row_usd[symbol] * USD)
        target = first_price_u * self.row_bps / 10_000
        return next((r for r in NICE_ROWS if r >= target), NICE_ROWS[-1])

    def large_print_for(self, symbol: str) -> float:
        return self.large_print_usd_by_symbol.get(symbol, self.large_print_usd)
