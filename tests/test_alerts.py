"""Phone alerts: ntfy and Telegram requests, de-duplication, the journal follower (ops/alerts.py)."""

from __future__ import annotations

import json
import urllib.error

import pytest

from flowforge.execution.journal import Journal
from flowforge.ops import alerts
from flowforge.ops.alerts import (
    MAX_PER_MINUTE,
    Follower,
    Message,
    Ntfy,
    Sender,
    Telegram,
    channels_from_env,
    configured,
    message_for,
    relay_once,
)
from flowforge.risk.calendar import NS


class Capture:
    def __init__(self, status=200, error=None):
        self.requests, self.status, self.error = [], status, error

    def __call__(self, req):
        self.requests.append(req)
        if self.error:
            raise self.error
        return self.status


def test_ntfy_and_telegram_requests():
    cap = Capture()
    Ntfy("my topic", "https://ntfy.example/", "tk_1", cap).send(Message("critical", "T", "body"))
    req = cap.requests[0]
    assert req.full_url == "https://ntfy.example/my%20topic" and req.get_method() == "POST"
    assert (
        req.headers["Title"] == "T"
        and req.headers["Priority"] == "5"
        and req.headers["Authorization"] == "Bearer tk_1"
    )
    assert req.data == b"body"
    Telegram("123:abc", "42", cap).send(Message("info", "Hello", "x" * 5000))
    tg = cap.requests[1]
    body = json.loads(tg.data)
    assert tg.full_url == "https://api.telegram.org/bot123:abc/sendMessage"
    assert (
        body["chat_id"] == "42"
        and body["disable_notification"] is True
        and len(body["text"]) == alerts.MAX_CHARS
    )
    with pytest.raises(ValueError):
        Ntfy("t", "http://plain.example")


def test_channels_come_from_env_and_values_are_never_reported():
    env = {"NTFY_TOPIC": "t", "TELEGRAM_BOT_TOKEN": "secret", "TELEGRAM_CHAT_ID": "1"}
    assert [c.name for c in channels_from_env(env)] == ["ntfy", "telegram"]
    assert channels_from_env({"TELEGRAM_BOT_TOKEN": "only"}) == []
    assert configured(env) == {"ntfy": True, "telegram": True}
    assert configured({}) == {"ntfy": False, "telegram": False}


def test_sender_dedupes_caps_and_logs_failures_without_the_url(tmp_path):
    now = [0]
    ok, bad = (
        Capture(),
        Capture(error=urllib.error.URLError("https://api.telegram.org/botSECRET/sendMessage")),
    )
    ok.name, bad.name = "ntfy", "telegram"
    for ch in (ok, bad):
        ch.send = ch.__call__  # the Capture stands in for a channel here
    journal = Journal(tmp_path / "ops", keep=True)
    s = Sender([ok, bad], journal, clock=lambda: now[0])
    assert s.send(Message("critical", "A", "1")) == {"ntfy": "ok", "telegram": "URLError"}
    assert s.send(Message("critical", "A", "1")) == {}  # same text within a minute
    failure = journal.events("alert_send_failed")[0]
    assert failure["error"] == "URLError" and "SECRET" not in json.dumps(failure)
    for i in range(MAX_PER_MINUTE + 1):
        s.send(Message("info", "B", str(i)))
    assert s.suppressed == 2  # 1 + 21 messages in the minute, 20 allowed
    now[0] = 61 * NS
    s.send(Message("info", "C", "later"))
    assert ok.requests[-1].body.endswith("(2 more alerts were held back)")
    http = Capture(error=urllib.error.HTTPError("u", 403, "no", {}, None))
    http.name, http.send = "ntfy", http.__call__
    assert Sender([http], None, clock=lambda: 0).send(Message("info", "D", "x")) == {"ntfy": "HTTP 403"}
    status = Capture(status=500)
    status.name, status.send = "ntfy", status.__call__
    assert Sender([status], None, clock=lambda: 0).send(Message("info", "E", "x")) == {"ntfy": "HTTP 500"}


def test_which_journal_records_become_messages():
    m = message_for({"event": "alert", "level": "critical", "message": "boom", "symbol": "SPY"})
    assert m == Message("critical", "FlowForge SPY: critical", "boom")
    assert message_for({"event": "alert", "level": "info", "message": "fyi"}) is None
    assert message_for({"event": "kill_engaged", "reason": "r"}).title == "FlowForge kill switch ON"
    assert message_for({"event": "watchdog_alert", "message": "m"}).level == "critical"
    assert message_for({"event": "watchdog_flatten", "reason": "r"}).body == "r"
    assert message_for({"event": "watchdog_flatten_report", "flat": True}) is None
    nf = message_for({"event": "watchdog_flatten_report", "flat": False, "remaining_positions": {"SPY": 3}})
    assert "SPY" in nf.body
    assert message_for({"event": "order_rejected", "reason": "x"}).level == "warning"
    assert message_for({"event": "eod_report", "level": "info", "title": "T", "summary": "S"}) == Message(
        "info", "T", "S"
    )
    assert message_for({"event": "fill"}) is None


def test_follower_starts_at_the_end_then_reads_new_lines_once(tmp_path):
    root = tmp_path / "journal"
    old = Journal(root / "node")
    old.write(1, "alert", level="critical", message="old news")
    old.close()
    cap = Capture()
    cap.name, cap.send = "ntfy", cap.__call__
    sender = Sender([cap], None, clock=lambda: 0)
    f = Follower(root, tmp_path / "offsets.json")
    assert relay_once(f, sender) == 0  # first run: nothing old is re-sent
    path = next((root / "node").glob("*.jsonl"))
    with path.open("a") as fh:
        fh.write(json.dumps({"ts_ns": 2, "event": "kill_engaged", "reason": "now"}) + "\n")
        fh.write("not json\n")
        fh.write('{"half": ')  # still being written
    f2 = Follower(root, tmp_path / "offsets.json")  # a restart keeps the place
    assert relay_once(f2, sender) == 1 and cap.requests[0].body == "now"
    assert relay_once(f2, sender) == 0
    path.write_text("")  # replaced (shorter): read from the start next time
    assert f2.poll() == []
    (root / "ops").mkdir()
    (root / "ops" / "2026-10-27.jsonl").write_text(json.dumps({"ts_ns": 3, "event": "fill"}) + "\n")
    assert f2.poll() == [{"ts_ns": 3, "event": "fill"}]
    assert Follower(tmp_path / "nothing", tmp_path / "o2.json").poll() == []


def test_cli_without_channels_and_test_send(monkeypatch, tmp_path, capsys):
    for k in ("NTFY_TOPIC", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    assert alerts.main(["test", "--journals", str(tmp_path)]) == 1
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setattr(alerts, "urllib_transport", lambda req: 200)
    assert alerts.main(["test", "--journals", str(tmp_path)]) == 0
    assert '"ntfy": "ok"' in capsys.readouterr().out
