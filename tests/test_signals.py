"""Phase 5 setups (docs/research/14-signal-hypotheses.md) on hand-built feature records."""

from __future__ import annotations

from conftest import DAY, et_ns

from flowforge.data.ticks import format_ts_ns
from flowforge.signals.setups import Signal, SignalEngine, facts, plan


def level(kind="stacked_imbalance", tf=1800, low=99.0, high=99.3, side=1, away=1, touches=0, created=None):
    return {
        "kind": kind,
        "tf": tf,
        "low": low,
        "high": high,
        "side": side,
        "created": created or format_ts_ns(et_ns(10, 0)),
        "away_bars": away,
        "touches": touches,
    }


def ctx(levels=(), **kw):
    by_kind: dict[str, list] = {}
    for lv in levels:
        by_kind.setdefault(lv["kind"], []).append(lv)
    base = {
        "day": DAY.isoformat(),
        "vwap": 99.5,
        "vwap_sigma": 0.4,
        "poc": 101.0,
        "val": 98.0,
        "vah": 101.5,
        "opening_range": {"high": 100.5, "low": 98.5, "complete": True},
        "prior_day": {"high": 102.0, "low": 97.0, "close": 100.0},
        "levels": by_kind,
    }
    return base | kw


def bar(h, m, low, high, close, open_=None, tf=300, levels=(), c=None, **kw):
    rec = {
        "type": "bar",
        "symbol": "SPY",
        "tf": tf,
        "end": format_ts_ns(et_ns(h, m)),
        "open": open_ if open_ is not None else high,
        "low": low,
        "high": high,
        "close": close,
        "row": 0.05,
        "trades": 100,
        "delta": 50,
        "gap": False,
        "big_bid": [],
        "absorption": [],
        "exhaustion": 0,
        "cvd_divergence": 0,
        "speed_ratio": 1.0,
        "vpin": 0.25,
        "nbbo": {"spread_bps_avg": 0.5},
        "context": c if c is not None else ctx(levels),
    }
    return rec | kw


def feed(engine, *records):
    out = []
    for r in records:
        out += engine.on_record(r)
    return out


def test_h1_fires_once_on_first_touch_from_above():
    lv = level()
    e = SignalEngine(("H1",))
    sigs = feed(e, bar(10, 30, 99.8, 100.2, 100.0, levels=[lv]), bar(10, 35, 99.2, 99.9, 99.6, levels=[lv]))
    assert [s.setup for s in sigs] == ["H1"]
    s = sigs[0]
    assert (s.zone_low, s.zone_high, s.atr) == (99.0, 99.3, 5.0)
    assert s.heavy_above == (101.0, 101.5)
    # A later return to the same level is a second touch: no signal.
    assert (
        feed(e, bar(10, 40, 99.8, 100.4, 100.3, levels=[lv]), bar(10, 45, 99.1, 99.9, 99.5, levels=[lv]))
        == []
    )


def test_h1_needs_a_pullback_from_above_and_a_close_in_or_over_the_zone():
    lv = level()
    e = SignalEngine(("H1",))
    # Came from below (previous low under the level high).
    assert (
        feed(e, bar(10, 30, 99.1, 99.25, 99.2, levels=[lv]), bar(10, 35, 99.0, 99.4, 99.3, levels=[lv])) == []
    )
    e = SignalEngine(("H1",))
    # Closed through the zone.
    assert (
        feed(e, bar(10, 30, 99.8, 100.2, 100.0, levels=[lv]), bar(10, 35, 98.5, 99.9, 98.9, levels=[lv]))
        == []
    )


def test_h1_respects_engine_counters_entry_window_and_gaps():
    e = SignalEngine(("H1",))
    touched = level(touches=1)
    assert (
        feed(
            e,
            bar(10, 30, 99.8, 100.2, 100.0, levels=[touched]),
            bar(10, 35, 99.2, 99.9, 99.6, levels=[touched]),
        )
        == []
    )
    e = SignalEngine(("H1",))
    lv = level()
    assert (
        feed(e, bar(15, 45, 99.8, 100.2, 100.0, levels=[lv]), bar(15, 50, 99.2, 99.9, 99.6, levels=[lv]))
        == []
    )
    e = SignalEngine(("H1",))
    assert (
        feed(
            e,
            bar(10, 30, 99.8, 100.2, 100.0, levels=[lv]),
            bar(10, 35, 99.2, 99.9, 99.6, levels=[lv], gap=True),
        )
        == []
    )
    e = SignalEngine(("H1",))
    five = level(tf=300)  # H1 uses 30-min levels only
    assert (
        feed(e, bar(10, 30, 99.8, 100.2, 100.0, levels=[five]), bar(10, 35, 99.2, 99.9, 99.6, levels=[five]))
        == []
    )


def test_h2_uses_five_minute_counters_already_updated_by_the_touching_bar():
    before = level("large_print", tf=300, away=2, touches=0)
    now = level("large_print", tf=300, away=0, touches=1)
    e = SignalEngine(("H2",))
    sigs = feed(
        e, bar(11, 0, 99.8, 100.2, 100.0, levels=[before]), bar(11, 5, 99.2, 99.9, 99.6, levels=[now])
    )
    assert [s.setup for s in sigs] == ["H2"]
    assert "large_print" in sigs[0].reason


def test_h3_multiple_node():
    lv = level("multiple_node", side=0)
    e = SignalEngine(("H3",))
    sigs = feed(e, bar(10, 30, 99.8, 100.2, 100.0, levels=[lv]), bar(10, 35, 99.2, 99.9, 99.6, levels=[lv]))
    assert [s.setup for s in sigs] == ["H3"]


def test_h4_zone_needs_a_confirmation_inside_it():
    e = SignalEngine(("H4",))
    prev = bar(10, 30, 98.6, 99.0, 98.8)
    plain = bar(10, 35, 97.9, 98.6, 98.3)
    assert feed(e, prev, plain) == []
    confirmed = bar(10, 40, 97.9, 98.6, 98.3, big_bid=[98.0])
    sigs = feed(e, confirmed)
    assert [s.setup for s in sigs] == ["H4"]
    assert sigs[0].reason == "VAL + big bid"


def test_h4_picks_the_highest_confirmed_zone_and_other_confirmations():
    e = SignalEngine(("H4",))
    lv = level("absorption", tf=300, low=98.4, high=98.45, side=0)
    rec = bar(10, 35, 97.9, 98.6, 98.5, open_=98.6, levels=[lv], cvd_divergence=1)
    sigs = feed(e, bar(10, 30, 98.6, 99.0, 98.8), rec)
    # The opening-range low (98.50) is the highest zone the bar closed in; CVD divergence confirms.
    assert sigs[0].zone_low == 98.5 and sigs[0].reason == "OR low + CVD divergence"
    e = SignalEngine(("H4",))
    sigs = feed(e, bar(10, 30, 98.6, 99.0, 98.8), bar(10, 35, 97.9, 98.6, 98.3, exhaustion=1))
    assert "seller exhaustion" in sigs[0].reason
    e = SignalEngine(("H4",))
    sigs = feed(e, bar(10, 30, 98.6, 99.0, 98.8), bar(10, 35, 97.9, 98.6, 98.3, absorption=[98.0]))
    assert "absorption" in sigs[0].reason


def test_h5_follows_a_buy_sweep_above_vwap_with_buyers_in_the_last_minute():
    e = SignalEngine(("H5",))
    feed(e, bar(10, 30, 99.8, 100.2, 100.0), bar(10, 31, 99.9, 100.1, 100.0, tf=60, delta=500))
    sweep = {
        "type": "sweep",
        "symbol": "SPY",
        "start": format_ts_ns(et_ns(10, 31, 10)),
        "end": format_ts_ns(et_ns(10, 31, 10.004)),
        "side": 1,
        "notional": 2_500_000.0,
        "low": 100.0,
        "high": 100.1,
        "vwap": 100.05,
    }
    sigs = feed(e, sweep)
    assert [s.setup for s in sigs] == ["H5"]
    assert sigs[0].zone_low == 100.0 and sigs[0].row == 0.05
    assert feed(e, sweep | {"side": -1}) == []
    assert feed(e, sweep | {"vwap": 99.0}) == []
    feed(e, bar(10, 32, 99.9, 100.1, 100.0, tf=60, delta=-10))
    assert feed(e, sweep) == []
    assert SignalEngine(("H5",)).on_record(sweep) == []  # no context yet


def test_records_that_are_not_signals_are_ignored():
    e = SignalEngine()
    assert e.on_record({"type": "day_stats"}) == []
    assert e.on_record(bar(10, 30, 99.8, 100.2, 100.0, tf=1800)) == []
    assert e.on_record(bar(10, 30, None, None, None, trades=0)) == []
    assert e.on_record(bar(10, 30, 99, 100, 99.5, tf=60, trades=0)) == []


def sig(**kw) -> Signal:
    base = {
        "setup": "H1",
        "symbol": "SPY",
        "ts": et_ns(10, 35),
        "day": DAY.isoformat(),
        "zone_low": 99.0,
        "zone_high": 99.3,
        "row": 0.05,
        "atr": 5.0,
        "heavy_above": (101.0, 101.5),
        "unfinished_lows": (),
        "reason": "test",
    }
    return Signal(**(base | kw))


def test_plan_zone_stop_inside_the_atr_band_and_volume_target():
    p = plan(sig(), 99.6, "zone", "volume")
    assert p.stop == 98.95 and round(p.risk, 2) == 0.65
    assert p.target == 100.95  # one row under the POC edge: 1.35 = 2.08R >= 1.5R


def test_plan_widens_a_too_close_stop_and_skips_a_too_far_one():
    p = plan(sig(zone_low=99.5), 99.6, "zone", "2r")
    assert p.stop == 99.1  # 10% of the 5.00 ATR
    assert p.target == 100.6
    assert plan(sig(zone_low=98.0), 99.6, "zone", "2r") is None  # 1.65 > 20% of ATR


def test_plan_atr_stop_volume_target_needs_one_and_a_half_r():
    p = plan(sig(), 99.6, "atr", "volume")
    assert p.stop == 98.85  # 15% of ATR
    assert p.target == 100.95  # POC edge: 1.35 / 0.75 = 1.8R
    assert plan(sig(heavy_above=(100.5, 101.5)), 99.6, "atr", "volume").target == 101.45  # 100.45 is 1.1R
    assert plan(sig(heavy_above=(100.0,)), 99.6, "atr", "volume") is None
    assert plan(sig(heavy_above=()), 99.6, "zone", "volume") is None


def test_plan_vetoes_unfinished_business_just_below_and_bad_inputs():
    assert plan(sig(unfinished_lows=(99.2,)), 99.6, "zone", "2r") is None
    assert plan(sig(unfinished_lows=(97.0,)), 99.6, "zone", "2r") is not None
    assert plan(sig(atr=None), 99.6, "zone", "2r") is None
    assert plan(sig(atr=0.0), 99.6, "zone", "2r") is None
    assert plan(sig(), 0.0, "zone", "2r") is None


def test_signal_id_and_facts_are_labels_not_numbers():
    s = sig()
    assert s.id.startswith("H1:SPY:")
    f = facts(
        "H4",
        bar(10, 35, 99, 100, 99.8, delta=-5, speed_ratio=2.0, vpin=None, large_prints=1),
        ctx(),
        "VAL + big bid",
    )
    assert f["setup"] == "support zone with confirmation"
    assert f["price_vs_vwap"] == "above" and f["bar_delta"] == "sellers"
    assert f["tape_speed"] == "fast" and f["vpin"] == "unknown" and f["spread"] == "tight"
    assert f["large_prints_in_bar"] == "yes" and f["sweeps_in_bar"] == "no"
    assert all(isinstance(v, str) for v in f.values())
