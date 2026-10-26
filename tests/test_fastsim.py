"""Fast simulator and quote reader (docs/research/14, "Costs and fills")."""

from __future__ import annotations

from conftest import DAY, et_ns

from flowforge.research.fastsim import Costs, Refusal, Trade, simulate
from flowforge.research.quotes import CHUNK_NS, MemoryQuotes, iter_from, last_before
from flowforge.signals.setups import Signal

MS = 1_000_000


def sig(ts=None, **kw) -> Signal:
    base = {
        "setup": "H1",
        "symbol": "SPY",
        "ts": ts or et_ns(10, 35),
        "day": DAY.isoformat(),
        "zone_low": 99.0,
        "zone_high": 99.3,
        "row": 0.05,
        "atr": 5.0,
        "heavy_above": (),
        "unfinished_lows": (),
        "reason": "test",
    }
    return Signal(**(base | kw))


def path(*points):
    """(seconds after 10:35, bid, ask)"""
    t0 = et_ns(10, 35)
    return MemoryQuotes({"SPY": [(t0 + int(s * 1e9), b, a) for s, b, a in points]})


def test_quote_reader_crosses_chunks_and_looks_back():
    t0 = et_ns(10, 35)
    q = MemoryQuotes({"SPY": [(t0 - 1, 1.0, 1.1), (t0 + CHUNK_NS, 2.0, 2.1), (t0 + 2 * CHUNK_NS, 3.0, 3.1)]})
    assert [x[1] for x in iter_from(q, "SPY", t0 - 1, t0 + 2 * CHUNK_NS + 1)] == [1.0, 2.0, 3.0]
    assert [x[1] for x in iter_from(q, "SPY", t0, t0 + CHUNK_NS + 1)] == [2.0]
    assert last_before(q, "SPY", t0 + CHUNK_NS - 5, 2 * CHUNK_NS)[1] == 1.0
    assert last_before(q, "SPY", t0 + 10, 5) is None
    assert last_before(q, "OTHER", t0, CHUNK_NS) is None


def test_target_exit_with_costs_in_r():
    q = path((-0.5, 99.58, 99.60), (0.1, 99.59, 99.61), (60, 100.60, 100.62), (61, 100.70, 100.72))
    t = simulate(sig(), "zone", "2r", q)
    assert isinstance(t, Trade)
    assert t.entry == 99.61 and t.stop == 98.95 and t.target == 100.9
    # 2R target 98.95 + ... = 100.90: never reached; flat at 15:55 at the last bid.
    assert t.exit_reason == "eod" and t.exit == 100.70
    t = simulate(sig(), "zone", "2r", path((-0.5, 99.58, 99.60), (0.1, 99.59, 99.61), (60, 100.95, 100.97)))
    assert t.exit_reason == "target" and t.exit == 100.95
    shares = int(50 // (99.60 - 98.95))
    assert t.shares == shares
    cost = 0.005 * shares * 2 + 0.0000206 * 100.95 * shares
    assert abs(t.cost_usd - round(cost, 4)) < 1e-9
    assert abs(t.r - ((100.95 - 99.61) * shares - cost) / ((99.61 - 98.95) * shares)) < 1e-5


def test_stop_fills_through_at_the_bid_after_latency():
    q = path((-0.5, 99.58, 99.60), (0.1, 99.59, 99.61), (30, 98.90, 98.92), (30.1, 98.70, 98.72))
    t = simulate(sig(), "zone", "2r", q)
    assert t.exit_reason == "stop" and t.exit == 98.70  # quote in force 150 ms after the trigger
    assert t.r < -1


def test_refusals_like_risk_guard():
    assert simulate(sig(), "zone", "2r", path((-5, 99.58, 99.60))).reason == "no fresh quote"
    assert simulate(sig(), "zone", "2r", path((-0.5, 99.60, 99.60))).reason == "locked or crossed"
    assert simulate(sig(), "zone", "2r", path((-0.5, 99.40, 99.60))).reason == "wide spread"
    r = simulate(sig(zone_low=97.0), "zone", "2r", path((-0.5, 99.58, 99.60)))
    assert isinstance(r, Refusal) and r.reason == "no plan"
    jumped = path((-0.5, 99.58, 99.60), (0.1, 99.80, 99.82))
    assert simulate(sig(), "zone", "2r", jumped).reason == "IOC not filled"
    assert (
        simulate(sig(), "zone", "2r", path((-0.5, 99.58, 99.60)), Costs(risk_usd=0.1)).reason == "size zero"
    )
    crash = path((-0.5, 99.58, 99.60), (0.1, 98.80, 98.82))
    assert simulate(sig(zone_low=99.5), "zone", "2r", crash, Costs(slippage_bps=200)).reason == (
        "ask below stop at arrival"
    )
