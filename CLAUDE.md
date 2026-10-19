# FlowForge — project rules for Claude

Autonomous US-equities order-flow trading bot. Real money. Capital preservation first, profitability second.

## Current phase
Phase 0 (research) complete, awaiting operator go-ahead. No trading code exists yet. See ROADMAP.md and DECISIONS.md.

## Non-negotiables
1. Verify APIs, tools, order types, limits, prices and rules against official docs; record URL + date in docs/research/. Unverifiable = UNVERIFIED, design around it.
2. Risk code is deterministic, unit-tested, and read from config/risk_limits.yaml (read-only at runtime). No LLM, Jev, ML or learning process changes or bypasses it. Only the operator edits limits, via commit.
3. No self-modifying live code. Learning proposes branches/PRs with walk-forward + paper evidence; operator approves promotion.
4. Stages: research → build → backtest → paper → live (confirmation) → live (autonomous within caps). Never skip. Gates in ROADMAP.md.
5. Secrets only in .env / OS keychain. Never in git, logs, Jev prompts or dashboard. Pre-commit secret scanner.
6. Log every signal, Jev call, risk decision, order, fill, rejection and error with timestamps.
7. When a capability is missing, stop and present options. No workarounds that touch real money.
8. Report net-of-slippage results, drawdowns and losing streaks. No cherry-picked windows.

## Facts from research (2026-10-19; re-verify monthly)
- Framework: NautilusTrader pinned `2.0.0rc7`, Python 3.13. No real money until 2.0.0 final + IB port merged + our paper soak tests pass.
- Broker: Interactive Brokers Pro via Nautilus IB adapter + IB Gateway (Docker). Paper on IBKR paper. Human login approval every Monday; IBC archived.
- Data: Alpaca Algo Trader Plus (full SIP trades + NBBO, ns) via custom DataClient; IBKR Nasdaq TotalView depth on ≤3 symbols. Elsewhere no depth, never faked.
- Jev: pin `jev-1.13.0`; 750 ms deadline, timeout = NO TRADE; numbers computed in code.
- PDT replaced 2026-06-04, but brokers may keep it per account until 2027-10-20; check the IBKR account's regime. T+1. Track wash sales across accounts.

## Architecture
One Nautilus TradingNode: data clients, feature indicators/actors, strategies, Jev gate actor, FlowForge RiskGuard, IB execution client. Separate processes: IB Gateway, watchdog (independent cancel/flatten), post-trade, FastAPI API, React dashboard. Node events → Redis Streams; journal in Postgres + TimescaleDB; ticks in the Nautilus Parquet catalog. Docker Compose; macOS + Linux VPS.

## Layout
- docs/research/ — one file per topic, sources dated
- docs/decisions/ — ADRs
- DECISIONS.md — Phase 0 decision summary
- ROADMAP.md — phases and promotion gates

## Commands
None yet (no code).
