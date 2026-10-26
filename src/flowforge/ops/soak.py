"""Restart soak test (Phase 7): stop and restart the paper node many times, and check that
every restart comes back clean.

Each cycle starts the node (`python -m flowforge.ops.paper_node ...`), lets it run, then
stops it: a normal stop (SIGTERM) and a hard kill (SIGKILL, like a crash) take turns. It
only stops the node while the heartbeat shows it flat: a restart while holding shares is
the `restart` drill's job (paper_drills.py), and by design it turns the kill switch on.
After each start it checks: the heartbeat is fresh again within 90 s, the node reconciled
with IBKR to the same positions and no open orders, and nothing went wrong (no position
mismatch, no error records, no kill switch).

Usage:
    python -m flowforge.ops.soak --cycles 8 -- --setups H4      # args after -- go to paper_node
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from flowforge.execution.heartbeat import read_heartbeat, wall_clock_ns

START_WITHIN_S = 90
FLAT_WAIT_S = 600
SETTLE_S = 5


@dataclass
class Cycle:
    n: int
    stop: str  # term | kill | skipped (not flat)
    up_s: float
    started_in_s: float | None = None
    positions_before: dict = field(default_factory=dict)
    positions_after: dict = field(default_factory=dict)
    open_orders_after: int | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class SoakPaths:
    heartbeat: Path
    journal: Path  # the node's journal folder
    kill_file: Path


def _launch(cmd: Sequence[str]) -> subprocess.Popen:
    return subprocess.Popen(list(cmd))  # noqa: S603 - our own module and arguments


def _records_since(journal: Path, since_ns: int) -> list[dict]:
    from flowforge.ops.ledger import read_journal

    root = journal.parent
    return [r for r in read_journal(root, None, [journal.name]).records if int(r["ts_ns"]) >= since_ns]


def wait_fresh(
    heartbeat: Path, since_ns: int, timeout_s: float, sleep: Callable[[float], None]
) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        beat = read_heartbeat(heartbeat)
        if beat is not None and beat.ts_ns >= since_ns:
            return time.monotonic() - t0
        sleep(1)
    return None


def wait_flat(heartbeat: Path, timeout_s: float, sleep: Callable[[float], None]) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        beat = read_heartbeat(heartbeat)
        if beat is not None and not beat.positions and beat.open_orders == 0:
            return True
        sleep(5)
    return False


def soak(
    cmd: Sequence[str],
    paths: SoakPaths,
    cycles: int,
    up_range: tuple[float, float] = (120, 600),
    seed: int = 7,
    launch: Callable[[Sequence[str]], Any] = _launch,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Cycle]:
    from flowforge.risk.killswitch import KillSwitch

    rng = random.Random(seed)  # noqa: S311 - reproducible timing, not security
    out: list[Cycle] = []
    proc = None
    before: dict = {}
    for n in range(1, cycles + 1):
        stop = "term" if n % 2 else "kill"
        cyc = Cycle(n, stop, round(rng.uniform(*up_range)), positions_before=before)
        launched = wall_clock_ns()
        proc = launch(cmd)
        cyc.started_in_s = wait_fresh(paths.heartbeat, launched, START_WITHIN_S, sleep)
        if cyc.started_in_s is None:
            cyc.problems.append(f"no fresh heartbeat within {START_WITHIN_S} s of the start")
        else:
            sleep(SETTLE_S)  # reconciliation and the first ticks
            beat = read_heartbeat(paths.heartbeat)
            cyc.positions_after = dict(beat.positions) if beat else {}
            cyc.open_orders_after = beat.open_orders if beat else None
            if cyc.positions_after != before:
                cyc.problems.append(
                    f"positions changed across the restart: {before} -> {cyc.positions_after}"
                )
            if before == {} and cyc.open_orders_after:
                cyc.problems.append(f"{cyc.open_orders_after} open orders after a flat restart")
        sleep(cyc.up_s)
        for r in _records_since(paths.journal, launched):
            if r["event"] == "error" or (r["event"] == "alert" and "mismatch" in r.get("message", "")):
                cyc.problems.append(f"{r['event']}: {r.get('message') or r.get('error')}")
        if KillSwitch(paths.kill_file).is_engaged():
            cyc.problems.append("kill switch is on")
        if not wait_flat(paths.heartbeat, FLAT_WAIT_S, sleep):
            cyc.stop = "skipped"
            cyc.problems.append(f"not flat within {FLAT_WAIT_S} s: soak stopped here, node left running")
            out.append(cyc)
            return out
        beat = read_heartbeat(paths.heartbeat)
        before = dict(beat.positions) if beat else {}
        proc.send_signal(signal.SIGTERM if stop == "term" else signal.SIGKILL)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            proc.kill()
            cyc.problems.append("node didn't stop within 60 s of SIGTERM")
        out.append(cyc)
        if not cyc.ok and any("kill switch" in p for p in cyc.problems):
            return out
        sleep(SETTLE_S)
    return out


def markdown(cycles: list[Cycle]) -> str:
    good = sum(c.ok for c in cycles)
    lines = [
        "# Paper restart soak",
        "",
        f"{good} of {len(cycles)} restarts clean.",
        "",
        "| # | Stop | Up (s) | Back in (s) | Positions after | Open orders | Problems |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in cycles:
        back = "" if c.started_in_s is None else f"{c.started_in_s:.0f}"
        orders = "" if c.open_orders_after is None else c.open_orders_after
        problems = "; ".join(c.problems) or "none"
        lines.append(
            f"| {c.n} | {c.stop} | {c.up_s:.0f} | {back} | {json.dumps(c.positions_after)}"
            f" | {orders} | {problems} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    import os

    from flowforge.config.risk_limits import load_risk_limits

    argv = list(sys.argv[1:] if argv is None else argv)
    node_args = argv[argv.index("--") + 1 :] if "--" in argv else []
    own = argv[: argv.index("--")] if "--" in argv else argv
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cycles", type=int, default=8)
    p.add_argument("--min-up", type=float, default=120)
    p.add_argument("--max-up", type=float, default=600)
    p.add_argument("--out", type=Path, default=Path("reports/paper"))
    a = p.parse_args(own)
    run_dir = Path(os.environ.get("FLOWFORGE_RUN_DIR", "/var/run/flowforge"))
    limits = load_risk_limits(Path("config/risk_limits.yaml")).limits
    paths = SoakPaths(
        run_dir / "heartbeat.json", Path("data/journal/node"), Path(limits.kill_switch.file_flag)
    )
    cmd = [sys.executable, "-m", "flowforge.ops.paper_node", *node_args]
    cycles = soak(cmd, paths, a.cycles, (a.min_up, a.max_up))
    a.out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    (a.out / f"soak-{stamp}.json").write_text(json.dumps([asdict(c) for c in cycles], indent=2))
    (a.out / f"soak-{stamp}.md").write_text(markdown(cycles), encoding="utf-8")
    print(markdown(cycles))
    return 0 if all(c.ok for c in cycles) and len(cycles) == a.cycles else 1


if __name__ == "__main__":
    sys.exit(main())
