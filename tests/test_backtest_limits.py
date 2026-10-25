from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from flowforge.backtest.bars import ET
from flowforge.backtest.limits import LimitBook
from flowforge.config.risk_limits import RiskLimits

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "risk_limits.yaml"
DAY = date(2025, 3, 3)  # a Monday
OPEN = datetime(2025, 3, 3, 9, 30, tzinfo=ET)
CLOSE = datetime(2025, 3, 3, 16, 0, tzinfo=ET)
NOW = datetime(2025, 3, 3, 10, 0, tzinfo=ET)


def _limits(**overrides) -> RiskLimits:
    data = yaml.safe_load(REPO_CONFIG.read_text())
    for dotted, value in overrides.items():
        section, key = dotted.split("__")
        data[section][key] = value
    return RiskLimits.model_validate(data)


def _book(equity=10_000.0, **overrides) -> LimitBook:
    book = LimitBook(_limits(**overrides), equity)
    book.start_day(DAY)
    return book


def _check(book, symbol="SPY", now=NOW, price=100.0, stop=99.0, volume=10_000.0, equity=None):
    return book.check_entry(symbol, now, OPEN, CLOSE, price, stop, volume, equity or book.cash_equity)


def test_sizing_takes_the_smallest_cap():
    d = _check(_book(), price=100.0, stop=99.0)
    # risk: 0.5% of 10k = $50 / $1 = 50; notional: 10% = $1000 / $100 = 10
    assert d.allowed and d.quantity == 10
    assert d.sizing == {"by_risk": 50, "by_notional": 10, "by_gross": 100, "by_volume": 500}
    wide = _check(_book(), price=100.0, stop=90.0)
    assert wide.quantity == 5  # $50 / $10 per share
    thin = _check(_book(), volume=60.0)
    assert thin.quantity == 3  # 5% of 60 shares a minute


def test_size_below_one_share_is_refused():
    d = _check(_book(), price=2_000.0, stop=1_990.0)
    assert not d.allowed and d.reason == "size rounds to zero shares"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"now": OPEN + timedelta(minutes=4)}, "inside no-entry window after the open"),
        ({"now": CLOSE - timedelta(minutes=14)}, "inside no-entry window before the close"),
        ({"stop": None}, "no valid stop below entry"),
        ({"stop": 100.0}, "no valid stop below entry"),
        ({"price": 4.0, "stop": 3.5}, "price below universe minimum"),
    ],
)
def test_entry_refusals(kwargs, reason):
    d = _check(_book(), **kwargs)
    assert not d.allowed and d.reason == reason


def test_position_count_symbol_and_trades_per_day():
    book = _book(trading__pdt_regime="intraday_margin", trading__max_trades_per_day=4)
    for s in ["A", "B", "C"]:
        book.record_entry(s, 1_000)
    assert _check(book, symbol="A").reason == "already holding this symbol"
    assert _check(book, symbol="D").reason == "max concurrent positions reached"
    book.record_exit("A", 10.0)
    book.record_entry("A", 1_000)
    book.record_exit("A", -5.0)
    assert book.realized == 5.0
    assert _check(book, symbol="D").reason == "max trades per day reached"


def test_gross_exposure_cap():
    book = _book(trading__pdt_regime="intraday_margin", portfolio__max_gross_exposure_pct=15)
    book.record_entry("A", 1_000)
    d = _check(book, symbol="B")
    assert d.sizing["by_gross"] == 5 and d.quantity == 5


def test_legacy_pdt_counts_day_trades_over_five_trading_days():
    book = _book()
    for s in ["A", "B", "C"]:
        book.record_entry(s, 1_000)
        book.record_exit(s, 0.0)
    assert _check(book).reason == "pattern day trader limit"
    for offset in range(1, 5):  # Tue..Fri: the Monday trades are still in the window
        book.start_day(DAY + timedelta(days=offset))
        assert _check(book).reason == "pattern day trader limit"
    book.start_day(DAY + timedelta(days=7))  # next Monday: Monday's trades dropped out
    assert _check(book).allowed
    # At or above $25k the legacy rule doesn't apply.
    assert _check(_book(equity=30_000.0, **{"trading__legacy_pdt_max_day_trades": 0})).allowed


def test_daily_loss_halts_for_the_day_only():
    book = _book(trading__pdt_regime="intraday_margin")
    assert book.check_loss_limits(9_850.0) is None
    assert book.check_loss_limits(9_800.0) == "daily"
    assert book.check_loss_limits(9_700.0) is None  # already halted, reported once
    assert _check(book).reason == "daily loss halt"
    book.realized = -200.0
    book.start_day(DAY + timedelta(days=1))
    assert _check(book).allowed


def test_weekly_drawdown_halts_until_next_week():
    book = _book(trading__pdt_regime="intraday_margin")
    book.check_loss_limits(10_500.0)  # new weekly peak
    book.realized = -300.0
    book.start_day(DAY + timedelta(days=1))
    assert book.check_loss_limits(9_975.0) == "weekly"  # 5% under the 10,500 peak
    assert _check(book).reason == "weekly drawdown halt"
    book.start_day(DAY + timedelta(days=2))
    assert _check(book).reason == "weekly drawdown halt"
    book.start_day(DAY + timedelta(days=7))
    assert _check(book).allowed
