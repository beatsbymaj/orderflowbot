# 10 — Alpaca tick history (trades + NBBO quotes) and the live stream

Accessed 2026-10-25 with WebFetch (the Alpaca API itself is not reachable from the build
container; nothing here was tested against a live response yet). Re-verify monthly.

## Historical trades `GET https://data.alpaca.markets/v2/stocks/trades` [T1]
- Params: `symbols` (required), `start`/`end` (RFC-3339 or date, **both inclusive**), `limit` 1–10,000 (default 1,000; counts across all symbols), `asof`, `feed` (`sip` default, `iex`, `boats`, `otc`), `currency`, `page_token`, `sort` (`asc` default).
- Response: `trades` keyed by symbol → list; `next_page_token` (null on the last page).
- Trade fields: `t` RFC-3339 ns; `x` exchange; `p` price; `s` size (uint32); `c` conditions; `i` exchange trade id (uint64); `z` tape A/B/C/N/O; `u` optional update `canceled` / `incorrect` / `corrected` (absent = valid).
- FlowForge: canceled and incorrect trades are kept in the raw files and left out of the catalog. How a `corrected` record relates to its original is **UNVERIFIED**; it is kept and counted.

## Historical quotes `GET https://data.alpaca.markets/v2/stocks/quotes` [Q1]
- Same params and paging as trades. Response `quotes` keyed by symbol.
- Fields: `t`; `ax`/`ap`/`as` ask exchange/price/size; `bx`/`bp`/`bs` bid; `c` conditions; `z` tape. A price of 0 means no active bid/ask.
- **Sizes are shares from 2025-11-03; before that, round lots.** This corrects doc 07, which said round lots. FlowForge multiplies pre-2025-11-03 sizes by 100 (ADR-0010).

## Minute bars and the gap check [B1][F1]
- Bar `n` = "Trade count in the bar", `v` = "Bar volume".
- FAQ: bars are built from SIP trades with the timestamp truncated to the minute. Volume and trade count go up for every trade except conditions that don't update volume: `M` (official close), `Q` (official open), `9` (corrected consolidated close, minute bars). Many other conditions (odd lot `I`, average price `B`, extended hours `U`, ...) add volume but don't move OHLC.
- So, per minute, trades without M/Q/9 and not canceled should equal `n`. Fewer = a gap. Whether bars are rebuilt after cancels is **UNVERIFIED**; minutes where we hold *more* trades than `n` are reported, not treated as errors.
- Trades are never split-adjusted, so the check uses `adjustment=raw` bars.

## Limits [P1][F1]
- Free plan: 200 historical calls/min; SIP history allowed when `end` is ≥15 minutes old ("subscription does not permit querying recent SIP data" otherwise). Live WebSocket on free keys: IEX only, 30 symbols.
- Algo Trader Plus ($99/mo): 10,000 calls/min, live SIP, unlimited symbols.

## Live WebSocket (for the next PR) [W1][W2]
- URLs: `wss://stream.data.alpaca.markets/v2/sip`, `.../v2/iex`, `.../v2/delayed_sip`; test stream `wss://stream.data.alpaca.markets/v2/test` with symbol `FAKEPACA`, available at all times.
- Flow: server sends `[{"T":"success","msg":"connected"}]`; client sends `{"action":"auth","key":..,"secret":..}` → `authenticated`; then `{"action":"subscribe","trades":[..],"quotes":[..],"statuses":[..],"lulds":[..]}`. Corrections and cancel/errors come automatically with `trades`.
- Messages: trade `T=t` (S,i,x,p,s,c,t,z); quote `T=q` (S,ax,ap,as,bx,bp,bs,c,t,z); correction `T=c` (oi,op,os,oc → ci,cp,cs,cc); cancel/error `T=x` (i,x,p,s,a=C/E); LULD `T=l` (u,d,i); trading status `T=s` (sc,sm,rc,rm).
- Errors: 400 invalid syntax, 401 not authenticated, 402 auth failed, 403 already authenticated, 404 auth timeout, 405 symbol limit, **406 connection limit (one connection for most plans)**, 407 slow client, 409 insufficient subscription, 410 invalid subscribe action, 500 internal. MessagePack via `Content-Type: application/msgpack`.

## Still UNVERIFIED
- Exchange code for FINRA TRF prints (call `/v2/stocks/meta/exchanges` on the PC).
- Condition code tables per tape (`/v2/stocks/meta/conditions/{trade|quote}?tape=A|B|C`; response shape not documented on the page).
- Whether `start`/`end` accept 9-digit fractions (the downloader sends whole seconds only).
- How long a full day of SPY quotes takes on 200 calls/min. To be measured on the first PC run.
- Alpaca data licence for trading at IBKR (doc 07).

## Sources
- [T1] https://docs.alpaca.markets/us/reference/stocktrades-1 (and `.md`)
- [Q1] https://docs.alpaca.markets/us/reference/stockquotes-1 (and `.md`)
- [B1] https://docs.alpaca.markets/us/reference/stockbars (and `.md`)
- [F1] https://docs.alpaca.markets/us/docs/market-data-faq
- [P1] https://docs.alpaca.markets/us/docs/about-market-data-api
- [W1] https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data
- [W2] https://docs.alpaca.markets/us/docs/streaming-market-data
- [M1] https://docs.alpaca.markets/us/reference/stockmetaconditions-1
