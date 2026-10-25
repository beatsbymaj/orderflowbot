"""VPIN: volume-synchronized probability of informed trading.

Easley, Lopez de Prado and O'Hara (2012), "Flow Toxicity and Liquidity in a High-Frequency
World", Review of Financial Studies 25(5). Volume is cut into equal buckets of V shares;
VPIN = sum over the last n buckets of |buy - sell| / (n * V).

Difference from the paper: buy and sell volume come from our trade-by-trade classification,
not bulk volume classification. Unsided prints (auctions, off-market) don't fill buckets.
"""

from __future__ import annotations

from collections import deque


class Vpin:
    def __init__(self, bucket: int, window: int) -> None:
        self.bucket = max(1, bucket)
        self.window = window
        self._buy = 0
        self._sell = 0
        self._imbalances: deque[int] = deque(maxlen=window)
        self.buckets = 0

    def add(self, size: int, side: int) -> None:
        while size:
            take = min(size, self.bucket - self._buy - self._sell)
            if side > 0:
                self._buy += take
            else:
                self._sell += take
            size -= take
            if self._buy + self._sell == self.bucket:
                self._imbalances.append(abs(self._buy - self._sell))
                self.buckets += 1
                self._buy = self._sell = 0

    @property
    def value(self) -> float | None:
        if len(self._imbalances) < self.window:
            return None
        return round(sum(self._imbalances) / (self.window * self.bucket), 6)
