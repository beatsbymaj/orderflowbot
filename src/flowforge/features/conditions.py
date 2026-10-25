"""SIP sale conditions and venues, as the features use them.

Sources (checked 2026-10-25, docs/research/11-sale-conditions.md):
- UTP Binary Output Spec v4.1 (Sept 2026), Sale Condition Matrix:
  https://www.utpplan.com/DOC/UtpBinaryOutputSpec.pdf
- CTS Pillar Output Spec v2.11b (2026-01-29): a space is a regular sale ('@' was retired),
  participant D is FINRA: https://www.ctaplan.com/publicdocs/ctaplan/CTS_Pillar_Output_Specification.pdf

Where a print carries several conditions, a "no" for any statistic wins (UTP matrix rule).
"""

from __future__ import annotations

from collections.abc import Iterable

# Don't add volume (and don't count toward Alpaca's minute bars; see data/quality.py).
NO_VOLUME = frozenset({"M", "Q", "9"})
# Add volume but not last price: C cash, H price variation, I odd lot, R seller, T form T,
# U extended hours out of sequence, V contingent, W average price, 7 qualified contingent.
VOLUME_ONLY = frozenset({"C", "H", "I", "R", "T", "U", "V", "W", "7"})
# Add volume and high/low but not last: G bunched sold, P prior reference, Z sold out of
# sequence, 4 derivatively priced.
NOT_LAST = frozenset({"G", "P", "Z", "4"})
# Priced away from the current market or out of sequence: no aggressor side, no footprint
# row, no price update. Odd lots (I) are volume-only on the SIP but trade at the market,
# so the features keep them.
OFF_MARKET = (VOLUME_ONLY - {"I"}) | NOT_LAST
# Auction and cross prints (O opening, 5 reopening, 6 closing, X cross): at the market
# price but with no aggressor.
AUCTION = frozenset({"O", "5", "6", "X"})
ISO = "F"  # intermarket sweep order
ODD_LOT = "I"
# Every code either spec defines; anything else is counted as unknown and treated as regular.
KNOWN = frozenset(" @ABCDEFGHIKLMNOPQRSTUVWXYZ1456789")

# Participant D is FINRA on both tapes: the ADF and the three TRFs (off-exchange prints).
OFF_EXCHANGE = "D"


def counts_volume(conditions: Iterable[str]) -> bool:
    return NO_VOLUME.isdisjoint(conditions)


def market_priced(conditions: Iterable[str]) -> bool:
    """Volume at a price that reflects the market now: footprint rows, OHLC, tick test."""
    c = frozenset(conditions)
    return NO_VOLUME.isdisjoint(c) and OFF_MARKET.isdisjoint(c)


def is_auction(conditions: Iterable[str]) -> bool:
    return not AUCTION.isdisjoint(conditions)


def unknown(conditions: Iterable[str]) -> list[str]:
    return [c for c in conditions if c not in KNOWN]
