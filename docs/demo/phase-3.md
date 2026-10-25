# Phase 3 demo note: order-flow features

What exists (`src/flowforge/features/`, ADR-0012):
- One engine for live and replay: trades and quotes in, feature records out, no wall clock.
- Nautilus `FeatureActor`: live it reads `TapeTrade` custom data (full trades with venue and conditions) from the Alpaca client; in a backtest it reads trade ticks plus the raw trade files.
- `python -m flowforge.features.replay`: features for every downloaded stock-day + summary.
- `python -m flowforge.features.parity`: the exit gate, three paths over the same ticks.

## Features and whether they are measured or inferred
"Measured" = read straight off the tape or NBBO. "Inferred" = depends on our classification or a threshold we chose (hypothesis until Phase 5 tests it).

| Feature | Where | Kind | Definition |
|---|---|---|---|
| Aggressor side | each trade | inferred | Quote rule at the NBBO in force; tick test at the midpoint or when locked/crossed/missing; none for off-market and auction prints |
| Delta, CVD | bar | inferred | Ask (buyer-initiated) − Bid volume; CVD resets each day |
| Footprint | bar | inferred | Bid / Ask / unsided volume per price row (row ≈ 1 bp of price) |
| POC (HVN) | bar, session | measured | Row with the most volume |
| Imbalances, stacked | bar | inferred | Diagonal Ask vs Bid ≥ 300%, ≥ 1,000 sh; 3+ in a row |
| Unfinished business | bar | inferred | High row with Bid > 0, low row with Ask > 0 |
| Big limit order | bar | inferred | One cell ≥ 6× the average cell of the last 20 bars |
| Absorption | bar | inferred | Bid and Ask both ≥ 3× average at one row, range ≤ median of last 20 bars |
| Exhaustion | bar | inferred | New 6-bar high with Ask at the top row ≤ 10% of the average row (mirror for lows). Not from the book |
| Delta / CVD divergence | bar | inferred | Up bar with negative delta (and mirror); new high with CVD below its recent high (and mirror) |
| Large prints | event | measured | Single market-priced print ≥ the prior day's 10th-largest print (floor $2M), so ~5–10 a day |
| Sweeps | event | measured + inferred | ISO (F) prints, one side, ≤ 5 ms apart, ≥ 2 venues, notional ≥ the prior day's 20th-largest sweep as the engine itself saw it (`next_sweep_usd`; side-blind ISO-cluster estimate only when the prior day wasn't replayed; floor $250k) |
| Off-exchange share | bar | measured | Volume on venue D (FINRA TRF/ADF) |
| ISO and odd-lot share | bar | measured | Volume with F / I |
| Tape speed | bar | measured | Trades/s, shares/s, busiest second, vs the last 20 bars |
| NBBO dynamics | bar | measured | Updates, mid ups/downs and change, time-weighted spread ($ and bps), size imbalance, time locked/crossed/missing |
| VPIN | bar | inferred | Buckets of 1/200 of the prior day's volume, 50-bucket window (about a quarter day), restarted each day |
| Context | bar | measured | VWAP ± σ, session POC/VAH/VAL (70%), opening range (30 min), prior day H/L/C, session H/L |
| Levels | bar | inferred | Stacked-imbalance, absorption, multiple-node, unfinished and large-print zones from 5/30-min bars, with bars-away and touch counts |

No depth-of-book features: the data has none (TotalView not wired yet), and none are faked.

## Results
- Offline (cloud, made-up ticks): replay, backtest and live paths identical; 148 tests pass, 97% coverage.
- **Exit gate on real ticks (operator's PC, 2026-10-25):** SPY 2026-10-23, whole session, 3,795,698 ticks (488,347 trades after 6 cancels, 3,307,351 quotes): replay, Nautilus backtest and live client all **IDENTICAL**, 1,932 records each, hash `75e5475936404de2`. Times 226 s / 233 s / 288 s; live client sent = received for every trade and quote. Re-checked on the final code (7e3df67): **IDENTICAL**, 501 records each, hash `afd618009d668231`, 244 s / 246 s / 344 s, live sent = received (fewer records because the tuned filters flag fewer events).
- **All 25 stock-days (first settings), 94 min on the PC, no errors:** late events 0, gap bars 0, unknown condition codes none. Quote rule 71–82% of trades, tick test 16–24% (mostly midpoint prints), no side 2–6%; NBBO locked/crossed at trade time 0.1–2%. Off-exchange (venue D) 38–55% of volume, in line with the market's TRF share, which supports the D = TRF reading. Per 5-min bar day (79 bars): absorption 2–15, stacked imbalances 0–9, big cells 1–8, exhaustion ~14, CVD divergence 13–21.
- What that run showed and what changed (2026-10-25): the fixed $2M large-print filter gave 24–392 a day (book target 5–10) → threshold now adapts to the prior day's 10th-largest print ($3.7–11.4M on these days). 2-venue ISO sweeps were everyday flow (96–3,104 a day) → now sized by the prior day's 20th-largest ISO cluster. VPIN stayed empty on 15 of 25 days because a day rarely filled the 50-bucket window → buckets are 1/200 of the prior day's volume. These changes need the 25-day run again to confirm the new counts.
- Re-run, first days (2026-10-25): SPY 10-20 gave 11 large prints (target met) but only 3 sweeps. The ISO-cluster estimate merges buy and sell ISOs within 5 ms, so it ran far above the engine's one-sided sweeps. Fixed: the engine now reports `next_sweep_usd` (its own 20th-largest qualifying sweep) and the replay CLI carries it into the next day. Also fixed: large-print records reported `notional` in millions by mistake (3.75 instead of 3,750,150); the filter itself was right. The first day of each symbol has no prior day, so it runs on the floors only and counts as warm-up.

- **All 25 stock-days on the final code (7e3df67), 89 min on the PC, no errors:** late events 0, unknown condition codes none, classification shares as before. On the 20 tuned days (each symbol's days 2–5):
  - Large prints: 8.8–13.8 a day on average per symbol, single days 1 (QQQ 10-23) to 32 (QQQ 10-22). Book target 5–10.
  - Sweeps: 418 in total, **20.9 a day** (target 20); per symbol SPY 15.3, QQQ 19.8, AAPL 26.3, MSFT 24.8, NVDA 18.5; single days 2 (MSFT 10-23) to 43 (AAPL 10-22). Every kept sweep is ≥ about $1M.
  - VPIN: non-null on 24 of 25 days (was 10 of 25); first value 14:15–17:00Z, mostly 45–140 min after the open. The miss is MSFT 10-19 (warm-up, 48 buckets).
  - Warm-up days (10-19, no prior day) run on the floors only: 33–245 large prints and 145–2,368 sweeps. Treat a symbol's first downloaded day as warm-up, or download one extra day before the test window.
  - Day-to-day spread is wide because each day is sized from yesterday alone; smoothing (e.g. a 5-day median) is a Phase 5 question.
  - Full table: `data/features/summary.md` on the PC (results not in git).

## Known risks
- Thresholds are guesses until Phase 5 walk-forward tests; adaptive filters swing day to day (sweeps 2–43, large prints 1–32).
- A live run needs yesterday's `next_sweep_usd` and prior-day stats seeded before the open (Phase 4/5 task); without them it runs like a warm-up day.
- Alpaca's trade timestamp type and TRF mapping are UNVERIFIED (docs/research/11).
- Live bars close on the next event, not a timer; outages aren't yet marked in live bars.
