# Runbook: paper drills (Phase 7)

Goal: prove on the real IBKR **paper** gateway that orders, stops and the watchdog behave the
way the simulator tests say. Each order drill buys **1 share of SPY** on the paper account,
through RiskGuard, like any other trade. Paper only: the drills refuse anything else.

## Before you start
- Market open, at least 5 minutes after 09:30 ET and not in the last 15 minutes.
- The gateway is up and healthy (`docker compose up -d ib-gateway`).
- **Stop the node and the watchdog** (the drills use the same IBKR client id):
  `docker compose stop node watchdog`
- Day trades: each order drill is a day trade. Under the current `legacy_pdt` setting the
  account gets 3 in 5 business days, and the trading node needs them too. Spread the drills
  over several days, or run them before paper trading starts. RiskGuard refuses a drill
  that would go over; the report then says "refused by risk". With `pdt_regime: cash` there
  is no count; each drill uses about $700 of settled cash.

## The drills, in order (PowerShell, in the repo folder)
```powershell
docker compose --profile drill run --rm paper-drill instruments   # no orders: checks the 5 symbols load from IBKR
docker compose --profile drill run --rm paper-drill round-trip    # buy 1, stop at IBKR, hold 20 s, sell
docker compose --profile drill run --rm paper-drill stop          # buy 1, stop 2 cents under, wait for it (up to 10 min)
docker compose --profile drill run --rm paper-drill takeover      # buy 1, crash the node, the watchdog cancels and sells
docker compose --profile drill run --rm paper-drill restart       # buy 1, crash, restart: the node must halt, the watchdog sells
```
Each one prints PASS or FAIL with a table and writes `reports/paper/drill-<name>-<time>.md`
(and `.json`). The dashboard's Health page lists them.

**After `takeover` and `restart` the kill switch is ON** (that is the point of those
drills). Read the report, check IBKR shows no SPY shares and no open orders, then:
```powershell
docker compose run --rm riskctl release
```

## What the numbers mean
- *entry submit → filled*: how long IBKR took to fill the buy. The backtests assume 150 ms.
- *entry fill → stop accepted*: the gap when the shares have no stop yet. Must be under 5 s.
- *bid at stop → stop filled*: how late the stop sold after the price touched it.
- *crash → watchdog flattened*: the watchdog waits 15 s of silence, then connects and sells.

## Restart soak (after the drills pass)
Starts and stops the paper node 8 times over about an hour, half normal stops, half hard
kills, only while it holds nothing, and checks each restart comes back clean:
```powershell
docker compose stop node
docker compose --profile drill run --rm paper-soak --cycles 8 -- --allow-iex   # drop --allow-iex on SIP
```
Report: `reports/paper/soak-<time>.md`. Start the node again afterwards (paper-node.md).
