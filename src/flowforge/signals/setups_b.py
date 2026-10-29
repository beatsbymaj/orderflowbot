"""Phase 5b setups: feature records in, long signals out (docs/research/17-phase5b-hypotheses.md).

Pure and deterministic, like `setups`: the same records always give the same signals. A signal
carries its stop level in `zone_low` (the stop is that level − 1 row) and the trigger price in
`zone_high`; `plan_b` turns it into a stop and an exit by the pre-registered rules.

B1 opening drive with buyers in control, B2 failed breakdown below the prior-day low,
B3 VWAP pullback on a buyers' day, B4 afternoon strength sweep.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time

from flowforge.data.ticks import NS, parse_ts_ns
from flowforge.signals.setups import ENTRY_TF, ET, Plan, Signal, _bucket, _down, _up, entry_window

SETUPS_B = ("B1", "B2", "B3", "B4")
NAMES_B = {
    "B1": "Opening drive, buyers in control",
    "B2": "Failed breakdown below the prior-day low",
    "B3": "VWAP pullback on a buyers' day",
    "B4": "Afternoon strength sweep",
}
WINDOWS = {
    "B1": (time(10, 0), time(11, 30)),
    "B2": (time(9, 45), time(15, 0)),
    "B3": (time(10, 30), time(15, 0)),
    "B4": (time(13, 0), time(15, 0)),
}
# Grid (pre-registered): the stop is fixed by the setup; the exit is hold-to-close or 2R.
STOP_MODES_B = ("level",)
TARGET_MODES_B = ("hold", "2r")
DEFAULT_B = ("level", "hold")
CONTROL_B = ("atr30", "hold")  # random entries: stop at 30% of ATR, held to the close
STOP_BAND = (0.15, 0.60)  # of ATR
CONTROL_STOP = 0.30
FIXED_RR = 2.0
B2_LOOKBACK = 3  # this bar and the 2 before it
B3_ABOVE_SHARE = 0.75
B4_NEAR_HIGH = 0.25  # of ATR


def plan_b(sig: Signal, entry_ref: float, stop_mode: str, target_mode: str) -> Plan | None:
    """Stop and exit for an entry near `entry_ref`, or None when the rules say no trade. A
    hold-to-close plan has an infinite target: only the stop or 15:55 ends it."""
    if not sig.atr or sig.atr <= 0 or entry_ref <= 0:
        return None
    lo, hi = STOP_BAND[0] * sig.atr, STOP_BAND[1] * sig.atr
    stop = sig.zone_low - sig.row if stop_mode == "level" else entry_ref - CONTROL_STOP * sig.atr
    if entry_ref - stop < lo:
        stop = entry_ref - lo
    elif entry_ref - stop > hi + 1e-9:
        return None
    stop = _down(stop)  # still below the entry: it is at least 15% of ATR under it, rounded down
    risk = entry_ref - stop
    target = math.inf if target_mode == "hold" else _up(entry_ref + FIXED_RR * risk)
    return Plan(entry_ref, stop, target)


def _in_window(setup: str, ts: int) -> bool:
    t = datetime.fromtimestamp(ts / NS, ET).time()
    first, last = WINDOWS[setup]
    return first <= t <= last and entry_window(ts)


@dataclass
class _Bar:
    low: float
    close: float
    vwap: float | None
    row: float
    flags: bool  # B2 confirmation in this bar, at or below the prior-day low


@dataclass
class _State:
    day: str = ""
    prev: _Bar | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=B2_LOOKBACK))
    closes: int = 0
    above: int = 0
    last_ctx: dict | None = None
    last_close: float | None = None
    last_cvd: int | None = None
    row: float = 0.0
    fired: set = field(default_factory=set)


class SignalEngineB:
    """Feed every feature record (in order); returns the signals each one creates."""

    def __init__(self, setups: tuple[str, ...] = SETUPS_B) -> None:
        self.setups = setups
        self.state: dict[str, _State] = {}

    def on_record(self, rec: dict) -> list[Signal]:
        kind = rec.get("type")
        if kind == "bar" and rec.get("tf") == ENTRY_TF:
            day = (rec.get("context") or {}).get("day", "")
            st = self.state.get(rec["symbol"])
            if st is None or (day and st.day != day):
                st = self.state[rec["symbol"]] = _State(day=day)
            return self._bar(st, rec)
        if kind == "sweep" and "B4" in self.setups:
            st = self.state.get(rec["symbol"])
            return self._sweep(st, rec) if st is not None else []
        return []

    def _bar(self, st: _State, bar: dict) -> list[Signal]:
        if not bar["trades"] or bar["low"] is None:
            return []
        ctx = bar.get("context") or {}
        vwap, row = ctx.get("vwap"), bar["row"] or st.row
        prior_low = (ctx.get("prior_day") or {}).get("low")
        flags = prior_low is not None and _b2_confirmation(bar, prior_low)
        cur = _Bar(bar["low"], bar["close"], vwap, row, flags)
        prev, st.prev = st.prev, cur
        st.recent.append(cur)
        if vwap is not None:
            st.closes += 1
            st.above += bar["close"] > vwap
        st.last_ctx, st.last_close, st.last_cvd, st.row = ctx, bar["close"], bar.get("cvd"), row
        ts = parse_ts_ns(bar["end"])
        if bar.get("gap") or prev is None:
            return []
        out = []
        for setup, check in (("B1", self._b1), ("B2", self._b2), ("B3", self._b3)):
            if setup in self.setups and setup not in st.fired and _in_window(setup, ts):
                stop_level = check(st, bar, ctx, prev)
                if stop_level is not None:
                    st.fired.add(setup)
                    out.append(_signal(setup, bar["symbol"], ts, ctx, stop_level, bar["close"], row, bar))
        return out

    @staticmethod
    def _b1(st: _State, bar: dict, ctx: dict, prev: _Bar) -> float | None:
        rng, vwap = ctx.get("opening_range") or {}, ctx.get("vwap")
        if not rng.get("complete") or rng.get("high") is None or vwap is None:
            return None
        buyers = bar["delta"] > 0 and (bar.get("cvd") or 0) > 0
        if bar["close"] > rng["high"] and buyers and bar["close"] > vwap:
            return (rng["high"] + rng["low"]) / 2
        return None

    @staticmethod
    def _b2(st: _State, bar: dict, ctx: dict, prev: _Bar) -> float | None:
        prior_low = (ctx.get("prior_day") or {}).get("low")
        low = ctx.get("session_low")
        if prior_low is None or low is None or not low < prior_low:
            return None
        reclaimed = prev.close < prior_low and bar["close"] >= prior_low + (bar["row"] or st.row)
        if reclaimed and bar["delta"] > 0 and any(b.flags for b in st.recent):
            return low
        return None

    @staticmethod
    def _b3(st: _State, bar: dict, ctx: dict, prev: _Bar) -> float | None:
        vwap, sigma = ctx.get("vwap"), ctx.get("vwap_sigma")
        if vwap is None or sigma is None or prev.vwap is None or st.closes == 0:
            return None
        row = bar["row"] or st.row
        buyers_day = st.above / st.closes >= B3_ABOVE_SHARE and (bar.get("cvd") or 0) > 0
        came_from_above = prev.low > prev.vwap + prev.row
        touched = bar["low"] <= vwap + row and bar["close"] > vwap and bar["delta"] > 0
        return vwap - sigma if buyers_day and came_from_above and touched else None

    def _sweep(self, st: _State, sw: dict) -> list[Signal]:
        ctx, ts = st.last_ctx, parse_ts_ns(sw["end"])
        if sw["side"] != 1 or ctx is None or "B4" in st.fired or not _in_window("B4", ts):
            return []
        vwap, high = ctx.get("vwap"), ctx.get("session_high")
        prior = ctx.get("prior_day") or {}
        if vwap is None or high is None or prior.get("high") is None or st.last_close is None:
            return []
        atr = prior["high"] - prior["low"]
        if sw["vwap"] > vwap and (st.last_cvd or 0) > 0 and high - st.last_close <= B4_NEAR_HIGH * atr:
            st.fired.add("B4")
            bar = {"delta": st.last_cvd, "sweeps": 1}
            return [_signal("B4", sw["symbol"], ts, ctx, vwap, sw["high"], st.row, bar)]
        return []


def _b2_confirmation(bar: dict, prior_low: float) -> bool:
    if any(p <= prior_low for p in (bar.get("big_bid") or []) + (bar.get("absorption") or [])):
        return True
    return bar.get("exhaustion") == 1 or bar.get("cvd_divergence") == 1


def _signal(
    setup: str, symbol: str, ts: int, ctx: dict, stop_level: float, trigger: float, row: float, bar: dict
) -> Signal:
    prior = ctx.get("prior_day") or {}
    atr = prior["high"] - prior["low"] if prior.get("high") is not None else None
    return Signal(
        setup=setup,
        symbol=symbol,
        ts=ts,
        day=ctx.get("day", ""),
        zone_low=round(stop_level, 4),
        zone_high=trigger,
        row=row or 0.01,
        atr=atr,
        heavy_above=(),
        unfinished_lows=(),
        reason=NAMES_B[setup],
        facts=facts_b(setup, trigger, ctx, bar),
    )


def facts_b(setup: str, price: float, ctx: dict, bar: dict) -> dict:
    """Labelled facts for the Jev gate, comparisons done in code (ADR-0004)."""
    vwap, delta = ctx.get("vwap"), bar.get("delta")
    return {
        "setup": NAMES_B[setup],
        "price_vs_vwap": "unknown" if vwap is None else ("above" if price > vwap else "below"),
        "bar_delta": "unknown"
        if delta is None
        else ("buyers" if delta > 0 else "sellers" if delta < 0 else "even"),
        "tape_speed": _bucket(bar.get("speed_ratio"), (0.7, 1.5), ("slow", "normal", "fast")),
        "vpin": _bucket(bar.get("vpin"), (0.2, 0.35), ("low", "medium", "high")),
        "sweeps_in_bar": "yes" if bar.get("sweeps") else "no",
    }
