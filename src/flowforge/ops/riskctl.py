"""Operator controls for the kill switch and halts.

    python -m flowforge.ops.riskctl status
    python -m flowforge.ops.riskctl kill --reason "stepping away"
    python -m flowforge.ops.riskctl release
    python -m flowforge.ops.riskctl reenable      # clears a weekly-drawdown halt

`kill` can be run by anyone at any time. `release` and `reenable` are for the operator
only and ask for the word "yes" first (or --yes). Releasing the switch does not restart
trading the same day: the day stays halted. Nothing here changes risk limits.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flowforge.config.risk_limits import load_risk_limits
from flowforge.execution.heartbeat import read_heartbeat, wall_clock_ns
from flowforge.risk.killswitch import KillSwitch
from flowforge.risk.state import RiskState

DEFAULT_STATE = Path("data/state/risk_state.json")
DEFAULT_HEARTBEAT = Path("/var/run/flowforge/heartbeat.json")


def _confirm(prompt: str, yes: bool) -> bool:
    if yes:
        return True
    try:
        return input(f"{prompt} Type yes to continue: ").strip().lower() == "yes"
    except EOFError:
        return False


def status(kill: KillSwitch, state: RiskState, heartbeat: Path) -> dict:
    beat = read_heartbeat(heartbeat)
    return {
        "kill_switch": kill.reason() if kill.is_engaged() else "off",
        "halt": state.halt_reason() or "none",
        "trading_day": state.day.isoformat() if state.day else None,
        "entries_today": state.trades_today(),
        "node": None
        if beat is None
        else {
            "last_seen_s_ago": round(beat.age_s(wall_clock_ns()), 1),
            "positions": beat.positions,
            "open_orders": beat.open_orders,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowForge kill switch and halt controls")
    parser.add_argument("command", choices=["status", "kill", "release", "reenable"])
    parser.add_argument("--reason", default="operator")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation question")
    parser.add_argument("--risk-limits", type=Path, default=Path("config/risk_limits.yaml"))
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--heartbeat", type=Path, default=DEFAULT_HEARTBEAT)
    parser.add_argument("--kill-file", type=Path, default=None, help="override the path in risk_limits.yaml")
    args = parser.parse_args(argv)

    limits = load_risk_limits(args.risk_limits).limits
    kill = KillSwitch(args.kill_file or limits.kill_switch.file_flag)
    state = RiskState(limits, args.state)

    if args.command == "kill":
        kill.engage(args.reason, "operator")
        print("Kill switch ON. The node stops entries and flattens; the watchdog backs it up.")
    elif args.command == "release":
        if not _confirm("This lets FlowForge trade again from the next trading day.", args.yes):
            print("Cancelled.")
            return 1
        was = kill.release()
        print("Kill switch OFF." if was else "Kill switch was already off.")
    elif args.command == "reenable":
        if not _confirm("This clears the weekly drawdown halt.", args.yes):
            print("Cancelled.")
            return 1
        print("Weekly halt cleared." if state.reenable() else "There was no weekly halt.")
    print(json.dumps(status(kill, state, args.heartbeat), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
