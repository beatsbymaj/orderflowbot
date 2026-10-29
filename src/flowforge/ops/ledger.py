"""Read the journals back: records, trades, signals and a day's numbers.

Used by the dashboard API and the end-of-day report. Read only. Journals are the JSON-lines
files written by execution/journal.py, one folder per writer (node, watchdog, ops,
dashboard) and one file per New York trading date.

Trades are rebuilt from order events, not from the strategy's memory, so the dashboard and
the report show what the broker said happened: entry and exit fills, commissions, and the
stop from the RiskGuard plan (for R).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

SECRET_KEY = re.compile(r"(key|secret|token|password|passwd)", re.IGNORECASE)
REDACTED = "[redacted]"


def scrub(value):
    """Mask any field whose name looks like a secret. Journals should never hold one; this
    is the second lock (CLAUDE.md rule 5)."""
    if isinstance(value, dict):
        return {k: (REDACTED if SECRET_KEY.search(str(k)) else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


@dataclass
class JournalRead:
    records: list[dict]
    bad_lines: int = 0
    files: int = 0


def journal_days(root: Path) -> list[str]:
    """Trading dates that have any journal file, newest first."""
    if not root.exists():
        return []
    return sorted({p.stem for p in root.rglob("*.jsonl") if _is_day(p.stem)}, reverse=True)


def _is_day(stem: str) -> bool:
    try:
        date.fromisoformat(stem)
    except ValueError:
        return False
    return True


def read_journal(root: Path, day: str | None = None, sources: Iterable[str] | None = None) -> JournalRead:
    """Every record of `day` (all days when None) from every writer folder under `root`,
    sorted by time. Each record gets `source` = its folder name."""
    out = JournalRead([])
    if not root.exists():
        return out
    wanted = set(sources) if sources is not None else None
    pattern = f"{day}.jsonl" if day else "*.jsonl"
    for path in sorted(root.rglob(pattern)):
        source = path.parent.name if path.parent != root else "node"
        if wanted is not None and source not in wanted:
            continue
        out.files += 1
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    int(rec["ts_ns"])
                    str(rec["event"])
                except (ValueError, KeyError, TypeError):
                    out.bad_lines += 1  # a torn last line after a crash, or a damaged file
                    continue
                rec["source"] = source
                out.records.append(scrub(rec))
    out.records.sort(key=lambda r: int(r["ts_ns"]))
    return out


# -- trades -----------------------------------------------------------------------------------
@dataclass
class TradeRow:
    intent: str
    symbol: str
    setup: str
    status: str  # open | closed | not filled
    opened_ns: int
    entry_order: str
    shares: int = 0
    entry_price: float | None = None
    stop: float | None = None
    target: float | None = None
    planned_shares: int = 0
    sold: int = 0
    exit_price: float | None = None
    exit_reason: str = ""
    closed_ns: int | None = None
    commission: float = 0.0
    gross: float | None = None
    net: float | None = None
    r: float | None = None
    orders: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def exit_kind(reason: str) -> str:
    text = reason.lower()
    for word, kind in (("target", "target"), ("end of day", "eod"), ("kill", "kill"), ("loss limit", "halt")):
        if word in text:
            return kind
    return "exit"


def build_trades(records: list[dict]) -> tuple[list[TradeRow], list[dict]]:
    """Trades by intent, and fills no trade owns (unreconciled: shown on Health and in EOD)."""
    trades: dict[str, TradeRow] = {}
    owner: dict[str, tuple[str, str]] = {}  # order id -> (intent, role)
    plans: dict[str, dict] = {}
    exit_reasons: dict[str, str] = {}
    orphans: list[dict] = []
    buys: dict[str, float] = {}
    sells: dict[str, float] = {}
    for rec in records:
        ev = rec["event"]
        if ev == "risk_decision" and rec.get("allowed") and rec.get("plan"):
            plans[rec["intent"]] = rec["plan"]
        elif ev == "order_submit" and rec.get("role", "entry") == "entry":
            intent = rec["intent"]
            plan = plans.get(intent, {})
            trades[intent] = TradeRow(
                intent=intent,
                symbol=rec.get("symbol", intent.split(":")[1] if ":" in intent else ""),
                setup=intent.split(":")[0] if ":" in intent else "",
                status="not filled",
                opened_ns=int(rec["ts_ns"]),
                entry_order=rec["order"],
                stop=plan.get("stop_price"),
                target=plan.get("target_price"),
                planned_shares=int(rec.get("quantity", plan.get("quantity", 0))),
                orders=[rec["order"]],
            )
            owner[rec["order"]] = (intent, "entry")
        elif ev in ("stop_submit", "exit_submit") and rec.get("intent") in trades:
            owner[rec["order"]] = (rec["intent"], "stop" if ev == "stop_submit" else "exit")
            trades[rec["intent"]].orders.append(rec["order"])
            if ev == "exit_submit":
                exit_reasons[rec["order"]] = str(rec.get("reason", ""))
        elif ev == "fill":
            found = owner.get(rec.get("order", ""))
            if found is None:
                orphans.append(rec)
                continue
            intent, role = found
            t = trades[intent]
            qty, px = int(rec.get("quantity", 0)), float(rec.get("price", 0.0))
            t.commission += float(rec.get("commission", 0.0) or 0.0)
            if role == "entry":
                t.shares += qty
                buys[intent] = buys.get(intent, 0.0) + qty * px
                t.entry_price = round(buys[intent] / t.shares, 4)
                if t.status == "not filled":
                    t.status = "open"
            else:
                t.sold += qty
                sells[intent] = sells.get(intent, 0.0) + qty * px
                t.exit_price = round(sells[intent] / t.sold, 4)
                t.exit_reason = "stop" if role == "stop" else exit_kind(exit_reasons.get(rec["order"], ""))
                if t.shares and t.sold >= t.shares:
                    t.status = "closed"
                    t.closed_ns = int(rec["ts_ns"])
    for intent, t in trades.items():
        t.commission = round(t.commission, 2)
        if t.status == "closed":
            t.gross = round(sells[intent] - buys[intent], 2)
            t.net = round(t.gross - t.commission, 2)
            if t.stop is not None and t.entry_price is not None and t.entry_price > t.stop:
                t.r = round(t.net / ((t.entry_price - t.stop) * t.shares), 2)
    return sorted(trades.values(), key=lambda t: t.opened_ns), orphans


# -- signals ----------------------------------------------------------------------------------
def build_signals(records: list[dict], trades: list[TradeRow]) -> list[dict]:
    """One row per signal with what became of it: not enabled, skipped, Jev, risk, traded."""
    by_intent: dict[str, dict] = {}
    traded = {t.intent: t for t in trades}
    for rec in records:
        ev, intent = rec["event"], rec.get("intent")
        if ev == "signal":
            by_intent[intent] = {
                "intent": intent,
                "ts_ns": rec["ts_ns"],
                "ts": rec.get("ts"),
                "setup": rec.get("setup"),
                "symbol": rec.get("symbol"),
                "reason": rec.get("reason", ""),
                "zone": rec.get("zone"),
                "enabled": bool(rec.get("enabled")),
                "outcome": "not enabled" if not rec.get("enabled") else "waiting",
                "why": "",
                "jev": None,
            }
            continue
        row = by_intent.get(intent)
        if row is None:
            continue
        if ev == "signal_skipped":
            row["outcome"], row["why"] = "skipped", rec.get("why", "")
        elif ev == "jev_decision":
            row["jev"] = {
                k: rec.get(k) for k in ("allow", "p_yes", "latency_ms", "reason", "vetoed_in_shadow")
            }
            if not rec.get("allow") and not rec.get("vetoed_in_shadow"):
                row["outcome"], row["why"] = "vetoed by Jev", str(rec.get("reason", ""))
        elif ev == "risk_decision":
            if rec.get("allowed"):
                row["outcome"] = "sent"
            else:
                row["outcome"], row["why"] = "refused by risk", "; ".join(rec.get("reasons", []))
    for intent, row in by_intent.items():
        t = traded.get(intent)
        if t is not None and row["outcome"] == "sent":
            row["outcome"] = {"not filled": "not filled", "open": "in trade", "closed": "traded"}[t.status]
            row["net"] = t.net
            row["r"] = t.r
    return sorted(by_intent.values(), key=lambda r: r["ts_ns"], reverse=True)


# -- the day's numbers --------------------------------------------------------------------------
ALERT_EVENTS = ("alert", "watchdog_alert", "kill_engaged", "watchdog_flatten")
REJECT_EVENTS = ("order_rejected", "order_denied", "order_cancel_rejected", "order_modify_rejected")


def day_numbers(records: list[dict], trades: list[TradeRow], orphans: list[dict]) -> dict:
    closed = [t for t in trades if t.status == "closed"]
    nets = [t.net or 0.0 for t in closed]
    marks = [r for r in records if r["event"] == "equity_mark"]
    worst_dd = 0.0
    if marks:
        peak = float(marks[0].get("day_start_equity") or marks[0]["equity"])
        for m in marks:
            eq = float(m["equity"])
            peak = max(peak, eq)
            worst_dd = max(worst_dd, peak - eq)
    streak = best = 0
    for n in nets:
        streak = streak + 1 if n < 0 else 0
        best = max(best, streak)
    return {
        "trades": len(closed),
        "open_trades": sum(1 for t in trades if t.status == "open"),
        "not_filled": sum(1 for t in trades if t.status == "not filled"),
        "wins": sum(1 for n in nets if n > 0),
        "losses": sum(1 for n in nets if n < 0),
        "net": round(sum(nets), 2),
        "gross": round(sum(t.gross or 0.0 for t in closed), 2),
        "commission": round(sum(t.commission for t in trades), 2),
        "r_total": round(sum(t.r or 0.0 for t in closed), 2),
        "max_drawdown_usd": round(worst_dd, 2),
        "losing_streak": best,
        "signals": sum(1 for r in records if r["event"] == "signal"),
        "risk_refusals": sum(1 for r in records if r["event"] == "risk_decision" and not r.get("allowed")),
        "rejections": sum(1 for r in records if r["event"] in REJECT_EVENTS),
        "alerts": sum(1 for r in records if r["event"] in ALERT_EVENTS),
        "critical": sum(
            1 for r in records if (r["event"] in ALERT_EVENTS and r.get("level", "critical") == "critical")
        ),
        "unreconciled_fills": len(orphans),
        "equity_start": float(marks[0].get("day_start_equity") or marks[0]["equity"]) if marks else None,
        "equity_last": float(marks[-1]["equity"]) if marks else None,
    }
