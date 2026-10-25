# Runbook: watch the live Alpaca stream (no trading)

What it does: connects to Alpaca's live data stream through the bot's Nautilus engine for
a set time, counts what arrives, and checks how late it is. **No orders, no broker.**

## 1. Practice stream (works any time, even weekends; fake data)
Start Docker Desktop. In PowerShell, inside the folder:
```
docker compose --profile data run --rm live-watch
```
After a minute you should see something like:
```
Watched the Alpaca test stream for 60 seconds.
  FAKEPACA: 812 trades, 1,530 quotes, 0 status messages
  Delay from exchange to us: average 41.2 ms, worst 180.5 ms; 0 late, 0 with our clock behind.
  Disconnects: 0.
```

## 2. Real stream on free keys (market hours only, 9:30–16:00 New York time)
Free keys only get IEX, one exchange with a small share of the trading. That's enough to
check the plumbing, but not for order-flow signals.
```
docker compose --profile data run --rm live-watch python -m flowforge.data.alpaca_live --feed iex --symbols SPY,AAPL --seconds 300
```

## What the end lines mean
- `STOPPED BY ALPACA: ... connection limit exceeded`: something else (a browser tab, another
  program) is using your Alpaca stream. Close it and try again.
- `... insufficient subscription`: that feed needs a paid plan. Use `test` or `iex`.
- `late` counts messages that arrived over 2 seconds after the exchange time.
- `with our clock behind`: your PC clock is off. In Windows: Settings > Time & language >
  Date & time > **Sync now**.

## Files
- `reports\live-<time>.json`: the counts above.
- `data\live\journal.jsonl`: every connect, disconnect, halt, price-band (LULD), correction
  and cancel, one per line.
