# FlowForge roadmap

Each phase ends with tests passing, a demo note, and a summary to the operator (built, test results, known risks, what's needed). No phase starts without the operator's go-ahead.

| Phase | Goal | Exit gate |
|---|---|---|
| 0 Research | docs/research/, DECISIONS.md, ADRs | **Done 2026-10-19 (rev. 2: Nautilus + IBKR + Alpaca data) — awaiting go-ahead** |
| 1 Foundations | Repo skeleton, .claude/agents/*, Compose (Nautilus node, IB Gateway, Redis, Timescale), config/ + risk_limits.yaml loader, secret scanner. IBKR paper connection: read-only account, positions, data permissions, Monday re-auth drill. QuantForge review. | Node connects to IBKR paper and reconciles cleanly; CI green |
| 2 Data slice | Alpaca SIP DataClient for Nautilus (trades, NBBO, LULD), REST backfill, gap marking, clock checks, Parquet catalog, data-quality checks; IBKR TotalView on active names | 5 sessions ingested with gaps detected and backfilled |
| 3 Feature slice | Streaming trade classification, delta/CVD, footprint, absorption, exhaustion, large prints, sweeps, TRF share, tape speed, NBBO dynamics, VPIN, context levels | Live vs replay parity tests identical on the same ticks |
| 4 Risk + execution slice | FlowForge RiskGuard (≥95% coverage) layered on Nautilus RiskEngine, risk actor, external watchdog, kill switch (UI/CLI/file), bracket stops, backtest fill/latency models | Chaos tests pass (feed drop, rejects, partials, halts, stale quotes, duplicates, clock skew) |
| 5 Signals + Jev gate | Hypotheses written first; rules-only baseline; Jev gate with calibration tracking | Walk-forward OOS results, multiple-testing adjusted; gate kept only if it beats baseline |
| 6 Dashboard + ops | Live, tape, signals, approvals, journal, proposals, health, settings; ntfy/Telegram; pre-market + EOD automation; runbooks | Operator walkthrough |
| 7 Paper | IBKR paper, including restart/reconcile soak tests | ≥4 weeks and ≥100 trades; positive net expectancy; DD within limits; zero risk violations; zero unreconciled orders |
| 8 Live stage 1 | IBKR live in confirmation mode, small allocation; requires Nautilus 2.0.0 final + IB port merged | ≥4 weeks; live slippage within modeled tolerance |
| 9 Live stage 2 | Autonomous within caps | Operator's written approval in docs/decisions/ |

Automatic demotion one stage on drawdown, slippage, calibration drift or error-rate breach. Research Scout re-runs monthly.
