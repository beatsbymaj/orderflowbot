# 09 — Backtest data and trading costs

Accessed 2026-10-25 with WebFetch. Re-verify monthly with the rest of docs/research/.

## Alpaca historical bars (free account)
- Free (Basic) plan: historical data "Since 2016", 200 historical API calls/min; real-time is IEX only. [A1]
- Without a subscription, SIP (full-market) history is allowed if "the `end` parameter must be at least 15 minutes old to query SIP data without a subscription". [A2] So a free key can download consolidated 1-minute bars for backtests. Error for recent SIP data: "subscription does not permit querying recent SIP data". [A2]
- `feed` parameter values: `iex`, `sip`, `boats`, `overnight`; IEX is "the only feed that can be used without a subscription" for live data. [A3]
- Endpoint used: `GET https://data.alpaca.markets/v2/stocks/bars` with `timeframe=1Min`, `limit=10000`, `next_page_token` paging, `adjustment=split`. Page size and paging match doc 07 [S1–S4]; **UNVERIFIED** on this date beyond that (the API reference page wasn't re-read). The downloader fails loudly on an unexpected response.
- Bar `t` is the bar's start time (UTC). FlowForge stamps Nautilus bars at start + 1 minute so nothing is seen before it closes.
- Licence: using Alpaca data for decisions executed at another broker is **UNVERIFIED** (doc 07). Backtests place no orders.

## IBKR Pro Fixed commissions, US stocks [I1]
- USD 0.005 per share, minimum USD 1.00 per order, maximum 1% of trade value; if the 1% maximum is below the minimum, the maximum is charged.
- Regulatory pass-through on Fixed: SEC fee USD 0.0000206 × value of sales; FINRA TAF USD 0.00 per share sold (fee holiday 2026-10-01 to 2026-12-31). Historical periods had a non-zero TAF; it's left out (cents per trade at our sizes).
- On a $10,000 account with the 10% notional cap, a round trip costs at least $2 on ~$1,000, about 20 bps. That dominates small-account results.

## What 1-minute bars can't model
- No quotes, so no spread, NBBO deviation or staleness checks; fills are at bar prices with one tick of slippage on market and stop orders (Nautilus `OneTickSlippageFillModel`), and take-profit limits fill when touched (optimistic). The report adds a stress test with extra slippage per side.
- No queue position, partial fills, halts or LULD.

## Sources
- [A1] https://docs.alpaca.markets/docs/about-market-data-api
- [A2] https://docs.alpaca.markets/us/docs/market-data-faq
- [A3] https://docs.alpaca.markets/docs/historical-stock-data-1
- [I1] https://www.interactivebrokers.com/en/pricing/commissions-stocks.php
