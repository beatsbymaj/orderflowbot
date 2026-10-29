"""Phase 7 paper scorecard: does the paper record meet the gate for live stage 1?

ROADMAP.md, Phase 7 exit gate: at least 4 weeks and at least 100 trades; positive net
expectancy; drawdown within limits; zero risk violations; zero unreconciled orders. This
reads the trading node's journals only (drills and the watchdog are left out) and
rebuilds every number from the order events, net of commissions, every day counted (no
picked windows). Passing it doesn't start anything: live money needs the operator's
written go (CLAUDE.md, stages).

Usage:
    python -m flowforge.ops.scorecard            # writes reports/paper/scorecard.md and .json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from flowforge.ops.ledger import build_trades, journal_days, read_journal

MIN_DAYS_SPAN = 28  # 4 calendar weeks
MIN_TRADES = 100
MISMATCH = "position mismatch"


def _violations(records: list[dict], limits: Any) -> list[str]:
    """Things the risk layer should have made impossible. Any one fails the gate."""
    out: list[str] = []
    allowed = {r["intent"] for r in records if r["event"] == "risk_decision" and r.get("allowed")}
    for r in records:
        if r["event"] == "order_submit" and r.get("role", "entry") == "entry" and r["intent"] not in allowed:
            out.append(f"{r['ts'][:10]} entry {r['order']} without an allowed risk decision")
    held: dict[str, int] = {}
    orders: dict[str, str] = {}  # order id -> symbol
    by_intent: dict[str, str] = {}
    for r in records:
        if r["event"] == "order_submit":
            by_intent[r["intent"]] = orders[r["order"]] = r.get("symbol", "?")
        elif r["event"] in ("stop_submit", "exit_submit"):
            orders[r["order"]] = by_intent.get(r.get("intent", ""), "?")
        elif r["event"] == "fill":
            sym = orders.get(r.get("order", ""), "?")
            qty = int(r.get("quantity", 0)) * (1 if r.get("side") == "BUY" else -1)
            held[sym] = held.get(sym, 0) + qty
            if held[sym] < 0:
                out.append(f"{r['ts'][:10]} {sym} went short ({held[sym]} shares): long only")
            open_names = sum(1 for q in held.values() if q > 0)
            cap = limits.portfolio.max_concurrent_positions
            if open_names > cap:
                out.append(f"{r['ts'][:10]} {open_names} positions open (limit {cap})")
    return out


def _drawdowns(records: list[dict]) -> dict:
    marks = [r for r in records if r["event"] == "equity_mark"]
    worst_day = worst_week = peak_dd = 0.0
    peak = None
    for m in marks:
        eq = float(m["equity"])
        start = float(m.get("day_start_equity") or eq)
        week_peak = float(m.get("week_peak_equity") or eq)
        if start > 0:
            worst_day = max(worst_day, (start - eq) / start * 100)
        if week_peak > 0:
            worst_week = max(worst_week, (week_peak - eq) / week_peak * 100)
        peak = eq if peak is None else max(peak, eq)
        if peak > 0:
            peak_dd = max(peak_dd, (peak - eq) / peak * 100)
    return {
        "worst_day_loss_pct": round(worst_day, 2),
        "worst_week_drawdown_pct": round(worst_week, 2),
        "max_drawdown_pct": round(peak_dd, 2),
        "equity_first": float(marks[0]["equity"]) if marks else None,
        "equity_last": float(marks[-1]["equity"]) if marks else None,
    }


def scorecard(journals: Path, limits: Any, first_day: str | None = None) -> dict:
    from flowforge.research import stats

    days = sorted(d for d in journal_days(journals) if first_day is None or d >= first_day)
    records: list[dict] = []
    for d in days:
        records += read_journal(journals, d, ["node"]).records
    starts = [r for r in records if r["event"] == "paper_node_start"]
    if first_day is None and starts:
        first = starts[0]["ts_ns"]
        records = [r for r in records if r["ts_ns"] >= first]
    traded_days = sorted({r["ts"][:10] for r in records}) if records else []
    trades, orphans = build_trades(records)
    closed = [t for t in trades if t.status == "closed"]
    nets = [(_day(t.closed_ns), t.net or 0.0) for t in closed]
    values = [n for _, n in nets]
    mean = sum(values) / len(values) if values else None
    p = stats.bootstrap_p(nets, reps=5000) if len(values) >= 10 else None
    dd = _drawdowns(records)
    violations = _violations(records, limits)
    mismatches = [r for r in records if r["event"] == "alert" and MISMATCH in r.get("message", "")]
    denied = [r for r in records if r["event"] == "order_denied"]
    span = (
        (date.fromisoformat(traded_days[-1]) - date.fromisoformat(traded_days[0])).days + 1
        if traded_days
        else 0
    )
    lim = limits.loss
    checks = [
        _check(
            "At least 4 weeks of paper",
            span >= MIN_DAYS_SPAN,
            _plural(span, "calendar day") + ", " + _plural(len(traded_days), "session"),
        ),
        _check("At least 100 trades", len(closed) >= MIN_TRADES, f"{len(closed)} closed trades"),
        _check(
            "Positive net expectancy",
            mean is not None and mean > 0,
            "no trades"
            if mean is None
            else f"{mean:+.2f} USD per trade after costs"
            + (f", bootstrap p {p:.3f}" if p is not None else ""),
        ),
        _check(
            "Daily loss within limit",
            dd["worst_day_loss_pct"] <= lim.max_daily_loss_pct,
            f"worst day -{dd['worst_day_loss_pct']}% (limit {lim.max_daily_loss_pct}%)",
        ),
        _check(
            "Weekly drawdown within limit",
            dd["worst_week_drawdown_pct"] <= lim.max_weekly_drawdown_pct,
            f"worst -{dd['worst_week_drawdown_pct']}% (limit {lim.max_weekly_drawdown_pct}%)",
        ),
        _check("Zero risk violations", not violations, "; ".join(violations[:5]) or "none"),
        _check(
            "Zero unreconciled orders",
            not orphans and not mismatches,
            f"{len(orphans)} fills no trade owns, {len(mismatches)} position mismatches",
        ),
    ]
    streak = stats.losing_streak(values) if values else 0
    return {
        "first_day": traded_days[0] if traded_days else None,
        "last_day": traded_days[-1] if traded_days else None,
        "sessions": len(traded_days),
        "trades": len(closed),
        "wins": sum(1 for v in values if v > 0),
        "losses": sum(1 for v in values if v < 0),
        "net": round(sum(values), 2),
        "commission": round(sum(t.commission for t in trades), 2),
        "mean_net": None if mean is None else round(mean, 2),
        "bootstrap_p": p,
        "losing_streak": streak,
        "not_filled": sum(1 for t in trades if t.status == "not filled"),
        "denied_by_nautilus": len(denied),
        **dd,
        "checks": checks,
        "passes": all(c["ok"] for c in checks),
    }


def _day(ts_ns: int | None) -> str:
    from flowforge.risk.calendar import et_date

    return et_date(ts_ns).isoformat() if ts_ns else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def markdown(card: dict) -> str:
    lines = [
        "# Paper scorecard (Phase 7 gate)",
        "",
        f"{card['first_day'] or '-'} to {card['last_day'] or '-'}, {card['sessions']} sessions,"
        " every day counted.",
        "",
        f"**{'Meets the Phase 7 gate' if card['passes'] else 'Does not meet the Phase 7 gate yet'}.**"
        " Live money still needs the operator's written go.",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
    ]
    lines += [f"| {c['name']} | {'ok' if c['ok'] else '**no**'} | {c['detail']} |" for c in card["checks"]]
    lines += [
        "",
        f"- Trades {card['trades']} ({card['wins']} won, {card['losses']} lost),"
        f" {card['not_filled']} entries not filled",
        f"- Net after costs {card['net']:+.2f} USD, commissions {card['commission']:.2f} USD",
        f"- Max drawdown {card['max_drawdown_pct']}%, longest losing streak {card['losing_streak']}",
        f"- Orders the Nautilus RiskEngine denied: {card['denied_by_nautilus']}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from flowforge.config.risk_limits import load_risk_limits

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--journals", type=Path, default=Path("data/journal"))
    p.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    p.add_argument("--first-day", default=None, help="YYYY-MM-DD; default: the first paper node start")
    p.add_argument("--out", type=Path, default=Path("reports/paper"))
    a = p.parse_args(argv)
    card = scorecard(a.journals, load_risk_limits(a.risk_limits).limits, a.first_day)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (a.out / "scorecard.md").write_text(markdown(card), encoding="utf-8")
    print(markdown(card))
    return 0 if card["passes"] else 1


if __name__ == "__main__":
    sys.exit(main())
