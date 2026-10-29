"""Tape snapshot for the dashboard's Tape page.

The node keeps, per symbol, the last finished 1-minute bars (close, volume, delta, CVD) and
the latest large prints and sweeps from the feature records, and writes them to one JSON
file every few seconds (temp file + rename, like the heartbeat). The dashboard only reads
that file. Nothing here can place an order.
"""

from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path

from flowforge.risk.calendar import NS

BARS_KEPT = 60  # one hour of 1-minute bars
EVENTS_KEPT = 20
WRITE_EVERY_S = 5
BAR_TF = 60


class TapeSnapshot:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.bars: dict[str, deque] = {}
        self.events: dict[str, deque] = {}
        self._last_write_ns: int | None = None
        self._dirty = False

    def on_record(self, rec: dict) -> None:
        sym = rec.get("symbol")
        if not sym:
            return
        kind = rec.get("type")
        if kind == "bar" and rec.get("tf") == BAR_TF:
            self.bars.setdefault(sym, deque(maxlen=BARS_KEPT)).append(
                {
                    "end": rec["end"],
                    "close": rec.get("close"),
                    "volume": rec.get("volume", 0),
                    "delta": rec.get("delta", 0),
                    "cvd": rec.get("cvd", 0),
                    "spread_bps": (rec.get("nbbo") or {}).get("spread_bps_avg"),
                }
            )
        elif kind == "large_print":
            self._event(
                sym, rec["ts"], "large print", rec["side"], rec["size"], rec["notional"], rec["price"]
            )
        elif kind == "sweep":
            self._event(sym, rec["end"], "sweep", rec["side"], rec["shares"], rec["notional"], rec["vwap"])
        else:
            return
        self._dirty = True

    def _event(self, sym: str, ts: str, kind: str, side: int, shares: int, notional: float, price) -> None:
        self.events.setdefault(sym, deque(maxlen=EVENTS_KEPT)).append(
            {"ts": ts, "kind": kind, "side": side, "shares": shares, "notional": notional, "price": price}
        )

    def maybe_write(self, now_ns: int) -> bool:
        if not self._dirty:
            return False
        if self._last_write_ns is not None and now_ns - self._last_write_ns < WRITE_EVERY_S * NS:
            return False
        self._last_write_ns = now_ns
        self._dirty = False
        body = {
            "ts_ns": now_ns,
            "symbols": {
                s: {"bars": list(self.bars.get(s, ())), "events": list(self.events.get(s, ()))}
                for s in sorted(set(self.bars) | set(self.events))
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(body))
        os.replace(tmp, self.path)
        return True
