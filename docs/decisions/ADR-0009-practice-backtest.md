# ADR-0009: Practice backtest on 1-minute bars before the data and feature slices

- Status: Accepted (2026-10-25, operator chose free Alpaca data)
- Context: After Phase 1 the operator asked for a backtest on past data. The roadmap's real backtests need the Phase 2 tick data and Phase 3 order-flow features, which don't exist yet. The free Alpaca key gives full-market (SIP) 1-minute bar history (docs/research/09).
- Decision: Add an offline practice backtest: an opening range breakout, long only, on 1-minute bars for SPY, QQQ, AAPL, MSFT, NVDA, run in Nautilus `BacktestEngine` with IBKR Pro Fixed fees, one-tick slippage and the operator's `config/risk_limits.yaml` (sizing, 3 positions, daily/weekly loss halts, trades per day, legacy PDT, session windows). Parameters are fixed up front, not tuned. The report shows net-of-fees results, a slippage stress test, drawdown, losing streak, a two-thirds/one-third split and a buy-and-hold SPY comparison.
- Not a promotion gate: it exercises the backtest machinery and the limit logic. It is not evidence for any order-flow hypothesis and doesn't skip Phases 2–5.
- Differences from live: the weekly drawdown halt resumes the next week (live needs a manual re-enable) so the whole period gets tested; spread, NBBO deviation and staleness limits can't be checked on bars.
- Consequences: The backtest code (`src/flowforge/backtest/`) is the start of Phase 4's fill/fee models. The limit checks there are backtest-only; the Phase 4 RiskGuard is still required.
