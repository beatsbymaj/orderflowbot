"""Phase 5 setups: feature records in, long signals out (docs/research/14-signal-hypotheses.md).

Pure and deterministic: the same records always give the same signals, in a backtest, the
fast simulator and the paper node. Nothing here sizes or sends orders; a signal becomes a
trade only through `plan` (stop and target) and then RiskGuard.

H1 stacked-imbalance pullback, H2 large-print pullback, H3 multiple-node pullback,
H4 support zone + order-flow confirmation, H5 follow a buy-side sweep.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from flowforge.data.ticks import NS, parse_ts_ns

ET = ZoneInfo("America/New_York")
SETUPS = ("H1", "H2", "H3", "H4", "H5")
ENTRY_TF = 300
FIRST_ENTRY = time(9, 35)
LAST_ENTRY = time(15, 45)
PULLBACK = {
    "H1": ("stacked_imbalance", (1800,)),
    "H2": ("large_print", None),
    "H3": ("multiple_node", (1800,)),
}
H4_LEVELS = ("stacked_imbalance", "multiple_node", "large_print", "absorption")
HEAVY_LEVELS = ("multiple_node", "absorption")
# Grid (pre-registered): stop x target.
STOP_MODES = ("zone", "atr")
TARGET_MODES = ("volume", "2r")
BOOK_DEFAULT = ("zone", "volume")
ATR_STOP = 0.15
ATR_BAND = (0.10, 0.20)
MIN_VOLUME_RR = 1.5
FIXED_RR = 2.0
TICK = 0.01


@dataclass(frozen=True)
class Signal:
    setup: str
    symbol: str
    ts: int  # ns, when the signal is known
    day: str
    zone_low: float
    zone_high: float
    row: float  # footprint row height
    atr: float | None  # prior session high - low
    heavy_above: tuple[float, ...]  # low edges of heavy-volume areas (volume target)
    unfinished_lows: tuple[float, ...]
    reason: str
    facts: dict = field(default_factory=dict, compare=False)  # labelled context for Jev
    exits: tuple[int, ...] = field(default=(), compare=False)  # Phase 5c: cross-down times (ns)

    @property
    def id(self) -> str:
        return f"{self.setup}:{self.symbol}:{self.ts}"


@dataclass(frozen=True)
class Plan:
    entry_ref: float
    stop: float
    target: float

    @property
    def risk(self) -> float:
        return self.entry_ref - self.stop


def plan(sig: Signal, entry_ref: float, stop_mode: str, target_mode: str) -> Plan | None:
    """Stop and target for an entry near `entry_ref` (the ask at the signal), or None when the
    pre-registered rules say no trade."""
    if not sig.atr or sig.atr <= 0 or entry_ref <= 0:
        return None
    lo, hi = ATR_BAND[0] * sig.atr, ATR_BAND[1] * sig.atr
    stop = sig.zone_low - sig.row if stop_mode == "zone" else entry_ref - ATR_STOP * sig.atr
    if entry_ref - stop < lo:
        stop = entry_ref - lo  # too close: widen to the band's edge (book §6)
    elif entry_ref - stop > hi + 1e-9:
        return None  # too far: skip
    stop = _down(stop)
    risk = entry_ref - stop
    if risk <= 0:
        return None
    if any(stop - risk <= u < entry_ref for u in sig.unfinished_lows):
        return None  # unfinished business just under us: price likely sweeps it
    if target_mode == "volume":
        target = next(
            (e - sig.row for e in sorted(sig.heavy_above) if e - sig.row - entry_ref >= MIN_VOLUME_RR * risk),
            None,
        )
        if target is None:
            return None
    else:
        target = entry_ref + FIXED_RR * risk
    return Plan(entry_ref, stop, _up(target))


def _down(x: float) -> float:
    return round(int(round(x / TICK, 6)) * TICK, 2)


def _up(x: float) -> float:
    n = round(x / TICK, 6)
    return round((int(n) + (n > int(n))) * TICK, 2)


def entry_window(ts: int) -> bool:
    t = datetime.fromtimestamp(ts / NS, ET).time()
    return FIRST_ENTRY <= t <= LAST_ENTRY


def level_key(level: dict) -> tuple:
    return (level["kind"], level["tf"], level["low"], level["high"], level["created"])


@dataclass
class _SymbolState:
    day: str = ""
    prev_low: float | None = None  # previous 5-min bar
    last_context: dict | None = None
    last_1m_delta: int | None = None
    row: float = 0.0
    touched: set = field(default_factory=set)  # level keys any 5-min bar has overlapped


class SignalEngine:
    """Feed every feature record (in order); returns the signals each one creates."""

    def __init__(self, setups: tuple[str, ...] = SETUPS) -> None:
        self.setups = setups
        self.state: dict[str, _SymbolState] = {}

    def on_record(self, rec: dict) -> list[Signal]:
        kind = rec.get("type")
        if kind == "bar":
            st = self._state(rec["symbol"], rec.get("context", {}).get("day", ""))
            if rec["tf"] == 60:
                if rec["trades"]:
                    st.last_1m_delta = rec["delta"]
                return []
            if rec["tf"] != ENTRY_TF:
                return []
            return self._bar(st, rec)
        if kind == "sweep" and "H5" in self.setups:
            st = self.state.get(rec["symbol"])
            return self._sweep(st, rec) if st is not None else []
        return []

    def _state(self, symbol: str, day: str) -> _SymbolState:
        st = self.state.get(symbol)
        if st is None or (day and st.day != day):
            st = self.state[symbol] = _SymbolState(day=day)
        return st

    # -- 5-minute bar setups -----------------------------------------------------------------
    def _bar(self, st: _SymbolState, bar: dict) -> list[Signal]:
        ctx = bar.get("context") or {}
        if not bar["trades"] or bar["low"] is None:
            return []
        prev_low, st.prev_low = st.prev_low, bar["low"]
        st.last_context = ctx
        st.row = bar["row"] or st.row
        levels = [lv for group in (ctx.get("levels") or {}).values() for lv in group]
        overlapping = [lv for lv in levels if bar["low"] <= lv["high"] and bar["high"] >= lv["low"]]
        # Only an overlap after the level was left counts as its first touch. The bar a level is
        # built in overlaps it too (a large print sits inside its own bar), and must not use it up.
        returns = {level_key(lv) for lv in overlapping if _left_then_first_touch(lv)}
        first_touch = returns - st.touched
        st.touched |= returns
        ts = parse_ts_ns(bar["end"])
        if bar.get("gap") or not entry_window(ts) or prev_low is None:
            return []
        out = []
        for setup, (kind, tfs) in PULLBACK.items():
            if setup not in self.setups:
                continue
            for lv in overlapping:
                if lv["kind"] != kind or (tfs and lv["tf"] not in tfs) or level_key(lv) not in first_touch:
                    continue
                if not _left_then_first_touch(lv) or not prev_low > lv["high"] or bar["close"] < lv["low"]:
                    continue
                out.append(self._signal(setup, bar, ctx, ts, lv["low"], lv["high"], f"{kind} {lv['tf']}s"))
                break  # one signal per setup per bar
        if "H4" in self.setups:
            sig = self._h4(bar, ctx, ts, levels)
            if sig is not None:
                out.append(sig)
        return out

    def _h4(self, bar: dict, ctx: dict, ts: int, levels: list[dict]) -> Signal | None:
        zones = []
        for name, value in (
            ("VAL", ctx.get("val")),
            ("prior-day low", (ctx.get("prior_day") or {}).get("low")),
            ("VWAP-1sd", _sub(ctx.get("vwap"), ctx.get("vwap_sigma"))),
            ("OR low", _or_low(ctx)),
        ):
            if value is not None:
                zones.append((value, value, name))
        top = bar["open"]  # levels at or below where this bar started (the prior close)
        zones += [
            (lv["low"], lv["high"], lv["kind"])
            for lv in levels
            if lv["kind"] in H4_LEVELS and lv["high"] <= top
        ]
        row = bar["row"] or 0.0
        best = None
        for lo, hi, name in zones:
            if not (bar["low"] <= hi and bar["close"] >= lo):
                continue
            conf = _confirmation(bar, lo - row, hi + row)
            if conf and (best is None or hi > best[1]):
                best = (lo, hi, f"{name} + {conf}")
        if best is None:
            return None
        return self._signal("H4", bar, ctx, ts, best[0], best[1], best[2])

    # -- sweeps ------------------------------------------------------------------------------
    def _sweep(self, st: _SymbolState, sw: dict) -> list[Signal]:
        ctx = st.last_context
        ts = parse_ts_ns(sw["end"])
        if sw["side"] != 1 or ctx is None or not entry_window(ts):
            return []
        vwap = ctx.get("vwap")
        if vwap is None or sw["vwap"] <= vwap or not st.last_1m_delta or st.last_1m_delta <= 0:
            return []
        fake_bar = {"symbol": sw["symbol"], "row": st.row, "close": sw["high"], "delta": st.last_1m_delta}
        return [
            self._signal("H5", fake_bar, ctx, ts, sw["low"], sw["high"], f"buy sweep ${sw['notional']:,.0f}")
        ]

    # -- shared ------------------------------------------------------------------------------
    def _signal(self, setup: str, bar: dict, ctx: dict, ts: int, lo: float, hi: float, reason: str) -> Signal:
        levels = [lv for group in (ctx.get("levels") or {}).values() for lv in group]
        ref = bar["close"]
        heavy = [x for x in (ctx.get("poc"), ctx.get("vah")) if x is not None and x > ref]
        heavy += [lv["low"] for lv in levels if lv["kind"] in HEAVY_LEVELS and lv["low"] > ref]
        unfinished = [lv["low"] for lv in levels if lv["kind"] == "unfinished" and lv["side"] == 1]
        prior = ctx.get("prior_day") or {}
        atr = prior["high"] - prior["low"] if prior.get("high") is not None else None
        return Signal(
            setup=setup,
            symbol=bar["symbol"],
            ts=ts,
            day=ctx.get("day", ""),
            zone_low=lo,
            zone_high=hi,
            row=bar["row"] or TICK,
            atr=atr,
            heavy_above=tuple(sorted(set(heavy))),
            unfinished_lows=tuple(sorted(set(unfinished))),
            reason=reason,
            facts=facts(setup, bar, ctx, reason),
        )


def _left_then_first_touch(lv: dict) -> bool:
    """Engine counters, as of the bar that produced the snapshot. 30-min levels haven't been
    updated by the current 30-min bar yet (away >= 1, touches 0); 5-min levels were updated
    by this very bar (touches 1, away reset to 0)."""
    if lv["tf"] == ENTRY_TF:
        return lv["touches"] == 1 and lv["away_bars"] == 0
    return lv["touches"] == 0 and lv["away_bars"] >= 1


def _confirmation(bar: dict, lo: float, hi: float) -> str | None:
    if any(lo <= p <= hi for p in bar.get("big_bid") or []):
        return "big bid"
    if any(lo <= p <= hi for p in bar.get("absorption") or []):
        return "absorption"
    if bar.get("exhaustion") == 1:
        return "seller exhaustion"
    if bar.get("cvd_divergence") == 1:
        return "CVD divergence"
    return None


def _sub(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(a - b, 4)


def _or_low(ctx: dict) -> float | None:
    rng = ctx.get("opening_range")
    return rng["low"] if rng and rng.get("complete") else None


def _bucket(x: float | None, cuts: tuple[float, ...], names: tuple[str, ...]) -> str:
    if x is None:
        return "unknown"
    for cut, name in zip(cuts, names, strict=False):
        if x < cut:
            return name
    return names[-1]


def facts(setup: str, bar: dict, ctx: dict, reason: str) -> dict:
    """Labelled facts for the Jev gate. Every comparison is done here, in code (ADR-0004)."""
    close, vwap = bar.get("close"), ctx.get("vwap")
    nbbo = bar.get("nbbo") or {}
    delta = bar.get("delta")
    return {
        "setup": {
            "H1": "stacked imbalance pullback",
            "H2": "large print pullback",
            "H3": "multiple node pullback",
            "H4": "support zone with confirmation",
            "H5": "buy-side sweep",
        }[setup],
        "trigger": reason,
        "price_vs_vwap": "unknown"
        if close is None or vwap is None
        else ("above" if close > vwap else "below"),
        "bar_delta": "unknown"
        if delta is None
        else ("buyers" if delta > 0 else "sellers" if delta < 0 else "even"),
        "tape_speed": _bucket(bar.get("speed_ratio"), (0.7, 1.5), ("slow", "normal", "fast")),
        "spread": _bucket(nbbo.get("spread_bps_avg"), (1.0, 3.0), ("tight", "normal", "wide")),
        "vpin": _bucket(bar.get("vpin"), (0.2, 0.35), ("low", "medium", "high")),
        "large_prints_in_bar": "yes" if bar.get("large_prints") else "no",
        "sweeps_in_bar": "yes" if bar.get("sweeps") else "no",
    }
