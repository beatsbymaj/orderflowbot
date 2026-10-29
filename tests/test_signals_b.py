"""Phase 5b setups (docs/research/17-phase5b-hypotheses.md) on hand-built feature records."""

from __future__ import annotations

import math

from conftest import et_ns
from test_signals import bar, ctx, feed

from flowforge.data.ticks import format_ts_ns
from flowforge.signals.setups import Signal
from flowforge.signals.setups_b import SignalEngineB, facts_b, plan_b

# ctx(): VWAP 99.5 (sigma 0.4), opening range 98.5-100.5 (complete), prior day 97-102 (ATR 5).


def b(h, m, low, high, close, cvd=500, **kw):
    h, m = h + m // 60, m % 60
    c = kw.pop("c", None)
    return bar(
        h,
        m,
        low,
        high,
        close,
        cvd=cvd,
        c=c if c is not None else ctx(session_high=high, session_low=98.0),
        **kw,
    )


def sweep(h, m, low=100.6, high=100.8, vwap=100.7, side=1):
    return {
        "type": "sweep",
        "symbol": "SPY",
        "end": format_ts_ns(et_ns(h, m)),
        "side": side,
        "low": low,
        "high": high,
        "vwap": vwap,
        "notional": 3e6,
    }


def test_b1_opening_drive_fires_once_in_its_window_only():
    e = SignalEngineB(("B1",))
    early = feed(e, b(9, 55, 99.8, 100.6, 100.4), b(9, 58, 100.4, 100.9, 100.8))  # before 10:00
    assert early == []
    sigs = feed(e, b(10, 5, 100.4, 100.9, 100.8), b(10, 10, 100.7, 101.2, 101.1))
    assert [s.setup for s in sigs] == ["B1"]
    s = sigs[0]
    assert s.zone_low == 99.5 and s.zone_high == 100.8 and s.atr == 5.0  # stop level: OR midpoint
    assert s.facts["price_vs_vwap"] == "above" and s.facts["bar_delta"] == "buyers"
    late = SignalEngineB(("B1",))
    assert feed(late, b(11, 30, 100, 100.5, 100.4), b(11, 35, 100.4, 100.9, 100.8)) == []


def test_b1_needs_buyers_and_a_complete_range():
    sellers = SignalEngineB(("B1",))
    assert feed(sellers, b(10, 0, 100, 100.4, 100.2), b(10, 5, 100.4, 100.9, 100.8, cvd=-10)) == []
    open_range = ctx(opening_range={"high": 100.5, "low": 98.5, "complete": False})
    unfinished = SignalEngineB(("B1",))
    assert feed(unfinished, b(10, 0, 100, 100.4, 100.2), b(10, 5, 100.4, 100.9, 100.8, c=open_range)) == []
    no_vwap = SignalEngineB(("B1",))
    assert feed(no_vwap, b(10, 0, 100, 100.4, 100.2), b(10, 5, 100.4, 100.9, 100.8, c=ctx(vwap=None))) == []


def test_b2_failed_breakdown_needs_the_reclaim_and_a_confirmation():
    below = ctx(session_low=96.5)
    e = SignalEngineB(("B2",))
    sigs = feed(
        e,
        b(10, 0, 96.5, 97.2, 96.8, c=below, absorption=[96.9]),  # absorption at/below the prior low
        b(10, 5, 96.7, 97.2, 97.1, c=below, delta=40),  # back above 97.05 (low + 1 row)
    )
    assert [s.setup for s in sigs] == ["B2"] and sigs[0].zone_low == 96.5  # stop level: session low
    assert (
        feed(e, b(10, 10, 96.8, 97.0, 96.9, c=below), b(10, 15, 96.9, 97.3, 97.2, c=below, absorption=[96.9]))
        == []
    )
    no_conf = SignalEngineB(("B2",))
    assert feed(no_conf, b(10, 0, 96.5, 97.2, 96.8, c=below), b(10, 5, 96.7, 97.2, 97.1, c=below)) == []
    never_below = SignalEngineB(("B2",))
    above = ctx(session_low=97.5)
    assert (
        feed(never_below, b(10, 0, 97.5, 98, 96.8, c=above, exhaustion=1), b(10, 5, 97, 97.3, 97.2, c=above))
        == []
    )
    sellers = SignalEngineB(("B2",))
    assert (
        feed(
            sellers,
            b(10, 0, 96.5, 97.2, 96.8, c=below, cvd_divergence=1),
            b(10, 5, 96.7, 97.2, 97.1, c=below, delta=-5),
        )
        == []
    )
    no_prior = SignalEngineB(("B2",))
    assert (
        feed(
            no_prior,
            b(10, 0, 96.5, 97.2, 96.8, c=ctx(prior_day={})),
            b(10, 5, 96.7, 97.2, 97.1, c=ctx(prior_day={})),
        )
        == []
    )


def test_b3_vwap_pullback_on_a_buyers_day():
    e = SignalEngineB(("B3",))
    ups = [b(9, 35 + 5 * i, 99.8, 100.4, 100.2) for i in range(12)]  # 09:35..10:30, all above VWAP
    pull = b(10, 35, 99.52, 100.1, 99.9, delta=30)  # low within 1 row of VWAP, close above
    sigs = feed(e, *ups, pull)
    assert [s.setup for s in sigs] == ["B3"] and sigs[0].zone_low == round(99.5 - 0.4, 4)
    mixed = SignalEngineB(("B3",))
    downs = [b(9, 35 + 5 * i, 99.0, 99.4, 99.2) for i in range(6)] + [
        b(10, 5 + 5 * i, 99.8, 100.4, 100.2) for i in range(6)
    ]
    assert feed(mixed, *downs, pull) == []  # only half the closes above VWAP
    from_below = SignalEngineB(("B3",))
    assert feed(from_below, *ups[:-1], b(10, 30, 99.3, 99.6, 99.4), pull) == []  # previous bar wasn't above
    no_sigma = SignalEngineB(("B3",))
    assert feed(no_sigma, *ups, b(10, 35, 99.52, 100.1, 99.9, c=ctx(vwap_sigma=None))) == []


def test_b4_afternoon_sweep_near_the_high():
    e = SignalEngineB(("B4",))
    feed(e, b(13, 5, 100.4, 100.9, 100.8))
    assert feed(e, sweep(12, 59)) == []  # before 13:00
    sigs = feed(e, sweep(13, 6))
    assert [s.setup for s in sigs] == ["B4"] and sigs[0].zone_low == 99.5 and sigs[0].zone_high == 100.8
    assert sigs[0].facts["sweeps_in_bar"] == "yes"
    assert feed(e, sweep(13, 7)) == []  # once a day
    far = SignalEngineB(("B4",))
    feed(far, b(13, 5, 98.4, 99.9, 99.6, c=ctx(session_high=103.0)))  # 3.4 below the high > 25% of ATR
    assert feed(far, sweep(13, 6)) == []
    sells = SignalEngineB(("B4",))
    feed(sells, b(13, 5, 100.4, 100.9, 100.8))
    assert feed(sells, sweep(13, 6, side=-1)) == [] and feed(sells, sweep(13, 6, vwap=99.0)) == []
    assert SignalEngineB(("B4",)).on_record(sweep(13, 6)) == []  # no bar seen yet
    bare = SignalEngineB(("B4",))
    feed(bare, b(13, 5, 100.4, 100.9, 100.8, c=ctx(vwap=None)))
    assert feed(bare, sweep(13, 6)) == []


def test_gap_bars_empty_bars_and_new_days():
    e = SignalEngineB()
    assert feed(e, b(10, 5, 100.4, 100.9, 100.8), b(10, 10, 100.7, 101.2, 101.1, gap=True)) == []
    assert e.on_record(b(10, 15, None, None, None, trades=0)) == []
    assert e.on_record({"type": "bar", "tf": 60, "symbol": "SPY"}) == []
    assert e.on_record({"type": "level"}) == []
    tomorrow = ctx(day="2026-10-28", session_high=100.9, session_low=98.0)
    sigs = feed(e, b(10, 20, 100.4, 100.9, 100.8, c=tomorrow), b(10, 25, 100.7, 101.2, 101.1, c=tomorrow))
    assert [s.setup for s in sigs] == ["B1"]  # a new day resets the once-a-day rule


def sig(zone_low=99.5, atr=5.0):
    return Signal("B1", "SPY", 0, "2026-10-27", zone_low, 100.8, 0.05, atr, (), (), "x")


def test_plan_b_band_hold_and_2r():
    p = plan_b(sig(), 100.8, "level", "hold")
    assert p.stop == 99.45 and math.isinf(p.target)  # 1.35 below: inside 0.75..3.0
    p = plan_b(sig(), 100.8, "level", "2r")
    assert p.target == round(100.8 + 2 * (100.8 - 99.45), 2)
    assert plan_b(sig(zone_low=100.6), 100.8, "level", "hold").stop == 100.05  # widened to 15% of ATR
    assert plan_b(sig(zone_low=97.0), 100.8, "level", "hold") is None  # 3.85 > 60% of ATR
    assert plan_b(sig(), 100.8, "atr30", "hold").stop == 99.3  # control: 30% of ATR
    assert plan_b(sig(atr=None), 100.8, "level", "hold") is None
    assert plan_b(sig(), 0.0, "level", "hold") is None


def test_facts_b_unknowns():
    f = facts_b("B2", 100.0, {"vwap": None}, {})
    assert f["price_vs_vwap"] == "unknown" and f["bar_delta"] == "unknown" and f["tape_speed"] == "unknown"
    assert facts_b("B2", 100.0, {"vwap": 101}, {"delta": 0})["bar_delta"] == "even"
    assert facts_b("B2", 100.0, {"vwap": 101}, {"delta": -3})["price_vs_vwap"] == "below"
