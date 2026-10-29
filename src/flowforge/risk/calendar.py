"""NYSE regular-hours calendar for the RiskGuard.

Source (checked 2026-10-26): https://www.nyse.com/trade/hours-calendars
- Core session 9:30 a.m. to 4:00 p.m. ET.
- Early closes at 1:00 p.m. ET: 2026-11-27, 2026-12-24, 2027-11-26, 2028-07-03, 2028-11-24.
NYSE publishes three years ahead. A date outside KNOWN_YEARS has no session here, so the
guard refuses entries on it (fail closed) until this table is extended from the same page.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
OPEN = time(9, 30)
CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)
NS = 1_000_000_000

KNOWN_YEARS = frozenset({2026, 2027, 2028})
HOLIDAYS = frozenset(
    date.fromisoformat(d)
    for d in (
        # 2026
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
        "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
        # 2027
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
        "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
        # 2028 (New Year's Day falls on a Saturday and is not observed)
        "2028-01-17", "2028-02-21", "2028-04-14", "2028-05-29", "2028-06-19",
        "2028-07-04", "2028-09-04", "2028-11-23", "2028-12-25",
    )
)  # fmt: skip
EARLY_CLOSES = frozenset(
    date.fromisoformat(d) for d in ("2026-11-27", "2026-12-24", "2027-11-26", "2028-07-03", "2028-11-24")
)


@dataclass(frozen=True)
class Session:
    day: date
    open_ns: int
    close_ns: int

    @property
    def early_close(self) -> bool:
        return self.day in EARLY_CLOSES


def _ns(day: date, at: time) -> int:
    return int(datetime.combine(day, at, tzinfo=ET).timestamp()) * NS


def et_date(ts_ns: int) -> date:
    return datetime.fromtimestamp(ts_ns / NS, UTC).astimezone(ET).date()


def session_for(day: date) -> Session | None:
    """The regular session on `day`, or None when the market is closed or the date is unknown."""
    if day.year not in KNOWN_YEARS or day.weekday() >= 5 or day in HOLIDAYS:
        return None
    return Session(day, _ns(day, OPEN), _ns(day, EARLY_CLOSE if day in EARLY_CLOSES else CLOSE))


def session_at(ts_ns: int) -> Session | None:
    return session_for(et_date(ts_ns))


def next_trading_day(day: date) -> date:
    """The first trading day after `day` (T+1 settlement). Past the known years it keeps
    walking, so proceeds never settle early: unknown dates aren't trading days."""
    d = day + timedelta(days=1)
    for _ in range(10):
        if is_trading_day(d):
            return d
        d += timedelta(days=1)
    return d


def is_trading_day(day: date) -> bool:
    return session_for(day) is not None
