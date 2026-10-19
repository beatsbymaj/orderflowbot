# 04 — Open-Source Framework & Order-Flow Tooling Survey (Architecture Patterns)

**Author:** Research Scout · **Date:** 2026-10-19 · **Scope:** architecture patterns to borrow or avoid. Strategies are out of scope.

## How this was verified

- **Star counts** come from the GitHub repo HTML pages, fetched 2026-10-19. They are rounded as GitHub shows them.
- **Latest release and date** come from the PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`), queried 2026-10-19. This is the most reliable "is it alive" signal I could get.
- **Last-commit dates are mostly UNVERIFIED.** The GitHub REST API and `gh` CLI were blocked in this session (HTTP 403, "repository access not enabled"), and the HTML captures did not include commit dates. Where a last commit is given, it came from a third-party tracker and is marked as such.
- Anything not confirmed from a primary source is marked **UNVERIFIED**.

## Summary table

| Project | What | License | Latest release (PyPI) | Stars (approx) | Maintenance | Depend or learn? |
|---|---|---|---|---|---|---|
| NautilusTrader | Rust core + Python event-driven engine, backtest/live parity | LGPL-3.0 | 1.231.0 (2026-08-02); v2.0.0rc4 pre-release (2026-09-02) | 30.2k | Very active; v1 → v2 (Rust-native) transition under way | **Learn heavily**; optional later adopt (see recommendation) |
| vectorbt (OSS) | Vectorized NumPy/Numba research backtester | Apache-2.0 + Commons Clause | 1.2.0 (2026-10-19) | 8.7k | Active releases | **Depend (research only)**, never in the live path |
| vectorbt PRO | Paid successor | Proprietary; private repo | n/a | n/a | Active (paid) | Avoid as a dependency (commercial-use restrictions) |
| QuantConnect LEAN | C# engine with Python algos; handler-based architecture | Apache-2.0 | `lean` CLI 1.0.230 (2026-10-15) | 21.2k | Very active | **Learn** (handler interfaces) |
| backtrader | Classic event-driven Python backtester | GPL-3.0 | 1.9.78.123 (2023-04-19) | 23.5k | **Effectively unmaintained** | Avoid |
| freqtrade | Crypto bot with dry-run, UI, Telegram, analysis tools | GPL-3.0 | 2026.9 (2026-09-29) | 55.0k | Very active (monthly releases) | **Learn ops UX**. Crypto-only, GPL, so don't depend |
| hftbacktest | Tick/L2/L3 backtester with queue and latency models (Rust + Numba) | MIT | 2.4.4 (2025-12-10) | 4.7k | Maintained; ~10 months since last PyPI release | **Learn models**; optionally depend for the microstructure sim |
| alpaca-py | Official Alpaca SDK | Apache-2.0 | 0.44.0 (2026-08-11) | 1.5k | Active | **Depend**, wrapped inside `AlpacaPaperAdapter` |
| tclf | Trade classification (tick, quote, LR, EMO, CLNV, depth, trade size), sklearn API | BSD-3-Clause | 0.0.9 (2024-02-18) | 22 | Low activity | **Learn and vendor-test against**. Reimplement streaming LR ourselves |
| flowrisk | VPIN / BVC toxicity | MIT | 0.3.0 (2020-08-02) | n/a | **Abandoned** | Learn only |
| wraquant | Quant toolkit with a `microstructure.toxicity` module (VPIN etc.) | MIT | 1.1.0 (2026-04-02) | **UNVERIFIED** | Recent release | Reference implementation only |
| OrderflowChart | Plotly footprint charts | Apache-2.0 | n/a (GitHub only) | 251 | Last push 2024-09-27 (per gittrend.io) | Dashboard UI inspiration only |
| jev-trader (jarrodwatts) | Community bot using TypeSafe's Jev model on Monad/Kuru | MIT | n/a | 13 on GitHub (articles claim ~2.1k; **conflicting**) | Unknown | Learn the "model as gate" pattern only |

---

## 1. NautilusTrader

**What it is.** A production-grade trading platform. The core (cache, message bus, data/exec/risk engines) is written in Rust, with a Python control plane via PyO3. Strategies run unchanged across backtest, sandbox (live data with simulated execution), and live. The project is migrating to a "Rust-native v2 runtime." v1 now gets only critical security backports on `develop_v1`, and v2 ships as release candidates (2.0.0rc4 on 2026-09-02). The GitHub page shows 21.8k commits on `develop`.

**License.** LGPL-3.0. Using it as a library from FlowForge's own code is fine. Modifications to Nautilus itself must be shared if distributed.

**Maintenance.** Very active, with bi-weekly releases as a goal. However, v2 has **heavy breaking changes**: config renames (`LiveExecClientConfig`→`ExecutionClientConfig`), enum changes, removed factory configs, and changed default leverage.

**Integrations.** Nightly docs list AX, Betfair, Binance, Bybit, Coinbase, Databento, Deribit, dYdX, Hyperliquid, Interactive Brokers, Kraken, OKX, Polymarket, Tardis, and others. **There is no Alpaca and no Robinhood adapter.** FlowForge would have to write both, against a Rust/PyO3 adapter API that is changing during the v2 migration.

**Borrow (high value):**

- **Live/backtest parity.** The same strategy code runs everywhere, and only the clock, data client, and exec client are swapped. FlowForge equivalent: the `BrokerAdapter` interface plus an injectable `Clock`, with the Sim adapter consuming the same Redis message schemas as live.
- **Kernel, message bus, and cache split.** The bus handles pub/sub, request/response, and command/event. The cache is the in-memory source of truth for orders, positions, and instruments. Per the docs, "durable state recovery belongs to the cache or event store, not the bus." **Adopt this rule: Redis Streams is transport. Postgres is the order/fill event store.**
- **Single-threaded deterministic core.** The core is LMAX-style. I/O runs as async tasks feeding channels into one logic thread. FlowForge equivalent: each service's decision logic runs on one asyncio task fed by queues, with no shared mutable state across tasks.
- **Pre-trade risk engine as a mandatory hop.** It validates fields, quantities, notionals, reduce-only, and trading state (ACTIVE/REDUCING/HALTED), and applies submit/modify rate limits. This maps directly to our deterministic risk engine.
- **Component FSM.** States are PRE_INITIALIZED → READY → RUNNING → STOPPED/DEGRADED/FAULTED → DISPOSED. Give every FlowForge service this lifecycle and expose it on the dashboard.
- **Crash-only design.** Panic aborts, a supervisor restarts the process, and recovery is just normal startup plus reconciliation. "Venue commands are not safe to retry blindly."
- **Reconciliation (the single most valuable pattern).** From the reconciliation docs:
  - Startup mass-status: order reports, then fills, then positions, applied in event-time order. **Unresolved positions block startup** (fail closed).
  - "A missing report is not flat." Absence means unknown, not zero.
  - Continuous loops: in-flight order checks, open-order polling, position polling. Each has its own retry budget.
  - Before marking an order terminal "not found", do a single-order probe, throttled to respect rate limits.
  - In-flight `SUBMITTED` past the retry budget becomes `INFLIGHT_TIMEOUT`, or stays unresolved under a "retain unresolved" policy.
  - External orders get `EXTERNAL` ownership. Synthetic fills reconcile position deltas. Synthetic IDs are deterministic so replays dedupe.
  - Raw broker reports are persisted separately from applied state.
- **Graceful shutdown with evidence.** After stop, the node keeps processing venue evidence and reports unresolved client order IDs as errors.

**Avoid / caveats:**

- **One `LiveNode` per process**, thread-local runtime state, and only one hosted node per event loop.
- The cache DB backing is **rejected when hosted on an external asyncio loop** (`run_async()`), because it blocks.
- Runner channels are **unbounded with no backpressure**.
- Under ASGI, run one worker with no hot reload. Embedding it in our FastAPI process is discouraged.
- The v1→v2 API churn means anything we build on it now is rework within months.
- The Rust toolchain is needed for adapter work (prebuilt wheels are fine for users).

**Verdict:** **Learn from it, don't depend on it now.** Re-evaluate once v2.0.0 is final, if FlowForge outgrows its Sim adapter's fidelity.

## 2. vectorbt (OSS) and vectorbt PRO

**What it is.** A vectorized (NumPy/Numba/pandas) portfolio simulator for fast parameter sweeps. The OSS edition is the "community edition of VectorBT PRO."

**License.** Apache-2.0 **with Commons Clause**. Free to use, but you may not sell a product or service whose value derives substantially from vectorbt. Optional dependencies may be more restrictive. FlowForge is internal, so research use is OK. Flag it if FlowForge is ever commercialized.

**Maintenance.** PyPI 1.2.0 uploaded 2026-10-19, so it is active. 121 open issues.

**PRO.** Distributed via a **private GitHub repo with paid membership**. Organization access is limited to "internal, non-commercial work". Commercial use needs written consent, and access to updates ends when the subscription lapses. **Do not make PRO a dependency.**

**Borrow:** Fast vectorized sweeps for *bar-level* hypothesis triage before porting a signal into the event-driven Sim. Its `Portfolio.from_signals` and records model (orders/trades as structured arrays) is a good shape for the post-trade learning service's analytics tables.

**Avoid:** Vectorized backtests cannot model queue position, partial fills, latency, or intrabar order-flow sequencing. That is exactly what an order-flow bot depends on. It is also a source of look-ahead bias via whole-array ops. **Never use it to make go/no-go decisions on order-flow signals.**

**Verdict:** **Optional research dependency** in a separate `research/` environment. Never import it from live services.

## 3. QuantConnect LEAN

**What it is.** A C#/.NET 10 engine with Python algorithm support. It is modular, with pluggable `Brokerages`, `Data`, `Engine`, `Messaging`, `Queues`, `Indicators`, and `Optimizer`.

**License.** Apache-2.0.

**Maintenance.** Very active. The `lean` CLI is at 1.0.230 (2026-10-15). There are 13.3k commits on GitHub.

**Alpaca.** LEAN has an Alpaca brokerage (2024 integration). However, **local LEAN CLI live trading requires a paid QuantConnect organization tier**, and US equity data for local runs may require purchased datasets.

**Borrow:**

- **Handler decomposition.** The engine is assembled from swappable handlers: data feed, transaction handler, result handler, setup handler, real-time handler, brokerage. The specific handler names are from prior knowledge and were **UNVERIFIED** today, since the page did not detail them. This is a clean template for our service boundaries and for the `BrokerAdapter` contract.
- **Security Master / corporate-actions handling** for adjusted vs raw equity prices. FlowForge needs this for splits and halts on US equities.
- **Brokerage model abstraction** (fees, fill model, slippage model, buying power model) as separate pluggable objects. Use the same pattern for the Sim adapter.

**Avoid:** The .NET runtime in a Python 3.12 stack, cloud/tier coupling for local live trading, and a large surface area.

**Verdict:** **Learn only.**

## 4. backtrader

**What it is.** The classic event-driven Python backtester (Cerebro, Strategy, Broker, Feeds, Analyzers).

**License.** GPL-3.0.

**Maintenance.** **Effectively unmaintained.**
- The last PyPI release was 1.9.78.123 on 2023-04-19.
- The README still lists Python 3.2 and Travis CI.
- There are 64 open PRs.
- Its newest dated README note is from 2018.

**Borrow:** The `Analyzer` plug-in concept for post-trade metrics, and `notify_order` / `notify_trade` callbacks as a simple order-lifecycle API.

**Avoid:** Everything else. Bar-centric design, no asyncio, GPL, dead upstream, and no queue/latency realism.

**Verdict:** **Avoid as a dependency.**

## 5. freqtrade

**What it is.** A Python crypto trading bot with roughly 55k stars and 33k commits.

**License.** GPL-3.0 (per PyPI metadata).

**Maintenance.** Very active, with monthly CalVer releases (2026.9 on 2026-09-29). **Crypto exchanges only, via CCXT. No US equities.**

**Borrow (ops UX, the reason to study it):**

- **Dry-run as a first-class mode** with a simulated wallet, and docs that insist users start there. FlowForge: `mode: sim | paper | live`, defaulting to paper, with the mode displayed in every dashboard header and log line.
- **Telegram / REST control plane** with commands like `/status`, `/profit`, `/balance`, `/forceexit`, and `/stop`. Our FastAPI should expose the same verbs, especially **kill switch, flatten-all, and pause new entries**, with auth and an audit log.
- **FreqUI** as a separate web UI over the REST API. This validates our FastAPI+React split.
- **`lookahead-analysis` and `recursive-analysis` CLI tools** that automatically detect look-ahead bias and indicator warm-up dependence. **Build equivalents** for our feature engine: replay with truncated history and assert features are identical.
- Protections (cooldowns and drawdown-based locks) as composable, config-driven guards. The README capture did not confirm this, so it is **UNVERIFIED** today, though known from prior docs. Map them into our risk engine.
- Docker-first quickstart and a single config file with schema validation.

**Avoid:** A monolithic single process. Persistence is SQLite (**UNVERIFIED** in today's capture; known from prior docs). Strategies are dataframe-per-candle, which does not fit tick-level order flow. GPL also argues against importing its code.

**Verdict:** **Learn only.**

## 6. hftbacktest

**What it is.** A tick-by-tick backtester (Rust core with Numba Python bindings, Python 3.11+). It does full order-book reconstruction from L2 market-by-price and L3 market-by-order feeds, with **feed and order latency models** and **queue-position fill models**. It also has a Rust-only live bot for Binance Futures and Bybit.

**License.** MIT.

**Maintenance.** Maintained. 1,038 commits, 6 open issues, 12 open PRs, a ROADMAP, and RTD docs. The last PyPI release was 2.4.4 on 2025-12-10, so there has been no PyPI release for about 10 months. The last commit date is **UNVERIFIED**.

**Borrow (core of our Sim adapter's fill model):**

- **Exchange models.** `NoPartialFillExchange` fills an order at exactly the trade price only if it is at the queue front. `PartialFillExchange` fills by the traded quantity at that level.
- **Queue models:**
  - `RiskAverseQueueModel`: the most conservative. Cancels come only from the back, so you advance only on trades.
  - `ProbQueueModel` family (Log, Identity, Square, Power variants): cancels are attributed probabilistically ahead of or behind you.
  - Pluggable custom models.
- **Latency models.** Separate *feed* latency (exchange → us) and *order* latency (us → exchange → ack), including interpolated latency from recorded data (`IntpOrderLatency`). FlowForge should **record real Alpaca ack latencies in paper trading** and replay them in Sim.
- **Stated caveats to adopt as Sim invariants:**
  - Replay has no market impact.
  - Taker fills against replayed depth are optimistic.
  - Queue position is estimated without MBO data.
  - Always validate against live.

**Avoid:** Its live connectors (crypto, Rust-only). It also assumes you have full-depth L2/L3 data. Alpaca's equities feed gives us trades and NBBO quotes (and SIP vs IEX matters), not full depth. Our queue models must therefore work from top-of-book plus trade prints.

**Verdict:** **Port the model concepts into our Python Sim adapter.** It is MIT, so code can be adapted with attribution. Optionally use hftbacktest directly offline for a "high-fidelity" comparison run on the occasional full-depth dataset.

## 7. Order-flow, footprint, VPIN, and trade-classification libraries (Python)

The ecosystem is **thin and mostly unmaintained**. Nothing here is production-grade for streaming use.

| Library | Covers | License | Status | Use |
|---|---|---|---|---|
| **tclf** (KarelZe/tclf) | Tick, reverse tick, quote, **Lee-Ready**, EMO, CLNV, depth, and trade-size rules. sklearn `ClassicalClassifier` with layered strategies. Accepts pandas, Polars, cuDF, or NumPy. | BSD-3 | 0.0.9 (2024-02-18), 22 stars | **Golden-test oracle**: run our streaming classifier and tclf on the same day's data and assert agreement. Batch only, so not for the hot path. |
| **flowrisk** (hanxixuana) | VPIN with **BVC** (bulk volume classification), toxicity estimators | MIT | 0.3.0 (2020-08-02), abandoned | Read for the algorithm structure. Don't depend on it. |
| **wraquant** (`microstructure.toxicity`) | VPIN and related toxicity metrics | MIT | 1.1.0 (2026-04-02) | Reference implementation to cross-check our VPIN. Stars and maintenance depth are **UNVERIFIED**. |
| **OrderflowChart** (murtazayusuf) | Plotly footprint/bid-ask volume charts | Apache-2.0 | Last push 2024-09-27, 251 stars | Visual reference for the React footprint panel only. |
| **orderflow** (PyPI) | n/a | MIT | 0.0.1 (2022-12-01) | Placeholder-grade. Ignore. |
| mlfinlab | Historically had BVC/VPIN | n/a | Not on PyPI JSON today (lookup failed); went commercial previously (**UNVERIFIED**) | Avoid. |

**Recommendation.** Implement trade classification ourselves as **incremental, O(1)-per-tick functions** in the feature engine:

- tick rule
- quote rule against the prevailing NBBO, with a configurable quote lag for Lee-Ready
- EMO / CLNV as variants
- BVC over volume buckets
- VPIN on volume-clock buckets
- footprint (price × bid/ask volume) and cumulative delta

Validate them offline against tclf (LR, EMO, CLNV) and against wraquant/flowrisk (VPIN/BVC). The academic references (Lee-Ready 1991; Easley, López de Prado and O'Hara on BVC/VPIN; Chakrabarty et al. on BVC vs tick vs LR, SSRN 2182819) belong in the features doc.

## 8. "Open-source JEV trading bot demo" by TypeSafe

**Findings (with low confidence):**

- **Jev** is a typed-decision ("System One") model from **TypeSafe AI**. It returns calibrated probabilities for typed questions (choice / score) rather than free text. Per secondary, promotional articles, it was announced alongside a $40M raise led by DCVC on September 15 [2026]. The maker attribution and the funding details are **UNVERIFIED** from a primary TypeSafe source.
- **I found no official TypeSafe-published open-source trading-bot repo.** The demos are community projects:
  - **jarrodwatts/jev-trader** (MIT, TypeScript/Bun). A Monad/Kuru MON-USDC bot.
    - One decision per N blocks, with one order in flight and a position cap.
    - A `Model` interface with a `JevModel` and a default `MockModel` momentum heuristic.
    - If the model misses a block, it holds.
    - Dry-run (simulated fills) when no private key is set.
    - SSE dashboard stream.
    - The GitHub page shows **13 stars / 11 commits**, while aggregator articles claim ~2.1k stars. **The counts conflict and are UNVERIFIED.** The repo is not stated to be affiliated with TypeSafe.
  - **zadescoxp/Jev-Trades** (Apache-2.0, ~7 stars per article): a Next.js simulated portfolio with no broker. **UNVERIFIED** (not fetched directly).
  - **aowang-ai/jev-trade**: a Hyperliquid bot built on jev-trader, dry-run/testnet by default. License **UNVERIFIED**.
  - An index at **github.com/Amal-David/awesome-jev** (community-maintained).

**Borrow (relevant to our LLM-ish gate):** The *architecture* the articles describe matches FlowForge's design and is worth codifying:

- **Code computes deterministic state.** The model answers only narrow typed judgments (regime, toxicity) in one batched call.
- **Hard risk limits have absolute veto**, independent of the model.
- **A fallback ladder** goes to deterministic-only operation if the model is unavailable, and **holds if the decision misses its deadline**.
- A `Model` interface with a **mock implementation as the default**, which gives a free test double and an offline baseline.
- Pin model versions, because unpinned versions silently shift confidence calibration. Verify calibration on our own logs before using probabilities for sizing.

**Avoid:** The vendor latency and cost claims (70–500 ms, $10–25/month) are unverified marketing. Every-tick model calls are a cost and latency trap. Gate only on candidate signals.

**Verdict:** **Learn the pattern. No code to depend on.**

## 9. Message bus: Redis Streams vs NATS JetStream vs ZeroMQ

FlowForge's scale is a single host or a few containers, and on the order of hundreds of symbols. Peak US-equities trade+quote throughput for a watchlist is roughly 10³–10⁵ msgs/s, which is well within all three options.

| | Redis Streams | NATS JetStream | ZeroMQ |
|---|---|---|---|
| Persistence / replay | Yes. Append-only log, `XRANGE` replay, `MAXLEN`/`MINID` trimming | Yes. File/memory streams, replay by sequence or time | **None** (brokerless sockets) |
| Consumer groups / acks | `XREADGROUP`, `XACK`, pending entries list, `XAUTOCLAIM` for dead consumers | Durable pull/push consumers, ack policies, max-deliver, redelivery | DIY |
| Dedup / idempotency | DIY (idempotent consumers keyed on event ID) | Built-in publish dedup window via `Nats-Msg-Id` (from prior knowledge; docs page fetched today did not show it, so **UNVERIFIED** today) | DIY |
| Extra value | Same server doubles as cache, rate-limit counters, and kill-switch flags | KV and object store, subject wildcards, clustering | Lowest latency, no broker |
| Python client | `redis` 8.1.0 (2026-07-30), MIT, asyncio | `nats-py` 2.16.0 (2026-09-16), Apache-2.0 | `pyzmq` 27.2.0 (2026-08-20), BSD-3 |
| Server license | Redis 8.x is tri-licensed RSALv2 / SSPLv1 / **AGPLv3**. Unmodified internal self-hosting is unaffected. **Valkey** (BSD) is a drop-in alternative if licensing ever matters. | Apache-2.0 | n/a |

**Recommendation: keep Redis Streams.** One fewer moving part matters, since we need Redis anyway for hot state and kill-switch flags. Consumer groups with a pending entries list plus `XAUTOCLAIM` give at-least-once delivery, and the scale is trivial for it.

Mitigations to put in the ADR:

1. Every event carries a ULID `event_id`. Consumers are **idempotent** (upsert by `event_id`). Duplicates are expected under at-least-once.
2. **The bus is not the system of record.** Following Nautilus's rule, orders, fills, and decisions are written to Postgres, and streams are trimmed by `MINID`/`MAXLEN`.
3. Enable AOF (`appendfsync everysec`) and accept losing up to 1 s of *market data*, which is recoverable. Never rely on Redis alone for *order events*.
4. Run a dead-consumer sweeper (`XAUTOCLAIM`) and a pending-entries-list depth alarm on the dashboard.

Revisit NATS JetStream only if we go multi-host or need built-in publish dedup. **Reject ZeroMQ.** We would rebuild persistence, replay, and acks, which are exactly the parts that matter for audit and backtest replay.

## 10. TimescaleDB licensing for self-hosting

Per the Tiger Data (formerly Timescale) editions page:

- **Apache-2.0 edition:**
  - hypertables, `create_hypertable`, `show_chunks`, `drop_chunks`
  - basic hyperfunctions: `time_bucket`, `first`, `last`, `histogram`
- **Community Edition (Tiger Data License, TSL)**, required for:
  - **columnstore/compression (Hypercore)**: `convert_to_columnstore`, `add_columnstore_policy`
  - **continuous aggregates** and their refresh policies
  - **retention policies** (`add_retention_policy`)
  - **background jobs** (`add_job`)
  - `time_bucket_gapfill`, `locf`, `interpolate`
  - reorder/move chunk, SkipScan
- **TSL terms:** Community Edition is **free to self-host, modify, and run in production**. You **cannot offer it as a hosted database service** to third parties. This is not legal advice; read the license text.

**Implication for FlowForge.** We self-host for our own use, so **TSL Community Edition is fine** and we should use it. Continuous aggregates (1s/1m bars and delta from ticks), compression of tick hypertables, and retention policies are exactly the features we need.

Gotchas:
- Many distro or managed "Postgres + timescaledb" packages ship the **Apache-only build**, which silently lacks these features. Pin the official `timescale/timescaledb` Docker image (TSL build) and add a startup check that `timescaledb.license = 'timescale'`.
- Fallback if TSL is ever unacceptable: Apache hypertables plus our own Parquet archival and `drop_chunks` cron. That is consistent with our Parquet archive plan anyway.

## 11. alpaca-py (official SDK)

**Status.** Apache-2.0, 1.5k stars, PyPI 0.44.0 (2026-08-11), Python ≥3.10. Active. Still pre-1.0, so **pin the exact version** and expect minor breaking changes.

**Clients:**
- `TradingClient(paper=True)`
- historical data clients
- `StockDataStream` / `CryptoDataStream` / `NewsDataStream` / `OptionDataStream` websockets
- `TradingStream` for order updates. This is from prior knowledge; the README capture today did not mention it, so it is **UNVERIFIED** today.

Async support is **UNVERIFIED**. The REST clients are historically synchronous (requests-based), so in our asyncio services wrap them in `asyncio.to_thread` or use the REST API via `httpx` directly. Streams run their own loop. Plan an adapter task for this.

**Order model (from the Alpaca API reference):**

- **Equity `type`:** market, limit, stop, stop_limit, trailing_stop (`trail_price` or `trail_percent`, with a high-water mark).
- **`order_class`:** simple, bracket, oco, oto.
- **`time_in_force` (equities):** day, gtc, opg, cls, ioc, fok.
- **`extended_hours=true`:** only valid with **limit + day**.
- **Other:** fractional quantity and notional orders are supported, and shorting is supported.

**`client_order_id`:**
- Max **128 chars**, "a unique identifier for the order", auto-generated if omitted. This is stated in the Broker API create-order reference; that the Trading API is the same is assumed and **UNVERIFIED** for the Trading API specifically.
- Orders can be looked up by client order ID (`get_order_by_client_id`).
- Server-side duplicate rejection behavior (e.g., HTTP 422 on reuse) is **UNVERIFIED**. Do not rely on it alone.

**Idempotency design for `AlpacaPaperAdapter`:**

1. Derive `client_order_id` deterministically: `ff-{strategy}-{intent_ulid}-{attempt}`, well under 128 chars. Persist the intent to Postgres **before** sending.
2. Alpaca's own docs say a **timed-out order "may have been sent to the market"**: do **not** resend and do not mark canceled until status is confirmed. On timeout or 5xx, query `get_order_by_client_id`. Resubmit only on a confirmed not-found, after a delay (the Nautilus "probe before terminal" pattern).
3. Treat the `TradingStream` updates as primary and REST polling as the reconciliation backstop, following the Nautilus loops.
4. Build bracket/OCO natively where possible, but the risk engine must still own stop placement logic so the Sim and Robinhood adapters behave consistently. Not all brokers support `order_class`, so emulate it in the adapter layer when unsupported.

---

## Recommendation: build custom services, borrow Nautilus's patterns

**Build FlowForge as custom Python 3.12 asyncio services. Do not adopt a framework as the runtime now.**

Reasons:

1. **No broker fit.** NautilusTrader, the only framework with the architecture we want, has **no Alpaca or Robinhood adapter**. LEAN has Alpaca but requires a paid tier for local live trading and a .NET runtime. Freqtrade is crypto-only, and backtrader is dead.
2. **Topology mismatch.** FlowForge is deliberately multi-service (ingest → features → signal → LLM gate → risk → execution) over Redis Streams, with a separate learning service and dashboard. Nautilus is a **single-process, single-node-per-process kernel** with unbounded internal channels, restrictions when hosted on an external asyncio loop, and a one-worker ASGI constraint. Adopting it means either collapsing our services into its kernel or fighting it.
3. **Churn risk.** Nautilus is mid-migration to a Rust-native v2 with large breaking changes (rc4 on 2026-09-02). Writing two Rust/PyO3-era adapters now means rework.
4. **The hard parts are ours anyway.** Order-flow features from NBBO plus prints, the LLM-ish gate, and the equities-specific Sim fill model are not provided by any framework.

**What to copy deliberately** (each should become an ADR or interface in our docs):

- **Nautilus:**
  - message-bus-is-not-storage rule
  - single-threaded decision loop per service
  - component FSM
  - mandatory pre-trade risk hop with trading states (ACTIVE/REDUCING/HALTED)
  - an **order state machine** (INITIALIZED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED / CANCELED / REJECTED / EXPIRED, plus PENDING_CANCEL / PENDING_UPDATE)
  - **startup and continuous reconciliation** with "missing ≠ flat", fail-closed startup, probe-before-terminal, deterministic synthetic IDs, raw-report persistence
  - crash-only recovery
  - backtest/sandbox/live parity via a swapped clock and adapters
- **hftbacktest:** separate feed and order latency models (calibrated from recorded paper-trading acks); queue models from risk-averse to probabilistic; documented Sim caveats.
- **freqtrade:** sim/paper/live mode always visible; REST/Telegram-style control verbs (kill, flatten, pause); look-ahead and recursive-analysis checks for features; config schema validation.
- **LEAN:** pluggable fee, fill, slippage, and buying-power models in the Sim adapter; Security Master for corporate actions.
- **TypeSafe/Jev community demos:** typed, narrow model questions; mock model as default; deadline means hold; deterministic risk veto.

**Dependencies to take:**
- `alpaca-py`, pinned
- `redis` (asyncio)
- TimescaleDB Community (TSL) via the official image
- optionally `vectorbt` in a research-only environment
- optionally `hftbacktest` offline
- `tclf` as a test-only oracle

**Re-evaluate** NautilusTrader once v2.0.0 is final, *if* we need its matching-engine fidelity. Our `BrokerAdapter` and event schemas should stay close enough to Nautilus's order/event model that a later migration of the execution core remains feasible.

---

## Sources (all accessed 2026-10-19)

- https://github.com/nautechsystems/nautilus_trader
- https://pypi.org/pypi/nautilus_trader/json
- https://nautilustrader.io/docs/nightly/concepts/architecture
- https://nautilustrader.io/docs/latest/concepts/execution/reconciliation
- https://nautilustrader.io/docs/latest/concepts/live
- https://nautilustrader.io/docs/nightly/integrations
- https://newreleases.io/project/github/nautechsystems/nautilus_trader/release/v2.0.0rc4
- https://github.com/polakowo/vectorbt
- https://pypi.org/pypi/vectorbt/json
- https://vectorbt.pro/faq
- https://github.com/QuantConnect/Lean
- https://pypi.org/pypi/lean/json
- https://quantconnect.com/docs/v2/lean-cli/live-trading/brokerages/alpaca
- https://github.com/mementum/backtrader
- https://pypi.org/pypi/backtrader/json
- https://github.com/freqtrade/freqtrade
- https://pypi.org/pypi/freqtrade/json
- https://gitcode.com/GitHub_Trending/fr/freqtrade/blob/develop/README.md (mirror of the freqtrade README)
- https://github.com/nkaz001/hftbacktest
- https://pypi.org/pypi/hftbacktest/json
- https://hft.readthedocs.io/en/latest/order_fill.html
- https://github.com/KarelZe/tclf
- https://pypi.org/pypi/tclf/json
- https://pypi.org/project/flowrisk
- https://pypi.org/pypi/flowrisk/json
- https://wraquant.readthedocs.io/en/stable/_modules/wraquant/microstructure/toxicity.html
- https://pypi.org/pypi/wraquant/json
- https://gittrend.io/repo/murtazayusuf/OrderflowChart
- https://pypi.org/pypi/orderflow/json
- https://papers.ssrn.com/abstract=2182819 (BVC vs tick rule vs Lee-Ready)
- https://github.com/jarrodwatts/jev-trader
- https://aiskill.market/blog/real-time-jev-loops-games-trading
- https://aiskill.market/blog/jev-trader-high-frequency-perp-bot
- https://youmind.com/landing/x-viral-articles/jev-hft-trading-system
- https://hyper.ai/en/stories/bb830d8688b009af61895165dc2ee974
- https://github.com/alpacahq/alpaca-py
- https://pypi.org/pypi/alpaca-py/json
- https://docs.alpaca.markets/us/docs/working-with-orders
- https://docs.alpaca.markets/us/v1.1/reference/createorderforaccount
- https://forum.alpaca.markets/t/client-order-id/1137
- https://www.tigerdata.com/docs/get-started/choose-your-path/timescaledb-editions
- https://redis.io/legal/licenses/
- https://pypi.org/pypi/redis/json
- https://pypi.org/pypi/nats-py/json
- https://pypi.org/pypi/pyzmq/json
- https://docs.nats.io/using-nats/developer/develop_jetstream/model_deep_dive
- https://www.algodaily.com/lessons/what-is-a-message-queue/broker-tour-nats-jetstream
