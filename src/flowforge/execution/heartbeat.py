"""Heartbeat file the trading node writes every second and the watchdog reads.

It carries what the watchdog needs to decide on its own: when the node last ran, which
positions it holds, and whether it is halted. Written atomically (temp file + rename).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from flowforge.risk.calendar import NS


@dataclass(frozen=True)
class Heartbeat:
    ts_ns: int
    trader: str
    positions: dict[str, int] = field(default_factory=dict)
    open_orders: int = 0
    halt: str | None = None
    kill: bool = False
    equity: float | None = None  # marked at the bid; None from nodes older than Phase 6
    day_start_equity: float | None = None

    def age_s(self, now_ns: int) -> float:
        return (now_ns - self.ts_ns) / NS


def write_heartbeat(path: str | Path, beat: Heartbeat) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(beat)))
    os.replace(tmp, path)


def read_heartbeat(path: str | Path) -> Heartbeat | None:
    """None when there is no heartbeat or it can't be parsed (both mean: node not seen)."""
    try:
        data = json.loads(Path(path).read_text())
        return Heartbeat(
            int(data["ts_ns"]),
            str(data["trader"]),
            {str(k): int(v) for k, v in data.get("positions", {}).items()},
            int(data.get("open_orders", 0)),
            data.get("halt"),
            bool(data.get("kill", False)),
            _float(data.get("equity")),
            _float(data.get("day_start_equity")),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def wall_clock_ns() -> int:
    return time.time_ns()
