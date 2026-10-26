# Runbook: the paper trading node (Phase 7)

Goal: run FlowForge on your IBKR **paper** account every trading day for at least 4 weeks,
then read the scorecard. No real money is involved anywhere in this runbook: the node
refuses to start unless the settings say paper (port, account starting with `DU`,
`IB_TRADING_MODE=paper`).

What it does: watches the tape (Alpaca), computes the order-flow features, finds signals,
asks RiskGuard, and places orders on IBKR paper. **Only setups that passed the Phase 5
walk-forward can trade.** If none passed (or the walk-forward hasn't run yet) it runs in
**shadow**: it does everything except place entries, and journals each signal as "not
enabled". That is still useful: it tests the plumbing for weeks before any trade.

## Before the first day
1. The read-only paper check passed (docs/runbooks/ib-paper-check.md).
2. The drills passed (docs/runbooks/paper-drills.md). At least `instruments` and `round-trip`.
3. `.env` has your Alpaca keys and, if you want phone alerts, ntfy or Telegram
   (docs/runbooks/alerts.md). Add these lines if they are missing:
   ```
   ALPACA_FEED=sip          # or iex with the free plan (see below)
   PAPER_NODE_ARGS=         # e.g. --allow-iex, or --setups H4
   ```
4. **Feed.** The free Alpaca plan streams only IEX trades (a few % of all trades). The
   walk-forward used the full tape (SIP). On IEX the node starts only with
   `PAPER_NODE_ARGS=--allow-iex`, and its results are not comparable. Decide this before
   counting paper weeks.
5. **Day-trade rule.** risk_limits.yaml says `pdt_regime: cash` (you confirmed a *Cash*
   account on 2026-10-26). If the account type ever changes, `legacy_pdt` means only 3 day
   trades in 5 business days under $25,000. Only you can change that file. With `cash` there is no
   day-trade count, but each day the bot only buys with *settled* cash (money from a sale
   arrives the next trading day). With $10,000 that is about $10,000 of buys a day. The
   pre-market check and the dashboard show how much sale money is still settling.

## Every trading day (PowerShell, in the repo folder)
1. Start Docker Desktop and wait for "running".
2. Monday: approve the IBKR login in the IBKR Mobile app when the gateway asks.
3. Start everything:
   ```powershell
   docker compose up -d ib-gateway
   docker compose --profile paper up -d watchdog daily alerts dashboard
   docker compose --profile paper run --rm node --preflight   # prints the plan, connects to nothing
   docker compose --profile paper up -d node
   ```
   The preflight prints `"shadow": true` when no setup may trade, and a `notes` list with
   anything worth knowing (IEX feed, a symbol with no downloaded prior day, and so on).
4. Open the dashboard: http://localhost:8080. Live shows the risk budget; Health shows the
   node, the watchdog and the **Paper gate** panel.
5. At 09:00 ET you get the pre-market check, after the close the end-of-day report, and the
   scorecard is refreshed.

## Stopping
- Normal stop: `docker compose stop node`. Stops stay at IBKR; the node flattens 5 minutes
  before the close anyway.
- Emergency: the dashboard's kill switch button, or `docker compose run --rm riskctl kill`.
- The node does **not** restart by itself after a crash. The watchdog flattens if it was
  holding shares. Read the alert, then start it again with step 3.

## If something goes wrong
| You see | What it means | What to do |
|---|---|---|
| `refusing to start: ... not a paper port` | the settings point at live | fix `.env`: paper port 4004 in Docker, `DU` account |
| `refusing to start: feed 'iex' ...` | free Alpaca plan | add `--allow-iex` to `PAPER_NODE_ARGS`, knowing the results don't compare |
| `H2 did not pass the walk-forward` | you asked for a setup that may not trade | remove it from `--setups` |
| Alert "position mismatch ... kill switch on" | IBKR shows shares the node doesn't know (or the other way) | the watchdog takes over; check IBKR, then `riskctl release` |
| Alert "instrument not loaded from the broker" | IBKR didn't send that symbol | run the `instruments` drill and send me the report |

## The scorecard
`docker compose --profile check run --rm scorecard` writes reports/paper/scorecard.md. The
gate: 4 weeks, 100 trades, positive average after costs, daily and weekly loss inside the
limits, zero risk violations, zero unreconciled orders. Meeting it does not start live
trading: that needs your written go, a Nautilus 2.0.0 final release and the IB adapter port.
