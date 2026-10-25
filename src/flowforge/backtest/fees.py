"""IBKR Pro Fixed commissions for US stocks, as a Nautilus fee model.

Source (checked 2026-10-25): https://www.interactivebrokers.com/en/pricing/commissions-stocks.php
- USD 0.005 per share, minimum USD 1.00, maximum 1% of trade value; if the 1% cap is
  below the minimum, the cap is charged.
- Regulatory pass-through on Fixed: SEC fee USD 0.0000206 x value of sales. FINRA TAF is
  USD 0.00 on that page (fee holiday Oct-Dec 2026), so it's not charged here.
"""

from __future__ import annotations

from nautilus_trader.execution import FeeModel
from nautilus_trader.model import Currency, Money, OrderSide

PER_SHARE = 0.005
MIN_PER_ORDER = 1.00
MAX_PCT_OF_VALUE = 0.01
SEC_FEE_RATE = 0.0000206


def ibkr_fixed_commission(shares: float, price: float, is_sale: bool) -> float:
    """Commission in USD for one fill of `shares` at `price`."""
    value = shares * price
    cap = MAX_PCT_OF_VALUE * value
    fee = cap if cap < MIN_PER_ORDER else min(max(PER_SHARE * shares, MIN_PER_ORDER), cap)
    if is_sale:
        fee += SEC_FEE_RATE * value
    return round(fee, 2)


class IbkrFixedFeeModel(FeeModel):
    # Nautilus charges per fill; IBKR's minimum is per order. Exits here are single fills
    # (bar-based fills don't partial), so per fill and per order are the same.
    _usd = Currency.from_str("USD")

    def get_commission(self, order, fill_qty, fill_px, instrument) -> Money:
        fee = ibkr_fixed_commission(float(fill_qty), float(fill_px), order.side == OrderSide.SELL)
        return Money(fee, self._usd)
