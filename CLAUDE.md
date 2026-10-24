# FlowForge — project rules for Claude

Autonomous US-equities order-flow trading bot. Real money. Capital preservation first, profitability second.

## Current phase
Phase 1 (foundations) in progress since 2026-10-19. Operator decisions: Alpaca + IBKR TotalView data, long-only at launch, QuantForge skipped, spec-default risk limits. No strategy or order-placing code exists yet. See ROADMAP.md and DECISIONS.md.

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

## Order flow reference
Trader Dale's *Order Flow Trading Setups* is the project's order-flow reference (summary: /mnt/project-files/orderflow/reference/order-flow-setups-book.md). Its examples are futures/forex and untested; every setup is a hypothesis to validate walk-forward on US equities. All its setups except DOM reading are computable from trades + the quote at trade time.

## Architecture
One Nautilus TradingNode: data clients, feature indicators/actors, strategies, Jev gate actor, FlowForge RiskGuard, IB execution client. Separate processes: IB Gateway, watchdog (independent cancel/flatten), post-trade, FastAPI API, React dashboard. Node events → Redis Streams; journal in Postgres + TimescaleDB; ticks in the Nautilus Parquet catalog. Docker Compose; macOS + Linux VPS.

## Layout
- docs/research/ — one file per topic, sources dated
- docs/decisions/ — ADRs
- config/risk_limits.yaml — operator-owned limits, read-only at runtime
- src/flowforge/ — Python package (config loader, Nautilus config, ops tools)
- DECISIONS.md — Phase 0 decision summary
- ROADMAP.md — phases and promotion gates

## Commands
- Setup: `python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pre-commit install`
- Tests: `pytest --cov` (risk limits loader must stay at 100%)
- Lint: `ruff check . && ruff format --check .`
- Bypass guard: `python scripts/check_no_risk_bypass.py src`
- Local stack: `cp .env.example .env`, fill it, `docker compose up -d ib-gateway redis timescaledb`
- Read-only IBKR paper check: `docker compose --profile check run --rm ib-paper-check` (refuses non-paper settings; step by step in docs/runbooks/ib-paper-check.md)

## Agents
Subagent definitions live in .claude/agents/ (11 roles). The orchestrator owns CLAUDE.md, ROADMAP.md, DECISIONS.md and docs/decisions/.
