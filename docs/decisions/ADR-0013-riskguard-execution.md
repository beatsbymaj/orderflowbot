# ADR-0013: RiskGuard, order handling, kill switch and watchdog (Phase 4)

- Status: Proposed (2026-10-26), for operator review with the Phase 4 PR
- Context: Phase 4 builds the safety layer every later strategy trades through (DECISIONS.md D7)
  and must pass chaos tests: feed drop, rejects, partials, halts, stale quotes, duplicates,
  clock skew (ROADMAP.md). Limits come only from config/risk_limits.yaml. Paper only, long only.
- Decisions:
  1. **Pure core, thin adapter.** `flowforge.risk` (guard, state, kill switch, order manager,
     calendar) is plain Python with no Nautilus import, so every rule is unit-tested.
     `flowforge.execution.strategy.GuardedStrategy` is the Nautilus base class that feeds it
     events and carries out its actions, the same in backtest and on the paper node.
  2. **RiskGuard.check_entry** collects every failed check (not just the first) and sizes the
     entry as the smallest of: risk to stop, position notional, gross exposure left, cash,
     % of recent 1-minute volume, strategy cap. It refuses on: kill switch, daily/weekly halt,
     limits file edited since start, not a buy, duplicate signal id, already holding/entering,
     not in universe, leveraged/inverse ETF, unknown or low daily dollar volume, market closed,
     outside regular hours, first/last minutes, halt/LULD pause, 5-minute cooldown after a resume,
     no quote, stale quote, quote stamped in the future (clock skew > 250 ms), locked/crossed
     NBBO, wide spread, low price, max positions, max trades per day, legacy PDT, limit too far
     from mid, bad stop or target, LULD bands. `check_exit` refuses any sell larger than the
     shares held (long only: never open a short).
  3. **Orders: IOC entry, then a stop for exactly the shares bought.** The entry is one
     marketable limit buy (ask + up to `max_slippage_bps`), immediate-or-cancel. As soon as it is
     done, one stop-market sell for the shares actually held goes to the broker. FlowForge
     watches the target itself and flattens there. There is never more than one live sell per
     trade. Native brackets were rejected: their partial-fill behaviour is wrong on the Nautilus
     simulated venue and undocumented at IBKR (docs/research/13).
  4. **Flatten = cancel, confirm, then sell.** A late stop fill can't combine with our sale into
     a short. Cancels not confirmed in 10 s turn the kill switch on and leave the stop in place.
     Exit sells are market orders, retried 3 times, then the kill switch.
  5. **Fault rules.** Stop rejected, cancelled outside FlowForge, expired, or not confirmed in
     5 s → flatten. Duplicate fill id → ignored. Fill for an unknown order or an overfill → halt
     the day (+ flatten for overfill). 3 rejected/denied entries in a row → halt the day.
     Broker vs FlowForge position mismatch for 3 checks → halt + kill switch. No fresh quotes
     while holding → warning (the broker stop protects; no blind market orders).
  6. **Loss limits** are marked every second with equity = start + realized + unrealized at the
     bid. Daily breach: flatten, halt the day. Weekly breach (from the week's peak): flatten,
     halt until the operator runs `riskctl reenable`. Halts, entry counts (for PDT) and seen
     signal ids persist in `data/state/risk_state.json`; an unreadable state file halts trading.
  7. **Kill switch** = the file named in risk_limits.yaml. On from the CLI
     (`riskctl kill`), the node, the watchdog, or (Phase 6) the dashboard. If its location
     can't be checked it reads as on. Off only by the operator (`riskctl release`, asks "yes");
     the day stays halted.
  8. **Watchdog** is its own process reading the kill flag and the node's heartbeat (written
     every second with broker positions and open orders). Ownership is handed over by time: the
     node flattens for the first 20 s after it sees the kill switch, then stops sending sells;
     the watchdog acts after 30 s if the node is still exposed, or at once if the heartbeat is
     older than 15 s. A node whose tick stalled 10 s assumes it may have been taken over and
     stops sending orders (it also keeps its stops: no cancels it can't follow with a sale).
     The watchdog connects with the node's client id because only that id can cancel the
     node's orders at IBKR; it never sells while a sell order it can see is open, and never
     more than the account holds. It flattens at most 3 times, 60 s apart, then asks for a human.
  9. **Backtest models:** fixed 150 ms latency; limit orders fill only when traded through;
     IBKR Fixed fees (Phase 1 model). Measured latency replaces this in Phase 7.
  10. **Claude defaults** (code constants, not in risk_limits.yaml; the operator can ask to move
      any into the YAML): 250 ms clock-skew tolerance; 5 min post-halt cooldown; 5 s entry
      timeout; 5 s stop confirmation; 10 s cancel confirmation; 3 exit attempts 2 s apart;
      3 consecutive rejects; 3 reconcile checks; flatten 5 min before the close; 20 s / 30 s
      kill hand-off; 15 s heartbeat staleness; 10 s freeze.
- Consequences: Phase 5 strategies subclass `GuardedStrategy` and only call `request_entry` /
  `request_exit`. Chaos tests (tests/test_chaos_sim.py on the Nautilus simulated venue,
  tests/test_order_manager.py on the pure core) are the Phase 4 gate and run in CI. Still to
  prove on IBKR paper (Phase 7 drills): IOC entries, stop placement latency, the watchdog's
  client-id takeover, restart with open positions (today a restarted node that finds shares it doesn't know
  halts and turns the kill switch on; the broker stops keep protecting them, and because the
  live node holds the client id the watchdog can only alert, so a human decides), and what IBKR
  reports as account balances.
  There is a short window (one order round trip) between the entry fill and the stop reaching
  IBKR; if the node dies inside it, the watchdog flattens after 15 s.
