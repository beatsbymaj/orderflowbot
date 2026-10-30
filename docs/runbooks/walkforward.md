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
them all).

**Windows: much faster from a Docker volume.** Reading `data\ticks` through Docker's Windows
file sharing (worse under OneDrive) took ~50 minutes per stock-day; from a Docker volume it
took ~1–1.5 minutes (measured 2026-10-28). Copy once, then mount it read-only:
```powershell
docker volume create ff-ticks
docker run --rm -v "${PWD}\data\ticks:/src:ro" -v ff-ticks:/dst alpine sh -c "cp -a /src/. /dst/"
docker compose --profile features run --rm -v ff-ticks:/app/data/ticks:ro features python -m flowforge.features.replay --ticks data/ticks --out data/features --jobs 3
```
Use the same `-v ff-ticks:/app/data/ticks:ro` for the walk-forward. `--jobs 3` used ~6–7 GB. This takes a few
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

## Phase 5c: the 9/20 EMA crossover (docs/research/18)
This tests two versions of the 9 EMA / 20 EMA crossover, long only: **C0** the plain cross, and
**C1** the cross confirmed by trend, order flow, market structure, key levels and volume. It uses
the Phase 5b spring days (2026-04-01 … 07-29) that are already on the PC, so **there is nothing new
to download**. Wait until the Phase 5b features are finished (`data\features-b\summary.md` and,
for IEX, `data\features-b-iex\summary.md` exist).

1. Get the new code (the PR's branch or main after it merges) into the FlowForge folder, keeping
   your `data`, `reports` and `.env`. Then rebuild the test image once:
   ```powershell
   docker compose --profile research build walkforward
   ```
2. Full tape (SIP):
   ```powershell
   docker compose --profile research run --rm walkforward python -m flowforge.research.walkforward --study 5c
   ```
3. Free feed (IEX), the one that decides paper:
   ```powershell
   docker compose --profile research run --rm walkforward python -m flowforge.research.walkforward --study 5c --features data/features-b-iex --ticks data/ticks-b-iex --out reports/phase5c-iex
   ```
4. Second look on the Aug–Oct days (reported, not part of the pass):
   ```powershell
   docker compose --profile research run --rm walkforward python -m flowforge.research.walkforward --study 5c-fall
   docker compose --profile research run --rm walkforward python -m flowforge.research.walkforward --study 5c-fall --features data/features-iex --ticks data/ticks-iex --out reports/phase5c-fall-iex
   ```
Each writes `walkforward.md`, `walkforward.json` and `trades.csv` in its `reports\phase5c…`
folder. If reading from `data\` is slow, mount the ticks from a Docker volume as in step 2 above
(`-v <volume>:/app/data/ticks-b:ro`, matching the folder the command reads).
