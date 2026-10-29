"""Pre-market check and end-of-day report, by hand or on a schedule.

    python -m flowforge.ops.daily premarket          # before the open
    python -m flowforge.ops.daily eod [--day DATE]   # after the close
    python -m flowforge.ops.daily schedule           # service: both, every trading day

Both only read: they never place, change or cancel an order, and never touch the risk
limits. Each writes a Markdown report to reports/daily/, a record to the ops journal (the
alert relay sends its summary to the phone), and prints the report.

Pre-market (09:00 ET): is today a trading day, is the kill switch off, is any halt still on,
do the risk limits load (and match what the node started with), is IB Gateway reachable,
is the node's heartbeat fresh, is the watchdog alive, how many day trades are left, is there
disk space, is Jev on. On Mondays it reminds the operator of the IBKR weekly login.

End of day (16:15 ET, 13:15 on early closes): trades, net after costs, wins and losses,
losing streak, the day's equity drawdown, refusals, rejects, alerts, unreconciled fills,
and whether the account is flat with no open orders.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from flowforge.config.risk_limits import load_risk_limits
from flowforge.execution.heartbeat import read_heartbeat, wall_clock_ns
from flowforge.execution.journal import Journal
from flowforge.ops.ledger import build_trades, day_numbers, read_journal
from flowforge.risk.calendar import ET, NS, et_date, is_trading_day, session_at, session_for
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

PREMARKET_AT = (9, 0)
EOD_AFTER_CLOSE_MIN = 15
MIN_FREE_GB = 5
HEARTBEAT_FRESH_S = 15
PDT_EQUITY = 25_000


@dataclass
class Paths:
    risk_limits: Path = Path("config/risk_limits.yaml")
    jev: Path = Path("config/jev.yaml")
    state: Path = Path("data/state/risk_state.json")
    heartbeat: Path = Path(os.environ.get("FLOWFORGE_HEARTBEAT", "/var/run/flowforge/heartbeat.json"))
    journals: Path = Path("data/journal")
    reports: Path = Path("reports/daily")
    data: Path = Path("data")
    kill_file: Path | None = None
    ib_host: str = os.environ.get("IB_HOST", "127.0.0.1")
    ib_port: int = int(os.environ.get("IB_PORT", "4002"))

    @property
    def watchdog(self) -> Path:
        return self.heartbeat.with_name("watchdog.json")


@dataclass
class Check:
    name: str
    ok: bool | None  # None = information only
    detail: str


@dataclass
class Report:
    kind: str  # premarket | eod
    day: date
    checks: list[Check] = field(default_factory=list)
    numbers: dict = field(default_factory=dict)

    @property
    def level(self) -> str:
        return "critical" if any(c.ok is False for c in self.checks) else "info"

    def add(self, name: str, ok: bool | None, detail: str) -> None:
        self.checks.append(Check(name, ok, detail))

    def title(self) -> str:
        what = "Pre-market check" if self.kind == "premarket" else "End of day"
        problems = sum(1 for c in self.checks if c.ok is False)
        state = f"{problems} problem{'s' if problems != 1 else ''}" if problems else "all clear"
        return f"FlowForge {what} {self.day.isoformat()}: {state}"

    def summary(self) -> str:
        lines = [
            f"{'OK ' if c.ok else 'NO ' if c.ok is False else '-  '}{c.name}: {c.detail}" for c in self.checks
        ]
        return "\n".join(lines)

    def markdown(self) -> str:
        mark = {True: "ok", False: "**PROBLEM**", None: "info"}
        rows = "\n".join(f"| {c.name} | {mark[c.ok]} | {c.detail} |" for c in self.checks)
        return f"# {self.title()}\n\n| Check | Result | Detail |\n|---|---|---|\n{rows}\n"


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _node_limits_hash(records: list[dict]) -> str | None:
    starts = [r for r in records if r["event"] == "start" and r.get("risk_limits_sha256")]
    return starts[-1]["risk_limits_sha256"] if starts else None


def premarket(paths: Paths, now_ns: int, port_check=None) -> Report:
    port_check = port_check or _port_open
    day = et_date(now_ns)
    rep = Report("premarket", day)
    session = session_for(day)
    if session is None:
        rep.add("Trading day", None, "market closed today: nothing will trade")
    else:
        close = datetime.fromtimestamp(session.close_ns / NS, ET).strftime("%H:%M")
        rep.add(
            "Trading day",
            True,
            f"open 09:30, close {close} ET" + (" (early close)" if session.early_close else ""),
        )
    try:
        loaded = load_risk_limits(paths.risk_limits)
    except Exception as exc:  # noqa: BLE001 (any failure to load is a stop-trading problem)
        rep.add("Risk limits", False, f"cannot load {paths.risk_limits}: {type(exc).__name__}")
        return rep
    limits = loaded.limits
    sha = loaded.sha256
    node_sha = _node_limits_hash(read_journal(paths.journals, sources=["node"]).records)
    if node_sha is not None and node_sha != sha:
        rep.add("Risk limits", False, "file changed since the node started: restart the node to load it")
    else:
        rep.add("Risk limits", True, f"loaded, sha256 {sha[:12]}")
    kill = KillSwitch(paths.kill_file or limits.kill_switch.file_flag)
    rep.add("Kill switch", not kill.is_engaged(), kill.reason() or "off")
    state = RiskState(limits, paths.state)
    halt = state.weekly_halt
    rep.add("Weekly halt", halt is None, halt.reason if halt else "none")
    beat = read_heartbeat(paths.heartbeat)
    if beat is None:
        rep.add("Trading node", False, "no heartbeat: the node is not running")
    else:
        age = beat.age_s(now_ns)
        rep.add("Trading node", age <= HEARTBEAT_FRESH_S, f"last heartbeat {age:.0f} s ago")
        if beat.positions:
            rep.add("Overnight shares", False, f"holding {beat.positions} before the open: check the account")
    alive = _read_json(paths.watchdog)
    if alive is None:
        rep.add("Watchdog", False, "not running (start it: docker compose --profile paper up -d watchdog)")
    else:
        age = (now_ns - int(alive.get("ts_ns", 0))) / NS
        rep.add("Watchdog", age <= HEARTBEAT_FRESH_S, f"last check {age:.0f} s ago")
    reachable = port_check(paths.ib_host, paths.ib_port)
    where = f"{paths.ib_host}:{paths.ib_port}"
    rep.add("IB Gateway", reachable, f"{where} {'answers' if reachable else 'does not answer'}")
    if day.weekday() == 0:
        rep.add("IBKR weekly login", None, "Monday: approve the IBKR login on your phone if asked")
    equity = beat.equity if beat is not None and beat.equity is not None else state.day_start_equity
    if limits.trading.pdt_regime == "legacy_pdt" and equity and equity < PDT_EQUITY:
        state.day = day
        used = state.day_trades_in_window()
        left = max(0, limits.trading.legacy_pdt_max_day_trades - used)
        rep.add("Day trades left", None, f"{left} of {limits.trading.legacy_pdt_max_day_trades} in 5 days")
    if limits.trading.pdt_regime == "cash":
        waiting = state.unsettled_cash(day)
        rep.add(
            "Settled cash",
            None,
            f"{waiting:,.2f} USD of sales still settling (T+1); buys use settled cash only",
        )
    free_gb = shutil.disk_usage(paths.data if paths.data.exists() else Path(".")).free / 1e9
    rep.add("Disk space", free_gb >= MIN_FREE_GB, f"{free_gb:.1f} GB free")
    jev = _read_yaml_flag(paths.jev)
    rep.add("Jev gate", None, jev)
    return rep


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _read_yaml_flag(path: Path) -> str:
    from flowforge.gate.jev import load_config

    try:
        cfg = load_config(path)
    except Exception as exc:  # noqa: BLE001
        return f"config unreadable ({type(exc).__name__}): gate stays off"
    if not cfg.enabled:
        return "off"
    return f"on ({'shadow: asks, never blocks' if cfg.shadow else 'can veto'}), {cfg.model}"


def eod(paths: Paths, day: date, now_ns: int) -> Report:
    rep = Report("eod", day)
    read = read_journal(paths.journals, day.isoformat())
    trades, orphans = build_trades([r for r in read.records if r["source"] == "node"])
    n = day_numbers(read.records, trades, orphans)
    rep.numbers = n
    rep.add(
        "Trades",
        None,
        f"{n['trades']} closed ({n['wins']} won, {n['losses']} lost), {n['not_filled']} not filled",
    )
    rep.add(
        "Net after costs",
        None,
        f"{n['net']:+,.2f} USD (commissions {n['commission']:,.2f}), {n['r_total']:+.2f} R",
    )
    rep.add(
        "Worst drawdown today",
        None,
        f"{n['max_drawdown_usd']:,.2f} USD; longest losing streak {n['losing_streak']}",
    )
    rep.add("Signals", None, f"{n['signals']} signals, {n['risk_refusals']} refused by risk")
    rep.add("Rejected orders", n["rejections"] == 0, str(n["rejections"]))
    rep.add("Critical alerts", n["critical"] == 0, str(n["critical"]))
    rep.add("Unreconciled fills", not orphans, str(len(orphans)))
    if read.bad_lines:
        rep.add("Journal", False, f"{read.bad_lines} unreadable lines")
    open_trades = [t.intent for t in trades if t.status == "open"]
    rep.add("Trades still open", not open_trades, ", ".join(open_trades) or "none")
    beat = read_heartbeat(paths.heartbeat)
    if beat is None:
        rep.add("Flat at the broker", None, "no heartbeat to check: look in IBKR")
    else:
        flat = not beat.positions and beat.open_orders == 0
        rep.add(
            "Flat at the broker",
            flat,
            "yes" if flat else f"holding {beat.positions}, {beat.open_orders} open orders",
        )
    return rep


def publish(rep: Report, paths: Paths, now_ns: int) -> Path:
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"{rep.day.isoformat()}-{rep.kind}.md"
    out.write_text(rep.markdown(), encoding="utf-8")
    (paths.reports / f"{rep.day.isoformat()}-{rep.kind}.json").write_text(
        json.dumps(
            {
                "kind": rep.kind,
                "day": rep.day.isoformat(),
                "level": rep.level,
                "checks": [vars(c) for c in rep.checks],
                "numbers": rep.numbers,
            },
            indent=2,
        )
    )
    event = "premarket_check" if rep.kind == "premarket" else "eod_report"
    Journal(paths.journals / "ops").write(
        now_ns, event, level=rep.level, title=rep.title(), summary=rep.summary(), report=str(out)
    )
    return out


# -- schedule ------------------------------------------------------------------------------
def due(now_ns: int, done: dict[str, str]) -> list[str]:
    """Which jobs should run now. `done` maps job -> last day it ran."""
    now = datetime.fromtimestamp(now_ns / NS, ET)
    day = now.date()
    if not is_trading_day(day):
        return []
    jobs = []
    if (now.hour, now.minute) >= PREMARKET_AT and done.get("premarket") != day.isoformat():
        session = session_at(now_ns)
        if session is None or now_ns < session.close_ns:
            jobs.append("premarket")
    session = session_for(day)
    if (
        session is not None
        and now_ns >= session.close_ns + EOD_AFTER_CLOSE_MIN * 60 * NS
        and done.get("eod") != day.isoformat()
    ):
        jobs.append("eod")
    return jobs


def write_scorecard(paths: Paths) -> Path:
    """After the close: refresh the Phase 7 paper scorecard (reports/paper/scorecard.*)."""
    from flowforge.config.risk_limits import load_risk_limits
    from flowforge.ops import scorecard

    card = scorecard.scorecard(paths.journals, load_risk_limits(paths.risk_limits).limits)
    out = paths.reports.parent / "paper"
    out.mkdir(parents=True, exist_ok=True)
    (out / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
    (out / "scorecard.md").write_text(scorecard.markdown(card), encoding="utf-8")
    return out / "scorecard.md"


def run_job(job: str, paths: Paths, now_ns: int) -> Report:
    rep = premarket(paths, now_ns) if job == "premarket" else eod(paths, et_date(now_ns), now_ns)
    publish(rep, paths, now_ns)
    if job == "eod":
        write_scorecard(paths)
    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowForge pre-market check and end-of-day report")
    parser.add_argument("command", choices=["premarket", "eod", "schedule"])
    parser.add_argument("--day", type=date.fromisoformat, default=None, help="eod only: which day")
    parser.add_argument("--state-file", type=Path, default=Path("data/state/daily_jobs.json"))
    args = parser.parse_args(argv)
    paths = Paths()
    now = wall_clock_ns()
    if args.command == "premarket":
        rep = premarket(paths, now)
    elif args.command == "eod":
        rep = eod(paths, args.day or et_date(now), now)
    else:
        print("Daily jobs scheduled: pre-market 09:00 ET, end of day 15 min after the close.", flush=True)
        while True:
            done = _read_json(args.state_file) or {}
            now = wall_clock_ns()
            for job in due(now, done):
                rep = run_job(job, paths, now)
                print(rep.markdown(), flush=True)
                done[job] = et_date(now).isoformat()
                args.state_file.parent.mkdir(parents=True, exist_ok=True)
                args.state_file.write_text(json.dumps(done))
            time.sleep(30)
    publish(rep, paths, now)
    print(rep.markdown())
    return 0 if rep.level == "info" else 1


if __name__ == "__main__":
    sys.exit(main())
