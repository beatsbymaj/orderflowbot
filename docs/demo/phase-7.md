# Phase 7 demo note: paper trading program, drills, soak and scorecard

Built 2026-10-26 (ADR-0016). **Nothing here has run on the IBKR paper gateway yet.** Every
test below is offline (simulated venue, fakes for the gateway). The drills, the soak and the
4-week paper run happen on the operator's PC.

## What was built
- `flowforge.ops.paper_node`: Alpaca data → features → OrderFlowStrategy → RiskGuard →
  Nautilus RiskEngine → IBKR paper. Refuses non-paper settings twice. `--preflight` prints
  the plan and connects to nothing. Only walk-forward-passing setups trade; otherwise shadow.
- An entry on an instrument IBKR never sent is refused ("instrument not loaded from the
  broker") instead of crashing the node.
- `flowforge.ops.paper_drills`: instruments, round-trip, stop, takeover, restart; 1 share of
  SPY each through RiskGuard; pass/fail reports with measured latencies.
- `flowforge.ops.soak`: 8 stops/starts (normal and hard kill, only while flat) with clean
  restart checks.
- `flowforge.ops.scorecard`: the Phase 7 exit gate from the node's journals, refreshed after
  each close by the daily service; `/api/paper` and a **Paper gate** + **Drills** panel on
  the dashboard's Health page (screenshots: phase-7/health.jpg, phase-7/health-phone.jpg,
  demo data).
- Docker services `node` (profile paper, no auto-restart), `paper-drill`, `paper-soak`
  (profile drill), `scorecard` (profile check). Runbooks: paper-node.md, paper-drills.md.

## Test results (2026-10-26)
- 368 tests pass, 0 fail. Coverage 95% overall; risk limits loader 100%, src/flowforge/risk
  98–100% per file. Ruff check and format clean. RiskEngine bypass guard passes.
- Drills on the Nautilus simulated venue: round-trip passes (IOC fill, stop accepted, sale,
  flat); stop drill measures the stop's fill after the bid touches it; a stop that never
  triggers is sold by the timeout and the drill fails, as it should; the crash drill dies
  only after the stop is accepted; IOC misses retry 3 times; a refused entry ends the drill.
- Scorecard: a made-up 22-session, 110-trade record that is profitable after commissions
  passes; short, thin or losing records fail; entries without a risk decision, shorts,
  too many positions, unreconciled fills, mismatches and loss-limit breaches each fail it.

## Known risks and open items
1. **Instrument ids on IBKR (UNVERIFIED).** The node asks IBKR for `SPY.XNAS` etc. so data
   and orders share one id. Run the `instruments` drill first; if it fails, the node can't
   price orders (it refuses entries, it doesn't crash). docs/research/16.
2. **Live feed.** Free Alpaca = IEX only. Paper on IEX runs only with `--allow-iex` and isn't
   comparable with the SIP walk-forward. Operator decision.
3. **Day-trade rule.** `legacy_pdt` with $10,000 caps paper at ~3 trades a week; 100 trades
   would take most of a year. Operator decision (check IBKR's regime, reset the paper
   balance, or accept a longer stage). Since 2026-10-26 there is also `pdt_regime: cash`
   for a cash account: no day-trade count, buys limited to settled cash (T+1). The operator
   switches it in risk_limits.yaml after confirming the account type.
4. **No setup may trade yet.** The Phase 5 real-data walk-forward still has to run on the PC.
   Until a setup passes, paper runs in shadow and the gate can't be met.
5. The watchdog can't flatten while a live node holds the client id (ADR-0013); the restart
   drill shows the hand-off (node halts, is stopped, the watchdog sells).
