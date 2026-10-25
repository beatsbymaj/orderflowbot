# FlowForge roadmap

Each phase ends with tests passing, a demo note, and a summary to the operator (built, test results, known risks, what's needed). No phase starts without the operator's go-ahead.

| Phase | Goal | Exit gate |
|---|---|---|
| 0 Research | docs/research/, DECISIONS.md, ADRs | **Done 2026-10-19 (rev. 2: Nautilus + IBKR + Alpaca data) — awaiting go-ahead** |
| 1 Foundations (in progress) | Repo skeleton, .claude/agents/*, Compose (Nautilus node, IB Gateway, Redis, Timescale), config/ + risk_limits.yaml loader, secret scanner. IBKR paper connection: read-only account, positions, data permissions, Monday re-auth drill. ~~QuantForge review~~ (skipped by operator). | Node connects to IBKR paper and reconciles cleanly; CI green |
| 2 Data slice | Alpaca SIP DataClient for Nautilus (trades, NBBO, LULD), REST backfill, gap marking, clock checks, Parquet catalog, data-quality checks; IBKR TotalView on active names | 5 sessions ingested with gaps detected and backfilled |
| 3 Feature slice (in progress) | Streaming trade classification, delta/CVD, footprint, absorption, exhaustion, large prints, sweeps, TRF share, tape speed, NBBO dynamics, VPIN, context levels | Live vs replay parity tests identical on the same ticks |
| 4 Risk + execution slice | FlowForge RiskGuard (≥95% coverage) layered on Nautilus RiskEngine, risk actor, external watchdog, kill switch (UI/CLI/file), bracket stops, backtest fill/latency models | Chaos tests pass (feed drop, rejects, partials, halts, stale quotes, duplicates, clock skew) |
| 5 Signals + Jev gate | Hypotheses written first; rules-only baseline; Jev gate with calibration tracking | Walk-forward OOS results, multiple-testing adjusted; gate kept only if it beats baseline |
| 6 Dashboard + ops | Live, tape, signals, approvals, journal, proposals, health, settings; ntfy/Telegram; pre-market + EOD automation; runbooks | Operator walkthrough |
| 7 Paper | IBKR paper, including restart/reconcile soak tests | ≥4 weeks and ≥100 trades; positive net expectancy; DD within limits; zero risk violations; zero unreconciled orders |
| 8 Live stage 1 | IBKR live in confirmation mode, small allocation; requires Nautilus 2.0.0 final + IB port merged | ≥4 weeks; live slippage within modeled tolerance |
| 9 Live stage 2 | Autonomous within caps | Operator's written approval in docs/decisions/ |

Phase 2 progress (2026-10-25, ADR-0010): past trades + NBBO download with per-minute gap check against Alpaca bars, one backfill pass, gap marks and quality reports (`flowforge.data.alpaca_ticks`, docs/runbooks/tick-download.md). Live Alpaca DataClient (ADR-0011: trades, NBBO, halts/LULD, clock checks, outage backfill) built and tested offline; operator has no Algo Trader Plus, so live SIP is untested and Phase 7 needs a live-feed decision. TotalView still to do.

Phase 3 progress (2026-10-25, ADR-0012): feature engine (`flowforge.features`), Nautilus actor, TapeTrade custom data from the Alpaca client, replay CLI and three-path parity check (replay / backtest / live client). Exit gate passed on real ticks 2026-10-25: SPY 2026-10-23 whole session (3.8M ticks) identical across all three paths, re-checked on the final code; 25-day run on final settings: ~21 sweeps and ~9–14 large prints a day after warm-up, VPIN on 24/25 days (docs/demo/phase-3.md). Operator approved Phase 3 as done 2026-10-25 20:58Z.

Interim (2026-10-25, ADR-0009): practice backtest on 1-minute bars (opening range breakout, long only, risk limits applied). Tests the backtest machinery only; it is not a gate and doesn't replace Phases 2–5.

Automatic demotion one stage on drawdown, slippage, calibration drift or error-rate breach. Research Scout re-runs monthly.
