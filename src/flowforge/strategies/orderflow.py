"""OrderFlowStrategy: the Phase 5 setups traded through GuardedStrategy (Phase 4).

Feature records come from the FeatureActor in the same node (`on_feature`, wired as the
actor's `on_record`). Each record goes through SignalEngine; a signal for an enabled setup
gets its stop and target from `setups.plan` at the current ask, then (optionally) the Jev
gate, then `request_entry`, where RiskGuard decides and sizes. Nothing else can send an order.

Enabled setups and their configuration are constructor arguments: only setups that passed
the Phase 5 walk-forward gate should be enabled for paper (ADR-0014).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from flowforge.execution.strategy import GuardedStrategy
from flowforge.gate.jev import GateDecision, JevGate
from flowforge.risk.guard import EntryRequest
from flowforge.signals.setups import BOOK_DEFAULT, Plan, Signal, SignalEngine, plan


@dataclass
class _Pending:
    signal: Signal
    plan: Plan
    deadline_ns: int
    future: Any
    shadow: bool = False  # already entered; only the answer is still to be journaled


class OrderFlowStrategy(GuardedStrategy):
    def __init__(
        self,
        symbols: list[str],
        guard: Any,
        universe: Mapping[str, Any],
        journal: Any = None,
        setups: Mapping[str, tuple[str, str]] | None = None,  # setup -> (stop mode, target mode)
        gate: JevGate | None = None,
        gate_blocking: bool = False,  # backtests: wait for Jev (real deadline) instead of polling
        **kwargs: Any,
    ) -> None:
        super().__init__(symbols, guard, universe, journal, **kwargs)
        self.setups = dict(setups or {})
        self.signals = SignalEngine()
        self.gate = gate
        self.gate_blocking = gate_blocking
        self.pending: list[_Pending] = []
        self.signal_count = 0

    # -- features -> signals ------------------------------------------------------------
    def on_feature(self, record: dict) -> None:
        for sig in self.signals.on_record(record):
            self.signal_count += 1
            self.on_signal(sig)

    def on_signal(self, sig: Signal) -> None:
        cfg = self.setups.get(sig.setup)
        self._journal(
            "signal",
            intent=sig.id,
            setup=sig.setup,
            symbol=sig.symbol,
            reason=sig.reason,
            zone=[sig.zone_low, sig.zone_high],
            enabled=cfg is not None,
        )
        if cfg is None:
            return
        st = self.market.get(sig.symbol)
        if st is None or st.quote is None:
            self._journal("signal_skipped", intent=sig.id, why="no quote")
            return
        p = plan(sig, st.quote.ask, *cfg)
        if p is None:
            self._journal(
                "signal_skipped", intent=sig.id, why="no plan (stop band, target or unfinished business)"
            )
            return
        if self.gate is None or not self.gate.config.enabled:
            self._enter(sig, p)
            return
        self._journal("jev_request", intent=sig.id, model=self.gate.config.model, facts=sig.facts)
        if self.gate_blocking:
            d = self.gate.decide(sig.facts)
            self._journal_gate(sig, d)
            if d.allow or self.gate.config.shadow:
                self._enter(sig, p)
            return
        now = self.clock.timestamp_ns()
        fut = self.gate.submit(sig.facts)
        deadline = now + self.gate.config.deadline_ms * 1_000_000
        self.pending.append(_Pending(sig, p, deadline, fut, shadow=self.gate.config.shadow))
        if self.gate.config.shadow:
            self._enter(sig, p)  # shadow mode: Jev is asked and logged, never obeyed

    # -- gate results (checked on every quote and every second) ----------------------------
    def on_market(self, symbol: str) -> None:
        if self.pending:
            self._check_pending()

    def on_risk_tick(self, now: int) -> None:
        if self.pending:
            self._check_pending()

    def _check_pending(self) -> None:
        now = self.clock.timestamp_ns()
        keep = []
        for item in self.pending:
            if item.future.done():
                d: GateDecision = item.future.result()
                late = now > item.deadline_ns
                self._journal_gate(item.signal, d, late)
                if d.allow and not late and not item.shadow:
                    self._enter(item.signal, item.plan)
            elif now > item.deadline_ns:
                item.future.cancel()
                self._journal(
                    "jev_decision", intent=item.signal.id, allow=False, reason="timeout (node clock)"
                )
            else:
                keep.append(item)
        self.pending = keep

    def _journal_gate(self, sig: Signal, d: GateDecision, late: bool = False) -> None:
        fields = d.as_dict() | ({"allow": False, "reason": "timeout (node clock)"} if late else {})
        self._journal("jev_decision", intent=sig.id, **fields)

    def _enter(self, sig: Signal, p: Plan) -> None:
        self.request_entry(EntryRequest(sig.id, sig.symbol, p.stop, p.target))


def setups_from_text(text: str) -> dict[str, tuple[str, str]]:
    """'H1:zone/volume,H4' -> {'H1': ('zone', 'volume'), 'H4': BOOK_DEFAULT}."""
    out: dict[str, tuple[str, str]] = {}
    for part in filter(None, (x.strip() for x in text.split(","))):
        name, _, cfg = part.partition(":")
        stop, _, target = cfg.partition("/")
        out[name] = (stop, target) if cfg else BOOK_DEFAULT
    return out
