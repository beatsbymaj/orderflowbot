# ADR-0006: NautilusTrader v2 as the trading framework

- Status: Proposed (2026-10-19); supersedes ADR-0001
- Context: Operator prefers NautilusTrader. With IBKR as broker, Nautilus's Stable IB adapter and identical Strategy code in backtest and live remove the main reasons ADR-0001 rejected it. v1 (1.231.0) is end-of-line; v2 is at 2.0.0rc7, requires Python 3.13, and its IB adapter is mid-port to Rust with open reconciliation bugs. See docs/research/08-nautilus-ibkr.md.
- Decision: Pin nautilus_trader==2.0.0rc7 on Python 3.13. Trading components (data clients, features, signals, Jev gate actor, risk guard, IB execution) live in one TradingNode. IB Gateway, watchdog, post-trade, API and dashboard are separate processes; node events go out over Redis Streams; journal in Postgres/TimescaleDB.
- Gate: no real money until 2.0.0 final, the IB port merged, and our paper soak tests (restart, reconnect, reconcile, no duplicate orders) pass.
- Consequences: Far less custom infrastructure (order state machine, backtester, reconciliation). We take on framework churn and must re-test on each upgrade; upgrades are explicit commits.
