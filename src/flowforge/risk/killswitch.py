"""Kill switch: a file flag shared by the node, the watchdog and the operator's tools.

The path comes from config/risk_limits.yaml (`kill_switch.file_flag`). The switch is on
while the file exists. Anyone can turn it on (CLI, dashboard, watchdog, the node itself);
only the operator turns it off (`python -m flowforge.ops.riskctl release`), or, when the
operator enables it in risk_limits.yaml, ops.auto_release once IBKR shows the paper account
empty and the switch wasn't pulled by the operator (ADR-0017).

If the flag's location can't be checked (permissions, broken mount), the switch reads as
on: not being able to see the switch is treated the same as the switch being pulled.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


class KillSwitch:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def is_engaged(self) -> bool:
        try:
            os.stat(self.path)
        except FileNotFoundError:
            return False
        except OSError:
            return True  # can't tell: fail closed
        return True

    def reason(self) -> str | None:
        if not self.is_engaged():
            return None
        try:
            data = json.loads(self.path.read_text())
            who, when = data.get("by", "unknown"), data.get("at")
            return f"{data.get('reason', 'no reason given')} (by {who} at {when})"
        except (OSError, ValueError, AttributeError):
            return "kill switch file present"

    def engaged_by(self) -> str | None:
        """Who turned it on ("operator", "node", "watchdog", ...); None if off or unreadable."""
        if not self.is_engaged():
            return None
        try:
            by = json.loads(self.path.read_text()).get("by")
        except (OSError, ValueError, AttributeError):
            return None
        return by if isinstance(by, str) and by else None

    def engage(self, reason: str, by: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"reason": reason, "by": by, "at": datetime.now(UTC).isoformat(timespec="seconds")}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(body))
        os.replace(tmp, self.path)

    def release(self) -> bool:
        """Operator only (or ops.auto_release under its rule). Returns whether the switch was on."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True
