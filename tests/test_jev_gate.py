"""Jev gate (ADR-0004): pinned model, 750 ms deadline, every failure is NO TRADE."""

from __future__ import annotations

import json
import time

import pytest

from flowforge.gate.calibration import Calibration
from flowforge.gate.evaluate import CachedGate, evaluate, trade_id
from flowforge.gate.jev import PINNED_MODEL, QUESTION_ID, GateDecision, JevConfig, JevGate, load_config
from flowforge.research.fastsim import Trade
from flowforge.signals.setups import Signal

FACTS = {"setup": "support zone with confirmation", "bar_delta": "buyers"}


def reply(p=0.8, model=PINNED_MODEL, status=200):
    body = json.dumps({"model": model, "answers": {QUESTION_ID: {"type": "noul", "noul": p}}}).encode()

    def transport(url, data, headers, timeout):
        transport.calls.append((url, json.loads(data), headers, timeout))
        return status, body

    transport.calls = []
    return transport


def gate(transport, **cfg):
    return JevGate(
        JevConfig(**({"enabled": True, "shadow": False} | cfg)), api_key="test-key", transport=transport
    )


def test_allows_above_threshold_and_sends_only_labelled_facts():
    t = reply(0.8)
    d = gate(t).decide(FACTS)
    assert d.allow and d.reason == "ok" and d.p_yes == 0.8 and d.model == PINNED_MODEL
    url, body, headers, timeout = t.calls[0]
    assert url.endswith("/v1/systemone") and body["model"] == "jev-1.13.0" and body["state"] == FACTS
    assert headers["Authorization"] == "Bearer test-key" and timeout == 0.75
    assert "test-key" not in json.dumps(d.as_dict())


def test_below_threshold_vetoes_and_shadow_only_records_it():
    d = gate(reply(0.4)).decide(FACTS)
    assert not d.allow and d.reason == "below threshold" and d.p_yes == 0.4
    d = gate(reply(0.4), shadow=True).decide(FACTS)
    assert d.allow and d.reason == "shadow" and d.vetoed_in_shadow and d.p_yes == 0.4


@pytest.mark.parametrize(
    ("transport", "reason"),
    [
        (reply(model="jev-1.14.0"), "error: answered by jev-1.14.0"),
        (reply(status=429), "error: HTTP 429"),
        (reply(p=1.5), "error: probability out of range"),
    ],
)
def test_bad_answers_are_no_trade(transport, reason):
    d = gate(transport).decide(FACTS)
    assert not d.allow and d.reason == reason


def test_exceptions_garbage_and_missing_key_are_no_trade():
    def boom(*_):
        raise OSError("network down")

    assert gate(boom).decide(FACTS).reason == "error: OSError"

    def garbage(*_):
        return 200, b"not json"

    assert gate(garbage).decide(FACTS).reason == "error: bad response"
    g = JevGate(JevConfig(enabled=True, shadow=False), api_key="", transport=reply())
    g.api_key = None
    d = g.decide(FACTS)
    assert not d.allow and "TYPESAFE_API_KEY" in d.reason


def test_deadline_is_a_total_budget():
    def slow(*_):
        time.sleep(1.0)
        return 200, b"{}"

    g = gate(slow)
    t0 = time.monotonic()
    d = g.decide(FACTS)
    assert d.reason == "timeout" and not d.allow
    assert time.monotonic() - t0 < 0.95
    ticks = iter([0.0, 0.9])  # the call came back, but 900 ms after it went out
    late = JevGate(
        JevConfig(enabled=True, shadow=False), api_key="k", transport=reply(), clock=lambda: next(ticks)
    )
    assert late.decide(FACTS).reason == "timeout"
    g.close()


def test_circuit_opens_after_repeated_failures_and_disabled_gate_passes():
    g = gate(reply(status=529))
    for _ in range(5):
        g.decide(FACTS)
    assert g.decide(FACTS).reason == "circuit open"
    d = JevGate(JevConfig(), api_key="k", transport=reply()).decide(FACTS)
    assert d.allow and d.reason == "disabled"


def test_async_submit():
    fut = gate(reply(0.9)).submit(FACTS)
    assert fut.result(timeout=2).allow
    off = JevGate(JevConfig(), api_key="k").submit(FACTS)
    assert off.result(timeout=0).reason == "disabled"


def test_config_is_pinned_and_bounded(tmp_path):
    with pytest.raises(ValueError, match="pinned"):
        JevConfig(model="jev-latest")
    with pytest.raises(ValueError, match="deadline"):
        JevConfig(deadline_ms=2000)
    with pytest.raises(ValueError, match="threshold"):
        JevConfig(threshold=1.5)
    path = tmp_path / "jev.yaml"
    assert load_config(path) == JevConfig()
    path.write_text("jev:\n  enabled: true\n  shadow: true\n  threshold: 0.7\n")
    assert load_config(path) == JevConfig(enabled=True, shadow=True, threshold=0.7)


def test_calibration_brier_and_reliability():
    c = Calibration()
    for p, o in [(0.9, True), (0.8, True), (0.2, False), (0.1, False), (1.0, True)]:
        c.add(p, o)
    assert c.brier() == pytest.approx((0.01 + 0.04 + 0.04 + 0.01 + 0) / 5)
    assert c.base_rate() == 0.6
    s = c.summary()
    assert s["beats_base_rate"] and s["n"] == 5
    assert {r["bin"] for r in s["reliability"]} == {"0.1-0.2", "0.2-0.3", "0.8-0.9", "0.9-1.0"}
    assert Calibration().summary()["brier"] is None


def make_trades(days, win_p, lose_p):
    trades, signals = [], {}
    for i, d in enumerate(days):
        for k, r in enumerate((1.5, -1.0)):
            ts = i * 10 + k
            t = Trade(
                "H4", "SPY", d, "zone/volume", ts, ts, 100, 10, 99, 102, ts + 1, 100 + r, "x", 0, 0, 0, r
            )
            trades.append(t)
            facts = {"win": r > 0}
            signals[trade_id(t)] = Signal("H4", "SPY", ts, d, 99, 99.3, 0.05, 5.0, (), (), "t", facts)
    return trades, signals


def test_gate_kept_only_when_it_separates_winners_from_losers(tmp_path):
    days = [f"2026-08-{i:02d}" for i in range(1, 31)]
    trades, signals = make_trades(days, 0.9, 0.2)
    folds = [(days[:20], days[20:], trades)]
    smart = CachedGate(
        lambda f: GateDecision(True, 0.9 if f["win"] else 0.2, 10.0, "shadow"), tmp_path / "a.jsonl"
    )
    res = evaluate("H4", folds, signals, smart)
    assert res["keep_gate"] and res["gated"]["trades"] == 10 and res["baseline"]["trades"] == 20
    assert res["folds"][0]["threshold"] == 0.5
    # Answers are cached on disk: a second run asks nothing.
    again = CachedGate(lambda f: pytest.fail("asked again"), tmp_path / "a.jsonl")
    assert evaluate("H4", folds, signals, again)["keep_gate"]
    blind = CachedGate(lambda f: GateDecision(True, 0.55, 10.0, "shadow"))
    assert not evaluate("H4", folds, signals, blind)["keep_gate"]
    dead = CachedGate(lambda f: GateDecision(False, None, None, "timeout"))
    res = evaluate("H4", folds, signals, dead)
    assert not res["keep_gate"] and res["gated"]["trades"] == 0 and res["answers"] == {"timeout": 60}
