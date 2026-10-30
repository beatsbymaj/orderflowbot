"""The Lab screen: research runs (walk-forward, Jev gate test) as they happen, read only.

A run's console output is saved to `data/walkforward*.log` (docs/runbooks/walkforward.md) and the
Jev answers to `reports/<run>/jev_answers.jsonl` (gate.evaluate.CachedGate). This only reads those
files; it starts, stops and changes nothing.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

LOG_LINES = 80
ANSWERS_SHOWN = 14
RUNNING_S = 1800  # no "Report:" line and a write this recent: still running (one H2 step can take 10+ min)
# a second lock on top of the run never printing one (CLAUDE.md rule 5)
SECRET = re.compile(r"(api[_-]?key|token|secret|password|bearer)\s*[=:]?\s*\S+", re.IGNORECASE)
STEP = re.compile(r"^(?P<setup>\w+) (?P<config>\S+): (?P<signals>\d+) signals, (?P<trades>\d+) trades$")


def _tail(path: Path, n: int) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [SECRET.sub(r"\1 [hidden]", line) for line in lines if line.strip()][-n:]


def _age(path: Path, now_s: float) -> float | None:
    try:
        return max(0.0, now_s - path.stat().st_mtime)
    except OSError:
        return None


def latest_run(root: Path, now_s: float) -> dict | None:
    """The newest walk-forward log, its last lines, and whether it is still going."""
    logs = sorted((root / "data").glob("walkforward*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return None
    log = logs[0]
    lines = _tail(log, 10_000)
    report = next((ln.split(":", 1)[1].strip() for ln in reversed(lines) if ln.startswith("Report:")), None)
    age = _age(log, now_s)
    state = "done" if report else ("running" if age is not None and age < RUNNING_S else "stopped")
    steps = [m.groupdict() for m in map(STEP.match, lines) if m]
    return {
        "log": f"data/{log.name}",
        "age_s": age,
        "state": state,
        "report": report,
        "steps": len(steps),
        "last_step": steps[-1] if steps else None,
        "lines": lines[-LOG_LINES:],
    }


def jev_runs(root: Path, now_s: float) -> list[dict]:
    """Each reports/<run>/jev_answers.jsonl: how many answers, how they went, the latest few."""
    out = []
    for path in sorted((root / "reports").glob("*/jev_answers.jsonl")):
        rows, bad = [], 0
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                bad += 1  # a half-written last line while the run appends
                continue
            if isinstance(row, dict):
                rows.append(row)
        answered = [r for r in rows if isinstance(r.get("p_yes"), (int, float))]
        lat = [r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), (int, float))]
        out.append(
            {
                "run": path.parent.name,
                "age_s": _age(path, now_s),
                "answers": len(rows),
                "answered": len(answered),
                "reasons": dict(Counter(str(r.get("reason")) for r in rows)),
                "mean_latency_ms": round(sum(lat) / len(lat), 1) if lat else None,
                "mean_p_yes": round(sum(r["p_yes"] for r in answered) / len(answered), 3)
                if answered
                else None,
                "bad_lines": bad,
                "recent": [
                    {k: r.get(k) for k in ("id", "p_yes", "reason", "latency_ms")}
                    for r in rows[-ANSWERS_SHOWN:]
                ][::-1],
            }
        )
    return sorted(out, key=lambda j: j["age_s"] if j["age_s"] is not None else 1e18)


def finished_reports(root: Path, now_s: float) -> list[dict]:
    return sorted(
        (
            {"run": p.parent.name, "age_s": _age(p, now_s)}
            for p in (root / "reports").glob("*/walkforward.md")
        ),
        key=lambda r: r["age_s"] if r["age_s"] is not None else 1e18,
    )


def lab_view(root: Path, now_s: float, threshold: float | None) -> dict:
    return {
        "run": latest_run(root, now_s),
        "jev": jev_runs(root, now_s),
        "reports": finished_reports(root, now_s),
        "threshold": threshold,
    }
