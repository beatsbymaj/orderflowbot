"""Jev gate (ADR-0004): a TypeSafe Jev yes/no check that can only veto a signal.

- Model pinned to `jev-1.13.0`; an answer from any other model is refused.
- 750 ms total deadline, no retries. Timeout, HTTP error, bad JSON or a missing answer =
  NO TRADE. The gate never adds, sizes or changes a trade, and RiskGuard runs after it.
- The state is labelled facts computed in code (signals.setups.facts): no prices, account,
  position or strategy identifiers are sent.
- The API key comes from the TYPESAFE_API_KEY environment variable and is never logged.

API (checked 2026-10-26, https://docs.typesafe.ai/api): POST /v1/systemone with `model`,
`state`, `questions`; a Noul answer is `answers[<id>].noul`, P(yes) in [0, 1].
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PINNED_MODEL = "jev-1.13.0"
URL = "https://api.typesafe.ai/v1/systemone"
QUESTION_ID = "long_ok"
QUESTIONS = {
    QUESTION_ID: {
        "type": "noul",
        "instructions": (
            "The state describes a stock at a possible long entry, as labelled facts from an order-flow"
            " footprint. Following order-flow reading (buyers defending a support zone, sellers"
            " exhausted, aggression on the buy side), is the order flow supportive of buying now?"
        ),
        "criteria": {
            "true": "The facts point to buyers in control or sellers absorbed at support.",
            "false": "The facts point to sellers in control, weak buying, or a poor setup.",
        },
    }
}

# (url, body, headers, timeout_s) -> (status, body)
Transport = Callable[[str, bytes, dict, float], tuple[int, bytes]]


def urllib_transport(url: str, body: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310 - fixed https URL
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


@dataclass(frozen=True)
class JevConfig:
    enabled: bool = False  # off until an out-of-sample test says the gate earns its place
    shadow: bool = True  # ask and log, but never veto
    model: str = PINNED_MODEL
    url: str = URL
    deadline_ms: int = 750
    threshold: float = 0.6
    max_consecutive_failures: int = 5  # then the gate stays closed (NO TRADE) until restart

    def __post_init__(self) -> None:
        if self.model != PINNED_MODEL:
            raise ValueError(f"Jev model must be pinned to {PINNED_MODEL} (ADR-0004), got {self.model}")
        if not 0 < self.deadline_ms <= 750:
            raise ValueError("Jev deadline must be 1..750 ms (ADR-0004)")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("Jev threshold must be within 0..1")


def load_config(path: Path) -> JevConfig:
    if not path.exists():
        return JevConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return JevConfig(**(data.get("jev") or {}))


@dataclass(frozen=True)
class GateDecision:
    allow: bool
    p_yes: float | None
    latency_ms: float | None
    reason: str  # ok | below threshold | timeout | error: ... | disabled | shadow | circuit open
    model: str | None = None
    vetoed_in_shadow: bool = False  # shadow mode: what the gate would have done

    def as_dict(self) -> dict:
        return {
            "allow": self.allow,
            "p_yes": self.p_yes,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "model": self.model,
            "vetoed_in_shadow": self.vetoed_in_shadow,
        }


@dataclass
class JevGate:
    config: JevConfig = field(default_factory=JevConfig)
    api_key: str | None = None
    transport: Transport = urllib_transport
    clock: Callable[[], float] = time.monotonic
    failures: int = 0

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.environ.get("TYPESAFE_API_KEY") or None
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jev")

    # -- calls -----------------------------------------------------------------------------
    def ask(self, facts: dict) -> tuple[float | None, float, str, str | None]:
        """One request. Returns (p_yes, latency_ms, error or '', model). Never raises."""
        body = json.dumps({"model": self.config.model, "state": facts, "questions": QUESTIONS}).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = self.clock()
        try:
            status, raw = self.transport(self.config.url, body, headers, self.config.deadline_ms / 1000)
        except Exception as exc:  # noqa: BLE001 - any failure is NO TRADE
            return None, (self.clock() - t0) * 1000, f"error: {type(exc).__name__}", None
        latency = (self.clock() - t0) * 1000
        if status != 200:
            return None, latency, f"error: HTTP {status}", None
        try:
            data = json.loads(raw)
            model = data.get("model")
            p = float(data["answers"][QUESTION_ID]["noul"])
        except (ValueError, KeyError, TypeError):
            return None, latency, "error: bad response", None
        if model != self.config.model:
            return None, latency, f"error: answered by {model}", model
        if not 0.0 <= p <= 1.0:
            return None, latency, "error: probability out of range", model
        return p, latency, "", model

    def decide(self, facts: dict) -> GateDecision:
        """Blocking, with the hard deadline (the HTTP timeout alone isn't a total budget)."""
        return self._finish(self.submit_raw(facts), wait=True)

    def submit(self, facts: dict) -> Future:
        """Non-blocking for the live node: a future of the GateDecision. Callers must also
        enforce the deadline on their own clock (`deadline_ms` after the signal)."""
        raw = self.submit_raw(facts)
        out: Future = Future()
        if not isinstance(raw, Future):
            out.set_result(self._finish(raw, wait=False))
            return out
        raw.add_done_callback(lambda f: out.set_result(self._finish(f, wait=False)))
        return out

    def submit_raw(self, facts: dict) -> Future | GateDecision:
        if not self.config.enabled:
            return GateDecision(True, None, None, "disabled")
        if not self.api_key:
            return GateDecision(self.config.shadow, None, None, "error: no TYPESAFE_API_KEY")
        if self.failures >= self.config.max_consecutive_failures:
            return GateDecision(self.config.shadow, None, None, "circuit open")
        return self._pool.submit(self.ask, facts)

    def _finish(self, raw: Future | GateDecision, wait: bool) -> GateDecision:
        if isinstance(raw, GateDecision):
            return raw
        try:
            p, latency, error, model = raw.result(timeout=self.config.deadline_ms / 1000 if wait else 0)
        except FutureTimeout:
            raw.cancel()
            self.failures += 1
            return self._closed(None, None, "timeout", None)
        if not error and latency > self.config.deadline_ms:
            error = "timeout"
        if error:
            self.failures += 1
            return self._closed(p, latency, error, model)
        self.failures = 0
        ok = p >= self.config.threshold
        if self.config.shadow:
            return GateDecision(True, p, round(latency, 1), "shadow", model, vetoed_in_shadow=not ok)
        return GateDecision(ok, p, round(latency, 1), "ok" if ok else "below threshold", model)

    def _closed(self, p, latency, reason: str, model) -> GateDecision:
        lat = None if latency is None else round(latency, 1)
        if self.config.shadow:
            return GateDecision(True, p, lat, reason, model, vetoed_in_shadow=True)
        return GateDecision(False, p, lat, reason, model)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
