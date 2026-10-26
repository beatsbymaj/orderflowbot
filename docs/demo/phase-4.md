# Phase 4 demo note: risk guard, orders, kill switch, watchdog

What exists (ADR-0013, research doc 13):
- `flowforge.risk`: RiskGuard (every entry checked and sized from config/risk_limits.yaml),
  RiskState (daily/weekly loss halts, PDT counts, persisted), kill switch file flag, NYSE
  calendar 2026–2028, OrderManager (what to do on every broker event).
- `flowforge.execution.GuardedStrategy`: the Nautilus base class Phase 5 strategies will use.
  IOC entry → stop for exactly the shares bought → target watched in code → flatten = cancel,
  confirm, sell. Journal of every decision, order, fill and alert; heartbeat file.
- `python -m flowforge.ops.riskctl status|kill|release|reenable` and the `watchdog` process
  (docs/runbooks/kill-switch.md).
- Backtest latency and fill models (`flowforge.execution.models`).

## Exit gate: chaos tests (all pass, 2026-10-26, Nautilus 2.0.0rc7 simulated venue)
| Fault | Test | What FlowForge did |
|---|---|---|
| Feed drop (90 s, no data) | `test_feed_drop_blocks_entries_and_warns_while_holding` | Refused the entry (stale quote), warned that the held position relies on the broker stop, logged when quotes came back |
| Stale quotes (3 s late) | `test_stale_and_future_stamped_quotes_are_refused` | Refused: "stale quote (3000 ms old)" |
| Clock skew (quotes 1 s in the future) | same | Refused: "clock skew" |
| Halt + resume | `test_halt_blocks_entries_then_cooldown_after_resume` | Refused during the halt and for 5 min after; allowed after |
| Rejected entries | `test_rejected_entries_halt_trading_for_the_day` | 3 denials in a row → day halted; 4th refused before an order was built |
| Stop rejected after the buy filled (gap through the stop) | `test_stop_rejected_after_entry_fill_is_flattened` | Critical alert, sold the shares at once, flat |
| Partial fill (only 4 of 9 shares offered) | `test_partial_fill_protects_only_the_shares_bought` | Kept 4 shares with a stop for exactly 4 |
| Duplicate signal | `test_duplicate_signal_sends_one_bracket` | One order; the repeat refused as a duplicate |
| Kill switch file | `test_kill_switch_file_flattens_and_blocks` | Cancelled the stop, then sold; later entries refused |
| Daily loss limit | `test_daily_loss_limit_flattens_and_halts` | Flattened, halted for the day |
| End of day | `test_end_of_day_flatten` | Flattened at 15:55 ET |
| Someone else trades the account | `test_unknown_position_in_the_account_halts_entries` | Mismatch → halt + kill switch |
| Node freezes | `test_frozen_node_stops_sending_orders` | Stopped sending orders and kept its stop |
| Kill hand-off to the watchdog | `test_after_the_kill_window_only_the_watchdog_sells` | Node stops selling after 20 s |
| Gap through the stop | `test_stop_gap_down_fills_through_and_the_loss_is_journaled` | Filled at 98.50, below the 99.00 stop; loss journaled |

Plus 29 order-lifecycle fault tests on the pure core (duplicate and unknown fills, overfill,
cancel refused, stop racing a flatten, exit rejected 3 times, oversell → kill switch, …),
36 RiskGuard tests, 10 state/calendar/kill-switch tests, 12 watchdog tests (including its
flatten run on the simulated venue: cancel first, then sell exactly the position) and 3 CLI
tests. RiskGuard package coverage: 99% (CI gate 95%). Whole repo: 96%.

## Found and fixed by the chaos tests
- Nautilus brackets release their stop at the full order size on a partial fill (the venue then
  rejects it) or not at all until the parent completes. → Switched to IOC entry + separate stop.
- A rejected OCO target during a flatten stalled the flatten. → Treated as gone.
- A frozen node could cancel its stop and then be blocked from selling. → It keeps its stops.

## Known limits (to prove on IBKR paper in Phase 7)
- IOC entries, stop placement time and `reduce_only` through the Nautilus IB adapter.
- The watchdog reconnecting as the node's client id after the node dies.
- A restarted node with open shares halts and needs a human (no automatic adoption yet).
- Between the entry fill and the stop reaching IBKR there is one order round trip without a stop.
- No live trading code path exists; nothing here has placed a paper order yet.
