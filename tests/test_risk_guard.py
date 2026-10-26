import math
import shutil
from datetime import date

import pytest
from conftest import DAY, REPO_LIMITS, account, et_ns, info, market, quote, request, with_limits

from flowforge.config.risk_limits import PdtRegime, load_risk_limits
from flowforge.risk import calendar
from flowforge.risk.guard import (
    CLOCK_SKEW_TOLERANCE_MS,
    RESUME_COOLDOWN_S,
    InstrumentInfo,
    MarketView,
    QuoteView,
    RiskGuard,
    round_down,
)
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

NOW = et_ns(10, 0)


def make_guard(limits, tmp_path=None, kill=None, day=DAY):
    state = RiskState(limits if not hasattr(limits, "limits") else limits.limits)
    state.start_day(day, 10_000.0)
    return RiskGuard(limits, state, kill)


def check(guard, req=None, mkt=None, inst="default", acct=None, now=NOW):
    return guard.check_entry(
        req or request(),
        mkt or market(),
        info() if inst == "default" else inst,
        acct or account(),
        now,
    )


def test_clean_entry_is_sized_by_the_tightest_limit(limits):
    d = check(make_guard(limits))
    assert d.allowed and d.reasons == ()
    # ask 100.02 + 10 bps = 100.12002 -> 100.12; risk/share 1.12; 0.5% of 10k = $50 -> 44 shares
    assert d.plan.limit_price == 100.12
    assert d.sizing["by_risk"] == math.floor(50 / 1.12)
    assert d.sizing["by_notional"] == 9  # 10% of 10k / 100.12
    assert d.plan.quantity == 9
    assert d.as_dict()["plan"]["quantity"] == 9


def test_strategy_cap_and_volume_cap(limits):
    g = make_guard(limits)
    assert check(g, req=request(max_quantity=3)).plan.quantity == 3
    d = check(g, mkt=market(avg_minute_volume=40))  # 5% of 40 = 2 shares
    assert d.plan.quantity == 2


def test_cash_and_gross_exposure_limit_size(limits):
    g = make_guard(limits)
    d = check(g, acct=account(cash=300.0))
    assert d.sizing["by_cash"] == 2 and d.plan.quantity == 2
    held = account(positions={"AAPL": 10}, position_notional={"AAPL": 9_950.0})
    d = check(g, acct=held)
    assert not d.allowed and "size rounds to zero shares" in d.reasons


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"req": request(side="SELL")}, "long only: entries must be buys"),
        ({"req": request(stop=100.00)}, "no valid stop below the bid"),
        ({"req": request(stop=0)}, "no valid stop below the bid"),
        ({"req": request(target=100.10)}, "target not above entry"),
        ({"inst": None}, "symbol is not in the trading universe"),
        ({"inst": InstrumentInfo("SPY", leveraged=True)}, "leveraged ETFs not allowed"),
        ({"inst": InstrumentInfo("SPY", inverse=True)}, "inverse ETFs not allowed"),
        ({"inst": InstrumentInfo("QQQ")}, "market data is for a different symbol"),
        ({"mkt": market(avg_daily_dollar_volume=0)}, "average daily dollar volume unknown or below minimum"),
        ({"mkt": market(halted=True)}, "trading halted or paused"),
        ({"mkt": market(q=quote(bid=100.02, ask=100.02))}, "locked, crossed or empty NBBO"),
        ({"mkt": market(q=quote(bid=100.05, ask=100.02))}, "locked, crossed or empty NBBO"),
        ({"mkt": market(luld_upper=100.10)}, "entry at or above the LULD upper band"),
        ({"mkt": market(luld_lower=99.00)}, "stop at or below the LULD lower band"),
        ({"acct": account(positions={"SPY": 5})}, "already holding or entering this symbol"),
        ({"acct": account(pending_entries={"SPY": 500.0})}, "already holding or entering this symbol"),
        (
            {"acct": account(positions={"A": 1, "B": 1}, pending_entries={"C": 1.0})},
            "max concurrent positions reached",
        ),
        ({"now": et_ns(9, 32)}, "inside no-entry window after the open"),
        ({"now": et_ns(15, 50)}, "inside no-entry window before the close"),
        ({"now": et_ns(9, 0)}, "outside regular hours"),
        ({"now": et_ns(16, 0)}, "outside regular hours"),
    ],
)
def test_each_check_refuses(limits, kwargs, reason):
    now = kwargs.pop("now", NOW)
    if "mkt" not in kwargs:
        kwargs["mkt"] = market(q=quote(ts_ns=now))
    d = check(make_guard(limits), now=now, **kwargs)
    assert not d.allowed
    assert reason in d.reasons


def test_no_quote_and_wide_spread_and_low_price(limits):
    g = make_guard(limits)
    d = check(g, mkt=MarketView("SPY", None, avg_daily_dollar_volume=30e9))
    assert "no quote" in d.reasons and d.plan is None
    d = check(g, mkt=market(q=quote(bid=99.80, ask=100.02)))
    assert any(r.startswith("spread") for r in d.reasons)
    d = check(g, req=request(stop=3.0), mkt=market(q=quote(bid=4.00, ask=4.001)))
    assert "price below universe minimum" in d.reasons


def test_stale_quote_and_clock_skew(limits):
    g = make_guard(limits)
    stale = market(q=quote(ts_ns=NOW - (limits.session.max_data_staleness_ms + 1) * 1_000_000))
    assert any(r.startswith("stale quote") for r in check(g, mkt=stale).reasons)
    ok = market(q=quote(ts_ns=NOW - limits.session.max_data_staleness_ms * 1_000_000))
    assert check(g, mkt=ok).allowed
    future = market(q=quote(ts_ns=NOW + (CLOCK_SKEW_TOLERANCE_MS + 1) * 1_000_000))
    assert any(r.startswith("clock skew") for r in check(g, mkt=future).reasons)
    small = market(q=quote(ts_ns=NOW + (CLOCK_SKEW_TOLERANCE_MS - 1) * 1_000_000))
    assert check(g, mkt=small).allowed


def test_halt_cooldown(limits):
    g = make_guard(limits)
    just = market(resumed_at_ns=NOW - (RESUME_COOLDOWN_S - 1) * 1_000_000_000)
    assert "cooling down after a halt" in check(g, mkt=just).reasons
    later = market(resumed_at_ns=NOW - RESUME_COOLDOWN_S * 1_000_000_000)
    assert check(g, mkt=later).allowed


def test_market_closed_days(limits):
    for day in (date(2026, 10, 31), date(2026, 11, 26), date(2029, 10, 30)):  # Sat, Thanksgiving, unknown
        now = et_ns(10, 0, day=day)
        d = check(make_guard(limits, day=day), mkt=market(q=quote(ts_ns=now)), now=now)
        assert "market closed today" in d.reasons


def test_early_close_moves_the_no_entry_window(limits):
    day = date(2026, 11, 27)
    session = calendar.session_for(day)
    assert session.early_close and calendar.session_for(DAY).early_close is False
    now = et_ns(12, 50, day=day)
    d = check(make_guard(limits, day=day), mkt=market(q=quote(ts_ns=now)), now=now)
    assert "inside no-entry window before the close" in d.reasons


def test_duplicates_and_daily_counts(limits):
    g = make_guard(limits)
    g.commit_entry(request("sig-1"))
    assert "duplicate signal sig-1" in check(g, req=request("sig-1")).reasons
    for i in range(limits.trading.max_trades_per_day - 1):
        g.state.record_entry(f"x{i}")
    d = check(g, req=request("sig-new"))
    assert "max trades per day reached" in d.reasons


def test_pdt_limit_only_under_25k_and_legacy_regime(limits):
    g = make_guard(limits)
    for i in range(limits.trading.legacy_pdt_max_day_trades):
        g.state.record_entry(f"x{i}")
    assert "pattern day trader limit" in check(g, req=request("new")).reasons
    assert "pattern day trader limit" not in check(g, req=request("new"), acct=account(30_000.0)).reasons
    other = make_guard(with_limits(limits, "trading", pdt_regime=PdtRegime.INTRADAY_MARGIN))
    for i in range(3):
        other.state.record_entry(f"x{i}")
    assert "pattern day trader limit" not in check(other, req=request("new")).reasons


def test_price_deviation_band(limits):
    tight = with_limits(limits, "per_trade", max_price_deviation_bps=5)
    d = check(make_guard(tight))
    assert "limit price too far from the NBBO mid" in d.reasons


def test_account_wide_blocks(limits, tmp_path):
    kill = KillSwitch(tmp_path / "KILL")
    g = make_guard(limits, kill=kill)
    assert check(g).allowed
    kill.engage("test", "pytest")
    assert any(r.startswith("kill switch on: test (by pytest") for r in check(g).reasons)
    kill.release()
    g.state.halt_day("daily", "daily loss 2.1% >= 2%")
    assert any(r.startswith("halted for the day") for r in check(g).reasons)


def test_limits_file_edited_mid_session_blocks_entries(tmp_path, limits):
    path = tmp_path / "risk_limits.yaml"
    shutil.copy(REPO_LIMITS, path)
    loaded = load_risk_limits(path)
    g = make_guard(loaded)
    assert check(g).allowed
    path.write_text(path.read_text() + "\n# edited\n")
    assert "risk limits file changed since startup: restart to load it" in check(g).reasons


def test_exits_never_exceed_the_position(limits):
    g = make_guard(limits)
    assert g.check_exit("SPY", 5, 5).allowed
    assert not g.check_exit("SPY", 6, 5).allowed
    assert g.check_exit("SPY", 6, 5).reasons == ("sell 6 > held 5: would go short",)
    assert not g.check_exit("SPY", 0, 5).allowed


def test_round_down_and_quote_math():
    assert round_down(100.12002, 0.01) == 100.12
    assert round_down(100.13, 0.01) == 100.13
    assert QuoteView(0, 0, 0, 0, 0).spread_bps == math.inf
