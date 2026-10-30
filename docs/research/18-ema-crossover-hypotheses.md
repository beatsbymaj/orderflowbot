# 18. Phase 5c: 9/20 EMA crossover with confirmations (pre-registered)

Written 2026-10-30, **before** any EMA, crossover signal or trade was computed on the data below.
This file is the test plan. The rules of docs/research/14 apply: changing a rule, the grid, the
window or the pass bar after results are seen makes the test invalid. Any such change gets a new
dated section here, and the test is rerun on data that wasn't used to make the change.

## Why
The operator asked on 2026-10-30 to test one more strategy: "the 9 EMA and 20 EMA crossover using
trend, order flow, market structure, key levels and volume to confirm direction and execution".
Long only (ADR-0005), paper only, under every limit in config/risk_limits.yaml. Nothing here
changes risk code or limits.

Two hypotheses are tested together, so the report shows whether the confirmations add anything:
- **C0** the plain crossover (no confirmations), the version most people trade.
- **C1** the crossover with all five confirmations the operator named.

**Known before writing, stated so it isn't hidden:**
- Phase 5 (research/14) on 2026-07-31 … 10-23: no setup passed; random entries lost −0.10 R a
  trade on SIP. That window is used here only as a **second look** (below), not for the pass.
- Phase 5b (research/17) setups on 2026-04-01 … 07-29: their ticks and features are being
  downloaded and built on the operator's PC; **no 5b result has been seen**, and no EMA has been
  computed on any of these days.
- The 2025-10 … 2026-10 practice ORB backtest (1-minute bars, no EMAs) lost 4.9%.

## Data
- Symbols: SPY, QQQ, AAPL, MSFT, NVDA (config/risk_limits.yaml universe).
- **Primary (the pass is judged here):** every NYSE trading day **2026-04-01 … 2026-07-29**,
  the Phase 5b days and files (`data/features-b`, `data/ticks-b`; IEX: `-iex` folders). No new
  download. 2026-03-31 is used only to warm the averages up (below).
- **Second look (reported, not part of the pass):** 2026-07-31 … 2026-10-23, the Phase 5 files
  (`data/features`, `data/ticks`; IEX: `-iex`), warmed up on 2026-07-30.
- Phase 3 features on the merged settings (FeatureConfig defaults). `gap` bars make no signals.
- Each feed judged on its own, as research/17: only an **IEX** pass may go to paper.

## Definitions
- **EMA(N)** of bar closes: multiplier k = 2/(N+1); EMA = close·k + previous EMA·(1−k); the first
  value is the simple average of the first N closes (IBKR TWS guide, source below). The averages
  **carry over from one day to the next** for each symbol, as on a chart: the walk-forward feeds
  each symbol's days in date order, starting with the warm-up day. A bar with no trades is
  skipped; a `gap` bar still updates the averages.
- **5-min EMAs**: EMA(9) and EMA(20) of 5-minute footprint closes.
- **Cross up**: on a 5-min close, EMA(9) > EMA(20) while on the previous 5-min close EMA(9) ≤ EMA(20).
  **Cross down**: the reverse.
- **30-min trend EMA**: EMA(20) of 30-minute footprint closes, as the engine has seen them
  (the 30-min bar whose record came before the 5-min bar being checked).
- **Swing low / high**: a 5-min bar whose low is lower (high is higher) than the lows (highs) of
  the 2 bars before it and the 2 bars after it. It is known when the 2nd bar after it closes.
  Swings carry over from day to day like the averages.
- **ATR**: the prior session's high − low (research/14). **1 row**: the bar's footprint row height.

## Hypotheses (checked when a 5-min bar closes, 09:45–15:00 ET)
Common: long only; no entry on `gap` bars; both 5-min EMAs must have their 20-bar start; the cross
up must be **today**; **one signal per hypothesis per symbol per day**, whether it fills or not.

**C0 plain crossover**: a cross up on this bar. Stop level: the lowest low of the last 5 bars
(this one included).

**C1 confirmed crossover**: a cross up on this bar **or one of the 2 bars before it**, EMA(9) still
above EMA(20), and all of these on this bar:
1. **Trend**: the last 30-min close is above the 30-min EMA(20), and that EMA is higher than one
   30-min bar earlier (needs ≥ 20 30-min closes seen).
2. **Order flow**: bar delta > 0, and session CVD now is higher than 3 bars ago (or than 0 when
   the session has fewer than 3 earlier bars).
3. **Market structure**: the last confirmed swing low is higher than the swing low before it
   (a higher low), and this bar's low is above that last swing low.
4. **Key levels**: the close is above the session VWAP, and there is room: the nearest of the
   prior-day high, prior-day close and the opening-range high (once complete) that lies above the
   close is at least 1 R away (R = close − stop, with the stop widened to 15% of ATR if closer).
   No such level above = room.
5. **Volume**: this bar's volume ≥ the average volume of the 20 previous 5-min bars.

Stop level: the last confirmed swing low.

## Stops and exits (both hypotheses)
- Stop = stop level − 1 row, kept **15–60% of ATR** below the entry: widened to 15% if closer,
  no trade if farther (same band as research/17).
- **Grid (the only thing fitted)**, exit ∈ {**cross**: sell at the bid when a 5-min close shows a
  cross down, **hold** to 15:55, **2R** target}. Stop always on; everything flat at 15:55 ET.
  2 hypotheses × 3 exits = **6 trials**. Default when train data can't choose: **cross**.
- Deflated Sharpe uses **N = 34**: these 6 + the 28 of Phases 5 and 5b.

**Control (not counted):** random entries at random 5-min closes 09:35–15:45 (seed 20261030), as
many per symbol-day as C0 + C1 signals, stop at 30% of ATR, held to 15:55.

## Walk-forward, costs, account check, pass bar
As research/17, unchanged: train 20 days, test the next 10, step 10; pick the exit with the best
mean net R among those with ≥ 10 train trades. IOC at ask + 10 bps after 150 ms; the same
refusals; IBKR Fixed fees + SEC fee; net R at $50 risk; $10,000 cash-account replay with every
limit as committed on the run day. A hypothesis passes on the primary window only if: ≥ 30
out-of-sample trades; mean net R > 0 with Holm-adjusted (C0, C1) day-block bootstrap p < 0.05
(10,000 resamples, seed 20261030); deflated Sharpe ≥ 0.95; beats the random control.

The second look must not lose: a primary IEX pass whose second-look IEX mean net R is ≤ the
control's there is reported as "did not hold up" and is **not** proposed for paper without the
operator's say.

Every number is reported, pass or fail: trades, win rate, mean and total R, max drawdown in R,
longest losing streak, per fold and per symbol. No window dropped.

## After a pass
A pass on IEX may run on IBKR paper (shadow first) after the Phase 7 drills, through a PR the
operator approves (the paper node only knows H setups today). Never live money on this study.

## Sources
- EMA multiplier 2/(N+1), first value a simple average: Interactive Brokers TWS Users' Guide,
  "Exponential Moving Average",
  https://www.interactivebrokers.co.uk/en/software/tws.bak/usersguidebook/technicalanalytics/apply_exponential_moving_average_indicator.htm
  (read 2026-10-30).
- Statistics as research/14 (Holm 1979; Bailey & López de Prado 2014).
- Confirmations follow the operator's request and the order-flow reference (delta in the trade's
  direction; trade from a level with room). The 9/20 crossover and its thresholds are house
  hypotheses, not quoted from the book.
