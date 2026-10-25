# ADR-0010: How past ticks are stored (Phase 2 data slice)

- Status: Accepted (2026-10-25)
- Context: Phase 2 needs every trade and NBBO quote in the Nautilus Parquet catalog, with gaps found and backfilled. The operator's free Alpaca keys allow SIP history older than 15 minutes (docs/research/10). Nautilus `TradeTick` has no venue or condition fields.
- Decisions:
  1. Instruments are `<SYMBOL>.XNAS` (same simulated venue as the bar backtest) with **6 price decimals**, so sub-penny off-exchange prints (e.g. midpoints) are not rounded. Mapping to IBKR's tradable instrument ids is a Phase 4 task.
  2. `trade_id` is `<exchange>-<exchange trade id>`, unique because Alpaca ids are per exchange. Raw trades (price, size, exchange, conditions, tape, update flag) go to `data/ticks/raw/<SYMBOL>/<day>/trades_<HHMM>.csv.gz`, joinable on that id. Phase 3 reads conditions from there.
  3. `aggressor_side` is always `NO_AGGRESSOR` in storage. Classification is Phase 3's job and must be the same code live and in replay.
  4. Canceled/incorrect trades stay in raw files, not in the catalog.
  5. Quote sizes before 2025-11-03 are multiplied by 100 (Alpaca reported round lots then). This assumes a 100-share round lot, true for SPY, QQQ, AAPL, MSFT, NVDA before the SEC round-lot change; check before adding very high-priced names.
  6. Gap rule: per minute, our trades (minus M/Q/9 conditions and cancels) must equal Alpaca's minute-bar trade count. Short minutes are re-downloaded once; anything still short is marked in `data/ticks/quality/<SYMBOL>_<day>.json` with `filled: false` and the day's status is `gaps`. Quote silences over 30 s in regular hours are re-downloaded once and marked, as warnings.
  7. Regular hours only for now (quotes from 09:25 so the first trade has an NBBO). Trading days come from SPY's minute bars, so holidays and half days need no hand-kept calendar.
- Consequences: A rerun of a day deletes that day's ticks first, so the catalog never holds two copies. Gap marks are the input Phase 3 uses to skip or flag windows. The live WebSocket client (next PR) will reuse the same records and backfill path.
