"""Fast simulator vs full Nautilus backtest cross-check."""

from __future__ import annotations

from datetime import date

from flowforge.research.crosscheck import compare, nautilus_trades, run_nautilus
from flowforge.research.fastsim import Trade

INTENT = "H1:SPY:100"


def fast(**kw):
    base = dict(
        setup="H1",
        symbol="SPY",
        day="d",
        config="zone/2r",
        signal_ts=100,
        entry_ts=101,
        entry=100.0,
        shares=10,
        stop=99.0,
        target=102.0,
        exit_ts=5_000_000_000,
        exit=102.0,
        exit_reason="target",
        gross_usd=0,
        cost_usd=0,
        net_usd=0,
        r=2.0,
    )
    return Trade(**(base | kw))


def events(exit_reason="target 102.0 reached (bid 102.0)", sell_px=102.01, stop_fill=False):
    ev = [
        {"event": "order_submit", "order": "O-1", "intent": INTENT, "ts_ns": 1},
        {"event": "fill", "order": "O-1", "quantity": 1, "price": 100.0, "ts_ns": 101},
        {"event": "stop_submit", "order": "O-2", "intent": INTENT, "ts_ns": 102},
    ]
    if stop_fill:
        ev.append({"event": "fill", "order": "O-2", "quantity": 1, "price": sell_px, "ts_ns": 5_000_000_000})
    else:
        ev += [
            {
                "event": "exit_submit",
                "order": "O-3",
                "intent": INTENT,
                "reason": exit_reason,
                "ts_ns": 4_900_000_000,
            },
            {"event": "fill", "order": "O-3", "quantity": 1, "price": sell_px, "ts_ns": 5_100_000_000},
        ]
    ev.append({"event": "fill", "order": "UNKNOWN", "quantity": 1, "price": 1.0, "ts_ns": 9})
    return ev


def test_matching_trades():
    n = nautilus_trades(events())
    assert n[INTENT]["exit_reason"] == "target" and n[INTENT]["buy_px"] == 100.0
    res = compare([fast()], n)
    assert res["matched"] == 1 and res["only_in_nautilus"] == []
    stop = nautilus_trades(events(stop_fill=True, sell_px=99.0))
    assert stop[INTENT]["exit_reason"] == "stop"
    assert compare([fast(exit=99.0, exit_reason="stop")], stop)["matched"] == 1
    eod = nautilus_trades(events("end of day"))
    assert eod[INTENT]["exit_reason"] == "eod"


def test_mismatches_are_reported():
    res = compare([fast(exit=101.0)], nautilus_trades(events()))
    assert res["matched"] == 0 and res["rows"][0]["checks"]["exit_price"] is False
    res = compare([fast(signal_ts=7)], nautilus_trades(events()))
    assert res["rows"][0]["why"] == "not traded in Nautilus" and res["only_in_nautilus"] == [INTENT]


def test_full_backtest_runs_on_stored_ticks(limits, tmp_path):
    from flowforge.features.sample import make_ticks, write_ticks_dir

    day = date(2026, 10, 23)
    write_ticks_dir(tmp_path / "ticks", "AAPL", day, *make_ticks(day, minutes=20))
    events, records = run_nautilus(
        tmp_path / "ticks", "AAPL", day, {"H4": ("zone", "volume")}, limits, tmp_path
    )
    assert any(r["type"] == "bar" and r["tf"] == 300 for r in records)
    assert all(e["event"] in ("order_submit", "stop_submit", "exit_submit", "fill") for e in events)
