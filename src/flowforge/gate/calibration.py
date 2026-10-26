"""Calibration tracking for the Jev gate (ADR-0004): Brier score and reliability per question.

The outcome of a gate answer is whether the trade it was asked about made money after costs
(net R > 0). A gate whose Brier score isn't better than always guessing the base rate adds
nothing and is switched off.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Calibration:
    pairs: list[tuple[float, bool]] = field(default_factory=list)

    def add(self, p: float, outcome: bool) -> None:
        self.pairs.append((p, outcome))

    @property
    def n(self) -> int:
        return len(self.pairs)

    def base_rate(self) -> float | None:
        return sum(o for _, o in self.pairs) / self.n if self.pairs else None

    def brier(self) -> float | None:
        return sum((p - o) ** 2 for p, o in self.pairs) / self.n if self.pairs else None

    def base_rate_brier(self, rate: float | None = None) -> float | None:
        """Brier of always forecasting `rate` (default: this sample's own win rate)."""
        rate = self.base_rate() if rate is None else rate
        if rate is None or not self.pairs:
            return None
        return sum((rate - o) ** 2 for _, o in self.pairs) / self.n

    def reliability(self, bins: int = 10) -> list[dict]:
        """Per probability bin: count, mean forecast, observed frequency."""
        out = []
        for b in range(bins):
            lo, hi = b / bins, (b + 1) / bins
            inside = [(p, o) for p, o in self.pairs if lo <= p < hi or (b == bins - 1 and p == 1.0)]
            if inside:
                out.append(
                    {
                        "bin": f"{lo:.1f}-{hi:.1f}",
                        "n": len(inside),
                        "mean_p": round(sum(p for p, _ in inside) / len(inside), 4),
                        "observed": round(sum(o for _, o in inside) / len(inside), 4),
                    }
                )
        return out

    def summary(self, base_rate: float | None = None) -> dict:
        b, ref = self.brier(), self.base_rate_brier(base_rate)
        return {
            "n": self.n,
            "brier": None if b is None else round(b, 5),
            "base_rate_brier": None if ref is None else round(ref, 5),
            "beats_base_rate": b is not None and ref is not None and b < ref,
            "reliability": self.reliability(),
        }
