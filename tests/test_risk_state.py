import json
from datetime import date

import pytest
from conftest import DAY

from flowforge.risk import calendar
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import MAX_CONSECUTIVE_REJECTS, RiskState, trading_days_back


def test_daily_loss_halts_for_the_day_and_clears_next_day(limits, tmp_path):
    s = RiskState(limits, tmp_path / "state.json")
    s.start_day(DAY, 10_000.0)
    assert s.on_equity(9_801.0) is None
    assert s.on_equity(9_800.0) == "daily"
    assert s.halt_reason().startswith("halted for the day: daily loss 2.00%")
    assert s.on_equity(9_700.0) is None  # reported once
    s.start_day(date(2026, 10, 28), 9_700.0)
    assert s.halt_reason() is None


def test_weekly_drawdown_from_peak_needs_the_operator(limits, tmp_path):
    path = tmp_path / "state.json"
    s = RiskState(limits, path)
    s.start_day(DAY, 10_000.0)
    s.on_equity(10_500.0)  # new weekly peak
    s.start_day(date(2026, 10, 28), 10_500.0)
    assert s.on_equity(9_975.0) == "weekly"  # 5% below the 10,500 peak (also 5% daily)
    assert s.halt_reason().startswith("halted until re-enabled: weekly drawdown 5.00%")
    # Survives a restart and a new week
    again = RiskState(limits, path)
    again.start_day(date(2026, 11, 2), 9_975.0)
    assert again.halt_reason().startswith("halted until re-enabled")
    assert again.reenable() is True
    assert RiskState(limits, path).halt_reason() is None
    assert again.reenable() is False


def test_daily_halt_survives_restart_same_day(limits, tmp_path):
    path = tmp_path / "state.json"
    s = RiskState(limits, path)
    s.start_day(DAY, 10_000.0)
    s.halt_day("kill", "operator pulled the switch")
    s.halt_day("errors", "ignored: already halted")
    again = RiskState(limits, path)
    again.start_day(DAY, 10_000.0)  # same day: no reset
    assert again.halt_reason() == "halted for the day: operator pulled the switch"


def test_unreadable_state_file_fails_closed(limits, tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    s = RiskState(limits, path)
    assert s.halt_reason().startswith("halted until re-enabled: risk state file unreadable")


def test_counts_and_pdt_window(limits, tmp_path):
    s = RiskState(limits, tmp_path / "state.json")
    assert s.trades_today() == 0 and s.day_trades_in_window() == 0 and s.on_equity(1.0) is None
    with pytest.raises(AssertionError):
        s.record_entry("too early")
    s.start_day(date(2026, 10, 20), 10_000.0)
    s.record_entry("a")
    s.start_day(DAY, 10_000.0)  # 2026-10-27: window is 10-21..10-27, so 10-20 drops out
    s.record_entry("b")
    s.record_entry("c")
    assert s.trades_today() == 2
    assert s.day_trades_in_window() == 2
    assert json.loads((tmp_path / "state.json").read_text())["entries_by_day"] == {"2026-10-27": 2}


def test_consecutive_rejects_trip_the_error_halt(limits):
    s = RiskState(limits)
    s.start_day(DAY, 10_000.0)
    for _ in range(MAX_CONSECUTIVE_REJECTS - 1):
        assert s.record_reject("insufficient funds") is False
    s.record_accept()
    assert s.consecutive_rejects == 0
    s.record_accept()
    tripped = [s.record_reject("no") for _ in range(MAX_CONSECUTIVE_REJECTS)]
    assert tripped[-1] is True
    assert "entries rejected in a row" in s.halt_reason()


def test_trading_days_back_skips_weekends_and_holidays():
    days = trading_days_back(date(2026, 11, 30), 5)  # Monday after Thanksgiving
    assert days == [
        date(2026, 11, 30),
        date(2026, 11, 27),
        date(2026, 11, 25),
        date(2026, 11, 24),
        date(2026, 11, 23),
    ]


def test_calendar_basics():
    s = calendar.session_for(DAY)
    assert (s.close_ns - s.open_ns) == int(6.5 * 3600) * calendar.NS
    assert calendar.session_for(date(2028, 1, 3)) is not None  # New Year's Day 2028 not observed
    assert calendar.session_at(s.open_ns).day == DAY
    assert not calendar.is_trading_day(date(2027, 3, 26))  # Good Friday


def test_kill_switch(tmp_path):
    kill = KillSwitch(tmp_path / "run" / "KILL")
    assert not kill.is_engaged() and kill.reason() is None
    kill.engage("daily loss", "node")
    assert kill.is_engaged()
    assert kill.reason().startswith("daily loss (by node at ")
    kill.path.write_text("garbage")
    assert kill.reason() == "kill switch file present"
    assert kill.release() is True and kill.release() is False


def test_kill_switch_fails_closed_when_unreadable(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("flowforge.risk.killswitch.os.stat", denied)
    assert KillSwitch(tmp_path / "KILL").is_engaged()


def test_sale_proceeds_settle_next_trading_day_and_persist(limits, tmp_path):
    s = RiskState(limits, tmp_path / "state.json")
    fri = date(2026, 10, 23)
    s.start_day(fri, 10_000.0)
    s.record_sale(0.0, fri)  # nothing to settle
    s.record_sale(1_234.567, fri)
    assert s.unsettled == [{"settles": "2026-10-26", "amount": 1234.57}]  # Friday -> Monday
    assert s.unsettled_cash() == 1234.57
    again = RiskState(limits, tmp_path / "state.json")
    assert again.unsettled_cash(fri) == 1234.57
    assert again.unsettled_cash(date(2026, 10, 26)) == 0.0  # settled on Monday
    again.start_day(date(2026, 10, 26), 10_000.0)
    assert again.unsettled == []


def test_next_trading_day_skips_weekends_and_holidays():
    from flowforge.risk.calendar import next_trading_day

    assert next_trading_day(date(2026, 10, 27)) == date(2026, 10, 28)
    assert next_trading_day(date(2026, 11, 25)) == date(2026, 11, 27)  # Thanksgiving
    far = next_trading_day(date(2099, 1, 1))  # beyond the known calendar: never early
    assert far > date(2099, 1, 1)
