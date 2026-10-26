# FlowForge — Phase 0 decisions

Status: **proposed, awaiting operator go-ahead.** Revision 2 (2026-10-19): operator chose **NautilusTrader** and **Interactive Brokers** (replacing Robinhood), confirmed trading **as an individual**, and asked for a cheaper data source than Massive. Each decision cites the research that backs it; sources and access dates are in `docs/research/`. ADRs in `docs/decisions/`.

## D1. Framework: NautilusTrader v2, pinned exactly; no real money on a release candidate
- Operator's choice. It now fits: Nautilus has a Stable Interactive Brokers adapter, and one Strategy class runs unchanged in the backtest engine and the live node (live/replay parity comes from the framework). → `08-nautilus-ibkr.md`
- Version: 1.231.0 is the last v1 and gets only security backports; v2 is at **2.0.0rc7 (2026-10-15)**, and its IB adapter is being ported to the Rust `ibapi` crate (PR #5041 open). Open IB issues include duplicate orders during reconciliation and old fills replayed as live (#4970, #5057, #5060).
- **Pin `nautilus_trader==2.0.0rc7`** for building and backtesting. **Gate for any real money:** 2.0.0 final released, IB port merged, and our own paper soak tests (restart, reconnect, reconcile, no duplicate orders) pass.
- **Python 3.13** (rc7 dropped 3.12). This changes the mission's "Python 3.12" line.
- LGPL-3.0 is fine for a private personal bot used unmodified as a library.
- Supersedes the earlier "custom services" decision (ADR-0001 → ADR-0006).

## D2. Service layout on Nautilus
- **Inside one Nautilus TradingNode:** market-data clients, feature engine (Indicators/Actors), signal engine (Strategy), Jev gate (Actor with hard deadline), FlowForge risk guard, IB execution client. The mission's `BrokerAdapter` becomes Nautilus's `ExecutionClient`; `SimAdapter` becomes the backtest engine with fill and latency models.
- **Separate processes:** IB Gateway (Docker), a watchdog that can flatten/kill independently of the node, post-trade/learning, FastAPI + React dashboard. Node publishes events to Redis Streams; Postgres + TimescaleDB holds the journal.
- The Redis Streams bus from the original spec remains, but only for traffic out of the node, not between trading components.

## D3. Market data: drop Massive; Alpaca Algo Trader Plus ($99/mo) for the tape, IBKR for depth
- **A paid feed can't be eliminated.** IEX-only free feeds carry ~4.7% of volume while off-exchange TRFs carry ~49% (Cboe summary, 2026-10-16), so free data misses most of the tape. IBKR's own tick-by-tick API is consolidated with exchange and condition codes, but allows only ~5 tick-by-tick streams at the default 100 lines, and trades and quotes use separate streams. That's 2–5 symbols. → `07-market-data-alternatives.md`
- **Cheapest that meets every requirement: Alpaca Algo Trader Plus, $99/mo.** Full SIP, unlimited symbols over WebSocket, trades with exchange + conditions + **ns timestamps** (Massive's WS was ms), NBBO quotes, live corrections/cancels, history since 2016. Saves $100/mo vs Massive.
- **Depth of book now exists, for a few names:** IBKR non-pro Nasdaq TotalView $16.50/mo (≤3 symbols at once), plus the required IBKR stock data (~$4.50–$14.50, partly waivable by commissions). Depth-based features (book imbalance, walls, pulled orders) become possible on the 1–3 active symbols only; everywhere else they stay unavailable and are not faked.
- **Total: about $120/mo** (Alpaca $99 + IBKR ~$4.50–$14.50 + TotalView $16.50 optional).
- **Cost of this choice:** Nautilus has no Alpaca adapter, so we write a custom Alpaca DataClient (WebSocket + REST backfill). It is data-only, which keeps it low-risk.
- **Cheaper pilot alternative:** IBKR data only (~$14.50) through the official adapter, limited to 2–5 symbols. Reasonable for Phases 2–3 if you want to delay the $99.
- Open: whether Alpaca's data agreement allows using the feed to trade at another broker (UNVERIFIED); which Alpaca code marks TRF prints; NBBO sizes are round lots on Alpaca and must be normalised.

## D4. Measured vs inferred order flow
- Measured: prints (price, size, venue, conditions), NBBO, spread, TRF/off-exchange, ISO sweeps, odd lots; on TotalView symbols, Nasdaq price-level depth (not full market depth, odd lots excluded).
- Inferred, labelled as such: aggressor side (quote rule → Lee-Ready → tick rule, unclassified % tracked), delta/CVD, footprint, absorption, exhaustion, iceberg-like refresh, off-exchange levels of interest, VPIN. Spoofing is only assessable on TotalView symbols, and only for Nasdaq's book.
- Earlier scanner: port its absorption/divergence/imbalance logic into Nautilus indicators; its book detectors become usable on TotalView symbols. → `06-existing-assets.md`

## D5. Broker: Interactive Brokers Pro; paper on IBKR paper (not Alpaca)
- API access needs a funded IBKR Pro account (paper too). $0 minimum; $2,000 for margin/shorting. Fractional shares are not available via API. 50 orders/sec cap. Fees: Fixed $0.005/share ($1 min) or Tiered $0.0035/share ($0.35 min) plus regulatory/exchange fees; Tiered is cheaper at small size. → `08-nautilus-ibkr.md`
- Paper stage moves to the **IBKR paper account**, so paper and live use the same adapter and order semantics. Alpaca stays only as a data vendor.
- Rich order types: limit, stop, stop-limit, trailing, bracket/OCA. Protective stops go to IBKR as bracket children so they survive a FlowForge crash.
- **Shorting becomes possible** on a margin account (≥$2,000), so long-only is no longer forced. Proposal: start long-only anyway and enable shorts after paper evidence (ADR-0005 revised).
- Robinhood MCP research (01) is kept for reference only.

## D6. Unattended operation: IBKR requires a human every week
- IBKR says headless Gateway is not supported, and the **weekly authentication cycle starts every Monday** with a manual login or IBKR Mobile approval. Daily restarts can be automatic. IBC, which automates the login screen, was **archived on 2026-09-01** (last release 3.24.2, still bundled in the `gnzsnz/ib-gateway` Docker image). → `08-nautilus-ibkr.md` B5–B8
- Design: pin the Gateway image digest; pre-market checklist fails closed if the Gateway isn't authenticated; a phone notification asks you to approve the Monday login. No trading when the Gateway is down. Fully unattended 24×7 isn't possible within IBKR's support.

## D7. Risk layering (deterministic; Nautilus's built-in risk is thin)
- Nautilus RiskEngine covers order notional, order rate, precision, cash and a trading state switch (ACTIVE/REDUCING/HALTED); there is no daily-loss or drawdown limit.
- Layers: (1) a `FlowForgeStrategy` base class where every order goes through a pure `RiskGuard.check()` reading `config/risk_limits.yaml`; (2) Nautilus RiskEngine limits set from the same file; (3) a risk Actor that flips the node to HALTED on daily/weekly loss breaches; (4) an external watchdog process with its own IB connection that can cancel and flatten; (5) a CI check that the RiskEngine bypass flag stays off. All unit-tested, ≥95% coverage.

## D8. Jev ops gate: unchanged
- Pin `jev-1.13.0`; Noul/Choice gate, Score only after our own calibration; 750 ms hard deadline with no retries (SDK default is 30 s); timeout ⇒ NO TRADE; numbers computed in code; Brier/reliability tracking; keep a gate only if it beats the rules-only baseline. Runs as a Nautilus Actor. → `02-typesafe-jev.md`, ADR-0004.

## D9. Regulation
- FINRA's PDT replacement took effect 2026-06-04, but brokers may keep old rules per account until 2027-10-20. IBKR's guide says the $25k minimum "is being removed" and describes intraday margin deficits (4+ unresolved in 12 months ⇒ 90-day restriction). **Confirm your account's regime before paper/live**; the risk engine keeps a per-account PDT flag. → `05`, `08` B3
- T+1 settlement; wash sales tracked across all your accounts in the journal (not tax advice); RTH only by default; LULD, circuit breakers and Reg SHO 201 are no-entry conditions (201 matters now that shorting is possible).

## D10. Infrastructure
Python 3.13, Nautilus Parquet data catalog for tick archives, Redis Streams (Redis 8 or Valkey) for node output, Postgres + TimescaleDB Community, Docker Compose (node, IB Gateway, watchdog, Redis, Postgres, API, dashboard), structured JSON logs, health endpoints. Runs on macOS and a Linux VPS (Gateway needs its virtual display in Docker).

## D11. Latency
IBKR order latency is unpublished (UNVERIFIED). Setups target seconds-to-minutes holds. Latency is measured in the IBKR paper stage and fed into the backtest's latency model; every report shows signal-to-fill latency and cost.

## D12. Account type: cash (2026-10-26)
The operator confirmed the IBKR account is a Cash account. `pdt_regime: cash` in
config/risk_limits.yaml: no day-trade count; RiskGuard, the backtest and the walk-forward
replay cap each day's buys at settled cash (sales settle T+1), which avoids good-faith
violations and freeriding (docs/research/05, 16). IBKR's own cash-account specifics are
UNVERIFIED; the paper account's type is checked in Client Portal before the paper run.
