"""Phone alerts: ntfy and Telegram, fed from the journals.

    python -m flowforge.ops.alerts test     # send one test message to every channel
    python -m flowforge.ops.alerts run      # follow the journals and send alerts (service)

Channels are set in .env and are both optional (docs/research/15-dashboard-alerts.md):
- ntfy: NTFY_TOPIC (required for ntfy), NTFY_URL (default https://ntfy.sh), NTFY_TOKEN
  (only for a protected topic or a self-hosted server).
- Telegram: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.

What is sent: node and watchdog alerts, the kill switch, watchdog flattens, order rejects,
and the pre-market and end-of-day summaries. Messages carry symbols, reasons and dollar
amounts, never keys, account numbers or tokens. A failed send is written to the ops
journal by HTTP status or error type only, because a Telegram URL contains the bot token.

Alerting never sits in the order path: if the phone can't be reached, trading and the
risk limits carry on exactly the same.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from flowforge.execution.heartbeat import wall_clock_ns
from flowforge.execution.journal import Journal
from flowforge.risk.calendar import NS

MAX_CHARS = 3500  # Telegram's limit is 4096 characters, ntfy's 4096 bytes; stay well under
SEND_TIMEOUT_S = 10
DEDUPE_S = 60  # the same text again within a minute is dropped
MAX_PER_MINUTE = 20  # then one "N more" line
PRIORITY = {"critical": "5", "warning": "4", "info": "3"}

Transport = Callable[[urllib.request.Request], int]  # returns the HTTP status


def urllib_transport(req: urllib.request.Request) -> int:
    with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:  # noqa: S310 (https URLs only)
        return resp.status


@dataclass(frozen=True)
class Message:
    level: str  # critical | warning | info
    title: str
    body: str

    def text(self) -> str:
        return f"{self.title}\n{self.body}"[:MAX_CHARS]


class Ntfy:
    name = "ntfy"

    def __init__(self, topic: str, url: str = "https://ntfy.sh", token: str = "", transport=None) -> None:
        if not url.startswith("https://"):
            raise ValueError("NTFY_URL must start with https://")
        self.endpoint = f"{url.rstrip('/')}/{urllib.parse.quote(topic, safe='')}"
        self.token = token
        self.transport = transport or urllib_transport

    def send(self, msg: Message) -> int:
        headers = {"Title": msg.title[:200], "Priority": PRIORITY.get(msg.level, "3"), "Tags": msg.level}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = msg.body[:MAX_CHARS].encode("utf-8")
        return self.transport(
            urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")  # noqa: S310
        )


class Telegram:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, transport=None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.transport = transport or urllib_transport

    def send(self, msg: Message) -> int:
        body = json.dumps(
            {"chat_id": self.chat_id, "text": msg.text(), "disable_notification": msg.level == "info"}
        ).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310 (fixed https URL)
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        return self.transport(req)


def channels_from_env(env: Mapping[str, str] | None = None, transport=None) -> list:
    env = os.environ if env is None else env
    out: list = []
    if env.get("NTFY_TOPIC"):
        out.append(
            Ntfy(
                env["NTFY_TOPIC"],
                env.get("NTFY_URL") or "https://ntfy.sh",
                env.get("NTFY_TOKEN", ""),
                transport,
            )
        )
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        out.append(Telegram(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"], transport))
    return out


def configured(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Which channels are set up, for the dashboard. Never the values."""
    env = os.environ if env is None else env
    return {
        "ntfy": bool(env.get("NTFY_TOPIC")),
        "telegram": bool(env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")),
    }


def _error_label(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__  # never str(exc): it can contain the request URL


class Sender:
    """Sends to every channel with de-duplication and a per-minute cap."""

    def __init__(self, channels: list, journal: Journal | None = None, clock=wall_clock_ns) -> None:
        self.channels = channels
        self.journal = journal
        self.clock = clock
        self.recent: dict[str, int] = {}
        self.window: deque[int] = deque()
        self.suppressed = 0

    def send(self, msg: Message) -> dict[str, str]:
        now = self.clock()
        key = msg.text()
        last = self.recent.get(key)
        if last is not None and now - last < DEDUPE_S * NS:
            return {}
        self.recent = {k: t for k, t in self.recent.items() if now - t < DEDUPE_S * NS}
        self.recent[key] = now
        while self.window and now - self.window[0] >= 60 * NS:
            self.window.popleft()
        if len(self.window) >= MAX_PER_MINUTE:
            self.suppressed += 1
            return {}
        if self.suppressed:
            n = self.suppressed
            held = f"{n} more alert{'s were' if n > 1 else ' was'} held back"
            msg = Message(msg.level, msg.title, f"{msg.body}\n({held})")
            self.suppressed = 0
        self.window.append(now)
        results: dict[str, str] = {}
        for ch in self.channels:
            try:
                status = ch.send(msg)
                results[ch.name] = "ok" if 200 <= status < 300 else f"HTTP {status}"
            except Exception as exc:  # noqa: BLE001 (a phone outage must never stop the relay)
                results[ch.name] = _error_label(exc)
            if results[ch.name] != "ok" and self.journal is not None:
                self.journal.write(now, "alert_send_failed", channel=ch.name, error=results[ch.name])
        return results


# -- journal records -> messages ------------------------------------------------------------
def message_for(rec: dict) -> Message | None:
    ev = rec.get("event")
    sym = rec.get("symbol") or ""
    where = f" {sym}" if sym else ""
    if ev == "alert" and rec.get("level") in ("critical", "warning"):
        return Message(rec["level"], f"FlowForge{where}: {rec['level']}", str(rec.get("message", "")))
    if ev == "watchdog_alert":
        return Message("critical", "FlowForge watchdog", str(rec.get("message", "")))
    if ev == "kill_engaged":
        return Message("critical", "FlowForge kill switch ON", str(rec.get("reason", "")))
    if ev == "watchdog_flatten":
        return Message("critical", "FlowForge watchdog is flattening", str(rec.get("reason", "")))
    if ev == "watchdog_flatten_report" and not rec.get("flat"):
        left = rec.get("remaining_positions") or {}
        problems = "; ".join(rec.get("problems") or [])
        return Message(
            "critical", "FlowForge: NOT flat after watchdog", f"still held {left}. {problems}".strip()
        )
    if ev in ("order_rejected", "order_cancel_rejected", "order_modify_rejected"):
        return Message(
            "warning", f"FlowForge order problem ({ev.replace('_', ' ')})", str(rec.get("reason", ""))
        )
    if ev in ("premarket_check", "eod_report"):
        return Message(rec.get("level", "info"), str(rec.get("title", ev)), str(rec.get("summary", "")))
    return None


class Follower:
    """Reads new lines from every journal file; remembers where it stopped across restarts.

    On the very first run it starts at the end of today's files, so an old day's alerts are
    never re-sent."""

    def __init__(self, root: Path, offsets_path: Path) -> None:
        self.root = root
        self.offsets_path = offsets_path
        self.offsets: dict[str, int] = {}
        self.first_run = True
        try:
            self.offsets = {str(k): int(v) for k, v in json.loads(offsets_path.read_text()).items()}
            self.first_run = False
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def poll(self) -> list[dict]:
        out: list[dict] = []
        if not self.root.exists():
            return out
        for path in sorted(self.root.rglob("*.jsonl")):
            key = str(path.relative_to(self.root))
            size = path.stat().st_size
            if key not in self.offsets:
                self.offsets[key] = size if self.first_run else 0
            start = self.offsets[key] if self.offsets[key] <= size else 0  # file replaced: start over
            if start == size:
                continue
            with path.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read(size - start)
            end = chunk.rfind(b"\n") + 1  # leave a half-written last line for next time
            for line in chunk[:end].splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
            self.offsets[key] = start + end
        self.first_run = False
        self.save()
        return out

    def save(self) -> None:
        self.offsets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.offsets_path.with_name(self.offsets_path.name + ".tmp")
        tmp.write_text(json.dumps(self.offsets))
        os.replace(tmp, self.offsets_path)


def relay_once(follower: Follower, sender: Sender) -> int:
    sent = 0
    for rec in follower.poll():
        msg = message_for(rec)
        if msg is not None and sender.send(msg):
            sent += 1
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowForge phone alerts (ntfy, Telegram)")
    parser.add_argument("command", choices=["test", "run"])
    parser.add_argument("--journals", type=Path, default=Path("data/journal"))
    parser.add_argument("--offsets", type=Path, default=Path("data/state/alert_offsets.json"))
    parser.add_argument("--every", type=float, default=2.0, help="seconds between journal checks")
    args = parser.parse_args(argv)

    channels = channels_from_env()
    if not channels:
        print("No alert channel set. Add NTFY_TOPIC or TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to .env.")
        return 1
    journal = Journal(args.journals / "ops")
    sender = Sender(channels, journal)
    if args.command == "test":
        results = sender.send(Message("info", "FlowForge test", "If you can read this, alerts work."))
        print(json.dumps(results))
        return 0 if results and all(v == "ok" for v in results.values()) else 1
    follower = Follower(args.journals, args.offsets)
    print(f"Following {args.journals} for alerts to: {', '.join(c.name for c in channels)}", flush=True)
    while True:
        relay_once(follower, sender)
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
