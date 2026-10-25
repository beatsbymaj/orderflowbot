# ADR-0011: Alpaca live stream as a Python Nautilus data client

- Status: Accepted (2026-10-25). The operator has **no** Algo Trader Plus plan, so live SIP is unavailable for now; the client is built and tested against the free `test` stream (FAKEPACA) and the free `iex` feed.
- Context: Nautilus 2.0.0rc7 has no Alpaca adapter. Its Python adapter API (`nautilus_trader.live.clients.MarketDataClient` + `DataClientFactory`) runs async hooks on the node's loop; a prototype confirmed trades reach a `DataActor` in a `LiveNode`.
- Decisions:
  1. Two layers. `alpaca_stream.py` handles the protocol (auth, subscribe, parse, reconnect with backoff 1→30 s, clock check) and knows nothing about Nautilus. `alpaca_data_client.py` maps records to `TradeTick`, `QuoteTick` and `InstrumentStatus`. Tests drive both with a fake socket inside a real `LiveNode`.
  2. `ts_event` = exchange time from Alpaca; `ts_init` = our receive time. The gap between them is the clock check: over 2 s is "stale", and our clock more than 250 ms behind is "clock skew". Both are journaled and totalled.
  3. Refusals a reconnect can't fix (402 auth, 406 connection limit, 409 insufficient subscription, ...) stop the stream and are journaled as `fatal`. There's no retry loop against a refusal (CLAUDE.md rule 7).
  4. Corrections, cancel/errors, LULD bands, non-halt statuses, outages and backfills have no Nautilus type yet. They go to `data/live/journal.jsonl`. Canceled trades are not passed to the node. Phase 3 consumes the journal for delta corrections.
  5. Halt/pause/resume/SSR status codes map to `MarketStatusAction` (table in the module). Imbalance and price-indication codes are journaled only.
  6. Outages: once the plan's delay has passed (15 min + 1 min on free keys), the window is re-downloaded over REST into the catalog with the history download's gap checks. Backfilled ticks are **not** replayed into the running node; an outage is missing data for strategies.
  7. Live quote sizes use multiplier 1 (shares). Alpaca's real-time page still says round lots; its REST page says shares since 2025-11-03. **UNVERIFIED** until a live session is compared with REST history for the same minutes.
  8. Dependency: `websockets==15.0.1` (pure Python), exact pin.
- Consequences: Paper trading (Phase 7) needs a full-market live feed. On free keys only IEX is live (about 2–5% of volume), which Phase 3 can't use for order flow. The live data source must be decided before Phase 7. Options: Algo Trader Plus ($99), IBKR tick-by-tick on ≤3 symbols, or another vendor (doc 07).
