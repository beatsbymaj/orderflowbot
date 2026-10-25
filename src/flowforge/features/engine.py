"""The feature engine: ordered trades and quotes in, feature records out.

Live (Nautilus actor on the Alpaca stream) and replay (files, or a Nautilus backtest) all
call the same `on_trade` / `on_quote`, and everything after that is this file. The output
depends only on the events, never on the wall clock, which is what the parity test checks.

Records written to the sink, as plain dicts:
- "bar": one footprint bar per timeframe (features, NBBO stats, session context)
- "large_print", "sweep": tape events, when they are confirmed
- "day_stats": counts for one symbol-day, at the end of the day
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from flowforge.data.ticks import NS, Quote, Trade, format_ts_ns
from flowforge.features import conditions as cond
from flowforge.features.classify import Classifier, Side
from flowforge.features.config import USD, FeatureConfig
from flowforge.features.context import Level, PriorDay, Session
from flowforge.features.footprint import Bar, History, QuoteClock, _px
from flowforge.features.sequencer import QUOTE, Sequencer
from flowforge.features.vpin import Vpin

ET = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
LEVEL_MIN_TF = 300  # levels come from 5- and 30-minute footprints (book), not 1-minute

Sink = Callable[[dict], None]


def to_u(price: float) -> int:
    return round(price * USD)


@dataclass
class Sweep:
    side: Side
    start: int
    last: int
    shares: int = 0
    notional: int = 0  # micro-dollars x shares
    venues: set[str] = field(default_factory=set)
    prices: set[int] = field(default_factory=set)
    prints: int = 0


class SymbolEngine:
    def __init__(self, symbol: str, cfg: FeatureConfig, sink: Sink, seeds: dict, gaps: list) -> None:
        self.symbol = symbol
        self.cfg = cfg
        self.sink = sink
        self.seeds = seeds  # day iso -> PriorDay
        self.gaps = gaps  # [(start_ns, end_ns)] from the Phase 2 quality reports
        self.seq = Sequencer(cfg.reorder_window_ms * 1_000_000)
        self.classifier = Classifier()
        self.session: Session | None = None
        self.day_end = 0
        self.bars: dict[int, Bar] = {}
        self.hist: dict[int, History] = {}
        self.vpin: Vpin | None = None
        self.sweep: Sweep | None = None
        self.stats: Counter = Counter()
        self.unknown: Counter = Counter()
        self.large_print_u = round(cfg.large_print_for(symbol) * USD)

    # -- input ------------------------------------------------------------------------
    def on_trade(self, trade: Trade) -> None:
        for ev in self.seq.push_trade(trade.ts, trade.exchange, trade.exchange_id, trade):
            self._process(ev)

    def on_quote(self, quote: Quote) -> None:
        for ev in self.seq.push_quote(quote.ts, quote):
            self._process(ev)

    def flush(self) -> None:
        for ev in self.seq.flush():
            self._process(ev)
        if self.session is not None:
            self._end_sweep()
            self._end_day()

    def _process(self, ev) -> None:
        self._roll(ev.ts)
        if ev.rank == QUOTE:
            self._quote(ev.payload)
        else:
            self._trade(ev.payload)

    # -- days and bars ----------------------------------------------------------------
    def _roll(self, ts: int) -> None:
        if self.sweep is not None and ts - self.sweep.last > self.cfg.sweep_gap_ms * 1_000_000:
            self._end_sweep()
        if self.session is None or ts >= self.day_end:
            if self.session is not None:
                self._end_day()
            self._start_day(ts)
        for tf in self.cfg.bar_seconds:
            while ts >= self.bars[tf].end:
                self._finish_bar(tf)

    def _start_day(self, ts: int) -> None:
        local = datetime.fromtimestamp(ts / NS, ET)
        day = local.date()
        midnight = datetime.combine(day, time(0), ET)
        self.day_end = _ns(midnight + timedelta(days=1))
        open_ns = _ns(datetime.combine(day, RTH_OPEN, ET))
        prior = self.seeds.get(day.isoformat())
        self.session = Session(
            day=day.isoformat(),
            open_ns=open_ns,
            or_end_ns=open_ns + self.cfg.opening_range_minutes * 60 * NS,
            prior=prior,
            max_levels=self.cfg.max_levels,
            value_area=self.cfg.value_area,
        )
        bucket = prior.volume // self.cfg.vpin_buckets_per_day if prior else self.cfg.vpin_bucket_fallback
        self.vpin = Vpin(bucket, self.cfg.vpin_window)
        adaptive = prior.large_print_usd if prior and prior.large_print_usd else 0.0
        self.large_print_u = round(max(self.cfg.large_print_for(self.symbol), adaptive) * USD)
        adaptive = prior.sweep_usd if prior and prior.sweep_usd else 0.0
        self.sweep_min_u = round(max(self.cfg.sweep_min_usd, adaptive) * USD)
        self.classifier = Classifier()  # yesterday's NBBO and last price don't carry over
        self.stats = Counter()
        self.unknown = Counter()
        self.hist = {tf: History(self.cfg.lookback_bars) for tf in self.cfg.bar_seconds}
        for tf in self.cfg.bar_seconds:
            step = tf * NS
            start = ts - ts % step
            self.bars[tf] = Bar(start, start + step, QuoteClock(start))

    def _end_day(self) -> None:
        for tf in self.cfg.bar_seconds:
            self._finish_bar(tf, last=True)
        s = self.session
        self.sink(
            {
                "type": "day_stats",
                "symbol": self.symbol,
                "day": s.day,
                "row": _px(s.row) if s.row else None,
                "vpin_bucket": self.vpin.bucket,
                "vpin_buckets": self.vpin.buckets,
                "late_events_total": self.seq.late,
                "unknown_conditions": dict(sorted(self.unknown.items())),
                **dict(sorted(self.stats.items())),
            }
        )
        self.session = None

    def _finish_bar(self, tf: int, last: bool = False) -> None:
        bar = self.bars[tf]
        s = self.session
        bar.quotes.advance(bar.end)
        bar.gap = any(a < bar.end and b > bar.start for a, b in self.gaps)
        hist = self.hist[tf]
        analysis, cells = bar.analyse(self.cfg, hist, s.cvd)
        record = {
            "type": "bar",
            "symbol": self.symbol,
            "tf": tf,
            "start": format_ts_ns(bar.start),
            "end": format_ts_ns(bar.end),
            **bar.summary(s.cvd, hist),
            **analysis,
            "vpin": self.vpin.value,
            "nbbo": bar.quotes.summary(),
        }
        if bar.trades:
            hist.push(bar, cells, s.cvd)
        if tf >= LEVEL_MIN_TF and bar.rows:
            self._levels_from(bar, tf, analysis)
        poc = max(bar.rows, key=lambda r: (sum(bar.rows[r]), -r)) if bar.rows else None
        s.on_bar(tf, bar.low, bar.high, poc, bar.end)
        record["context"] = s.snapshot(bar.end)
        self.sink(record)
        if not last:
            self.bars[tf] = Bar(bar.end, bar.end + tf * NS, bar.quotes.carry(bar.end), row=s.row)

    def _levels_from(self, bar: Bar, tf: int, analysis: dict) -> None:
        s, row = self.session, bar.row
        for key, side in (("stacked_buy", 1), ("stacked_sell", -1)):
            for lo, hi in analysis[key]:
                s.add_level(Level("stacked_imbalance", tf, to_u(lo), to_u(hi) + row - 1, side, bar.end))
        for price in analysis["absorption"]:
            s.add_level(Level("absorption", tf, to_u(price), to_u(price) + row - 1, 0, bar.end))
        if analysis["unfinished_high"]:
            s.add_level(Level("unfinished", tf, bar.high, bar.high, -1, bar.end))
        if analysis["unfinished_low"]:
            s.add_level(Level("unfinished", tf, bar.low, bar.low, 1, bar.end))

    # -- events -----------------------------------------------------------------------
    def _quote(self, q: Quote) -> None:
        bid, ask = to_u(q.bid_price), to_u(q.ask_price)
        self.classifier.on_quote(bid, ask)
        for bar in self.bars.values():
            bar.quotes.update(q.ts, bid, ask, q.bid_size, q.ask_size)
        self.stats["quotes"] += 1

    def _trade(self, t: Trade) -> None:
        s, conds = self.session, t.conditions
        self.stats["trades"] += 1
        for code in cond.unknown(conds):
            self.unknown[code] += 1
        if not cond.counts_volume(conds):
            self.stats["no_volume_prints"] += 1
            return
        price = to_u(t.price)
        priced = cond.market_priced(conds)
        if priced and not s.row:
            s.row = self.cfg.row_for(self.symbol, price)
            for bar in self.bars.values():
                bar.row = s.row
        self.stats[f"nbbo_{self.classifier.nbbo_state}"] += 1
        side, method = self.classifier.classify(price, conds)
        self.stats[f"method_{method}"] += 1
        self.stats["volume"] += t.size
        flags = {
            "market_priced": priced,
            "off_exchange": t.exchange == cond.OFF_EXCHANGE,
            "iso": cond.ISO in conds,
            "odd_lot": cond.ODD_LOT in conds,
        }
        for bar in self.bars.values():
            bar.add(t.ts, price, t.size, side, method, flags)
        if priced:
            s.add_trade(t.ts, price, t.size)
        if side is not Side.NONE:
            s.cvd += side * t.size
            self.vpin.add(t.size, side)
        if priced and not cond.is_auction(conds) and price * t.size >= self.large_print_u:
            self._large_print(t, price, side)
        if flags["iso"] and side is not Side.NONE:
            self._add_to_sweep(t, price, side)

    def _large_print(self, t: Trade, price: int, side: Side) -> None:
        self.stats["large_prints"] += 1
        for bar in self.bars.values():
            bar.large_prints += 1
            bar.large_print_volume += t.size
        s = self.session
        tf = min((tf for tf in self.cfg.bar_seconds if tf >= LEVEL_MIN_TF), default=self.cfg.bar_seconds[-1])
        row_low = price - price % s.row
        s.add_level(Level("large_print", tf, row_low, row_low + s.row - 1, int(side), t.ts))
        self.sink(
            {
                "type": "large_print",
                "symbol": self.symbol,
                "ts": format_ts_ns(t.ts),
                "price": _px(price),
                "size": t.size,
                "notional": round(price * t.size / USD / USD, 2),
                "exchange": t.exchange,
                "side": int(side),
                "conditions": "".join(sorted(t.conditions)).strip(),
                "trade_id": t.trade_id,
            }
        )

    def _add_to_sweep(self, t: Trade, price: int, side: Side) -> None:
        sw = self.sweep
        if sw is not None and sw.side is not side:
            self._end_sweep()
            sw = None
        if sw is None:
            sw = self.sweep = Sweep(side, t.ts, t.ts)
        sw.last = t.ts
        sw.shares += t.size
        sw.notional += price * t.size
        sw.venues.add(t.exchange)
        sw.prices.add(price)
        sw.prints += 1

    def _end_sweep(self) -> None:
        sw, self.sweep = self.sweep, None
        if (
            sw is None
            or len(sw.venues) < self.cfg.sweep_min_venues
            or sw.shares < self.cfg.sweep_min_shares
            or sw.notional < self.sweep_min_u
        ):
            return
        self.stats["sweeps"] += 1
        for bar in self.bars.values():
            bar.sweeps += 1
            bar.sweep_volume += sw.shares
        self.sink(
            {
                "type": "sweep",
                "symbol": self.symbol,
                "start": format_ts_ns(sw.start),
                "end": format_ts_ns(sw.last),
                "side": int(sw.side),
                "shares": sw.shares,
                "prints": sw.prints,
                "venues": sorted(sw.venues),
                "low": _px(min(sw.prices)),
                "high": _px(max(sw.prices)),
                "vwap": _px(sw.notional / sw.shares),
            }
        )


def _ns(dt: datetime) -> int:
    return int(dt.timestamp()) * NS


class FeatureEngine:
    """One SymbolEngine per symbol; records go to `sink` in processing order."""

    def __init__(self, config: FeatureConfig | None = None, sink: Sink | None = None) -> None:
        self.config = config or FeatureConfig()
        self.records: list[dict] = []
        self.sink = sink or self.records.append
        self.symbols: dict[str, SymbolEngine] = {}
        self._seeds: dict[str, dict] = {}
        self._gaps: dict[str, list] = {}

    def _engine(self, symbol: str) -> SymbolEngine:
        eng = self.symbols.get(symbol)
        if eng is None:
            eng = self.symbols[symbol] = SymbolEngine(
                symbol,
                self.config,
                self.sink,
                self._seeds.setdefault(symbol, {}),
                self._gaps.setdefault(symbol, []),
            )
        return eng

    def seed_prior_day(self, symbol: str, day: str, prior: PriorDay) -> None:
        """Prior session's high/low/close/volume for `day` (ISO date): context and VPIN bucket."""
        self._seeds.setdefault(symbol, {})[day] = prior

    def mark_gap(self, symbol: str, start: int, end: int) -> None:
        """Bars overlapping [start, end) are flagged `gap` (data known to be missing)."""
        self._gaps.setdefault(symbol, []).append((start, end))

    def on_trade(self, symbol: str, trade: Trade) -> None:
        if not trade.dropped:
            self._engine(symbol).on_trade(trade)

    def on_quote(self, symbol: str, quote: Quote) -> None:
        self._engine(symbol).on_quote(quote)

    def flush(self) -> None:
        for symbol in sorted(self.symbols):
            self.symbols[symbol].flush()
