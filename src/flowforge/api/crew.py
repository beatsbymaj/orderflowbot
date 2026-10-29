"""The crew: each working part of FlowForge as one agent the dashboard can draw.

Every state comes from the same files the Health page reads (heartbeat, tape snapshot,
watchdog file, kill switch, today's journal). Nothing is guessed: an agent whose process
isn't running is "off", and only a part that is really doing its job right now is
"working". The dashboard animates "working" agents and nothing else.

States:
    working  doing its job now (market open, process fresh)
    ready    running, nothing to do (market closed, no channel traffic)
    blocked  stopped on purpose by the kill switch or a loss halt
    off      not running, or switched off in config
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# The order the work flows in, left to right on the screen; the last three support it.
CHAIN = ("scout", "tape", "strategist", "jev", "guard", "executor")
SUPPORT = ("watchdog", "herald", "archivist")

JOURNAL_ACTIVE_S = 120  # the archivist counts as writing if the last record is this recent


@dataclass
class Agent:
    id: str
    name: str
    role: str
    state: str
    detail: str


@dataclass
class CrewInputs:
    live: bool  # market open (or demo)
    node_age_s: float | None  # None: no heartbeat file
    node_fresh_s: float
    tape_age_s: float | None
    tape_stale_s: float
    watchdog_age_s: float | None
    gateway_up: bool
    kill_on: bool
    halt: str | None
    jev_enabled: bool
    jev_shadow: bool
    alert_channels: list[str]
    counts: dict[str, int]  # today's journal events by name
    last_record_age_s: float | None


def _n(counts: dict[str, int], *events: str) -> int:
    return sum(counts.get(e, 0) for e in events)


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def build_crew(i: CrewInputs) -> list[Agent]:
    node_up = i.node_age_s is not None and i.node_age_s <= i.node_fresh_s
    stopped = i.kill_on or bool(i.halt)
    node_state = "off" if not node_up else ("working" if i.live else "ready")
    why_off = "trading node not running" if i.node_age_s is None else f"no heartbeat for {i.node_age_s:.0f} s"

    tape_fresh = i.tape_age_s is not None and i.tape_age_s <= i.tape_stale_s
    if not node_up:
        scout = ("off", why_off)
    elif i.live and tape_fresh:
        scout = ("working", f"tape updated {i.tape_age_s:.0f} s ago")
    elif i.live:
        scout = ("ready", "waiting for the first trades")
    else:
        scout = ("ready", "market closed")

    signals = _n(i.counts, "signal")
    skipped = _n(i.counts, "signal_skipped")
    jev = _n(i.counts, "jev_decision")
    decisions = _n(i.counts, "risk_decision")
    orders = _n(i.counts, "order_submit")
    fills = _n(i.counts, "fill")
    alerts_sent = _n(i.counts, "alert")

    if not i.jev_enabled:
        oracle = ("off", "switched off until the tests say it helps")
    elif not node_up:
        oracle = ("off", why_off)
    else:
        mode = "shadow: asks, never vetoes" if i.jev_shadow else "can veto a signal"
        oracle = (node_state, f"{_plural(jev, 'answer')} today, {mode}")

    if stopped:  # shown with the node down too: it is what keeps the next start from trading
        guardian = ("blocked", "kill switch on: no new trades" if i.kill_on else f"halted: {i.halt}")
    elif not node_up:
        guardian = ("off", why_off)
    else:
        guardian = (node_state, f"{_plural(decisions, 'decision')} today")

    if not i.gateway_up:
        trader = ("off", "IB Gateway not connected")
    elif not node_up:
        trader = ("off", why_off)
    elif stopped:
        trader = ("blocked", f"{_plural(orders, 'order')} today, none new while stopped")
    else:
        trader = (node_state, f"{_plural(orders, 'order')}, {_plural(fills, 'fill')} today")

    if i.watchdog_age_s is None:
        sentinel = ("off", "watchdog not running")
    elif i.watchdog_age_s > i.node_fresh_s:
        sentinel = ("off", f"no check for {i.watchdog_age_s:.0f} s")
    else:
        # The watchdog is always on duty while it runs: it is what acts when the node goes quiet.
        sentinel = ("working", f"checked {i.watchdog_age_s:.0f} s ago")

    if not i.alert_channels:
        herald = ("off", "no phone channel set in .env")
    else:
        herald = ("ready", f"{', '.join(i.alert_channels)}: {_plural(alerts_sent, 'alert')} today")

    if i.last_record_age_s is None:
        scribe = ("ready" if node_up else "off", "nothing written today")
    elif i.last_record_age_s <= JOURNAL_ACTIVE_S and node_up:
        scribe = ("working", f"last entry {i.last_record_age_s:.0f} s ago")
    else:
        scribe = ("ready" if node_up else "off", f"last entry {i.last_record_age_s / 60:.0f} min ago")

    # Names and ids as the operator first saw them on the dashboard (2026-10-29).
    rows = {
        "scout": ("Scout", "Alpaca market data", scout),
        "tape": (
            "Tape",
            "Order-flow features",
            ("off", why_off) if not node_up else (node_state, "delta, imbalance, volume profile"),
        ),
        "strategist": (
            "Strategist",
            "Setups H1–H5",
            ("off", why_off)
            if not node_up
            else (node_state, f"{_plural(signals, 'signal')}, {skipped:,} skipped today"),
        ),
        "jev": ("Jev", "AI trade gate", oracle),
        "guard": ("Guard", "RiskGuard limits", guardian),
        "executor": ("Executor", "IBKR paper orders", trader),
        "watchdog": ("Watchdog", "Emergency seller", sentinel),
        "herald": ("Herald", "Phone alerts", herald),
        "archivist": ("Archivist", "Journal and reports", scribe),
    }
    return [Agent(k, n, r, s, d) for k, (n, r, (s, d)) in rows.items()]


def crew_summary(agents: list[Agent]) -> dict:
    working = sum(1 for a in agents if a.state == "working")
    blocked = [a.name for a in agents if a.state == "blocked"]
    off = sum(1 for a in agents if a.state == "off")
    if blocked:
        line = f"{' and '.join(blocked)} stopped by the kill switch or a halt"
    elif working:
        line = f"{working} of {len(agents)} agents working"
    elif off == len(agents):
        line = "Every agent is off: the bot isn't running"
    else:
        line = "Standing by: nothing to do right now"
    return {
        "agents": [asdict(a) for a in agents],
        "working": working,
        "line": line,
        "chain": list(CHAIN),
        "support": list(SUPPORT),
    }
