# ADR-0016: Phase 7 paper node, drills, restart soak and the paper scorecard

- Status: Proposed (2026-10-26). Built and tested offline; nothing has run on the IBKR paper
  gateway yet. The drills and the 4-week paper run are the operator's to start.
- Context: ROADMAP Phase 7 is "IBKR paper, including restart/reconcile soak tests", with the
  exit gate ≥4 weeks and ≥100 trades, positive net expectancy, drawdown within limits, zero
  risk violations and zero unreconciled orders. ADR-0013 left the IBKR-side checks to this
  phase: IOC entries, stop placement latency, the watchdog's client-id takeover and restart
  with open positions. The Phase 5 real-data walk-forward has not run yet (operator's PC).
- Decisions:
  1. **One paper node** (`flowforge.ops.paper_node`): a Nautilus LiveNode with the Alpaca data
     client, the IB execution client, FeatureActor and OrderFlowStrategy on GuardedStrategy,
     the Nautilus RiskEngine from risk_limits.yaml (never bypassed) and reconciliation on.
     It refuses to start unless the IB settings say paper (port 4002/4004/7497, account
     "DU…", IB_TRADING_MODE=paper), checked once when planning and again before building.
  2. **Which setups trade is decided by the walk-forward, not by a flag.** Only setups with
     `passes: true` in reports/phase5/walkforward.json may be enabled, each with the stop and
     target its last training fold chose. `--setups` can narrow that list, never widen it.
     With no passing setup (or no result file) the node runs in **shadow**: same data,
     features, signals and journal, no entries. Shadow is how Phase 7 starts today.
  3. **One instrument id for data and orders.** The Alpaca data uses `SYMBOL.XNAS`. The IB
     instrument provider loads the same ids (`load_ids`, `symbol_to_mic_venue` = XNAS for each
     name) and the IB client is added with `RoutingConfig(default=True)`, the only execution
     client, so every order goes to IBKR. UNVERIFIED on a live gateway (docs/research/16);
     drill `instruments` checks it before any order drill. An entry on an instrument the
     broker never sent is refused ("instrument not loaded from the broker") instead of
     crashing.
  4. **Feed.** `sip` by default. The free Alpaca plan only streams IEX; `--allow-iex` runs on
     it anyway and journals that results aren't comparable with the SIP walk-forward. This
     is an operator decision (Algo Trader Plus, IEX, or another feed).
  5. **Drills** (`flowforge.ops.paper_drills`): 1 share of SPY each, only through
     request_entry/request_exit, with the node's real risk state and kill switch so drills
     count toward the day's trade and day-trade limits. `instruments` (no orders),
     `round-trip`, `stop` (stop 2 cents under the bid), `takeover` (child node buys, dies
     with its stop resting, the real watchdog cancels and sells) and `restart` (child node
     dies holding; a fresh node must find the unknown shares, halt and turn the kill switch
     on without sending orders; then the watchdog flattens). Each writes a pass/fail report
     with measured latencies to reports/paper/. The drills that end with the kill switch on
     leave it on: releasing it with riskctl is part of the drill.
  6. **Restart soak** (`flowforge.ops.soak`): start the node, run 2–10 minutes, stop it,
     SIGTERM and SIGKILL in turn, only while the heartbeat shows it flat; after each start
     the heartbeat must be fresh within 90 s with the same positions, no open orders after a
     flat stop, and no mismatch, error or kill switch. Restarts while holding are the
     `restart` drill's job.
  7. **Scorecard** (`flowforge.ops.scorecard`): the exit gate computed from the node's
     journals only, every day since the first paper start, trades rebuilt from order events,
     net of commissions. Risk violations are things the risk layer should make impossible
     (an entry without an allowed decision, a short, more positions than the limit); orders
     the Nautilus RiskEngine denied are counted separately. Refreshed by the daily service
     after each close and shown on the dashboard's Health page with the latest drills.
  8. **No auto-restart of the node** in Docker (`restart: "no"`): a crashed node stays down,
     the watchdog flattens, a human restarts it.
  9. **Redis Streams / TimescaleDB review (from ADR-0015): still deferred.** The scorecard
     reads 4 weeks of JSON lines in seconds; revisit if it gets slow or a second consumer
     needs the events live.
- Consequences: Phase 7 can start in shadow as soon as the gateway, the watchdog and the
  node are up; trading in paper starts only after a setup passes the real-data
  walk-forward. Known blockers, both operator decisions: (a) the live feed (above);
  (b) the day-trade rule: risk_limits.yaml says `legacy_pdt` with 3 day trades per 5
  business days under $25,000, and the paper account holds $10,000, so 100 trades would take
  most of a year. Options: confirm the IBKR account is on the new intraday-margin regime and
  change `pdt_regime` (operator-only edit), reset the paper balance in Client Portal
  (then live must match it), or accept a longer paper stage.
