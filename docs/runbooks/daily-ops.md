# Morning check and evening report

Two automatic reports, on trading days only:
- **Pre-market check, 09:00 New York time.** Is the kill switch off, any halt still on, do
  the risk limits load, is IB Gateway answering, are the trading program and the watchdog
  alive, how many day trades are left, is there disk space. Mondays add a reminder to
  approve the IBKR login.
- **End of day, 16:15 New York time** (13:15 on early-close days). Trades, money made or
  lost after costs, the worst dip of the day, losing streak, refused and rejected orders,
  alerts, and the most important line: **is the account flat** (no shares, no open orders).

They only read. They never place, change or cancel an order.

Each report goes to `reports\daily\` (a `.md` file you can open in Notepad), to the
dashboard's Health screen, and to your phone if alerts are set up.

## Turn it on
```powershell
docker compose --profile paper up -d daily
```

## Run one by hand
```powershell
docker compose --profile paper run --rm daily python -m flowforge.ops.daily premarket
docker compose --profile paper run --rm daily python -m flowforge.ops.daily eod
```
`eod --day 2026-10-27` writes the report for an earlier day.

## When a line says PROBLEM
| Line | What to do |
|---|---|
| Kill switch | It is on. Read why. Release it only when you are sure: `docker compose run --rm riskctl release` |
| Weekly halt | The weekly loss limit was hit. Look at the trades first; then `riskctl reenable`. |
| Trading node / Watchdog | Start them (Phase 7 runbook), then check again. |
| IB Gateway | Open Docker Desktop, check `ib-gateway` is running; log in again if it asks (VNC, see ib-paper-check.md). |
| Risk limits changed | You edited risk_limits.yaml since the bot started. Restart the bot so it uses the new file. |
| Overnight shares / Flat at the broker | Shares were left over. Look in IBKR right away; the watchdog runbook explains how to flatten. |
| Unreconciled fills | A fill the bot can't match to its own orders. Compare with IBKR's trade list. |
