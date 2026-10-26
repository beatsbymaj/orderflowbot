"""Phase 5 statistics: bootstrap, Holm, PSR/DSR, drawdown."""

from __future__ import annotations

import math

from flowforge.research import stats


def test_holm_matches_a_worked_example():
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert adj["a"] == 0.03  # 3 x 0.01
    assert math.isclose(adj["c"], 0.06) and math.isclose(adj["b"], 0.06)  # 2 x 0.03, then max(0.06, 1 x 0.04)
    assert adj["d"] is None
    assert stats.holm({"a": 0.9, "b": 0.8}) == {"a": 1.0, "b": 1.0}


def test_day_bootstrap_separates_edge_from_noise():
    good = [(f"d{i}", 0.5 + (0.3 if i % 2 else -0.3)) for i in range(40)]
    noise = [(f"d{i}", 1.0 if i % 2 else -1.0) for i in range(40)]
    assert stats.bootstrap_p(good, reps=2000) < 0.01
    assert stats.bootstrap_p(noise, reps=2000) > 0.3
    assert stats.bootstrap_p([], reps=10) is None
    better = [(f"d{i}", 1.0) for i in range(30)]
    worse = [(f"d{i}", 0.0 + (0.1 if i % 3 else -0.1)) for i in range(30)]
    assert stats.paired_bootstrap_p(better, worse, reps=2000) < 0.01
    assert stats.paired_bootstrap_p([], [], reps=10) is None


def test_psr_and_deflated_sharpe():
    x = [0.2 + (0.5 if i % 2 else -0.5) for i in range(200)]
    p0 = stats.psr(x)
    assert 0.99 < p0 <= 1.0
    sr0 = stats.expected_max_sharpe([0.0, 0.1, -0.1, 0.2, -0.2], 20)
    assert sr0 > 0
    assert stats.dsr(x, [0.0, 0.1, -0.1, 0.2, -0.2], 20) < p0
    assert stats.psr([1.0, 1.0]) is None and stats.psr([1.0, 1.0, 1.0]) is None
    assert stats.expected_max_sharpe([0.1], 20) == 0.0 and stats.expected_max_sharpe([0.1, 0.2], 1) == 0.0
    assert stats.sharpe([1.0]) is None and stats.sharpe([1.0, 1.0]) is None
    assert stats.moments([2.0, 2.0]) == (2.0, 0.0, 0.0, 3.0)


def test_drawdown_streak_and_summary():
    r = [1, -1, -1, -1, 2, -0.5]
    assert stats.max_drawdown(r) == 3
    assert stats.losing_streak(r) == 3
    s = stats.summary(r)
    assert s["trades"] == 6 and s["win_rate"] == round(2 / 6, 4) and s["total_r"] == -0.5
    assert stats.summary([])["mean_r"] is None
