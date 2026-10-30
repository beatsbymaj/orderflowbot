"""Phase 5c setups: the 9/20 EMA crossover (docs/research/18-ema-crossover-hypotheses.md).

Pure and deterministic, like `setups` and `setups_b`. Unlike them, one engine is fed a symbol's
days in date order: the EMAs, swings and volume history carry over from one day to the next, as
on a chart. Day-scoped state (CVD history, one signal a day, cross-down times) resets each day.

C0 plain crossover, C1 crossover confirmed by trend, order flow, structure, levels and volume.
A signal carries its stop level in `zone_low` (the stop is that level − 1 row) and the trigger
close in `zone_high`. `exits` (filled by `finish`) are the day's cross-down times after it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, time

from flowforge.data.ticks import NS, parse_ts_ns
from flowforge.signals.setups import ENTRY_TF, ET, Plan, Signal, _bucket, entry_window
from flowforge.signals.setups_b import STOP_BAND, plan_b

SETUPS_C = ("C0", "C1")
NAMES_C = {"C0": "Plain 9/20 EMA crossover", "C1": "Confirmed 9/20 EMA crossover"}
WINDOW = (time(9, 45), time(15, 0))
TREND_TF = 1800
FAST, SLOW, TREND = 9, 20, 20
# Grid (pre-registered): the stop is fixed by the setup; the exit is a cross down, hold or 2R.
STOP_MODES_C = ("swing",)
TARGET_MODES_C = ("cross", "hold", "2r")
DEFAULT_C = ("swing", "cross")
C0_LOW_BARS = 5  # C0 stop level: lowest low of the last 5 bars
C1_CROSS_BARS = 3  # C1: the cross on this bar or one of the 2 before it
CVD_BACK = 3
VOLUME_BARS = 20
SWING_SIDE = 2  # a swing is lower/higher than the 2 bars on each side


def plan_c(sig: Signal, entry_ref: float, stop_mode: str, target_mode: str) -> Plan | None:
    """research/18: the research/17 stop band; "cross" and "hold" have no price target (the
    cross-down exit is a time, applied by the simulator)."""
    return plan_b(sig, entry_ref, "level", "2r" if target_mode == "2r" else "hold")


class Ema:
    """EMA(n): multiplier 2/(n+1), first value the simple average of the first n inputs."""

    def __init__(self, n: int) -> None:
        self.n, self.k = n, 2 / (n + 1)
        self.seed: list[float] = []
        self.value: float | None = None
        self.prev: float | None = None

    def update(self, x: float) -> float | None:
        self.prev = self.value
        if self.value is None:
            self.seed.append(x)
            if len(self.seed) == self.n:
                self.value = sum(self.seed) / self.n
        else:
            self.value = x * self.k + self.value * (1 - self.k)
        return self.value


@dataclass
class _Symbol:
    """Carried across days."""

    fast: Ema = field(default_factory=lambda: Ema(FAST))
    slow: Ema = field(default_factory=lambda: Ema(SLOW))
    trend: Ema = field(default_factory=lambda: Ema(TREND))
    trend_close: float | None = None
    bars: deque = field(default_factory=lambda: deque(maxlen=2 * SWING_SIDE + 1))  # (low, high)
    lows: deque = field(default_factory=lambda: deque(maxlen=C0_LOW_BARS))
    swing_lows: deque = field(default_factory=lambda: deque(maxlen=2))
    volumes: deque = field(default_factory=lambda: deque(maxlen=VOLUME_BARS))
    above: bool | None = None  # EMA(9) > EMA(20) at the last 5-min close
    # Day-scoped.
    day: str = ""
    n_today: int = 0
    cross_at: int | None = None  # index (today) of the last cross up
    cvds: list = field(default_factory=list)
    fired: set = field(default_factory=set)
    cross_downs: list = field(default_factory=list)


class SignalEngineC:
    """Feed a symbol's feature records in time order, day after day; returns new signals."""

    def __init__(self, setups: tuple[str, ...] = SETUPS_C) -> None:
        self.setups = setups
        self.state: dict[str, _Symbol] = {}

    def on_record(self, rec: dict) -> list[Signal]:
        if rec.get("type") != "bar" or not rec.get("trades") or rec.get("close") is None:
            return []
        st = self.state.setdefault(rec["symbol"], _Symbol())
        if rec["tf"] == TREND_TF:
            st.trend.update(rec["close"])
            st.trend_close = rec["close"]
            return []
        if rec["tf"] != ENTRY_TF:
            return []
        day = (rec.get("context") or {}).get("day", "")
        if day and day != st.day:
            st.day, st.n_today, st.cross_at = day, 0, None
            st.cvds, st.fired, st.cross_downs = [], set(), []
        return self._bar(st, rec)

    def finish(self, signals: list[Signal]) -> list[Signal]:
        """Attach each signal's later cross-down times (same symbol, same day)."""
        out = []
        for s in signals:
            st = self.state.get(s.symbol)
            downs = tuple(t for t in (st.cross_downs if st and st.day == s.day else []) if t > s.ts)
            out.append(replace(s, exits=downs))
        return out

    def _bar(self, st: _Symbol, bar: dict) -> list[Signal]:
        ts = parse_ts_ns(bar["end"])
        ctx = bar.get("context") or {}
        row = bar.get("row") or 0.01
        prev_volumes = list(st.volumes)
        st.volumes.append(bar.get("volume") or 0)
        st.lows.append(bar["low"])
        st.bars.append((bar["low"], bar["high"]))
        self._swing(st)
        fast, slow = st.fast.update(bar["close"]), st.slow.update(bar["close"])
        prev_above, st.above = st.above, (fast > slow) if fast is not None and slow is not None else None
        idx = st.n_today
        st.n_today += 1
        cvd_back = st.cvds[-CVD_BACK] if len(st.cvds) >= CVD_BACK else 0
        st.cvds.append(bar.get("cvd") or 0)
        if prev_above is not None and st.above is not None:
            if st.above and not prev_above:
                st.cross_at = idx
            elif prev_above and not st.above:
                st.cross_at = None
                st.cross_downs.append(ts)
        if bar.get("gap") or not st.above or st.cross_at is None or not _in_window(ts):
            return []
        out = []
        if "C0" in self.setups and "C0" not in st.fired and st.cross_at == idx:
            st.fired.add("C0")
            out.append(_signal("C0", bar, ts, ctx, min(st.lows), row))
        if "C1" in self.setups and "C1" not in st.fired and idx - st.cross_at < C1_CROSS_BARS:
            stop_level = self._c1(st, bar, ctx, row, cvd_back, prev_volumes)
            if stop_level is not None:
                st.fired.add("C1")
                out.append(_signal("C1", bar, ts, ctx, stop_level, row))
        return out

    @staticmethod
    def _swing(st: _Symbol) -> None:
        """The middle of the last 5 bars is a swing low when its low is under all 4 others."""
        if len(st.bars) < st.bars.maxlen:
            return
        mid = st.bars[SWING_SIDE][0]
        if all(mid < b[0] for i, b in enumerate(st.bars) if i != SWING_SIDE):
            st.swing_lows.append(mid)

    @staticmethod
    def _c1(
        st: _Symbol, bar: dict, ctx: dict, row: float, cvd_back: float, prev_volumes: list
    ) -> float | None:
        # 1. Trend: last 30-min close over a rising 30-min EMA(20).
        t = st.trend
        if t.value is None or t.prev is None or st.trend_close is None:
            return None
        if not (st.trend_close > t.value and t.value > t.prev):
            return None
        # 2. Order flow: buyers this bar, session CVD up over the last 3 bars.
        if not (bar.get("delta", 0) > 0 and (bar.get("cvd") or 0) > cvd_back):
            return None
        # 3. Structure: a higher low, not broken by this bar.
        if len(st.swing_lows) < 2:
            return None
        last_low, before = st.swing_lows[-1], st.swing_lows[-2]
        if not (last_low > before and bar["low"] > last_low):
            return None
        # 4. Key levels: above VWAP, with at least 1R of room to the next level above.
        vwap, close = ctx.get("vwap"), bar["close"]
        if vwap is None or close <= vwap:
            return None
        prior = ctx.get("prior_day") or {}
        if prior.get("high") is None:
            return None
        atr = prior["high"] - prior["low"]
        risk = max(close - (last_low - row), STOP_BAND[0] * atr)
        rng = ctx.get("opening_range") or {}
        levels = [prior.get("high"), prior.get("close")]
        if rng.get("complete"):
            levels.append(rng.get("high"))
        above = [lv for lv in levels if lv is not None and lv > close]
        if above and min(above) - close < risk:
            return None
        # 5. Volume: at least the average of the 20 bars before.
        if len(prev_volumes) < VOLUME_BARS or (bar.get("volume") or 0) < sum(prev_volumes) / VOLUME_BARS:
            return None
        return last_low


def _in_window(ts: int) -> bool:
    t = datetime.fromtimestamp(ts / NS, ET).time()
    return WINDOW[0] <= t <= WINDOW[1] and entry_window(ts)


def _signal(setup: str, bar: dict, ts: int, ctx: dict, stop_level: float, row: float) -> Signal:
    prior = ctx.get("prior_day") or {}
    atr = prior["high"] - prior["low"] if prior.get("high") is not None else None
    return Signal(
        setup=setup,
        symbol=bar["symbol"],
        ts=ts,
        day=ctx.get("day", ""),
        zone_low=round(stop_level, 4),
        zone_high=bar["close"],
        row=row,
        atr=atr,
        heavy_above=(),
        unfinished_lows=(),
        reason=NAMES_C[setup],
        facts=facts_c(setup, bar, ctx),
    )


def facts_c(setup: str, bar: dict, ctx: dict) -> dict:
    """Labelled facts for the Jev gate, comparisons done in code (ADR-0004)."""
    vwap, delta = ctx.get("vwap"), bar.get("delta")
    return {
        "setup": NAMES_C[setup],
        "price_vs_vwap": "unknown" if vwap is None else ("above" if bar["close"] > vwap else "below"),
        "bar_delta": "unknown"
        if delta is None
        else ("buyers" if delta > 0 else "sellers" if delta < 0 else "even"),
        "tape_speed": _bucket(bar.get("speed_ratio"), (0.7, 1.5), ("slow", "normal", "fast")),
        "vpin": _bucket(bar.get("vpin"), (0.2, 0.35), ("low", "medium", "high")),
    }
