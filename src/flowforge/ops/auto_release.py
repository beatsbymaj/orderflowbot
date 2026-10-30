"""Turn the kill switch off by itself once IBKR shows the paper account is empty (ADR-0017).

The operator chose this on 2026-10-30 ("Let Claude do it when it's safe") and turns it on with
`kill_switch.auto_release_when_flat` in config/risk_limits.yaml, by commit. The decision is
this fixed code, never an LLM, and it releases only when every one of these holds:

- the setting is true in risk_limits.yaml
- the IB settings say paper (the same guards as ib_paper_check; anything else exits 2)
- the kill switch was turned on by the node, the watchdog or a drill, not by the operator
  (`riskctl kill`, the dashboard) and not unreadable
- IBKR answers, and reports 0 open positions and 0 open orders

It never touches a daily or weekly halt: after a trip the day stays halted and trading
resumes the next trading day, as with `riskctl release`. Every decision is journaled to
data/journal/ops/auto_release.jsonl.

    python -m flowforge.ops.auto_release            # exit 0 = switch off, 1 = left on, 2 = not paper
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from flowforge.config.risk_limits import load_risk_limits
from flowforge.ops.ib_paper_check import CheckReport, NotPaperError, run_check, settings_from_env
from flowforge.risk.killswitch import KillSwitch

OPERATOR_KILLS = ("operator", "dashboard")  # a human pulled it: only a human releases it
JOURNAL = Path("data/journal/ops/auto_release.jsonl")
CHECK_SECONDS = 30.0


@dataclass(frozen=True)
class Decision:
    release: bool
    reason: str


def decide(enabled: bool, engaged: bool, by: str | None, report: CheckReport | None) -> Decision:
    """The whole rule, with no I/O. `by` is who turned the switch on (None = unreadable)."""
    if not engaged:
        return Decision(False, "kill switch already off")
    if not enabled:
        return Decision(False, "auto release is off in risk_limits.yaml")
    if by is None or by in OPERATOR_KILLS:
        return Decision(False, f"turned on by {by or 'unknown'}: only the operator releases it")
    if report is None or not report.ok:
        problems = "; ".join(report.problems) if report else "no check ran"
        return Decision(False, f"IBKR check failed ({problems}): can't confirm the account is empty")
    if report.open_positions or report.open_orders:
        return Decision(
            False,
            f"account not empty: {len(report.open_positions)} positions, "
            f"{len(report.open_orders)} open orders",
        )
    return Decision(True, f"turned on by {by}; IBKR shows 0 positions and 0 open orders")


def _journal(path: Path, decision: Decision, kill_reason: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts_ns": time.time_ns(),
        "event": "auto_release",
        "released": decision.release,
        "reason": decision.reason,
        "kill_switch": kill_reason,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def run(
    risk_limits_path: Path,
    kill_file: Path | None = None,
    journal: Path = JOURNAL,
    check=run_check,
    seconds: float = CHECK_SECONDS,
) -> Decision:
    """Check and, if the rule allows, release. Raises NotPaperError for non-paper settings."""
    limits = load_risk_limits(risk_limits_path).limits
    kill = KillSwitch(kill_file or limits.kill_switch.file_flag)
    enabled = limits.kill_switch.auto_release_when_flat
    engaged, kill_reason = kill.is_engaged(), kill.reason()
    report = None
    if engaged and enabled and kill.engaged_by() not in (None, *OPERATOR_KILLS):
        settings = settings_from_env()  # NotPaperError before anything connects
        report = check(settings, seconds, risk_limits_path)
    decision = decide(enabled, engaged, kill.engaged_by(), report)
    if decision.release:
        kill.release()
    if engaged:
        _journal(journal, decision, kill_reason)
    return decision


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    p.add_argument("--kill-file", type=Path, default=None, help="override the path in risk_limits.yaml")
    p.add_argument("--journal", type=Path, default=JOURNAL)
    a = p.parse_args(argv)
    try:
        decision = run(a.risk_limits, a.kill_file, a.journal)
    except NotPaperError as exc:
        print(f"auto_release: {exc}", file=sys.stderr)
        return 2
    print(("Kill switch OFF: " if decision.release else "Kill switch left as it is: ") + decision.reason)
    return 0 if decision.release or decision.reason == "kill switch already off" else 1


if __name__ == "__main__":
    sys.exit(main())
