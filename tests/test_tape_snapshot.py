"""Dashboard tape snapshot written by the node (strategies/tape.py)."""

from __future__ import annotations

import json

from flowforge.risk.calendar import NS
from flowforge.strategies.tape import BARS_KEPT, TapeSnapshot


def test_keeps_recent_bars_and_events_and_writes_at_most_every_5_s(tmp_path):
    snap = TapeSnapshot(tmp_path / "run" / "tape.json")
    assert snap.maybe_write(0) is False  # nothing yet
    for i in range(BARS_KEPT + 5):
        snap.on_record({"type": "bar", "symbol": "SPY", "tf": 60, "end": f"t{i}", "close": i, "delta": 1})
    snap.on_record({"type": "bar", "symbol": "SPY", "tf": 300, "end": "x"})  # not a 1-minute bar
    snap.on_record(
        {"type": "sweep", "symbol": "QQQ", "end": "t", "side": -1, "shares": 5, "notional": 9.0, "vwap": 1.8}
    )
    snap.on_record({"type": "day_stats", "symbol": "SPY"})
    snap.on_record({"type": "bar"})  # no symbol
    assert snap.maybe_write(10 * NS) is True
    data = json.loads((tmp_path / "run" / "tape.json").read_text())
    assert (
        len(data["symbols"]["SPY"]["bars"]) == BARS_KEPT and data["symbols"]["SPY"]["bars"][-1]["close"] == 64
    )
    assert data["symbols"]["QQQ"]["events"][0] == {
        "ts": "t",
        "kind": "sweep",
        "side": -1,
        "shares": 5,
        "notional": 9.0,
        "price": 1.8,
    }
    snap.on_record({"type": "bar", "symbol": "SPY", "tf": 60, "end": "n", "close": 1})
    assert snap.maybe_write(12 * NS) is False  # too soon
    assert snap.maybe_write(15 * NS) is True
    assert snap.maybe_write(30 * NS) is False  # nothing new
