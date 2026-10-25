"""Backtest statistics: net results, drawdown, losing streaks, splits and a slippage stress test."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime


def max_drawdown(equity: list[float]) -> tuple[float, float]:
    """Largest peak-to-trough fall as (dollars, percent of the peak)."""
    peak = float("-inf")
    worst, worst_pct = 0.0, 0.0
    for value in equity:
        peak = max(peak, value)
        if peak - value > worst:
            worst, worst_pct = peak - value, (peak - value) / peak * 100 if peak > 0 else 0.0
    return worst, worst_pct


def longest_losing_streak(pnls: list[float]) -> int:
    best = run = 0
    for pnl in pnls:
        run = run + 1 if pnl < 0 else 0
        best = max(best, run)
    return best


def trade_summary(trades: list[dict], extra_slippage_bps: float = 0.0) -> dict:
    """Summary of closed trades. `extra_slippage_bps` charges that much more per side."""
    pnls = [
        t["pnl"] - extra_slippage_bps / 10_000 * t["quantity"] * (t["entry_price"] + t["exit_price"])
        for t in trades
    ]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_loss = -sum(losses)
    return {
        "trades": len(pnls),
        "net_pnl": round(sum(pnls), 2),
        "win_rate_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
        "longest_losing_streak": longest_losing_streak(pnls),
        "commissions": round(sum(t["commission"] for t in trades), 2),
    }


def daily_equity(equity: list[tuple[datetime, float]]) -> list[tuple[str, float]]:
    """Last marked equity of each day."""
    last: dict[str, float] = {}
    for when, value in equity:
        last[when.date().isoformat()] = value
    return sorted(last.items())


def summarize(
    trades: list[dict],
    equity: list[tuple[datetime, float]],
    journal: list[dict],
    starting_equity: float,
    stress_bps: float = 5.0,
) -> dict:
    values = [v for _, v in equity] or [starting_equity]
    dd, dd_pct = max_drawdown([starting_equity, *values])
    daily_pnl: dict[str, float] = defaultdict(float)
    monthly: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        daily_pnl[t["exit_time"][:10]] += t["pnl"]
        monthly[t["exit_time"][:7]] += t["pnl"]
        by_symbol[t["symbol"]].append(t)

    days = [d for d, _ in daily_equity(equity)]
    cut = days[len(days) * 2 // 3] if len(days) >= 3 else None
    first = [t for t in trades if cut is None or t["exit_time"][:10] < cut]
    second = [t for t in trades if cut is not None and t["exit_time"][:10] >= cut]

    signals = [j for j in journal if j["event"] == "signal"]
    blocked = Counter(j["reason"] for j in signals if not j["allowed"])
    halts = Counter(j["limit"] for j in journal if j["event"] == "risk_halt")
    errors = [j for j in journal if j["event"] in {"error", "order_denied", "order_rejected"}]
    return {
        "starting_equity": starting_equity,
        "ending_equity": round(starting_equity + sum(t["pnl"] for t in trades), 2),
        "return_pct": round(sum(t["pnl"] for t in trades) / starting_equity * 100, 2),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "trading_days": len(days),
        "max_drawdown": round(dd, 2),
        "max_drawdown_pct": round(dd_pct, 2),
        "worst_day": round(min(daily_pnl.values()), 2) if daily_pnl else 0.0,
        "best_day": round(max(daily_pnl.values()), 2) if daily_pnl else 0.0,
        "all": trade_summary(trades),
        "stress": {"extra_slippage_bps_per_side": stress_bps, **trade_summary(trades, stress_bps)},
        "split": {
            "cut_date": cut,
            "first_two_thirds": trade_summary(first),
            "last_third": trade_summary(second),
        },
        "monthly_pnl": {k: round(v, 2) for k, v in sorted(monthly.items())},
        "by_symbol": {s: trade_summary(ts) for s, ts in sorted(by_symbol.items())},
        "exit_reasons": dict(Counter(t["exit_reason"] for t in trades)),
        "signals": len(signals),
        "signals_blocked": dict(blocked),
        "risk_halts": dict(halts),
        "errors": len(errors),
    }
