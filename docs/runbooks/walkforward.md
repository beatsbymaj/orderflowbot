# Phase 5: test the trading ideas on past data

This tests the five trading ideas in `docs/research/14-signal-hypotheses.md` on about three
months of real past prices. It only reads data and writes reports. It never places an order
and never talks to IBKR.

Everything runs in PowerShell, inside the FlowForge folder (the one with
`docker-compose.yml`). Docker Desktop must be running. Leave the PC on and awake: some steps
take hours.

## 1. Download the past prices (the longest step)
Your free Alpaca keys must be in `.env` (`ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`).
```powershell
docker compose --profile data run --rm ticks-download python -m flowforge.data.alpaca_ticks --out data/ticks --start 2026-07-30 --end 2026-10-23
```
- About 300 stock-days, about 12 GB of disk. On the free plan this can take a day.
- If it stops (internet drop, PC restart), run the same line again. Days already done are
  skipped.
- When it finishes, `data\ticks\quality\summary.md` says which days had gaps.

## 2. Work out the order-flow features
```powershell
docker compose --profile features run --rm features python -m flowforge.features.replay --ticks data/ticks --out data/features --jobs 4
```
`--jobs 4` works on 4 stocks at once. Use `--jobs 2` if the PC gets slow. If it stops (sleep, Docker
quit), run the same line again: finished stock-days are kept and skipped (`--force` redoes
them all). This takes a few
hours. The result is `data\features\summary.md`.

## 3. Run the walk-forward test
```powershell
docker compose --profile research run --rm walkforward
```
It prints one line per idea and settings, then writes:
- `reports\phase5\walkforward.md`: the results in plain words and tables.
- `reports\phase5\walkforward.json`: the same numbers for the dashboard.
- `reports\phase5\trades.csv`: every test trade.

How to read it: an idea **passes** only if, on days it was *not* tuned on, it made money
after costs, the result is unlikely to be luck even after allowing for testing five ideas
(the "p (Holm)" column under 0.05), the deflated Sharpe is at least 0.95, and it beat random
entries. "No idea passed" is a normal, useful answer: it means don't trade these yet.

## 4. Optional: test the Jev gate
Only if you have a TypeSafe API key. Put it in `.env` as `TYPESAFE_API_KEY=...`, then:
```powershell
docker compose --profile research run --rm walkforward python -m flowforge.research.walkforward --features data/features --ticks data/ticks --out reports/phase5 --jev
```
Jev only sees labels like "price above VWAP" or "tape fast", never your account. Answers are
saved in `reports\phase5\jev_answers.jsonl`, so running it again costs nothing.

## 5. Optional: check the fast tester against the full simulator
Pick a day and an idea from the report:
```powershell
docker compose --profile research run --rm crosscheck --symbol SPY --day 2026-10-21 --setup H4 --config zone/volume
```
It writes `reports\phase5\crosscheck_SPY_2026-10-21_H4.json` with how many trades matched.
The full simulator applies every risk limit (for example at most 3 day trades in 5 days on a
small account), so it can take fewer trades than the fast tester.
