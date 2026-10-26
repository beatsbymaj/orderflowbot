"""Append-only JSON-lines journal for every signal, risk decision, order event, fill and alert.

CLAUDE.md rule 6. One file per New York trading date under the journal directory. Phase 6
also streams these records to Redis and Postgres; the files stay as the local record.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flowforge.risk.calendar import NS, et_date


class Journal:
    def __init__(self, directory: str | Path | None = None, keep: bool = False) -> None:
        self.directory = Path(directory) if directory is not None else None
        self.records: list[dict] = []  # kept in memory when there is no directory, or keep=True
        self._keep = keep or directory is None
        self._fh = None
        self._day = None

    def write(self, ts_ns: int, event: str, **fields: Any) -> dict:
        record = {
            "ts": datetime.fromtimestamp(ts_ns / NS, UTC).isoformat(timespec="microseconds"),
            "ts_ns": ts_ns,
            "event": event,
            **fields,
        }
        if self._keep:
            self.records.append(record)
        if self.directory is not None:
            day = et_date(ts_ns)
            if day != self._day:
                self.close()
                self.directory.mkdir(parents=True, exist_ok=True)
                self._fh = (self.directory / f"{day.isoformat()}.jsonl").open("a", encoding="utf-8")
                self._day = day
            self._fh.write(json.dumps(record, default=str) + "\n")
            self._fh.flush()
        return record

    def events(self, name: str) -> list[dict]:
        return [r for r in self.records if r["event"] == name]

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
