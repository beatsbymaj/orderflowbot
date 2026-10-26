# 14. Phase 5 signal hypotheses (pre-registered)

Written 2026-10-26, **before** any signal was run on real data. This file is the test plan.
Changing a rule, the grid, the data window or the pass bar after results are seen makes the
test invalid; any such change gets a new dated section and the whole test is re-run on data
that wasn't used to make the change. Every setup below is a hypothesis (CLAUDE.md: the book's
examples are futures/forex and untested on US equities).

## Data
- Symbols: SPY, QQQ, AAPL, MSFT, NVDA (Phase 2 universe).
- Days: the 60 NYSE trading days 2026-07-31 … 2026-10-23, plus 2026-07-30 as the warm-up day
  (prior-day stats for 07-31). Regular hours only. Alpaca SIP trades + NBBO (free plan
  history), Phase 2 download with gap checks; Phase 3 features on the final settings
  (FeatureConfig defaults as merged, commit 324f8e9). Stock-days with unfilled gaps keep their
  gap flags and bars marked `gap` produce no signals.
- Nothing from after 2026-10-23 is used. If the download can't get every day, the test runs on
  what it got and the report says which days are missing.

## Common rules (all hypotheses)
- **Long only.** Signals are checked when a 5-minute footprint bar closes (or, for H5, when a
  sweep is confirmed). Entry = that moment. No signals before 09:35 or after 15:45 ET
  (RiskGuard's first-5 / last-15-minute rule) and none on `gap` bars.
- **One trade per symbol at a time**; a new signal while holding is ignored.
- **"ATR"** = the prior session's high − low (the only multi-day range the engine carries).
- **Stop distance band (book §6):** the stop must be 10–20% of ATR below the entry. Too close →
  widened to 10%. Too far → no trade.
- **Unfinished-business veto (book §6):** no trade if an active unfinished low lies between
  (stop − 1 risk) and the entry.
- **Volume target (book §6):** the first heavy-volume area above the entry that gives at least
  1.5R: session POC, VAH, multiple-node or absorption levels (low edge above entry). Target =
  one row below that edge. None that gives 1.5R → no trade.
- **Exits:** stop-market at the stop (gaps fill through), target sold at the bid, everything
  flat at 15:55 ET.

## Hypotheses
| Id | Name | Source | Entry condition on the closing 5-min bar |
|---|---|---|---|
| H1 | Stacked-imbalance pullback | book §3 #4 | A 30-min stacked-imbalance level (either side) that price left for ≥ 1 whole 30-min bar, never touched since (`touches == 0`), first touched from above: previous 5-min low > level high, this bar's low ≤ level high, close ≥ level low |
| H2 | Large-print (trades-filter) pullback | book §3 #3 | Same pullback rule on a large-print level (side ignored, as the book says) |
| H3 | Multiple-node pullback | book §3 #2 | Same pullback rule on a 30-min multiple-node level |
| H4 | Zone + order-flow confirmation | book §5 | Price is in a support zone (this bar's low ≤ zone high and close ≥ zone low; zones: session VAL, prior-day low, VWAP − 1σ, opening-range low once complete, and any stacked-imbalance / multiple-node / large-print / absorption level below the prior close) and the bar shows a confirmation at or inside the zone: big bid cell, absorption row, seller exhaustion (`exhaustion == 1`) or bullish CVD divergence (`cvd_divergence == 1`) |
| H5 | Follow buy-side aggression | house playbook | A confirmed buy-side sweep while price is above VWAP and the current 5-min bar's delta is positive. Zone = sweep low … sweep high |

Stops: H1–H4 "zone" stop = zone low − 1 footprint row; H5 = sweep low − 1 row.

**Control (not a hypothesis, not counted):** random entries, same count per symbol-day as all
hypotheses combined, at random 5-minute bar closes 09:35–15:45 (seed 20261026), with the fixed
stop and 2R target below. It shows what "no edge" looks like under the same costs.

## Grid (the only things fitted)
Each hypothesis has 4 configurations: stop ∈ {zone (book), fixed 15% of ATR} × target ∈
{volume target (book), fixed 2R}. With 5 hypotheses that is **20 trials**, all counted in the
multiple-testing adjustment. No other parameter is tuned in Phase 5.

## Walk-forward
- Days in order. Train on 20 days, test the next 10, move forward 10: four folds, test days
  21–30, 31–40, 41–50, 51–60 (40 out-of-sample days).
- In each train window, per hypothesis, pick the configuration with the highest mean net R per
  trade among those with ≥ 10 train trades (tie or none → the book default: zone stop + volume
  target). Apply it unchanged to the next 10 days. Out-of-sample results are the four test
  windows joined.

## Costs and fills (fast simulator)
- Signal → order 150 ms later (Phase 4 latency model). Entry: immediate-or-cancel limit at
  the ask seen at the signal + 10 bps (`max_slippage_bps`); fills at the ask at arrival if it
  is at or below the limit, otherwise no trade. Refused like RiskGuard would: spread > 10 bps,
  quote older than 2 s, locked or crossed NBBO.
- Stop: when the bid reaches the stop, a market sell 150 ms later fills at the bid then.
  Target: when the bid reaches the target, the same. Flat at 15:55 at the bid 150 ms later.
- **Primary result ("edge"):** net R per trade after spread, slippage and per-share costs
  (IBKR Fixed $0.005/share, SEC fee on sales), sized at $50 risk per trade (0.5% of $10,000)
  with no notional cap.
- **Account check (required before paper promotion, not the test statistic):** the same OOS
  trades replayed with every limit in config/risk_limits.yaml on a $10,000 cash account
  (10% notional cap, $1 minimum commission, 3 positions, 10 trades/day, legacy PDT 3 day
  trades per 5 days). Reported as dollars, drawdown and losing streak.
- A Nautilus backtest of the chosen configurations through GuardedStrategy on a sample of OOS
  stock-days checks the fast simulator (trade-by-trade agreement reported).

## Pass bar (Phase 5 exit gate)
A hypothesis passes only if all hold on the out-of-sample trades:
1. ≥ 30 trades.
2. Mean net R > 0 with a one-sided p-value from a day-block bootstrap (10,000 resamples of
   test days, seed 20261026) that is < 0.05 **after Holm adjustment across H1–H5**.
3. Deflated Sharpe ratio (Bailey & López de Prado 2014) ≥ 0.95, with N = 20 trials and the
   variance of per-trade Sharpe across the 20 configurations run on all 60 days.
4. Beats the random control's mean net R.
Results are reported for every hypothesis, pass or fail: trades, win rate, mean and total R,
max drawdown in R, longest losing streak, per fold, per symbol. No window is dropped.

## Jev gate (ADR-0004, tested only on what passes or on H4 if nothing passes)
- `jev-1.13.0`, Noul questions on a state of labelled facts computed in code (no raw numbers
  Jev would have to compare). 750 ms total deadline, no retries; timeout or any error = NO
  TRADE (counted as a veto in the results, not dropped).
- Threshold τ ∈ {0.5, 0.6, 0.7} picked in each train window (3 more trials, counted).
- The gate is kept only if, out of sample, gated mean net R beats the ungated baseline with a
  paired day-block bootstrap p < 0.05, and Jev's Brier score beats the base-rate forecast.
  Calibration (Brier, 10-bin reliability) is tracked per question. Without a TypeSafe key the
  gate stays off and this part is reported as not run.

## Sources (checked 2026-10-26)
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian
  Journal of Statistics* 6(2), 65–70.
- Bailey, D. H. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio
  Management* 40(5), 94–107. https://ssrn.com/abstract=2460551 ; formulas as summarised at
  https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio : PSR = Φ((SR − SR*)·√(T−1) /
  √(1 − γ₃·SR + (γ₄−1)/4·SR²)); SR₀ = √V[SR]·((1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e))).
- Trader Dale, *Order Flow Trading Setups* (project reference summary §3, §5, §6).
