# Runbook: turn the downloaded ticks into order-flow features (Phase 3)

What it does: reads the trades and quotes you downloaded in Phase 2 and works out, for each
stock and each 1-, 5- and 30-minute bar: who was the aggressive buyer or seller, delta and
cumulative delta, the Bid × Ask footprint, imbalances, absorption, big prints, sweeps,
off-exchange share, tape speed, spread and quote behaviour, VPIN and the day's key levels.
**Offline only.** No internet, no Alpaca keys, no IBKR, no orders.

You need: the Phase 2 folder with `data\ticks` in it, and Docker Desktop.

## 1. Get the new code
1. Download the ZIP of this branch from GitHub and unzip it.
2. Copy the whole `data` folder from your Phase 2 folder
   (`C:\Users\mario\OneDrive\Desktop\orderflowbot-phase2\data`) into the new folder.
   (Or unzip the new code over the Phase 2 folder; the `data` folder is not in the ZIP, so it stays.)
3. Start Docker Desktop and wait until it says it is running.
4. Open PowerShell in the new folder.

## 2. The gate test: do live and replay agree? (about 10 minutes for one SPY day)
```
docker compose --profile features run --rm features-parity
```
It runs SPY 2026-10-23 three ways: straight replay, a Nautilus backtest, and through the
real live Alpaca client (fed from the files instead of the internet). Good result:
```
SPY 2026-10-23 (whole session, 3,795,704 ticks): IDENTICAL
```
`DIFFERENT` means the gate failed: send me `reports\parity-SPY-2026-10-23.json`.

Try a quicker one first if you like (first 30 minutes of the day):
```
docker compose --profile features run --rm features-parity python -m flowforge.features.parity --ticks data/ticks --symbol SPY --day 2026-10-23 --minutes 30
```

## 3. Make features for all 25 stock-days (about 20–40 minutes)
```
docker compose --profile features run --rm features
```
Each stock-day prints one line, e.g. `SPY 2026-10-23: 488,353 trades, 41 large prints, 312 sweeps`.

## 4. What to send me
- `data\features\summary.md`: one line per stock and day. **Send me this file**, plus the
  parity report from step 2.

## 5. Where things are
- `data\features\<SYMBOL>\<day>.jsonl.gz`: every bar and event, one per line.
- `reports\parity-SPY-2026-10-23.json`: the gate result.

## If something goes wrong
- `No such file` / `0 symbol-days`: the `data\ticks` folder isn't in this folder. Redo step 1.2.
- Docker says it ran out of memory: in Docker Desktop, Settings > Resources, give it 8 GB, or
  run fewer stocks: add `--symbols SPY` to the step 3 command after `features`, like
  `docker compose --profile features run --rm features python -m flowforge.features.replay --ticks data/ticks --out data/features --symbols SPY`.
