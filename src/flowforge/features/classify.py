"""Aggressor side for each trade: quote rule at the NBBO in force, tick test as fallback.

Lee and Ready (1991) without their 5-second delay: SIP timestamps are nanoseconds, and the
sequencer already gives each trade the quote that came before it.
- NBBO valid (bid > 0, ask > bid): above the midpoint = buy, below = sell, at it = tick test.
- NBBO locked, crossed or missing (all seen in the Phase 2 data): tick test.
- Tick test: price above the last different market-priced price = buy, below = sell.
- Off-market prints (average price, derivatively priced, ...) and auction prints: no side.
"""

from __future__ import annotations

from enum import IntEnum

from flowforge.features import conditions as cond


class Side(IntEnum):
    SELL = -1
    NONE = 0
    BUY = 1


QUOTE_RULE = "quote"
TICK_TEST = "tick"
NO_SIDE = "none"


class Classifier:
    def __init__(self) -> None:
        self.bid = 0
        self.ask = 0
        self._last_price = 0
        self._last_move = Side.NONE  # direction of the last price change, for zero ticks

    def on_quote(self, bid_u: int, ask_u: int) -> None:
        self.bid = bid_u
        self.ask = ask_u

    @property
    def nbbo_state(self) -> str:
        if self.bid <= 0 or self.ask <= 0:
            return "missing"
        if self.ask == self.bid:
            return "locked"
        return "crossed" if self.ask < self.bid else "ok"

    def classify(self, price_u: int, conditions: tuple[str, ...]) -> tuple[Side, str]:
        if not cond.market_priced(conditions):
            return Side.NONE, NO_SIDE
        tick = self._tick(price_u)
        if cond.is_auction(conditions):
            return Side.NONE, NO_SIDE
        if self.nbbo_state == "ok":
            twice = 2 * price_u
            mid2 = self.bid + self.ask
            if twice > mid2:
                return Side.BUY, QUOTE_RULE
            if twice < mid2:
                return Side.SELL, QUOTE_RULE
        if tick is Side.NONE:
            return Side.NONE, NO_SIDE
        return tick, TICK_TEST

    def _tick(self, price_u: int) -> Side:
        """Tick-test side for this price, then remember it."""
        if self._last_price == 0:
            side = Side.NONE
        elif price_u > self._last_price:
            side = Side.BUY
        elif price_u < self._last_price:
            side = Side.SELL
        else:
            side = self._last_move
        self._last_price = price_u
        self._last_move = side
        return side
