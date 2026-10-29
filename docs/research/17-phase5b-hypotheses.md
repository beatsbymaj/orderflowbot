# 17. Phase 5b: new setups on fresh data (pre-registered)

Written 2026-10-29, **before** any tick, feature or signal from the study window below was
downloaded or looked at. This file is the test plan. The rules of docs/research/14 apply:
changing a rule, the grid, the window or the pass bar after results are seen makes the test
invalid. Any such change gets a new dated section here, and the test is rerun on data that
wasn't used to make the change.

## Why a second study
Phase 5 (docs/research/14) tested five book setups on 2026-07-31 … 2026-10-23. None passed:
on SIP, H2 lost −0.04 R a trade, H4 −0.11 R and H5 −0.05 R, against −0.10 R for random entries,
and H1/H3 made no out-of-sample trades. The operator chose on 2026-10-29 to try new ideas on
data not yet seen ("New ideas, fresh data").

What those results suggest, and what these setups change:
- **Costs ate small trades.** Stops of 10–20% of the prior day's range meant a few cents of
  risk per share on SPY. Spread and fees were a large share of each R, and the setups did
  about as well as random entries. These setups use wider stops (15–60% of the range), take
  at most one trade per symbol a day, and can hold until the close, so costs are a smaller
  part of each trade.
- **Fading 5-minute levels did not work.** These setups follow the session's order flow
  (cumulative delta, VWAP, buy sweeps) or trade one well-known daily level (the prior day's
  low), instead of small intraday levels.

**Known before writing, stated so it isn't hidden:**
- Phase 5 results on Aug–Oct 2026, as above.
- The Phase 1 practice backtest (docs: /mnt/project-files/flowforge/backtests/2026-10-25-orb-practice.md):
  a plain opening-range breakout on 1-minute bars (no order flow), Oct 2025 – Oct 2026, lost
  4.9%. That window overlaps the one below **in bars only**. B1 is an opening-range setup with
  an order-flow filter, and that earlier loss is a reason to expect little from it.
- Phase 3 feature checks used ticks from 2026-10-19 … 10-23 only.
No tick, footprint, signal or trade from the window below has been looked at.

## Data
- Symbols: SPY, QQQ, AAPL, MSFT, NVDA (the same universe, and the one in config/risk_limits.yaml).
- Days: every NYSE trading day from **2026-04-01 to 2026-07-29**, regular hours, with
  2026-03-31 as the warm-up day (prior-day stats only, no signals). Nothing from before
  2026-04-01 or after 2026-07-29 is used.
- Two feeds, both reported, as in the research/14 IEX addendum:
  - **SIP**: `alpaca_ticks --feed sip` → `data/ticks-b`, features → `data/features-b`, results → `reports/phase5b/`.
  - **IEX**: `--feed iex` → `data/ticks-b-iex`, features → `data/features-b-iex`, results → `reports/phase5b-iex/`.
  - Only a setup that passes **on IEX** may trade on paper (the paper feed is IEX). A SIP-only
    pass is reported as "needs the full feed".
- Phase 3 features on the merged settings (FeatureConfig defaults, unchanged since Phase 5).
- Stock-days with gaps keep their gap flags, and `gap` bars make no signals. Missing days are
  listed in the report, and the test runs on what was downloaded.

## Common rules (B1–B4)
- **Long only.** Each setup is checked when a 5-minute footprint bar closes. B4 is checked
  when a buy sweep is confirmed. Entry is at that moment.
- **No entries** before 09:35 or after 15:45 ET, and none on `gap` bars. Each setup has its
  own narrower window below.
- **At most one trade per setup per symbol per day.** A setup that has signalled on a symbol
  doesn't signal again that day, whether the trade filled or not.
- **"ATR"** is the prior session's high − low, as in research/14.
- **"Session CVD"** is the `cvd` field of the bar that closed: the engine's cumulative
  delta since the session began.
- **Stop band (new):** the stop must be **15–60% of ATR** below the entry. If it is closer, it
  is widened to 15%. If it is farther, there is no trade. There is no unfinished-business veto
  and no volume target.
- **Exits:** a stop-market order at the stop (gaps fill through it). The target, when there is
  one, sells at the bid. Everything is flat at 15:55 ET.

## Hypotheses
| Id | Name | Window (ET) | Entry when the 5-min bar closes with … | Stop |
|---|---|---|---|---|
| B1 | Opening drive with buyers in control | 10:00–11:30 | close > opening-range high (30-min range, complete), bar delta > 0, session CVD > 0, close > VWAP | opening-range midpoint − 1 row |
| B2 | Failed breakdown below the prior-day low | 09:45–15:00 | the session has traded below the prior-day low; the previous 5-min close was below it and this close is ≥ prior-day low + 1 row; bar delta > 0; and this bar or one of the 2 before it shows, at or below the prior-day low, a big-bid cell or an absorption row, or has seller exhaustion (`exhaustion == 1`) or bullish CVD divergence (`cvd_divergence == 1`) | session low − 1 row |
| B3 | VWAP pullback on a buyers' day | 10:30–15:00 | at least 75% of the session's 5-min closes so far (this one included) are above their bar's VWAP; session CVD > 0; the previous bar's low was above VWAP + 1 row; this bar's low ≤ VWAP + 1 row, close > VWAP and bar delta > 0 | VWAP − 1σ (session VWAP band) − 1 row |
| B4 | Afternoon strength sweep | 13:00–15:00 | (on a confirmed buy sweep, not a bar close) the sweep's VWAP is above the session VWAP, the last closed 5-min bar's session CVD > 0, and that bar's close is within 25% of ATR of the session high | session VWAP − 1 row |

The VWAP, σ, opening range, prior-day low and session high/low are the context fields of the
last closed 5-min bar, as the engine writes them. "1 row" is that bar's footprint row height.

**Control (not a hypothesis, not counted):** random entries at random 5-minute bar closes
from 09:35 to 15:45 (seed 20261029), as many per symbol-day as B1–B4 together. Each has a stop
at 30% of ATR, held to 15:55 ET. It shows what "no edge" looks like under the same costs.

## Grid (the only thing fitted)
Each hypothesis has **2 configurations**: exit ∈ {**hold** to 15:55 (no target), **2R**
target}. The stop is fixed by the table. With 4 hypotheses that is **8 trials**.

The deflated Sharpe uses **N = 28**: these 8 plus the 20 of Phase 5, because the same
researcher has now tried both sets on this universe.

## Walk-forward
- Days in order: train on 20, test the next 10, move forward 10, as long as a full test
  window fits. About 82 study days gives 6 folds and 60 out-of-sample days.
- In each train window, each hypothesis takes the configuration with the highest mean net R
  per trade, among those with ≥ 10 train trades. If they tie, or none has 10, it takes the
  default, **hold**. That choice then runs unchanged on the next 10 days.

## Costs, fills and account check
Unchanged from research/14:
- An IOC limit at the ask + 10 bps, 150 ms after the signal.
- The same refusals: spread > 10 bps, a stale quote, or a locked or crossed NBBO.
- IBKR Fixed per-share fees and the SEC fee.
- The primary result is net R at $50 risk.

The account check replays the out-of-sample trades with every limit in config/risk_limits.yaml
as committed when the test runs: a cash account, $10,000, settled cash only, 10% notional cap.

## Pass bar
A hypothesis passes only if all of these hold, on its out-of-sample trades:
1. ≥ 30 trades.
2. Mean net R > 0, with a one-sided day-block bootstrap p-value (10,000 resamples, seed
   20261029) that is < 0.05 **after Holm adjustment across B1–B4**.
3. Deflated Sharpe ratio ≥ 0.95, with N = 28 (above). The variance of the per-trade Sharpe is
   taken across these 8 configurations run on all study days.
4. It beats the random control's mean net R.

Each feed is judged on its own (SIP, IEX). Every hypothesis is reported, pass or fail:
- trades, win rate, mean and total R,
- max drawdown in R and the longest losing streak,
- per fold and per symbol.
No window is dropped.

## After a pass
Promotion follows ROADMAP.md: a setup that passes on IEX may run on IBKR paper with every limit,
and only after the Phase 7 drills pass. Paper results are reported against this test's numbers.
A setup is not promoted toward live money on this study alone.

## Code faults
If the run shows a fault in the code (a rule implemented differently from this file), the fix
gets a dated "Correction" section here naming the commit, and both runs are reported, as in
research/14. A rule that is merely losing is not a fault.

## Sources
As research/14 (Holm 1979; Bailey & López de Prado 2014). The setups are house hypotheses
built from the project's order-flow features (opening range, VWAP bands, session CVD,
absorption, exhaustion, sweeps). They are not quoted from the reference book.
