# 08: NautilusTrader + Interactive Brokers

- **Author:** FlowForge Research Scout
- **Date:** 2026-10-19 (all sources accessed 2026-10-19)
- **Scope:** The plan changes to **NautilusTrader** as the framework and **Interactive Brokers (IBKR)** as the broker, replacing the Robinhood MCP and probably Alpaca paper. This note covers framework version and pinning, the IB adapter, backtest/live parity, risk layering, integration with our services, IBKR account/API/order/fee/paper facts, and the design changes these imply. **Market-data pricing is out of scope** (see 07). Nothing was signed in to and no credentials were used.
- **Status labels:** **VERIFIED** means an official source (nautilustrader.io, the nautilus_trader GitHub repo/PyPI, interactivebrokers.com / IBKR docs / IBKR Guides, or the IBC / ib-gateway-docker repos) states it, and the source is cited. **UNVERIFIED** means it is missing from official sources, comes only from third parties, or is our inference.
- **Method notes:** GitHub issue search and the `develop` branch source were not reachable from the sandbox. Adapter source was read from the `develop_v1` branch (v1 line) through raw.githubusercontent.com. v2 facts come from PyPI metadata, release notes, the docs site and individual issue/PR pages.

---

## 1. Findings table

### A. NautilusTrader

| # | Question | Finding | Status | Src |
|---|---|---|---|---|
| A1 | Latest stable | **1.231.0** (PyPI "latest", 2026-08-02). The release notes say it is meant to be the **final 1.x** (legacy Cython core). | VERIFIED | N1, N2 |
| A2 | v2 status | **2.0.0rc7** (2026-10-15) is the newest pre-release. rc2 to rc7 shipped between Aug and Oct 2026, roughly every 2 weeks. No date for 2.0.0 final is published. | VERIFIED (no final date: VERIFIED absent) | N2, N3, N4 |
| A3 | v1 support window | After 1.231.0, `develop` is v2-only and v1 moves to `develop_v1` with **critical security backports for ~3 months**, so roughly to early Nov 2026. The README says the stable v1 wheel "cannot run the v2 documentation". | VERIFIED (the end date is our arithmetic) | N1, N5 |
| A4 | Python versions | 1.231.0: `>=3.12,<3.15` (3.12, 3.13, 3.14). rc2 to rc6: also 3.12 to 3.14. **rc7 drops 3.12 and requires `>=3.13,<3.15`.** README: "Python 3.13 and 3.14". | VERIFIED | N2, N4, N5 |
| A5 | Breaking churn in v2 | Heavy. rc3 removed the Cython package. rc4 renamed configs (`LiveExecClientConfig`→`ExecutionClientConfig`, `LiveDataClientConfig`→`DataClientConfig`), changed enums and made **default backtest leverage 10x for margin accounts** (set `default_leverage=1`). rc7 changed timers, orders and option IDs, and made `BacktestNode` batch book deltas by default ("fills can differ"). | VERIFIED | N3, N4 |
| A6 | Licence | **LGPL-3.0-or-later**. Contributions need a CLA. | VERIFIED | N2, N5 |
| A7 | Licence impact for a private bot | No obligations while we don't distribute. If we ever distribute a modified Nautilus, we must publish those modifications to Nautilus itself. Our own strategy code that imports it is not affected. Not legal advice. | UNVERIFIED (interpretation) | N5 |
| A8 | Official adapters | **Stable:** AX, Betfair, Binance, Blockchain, Bybit, Coinbase, **Databento**, Deribit, Derive, dYdX, Hyperliquid, **Interactive Brokers**, Kraken, Lighter, Lighter on Robinhood (a crypto DEX venue, *not* Robinhood equities), OKX, Polymarket, Tardis. **Beta:** CoinAPI. **Planned:** FinFeed, Kalshi. | VERIFIED | N6, N5 |
| A9 | Alpaca / Polygon / Massive adapters | **None.** Databento is the only US-equities data adapter. | VERIFIED (absent) | N6 |
| A10 | IB adapter status | Listed as **Stable**. In v1 it is Python on IB's `ibapi` (repackaged as `nautilus-ibapi`, 10.45.1 pinned in 1.231.0). **In v2 rc7 it moved to the Rust `ibapi` crate** (wboayue/rust-ibapi; the release notes say a port to v5.0.0, crates.io latest is 5.1.0), and the Python `ib` extra is gone from rc7's dependencies. A larger IB refactor, **PR #5041, is still open** and has requested changes. | VERIFIED | N4, N6, N7, N8, N15 |
| A11 | Connects to | TWS or IB Gateway over the TWS socket API. Default ports: Gateway **4002 paper / 4001 live**, TWS 7497 / 7496. TWS/Gateway must emit market-data timestamps in UTC. | VERIFIED | N7 |
| A12 | Dockerized gateway | v1: `DockerizedIBGatewayConfig(username, password, trading_mode="paper"\|"live", read_only_api=True default, timeout)`. Default image **`ghcr.io/gnzsnz/ib-gateway:stable`**, restart policy `always`, `EXISTING_SESSION_DETECTED_ACTION=primary`. The docs don't mention 2FA. Whether rc7 keeps `DockerizedIBGateway` is unclear, because the docs still show `[ib,docker]` extras that rc7 doesn't declare. | v1: VERIFIED; v2 status: UNVERIFIED | N7, N9 |
| A13 | IB order types | Market, limit, stop-market, stop-limit, MIT, LIT, market-to-limit, trailing stop market/limit (v1.229 added a bps trailing offset), MOC/LOC through `AT_THE_CLOSE`. TIF values: DAY, GTC, IOC, FOK, GTD, OPG, at-close. | VERIFIED | N7, N10 |
| A14 | IB-specific order fields | `IBOrderTags`: `whatIf`, `ocaGroup`/`ocaType`, `allOrNone`, `activeStartTime`/`activeStopTime`, `goodAfterTime`, `blockOrder`, `sweepToFill`, **`outsideRth`**, `hidden`, `conditions`. v1 has no `algoStrategy`. **Adaptive** limit orders exist in v2 (rc4 fixed them "being parsed as market orders"). | VERIFIED | N3, N10 |
| A15 | Bracket / OCO | `order_factory.bracket(...)` is supported. **OCO/OUO contingency does *not* auto-create IB OCA groups**, so you set `ocaGroup`/`ocaType` tags yourself. | VERIFIED | N7 |
| A16 | Data via IB adapter | Trade ticks use **`reqTickByTickData("AllLast")`**. Quote ticks default to **`batch_quotes=True`, which is `reqMktData`** (aggregated snapshots), to avoid "Max number of tick-by-tick requests". Opt out per subscription to get tick-by-tick `BidAsk`. L2 depth (default 20 levels, SMART depth) is available. **L3/MBO is not supported.** Bars run from 1 s to 1 d. Historical `request_ticks` (TRADES / BID_ASK) and `request_bars` are available. | VERIFIED (v1 source) | N7, N11 |
| A17 | Reconciliation at startup | Generic engine: fetches venue order/fill/position reports and aligns the cache. Unknown orders become `EXTERNAL` (tag `VENUE`). Position gaps generate synthetic `EXTERNAL`/`RECONCILIATION` orders when `generate_missing_orders=True` (the default). Continuous checks follow. IB-specific options: `fetch_all_open_orders`, client-id-scoped order visibility. | VERIFIED | N12, N7 |
| A18 | Reconnects / nightly restart | v1 doc: watchdog plus `IB_MAX_CONNECTION_ATTEMPTS`. Data-farm 2103/2105 → 2104/2106 notices "common during the nightly gateway restart" degrade only the affected feeds and resubscribe. On reconnect, IB error 326 (client id still held) triggers a backoff, then a fallback within a **client-id band**, during which order isolation by client id doesn't hold. | VERIFIED | N7 |
| A19 | Known IB pain points (open or recent) | #4970: callback lost the `client_order_id` mapping and created a **duplicate reconciliation order** (2.0.0rc3; the fix is in the unreleased refactor). #5057: **working orders from a previous session can't be managed after restart** (rc6, open). #5060: external fills marked reduce-only, startup execution queries **replayed as live fills**, position check stalls up to 60 min (rc5, partly fixed in rc7). #3176: duplicate orders on restart with Redis cache (v1.221; root cause was a colon in `order_id_tag`). #4983: Rust IB adapter denied every stock order over a venue mismatch. | VERIFIED (issue pages) | N13, N14, N16, N17, N18 |
| A20 | Gateway-free IB (Web API/OAuth) | Proposal #5069 asked for it because "weekly 2FA re-auth" blocks unattended use. The maintainer said the TWS API "is still the standard for now", that it belongs in rust-ibapi, and that it is not expected in the short term. He suggested disabling 2FA. | VERIFIED | N19 |
| A21 | Built-in pre-trade RiskEngine | Present in backtest, sandbox and live. It checks price/trigger precision and positivity, quantity precision and min/max, GTD expiry, `reduce_only` not increasing the position, **`max_notional_per_order`** (per instrument) and instrument `max_notional`, cash-account balance impact, **submit/modify rate limits** (default `100/00:00:01` each), and **TradingState** (`ACTIVE` / `HALTED`: new submits and modifies denied, cancels pass / `REDUCING`: only exposure-reducing). Cancels and queries skip the RiskEngine. rc7 added "free balance checks now include unacknowledged orders". | VERIFIED | N20, N21, N4 |
| A22 | Missing from built-in risk | No daily-loss, drawdown, max-position, per-symbol exposure, spread, session or kill-switch rules. No documented plug-in hook for custom pre-trade checks. | VERIFIED (absent from the docs list); "no hook": UNVERIFIED | N20 |
| A23 | Can strategies bypass it? | `RiskEngineConfig(bypass=True)` skips all checks except duplicate IDs. The normal path is Strategy → (OrderEmulator / ExecAlgorithm) → RiskEngine → ExecEngine. However, `ExecEngine.execute` is a registered message-bus endpoint, so code with bus access *could* send commands straight to the ExecEngine. That isn't a documented API. | bypass flag: VERIFIED; direct-send: UNVERIFIED (source inference) | N21, N22 |
| A24 | Backtest/live parity | The same `Strategy`/`Actor` code runs in `BacktestEngine`/`BacktestNode`, sandbox and `TradingNode`. The single-threaded kernel gives deterministic ordering. | VERIFIED | N23, N24 |
| A25 | Fill models | `FillModel(prob_fill_on_limit=1.0, prob_slippage=0.0, random_seed, liquidity_consumption)` plus 10 built-ins (`BestPrice`, `OneTickSlippage`, `Probabilistic`, `TwoTier`, `ThreeTier`, `LimitOrderPartialFill`, `SizeAware`, `CompetitionAware`, `VolumeSensitive`, `MarketHours`). With L1 books, `prob_slippage` applies one adverse tick. With L2/L3, the book walk sets price impact. | VERIFIED | N25 |
| A26 | Trade-driven fills and queue | `trade_execution=True` lets trade ticks fill resting orders, capped at `min(leaves, trade.size)`. `queue_position=True` (LIMIT only) tracks the queue ahead from quotes and same-price trades. `NO_AGGRESSOR` trades clear both sides, which the docs call "optimistic". | VERIFIED | N26 |
| A27 | Latency model | `LatencyModel(base, insert, update, delete)` in ns, all caller-supplied, no defaults. | VERIFIED | N27 |
| A28 | Data catalog | `ParquetDataCatalog` (Parquet, DataFusion queries) feeds `BacktestNode`. | VERIFIED (N23 lists data catalogs; the Parquet name comes from earlier docs) / partly UNVERIFIED today | N23, N36 |
| A29 | Process model | **One node per process.** Put many strategies in one `TradingNode`. The kernel is single-threaded, and persistence and adapters run on Tokio or thread pools. | VERIFIED | N24 |
| A30 | External message bus | Redis Streams (≥ 6.2) egress of serialized bus messages, with `types_filter`, `stream_per_topic`, `autotrim_mins`/`autotrim_maxlen` and a key of `trader:{trader_id}:{instance_id}:{streams_prefix}`. Ingress (`external_streams`) is documented for the **Rust live runtime**, starts at the current time and is live-only, not a replay. Encodings: MessagePack or JSON (SBE / Cap'n Proto for Rust egress). | VERIFIED | N28 |
| A31 | Cache persistence | `RedisCacheConfig` **or `PostgresCacheConfig`**. With `load_cache=True` the node restores state on restart. The docs say the Cache "is not designed to be a full database replacement". #5057 reports that `run_async()` doesn't accept a cache database. | VERIFIED | N29, N14 |
| A32 | Actors and blocking I/O | `Actor.run_in_executor` / `queue_for_executor` exist for off-kernel work. | VERIFIED (v1 source) | N22 |

### B. Interactive Brokers

| # | Question | Finding | Status | Src |
|---|---|---|---|---|
| B1 | Account type for the API | TWS API needs "**A funded and opened IBKR Pro account**". The FAQ says "API support is not available" means you have IBKR Lite. The Web API also needs Pro, and a funded live account even to use paper. | VERIFIED | I1, I2, I3 |
| B2 | Minimums | Lite and Pro: **USD 0 minimum**, no maintenance fee. Margin or short sales need the standard **Reg T USD 2,000** minimum. | VERIFIED | I4, I5 |
| B3 | PDT / intraday margin at IBKR | IBKR Guides (updated 2026-06-03): the $25k PDT minimum "is being removed"; trades that reduce excess liquidity may create an **Intraday Margin Deficit** (resolve "ideally within 3 business days"; **4+ missed in 12 months ⇒ 90-day restriction**). However, "**Your account may still be subject to existing PDT rules during FINRA's transition period, which ends in October 2027**." IBKR has published no per-account switch date. | VERIFIED (text); the account's actual regime is UNVERIFIED | I5 |
| B4 | API options | (1) **TWS API** over TWS/IB Gateway (Python `ibapi`, min Python 3.11). (2) **Web API (formerly Client Portal)**: retail clients authenticate through the **Client Portal Gateway** Java program, 10 req/s via CPG, one brokerage session per username, `/iserver` briefly down ~01:00 local nightly. (3) **OAuth 1.0a/2.0** are for licensed organisations, FAs, IBrokers and third-party vendors, not documented for individuals. | VERIFIED | I1, I3, I6, I7, I8 |
| B5 | Headless / VPS | IBKR says "**a headless session of TWS or IBGW without a GUI is not supported**" and the user must type credentials into a login window. IB Gateway uses ~40% fewer resources than TWS and "won't be locked due to inactivity". In practice, Docker images run Gateway under Xvfb with IBC. | VERIFIED (IBKR stance); Docker practice VERIFIED from the image repo | I9, I10, X2 |
| B6 | Restart and re-auth | Recommended settings: "Never lock" and **"Auto restart"**, so TWS/IBGW "can potentially run from Sunday to Sunday without re-authenticating". "**The weekly authentication cycle starts on every Monday**", and a manual login completes it. 2FA through IBKR Mobile applies at that login. | VERIFIED (2FA-at-weekly-login: UNVERIFIED detail) | I9, I10 |
| B7 | IBC status | **IBC is archived/retired as of 2026-09-01.** Last release **3.24.2** supports TWS/Gateway 10.48+ (Java 25). Only significant bug fixes may follow, and a planned "IBC 4.0" is unconfirmed. IBC cannot complete a login that needs a card/device code: you must approve it in IBKR Mobile. GPL-3.0. Not affiliated with IBKR. | VERIFIED | X1, X3 |
| B8 | Docker gateway image | `gnzsnz/ib-gateway`: stable = Gateway **10.50.1f**, latest = **10.51.1c**, both with **IBC 3.24.2**. Supports `AUTO_RESTART_TIME`, `TWOFA_TIMEOUT_ACTION`, `RELOGIN_AFTER_TWOFA_TIMEOUT`, `TRADING_MODE=paper\|live\|both`, socat (4003/4004), VNC, Docker secrets. MIT. | VERIFIED | X2 |
| B9 | Message pacing | Requests/s = **max market-data lines ÷ 2** (default 100 lines ⇒ **50/s**). "The TWS API has an order rate limitation of **50 orders per second**." With "reject above max rate" set you get error 100, and three violations end the session. Otherwise TWS paces (queues). Historical-data pacing violations can disable the API session "for several minutes" (Nautilus doc). | VERIFIED | I11, I12, I2, N7 |
| B10 | Tick-by-tick limits | Simultaneous tick-by-tick subscriptions are capped at **5% of market-data lines**, so **about 5 at the default 100 lines**. Max 1 request per instrument per 15 s. `reqMktData` "is not tick-by-tick": stocks get **250 ms aggregated snapshots**. | VERIFIED | I13, I14 |
| B11 | Order Efficiency Ratio | IBKR applies an OER order-pacing limit that can affect automated systems "that submit thousands of orders without regular executions". The page gives no numeric threshold. | VERIFIED (threshold UNVERIFIED) | I15 |
| B12 | Order types via API | Limit, market, stop, stop-limit, trailing, MIT/LIT, MOC/LOC, **brackets** (`parentId` plus `transmit=false` on parent and first child, `true` on last), OCA groups, IB algos including Adaptive, `outsideRth` for extended hours, `whatIf` margin preview. A "marketable limit" is just a limit through the far touch. | VERIFIED (API mechanics N10, I16, I17); Adaptive via API: VERIFIED indirectly through the Nautilus rc4 fix | I16, I17, N10, N3 |
| B13 | Shorting and borrow | Shorting needs a **margin account**. Generic tick **236** returns *Shortable* (>2.5 ⇒ ≥1,000 shares available; >1.5 ⇒ locatable; ≤1.5 ⇒ not shortable) and *Shortable Shares* (tick 89). | VERIFIED | I18, I19 |
| B14 | Fractional shares | "Fractional shares are available on all IBKR platforms **except via IBKR API**." | VERIFIED | I18 |
| B15 | Commissions (US stocks, Pro) | **Fixed:** USD 0.005/share, **min USD 1.00**, max 1% of trade value, includes exchange/clearing; regulatory fees pass through. **Tiered:** USD 0.0035/share (≤300k sh/month) down to 0.0005, **min USD 0.35**, max 1% (a general note on the same page says 0.5%, a conflict on IBKR's side), plus exchange, clearing (NSCC/DTC USD 0.00020/sh) and pass-through fees (NYSE 0.000175×comm, FINRA 0.000565×comm). Directed API orders can't use Tiered; SmartRouted API orders can use either. Modifying an order counts as cancel/replace and may re-trigger the minimum. **Lite:** USD 0 for US-listed stocks/ETFs, but IBKR can reject Lite orders, and Lite has no API. | VERIFIED | I20, I21, I4 |
| B16 | Regulatory fees | SEC fee **USD 0.0000206 × sale value**. FINRA TAF **USD 0.00 (fee holiday 2026-10-01 to 2026-12-31)**, normally 0.000195/sh sold, max 9.79. FINRA CAT **USD 0.000003/share**. | VERIFIED | I20 |
| B17 | Paper account | Available to funded Pro clients. The API treats paper like live (log in with paper credentials). **Market data is per username**, so either subscribe on the paper user or share data from the live user. Fills are simulated, "may or may not be within the bounds of the live market", and "no simulator could be perfect". Starting balance and depth simulation are not stated. | VERIFIED (balance UNVERIFIED) | I2, I3, I22 |
| B18 | Realistic order latency | Nothing official. Community reports range from tens to a few hundred ms from API to ack. Measure it ourselves. | UNVERIFIED | — |
| B19 | Automated trading terms for individuals | IBKR offers the TWS API to individual Pro clients. Lite orders can be rejected at IBKR's discretion, and OER applies. We found no specific clause banning or limiting individual algo trading. Whether algo use affects market-data professional status is out of scope (see 07). | Partly VERIFIED (I4, I15); remainder UNVERIFIED | I4, I15 |

---

## 2. Details

### 2.1 Which Nautilus line to pin

- **v1 (1.231.0)** has the mature Python IB adapter (years of use, `nautilus-ibapi` 10.45.1) and supports Python 3.12. It is **frozen**: only security backports until about Nov 2026, and the current docs describe v2.
- **v2 (2.0.0rcN)** is where all fixes land. It has a **new Rust IB adapter** (rust-ibapi), the IB port (PR #5041) is still open, and the reconciliation bugs in A19 belong to this line. Each RC brings breaking renames, and rc7 needs **Python ≥3.13**.
- **Recommendation:** build on **v2, pinned to an exact RC** (`nautilus_trader==2.0.0rc7`, Python 3.13). Upgrade deliberately, re-running the golden replay tests each time. Starting on v1 means a forced v1→v2 migration within weeks. Gate real-money live on (a) **2.0.0 final**, (b) PR #5041, or its successor, merged, and (c) our own IB paper soak test passing the restart/reconcile scenarios in §4. This updates the project's "Python 3.12" stack line to **3.13**. Pin `uv.lock` and the wheel hash.

### 2.2 IB adapter: how it connects

Nautilus `TradingNode` (IB DataClient and ExecClient) → TCP → IB Gateway (Java, in Docker with IBC driving the login UI) → IBKR servers. The adapter does not handle login. The Gateway container does, and that is where 2FA and the weekly re-auth live (B6–B8). In paper mode the Gateway logs in with the paper username on port 4002.

Client ids matter. IB scopes open-order visibility per client id, so FlowForge should reserve a **band per process** (e.g. node 1–5, watchdog 11–15) and set `fetch_all_open_orders=True` on the node so restarts see every working order.

### 2.3 Data path implications for tape reading

The IB adapter's defaults produce **true tick-by-tick trades** (`AllLast`), but **quotes are 250 ms aggregated snapshots** unless `batch_quotes=False`. Every tick-by-tick stream (trades and/or BidAsk) consumes one of about **5 slots at 100 market-data lines**. So with IB as the data source, FlowForge can only tape-read **about 2–5 symbols at once** at default lines (more with more lines). This affects data-source choice, but pricing is covered by 07. Nautilus's **Databento adapter is Stable**, so "Databento data + IB execution" is a supported pairing. Massive/Polygon would need a custom `DataClient`.

### 2.4 Backtest fidelity for our case

US-equity L1 (NBBO plus prints) maps to an **L1_MBP** venue with `trade_execution=True`, `queue_position=True` and `liquidity_consumption=True`. Our trades have no aggressor flag, so feed our Lee-Ready-classified aggressor into `TradeTick.aggressor_side`. Otherwise `NO_AGGRESSOR` clears queues on both sides (optimistic). Add a `LatencyModel` calibrated from measured IB paper ack latency (B18). Always set `default_leverage=1` and use a **cash or margin account type that matches the live account** (rc4 changed the default to 10x for margin).

### 2.5 Risk layering (our deterministic `risk_limits.yaml` on top of Nautilus)

| Layer | Where | What it enforces | Can a strategy skip it? |
|---|---|---|---|
| L0 Broker | IBKR account settings and presets (precautionary size/price limits in TWS/Gateway; read-only API toggle) | Hard ceilings outside our code | No (UNVERIFIED which presets bind API orders; test in paper) |
| L1 FlowForge pre-trade | `FlowForgeStrategy` base class: every entry goes through `self.ff_submit(intent)` → `RiskGuard.check(intent, portfolio, cache)`, a pure function loaded from `risk_limits.yaml` (0.5% risk/trade, max position, gross ≤ equity, symbol/session/spread rules, IML headroom, wash-sale cooldown, PDT regime flag). Denial logs a reason and submits nothing. | Our rules, deterministic, identical in backtest and live | Only by code that calls `submit_order` directly. **Lint/CI rule: no `submit_order` outside the base class.** |
| L2 Nautilus RiskEngine | `RiskEngineConfig(bypass=False, max_notional_per_order={...}, max_order_submit_rate="5/00:00:01", max_order_modify_rate=...)` | Precision, notional, rate, reduce-only, cash balance, TradingState | Only via `bypass=True`. **CI asserts `bypass is False`.** |
| L3 Portfolio guard Actor | `RiskSupervisor` Actor on account/position/fill events: daily loss, drawdown, consecutive losses, stale data, broker disconnect → calls `risk_engine.set_trading_state(HALTED or REDUCING)` and optionally flattens | Kill switch inside the node; cancels still pass | No: TradingState is enforced in the RiskEngine for all strategies |
| L4 External watchdog | Separate process with its own IB client id (read mostly) plus Postgres/Redis heartbeats. On node death, a breach or a dashboard kill, it cancels all orders and flattens with its own connection, and alerts | Survives node crashes | Independent of the node |

`ExecEngine.execute` is reachable on the bus (A23), so treat "no strategy bypass" as a **code-review and CI invariant**, not a framework guarantee.

### 2.6 External services

- **Redis:** stops being the inter-service *decision* bus. The node's internal MessageBus carries data → features → signal → risk → exec in one process. Redis becomes (a) **egress** of bus events (`types_filter` for orders, fills, positions, signals and custom data; `autotrim`) for the dashboard and post-trade, and (b) optional **ingress** for operator commands. Ingress is documented for the Rust live runtime, so confirm the Python path. Keep "bus is not storage".
- **Postgres:** `PostgresCacheConfig` gives Nautilus state persistence and restart recovery (or use Redis cache). Keep **our own** append-only schema (orders, fills, decisions, Jev calls) written by an `AuditWriter` Actor through `run_in_executor`, or by a separate consumer of the Redis egress. Nautilus calls its cache "not a full database replacement".
- **Jev gate:** a `JevGate` Actor. Strategy publishes `GateRequest(custom data)`, the Actor calls TypeSafe in `run_in_executor` with the **750 ms hard deadline** (ADR-0004) and publishes `GateDecision`. The Strategy enters only on `ALLOW` received before its own deadline. Timeout, error or a late reply means NO TRADE. The HTTP call never runs on the kernel thread. In backtests, swap in a replay or stub Actor that reads recorded decisions, for deterministic parity.
- **FastAPI dashboard:** a **separate process** (one node per process; avoid embedding the node in an ASGI server). It reads Postgres and Redis egress and writes kill/arm commands to a Redis command stream consumed by an in-node `OpsCommand` Actor and by the watchdog.

---

## 3. Design implications (vs the earlier custom-services plan, ADR-0001)

| Earlier service | New home | Notes |
|---|---|---|
| `ingest` | **Nautilus DataClient(s)** in the node: IB adapter, Databento adapter, or a custom Massive `DataClient` | One connection owner remains true (the node). Recorder: stream to `ParquetDataCatalog` for replay parity |
| `features` | **Actor(s)** in the node (incremental order-flow indicators, Lee-Ready, VPIN) publishing custom `Data` | Same code in backtest and live, which satisfies the parity requirement natively |
| `signals` | **Strategy** (`FlowForgeStrategy` subclasses) | |
| `jev-gate` | **Actor** with an executor and deadline (or a separate process over Redis if isolation is preferred) | NO TRADE on timeout is unchanged |
| `risk` | **L1 RiskGuard in the Strategy base, L2 Nautilus RiskEngine, L3 RiskSupervisor Actor**, plus **L4 external watchdog process** | See §2.5 |
| `execution` | **Nautilus ExecutionEngine + IB ExecutionClient** | `BrokerAdapter` interface is retired. `RobinhoodMCPAdapter` is dropped. `SimAdapter` becomes `BacktestEngine` with `FillModel`/`LatencyModel`/queue position. `AlpacaPaperAdapter` is dropped (no Nautilus adapter) |
| `post-trade` | **Separate process** consuming Redis egress plus Postgres | Learning and analytics stay out of the node |
| `api` | **Separate FastAPI process** plus React | Reads Postgres/Redis and sends commands through Redis |
| (new) | **IB Gateway container** (gnzsnz image + IBC) and **watchdog** | Ops-critical: restarts, 2FA, client-id bands |

**Paper stage:** use **IBKR paper** (same adapter and code path, port 4002, `TRADING_MODE=paper`) instead of Alpaca paper. Alpaca would need a custom Nautilus adapter, and its paper fills are not IB's. Caveats: IB paper fills are simulated and imperfect (B17), paper needs a funded Pro live account first, and market data must be shared to the paper user. Use Nautilus **sandbox mode** (live data, simulated exec) as the stage between backtest and IB paper.

**ADRs to supersede or revisit:** ADR-0001 (custom services → Nautilus node plus satellites), ADR-0003 (Robinhood MCP → IBKR), ADR-0005 (long-only was driven by Robinhood agentic accounts having no margin; IBKR margin allows shorting with borrow (tick 236), but keeping long-only at launch is still sensible), CLAUDE.md stack line (Python 3.13, nautilus_trader, IB Gateway).

---

## 4. Blockers

1. **A funded IBKR Pro live account is required** before any API or paper use (B1, B4).
2. **Weekly re-auth with 2FA needs a human.** Auto-restart covers the daily restart, but the Monday cycle needs a manual login or IBKR Mobile approval (B6, B7). Unattended 24×7 is not achievable within IBKR's stated support ("headless … not supported"). Plan a **Monday pre-open human checklist** plus watchdog alerts. Disabling 2FA (the maintainer's suggestion) is a security trade-off we should not take by default.
3. **IBC is retired (2026-09-01).** The docker image still bundles IBC 3.24.2. A future Gateway/Java change could break automated login with no upstream fix. Pin the Gateway version (`stable` tag digest) and watch for a community fork.
4. **The v2 IB adapter is mid-rewrite.** Restart, reconciliation and duplicate-order bugs are open (A19). **No real money until** 2.0.0 final plus the IB port merged plus our soak tests pass: restart with working bracket orders, Gateway nightly restart mid-session, error 326 client-id fallback, external manual order, partial fills across reconnect.
5. **Python 3.13 is required** (rc7). The spec said 3.12.
6. **Tick-by-tick slots (~5 at 100 lines)** cap the tape-reading universe if IB is the data source (B10). A data-vendor decision is needed (doc 07).
7. **PDT regime at IBKR is per account until Oct 2027** (B3). Keep the `regime` flag from doc 05 and confirm the account's status before live.

## 5. Open questions

1. Does the rc7 Rust IB adapter still ship `DockerizedIBGateway`, or must we run the gnzsnz container ourselves (likely better anyway)?
2. Is `external_streams` ingress (Redis → bus) available to Python-configured v2 nodes, or Rust-only?
3. Does `run_async()` in v2 accept a cache database (#5057)? This affects whether we can embed the node in our own asyncio supervisor.
4. Which IBKR precautionary settings (order size/value limits) apply to API orders, and can they be set as a hard L0 ceiling?
5. IBKR paper: starting balance, whether it simulates IML/IMD and shorting borrow, and how it fills against depth. Measure in paper.
6. Measured IB order ack latency and fill-report latency from our VPS (feeds `LatencyModel`).
7. OER threshold applicable to our account. Expect few orders per day, so likely a non-issue.
8. Fixed vs Tiered for small, low-volume trading: Fixed ($1 min) is simpler, while Tiered ($0.35 min plus fees) is likely cheaper for small share counts. Model both in the backtest cost model.
9. When will 2.0.0 final ship? Track the release feed.

---

## Sources (all accessed 2026-10-19)

**NautilusTrader**
- N1: https://github.com/nautechsystems/nautilus_trader/releases
- N2: https://pypi.org/pypi/nautilus_trader/json (and `/2.0.0rc6/json`, `/2.0.0rc7/json`)
- N3: https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v2.0.0rc4 (mirror of GitHub release notes)
- N4: https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v2.0.0rc7 (mirror of GitHub release notes)
- N5: https://github.com/nautechsystems/nautilus_trader (README)
- N6: https://nautilustrader.io/docs/latest/integrations/
- N7: https://nautilustrader.io/docs/latest/integrations/ib and https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/integrations/ib.md
- N8: https://crates.io/api/v1/crates/ibapi (rust-ibapi 5.1.0, https://github.com/wboayue/rust-ibapi/)
- N9: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/nautilus_trader/adapters/interactive_brokers/config.py and `.../gateway.py`
- N10: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/nautilus_trader/adapters/interactive_brokers/parsing/execution.py, `.../execution.py`, `.../common.py` (IBOrderTags)
- N11: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/nautilus_trader/adapters/interactive_brokers/data.py and `.../client/market_data.py`
- N12: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/concepts/reconciliation.md and `.../live.md`
- N13: https://github.com/nautechsystems/nautilus_trader/issues/4970
- N14: https://github.com/nautechsystems/nautilus_trader/issues/5057
- N15: https://github.com/nautechsystems/nautilus_trader/pull/5041
- N16: https://github.com/nautechsystems/nautilus_trader/issues/5060
- N17: https://github.com/nautechsystems/nautilus_trader/issues/3176
- N18: https://github.com/nautechsystems/nautilus_trader/issues/4983
- N19: https://github.com/nautechsystems/nautilus_trader/issues/5069
- N20: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/concepts/execution.md (Risk engine section)
- N21: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/nautilus_trader/risk/config.py
- N22: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/nautilus_trader/risk/engine.pyx, `.../execution/engine.pyx`, `.../common/actor.pyx`
- N23: https://nautilustrader.io/docs/latest/concepts/backtesting
- N24: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/concepts/architecture.md
- N25: https://nautilustrader.io/docs/latest/concepts/backtesting/fill-models/
- N26: https://nautilustrader.io/docs/latest/concepts/backtesting/trade-execution/
- N27: https://nautilustrader.io/docs/core-latest/src/nautilus_execution/models/latency.rs.html
- N28: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/concepts/message_bus.md
- N29: https://raw.githubusercontent.com/nautechsystems/nautilus_trader/develop_v1/docs/concepts/cache.md
- N36: docs/research/04-github-survey.md (prior pass)

**Interactive Brokers**
- I1: https://www.interactivebrokers.com/docs/tws-api/doc/notes-limitations/requirements
- I2: https://www.interactivebrokers.com/docs/third-party-integrations/general-third-party-frequently-asked-questions
- I3: https://www.interactivebrokers.com/campus/ibkr-api-page/web-api-trading/
- I4: https://www.interactivebrokers.com/en/general/compare-lite-pro.php
- I5: https://www.ibkrguides.com/orgportal/messagecenter/supportpdtreset.htm
- I6: https://www.interactivebrokers.com/docs/web-api/authentication/oauth-1a/introduction
- I7: https://www.interactivebrokers.com/docs/web-api/authentication/oauth-2/introduction
- I8: https://www.interactivebrokers.com/docs/web-api/trading/usage-and-availability/pacing-limitations
- I9: https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway
- I10: https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/daily-weekly-reauthentication
- I11: https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/introduction
- I12: https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/pacing-behavior
- I13: https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/tick-by-tick-data/introduction
- I14: https://interactivebrokers.github.io/tws-api/top_data.html and https://interactivebrokers.github.io/tws-api/tick_data.html
- I15: https://www.interactivebrokers.com/docs/general/order-types/notes-limitations/order-efficiency-ratio
- I16: https://interactivebrokers.github.io/tws-api/bracket_order.html
- I17: https://www.interactivebrokers.com/docs/general/order-types/complex-orders/bracket-orders
- I18: https://www.interactivebrokers.com.sg/en/?f=45718 (IBKR fractional trading page; global content)
- I19: https://interactivebrokers.github.io/tws-api/tick_types.html
- I20: https://www.interactivebrokers.com/en/pricing/commissions-stocks.php?region=americas
- I21: https://www.interactivebrokers.com/commissions
- I22: https://www.ibkrguides.com/kb/en-us/article-252.htm

**Gateway automation**
- X1: https://github.com/IbcAlpha/IBC
- X2: https://github.com/gnzsnz/ib-gateway-docker
- X3: https://github.com/IbcAlpha/IBC/releases/latest
