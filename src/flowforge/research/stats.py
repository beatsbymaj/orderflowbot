"""Statistics for the Phase 5 gate (docs/research/14, "Pass bar"). Standard library only.

- Day-block bootstrap: resample whole test days, so trades on the same day (which move
  together) aren't treated as independent.
- Holm (1979) step-down adjustment across the hypotheses.
- Probabilistic and deflated Sharpe ratio (Bailey & Lopez de Prado 2014).
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from statistics import NormalDist, fmean, pstdev

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


def by_day(values: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for day, v in values:
        out.setdefault(day, []).append(v)
    return out


def bootstrap_p(
    values: Sequence[tuple[str, float]], reps: int = 10_000, seed: int = 20261026
) -> float | None:
    """One-sided p-value for mean > 0: resample days from the data shifted to mean 0 and count
    how often the resampled mean is at least the observed one."""
    days = by_day(values)
    if not days:
        return None
    n = sum(len(v) for v in days.values())
    observed = sum(sum(v) for v in days.values()) / n
    centred = [[x - observed for x in v] for v in days.values()]
    sums = [sum(v) for v in centred]
    counts = [len(v) for v in centred]
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling, not security
    k = len(centred)
    hits = 0
    for _ in range(reps):
        s = c = 0
        for _ in range(k):
            i = rng.randrange(k)
            s += sums[i]
            c += counts[i]
        if c and s / c >= observed:
            hits += 1
    return (hits + 1) / (reps + 1)


def paired_bootstrap_p(
    a: Sequence[tuple[str, float]], b: Sequence[tuple[str, float]], reps: int = 10_000, seed: int = 20261026
) -> float | None:
    """One-sided p-value that mean(a) > mean(b) per day (a, b: per-trade values on the same
    days, e.g. gated vs ungated). Uses daily mean differences."""
    da, db = by_day(a), by_day(b)
    days = sorted(set(da) | set(db))
    if not days:
        return None
    diffs = [(d, sum(da.get(d, [])) - sum(db.get(d, []))) for d in days]
    return bootstrap_p(diffs, reps, seed)


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-adjusted p-values (None stays None and doesn't count as a test)."""
    tested = sorted((p, k) for k, p in pvalues.items() if p is not None)
    m = len(tested)
    out: dict[str, float | None] = {k: None for k in pvalues}
    running = 0.0
    for i, (p, k) in enumerate(tested):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def moments(x: Sequence[float]) -> tuple[float, float, float, float]:
    """Mean, population std, skewness, (non-excess) kurtosis."""
    mu = fmean(x)
    sd = pstdev(x)
    if sd == 0:
        return mu, 0.0, 0.0, 3.0
    skew = fmean(((v - mu) / sd) ** 3 for v in x)
    kurt = fmean(((v - mu) / sd) ** 4 for v in x)
    return mu, sd, skew, kurt


def sharpe(x: Sequence[float]) -> float | None:
    if len(x) < 2:
        return None
    mu, sd, _, _ = moments(x)
    return mu / sd if sd else None


def psr(x: Sequence[float], benchmark: float = 0.0) -> float | None:
    """P(true per-trade Sharpe > benchmark)."""
    if len(x) < 3:
        return None
    mu, sd, skew, kurt = moments(x)
    if sd == 0:
        return None
    sr = mu / sd
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return _N.cdf((sr - benchmark) * math.sqrt(len(x) - 1) / math.sqrt(denom))


def expected_max_sharpe(trial_sharpes: Sequence[float], n_trials: int) -> float:
    """SR0: the best Sharpe expected from n_trials tries with no skill."""
    if n_trials < 2 or len(trial_sharpes) < 2:
        return 0.0
    sd = pstdev(trial_sharpes)
    z1 = _N.inv_cdf(1 - 1 / n_trials)
    z2 = _N.inv_cdf(1 - 1 / (n_trials * math.e))
    return sd * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def dsr(x: Sequence[float], trial_sharpes: Sequence[float], n_trials: int) -> float | None:
    return psr(x, expected_max_sharpe(trial_sharpes, n_trials))


def max_drawdown(values: Sequence[float]) -> float:
    """Largest peak-to-trough fall of the running sum (same units as values)."""
    peak = total = worst = 0.0
    for v in values:
        total += v
        peak = max(peak, total)
        worst = max(worst, peak - total)
    return worst


def losing_streak(values: Sequence[float]) -> int:
    best = run = 0
    for v in values:
        run = run + 1 if v < 0 else 0
        best = max(best, run)
    return best


def summary(values: Sequence[float]) -> dict:
    n = len(values)
    return {
        "trades": n,
        "win_rate": round(sum(1 for v in values if v > 0) / n, 4) if n else None,
        "mean_r": round(fmean(values), 4) if n else None,
        "total_r": round(sum(values), 3),
        "max_drawdown_r": round(max_drawdown(values), 3),
        "losing_streak": losing_streak(values),
        "sharpe_per_trade": round(s, 4) if (s := sharpe(values)) is not None else None,
    }
