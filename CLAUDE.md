# FlowForge — project rules for Claude

Autonomous US-equities order-flow trading bot. Real money. Capital preservation first, profitability second.

## Current phase
Phases 1–3 merged 2026-10-25; Phase 4 (risk + execution) built 2026-10-26, awaiting review; Phase 5 (signals + Jev gate) built 2026-10-26, real-data walk-forward pending on the operator's PC; Phase 6 (dashboard, alerts, daily reports) built 2026-10-26, operator walkthrough pending. Operator decisions: Alpaca + IBKR TotalView data, long-only at launch, QuantForge skipped, spec-default risk limits; 2026-10-26 operator asked to continue through Phase 7 (paper) without a separate go per phase, never live money. Order code exists only behind RiskGuard (`GuardedStrategy`); `OrderFlowStrategy` trades only setups explicitly enabled, and none is enabled until one passes the Phase 5 gate. See ROADMAP.md and DECISIONS.md.

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
One Nautilus TradingNode: data clients, feature indicators/actors, strategies, Jev gate actor, FlowForge RiskGuard, IB execution client. Separate processes: IB Gateway, watchdog (independent cancel/flatten), post-trade, alert relay, daily reports, FastAPI API + no-build dashboard. Node events → JSON-lines journals (Redis Streams / TimescaleDB deferred, ADR-0015); ticks in the Nautilus Parquet catalog. Docker Compose; macOS + Linux VPS.

## Layout
- docs/research/ — one file per topic, sources dated
- docs/decisions/ — ADRs
- config/risk_limits.yaml — operator-owned limits, read-only at runtime
- src/flowforge/ — Python package (config loader, Nautilus config, ops tools)
- DECISIONS.md — Phase 0 decision summary
- ROADMAP.md — phases and promotion gates

## Commands
- Setup: `python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pre-commit install`
- Tests: `pytest --cov` (risk limits loader must stay at 100%, src/flowforge/risk ≥ 95%)
- Lint: `ruff check . && ruff format --check .`
- Bypass guard: `python scripts/check_no_risk_bypass.py src`
- Local stack: `cp .env.example .env`, fill it, `docker compose up -d ib-gateway redis timescaledb`
- Practice backtest (offline, no orders): `docker compose --profile backtest run --rm bars-download`, then `... run --rm backtest` (docs/runbooks/backtest.md)
- Tick download (past trades + NBBO, gap check, catalog): `docker compose --profile data run --rm ticks-download` (docs/runbooks/tick-download.md)
- Live stream watch (no trading): `docker compose --profile data run --rm live-watch` (docs/runbooks/live-stream.md)
- Phase 3 features (offline): `docker compose --profile features run --rm features`; parity gate: `... run --rm features-parity` (docs/runbooks/features.md)
- Kill switch / halts: `docker compose run --rm riskctl status|kill|release|reenable`; watchdog: `docker compose --profile paper up -d watchdog` (docs/runbooks/kill-switch.md)
- Phase 5 walk-forward (offline, no orders): `docker compose --profile research run --rm walkforward` (`--jev` for the gate; docs/runbooks/walkforward.md). Hypotheses are pre-registered in docs/research/14; don't change them after seeing results.
- Dashboard (read only + kill ON): `docker compose --profile paper up -d dashboard` → http://localhost:8080; demo data: `docker compose --profile demo up --build dashboard-demo` (docs/runbooks/dashboard.md)
- Phone alerts and daily reports: `docker compose --profile paper up -d alerts daily` (docs/runbooks/alerts.md, daily-ops.md)
- Read-only IBKR paper check: `docker compose --profile check run --rm ib-paper-check` (refuses non-paper settings; step by step in docs/runbooks/ib-paper-check.md)

## Agents
Subagent definitions live in .claude/agents/ (11 roles). The orchestrator owns CLAUDE.md, ROADMAP.md, DECISIONS.md and docs/decisions/.
