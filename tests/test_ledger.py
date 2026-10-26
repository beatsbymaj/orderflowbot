"""Journals read back into trades, signals and a day's numbers (ops/ledger.py)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from flowforge.api.demo import write_demo
from flowforge.execution.journal import Journal
from flowforge.ops.ledger import (
    build_signals,
    build_trades,
    day_numbers,
    exit_kind,
    journal_days,
    read_journal,
    scrub,
)
from flowforge.risk.calendar import ET, et_date

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=ET)


def demo(tmp_path):
    day = write_demo(tmp_path, REPO_CONFIG, NOW)
    return day, read_journal(tmp_path / "data" / "journal", day.isoformat())


def test_demo_day_rebuilds_into_the_trades_it_was_written_from(tmp_path):
    day, read = demo(tmp_path)
    assert day.isoformat() == "2026-10-27" and read.bad_lines == 0 and read.files == 2
    node = [r for r in read.records if r["source"] == "node"]
    trades, orphans = build_trades(node)
    assert orphans == []
    spy, qqq, msft = trades
    assert (spy.symbol, spy.status, spy.exit_reason, spy.net, spy.r) == (
        "SPY",
        "closed",
        "target",
        1.55,
        0.92,
    )
    assert (qqq.exit_reason, qqq.net, qqq.r) == ("stop", -5.54, -1.61)
    assert (msft.status, msft.shares, msft.commission, msft.net) == ("open", 2, 1.0, None)
    n = day_numbers(node, trades, orphans)
    assert (n["trades"], n["wins"], n["losses"], n["net"], n["commission"]) == (2, 1, 1, -3.99, 5.0)
    assert (n["signals"], n["risk_refusals"], n["open_trades"], n["losing_streak"]) == (6, 2, 1, 1)
    assert n["equity_start"] == 10_000.0 and n["equity_last"] == round(10_000 - 3.99 - 1.0 + 0.42 * 2, 2)
    sig = {s["symbol"] + s["setup"]: s["outcome"] for s in build_signals(node, trades)}
    assert sig == {
        "NVDAH4": "refused by risk",
        "MSFTH4": "in trade",
        "QQQH4": "traded",
        "AAPLH4": "refused by risk",
        "NVDAH1": "not enabled",
        "SPYH4": "traded",
    }
    assert journal_days(tmp_path / "data" / "journal") == ["2026-10-27"]


def test_unowned_fills_partial_exits_skips_jev_and_secrets(tmp_path):
    j = Journal(tmp_path / "node")
    t = 1_793_000_000_000_000_000
    j.write(t, "signal", intent="H1:SPY:1", setup="H1", symbol="SPY", enabled=True)
    j.write(t + 1, "signal_skipped", intent="H1:SPY:1", why="no quote")
    j.write(t + 2, "signal", intent="H2:SPY:2", setup="H2", symbol="SPY", enabled=True)
    j.write(t + 3, "jev_decision", intent="H2:SPY:2", allow=False, reason="below threshold", p_yes=0.3)
    j.write(t + 4, "signal", intent="H3:SPY:3", setup="H3", symbol="SPY", enabled=True)
    j.write(t + 5, "jev_decision", intent="H3:SPY:3", allow=True, vetoed_in_shadow=False, p_yes=0.9)
    j.write(t + 6, "risk_decision", intent="H3:SPY:3", allowed=True, plan={"stop_price": 9.0, "quantity": 4})
    j.write(t + 7, "order_submit", intent="H3:SPY:3", symbol="SPY", order="E", role="entry", quantity=4)
    j.write(t + 8, "jev_decision", intent="unknown", allow=True)
    j.write(t + 9, "fill", order="E", quantity=4, price=10.0, commission=1.0)
    j.write(t + 10, "exit_submit", intent="H3:SPY:3", order="X", reason="kill switch")
    j.write(t + 11, "fill", order="X", quantity=1, price=11.0, commission=0.5)
    j.write(t + 12, "fill", order="NOBODY", quantity=1, price=1.0, api_key="abc")
    j.write(t + 13, "signal", intent="H4:SPY:4", setup="H4", symbol="SPY", enabled=True)
    j.write(t + 14, "risk_decision", intent="H4:SPY:4", allowed=True, plan={})
    j.write(t + 15, "order_submit", intent="H4:SPY:4", symbol="SPY", order="N", role="entry", quantity=1)
    j.close()
    with (tmp_path / "node" / f"{et_date(t)}.jsonl").open("a") as fh:
        fh.write('{"torn": \n\n')
    read = read_journal(tmp_path)
    assert read.bad_lines == 1
    trades, orphans = build_trades(read.records)
    assert [o["order"] for o in orphans] == ["NOBODY"] and orphans[0]["api_key"] == "[redacted]"
    t3 = trades[0]
    assert (t3.status, t3.sold, t3.exit_price, t3.exit_reason, t3.net) == ("open", 1, 11.0, "kill", None)
    assert trades[1].status == "not filled"
    rows = {r["setup"]: r for r in build_signals(read.records, trades)}
    assert rows["H1"]["outcome"] == "skipped" and rows["H2"]["outcome"] == "vetoed by Jev"
    assert rows["H3"]["outcome"] == "in trade" and rows["H3"]["jev"]["p_yes"] == 0.9
    assert rows["H4"]["outcome"] == "not filled"
    assert read_journal(tmp_path, sources=["watchdog"]).records == []
    assert read_journal(tmp_path / "missing").records == [] and journal_days(tmp_path / "missing") == []


def test_scrub_and_exit_kinds():
    assert scrub({"a": [{"TELEGRAM_BOT_TOKEN": "x", "n": 1}], "password": 2}) == {
        "a": [{"TELEGRAM_BOT_TOKEN": "[redacted]", "n": 1}],
        "password": "[redacted]",
    }
    assert [
        exit_kind(x) for x in ("Target 10 reached", "end of day", "kill switch", "daily loss limit", "x")
    ] == ["target", "eod", "kill", "halt", "exit"]


def test_root_level_files_count_as_node_and_bad_json_shapes_are_skipped(tmp_path):
    (tmp_path / "2026-10-27.jsonl").write_text(
        json.dumps({"ts_ns": 1, "event": "x"}) + "\n" + json.dumps({"event": "no ts"}) + "\n" + "[1]\n"
    )
    (tmp_path / "notes.jsonl").write_text("")
    read = read_journal(tmp_path)
    assert [r["source"] for r in read.records] == ["node"] and read.bad_lines == 2
    assert journal_days(tmp_path) == ["2026-10-27"]
