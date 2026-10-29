"""Offline test of the Jev gate on walk-forward trades (docs/research/14, "Jev gate").

For each fold the threshold is picked on the train trades, then applied to the test trades
of the same configuration. Failed or late answers veto the trade. The gate is kept only if,
out of sample, it raises the daily net R over the ungated trades (paired day bootstrap,
p < 0.05) and its Brier score beats the train-window base rate.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from statistics import fmean

from flowforge.gate.calibration import Calibration
from flowforge.gate.jev import GateDecision
from flowforge.research import stats
from flowforge.research.fastsim import Trade
from flowforge.signals.setups import Signal

THRESHOLDS = (0.5, 0.6, 0.7)
MIN_KEPT = 10
ALPHA = 0.05


def trade_id(t: Trade) -> str:
    return f"{t.setup}:{t.symbol}:{t.signal_ts}"


class CachedGate:
    """Ask once per signal; answers are kept in a JSON-lines file so a rerun costs nothing."""

    def __init__(self, decide: Callable[[dict], GateDecision], path: Path | None = None) -> None:
        self.decide = decide
        self.path = path
        self.cache: dict[str, dict] = {}
        if path is not None and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["id"]] = row

    def p_yes(self, sig: Signal) -> tuple[float | None, str]:
        row = self.cache.get(sig.id)
        if row is None:
            d = self.decide(sig.facts)
            ok = d.reason in ("ok", "below threshold", "shadow")
            row = {
                "id": sig.id,
                "p_yes": d.p_yes if ok else None,
                "reason": d.reason,
                "latency_ms": d.latency_ms,
                "model": d.model,
            }
            self.cache[sig.id] = row
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row) + "\n")
        return row["p_yes"], row["reason"]


def _kept(trades: list[Trade], p: dict[str, float | None], tau: float) -> list[Trade]:
    return [t for t in trades if (q := p.get(trade_id(t))) is not None and q >= tau]


def evaluate(
    setup: str,
    folds: list[tuple[list[str], list[str], list[Trade]]],
    signals: dict[str, Signal],
    gate: CachedGate,
) -> dict:
    """`folds`: (train days, test days, trades of the configuration chosen for that fold)."""
    p: dict[str, float | None] = {}
    reasons: dict[str, int] = {}
    for _, _, trades in folds:
        for t in trades:
            tid = trade_id(t)
            if tid not in p and tid in signals:
                p[tid], why = gate.p_yes(signals[tid])
                reasons[why] = reasons.get(why, 0) + 1
    base_oos: list[Trade] = []
    gated_oos: list[Trade] = []
    calib = Calibration()
    per_fold = []
    for train, test, trades in folds:
        tr = [t for t in trades if t.day in set(train)]
        te = [t for t in trades if t.day in set(test)]
        best, best_mean = THRESHOLDS[0], None
        for tau in THRESHOLDS:
            kept = _kept(tr, p, tau)
            if len(kept) >= MIN_KEPT and (best_mean is None or fmean(t.r for t in kept) > best_mean):
                best, best_mean = tau, fmean(t.r for t in kept)
        rate = fmean(t.r > 0 for t in tr) if tr else None
        kept = _kept(te, p, best)
        base_oos += te
        gated_oos += kept
        for t in te:
            q = p.get(trade_id(t))
            if q is not None:
                calib.add(q, t.r > 0)
        per_fold.append(
            {"threshold": best, "train_win_rate": rate, "test_trades": len(te), "kept": len(kept)}
        )
    test_days = sorted({d for _, test, _ in folds for d in test})
    base_daily = _daily(base_oos, test_days)
    gated_daily = _daily(gated_oos, test_days)
    pval = stats.paired_bootstrap_p(gated_daily, base_daily) if base_oos else None
    rates = [f["train_win_rate"] for f in per_fold if f["train_win_rate"] is not None]
    cal = calib.summary(fmean(rates) if rates else None)
    base_mean = fmean(t.r for t in base_oos) if base_oos else None
    gated_mean = fmean(t.r for t in gated_oos) if gated_oos else None
    keep = (
        pval is not None
        and pval < ALPHA
        and gated_mean is not None
        and base_mean is not None
        and gated_mean > base_mean
        and cal["beats_base_rate"]
    )
    return {
        "setup": setup,
        "answers": reasons,
        "folds": per_fold,
        "baseline": stats.summary([t.r for t in base_oos]),
        "gated": stats.summary([t.r for t in gated_oos]),
        "p_value": None if pval is None else round(pval, 5),
        "calibration": cal,
        "keep_gate": keep,
    }


def _daily(trades: list[Trade], days: list[str]) -> list[tuple[str, float]]:
    """Net R per test day (a vetoed trade counts as 0)."""
    total = {d: 0.0 for d in days}
    for t in trades:
        total[t.day] = total.get(t.day, 0.0) + t.r
    return sorted(total.items())
