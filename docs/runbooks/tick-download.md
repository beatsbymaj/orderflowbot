# Runbook: download every trade and quote (Phase 2 tick data)

What it does: downloads every single trade and every best bid/ask change (the NBBO) for
a few stocks, checks the data for holes, fills the holes it can, and saves it all for
the bot to replay later. **No orders go anywhere.** No IBKR login needed.

Works with your free Alpaca keys (they're already in `.env` from the backtest).

## 1. Get the new code
- Without git (Windows): download the ZIP from GitHub, unzip it, and copy your `.env`
  file into the new folder (also copy the `data` folder if you want to keep old downloads).

## 2. Try one stock, one day first (about 5–15 minutes)
Start Docker Desktop and wait until it says it is running. In PowerShell, inside the folder:
```
docker compose --profile data run --rm ticks-download python -m flowforge.data.alpaca_ticks --out data/ticks --symbols SPY --days 1
```
You should see `Trading days: ...`, then a line like:
`SPY 2026-10-23: 1,234,567 trades, 8,765,432 quotes. Matched Alpaca's minute bars in 390 of 390 minutes. No gaps.`

## 3. The full download (5 stocks, last 5 trading days; can take a few hours)
```
docker compose --profile data run --rm ticks-download
```
- You can stop it any time with Ctrl+C. Run the same command again and it skips the days
  that are finished. (Add `--force` only if you want to download everything again.)
- The free plan allows 200 requests a minute, so this is slow on purpose.

## 4. What the ending means
- `Done: 25 symbol-days ...` and no warning: every minute matched.
- `WARNING: ... still have trade gaps`: some minutes are still missing trades even after a
  second try. That's recorded, not hidden. Send me the summary file.

## 5. Where things are
- `data\ticks\quality\summary.md`: one plain line per stock and day. **Send me this file.**
- `data\ticks\quality\SPY_<day>.json`: the full check for one day (gaps, crossed quotes, ...).
- `data\ticks\catalog\`: the saved ticks (Nautilus format).
- `data\ticks\raw\`: every trade with its exchange and condition codes.

## If something goes wrong
- `subscription does not permit`: you asked for a day that's too recent. Wait 15 minutes.
- `HTTP 401` or `403`: the keys in `.env` are wrong or missing.
- Out of disk space: each stock-day can be a few hundred MB. Free some space or use fewer `--symbols`.
