# 07 — Market-data alternatives to Massive Stocks Advanced ($199/mo)

Research Scout, accessed 2026-10-19. Each claim cites a source [S#] (list at the end). All sources were accessed 2026-10-19 with WebSearch/WebFetch. We did not sign up or use any keys. **Changes since this brief was first written:** execution is now **Interactive Brokers (IBKR)**, not Robinhood, and the framework is **NautilusTrader**. The recommendation below assumes both.

Baseline (see `03-massive-market-data.md`): Massive Stocks Advanced is $199/mo, non-pro. It gives real-time SIP trades (exchange id, TRF id, conditions) and NBBO over WebSocket with **ms** timestamps, plus REST and flat-file tick history back to 2003 with **ns** timestamps. It has **no depth of book**, and NautilusTrader has **no adapter** for it.

## TL;DR answers

1. **Can we drop the paid feed entirely?** **No, not if we want a real watchlist.** IBKR's API does deliver consolidated, unsampled tick-by-tick trades (with exchange and special conditions) and bid/ask. It still costs about **$4.50–$14.50/mo** in exchange subscriptions, because free TWS data is not delivered to the API. IBKR also caps tick-by-tick at **5 simultaneous subscriptions** per 100 market-data lines. Trades and bid/ask are separate subscriptions, so that is about **2 symbols** of full tape plus NBBO. Timestamps are **1-second** resolution, and history is only 3 years at 1,000 ticks per request. That works for a 1–2 symbol pilot, not for a scanner-plus-tape strategy. [S20][S21][S22][S24][S25]
2. **Cheapest option that meets every requirement: Alpaca Algo Trader Plus, $99/mo.** It covers the full SIP ("All US Stock Exchanges", CTA+UTP, "100% of market volume") with an unlimited-symbol WebSocket. Trades carry exchange `x`, conditions `c[]` and tape `z`, with RFC-3339 **ns** timestamps. Quotes are **NBBO** (Alpaca staff), and corrections and cancels are pushed automatically. REST history runs from 2016 at 10,000 req/min. [S1][S2][S3][S4]
3. **Best value: Alpaca Algo Trader Plus ($99) for the tape, plus IBKR for execution and optional depth.** IBKR data costs about $14.50/mo before waivers, and Nasdaq TotalView adds $16.50 for ≤3 depth symbols. Total is about **$99–$130/mo**, versus $199 for Massive alone, and that combination includes depth.
4. **Affordable real depth of book? Yes, but only through IBKR, and narrowly.** Non-pro prices are Nasdaq TotalView-OpenView **$16.50/mo**, NYSE OpenBook **$25**, NYSE ArcaBook **$11** and Cboe BZX L2 **$8**. The API allows **3 simultaneous depth subscriptions** at the base 100 lines. It is price-level depth with MPID / exchange per row, "sent without sampling or filtering", but **odd lots are excluded**. It is **not** MBO. True MBO (Databento XNAS.ITCH live) is **commercial-entity only**: "Nasdaq doesn't permit non-professional or individual users to start a license." [S20][S26][S27][S11][S9]
5. **Recommended combo (~$115–$130/mo):** live tape and NBBO from **Alpaca Algo Trader Plus ($99)**; execution, depth on the 1–3 active names, and a cross-check tick stream from **IBKR** (≈$14.50, waivable in part, + TotalView $16.50); backtest history from **Alpaca REST** (2016→, included). Optionally take **one month of Massive Advanced ($199, one-off)** for pre-2016 history or bulk quotes flat files, if its ToS allows (**UNVERIFIED**). The Massive subscription is otherwise **dropped**.

## Comparison table (non-pro, individual, US equities)

| Vendor / plan | Monthly cost (non-pro) | RT trades with venue + conditions | NBBO quotes | Full SIP coverage (incl. TRF) | Depth (L2/MBO) | History (ticks) | Timestamp precision | Licence fit (automated personal) | Nautilus adapter | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| **Massive Stocks Advanced** (baseline) | $199 | Yes (`x`, `trfi`, `c[]`) | Yes (NBBO) | Yes (CTA/UTP + FINRA TRFs) | **None** | Trades+quotes REST & flat files since 2003-09-10 | WS **ms**; REST/files ns | "Non-pros only"; automated-use clause **UNVERIFIED** | **No** (custom adapter) | VERIFIED (see doc 03) |
| **Alpaca Basic** (free) | $0 | IEX only; 30 symbols | IEX BBO, not NBBO | **No** (IEX only, ~4.7% share) | None | SIP history but `end` must be ≥15 min old | ns | n/a | **No** | VERIFIED [S1][S4][S30] |
| **Alpaca Algo Trader Plus** | **$99** | Yes: `x` exchange, `c` conditions, `z` tape, `i` id; corrections (`c`) + cancels/errors (`x`) auto-pushed; LULD, imbalances | **Yes. "Alpaca provides only NBBO quotes"** (staff, 2025-10-27); sizes in round lots | Yes. "All US Stock Exchanges"; CTA+UTP "100% of market volume". TRF code mapping (`D`?) **UNVERIFIED** | None (no per-exchange quotes) | REST since 2016, 10k req/min, 10k rows/page; no flat files documented | RFC-3339 **ns** (WS & REST) | Pro/non-pro and third-party-execution terms **UNVERIFIED**. Needs an Alpaca account (you already have paper) | **No** (custom adapter) | Price/coverage VERIFIED; licence **UNVERIFIED** [S1][S2][S3][S4][S5][S6] |
| **IBKR** (US Securities Snapshot & Futures Value Bundle + US Equity & Options Add-On Streaming) | $10 (waived for activity; $30-commission threshold shown only as an example) + $4.50 = **$4.50–$14.50** | Yes, via `reqTickByTickData` AllLast: `exchange`, `specialConditions`, pastLimit/unreported flags; AllLast adds average-price/derivative/combo trades | Yes, via BidAsk tick-by-tick (separate subscription) | Yes: bundle = Network A+B+C L1 (consolidated) + OPRA. Odd-lot coverage in AllLast **UNVERIFIED** | Optional add-ons (below) | `reqHistoricalTicks`: last **3 years**, max **1,000 ticks/request**, one session per request, pacing limits | Tick-by-tick `time` is epoch **seconds** (doc samples); Nautilus says historical ticks have "microsecond precision" (conflict, **UNVERIFIED**) | Non-pro = "any natural person" not registered/advising → fits | **Yes (stable)** | Prices VERIFIED; waiver amount partly **UNVERIFIED** [S20][S21][S22][S23][S24][S25][S28] |
| IBKR **limits** | Quote Booster $30 per +100 lines (max 10) | **5 tick-by-tick subs per 100 lines** (5% of lines); 1 request per instrument per 15 s; `reqMktData` is **sampled every 250 ms** (not usable for tape) | — | — | **3** depth subs at ≤400 lines | — | — | Lines = max(100, commissions/8, equity×100/$1M) | — | VERIFIED [S22][S24][S25][S29] |
| IBKR **depth add-ons** | TotalView-OpenView **$16.50**; NYSE OpenBook **$25**; ArcaBook **$11**; Cboe BZX L1/L2 **$8** | — | — | Single venue each | Price-level by MPID/exchange, "without sampling or filtering", **odd lots excluded**; SMART depth aggregates venues. Not MBO | None | — | Non-pro | Yes (L2 "where available") | VERIFIED [S20][S26][S28] |
| **Databento Standard** | $199 (no licence fees) | Live US equities = **EQUS.MINI only**: "proprietary blend" of some exchanges/ATSs, "subset of US equities exchanges"; `side` aggressor field; **no SIP sale-condition codes**; TRF inclusion not stated | **Synthetic** "mini" NBBO, not the official NBBO | **No** | None live (Standard) | L0 16+ yrs, L1 12 mo, L2/L3 1 mo included; more pay-as-you-go ($/GB) | **ns** (4 timestamps) | Personal use OK, ≤2 devices | **Yes (stable)** | VERIFIED [S7][S8][S10][S12][S13][S14] |
| **Databento Plus / Unlimited** | $1,750 / $4,500 licence fees/mo, annual contract | XNAS.BASIC (Nasdaq + Nasdaq TRFs) live; prop feeds | Per-venue BBO | Partial (NYSE TRF "coming soon") | XNAS.ITCH MBO **commercial entities only** | 16+ yrs (Unlimited) | ns | Not for individuals | Yes | VERIFIED; out of budget [S7][S9][S11][S15] |
| **Databento usage-based (history only)** | Pay per GB (from $0.40/GB) | Historical per-venue prop feeds incl. MBO (XNAS.ITCH history) | — | Must stitch venues; no SIP | **Historical MBO yes** | XNAS.ITCH etc. | ns | Historical OK | Yes (DBN loader) | VERIFIED (rate card per dataset **UNVERIFIED**) [S7][S10][S12] |
| Schwab Trader API | $0 with a Schwab account | Level One equities (last trade + exchange fields) only; **no equity time-and-sales stream** in client docs | L1 quote (consolidated? **UNVERIFIED**) | **UNVERIFIED** | NASDAQ_BOOK / NYSE_BOOK L2 | Minute bars only | **UNVERIFIED** | Needs a second brokerage; official docs login-gated | No | **UNVERIFIED** (third-party docs only) [S16][S17] |
| Tradier | Bundled with a Tradier brokerage account (**UNVERIFIED**) | `timesale` stream has `exch`, `seq`, `flag`, cancel/correction | Per-exchange quote events | **UNVERIFIED** | None | **UNVERIFIED** | ms (example only) | Needs a Tradier brokerage account → ruled out | No | Partly VERIFIED [S18] |
| EODHD WebSocket | Paid plans (price not on page) | Trades: **Cboe EDGX only**, conditions "always empty", no exchange | EDGX top of book, "not a national best bid and offer" | **No** | No | — | ms | — | No | VERIFIED → **ruled out** [S19] |
| Twelve Data Grow/Pro | $79 / $229 | Not documented (price-level WS) | Not documented | Not documented | No | — | — | — | No | Ruled out (no tick/condition data documented) [S31] |
| Tiingo Power | $30 individual | "IEX Feed" (IEX-only) | IEX | **No** | No | — | — | Personal internal use | No | Ruled out (IEX) [S32] |
| Finnhub | **UNVERIFIED** (pricing page is JS-only) | **UNVERIFIED** | **UNVERIFIED** | **UNVERIFIED** | — | — | — | — | No | **UNVERIFIED**; low priority [S33] |
| Webull / moomoo OpenAPI | Tied to own brokerage | Webull: MQTT-over-WS streaming; field detail not on page. moomoo: 6 exchange L2 feeds | **UNVERIFIED** | **UNVERIFIED** | moomoo L2 (price **UNVERIFIED**) | — | — | Needs that brokerage account → ruled out | No | **UNVERIFIED** [S34][S35] |
| Robinhood MCP `get_equity_quotes` / `get_equity_price_book` | $0 | **No** tick stream (request/response quotes, ≤20 symbols; L2 ≤4 symbols) | Snapshot only | n/a | Snapshot L2 | OHLCV only | n/a | Moot (broker changed to IBKR) | No | VERIFIED from doc 01 |

### Why IEX-only data is insufficient
On 2026-10-16, IEX had **4.67%** of US equity volume. FINRA TRFs (off-exchange) had **49.35%**, Nasdaq 14.68%, NYSE group 20.03% and Cboe 8.77% (Cboe market-volume summary, one day) [S30]. Alpaca's own example shows AAPL on 2023-09-29 with 12,630 IEX trades against more than 535,136 total [S4]. An IEX-only stream misses about 95% of prints, all TRF and dark activity, and ISO sweeps across other venues. Its BBO is IEX's own quote, not the NBBO, so quote-rule classification would be wrong. That rules out IEX-only feeds: Alpaca Basic, Tiingo and IBKR's free Cboe One/IEX. The IBKR free feed is also "non-consolidated", and TWS-free data "may not be available in the API" [S20][S23].

## Recommendation (assuming IBKR execution + NautilusTrader)

| Role | Source | Cost/mo | Why |
|---|---|---|---|
| Live tape + NBBO (watchlist, aggressor classification, delta, sweeps, TRF share) | **Alpaca Algo Trader Plus** | $99 | Full SIP, unlimited symbols, ns timestamps (better than Massive's ms WS), conditions + exchange, auto corrections/cancels |
| Execution, account, positions | **IBKR** (Nautilus IB adapter, dockerized Gateway) | commissions | Already chosen |
| Required IBKR L1 data (needed for API orders/quotes anyway) | Snapshot bundle + Add-On Streaming | $4.50–$14.50 | Lets IB tick-by-tick act as a **cross-check** on 1–2 active names and as a failover tape |
| Depth on active names (nice-to-have) | IBKR Nasdaq TotalView-OpenView (+ ArcaBook $11 for ETFs if needed) | $16.50 (+$11) | Only affordable non-pro L2; ≤3 concurrent depth subs |
| Backtest history | Alpaca REST (2016→) included; optional Databento usage-based for MBO research samples; optional one-off Massive Advanced month for pre-2016/flat files | $0 incl. (+ one-offs) | Avoids recurring $199 |
| **Total recurring** | | **≈ $104–$141** (typ. **~$120**) | vs $199 Massive alone with no depth |

Choose **Massive instead of Alpaca** only if (a) the Alpaca market-data ToS forbids using the data to drive orders at another broker, or (b) the Alpaca bulk-history pull proves too slow or incomplete for quotes. **Databento Standard** suits futures or MBO research. For US-equity tape reading it is the wrong product, because EQUS.MINI is not the SIP and has no condition codes.

## Design implications (ingest changes vs doc 03)

1. **Write a custom NautilusTrader `LiveDataClient` for Alpaca.** Nautilus has stable adapters only for Databento and IB, not Alpaca or Polygon/Massive [S36]. That is the main integration cost (the same cost applies to Massive). Map trades to `TradeTick` with `aggressor_side=NO_AGGRESSOR` and fill it in a classifier actor. Keep venue and conditions in a custom data type or `info` side table, because `TradeTick` has no conditions field.
2. **Plan for one WS connection.** Most Alpaca subscriptions allow one connection per endpoint; a second gets 406. Slow clients get 407 and are disconnected. Use msgpack (`Content-Type: application/msgpack`) and RFC-7692 compression [S5].
3. **Timestamps.** Alpaca gives ns RFC-3339 strings, so parse them to int64 ns. This removes the ms-precision caveat from doc 03. IB ticks are seconds, so never mix IB and Alpaca ticks in one classifier stream. Use IB ticks only for reconciliation.
4. **Corrections and cancels are first-class WS messages** (`c`, `x`) on Alpaca. Apply them to the delta and footprint state in real time instead of waiting for nightly re-derivation.
5. **Condition and exchange tables.** On start, load `/v2/stocks/meta/conditions` and `/v2/stocks/meta/exchanges`, and confirm the TRF code. Port doc 03's exclude-list logic to SIP letter codes. Alpaca uses SIP letters such as `I` (odd lot), `F` (ISO) and `W` (average price), not Massive's numeric ids [S4][S6].
6. **NBBO sizes are round lots** on Alpaca. Massive's docs say "shares". Normalise this before computing top-of-book imbalance [S3].
7. **IB constraints.** Budget tick-by-tick slots: each symbol needs AllLast + BidAsk, so 2 of 5 slots per symbol. Allow 1 request per instrument per 15 s, and keep depth subs ≤3. Raise lines with Quote Booster packs ($30 per +5 TBT) only if needed. Set TWS/Gateway timestamps to UTC (Nautilus requirement) [S24][S25][S37].
8. **Depth handling.** IB L2 resets with error 317, after which you must clear the book. Rows are price-level with MPID, and odd lots are excluded. Model features as "displayed round-lot depth on Nasdaq", not a full book [S26].
9. **Backtest store.** Do a one-time Alpaca REST bulk pull (10k rows/page, 10k req/min) to Parquet in the Nautilus catalog. Keep the Massive flat-file loader design as an optional path.

## Open questions

1. **Alpaca market-data ToS.** Is using Algo Trader Plus data to trade at IBKR permitted? Does an Alpaca account with paper-only status qualify? What is the pro/non-pro classification? (**UNVERIFIED**; read the Alpaca Market Data Agreement or ask support.)
2. **Alpaca TRF identification.** Which exchange code marks FINRA TRF prints? Is a TRF id (Carteret / Chicago / NYSE TRF) exposed? (**UNVERIFIED**; needs a call to `/v2/stocks/meta/exchanges`.)
3. **Alpaca historical quotes.** Do they start in 2016 like trades? How long does a bulk pull of quotes take for about 50 symbols? (**UNVERIFIED**)
4. **IB waiver.** What is the exact commission threshold that waives the $10 Snapshot bundle? Does the free Cboe One/IEX feed reach the API at all? (Pricing page example shows $30; **UNVERIFIED** for this bundle.)
5. **IB tick-by-tick details.** Does AllLast include odd lots? What is the real timestamp resolution: seconds per the samples, or µs per the Nautilus docs? (**UNVERIFIED**; measure on the paper account.)
6. **Massive ToS.** Can we take a one-month Advanced plan only to download history? (**UNVERIFIED**)
7. **Schwab** official streamer service list and terms. The docs are login-gated (**UNVERIFIED**). Low priority now that IBKR is the broker.

## Sources (all accessed 2026-10-19)

- [S1] Alpaca — About Market Data API (plans, $99, SIP, history since 2016): https://docs.alpaca.markets/us/docs/about-market-data-api (also https://docs.alpaca.markets/docs/about-market-data-api)
- [S2] Alpaca forum — "Alpaca provides only NBBO quotes" (staff, 2025-10-27): https://forum.alpaca.markets/t/we-need-per-exchange-quote-from-sip/18019 ; and https://forum.alpaca.markets/t/does-alpaca-have-nbbo-quotes-from-all-exchanges/14055/8
- [S3] Alpaca — Real-time Stock Data (WS URLs, trade/quote/correction/cancel schemas): https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data
- [S4] Alpaca — Market Data FAQ (ns timestamps, IEX vs SIP example, 15-min rule): https://docs.alpaca.markets/us/docs/market-data-faq
- [S5] Alpaca — WebSocket Stream (connection limit, 406/407/409, msgpack): https://docs.alpaca.markets/us/docs/streaming-market-data
- [S6] Alpaca — Historical trades / exchange codes / condition codes: https://docs.alpaca.markets/us/reference/stocktrades-1 ; https://docs.alpaca.markets/us/reference/stockmetaexchanges-1 ; https://docs.alpaca.markets/us/reference/stockmetaconditions-1
- [S7] Databento — Pricing: https://databento.com/pricing
- [S8] Databento — US Equities Mini blog (2025-02-01): https://databento.com/blog/databento-us-equities-mini-now-available
- [S9] Databento — Nasdaq TotalView-ITCH live (commercial only): https://databento.com/blog/nasdaq-totalview-live
- [S10] Databento — Equities overview: https://databento.com/equities
- [S11] Databento — Live data licensing/fees: https://databento.com/docs/portal/live-data
- [S12] Databento — EQUS.MINI dataset docs & catalog: https://databento.com/docs/venues-and-datasets/equs-mini ; https://databento.com/catalog/us-equities/EQUS.MINI/equities/MSFT
- [S13] Databento — Introducing US Equities (2025-01-13): https://databento.com/blog/introducing-databento-us-equities
- [S14] Databento — Finding an equities dataset: https://databento.com/docs/examples/equities/equities-introduction/finding-an-equities-dataset
- [S15] Databento — US equities catalog; Live Nasdaq TRF example: https://databento.com/catalog/us-equities ; https://databento.com/docs/examples/basics-live/live-nasdaq-trf
- [S16] schwab-py streaming docs (third-party): https://schwab-py.readthedocs.io/en/latest/streaming.html
- [S17] dp_exchange_schwab usage rules (third-party): https://repo.hex.pm/preview/dp_exchange_schwab/0.1.7/usage-rules.md
- [S18] Tradier — Streaming data: https://docs.tradier.com/docs/streaming-data ; https://docs.tradier.com/docs/streaming
- [S19] EODHD — Real-time WebSockets: https://eodhd.com/financial-apis/new-real-time-data-api-websockets
- [S20] IBKR — Market Data Pricing (non-pro prices, waivers, non-pro definition, free Cboe One/IEX non-consolidated): https://www.interactivebrokers.com/en/pricing/market-data-pricing.php
- [S21] IBKR — Popular market data subscriptions (Add-On bundle = Network A/B/C + OPRA, requires Snapshot bundle): https://www.interactivebrokers.com/docs/general/market-data-subscriptions/popular-market-data-subscriptions/introduction
- [S22] IBKR — Live ticks / tick-by-tick intro (5% of lines, AllLast fields): https://www.interactivebrokers.com/docs/tws-api/doc/md/live-ticks.md ; https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/tick-by-tick-data/introduction ; https://interactivebrokers.github.io/tws-api/tick_data.html
- [S23] IBKR — TWS data vs API data; error 10089: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/tws-data-vs-api-data ; https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/common-error-resolution/requested-market-data-requires-additional-subscription-for-api-see-link-in-market-data-connections-dialog-for-more-details-delayed-market-data-is-available
- [S24] IBKR — reqMktData sampled 250 ms: https://interactivebrokers.github.io/tws-api/top_data.html
- [S25] IBKR — Historical ticks (3 years, 1000/request): https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-ticks.md
- [S26] IBKR — Market depth API (no sampling, odd lots excluded, MPID, error 317): https://www.interactivebrokers.com/docs/tws-api/doc/md/market-depth.md
- [S27] IBKR — TWS Market Depth (max 3 windows): https://www.interactivebrokers.com.hk/php/webhelp/TWS_Reference_Information/Market_Depth.htm
- [S28] IBKR — Market data lines (100 default, formula, Quote Booster $30): https://interactivebrokers.com/en/index.php?f=14193
- [S29] IBKR — Specialized market data lines (TBT/depth table): https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-lines/specialized-market-data-lines
- [S30] Cboe — U.S. Equities Market Volume Summary (2026-10-16): https://www.cboe.com/us/equities/market_statistics
- [S31] Twelve Data — Pricing: https://twelvedata.com/pricing
- [S32] Tiingo — Pricing: https://www.tiingo.com/about/pricing
- [S33] Finnhub — pricing / WS docs (content not retrievable): https://finnhub.io/pricing ; https://finnhub.io/docs/api/websocket-trades
- [S34] Webull — Market Data API overview: https://developer.webull.com/apis/docs/market-data-api/overview/
- [S35] moomoo — Level 2 quotes article: https://www.moomoo.com/us/learn/mastering-level-2-streaming-quotes-for-us-stocks
- [S36] NautilusTrader — Integrations list: https://nautilustrader.io/docs/latest/integrations
- [S37] NautilusTrader — IB and Databento integration docs: https://nautilustrader.io/docs/latest/integrations/ib/ ; https://nautilustrader.io/docs/latest/integrations/databento
- Internal: `03-massive-market-data.md` (baseline), `01-robinhood-trading-mcp.md` (Robinhood quote tools).
