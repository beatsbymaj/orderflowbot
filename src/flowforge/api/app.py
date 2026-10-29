"""FlowForge dashboard API: read-only views of the node's files, plus one button.

    python -m flowforge.api                       # http://127.0.0.1:8080
    python -m flowforge.api --demo data/demo      # made-up data, for a walkthrough

Everything here reads files the node, the watchdog and the ops jobs already write: the
journals, the heartbeat, the risk state, the tape snapshot, the reports, the risk limits.
Nothing reads from or talks to the broker, and nothing writes, with one exception:
`POST /api/kill` turns the kill switch ON. There is no endpoint to turn it off, clear a
halt or change a limit; those stay in `riskctl` and in git (CLAUDE.md rule 2, ADR-0015).

The kill request must carry the header `X-FlowForge-Action: engage-kill`. Browsers can't
add a custom header to a cross-site request without the server's say-so, so another web
page can't press the button for you. The API listens on localhost only.

Secrets: settings show whether a channel is configured, never its value; journal records
are scrubbed of any secret-looking field (ops/ledger.scrub).
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from flowforge.api.crew import CrewInputs, build_crew, crew_summary
from flowforge.config.risk_limits import load_risk_limits
from flowforge.execution.heartbeat import read_heartbeat, wall_clock_ns
from flowforge.execution.journal import Journal
from flowforge.gate.jev import load_config as load_jev
from flowforge.ops import alerts
from flowforge.ops.daily import HEARTBEAT_FRESH_S, MIN_FREE_GB, PDT_EQUITY, _node_limits_hash, _port_open
from flowforge.ops.ledger import build_signals, build_trades, day_numbers, journal_days, read_journal, scrub
from flowforge.risk.calendar import ET, NS, et_date, session_for
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

STATIC = Path(__file__).parent / "static"
KILL_HEADER = "engage-kill"
TAPE_STALE_S = 30
JOURNAL_PAGE = 200


@dataclass
class ApiPaths:
    root: Path = Path(".")
    run_dir: Path = Path(os.environ.get("FLOWFORGE_RUN_DIR", "/var/run/flowforge"))
    demo: bool = False
    kill_file: Path | None = None  # default: risk_limits.yaml kill_switch.file_flag
    ib_host: str = os.environ.get("IB_HOST", "127.0.0.1")
    ib_port: int = int(os.environ.get("IB_PORT", "4002"))
    env: dict = field(default_factory=lambda: dict(os.environ))

    @property
    def journals(self) -> Path:
        return self.root / "data" / "journal"

    @property
    def state(self) -> Path:
        return self.root / "data" / "state" / "risk_state.json"

    @property
    def heartbeat(self) -> Path:
        return self.run_dir / "heartbeat.json"

    @property
    def watchdog(self) -> Path:
        return self.run_dir / "watchdog.json"

    @property
    def tape(self) -> Path:
        return self.run_dir / "tape.json"

    @property
    def risk_limits(self) -> Path:
        return self.root / "config" / "risk_limits.yaml"

    @property
    def jev(self) -> Path:
        return self.root / "config" / "jev.yaml"

    @property
    def reports(self) -> Path:
        return self.root / "reports"


class KillBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def _iso(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / NS, UTC).astimezone(ET).isoformat(timespec="seconds")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def session_state(now_ns: int) -> dict:
    day = et_date(now_ns)
    s = session_for(day)
    if s is None:
        return {"day": day.isoformat(), "state": "closed", "label": "Market closed today"}
    close = datetime.fromtimestamp(s.close_ns / NS, ET).strftime("%H:%M")
    if now_ns < s.open_ns:
        state, label = "pre", f"Opens 09:30 ET, closes {close}"
    elif now_ns < s.close_ns:
        state, label = "open", f"Open until {close} ET"
    else:
        state, label = "post", f"Closed at {close} ET"
    return {"day": day.isoformat(), "state": state, "label": label, "early_close": s.early_close}


def research_summary(reports: Path) -> dict | None:
    data = _read_json(reports / "phase5" / "walkforward.json")
    if not isinstance(data, dict):
        return None
    rows = []
    for setup, o in sorted((data.get("oos") or {}).items()):
        rows.append(
            {
                "setup": setup,
                "trades": o.get("trades"),
                "win_rate": o.get("win_rate"),
                "mean_r": o.get("mean_r"),
                "total_r": o.get("total_r"),
                "max_drawdown_r": o.get("max_drawdown_r"),
                "losing_streak": o.get("losing_streak"),
                "p_holm": o.get("p_holm"),
                "deflated_sharpe": o.get("deflated_sharpe"),
                "checks": o.get("checks") or {},
                "passes": bool(o.get("passes")),
            }
        )
    account = data.get("account") or {}
    return {
        "days": len(data.get("days") or []),
        "missing": len(data.get("missing") or []),
        "setups": rows,
        "passed": [r["setup"] for r in rows if r["passes"]],
        "control": data.get("control"),
        "account_all": {
            k: v for k, v in (account.get("all_hypotheses") or {}).items() if k != "equity_by_day"
        },
        "account_passing": account.get("passing_only"),
        "gate": data.get("gate") or {},
    }


def create_app(paths: ApiPaths | None = None, clock=wall_clock_ns, port_check=_port_open) -> FastAPI:
    paths = paths or ApiPaths()
    app = FastAPI(title="FlowForge dashboard API", docs_url=None, redoc_url=None, openapi_url=None)

    def limits():
        return load_risk_limits(paths.risk_limits)

    def kill_switch(loaded=None) -> KillSwitch:
        loaded = loaded or limits()
        return KillSwitch(paths.kill_file or loaded.limits.kill_switch.file_flag)

    def latest_day(now_ns: int) -> str:
        days = journal_days(paths.journals)
        today = et_date(now_ns).isoformat()
        return today if today in days or not days else days[0]

    @app.get("/api/status")
    def status() -> dict:
        now = clock()
        loaded = limits()
        lim = loaded.limits
        kill = kill_switch(loaded)
        state = RiskState(lim, paths.state)
        beat = read_heartbeat(paths.heartbeat)
        day = latest_day(now)
        read = read_journal(paths.journals, day)
        node_recs = [r for r in read.records if r["source"] == "node"]
        trades, orphans = build_trades(node_recs)
        numbers = day_numbers(node_recs, trades, orphans)
        marks = [r for r in node_recs if r["event"] == "equity_mark"]
        equity = beat.equity if beat and beat.equity is not None else numbers["equity_last"]
        day_start = (beat.day_start_equity if beat and beat.day_start_equity else None) or numbers[
            "equity_start"
        ]
        node = None
        if beat is not None:
            age = beat.age_s(now)
            node = {
                "age_s": round(age, 1),
                "stale": age > HEARTBEAT_FRESH_S,
                "positions": beat.positions,
                "open_orders": beat.open_orders,
                "equity": beat.equity,
            }
        budget = []
        if day_start:
            used = max(0.0, day_start - (equity or day_start))
            cap = day_start * lim.loss.max_daily_loss_pct / 100
            budget.append(
                {
                    "key": "daily_loss",
                    "label": "Daily loss",
                    "used": round(used, 2),
                    "limit": round(cap, 2),
                    "unit": "usd",
                    "note": f"{lim.loss.max_daily_loss_pct}% of {day_start:,.2f}: flatten, stop for the day",
                }
            )
        if state.week_peak_equity:
            used = max(0.0, state.week_peak_equity - (equity or state.week_peak_equity))
            cap = state.week_peak_equity * lim.loss.max_weekly_drawdown_pct / 100
            budget.append(
                {
                    "key": "weekly_drawdown",
                    "label": "Weekly drawdown",
                    "used": round(used, 2),
                    "limit": round(cap, 2),
                    "unit": "usd",
                    "note": f"{lim.loss.max_weekly_drawdown_pct}% of the week's peak: stop until re-enabled",
                }
            )
        budget.append(
            {
                "key": "trades",
                "label": "Trades today",
                "used": state.trades_today() if state.day else 0,
                "limit": lim.trading.max_trades_per_day,
                "unit": "count",
                "note": "entries sent today",
            }
        )
        if lim.trading.pdt_regime == "legacy_pdt" and (equity or 0) < PDT_EQUITY:
            if state.day is None:
                state.day = et_date(now)
            budget.append(
                {
                    "key": "day_trades",
                    "label": "Day trades, 5 days",
                    "used": state.day_trades_in_window(),
                    "limit": lim.trading.legacy_pdt_max_day_trades,
                    "unit": "count",
                    "note": "pattern day trader rule while the account is under $25,000",
                }
            )
        if lim.trading.pdt_regime == "cash":
            if state.day is None:
                state.day = et_date(now)
            budget.append(
                {
                    "key": "unsettled",
                    "label": "Sales still settling",
                    "used": state.unsettled_cash(),
                    "limit": None,
                    "unit": "usd",
                    "note": "cash account: sales settle next trading day; buys use settled cash only",
                }
            )
        held = len(beat.positions) if beat else 0
        budget.append(
            {
                "key": "positions",
                "label": "Open positions",
                "used": held,
                "limit": lim.portfolio.max_concurrent_positions,
                "unit": "count",
                "note": "long only",
            }
        )
        tape = _read_json(paths.tape) or {}
        last_px = {
            s: (v.get("bars") or [{}])[-1].get("close") for s, v in (tape.get("symbols") or {}).items()
        }
        positions = []
        for t in trades:
            if t.status != "open":
                continue
            px = last_px.get(t.symbol)
            unreal = round((px - t.entry_price) * (t.shares - t.sold), 2) if px and t.entry_price else None
            positions.append(t.as_dict() | {"last": px, "unrealized": unreal})
        halt = state.halt_reason()
        engaged = kill.is_engaged()
        if engaged:
            finding = f"Kill switch is on: {kill.reason()}. Nothing new will trade."
        elif halt:
            finding = halt[0].upper() + halt[1:] + "."
        elif beat is None:
            finding = "The trading node is not running, so nothing can trade."
        elif node and node["stale"]:
            age = node["age_s"]
            finding = f"The trading node last checked in {age:.0f} s ago. The watchdog takes over after 15 s."
        elif positions:
            n = len(positions)
            held = f"{n} positions, each" if n > 1 else "1 position,"
            finding = f"Holding {held} with a stop at the broker."
        elif numbers["trades"]:
            finding = f"Flat. {numbers['trades']} trades today, {numbers['net']:+,.2f} USD after costs."
        else:
            finding = "Flat, no trades yet today."
        return {
            "product": "FlowForge",
            "account": "IBKR paper",
            "demo": paths.demo,
            "now": _iso(now),
            "day": day,
            "session": session_state(now),
            "kill": {"on": engaged, "reason": kill.reason()},
            "halt": halt,
            "node": node,
            "equity": equity,
            "day_start_equity": day_start,
            "finding": finding,
            "budget": budget,
            "today": numbers,
            "equity_curve": [[int(m["ts_ns"]), float(m["equity"])] for m in marks],
            "positions": positions,
            "trades": [t.as_dict() for t in trades][-8:],
            "recent": [
                r
                for r in read.records
                if r["event"]
                in (
                    "alert",
                    "kill_engaged",
                    "watchdog_alert",
                    "watchdog_flatten",
                    "order_rejected",
                    "risk_decision",
                    "premarket_check",
                    "eod_report",
                )
            ][-6:],
        }

    @app.get("/api/trades")
    def trades_view(day: str | None = None) -> dict:
        now = clock()
        day = day or latest_day(now)
        recs = [r for r in read_journal(paths.journals, _day(day)).records if r["source"] == "node"]
        trades, orphans = build_trades(recs)
        return {
            "day": day,
            "trades": [t.as_dict() for t in trades],
            "unreconciled": orphans,
            "numbers": day_numbers(recs, trades, orphans),
        }

    @app.get("/api/signals")
    def signals_view(day: str | None = None) -> dict:
        now = clock()
        day = day or latest_day(now)
        recs = [r for r in read_journal(paths.journals, _day(day)).records if r["source"] == "node"]
        trades, _ = build_trades(recs)
        rows = build_signals(recs, trades)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        return {"day": day, "signals": rows, "outcomes": counts, "research": research_summary(paths.reports)}

    @app.get("/api/journal")
    def journal_view(
        day: str | None = None,
        event: str = "",
        exclude: str = "",
        source: str = "",
        q: str = "",
        offset: int = 0,
        limit: int = JOURNAL_PAGE,
    ) -> dict:
        now = clock()
        days = journal_days(paths.journals)
        day = day or latest_day(now)
        read = read_journal(paths.journals, _day(day))
        rows = read.records
        kinds: dict[str, int] = {}
        for r in rows:
            kinds[r["event"]] = kinds.get(r["event"], 0) + 1
        if event:
            wanted = set(event.split(","))
            rows = [r for r in rows if r["event"] in wanted]
        if exclude:
            unwanted = set(exclude.split(","))
            rows = [r for r in rows if r["event"] not in unwanted]
        if source:
            rows = [r for r in rows if r["source"] == source]
        if q:
            needle = q.lower()
            rows = [r for r in rows if needle in json.dumps(r, default=str).lower()]
        rows = list(reversed(rows))
        limit = max(1, min(limit, 1000))
        return {
            "day": day,
            "days": days,
            "events": dict(sorted(kinds.items())),
            "total": len(rows),
            "offset": offset,
            "rows": rows[offset : offset + limit],
            "bad_lines": read.bad_lines,
        }

    @app.get("/api/tape")
    def tape_view() -> dict:
        now = clock()
        data = _read_json(paths.tape)
        if not isinstance(data, dict):
            return {"available": False, "symbols": {}, "age_s": None}
        age = (now - int(data.get("ts_ns", 0))) / NS
        return {
            "available": True,
            "age_s": round(age, 1),
            "stale": age > TAPE_STALE_S,
            "symbols": data.get("symbols") or {},
        }

    @app.get("/api/approvals")
    def approvals_view() -> dict:
        return {
            "mode": "paper",
            "items": [],
            "note": "Confirmation mode is Phase 8 (live stage 1). In paper, trades that pass the risk limits "
            "go straight to the IBKR paper account, so there is nothing to approve here yet.",
        }

    @app.get("/api/proposals")
    def proposals_view() -> dict:
        folder = paths.reports / "proposals"
        items = []
        for p in sorted(folder.glob("*.json")) if folder.exists() else []:
            data = _read_json(p)
            if isinstance(data, dict):
                items.append(scrub(data) | {"file": p.name})
        return {"items": items, "research": research_summary(paths.reports)}

    @app.get("/api/health")
    def health_view() -> dict:
        now = clock()
        checks = []

        def add(name: str, state: str, detail: str, group: str) -> None:
            checks.append({"name": name, "state": state, "detail": detail, "group": group})

        try:
            loaded = limits()
            node_sha = _node_limits_hash(read_journal(paths.journals, sources=["node"]).records)
            same = node_sha is None or node_sha == loaded.sha256
            add(
                "Risk limits",
                "ok" if same else "bad",
                f"sha256 {loaded.sha256[:12]}"
                + ("" if same else ", changed since the node started: restart it"),
                "Safety",
            )
            kill = kill_switch(loaded)
            add("Kill switch", "bad" if kill.is_engaged() else "ok", kill.reason() or "off", "Safety")
            state = RiskState(loaded.limits, paths.state)
            halt = state.halt_reason()
            add("Halts", "bad" if halt else "ok", halt or "none", "Safety")
        except Exception as exc:  # noqa: BLE001 (shown, never hidden)
            add("Risk limits", "bad", f"cannot load: {type(exc).__name__}", "Safety")
        beat = read_heartbeat(paths.heartbeat)
        if beat is None:
            add("Trading node", "bad", "no heartbeat file", "Processes")
        else:
            age = beat.age_s(now)
            add(
                "Trading node",
                "ok" if age <= HEARTBEAT_FRESH_S else "bad",
                f"heartbeat {age:.1f} s ago",
                "Processes",
            )
        dog = _read_json(paths.watchdog)
        if not isinstance(dog, dict):
            add("Watchdog", "bad", "not running", "Processes")
        else:
            age = (now - int(dog.get("ts_ns", 0))) / NS
            add(
                "Watchdog",
                "ok" if age <= HEARTBEAT_FRESH_S else "bad",
                f"checked {age:.1f} s ago, last decision: {dog.get('action', '-')}",
                "Processes",
            )
        up = port_check(paths.ib_host, paths.ib_port)
        add(
            "IB Gateway",
            "ok" if up else "bad",
            f"port {paths.ib_port} {'answers' if up else 'closed'}",
            "Broker",
        )
        if et_date(now).weekday() == 0:
            add("IBKR weekly login", "info", "Monday: approve the login on your phone if IBKR asks", "Broker")
        tape = _read_json(paths.tape)
        session = session_state(now)
        if isinstance(tape, dict):
            age = (now - int(tape.get("ts_ns", 0))) / NS
            live = session["state"] == "open"
            add(
                "Market data",
                "warn" if live and age > TAPE_STALE_S else "ok",
                f"tape updated {age:.0f} s ago",
                "Data",
            )
        else:
            add("Market data", "info", "no tape snapshot yet", "Data")
        read = read_journal(paths.journals, et_date(now).isoformat())
        last = read.records[-1]["ts_ns"] if read.records else None
        add(
            "Journal",
            "warn" if read.bad_lines else "ok",
            (f"last record {_iso(int(last))}" if last else "nothing written today")
            + (f", {read.bad_lines} unreadable lines" if read.bad_lines else ""),
            "Data",
        )
        _, orphans = build_trades([r for r in read.records if r["source"] == "node"])
        add("Unreconciled fills", "bad" if orphans else "ok", str(len(orphans)), "Broker")
        target = paths.root / "data"
        free = shutil.disk_usage(target if target.exists() else paths.root).free / 1e9
        add("Disk", "ok" if free >= MIN_FREE_GB else "bad", f"{free:.1f} GB free", "Data")
        ch = alerts.configured(paths.env)
        on = [k for k, v in ch.items() if v]
        add(
            "Phone alerts",
            "ok" if on else "warn",
            ", ".join(on) if on else "no channel set in .env",
            "Alerts",
        )
        failures = [r for r in read.records if r["event"] == "alert_send_failed"]
        if failures:
            add(
                "Alert delivery",
                "warn",
                f"{len(failures)} failed today, last: {failures[-1].get('error')}",
                "Alerts",
            )
        order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
        worst = min((order[c["state"]] for c in checks), default=3)
        return {"now": _iso(now), "overall": [k for k, v in order.items() if v == worst][0], "checks": checks}

    @app.get("/api/settings")
    def settings_view() -> dict:
        loaded = limits()
        raw = yaml.safe_load(paths.risk_limits.read_text(encoding="utf-8")) or {}
        try:
            jev = vars(load_jev(paths.jev)) | {"valid": True}
        except Exception as exc:  # noqa: BLE001
            jev = {"valid": False, "error": type(exc).__name__}
        research = research_summary(paths.reports)
        return {
            "risk_limits": raw,
            "risk_limits_sha256": loaded.sha256,
            "risk_limits_path": str(paths.risk_limits),
            "jev": jev,
            "alerts": alerts.configured(paths.env),
            "paper_setups": research["passed"] if research else [],
            "research_ran": research is not None,
            "kill_file": str(paths.kill_file or loaded.limits.kill_switch.file_flag),
        }

    @app.get("/api/reports")
    def reports_view() -> dict:
        folder = paths.reports / "daily"
        items = []
        for p in sorted(folder.glob("*.json"), reverse=True)[:20] if folder.exists() else []:
            data = _read_json(p)
            if isinstance(data, dict):
                items.append({k: data.get(k) for k in ("kind", "day", "level", "checks")})
        return {"items": items}

    @app.get("/api/paper")
    def paper_view() -> dict:
        """Phase 7: the paper scorecard (refreshed after each close), drills and soak runs."""
        folder = paths.reports / "paper"
        card = _read_json(folder / "scorecard.json")
        drills = []
        for p in sorted(folder.glob("drill-*.json"), reverse=True)[:12] if folder.exists() else []:
            data = _read_json(p)
            if isinstance(data, dict):
                failing = [c["name"] for c in data.get("checks", []) if c.get("ok") is False]
                drills.append(
                    {
                        "drill": data.get("drill"),
                        "started": data.get("started"),
                        "passed": data.get("passed"),
                        "failing": failing,
                    }
                )
        soaks = []
        for p in sorted(folder.glob("soak-*.json"), reverse=True)[:5] if folder.exists() else []:
            data = _read_json(p)
            if isinstance(data, list):
                soaks.append(
                    {
                        "file": p.stem,
                        "cycles": len(data),
                        "clean": sum(1 for c in data if not c.get("problems")),
                    }
                )
        return {"scorecard": card if isinstance(card, dict) else None, "drills": drills, "soaks": soaks}

    @app.get("/api/crew")
    def crew_view() -> dict:
        """Each working part as an agent; the states come from the files the Health page reads."""
        now = clock()

        def age(ts_ns) -> float:
            return (now - int(ts_ns)) / NS

        loaded = limits()
        beat = read_heartbeat(paths.heartbeat)
        tape = _read_json(paths.tape)
        dog = _read_json(paths.watchdog)
        read = read_journal(paths.journals, et_date(now).isoformat())
        counts: dict[str, int] = {}
        for r in read.records:
            if r["source"] == "node":
                counts[r["event"]] = counts.get(r["event"], 0) + 1
        try:
            jev = load_jev(paths.jev)
            jev_on, jev_shadow = bool(jev.enabled), bool(jev.shadow)
        except Exception:  # noqa: BLE001 (an unreadable config is off; Settings shows why)
            jev_on, jev_shadow = False, True
        inputs = CrewInputs(
            live=paths.demo or session_state(now)["state"] == "open",
            node_age_s=beat.age_s(now) if beat is not None else None,
            node_fresh_s=HEARTBEAT_FRESH_S,
            tape_age_s=age(tape.get("ts_ns", 0)) if isinstance(tape, dict) else None,
            tape_stale_s=TAPE_STALE_S,
            watchdog_age_s=age(dog.get("ts_ns", 0)) if isinstance(dog, dict) else None,
            gateway_up=port_check(paths.ib_host, paths.ib_port),
            kill_on=kill_switch(loaded).is_engaged(),
            halt=RiskState(loaded.limits, paths.state).halt_reason(),
            jev_enabled=jev_on,
            jev_shadow=jev_shadow,
            alert_channels=[k for k, v in alerts.configured(paths.env).items() if v],
            counts=counts,
            last_record_age_s=age(read.records[-1]["ts_ns"]) if read.records else None,
        )
        # today's totals, so the dashboard can cheer a real fill (it never invents one)
        tally = {k: counts.get(k, 0) for k in ("signal", "order_submit", "fill")}
        return {"now": _iso(now), "tally": tally} | crew_summary(build_crew(inputs))

    @app.post("/api/kill")
    def kill(
        body: KillBody, request: Request, x_flowforge_action: str | None = Header(default=None)
    ) -> JSONResponse:
        if x_flowforge_action != KILL_HEADER:
            raise HTTPException(status_code=400, detail="missing X-FlowForge-Action: engage-kill")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(status_code=403, detail="kill requests must come from this dashboard")
        now = clock()
        ks = kill_switch()
        already = ks.is_engaged()
        if not already:
            ks.engage(body.reason, "dashboard")
        Journal(paths.journals / "dashboard").write(
            now, "kill_engaged", reason=body.reason, by="dashboard", already_on=already
        )
        return JSONResponse({"on": True, "already_on": already, "reason": ks.reason()})

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _day(text: str) -> str:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="day must be YYYY-MM-DD") from exc
