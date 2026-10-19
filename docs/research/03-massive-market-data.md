# 03 — Massive.com (formerly Polygon.io) as FlowForge market-data source

Research Scout, accessed 2026-10-19. Every claim cites a source in the [Sources](#sources) list as [S#]. All sources were accessed on 2026-10-19 with WebFetch/WebSearch. We did not sign up and used no API keys, so the live API responses were not checked (e.g. `/v3/reference/exchanges` output).

## TL;DR

- **Recommended plan: Stocks Advanced, $199/mo (billed monthly; 20% off annual, exact annual price not shown) [S2].** It is the only Individual stocks plan with **real-time** trades **and** NBBO quotes over WebSocket, plus REST tick history and trades/quotes flat files back to 2003-09-10 [S2][S5][S6][S7][S9][S11].
- **Advanced is "Non-pros only" [S2][S16].** If FlowForge trades through an LLC or other entity, it counts as **professional** [S17] and needs **Stocks Business ($2,499/mo)**. Business does **not** include intraday ticks. Real-time trades and quotes require an exchange expansion, for example "Full Market" at +$1,999/mo plus exchange fees [S15].
- **No Level 2 / depth-of-book for US stocks.** Massive has no order-book endpoint or WebSocket channel for any asset class [S13]. Only top-of-book NBBO plus the consolidated trade tape are available.
- WebSocket timestamps are **milliseconds** [S5][S6]. REST and flat-file timestamps are **nanoseconds** [S7][S8][S9][S11].

## Findings table

| # | Question | Finding | Status | Source |
|---|---|---|---|---|
| 1 | Rebrand date | Polygon.io → Massive effective **Oct 30, 2025, 4 PM ET** | VERIFIED | S1, S19 |
| 2 | api.polygon.io still works? | At announcement it "continue[s] to work for an extended period". Retirement was planned "in the new year" with advance notice. The 2026 changelog has no retirement entry. Status as of 2026-10-19 not confirmed. | VERIFIED (as of announcement); current status **UNVERIFIED** | S1, S20 |
| 3 | New hostnames | REST `api.massive.com`. WS real-time `wss://socket.massive.com/stocks`. WS delayed `wss://delayed.massive.com/stocks`. Flat files S3 endpoint `https://files.massive.com`, bucket `flatfiles`. | VERIFIED | S1, S4, S10 |
| 4 | API keys after rebrand | "Existing API keys remain valid" | VERIFIED | S1 |
| 5 | Plan with real-time trades + NBBO over WS | **Stocks Advanced $199/mo** (real-time T and Q). Developer $79 has trades at **15-min delay** and no quotes. Starter $29 is 15-min delayed with no trades or quotes. Basic is free, end-of-day only. | VERIFIED | S2, S5, S6 |
| 6 | Individual vs business / pro | All four Individual plans are for non-pro, personal, non-business use. Advanced is labelled "Non-pros only". Professional is the default if the questionnaire is unclear. An LLC used for own trading counts as professional for US stocks. | VERIFIED | S2, S16, S17 |
| 7 | Business plan | Stocks Business $2,499/mo. Trades and quotes only "after end-of-day". "Intraday ticks are available with exchange expansions": Full Market $1,999, Nasdaq Basic $1,999, Cboe EDGX $1,999, IEX $499, Full Market Delayed $499, NYSE Imbalances $399, and "additional exchange fees apply". Up to 3 concurrent WS connections plus 1 per expansion. | VERIFIED | S15 |
| 8 | WS channels (stocks) | `T` trades, `Q` quotes (NBBO), `A` per-second aggs, `AM` per-minute aggs, `LULD`, `NOI` (imbalances; NYSE; Imbalances Expansion $49/mo), `FMV` (Business only) | VERIFIED | S3 |
| 9 | WS connection limits | "By default, one concurrent WebSocket connection per asset class". Contact support for more. A slow consumer can be disconnected. There is no subscription cap on stocks (only options quotes are capped at about 1,000 contracts). `T.*` subscribes to all tickers. | VERIFIED | S4, S18, S12 |
| 10 | WS trade fields | `ev, sym, x (exchange), i, z (tape), p, s, ds, c[], t (SIP ms), pt (participant ms), q (seq), trfi (TRF id), trft (TRF ts ms)` | VERIFIED | S5 |
| 11 | WS quote fields | `ev, sym, bx, bp, bs, ax, ap, as, c, i[], t (SIP ms), pt, q, z`. The feed is NBBO. Bid or ask fields are omitted when that side is absent. Docs do not say whether `bs`/`as` are shares or round lots (the page says "number of shares"). | VERIFIED (lot semantics **UNVERIFIED**) | S6 |
| 12 | Condition codes reference | `GET /v3/reference/conditions` (filters `asset_class`, `data_type`, `sip`, `id`). Results include `sip_mapping` (CTA/UTP) and `update_rules.{consolidated,market_center}.{updates_high_low, updates_open_close, updates_volume}`. Also a static glossary table. | VERIFIED | S7a, S14 |
| 13 | Exchanges reference | `GET /v3/reference/exchanges`. Fields `id, type (exchange/TRF/SIP), asset_class, locale, name, acronym, mic, operating_mic, participant_id` | VERIFIED (field list); live id list **UNVERIFIED** | S7b |
| 14 | Off-exchange / TRF identification | Massive's own blog: `exchange == 4` means a FINRA TRF report. `trf_id` gives the specific TRF (201/202/203). FINRA ORF (OTC) is exchange 62, which has no participant timestamp. | VERIFIED (blog + docs). Full TRF id↔name mapping **UNVERIFIED** (needs a live `/v3/reference/exchanges` call) | S8b, S5, S7 |
| 15 | ISO | Condition **14 Intermarket Sweep** (SIP `F`). Updates high/low, last and volume. | VERIFIED | S14 |
| 16 | Odd lots | Condition **37 Odd Lot Trade** (SIP `I`). Does not update high/low or last. Counts in volume. | VERIFIED | S14 |
| 17 | Average-price / derivative / other excludes | **2 Average Price (W)**, **10 Derivatively Priced (4)**, **7 Cash Sale**, **20 Next Day**, **22 Prior Reference Price**, **12 Form T**, **13 Extended Hours (Sold Out of Sequence)**, **33 Sold Out of Sequence**, **52 Contingent**, **53 QCT**, **15/16 official close/open** (no volume), **38 Corrected Consolidated Close** | VERIFIED (glossary). Must confirm against the live endpoint's `update_rules`. | S14, S7a |
| 18 | REST trades | `GET /v3/trades/{ticker}`. Fields `price,size,decimal_size,exchange,id,conditions,sequence_number,sip_timestamp, correction?,participant_timestamp?,tape?,trf_id?,trf_timestamp?`. limit ≤ 50,000, paginated by `next_url`. ns timestamps. History back to 2003-09-10. Developer: 10 yrs, delayed. Advanced: all history, real-time. | VERIFIED | S7 |
| 19 | REST quotes (NBBO) | `GET /v3/quotes/{ticker}`. ns timestamps. Back to 2003-09-10. **Advanced only** among Individual plans. | VERIFIED | S9 |
| 20 | Flat files | S3 `files.massive.com`, bucket `flatfiles`, e.g. `us_stocks_sip/trades_v1/2025/11/2025-11-05.csv.gz`. Format csv.gz with a header row. Separate S3 access key and secret. Updated **11 AM ET** for the prior day. Trades files: Developer (10 yrs) / Advanced / Business (all). Quotes files: Advanced / Business only. Quotes path prefix not shown (likely `quotes_v1`, **UNVERIFIED**). | VERIFIED (path for quotes **UNVERIFIED**) | S10, S8, S11 |
| 21 | Level 2 / depth of book | **Not available.** "no order-book endpoint or order-book WebSocket channel for stocks, options, futures, forex or crypto". Closest offerings are NBBO and the trade tape. | VERIFIED | S13 |
| 22 | Data source | Consolidated SIPs (CTA, UTP) plus FINRA TRFs (NYSE TRF, Nasdaq TRF Carteret and Chicago) and OTC reporting | VERIFIED | S21 |
| 23 | Timestamps | SIP `t`/`sip_timestamp`. Participant `pt`/`participant_timestamp` is typically ≤ SIP and absent on ORF/62. TRF `trft`/`trf_timestamp`. **REST/flat files: ns. WS: ms.** | VERIFIED | S5, S6, S7, S8, S9 |
| 24 | Python client | PyPI **`massive` v2.9.0 (Oct 16, 2026)**, Python ≥3.10, MIT, repo `massive-com/client-python`. Provides `RESTClient` and `WebSocketClient`. Defaults to `api.massive.com`. | VERIFIED | S22, S23 |
| 25 | MCP server `mcp_massive` | 3 tools: `search_endpoints`, `call_api` (any REST endpoint), `query_data` (SQL over a local SQLite store). stdio/sse/streamable-http. Hosted at `mcp.massive.com`. "experimental and could be subject to breaking changes". No WebSocket streaming. Pinned install `v0.10.0`. MIT. → **Research/agent use only, not production ingest.** | VERIFIED (facts); "not for production" is our inference | S24 |
| 26 | Recent data-quality notes | 2026-09-22: Tape B (e.g. SPY, IWM) trades and quotes were mis-tagged as tape 1. Real-time is correct since 2026-09-07. **Historical data before 2026-09-21 still shows tape 1.** 2026-02-23: fractional `decimal_size`/`ds` added. Flat-file trades `size` is now decimal (old files unchanged). | VERIFIED | S20 |
| 27 | Automated trading on an Individual (non-pro) plan permitted? | The pro/non-pro articles do not discuss automated trading. Personal own-account trading appears to be non-pro. | **UNVERIFIED** (read Market Data ToS S25) | S16, S17 |
| 28 | Message rate for full `T.*` + `Q.*` firehose | No bandwidth or msgs/sec figure published. Quotes run about 2× the trade event rate. | **UNVERIFIED** (measure) | S18 |

## Order-flow concepts: directly measured vs inferred (trades + NBBO only)

| Concept | Measured / Inferred / Unavailable | How, and caveats |
|---|---|---|
| Aggressor side (buy/sell initiator) | **Inferred** | Not in the feed. Classify by comparing trade price to the prevailing NBBO (quote rule / Lee-Ready / EMO / bulk-volume). In real time, WS timestamps are ms only, so trade↔quote alignment is coarse. Use arrival order plus the per-ticker `q`. Historically, use ns `sip_timestamp`. Off-exchange (exch 4) prints are reported late and many are midpoint prints, so classification is unreliable. Tag them as "unknown" or classify separately. |
| Delta / cumulative delta | **Inferred** | Sum of signed volume from the aggressor classification. Inherits its error. Exclude non-regular prints (see conditions). |
| Footprint (volume at price × side) | **Inferred** (volume at price is measured; side is inferred) | Price and size are exact per print. Bid/ask split is inferred. |
| Absorption | **Inferred** | Heuristic: large aggressive volume with little NBBO price progress, and NBBO size refreshing. NBBO size is only the top level across venues, with no queue view. |
| Icebergs / hidden liquidity | **Inferred (weak)** | Repeated fills at one price beyond the displayed NBBO size suggest reserve or hidden orders. No order IDs and no depth. Low confidence. |
| Depth / order book imbalance | **Unavailable** beyond L1 | Only NBBO best bid/ask price, size and venue [S13]. Top-of-book imbalance (bs vs as) is measured. Deeper levels are not. |
| Sweeps | **Measured (ISO flag)** + inferred clustering | Condition 14 marks ISO prints. Group by ms timestamp, multiple venues (`x`) and same side to reconstruct sweeps. Exact sweep grouping is inferred. |
| Off-exchange share | **Measured** | `exchange == 4` (FINRA TRF) plus `trf_id`. Share = TRF volume / total eligible volume. Exclude ORF/62 (OTC securities). |
| Block prints | **Measured** | Size and notional per print. Filter non-regular conditions. |
| Auctions / opening and closing prints | **Measured** | Conditions 8, 17, 19, 25, 28. NOI imbalances need the add-on. |
| Halts / LULD | **Measured** | `LULD` channel, quote conditions and indicators. |

## Design implications for the ingest service

1. **One WS connection per asset class (Individual)** [S4]. The ingest service must be a single connection owner that fans out internally (e.g. to Redis Streams or NATS). Do not let research tools or the MCP server open a second real-time connection. Business gets 3 or more [S15].
2. **Slow consumers get disconnected** [S4]. Do a minimal decode on the socket thread and push to a bounded queue with backpressure metrics. Subscribe to a watch-list instead of `T.*,Q.*` unless throughput is measured (no published rate; Q events run at about 2× T [S18]).
3. **Gap handling**: `q` is per-ticker, increasing and non-consecutive [S5][S6], so it **cannot detect drops**. On reconnect, backfill via REST `/v3/trades` and `/v3/quotes` with `timestamp.gte` over the gap window, then de-duplicate by (ticker, `q`/`id`). End-of-day reconcile against flat files (available 11 AM ET next day) [S8].
4. **Timestamp normalisation**: store ns `int64` throughout. WS ms values ×1e6 are flagged `ts_precision=ms`. Keep `sip_ts`, `participant_ts` and `trf_ts` separately. Sort and join on SIP time (the common clock). Participant time is ≤ SIP time and missing on ORF.
5. **Condition filter layer**: on startup, load `/v3/reference/conditions` (stocks, trade and quote) and `/v3/reference/exchanges`, and cache them. Drive "eligible for delta/footprint" from `update_rules` plus an explicit exclude list {2, 7, 10, 12, 13, 15, 16, 20, 22, 33, 38, 52, 53} and an odd-lot (37) policy. Odd lots are real executions but do not update last. Keep them for volume and flag them for price.
6. **Corrections**: REST and flat files carry `correction`. WS has none documented. Plan a nightly re-derivation of features from flat files to fix late corrections and cancels.
7. **Fractional shares**: use `decimal_size`/`ds` [S20]. Do not assume integer `size` in new flat files.
8. **Tape bug**: do not trust `tape` on historical data before 2026-09-21 for Tape B names. Derive tape from the listing exchange [S20].
9. **Backtesting store**: bulk-load trades and quotes flat files (csv.gz) to Parquet, partitioned by date and ticker. Quotes are very large, so start with a ticker subset. REST is for gaps only.
10. **Use the official `massive` Python client (2.9.0)** or a raw `websockets` client. Pin the version. Point at `api.massive.com` and `socket.massive.com`, not the polygon hostnames, which are slated for retirement.
11. **MCP server** (`mcp_massive`): for agent-assisted research and ad-hoc queries only. It is experimental, REST-only, with no streaming [S24].
12. **Licensing gate**: record the entity decision (personal vs LLC) before go-live. It changes the cost from $199/mo to ≥$4,498/mo plus exchange fees [S2][S15][S17].

## Open questions

1. Is api.polygon.io still live on 2026-10-19, and is there a hard retirement date? (**UNVERIFIED**)
2. Does an automated, own-account trading bot on an Individual plan comply with the Market Data ToS? Read S25 and/or ask Massive. (**UNVERIFIED**)
3. What is the full live mapping of `/v3/reference/exchanges` ids (4, 62, TRF ids 201/202/203, and lit venues)? Needs an API key. (**UNVERIFIED**)
4. Are WS NBBO `bs`/`as` in shares or round lots? Was the 2025 round-lot definition change (MDI) reflected? (**UNVERIFIED**)
5. Does WS ever deliver corrections or cancels? (**UNVERIFIED**)
6. What is the flat-file quotes key prefix (`us_stocks_sip/quotes_v1/...`)? (**UNVERIFIED**)
7. What is the realistic events/sec and bandwidth of `T.*` + `Q.*`, and what is the end-to-end latency (socket → SIP `t` delta)? Measure in a trial.
8. Can an Individual Advanced account get a second WS connection (e.g. hot standby) via support? (**UNVERIFIED**)
9. What is the exact annual price of Advanced (20% off implies about $159/mo, **UNVERIFIED**)?

## Sources

All accessed 2026-10-19.

- S1 — Polygon is now Massive (blog): https://massive.com/blog/polygon-is-now-massive
- S2 — Pricing: https://massive.com/pricing
- S3 — WebSocket Stocks Overview: https://massive.com/docs/websocket/stocks/overview
- S4 — WebSocket Quickstart: https://massive.com/docs/websocket/quickstart
- S5 — WebSocket Stocks Trades: https://massive.com/docs/websocket/stocks/trades
- S6 — WebSocket Stocks Quotes: https://massive.com/docs/websocket/stocks/quotes
- S7 — REST Trades: https://massive.com/docs/rest/stocks/trades-quotes/trades
- S7a — REST Condition Codes: https://massive.com/docs/rest/stocks/market-operations/condition-codes
- S7b — REST Exchanges: https://massive.com/docs/rest/stocks/market-operations/exchanges.md (field list confirmed via https://massive.com/docs/rest/options/market-operations/exchanges.md)
- S8 — Flat Files Stocks Trades: https://massive.com/docs/flat-files/stocks/trades.md
- S8b — Dark pool scanner with Massive (blog, 2026-05-06): https://massive.com/blog/dark-pool-scanner-with-massive
- S9 — REST Quotes: https://massive.com/docs/rest/stocks/trades-quotes/quotes
- S10 — Flat Files Quickstart: https://massive.com/docs/flat-files/quickstart
- S11 — Flat Files Stocks Quotes: https://massive.com/docs/flat-files/stocks/quotes.md
- S12 — Python client README: https://github.com/massive-com/client-python
- S13 — KB: Does Massive offer Level 2 data?: https://massive.com/knowledge-base/article/does-massive-offer-level-2-data (redirect from https://polygon.io/knowledge-base/article/does-polygon-offer-level-2-data)
- S14 — Glossary: Stock conditions & indicators: https://massive.com/glossary/us/stocks/conditions-indicators
- S15 — Stocks API for Business: https://massive.com/business-stocks
- S16 — KB: Pro/non-pro classifications for stock data: https://massive.com/knowledge-base/article/what-are-pro-and-non-pro-classifications-for-massives-stock-data
- S17 — KB: Am I a professional or non-professional subscriber?: https://massive.com/knowledge-base/article/am-i-a-professional-or-non-professional-subscriber
- S18 — KB: How much data is streamed through Massive's WebSockets: https://massive.com/knowledge-base/article/how-much-data-is-streamed-through-massives-websockets
- S19 — Press release (EIN Presswire): https://www.einpresswire.com/article/863823068/polygon-io-is-now-massive
- S20 — Changelog: https://massive.com/changelog
- S21 — KB: Where does Massive's stock data come from?: https://massive.com/knowledge-base/article/where-does-stock-data-come-from
- S22 — PyPI `massive`: https://pypi.org/project/massive/
- S23 — llms.txt docs index: https://massive.com/llms.txt
- S24 — mcp_massive: https://github.com/massive-com/mcp_massive
- S25 — Market Data Terms of Service (not reviewed in depth): https://massive.com/legal/market-data-terms-of-service
