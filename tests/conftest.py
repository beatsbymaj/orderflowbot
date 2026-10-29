"""Shared helpers for the RiskGuard, order manager and chaos tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from flowforge.config.risk_limits import RiskLimits, load_risk_limits
from flowforge.risk.calendar import ET, NS
from flowforge.risk.guard import AccountView, EntryRequest, InstrumentInfo, MarketView, QuoteView

REPO_LIMITS = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"
# Tuesday 2026-10-27, a normal full session.
DAY = datetime(2026, 10, 27, tzinfo=ET).date()


def et_ns(hour: int, minute: int, second: float = 0, day=DAY) -> int:
    whole = int(second)
    dt = datetime(day.year, day.month, day.day, hour, minute, whole, tzinfo=ET)
    return int(dt.timestamp()) * NS + int((second - whole) * NS)


def with_limits(limits: RiskLimits, section: str, **changes) -> RiskLimits:
    part = getattr(limits, section).model_copy(update=changes)
    return limits.model_copy(update={section: part})


def quote(bid=100.00, ask=100.02, ts_ns=None, size=500) -> QuoteView:
    return QuoteView(bid, ask, size, size, ts_ns if ts_ns is not None else et_ns(10, 0))


def market(symbol="SPY", q=None, **kw) -> MarketView:
    defaults = {"avg_minute_volume": 100_000.0, "avg_daily_dollar_volume": 30e9}
    defaults.update(kw)
    return MarketView(symbol, q if q is not None else quote(), **defaults)


def account(equity=10_000.0, cash=None, **kw) -> AccountView:
    return AccountView(equity, equity if cash is None else cash, **kw)


def request(intent="sig-1", symbol="SPY", stop=99.00, target=102.00, **kw) -> EntryRequest:
    return EntryRequest(intent, symbol, stop, target, **kw)


def info(symbol="SPY", **kw) -> InstrumentInfo:
    return InstrumentInfo(symbol, **kw)


@pytest.fixture
def loaded():
    return load_risk_limits(REPO_LIMITS)


@pytest.fixture
def limits(loaded) -> RiskLimits:
    return loaded.limits
