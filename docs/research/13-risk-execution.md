# 13. Risk and execution facts for Phase 4

Checked 2026-10-26. Re-verify monthly with the other research files.

## Market calendar
- NYSE holidays and early closes, 2026–2028: https://www.nyse.com/trade/hours-calendars (2026-10-26).
  Core session 9:30–16:00 ET. Early closes at 13:00 ET: 2026-11-27, 2026-12-24, 2027-11-26,
  2028-07-03, 2028-11-24. New Year's Day 2028 falls on a Saturday and is not observed.
  Encoded in `flowforge.risk.calendar`; any date outside 2026–2028 has no session (no entries).

## IBKR order handling
- Bracket orders: "Your two Sell orders now enter the market" once the parent fills; when one
  exit fills, the other is cancelled. Child quantity "matches the original order quantity".
  https://www.interactivebrokers.com/en/trading/orders/bracket.php (2026-10-26).
  **UNVERIFIED:** what IBKR does with bracket children when the parent fills only partly. The
  page doesn't say. FlowForge doesn't use native brackets for this reason (ADR-0013).
- Bracket API sample: parent and target are sent with `transmit=false`, the stop last with
  `transmit=true`. https://www.interactivebrokers.com/docs/general/order-types/complex-orders/bracket-orders (2026-10-26).
- Cancel scope: "cancelOrder can only be used to cancel an order that was placed originally by a
  client with the same client ID"; reqGlobalCancel "will cancel all open orders, regardless of how
  they were originally placed". https://interactivebrokers.github.io/tws-api/cancel_order.html (2026-10-26).
- Master client id: lets a connection "pull order and trades data even from other API
  connections" (visibility only; the page says nothing about control).
  https://www.interactivebrokers.com/docs/tws-api/doc/order-management/client-id-0-and-the-master-client-id (2026-10-26).
  → The watchdog reconnects under the node's own client id after the node is gone, so it can
  cancel the node's stops. **UNVERIFIED** until the Phase 7 paper drill: that IB Gateway frees
  the id promptly when the node dies, and that the Nautilus IB adapter can cancel orders it
  learns about through reconciliation (`fetch_all_open_orders=True`). Whether Nautilus ever
  sends reqGlobalCancel is unknown (the message type exists in the adapter's binary).
- IOC is listed as a US time in force; stop orders may be simulated by IBKR ("The broker
  simulates certain order types (for example, stop or conditional orders)"), which still means
  they are held at IBKR, not on our machine. https://www.interactivebrokers.com/en/trading/ordertypes.php (2026-10-26).
  **UNVERIFIED:** IOC limit orders for US stocks through the Nautilus IB adapter; `reduce_only` is rejected
  by the adapter (VERIFIED 2026-10-30, docs/research/16), so FlowForge doesn't send it. FlowForge's own check that a sell never exceeds the shares held doesn't rely on it.
- Order latency is still unpublished (DECISIONS.md D11). Backtests assume 150 ms.

## NautilusTrader 2.0.0rc7 behaviour found while testing (simulated venue)
- `order_factory.bracket` requires a take-profit price for a LIMIT take-profit.
- With the default `oto_trigger_mode=PARTIAL`, a bracket's children are released at the **full**
  order size on the first partial fill; on a cash account the venue then rejects the stop
  ("Short selling not permitted"). With `FULL`, children wait for the full fill, so the shares
  bought so far have no stop. Either way a native bracket can't be trusted with partial fills.
- When the target leg was the OCO sibling of a rejected stop, the venue rejected it ("Contingent
  order … already closed") rather than cancelling it. FlowForge's flatten logic had to treat that
  reject as "gone" (found by the chaos tests, fixed before merge).
- The simulated matching engine holds orders while an instrument is in HALT status.
- NETTING venues key positions by strategy; another strategy can't name that position when
  closing. The watchdog's flatten falls back to an unnamed sell of the same size.
- The Nautilus RiskEngine denies orders a CASH account can't pay for (`OrderDenied`); FlowForge
  counts those like broker rejects.
- Python strategies can't switch the Nautilus RiskEngine trading state (no API in rc7), so the
  RiskGuard halts and the kill switch are FlowForge's own (DECISIONS.md D7 layer 3 lives in the
  guarded strategy's 1-second tick).
