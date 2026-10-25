import pytest

from flowforge.backtest.fees import ibkr_fixed_commission


@pytest.mark.parametrize(
    ("shares", "price", "sale", "expected"),
    [
        (10, 100.0, False, 1.00),  # minimum applies
        (1000, 50.0, False, 5.00),  # 0.005 per share
        (1, 50.0, False, 0.50),  # 1% cap below the minimum: the cap is charged
        (1000, 0.30, False, 3.00),  # 1% cap below the per-share rate
        (10, 100.0, True, 1.02),  # SEC fee on sales: 0.0000206 x 1000
    ],
)
def test_ibkr_fixed_commission(shares, price, sale, expected):
    assert ibkr_fixed_commission(shares, price, sale) == pytest.approx(expected)
