from datetime import datetime

from flowforge.backtest.stats import (
    daily_equity,
    longest_losing_streak,
    max_drawdown,
    summarize,
    trade_summary,
)


def _trade(day, pnl, qty=10, entry=100.0, exit_=101.0, symbol="SPY"):
    return {
        "symbol": symbol,
        "entry_time": f"{day}T10:00:00-05:00",
        "exit_time": f"{day}T11:00:00-05:00",
        "quantity": qty,
        "entry_price": entry,
        "exit_price": exit_,
        "commission": 2.0,
        "pnl": pnl,
        "exit_reason": "target" if pnl > 0 else "stop",
    }


def test_max_drawdown_and_streak():
    assert max_drawdown([100, 120, 90, 130, 125]) == (30, 25.0)
    assert max_drawdown([100, 110]) == (0.0, 0.0)
    assert longest_losing_streak([1, -1, -2, 3, -1, -1, -1, 0]) == 3


def test_trade_summary_and_stress():
    trades = [_trade("2025-01-02", 20.0), _trade("2025-01-03", -10.0)]
    s = trade_summary(trades)
    assert s["net_pnl"] == 10.0 and s["win_rate_pct"] == 50.0 and s["profit_factor"] == 2.0
    stressed = trade_summary(trades, extra_slippage_bps=10)
    # 10 bps per side on (100 + 101) * 10 shares = 2.01 per trade
    assert stressed["net_pnl"] == round(10.0 - 2 * 2.01, 2)
    assert trade_summary([])["trades"] == 0 and trade_summary([])["profit_factor"] is None


def test_summarize_split_and_counts():
    days = ["2025-01-02", "2025-01-03", "2025-01-06"]
    trades = [_trade(days[0], 30.0), _trade(days[1], -10.0), _trade(days[2], -5.0, symbol="QQQ")]
    equity = [
        (datetime.fromisoformat(f"{d}T15:00:00-05:00"), v)
        for d, v in zip(days, [10_030, 10_020, 10_015], strict=True)
    ]
    journal = [
        {"event": "signal", "allowed": False, "reason": "pattern day trader limit"},
        {"event": "signal", "allowed": True, "reason": "ok"},
        {"event": "risk_halt", "limit": "daily"},
        {"event": "order_denied"},
    ]
    s = summarize(trades, equity, journal, 10_000.0)
    assert s["ending_equity"] == 10_015.0 and s["max_drawdown"] == 15.0
    assert s["split"]["cut_date"] == "2025-01-06"
    assert s["split"]["first_two_thirds"]["trades"] == 2 and s["split"]["last_third"]["trades"] == 1
    assert s["signals"] == 2 and s["signals_blocked"] == {"pattern day trader limit": 1}
    assert s["risk_halts"] == {"daily": 1} and s["errors"] == 1
    assert set(s["by_symbol"]) == {"QQQ", "SPY"} and s["worst_day"] == -10.0
    assert daily_equity(equity)[0] == ("2025-01-02", 10_030)


def test_summarize_with_no_activity():
    s = summarize([], [], [], 10_000.0)
    assert s["ending_equity"] == 10_000.0 and s["first_day"] is None and s["split"]["cut_date"] is None
