# The dashboard

The dashboard is a web page on your own computer that shows what FlowForge is doing. It
can look at everything and change nothing, with one exception: the **Kill switch** button,
which can only turn the kill switch ON. Turning it off, clearing a halt and changing a risk
limit are never on the dashboard (see kill-switch.md and config/risk_limits.yaml).

Everything here runs in PowerShell, inside the FlowForge folder (the one with
`docker-compose.yml`). Docker Desktop must be running.

## 1. Try it with made-up data first
```powershell
docker compose --profile demo up --build dashboard-demo
```
Open http://localhost:8080 in your browser. A yellow line at the top says **Demo data**:
every number is invented, so you can click around safely. Press `Ctrl+C` in PowerShell to
stop it.

## 2. Run it for real (paper)
```powershell
docker compose --profile paper up -d --build dashboard
```
Open http://localhost:8080. It only works on this computer (nobody on your Wi-Fi can open
it). To stop it: `docker compose stop dashboard`.

Until the trading program runs (Phase 7), most screens say so plainly, for example "The
trading node is not running, so nothing can trade." That is correct, not broken.

## 3. The screens (the icons on the left; on a phone, along the bottom)
| Screen | What it answers |
|---|---|
| Live | Is FlowForge safe right now, and is it up or down today? Equity, open positions, today's trades, and the **risk budget**: how much of each limit is used. |
| Tape | Who is pushing each stock: buying minus selling every minute, big prints and sweeps. |
| Signals | Every trade idea today and what happened to it (traded, refused by risk, skipped), plus the Phase 5 verdict for each setup. |
| Approvals | Empty in paper. Confirmation mode comes in Phase 8. |
| Journal | Every record the bot wrote, searchable. Pick a day, an event or type a word and press Enter. |
| Proposals | Changes the learning process suggests (after Phase 7). Each needs your approval. |
| Health | Is everything answering: trading program, watchdog, IB Gateway, data, disk, phone alerts. |
| Settings | Your risk limits and other settings, read only. The kill switch button is here too. |

## 4. The kill switch button
1. Click **Kill switch** (top right of Live) or **Turn kill switch on** (Settings).
2. Type why (or keep "stopped from the dashboard") and click **Turn kill switch on**.
3. Live now says "Kill switch is on". The bot stops new trades and sells what it holds; the
   watchdog backs it up.
4. To turn it off later, use the command line only:
   `docker compose run --rm riskctl release`

If the button ever fails, the screen says so. Use the command line instead:
`docker compose run --rm riskctl kill --reason "why"`
