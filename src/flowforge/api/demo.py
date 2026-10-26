"""Made-up data for a dashboard walkthrough before the node trades on paper.

    python -m flowforge.api --demo data/demo

Writes one invented trading day into its own folder (never into data/journal): a SPY
target, a QQQ stop, an open MSFT position, a refused AAPL signal (wide spread), a refused
NVDA signal (3 day trades in 5 days), a skipped H1 signal, equity marks, a tape snapshot,
a pre-market report and an invented walk-forward result. Every screen shows a "Demo data"
banner while it is used.

Sizes follow the real limits for a $10,000 account: at most 10% of equity per position
($1,000), so one SPY share and two QQQ shares. That, with IBKR's $1 minimum commission,
is why the demo day loses money on a winning trade count: the same will be true on paper.
"""

from __future__ import annotations

import json
import math
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from flowforge.execution.heartbeat import Heartbeat, write_heartbeat
from flowforge.execution.journal import Journal
from flowforge.risk.calendar import ET, NS, is_trading_day

AS_OF = (14, 30)
START_EQUITY = 10_000.0
FEE = 1.00  # IBKR Fixed minimum per order
OPEN_MARK = {"MSFT": 431.62}  # the open position's last price, in the equity and the tape alike


def demo_day(now: datetime) -> date:
    d = now.date()
    if is_trading_day(d) and (now.hour, now.minute) >= AS_OF:
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _ns(day: date, hh: int, mm: int, ss: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hh, mm, ss, tzinfo=ET).timestamp()) * NS


def _trade_events(j: Journal, day: date, t: dict) -> None:
    """signal -> risk decision -> entry -> fill -> stop (-> exit)."""
    at = lambda hh, mm, ss=0: _ns(day, hh, mm, ss)  # noqa: E731
    intent = f"{t['setup']}:{t['symbol']}:{at(*t['at'])}"
    h, m = t["at"]
    j.write(
        at(h, m),
        "signal",
        intent=intent,
        setup=t["setup"],
        symbol=t["symbol"],
        reason=t["reason"],
        zone=t["zone"],
        enabled=True,
    )
    plan = {
        "symbol": t["symbol"],
        "quantity": t["qty"],
        "limit_price": t["limit"],
        "stop_price": t["stop"],
        "target_price": t["target"],
    }
    j.write(
        at(h, m, 1),
        "risk_decision",
        intent=intent,
        symbol=t["symbol"],
        allowed=True,
        reasons=[],
        plan=plan,
        sizing={"by_risk": t["by_risk"], "by_notional": t["qty"], "final": t["qty"]},
    )
    oid = f"O-{t['symbol']}-1"
    j.write(
        at(h, m, 1),
        "order_submit",
        intent=intent,
        symbol=t["symbol"],
        order=oid,
        role="entry",
        quantity=t["qty"],
        limit=t["limit"],
    )
    j.write(at(h, m, 1), "order_accepted", order=oid)
    j.write(
        at(h, m, 2),
        "fill",
        order=oid,
        fill_id=f"F-{oid}",
        side="BUY",
        quantity=t["qty"],
        price=t["entry"],
        commission=FEE,
        complete=True,
    )
    sid = f"O-{t['symbol']}-2"
    j.write(at(h, m, 2), "stop_submit", intent=intent, order=sid, quantity=t["qty"], stop=t["stop"])
    j.write(at(h, m, 3), "order_accepted", order=sid)
    if t.get("exit") is None:
        return
    eh, em = t["exit_at"]
    if t["how"] == "stop":
        j.write(
            at(eh, em),
            "fill",
            order=sid,
            fill_id=f"F-{sid}",
            side="SELL",
            quantity=t["qty"],
            price=t["exit"],
            commission=FEE,
            complete=True,
        )
    else:
        j.write(at(eh, em), "exit_request", intent=intent, reason=f"target {t['target']} reached")
        j.write(at(eh, em), "cancel", order=sid, reason="exit")
        j.write(at(eh, em, 1), "order_canceled", order=sid)
        xid = f"O-{t['symbol']}-3"
        j.write(
            at(eh, em, 1),
            "exit_submit",
            intent=intent,
            order=xid,
            symbol=t["symbol"],
            quantity=t["qty"],
            reason=f"target {t['target']} reached (bid {t['exit']})",
        )
        j.write(
            at(eh, em, 2),
            "fill",
            order=xid,
            fill_id=f"F-{xid}",
            side="SELL",
            quantity=t["qty"],
            price=t["exit"],
            commission=FEE,
            complete=True,
        )
    j.write(
        at(eh, em, 2),
        "position_closed",
        symbol=t["symbol"],
        realized_pnl=round((t["exit"] - t["entry"]) * t["qty"] - 2 * FEE, 2),
    )


TRADES = [
    {
        "setup": "H4",
        "symbol": "SPY",
        "at": (9, 41),
        "reason": "support zone held, delta turned up",
        "zone": [580.95, 581.10],
        "qty": 1,
        "by_risk": 29,
        "limit": 581.37,
        "entry": 581.31,
        "stop": 579.62,
        "target": 584.90,
        "exit": 584.86,
        "exit_at": (10, 12),
        "how": "target",
    },
    {
        "setup": "H4",
        "symbol": "QQQ",
        "at": (11, 20),
        "reason": "support zone held, delta turned up",
        "zone": [497.80, 498.00],
        "qty": 2,
        "by_risk": 29,
        "limit": 498.20,
        "entry": 498.12,
        "stop": 496.40,
        "target": 501.60,
        "exit": 496.35,
        "exit_at": (11, 52),
        "how": "stop",
    },
    {
        "setup": "H4",
        "symbol": "MSFT",
        "at": (13, 5),
        "reason": "support zone held, delta turned up",
        "zone": [430.90, 431.05],
        "qty": 2,
        "by_risk": 38,
        "limit": 431.28,
        "entry": 431.20,
        "stop": 429.90,
        "target": 434.10,
        "exit": None,
    },
]


def _equity_at(ts: int, day: date) -> float:
    """Realized P&L so far plus the open position marked with a small made-up wiggle."""
    eq = START_EQUITY
    for t in TRADES:
        start = _ns(day, *t["at"])
        if ts < start:
            continue
        end = _ns(day, *t["exit_at"]) if t.get("exit") else None
        if end is not None and ts >= end:
            eq += (t["exit"] - t["entry"]) * t["qty"] - 2 * FEE
            continue
        eq -= FEE
        drift = (t["exit"] if t.get("exit") else OPEN_MARK[t["symbol"]]) - t["entry"]
        span = ((end or _ns(day, *AS_OF)) - start) or 1
        frac = (ts - start) / span
        eq += (drift * frac + 0.35 * (1 - frac) * math.sin(ts / NS / 97)) * t["qty"]
    return round(eq, 2)


def _tape(day: date, now_ns: int) -> dict:
    base = {"SPY": 582.4, "QQQ": 497.6, "AAPL": 231.8, "MSFT": 431.5, "NVDA": 188.9}
    out = {}
    end = _ns(day, *AS_OF)
    for k, (sym, px) in enumerate(base.items()):
        bars, cvd = [], 0
        for i in range(60):
            ts = end - (59 - i) * 60 * NS
            delta = int(900 * math.sin(i / 4 + k) + 300 * math.cos(i / 1.7 + k))
            cvd += delta
            close = round(px + 0.6 * math.sin(i / 9 + k) + 0.004 * cvd / 100, 2)
            vol = 18_000 + int(7_000 * abs(math.sin(i / 5 + k)))
            bars.append(
                {
                    "end": datetime.fromtimestamp(ts / NS, ET).isoformat(),
                    "close": close,
                    "volume": vol,
                    "delta": delta,
                    "cvd": cvd,
                    "spread_bps": round(0.3 + 0.2 * abs(math.sin(i)), 2),
                }
            )
        events = []
        for i in (12, 31, 47, 55):
            ts = end - (59 - i) * 60 * NS + 17 * NS
            side = 1 if (i + k) % 3 else -1
            shares = 2_000 + 300 * ((i * 7 + k) % 9)
            kind = "sweep" if i in (31, 55) else "large print"
            events.append(
                {
                    "ts": datetime.fromtimestamp(ts / NS, ET).isoformat(),
                    "kind": kind,
                    "side": side,
                    "shares": shares,
                    "notional": round(shares * bars[i]["close"], 2),
                    "price": bars[i]["close"],
                }
            )
        if sym in OPEN_MARK:
            bars[-1]["close"] = OPEN_MARK[sym]
        out[sym] = {"bars": bars, "events": events}
    return {"ts_ns": now_ns, "symbols": out}


def _walkforward() -> dict:
    """Invented, and labelled so: the real file comes from the Phase 5 run on the PC."""

    def row(trades, win, mean, p, d):
        return {
            "trades": trades,
            "win_rate": win,
            "mean_r": mean,
            "total_r": round(trades * mean, 3),
            "max_drawdown_r": round(6 + trades * 0.08, 3),
            "losing_streak": 7,
            "p_value": p,
            "p_holm": min(1.0, round(p * 3, 4)),
            "deflated_sharpe": d,
            "checks": {
                "enough_trades": trades >= 30,
                "significant_after_holm": False,
                "deflated_sharpe_ok": False,
                "beats_control": mean > -0.05,
            },
            "passes": False,
        }

    return {
        "demo": True,
        "days": [f"d{i}" for i in range(60)],
        "missing": [],
        "oos": {
            "H1": row(22, 0.36, -0.08, 0.71, 0.08),
            "H2": row(41, 0.39, 0.03, 0.38, 0.31),
            "H3": row(17, 0.35, -0.12, 0.80, 0.04),
            "H4": row(58, 0.41, 0.06, 0.19, 0.52),
            "H5": row(33, 0.33, -0.15, 0.88, 0.02),
        },
        "control": {"trades": 120, "win_rate": 0.31, "mean_r": -0.05, "total_r": -6.0},
        "account": {
            "all_hypotheses": {
                "start_equity": 10000,
                "end_equity": 9687.6,
                "net_pnl": -312.4,
                "trades_taken": 96,
                "fees": 192.0,
                "max_drawdown_pct": 3.9,
                "losing_streak": 8,
            },
            "passing_only": None,
        },
        "gate": {},
    }


def write_demo(out: Path, config_src: Path = Path("config"), now: datetime | None = None) -> date:
    now = now or datetime.now(ET)
    day = demo_day(now)
    for sub in ("data", "reports", "run", "config"):
        shutil.rmtree(out / sub, ignore_errors=True)
    (out / "config").mkdir(parents=True)
    for name in ("risk_limits.yaml", "jev.yaml"):
        shutil.copy(config_src / name, out / "config" / name)
    from flowforge.config.risk_limits import load_risk_limits

    sha = load_risk_limits(out / "config" / "risk_limits.yaml").sha256
    j = Journal(out / "data" / "journal" / "node")
    at = lambda hh, mm, ss=0: _ns(day, hh, mm, ss)  # noqa: E731
    j.write(at(9, 25), "start", symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA"], risk_limits_sha256=sha)
    j.write(at(9, 30), "day_start", day=day.isoformat(), equity=START_EQUITY)
    events = []
    for t in TRADES:
        _trade_events(j, day, t)
    nv = f"H1:NVDA:{at(10, 5)}"
    j.write(
        at(10, 5),
        "signal",
        intent=nv,
        setup="H1",
        symbol="NVDA",
        reason="stacked buy imbalance retested",
        zone=[188.40, 188.55],
        enabled=False,
    )
    ap = f"H4:AAPL:{at(10, 48)}"
    j.write(
        at(10, 48),
        "signal",
        intent=ap,
        setup="H4",
        symbol="AAPL",
        reason="support zone held, delta turned up",
        zone=[231.10, 231.25],
        enabled=True,
    )
    j.write(
        at(10, 48, 1),
        "risk_decision",
        intent=ap,
        symbol="AAPL",
        allowed=False,
        reasons=["spread 14.2 bps > max 10 bps"],
        plan=None,
        sizing={},
    )
    j.write(at(11, 52, 3), "alert", level="info", message="stop filled: QQQ 2 sh at 496.35", symbol="QQQ")
    nv2 = f"H4:NVDA:{at(14, 10)}"
    j.write(
        at(14, 10),
        "signal",
        intent=nv2,
        setup="H4",
        symbol="NVDA",
        reason="support zone held, delta turned up",
        zone=[188.70, 188.82],
        enabled=True,
    )
    j.write(
        at(14, 10, 1),
        "risk_decision",
        intent=nv2,
        symbol="NVDA",
        allowed=False,
        reasons=["legacy PDT: 3 day trades in the last 5 days (max 3) while equity < $25,000"],
        plan=None,
        sizing={},
    )
    ts = at(9, 30)
    while ts <= at(*AS_OF):
        events.append(ts)
        ts += 60 * NS
    # Journal files are append-only and time ordered; equity marks go in a second pass
    # into the same file, which the reader sorts by time.
    for ts in events:
        eq = _equity_at(ts, day)
        j.write(ts, "equity_mark", equity=eq, day_start_equity=START_EQUITY, week_peak_equity=10_021.4)
    j.close()
    ops = Journal(out / "data" / "journal" / "ops")
    ops.write(
        at(9, 0),
        "premarket_check",
        level="info",
        title=f"FlowForge Pre-market check {day}: all clear",
        summary="OK Trading day: open 09:30, close 16:00 ET\nOK Kill switch: off\nOK IB Gateway: answers",
    )
    ops.close()
    state = {
        "day": day.isoformat(),
        "day_start_equity": START_EQUITY,
        "week": list(day.isocalendar()[:2]),
        "week_peak_equity": 10_021.4,
        "daily_halt": None,
        "weekly_halt": None,
        "entries_by_day": {day.isoformat(): 3},
        "seen_intents": [],
        "consecutive_rejects": 0,
    }
    (out / "data" / "state").mkdir(parents=True, exist_ok=True)
    (out / "data" / "state" / "risk_state.json").write_text(json.dumps(state, indent=2))
    (out / "reports" / "phase5").mkdir(parents=True)
    (out / "reports" / "phase5" / "walkforward.json").write_text(json.dumps(_walkforward(), indent=2))
    daily = out / "reports" / "daily"
    daily.mkdir(parents=True)
    (daily / f"{day}-premarket.json").write_text(
        json.dumps(
            {
                "kind": "premarket",
                "day": day.isoformat(),
                "level": "info",
                "checks": [{"name": "Kill switch", "ok": True, "detail": "off"}],
                "numbers": {},
            }
        )
    )
    from flowforge.ops import scorecard

    # The Phase 7 scorecard of the one invented day: far from the gate, as it should be.
    limits = load_risk_limits(out / "config" / "risk_limits.yaml").limits
    card = scorecard.scorecard(out / "data" / "journal", limits, day.isoformat())
    (out / "reports" / "paper").mkdir(parents=True)
    (out / "reports" / "paper" / "scorecard.json").write_text(json.dumps(card, indent=2))
    beat(out, day)
    return day


def beat(out: Path, day: date) -> None:
    """Fresh heartbeat, watchdog file and tape, as if the node were running."""
    now = time.time_ns()
    eq = _equity_at(_ns(day, *AS_OF), day)
    run = out / "run"
    write_heartbeat(
        run / "heartbeat.json", Heartbeat(now, "demo", {"MSFT": 2}, 1, None, False, eq, START_EQUITY)
    )
    (run / "watchdog.json").write_text(json.dumps({"ts_ns": now, "action": "none", "reason": ""}))
    (run / "tape.json").write_text(json.dumps(_tape(day, now)))


def keep_beating(out: Path, day: date, every_s: float = 1.0) -> threading.Thread:
    def loop() -> None:
        while True:
            beat(out, day)
            time.sleep(every_s)

    th = threading.Thread(target=loop, name="demo-heartbeat", daemon=True)
    th.start()
    return th
