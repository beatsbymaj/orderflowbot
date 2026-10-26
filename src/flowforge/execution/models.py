"""Fill and latency models for tick backtests of guarded strategies.

IBKR order latency is not published (DECISIONS.md D11, UNVERIFIED). Until the Phase 7
paper stage measures it, backtests use a conservative fixed latency and fills that never
improve on the quote:
- 150 ms from our order to the venue and for each cancel/modify (Claude default; Phase 2
  measured ~14 ms mean data lag on the operator's PC, order round trips are slower).
- Limit orders resting at the quote fill only when the market trades through them
  (prob_fill_on_limit=0), and market/stop orders slip one tick half the time.
"""

from __future__ import annotations

from nautilus_trader.execution import DefaultFillModel, StaticLatencyModel

DEFAULT_LATENCY_MS = 150
MS = 1_000_000


def latency_model(ms: float = DEFAULT_LATENCY_MS) -> StaticLatencyModel:
    ns = int(ms * MS)
    return StaticLatencyModel(base_latency_nanos=ns, insert_latency_nanos=0, update_latency_nanos=0)


def fill_model(
    prob_fill_on_limit: float = 0.0, prob_slippage: float = 0.5, seed: int = 42
) -> DefaultFillModel:
    return DefaultFillModel(prob_fill_on_limit, prob_slippage, seed)
