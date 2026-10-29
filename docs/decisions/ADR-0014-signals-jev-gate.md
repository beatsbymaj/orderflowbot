# ADR-0014: Phase 5 signals, walk-forward test and Jev gate

- Status: Proposed (2026-10-26), for operator review with the Phase 5 results
- Context: ROADMAP Phase 5: hypotheses written first, a rules-only baseline, a Jev gate with
  calibration tracking; exit gate = walk-forward out-of-sample results, adjusted for multiple
  testing, and the gate kept only if it beats the baseline. Long only, paper only.
- Decisions:
  1. **Pre-registration.** The five setups, the 4-configuration grid, the data window, the
     folds, the cost model and the pass bar are fixed in docs/research/14-signal-hypotheses.md,
     committed (b11f264) before any signal ran on real data. Wording clarifications made while
     coding, still before any real-data run, are dated in that file.
  2. **Setups are pure code** (`flowforge.signals.setups`): feature records in, signals out.
     The same `SignalEngine` and `plan` run in the fast simulator, the Nautilus backtest and the
     paper node. A signal carries the zone, the heavy-volume areas above, unfinished lows, the
     prior-day range (ATR proxy) and labelled facts for Jev; `plan` turns it into a stop and a
     target or "no trade" (stop band 10-20% of ATR, unfinished-business veto, 1.5R minimum for
     volume targets).
  3. **Fast simulator for the grid, Nautilus to check it.** `flowforge.research.fastsim` replays
     NBBO quotes from the Phase 2 catalog (150 ms latency, IOC at ask + 10 bps, stop and target
     on the bid, flat 15:55). `flowforge.research.crosscheck` runs the same stock-day through a
     Nautilus BacktestEngine with FeatureActor -> OrderFlowStrategy -> GuardedStrategy and
     compares trade by trade.
  4. **Two result measures.** "Edge" = net R per trade after spread, slippage and per-share
     IBKR costs at $50 risk (the test statistic). "Account check" = the same trades on a
     $10,000 cash account with every limit in risk_limits.yaml and the $1 minimum commission
     (the money the operator would actually see). Both are reported.
  5. **Statistics** (`flowforge.research.stats`, standard library): day-block bootstrap,
     Holm across H1-H5, deflated Sharpe with N = 20 trials, plus a random-entry control.
  6. **OrderFlowStrategy** subclasses GuardedStrategy and only calls `request_entry`. Setups
     are enabled per name and configuration; only setups that pass the gate may be enabled
     for paper. Every signal, skipped signal, Jev request and Jev decision is journaled.
  7. **Jev gate** (`flowforge.gate`): ADR-0004 unchanged. Pinned `jev-1.13.0` (other models
     refused, also in the answer), 750 ms total deadline enforced on a worker thread and on the
     node clock, no retries, any failure = NO TRADE, circuit closed after 5 failures in a row.
     State = labelled facts only (no prices, account or positions). Off by default
     (config/jev.yaml); shadow mode asks and logs but never vetoes. Offline test with a
     per-fold threshold from {0.5, 0.6, 0.7}, answers cached to disk, Brier and reliability
     tracked; kept only if it raises daily net R out of sample (p < 0.05) and beats the base
     rate's Brier score.
- Consequences: The walk-forward needs ~3 months of ticks (about 12 GB, many hours on the
  free Alpaca plan) and runs on the operator's PC. If no setup passes, nothing is enabled for
  paper trading and Phase 7 can only test the plumbing (orders, stops, watchdog), not
  profitability. A TypeSafe API key is the operator's choice; without one the gate stays off.
