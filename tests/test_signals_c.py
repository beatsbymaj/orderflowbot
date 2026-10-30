"""Phase 5c 9/20 EMA crossover (docs/research/18-ema-crossover-hypotheses.md) on hand-built records."""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import replace
from datetime import date

import pytest
from conftest import DAY, et_ns

from flowforge.data.ticks import format_ts_ns
from flowforge.research import walkforward as wf
from flowforge.research.fastsim import simulate
from flowforge.research.quotes import MemoryQuotes
from flowforge.signals.setups import Signal
from flowforge.signals.setups_c import Ema, SignalEngineC, plan_c

NS = 1_000_000_000
# 8 flat bars, a fall to a swing low (96.9), a bounce, a higher swing low (97.7), then a rally:
# EMA(9) crosses over EMA(20) on bar 27 (11:50 ET).
CLOSES = [100.0] * 8 + [99.6, 99.2, 98.8, 98.4, 98.0, 97.6, 97.2, 97.0, 97.3, 97.7, 98.1, 98.5]
CLOSES += [98.3, 98.0, 97.8, 98.0, 98.4, 98.9, 99.5, 100.1, 100.7, 101.3, 101.9, 102.5]
CROSS = 27


def ctx(day=DAY, **kw):
    base = {
        "day": day.isoformat(),
        "vwap": 95.0,
        "opening_range": {"high": 120.0, "low": 90.0, "complete": True},
        "prior_day": {"high": 120.0, "low": 95.0, "close": 110.0},  # ATR 25
    }
    return base | kw


def trend(closes, day=DAY):
    """30-min bars (before the session's 5-min bars, as the engine would have seen them)."""
    t = et_ns(9, 30, day=day)
    return [
        {"type": "bar", "symbol": "SPY", "tf": 1800, "end": format_ts_ns(t), "trades": 1, "close": c}
        for c in closes
    ]


def bars(closes=CLOSES, day=DAY, volume=None, c=None, **kw):
    out, cvd = [], 0
    for i, close in enumerate(closes):
        cvd += kw.get("delta", 50)
        rec = {
            "type": "bar",
            "symbol": "SPY",
            "tf": 300,
            "end": format_ts_ns(et_ns(9, 35, day=day) + i * 300 * NS),
            "trades": 100,
            "close": close,
            "low": round(close - 0.1, 2),
            "high": round(close + 0.1, 2),
            "row": 0.05,
            "delta": 50,
            "cvd": cvd,
            "volume": (volume or (lambda i: 1000 if i < 24 else 2000))(i),
            "gap": False,
            "context": c or ctx(day),
        }
        out.append(rec | kw)
    return out


RISING = [80.0 + i for i in range(22)]


def run(*records, setups=("C0", "C1")):
    e = SignalEngineC(setups)
    sigs = [s for r in records for s in e.on_record(r)]
    return e, e.finish(sigs)


def test_ema_seeds_with_the_simple_average_then_uses_two_over_n_plus_one():
    e = Ema(3)
    assert [e.update(x) for x in (1, 2, 3)] == [None, None, 2.0]
    assert e.update(4) == 3.0 and e.prev == 2.0  # 4 * 0.5 + 2 * 0.5


def test_both_fire_on_the_confirmed_cross():
    _, sigs = run(*trend(RISING), *bars())
    assert [s.setup for s in sigs] == ["C0", "C1"]
    c0, c1 = sigs
    assert c0.ts == c1.ts == et_ns(9, 35) + CROSS * 300 * NS
    assert c0.zone_low == 97.9  # lowest low of the last 5 bars
    assert c1.zone_low == 97.7  # last swing low
    assert c1.zone_high == 100.1 and c1.atr == 25.0 and c1.exits == ()
    assert c1.facts["price_vs_vwap"] == "above" and c1.facts["bar_delta"] == "buyers"


@pytest.mark.parametrize(
    "why, records",
    [
        ("30-min trend falling", lambda: [*trend(RISING[::-1]), *bars()]),
        ("too few 30-min closes", lambda: [*trend(RISING[:20]), *bars()]),
        ("sellers on the bar", lambda: [*trend(RISING), *bars(delta=-50)]),
        ("below VWAP", lambda: [*trend(RISING), *bars(c=ctx(vwap=102.0))]),
        (
            "no room to the prior close",
            lambda: [*trend(RISING), *bars(c=ctx(prior_day={"high": 120.0, "low": 95.0, "close": 103.5}))],
        ),
        ("light volume", lambda: [*trend(RISING), *bars(volume=lambda i: 2000 if i < 24 else 1000)]),
        ("lower low", lambda: [*trend(RISING), *bars(CLOSES[:22] + [96.5] + CLOSES[23:])]),
    ],
)
def test_c1_needs_every_confirmation(why, records):
    _, sigs = run(*records())
    assert [s.setup for s in sigs] == ["C0"], why


def test_c1_may_confirm_up_to_two_bars_after_the_cross():
    def volume(light_bars):
        return lambda i: 1000 if i < 24 else 2000 if i < CROSS else 500 if i < CROSS + light_bars else 4000

    _, sigs = run(*trend(RISING), *bars(volume=volume(2)))  # cross bar light, 2 bars later heavy
    assert [(s.setup, (s.ts - et_ns(9, 35)) // (300 * NS)) for s in sigs] == [
        ("C0", CROSS),
        ("C1", CROSS + 2),
    ]
    _, sigs = run(*trend(RISING), *bars(volume=volume(3)))  # heavy 3 bars later: too late
    assert [s.setup for s in sigs] == ["C0"]


def test_one_signal_a_day_window_gaps_and_cross_down_exits():
    down_up = CLOSES + [100.0, 98.0, 96.0, 95.0, 94.0, 96.0, 99.0, 102.0, 105.0, 108.0]
    e, sigs = run(*trend(RISING), *bars(down_up))
    assert [s.setup for s in sigs] == ["C0", "C1"]  # the second cross up that day is ignored
    assert len(sigs[0].exits) == 1 and sigs[0].exits[0] > sigs[0].ts
    assert e.state["SPY"].cross_downs == list(sigs[0].exits)
    gapped = bars()
    gapped[CROSS]["gap"] = True
    assert "C0" not in [s.setup for s in run(*trend(RISING), *gapped)[1]]  # the cross bar was a gap
    # Same pattern shifted so the cross lands at 15:15 ET, after the window.
    late = bars()
    for i, r in enumerate(late):
        r["end"] = format_ts_ns(et_ns(13, 0) + i * 300 * NS)
    assert run(*trend(RISING), *late)[1] == []


def test_averages_carry_over_days_but_signals_and_crosses_reset():
    day2 = date(2026, 10, 28)
    e = SignalEngineC()
    for r in [*trend(RISING), *bars()]:
        e.on_record(r)
    st = e.state["SPY"]
    fast = st.fast.value
    sigs = e.on_record(bars([102.6], day=day2)[0])
    assert sigs == [] and st.day == "2026-10-28" and st.n_today == 1 and st.fired == set()
    assert st.fast.value != fast and st.slow.value is not None  # no new warm-up


def test_plan_c_band_and_exits():
    s = Signal("C1", "SPY", 0, DAY.isoformat(), 97.7, 100.1, 0.05, 25.0, (), (), "t")
    assert math.isinf(plan_c(s, 100.1, "swing", "cross").target)
    assert math.isinf(plan_c(s, 100.1, "swing", "hold").target)
    p = plan_c(s, 100.1, "swing", "2r")
    assert p.stop == 96.35 and p.target == 107.6  # widened to 15% of ATR (3.75)
    far = Signal("C1", "SPY", 0, DAY.isoformat(), 80.0, 100.1, 0.05, 25.0, (), (), "t")
    assert plan_c(far, 100.1, "swing", "2r") is None  # stop farther than 60% of ATR: no trade


def test_fastsim_cross_exit_sells_at_the_bid_on_the_cross_down():
    t0 = et_ns(10, 35)
    sig = Signal(
        "C1", "SPY", t0, DAY.isoformat(), 97.7, 100.1, 0.05, 25.0, (), (), "t", exits=(t0 + 600 * NS,)
    )
    q = MemoryQuotes(
        {"SPY": [(t0 - NS, 100.0, 100.02), (t0 + 300 * NS, 100.5, 100.52), (t0 + 900 * NS, 99.0, 99.02)]}
    )
    tr = simulate(sig, "swing", "cross", q)
    assert tr.exit_reason == "cross" and tr.exit == 100.5 and tr.exit_ts == t0 + 600 * NS + 150_000_000
    held = simulate(sig, "swing", "hold", q)
    assert held.exit_reason == "eod" and held.exit == 99.0


def test_walkforward_feeds_the_warm_up_day_first_and_drops_its_signals(tmp_path):
    d0, d1 = date(2026, 10, 26), DAY
    folder = tmp_path / "SPY"
    folder.mkdir()
    for d, recs in ((d0, [*trend(RISING, d0), *bars(CLOSES[:24], day=d0)]), (d1, bars(CLOSES[24:], day=d1))):
        with gzip.open(folder / f"{d.isoformat()}.jsonl.gz", "wt", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in recs))
    sds = wf.load_stock_days(tmp_path, ["SPY"], [d1.isoformat()], wf.PHASE5C)
    assert [sd.day for sd in sds] == [d1.isoformat()]
    assert sorted(s.setup for s in sds[0].signals) == ["C0", "C1"]  # warmed up, cross on day 2
    cold = wf.load_stock_days(tmp_path, ["SPY"], [d1.isoformat()], replace(wf.PHASE5C, carry=False))
    assert cold[0].signals == []  # a fresh engine each day never warms up
    assert wf.PHASE5C.n_trials == 34 and len(wf.PHASE5C.configs) == 3
    assert wf.STUDIES["5c-fall"].first == "2026-07-31" and wf.STUDIES["5c-fall"].carry
