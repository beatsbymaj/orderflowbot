# ADR-0017: The kill switch may turn itself off when the paper account is empty

- Status: Accepted (2026-10-30, operator). Paper only.
- Context: on 2026-10-30 a paper drill hit a broker bug (`reduce_only`, docs/research/16), the
  node pulled the kill switch, and every step after that needed the operator at the PC. The
  takeover and restart drills pull the switch on purpose, so a full drill run needed the
  operator twice more. The operator asked for this to happen automatically and, offered three
  options (a dashboard button shown only when the account is empty; release by this code when
  it is safe; keep `riskctl release`), chose release when it is safe.
- Decision:
  1. `kill_switch.auto_release_when_flat` in config/risk_limits.yaml (default false; the
     operator set it true by commit). Only the operator changes it.
  2. `flowforge.ops.auto_release` is fixed, unit-tested code (no LLM, Jev or ML in the
     decision). It turns the switch off only if all hold: the setting is true; the IB settings
     say paper (same guards as ib_paper_check); the switch was pulled by the node, the
     watchdog or a drill (`by` in the flag file), never `operator` or `dashboard`, and never
     an unreadable flag; IBKR answers and shows 0 open positions and 0 open orders.
  3. It never clears a daily or weekly halt. A trip still stops trading for the rest of that
     day; the next trading day starts with the switch off if the account is empty.
  4. Every decision, release or not, is journaled to data/journal/ops/auto_release.jsonl.
  5. `paper_drills all` runs the five drills in order, checks the account is empty before each
     and uses this rule between them; the Windows morning task (docs/runbooks/morning.md)
     runs it before the watchdog and the node start.
- Consequences: a node or watchdog kill no longer waits for the operator once the account is
  flat. An operator kill still does. Before any live-money stage this setting is reviewed again
  (ROADMAP gates); it is not carried over to live by default.
