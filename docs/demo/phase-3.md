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
| Large prints | event | measured | Single market-priced print ≥ $2M (tune to 5–10 a day) |
| Sweeps | event | measured + inferred | ISO (F) prints, one side, ≤ 5 ms apart, ≥ 2 venues, ≥ 1,000 sh |
| Off-exchange share | bar | measured | Volume on venue D (FINRA TRF/ADF) |
| ISO and odd-lot share | bar | measured | Volume with F / I |
| Tape speed | bar | measured | Trades/s, shares/s, busiest second, vs the last 20 bars |
| NBBO dynamics | bar | measured | Updates, mid ups/downs and change, time-weighted spread ($ and bps), size imbalance, time locked/crossed/missing |
| VPIN | bar | inferred | 50 buckets of 1/50 of the prior day's volume |
| Context | bar | measured | VWAP ± σ, session POC/VAH/VAL (70%), opening range (30 min), prior day H/L/C, session H/L |
| Levels | bar | inferred | Stacked-imbalance, absorption, multiple-node, unfinished and large-print zones from 5/30-min bars, with bars-away and touch counts |

No depth-of-book features: the data has none (TotalView not wired yet), and none are faked.

## Results
- Offline (cloud, made-up ticks): replay, backtest and live paths identical; 144 tests pass, 97% coverage.
- **Exit gate on real ticks (operator's PC, 2026-10-25):** SPY 2026-10-23, whole session, 3,795,698 ticks (488,347 trades after 6 cancels, 3,307,351 quotes): replay, Nautilus backtest and live client all **IDENTICAL**, 1,932 records each, hash `75e5475936404de2`. Times 226 s / 233 s / 288 s; live client sent = received for every trade and quote.
- Features for all 25 stock-days: _summary to add when the run finishes._

## Known risks
- Thresholds are guesses until Phase 5 walk-forward tests.
- Alpaca's trade timestamp type and TRF mapping are UNVERIFIED (docs/research/11).
- Live bars close on the next event, not a timer; outages aren't yet marked in live bars.
