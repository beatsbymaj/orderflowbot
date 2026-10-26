"""Account state the risk limits depend on: day and week equity marks, halts, entry counts.

Loss limits (config/risk_limits.yaml `loss`):
- daily: equity down `max_daily_loss_pct` from the day's starting equity. Flatten, halt
  for the rest of the day.
- weekly: equity down `max_weekly_drawdown_pct` from the week's peak. Flatten, halt until
  the operator re-enables trading (`python -m flowforge.ops.riskctl reenable`).

Halts and entry counts are written to a JSON file after every change so a restart can't
clear them. An unreadable state file halts trading (fail closed) until the operator looks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from flowforge.config.risk_limits import RiskLimits
from flowforge.risk.calendar import is_trading_day

PDT_WINDOW_DAYS = 5
# Claude default (not in risk_limits.yaml): this many rejected or denied entries in a row
# halts new entries for the day. Something is wrong with the orders or the account.
MAX_CONSECUTIVE_REJECTS = 3


@dataclass(frozen=True)
class Halt:
    kind: str  # daily | weekly | errors | kill | state
    reason: str
    since: str
    day: str


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def trading_days_back(day: date, count: int) -> list[date]:
    """The `count` trading days ending on `day` (inclusive if it is one)."""
    days: list[date] = []
    d = day
    for _ in range(count * 4 + 10):  # bounded walk; holidays never cluster this much
        if is_trading_day(d):
            days.append(d)
            if len(days) == count:
                break
        d -= timedelta(days=1)
    return days


class RiskState:
    def __init__(self, limits: RiskLimits, path: str | Path | None = None) -> None:
        self.limits = limits
        self.path = Path(path) if path is not None else None
        self.day: date | None = None
        self.day_start_equity = 0.0
        self.week: tuple[int, int] | None = None
        self.week_peak_equity = 0.0
        self.daily_halt: Halt | None = None
        self.weekly_halt: Halt | None = None
        self.entries_by_day: dict[str, int] = {}
        self.seen_intents: set[str] = set()
        self.consecutive_rejects = 0
        self._load()

    # -- persistence ------------------------------------------------------------------
    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.day = date.fromisoformat(data["day"]) if data.get("day") else None
            self.day_start_equity = float(data.get("day_start_equity", 0.0))
            week = data.get("week")
            self.week = (int(week[0]), int(week[1])) if week else None
            self.week_peak_equity = float(data.get("week_peak_equity", 0.0))
            self.daily_halt = Halt(**data["daily_halt"]) if data.get("daily_halt") else None
            self.weekly_halt = Halt(**data["weekly_halt"]) if data.get("weekly_halt") else None
            self.entries_by_day = {str(k): int(v) for k, v in data.get("entries_by_day", {}).items()}
            self.seen_intents = set(data.get("seen_intents", []))
            self.consecutive_rejects = int(data.get("consecutive_rejects", 0))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.weekly_halt = Halt("state", f"risk state file unreadable: {exc}", _now_iso(), "")

    def save(self) -> None:
        if self.path is None:
            return
        data = {
            "day": self.day.isoformat() if self.day else None,
            "day_start_equity": self.day_start_equity,
            "week": list(self.week) if self.week else None,
            "week_peak_equity": self.week_peak_equity,
            "daily_halt": vars(self.daily_halt) if self.daily_halt else None,
            "weekly_halt": vars(self.weekly_halt) if self.weekly_halt else None,
            "entries_by_day": self.entries_by_day,
            "seen_intents": sorted(self.seen_intents),
            "consecutive_rejects": self.consecutive_rejects,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, self.path)

    # -- day / week -------------------------------------------------------------------
    def start_day(self, day: date, equity: float) -> None:
        """Call once per session before trading, with marked-to-market equity."""
        if day == self.day:
            return
        self.day = day
        self.day_start_equity = equity
        self.seen_intents.clear()
        self.consecutive_rejects = 0
        if self.daily_halt is not None and self.daily_halt.day != day.isoformat():
            self.daily_halt = None
        week = day.isocalendar()[:2]
        if week != self.week:
            self.week = week
            self.week_peak_equity = equity  # the weekly halt itself is only cleared by the operator
        keep = {d.isoformat() for d in trading_days_back(day, PDT_WINDOW_DAYS)}
        self.entries_by_day = {d: n for d, n in self.entries_by_day.items() if d in keep}
        self.save()

    def on_equity(self, equity: float) -> str | None:
        """Mark equity; returns the loss limit this mark breached ("daily" or "weekly"), if any."""
        if self.day is None:
            return None
        self.week_peak_equity = max(self.week_peak_equity, equity)
        lim = self.limits.loss
        breach = None
        if self.weekly_halt is None and self.week_peak_equity > 0:
            dd = (self.week_peak_equity - equity) / self.week_peak_equity * 100
            if dd >= lim.max_weekly_drawdown_pct:
                reason = f"weekly drawdown {dd:.2f}% >= {lim.max_weekly_drawdown_pct}%"
                self.weekly_halt = Halt("weekly", reason, _now_iso(), self.day.isoformat())
                breach = "weekly"
        if self.daily_halt is None and self.day_start_equity > 0:
            loss = (self.day_start_equity - equity) / self.day_start_equity * 100
            if loss >= lim.max_daily_loss_pct:
                reason = f"daily loss {loss:.2f}% >= {lim.max_daily_loss_pct}%"
                self.daily_halt = Halt("daily", reason, _now_iso(), self.day.isoformat())
                breach = breach or "daily"
        if breach:
            self.save()
        return breach

    # -- halts ------------------------------------------------------------------------
    def halt_day(self, kind: str, reason: str) -> None:
        if self.daily_halt is None:
            day = self.day.isoformat() if self.day else ""
            self.daily_halt = Halt(kind, reason, _now_iso(), day)
            self.save()

    def halt_reason(self) -> str | None:
        if self.weekly_halt is not None:
            return f"halted until re-enabled: {self.weekly_halt.reason}"
        if self.daily_halt is not None:
            return f"halted for the day: {self.daily_halt.reason}"
        return None

    def reenable(self) -> bool:
        """Operator only (riskctl). Clears the weekly halt; returns whether there was one."""
        had = self.weekly_halt is not None
        self.weekly_halt = None
        self.save()
        return had

    # -- counts -----------------------------------------------------------------------
    def trades_today(self) -> int:
        return self.entries_by_day.get(self.day.isoformat(), 0) if self.day else 0

    def day_trades_in_window(self) -> int:
        """Every entry is flattened the same day, so each entry counts as a day trade."""
        if self.day is None:
            return 0
        return sum(self.entries_by_day.get(d.isoformat(), 0) for d in trading_days_back(self.day, 5))

    def record_entry(self, intent_id: str) -> None:
        assert self.day is not None, "start_day must be called before entries"
        self.seen_intents.add(intent_id)
        key = self.day.isoformat()
        self.entries_by_day[key] = self.entries_by_day.get(key, 0) + 1
        self.save()

    def record_reject(self, reason: str) -> bool:
        """Count a rejected or denied entry; True when this one trips the error halt."""
        self.consecutive_rejects += 1
        tripped = self.consecutive_rejects >= MAX_CONSECUTIVE_REJECTS
        if tripped:
            self.halt_day("errors", f"{self.consecutive_rejects} entries rejected in a row (last: {reason})")
        else:
            self.save()
        return tripped

    def record_accept(self) -> None:
        if self.consecutive_rejects:
            self.consecutive_rejects = 0
            self.save()
