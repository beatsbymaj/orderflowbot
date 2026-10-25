# Runbook: read-only IBKR paper check

Goal: prove FlowForge can log in to your IBKR **paper** account, reconcile, and read balances, positions and open orders. Nothing in this check can place an order: the node has no strategies and the script refuses anything that isn't a paper account. The paper gateway runs with `READ_ONLY_API: "no"` because the Nautilus IB execution client fails to connect in read-only mode (IB error 321, seen 2026-10-24); live stays a separate, operator-approved change.

## 1. One-time: get a paper login (IBKR website)
1. You need an IBKR Pro account (funded) for API access, paper included.
2. In IBKR's Client Portal go to **Settings → Account Settings → Paper Trading Account** and create/reset the paper account. IBKR emails you a separate **paper username**; set its password there.
3. Note the paper **account id**: it starts with `DU` (shown at the top of Client Portal when logged in with the paper username, or in TWS/Gateway once logged in).

## 2. One-time: the machine that runs it (your Mac or a Linux VPS)
Install **Docker Desktop** (Mac) or Docker Engine + Compose plugin (Linux), **git**, and a VNC viewer (Mac: built-in Screen Sharing, open `vnc://localhost:5900`; otherwise RealVNC or TigerVNC).

```bash
git clone https://github.com/beatsbymaj/orderflowbot.git
cd orderflowbot
git checkout phase-1-foundations   # until PR #1 is merged
cp .env.example .env
```

Edit `.env` (never commit it; it's git-ignored):
```
IB_TRADING_MODE=paper
IB_ACCOUNT_ID=DUxxxxxxx          # your paper account id
TWS_USERID=your_paper_username   # the PAPER username, not your live one
TWS_PASSWORD=your_paper_password
VNC_SERVER_PASSWORD=pick-anything
POSTGRES_PASSWORD=pick-anything
```

### On Windows
- Install **Docker Desktop for Windows** (it uses WSL 2; accept that during setup and restart if asked), **Git for Windows**, and a VNC viewer such as **TigerVNC** or **RealVNC Viewer**.
- Use **PowerShell**. The commands are the same except copying the env file: `Copy-Item .env.example .env`, then `notepad .env` to edit it.
- Start Docker Desktop and wait until it says it is running before any `docker` command.
- The repo's `.gitattributes` keeps files in Unix line endings so the containers read them correctly.

## 3. Start IB Gateway and log in
```bash
docker compose up -d ib-gateway
docker compose logs -f ib-gateway     # Ctrl+C to stop following
```
The container logs in with the paper credentials. If IBKR asks for a second factor, approve it in the **IBKR Mobile** app; to see the gateway window, open the VNC viewer at `localhost:5900` with `VNC_SERVER_PASSWORD`. Wait until `docker compose ps` shows `ib-gateway` as **healthy** (can take 1–2 minutes).

## 4. Run the check
```bash
docker compose --profile check run --rm ib-paper-check
```
It builds the FlowForge image (first time only, a few minutes), connects for 60 seconds, then prints a JSON report and saves it under `reports/`.

## 5. What you should see
```json
{
  "started_at": "...",
  "account": "DU*****67",
  "connected": true,
  "balances": [{"currency": "USD", "total": "...", "free": "..."}],
  "open_positions": [],
  "open_orders": [],
  "problems": [],
  "ok": true
}
```
- `ok: true` with `problems: []` means it worked. Paste the report into the thread (the account id is already masked).
- Exit code 2 and "refusing to connect" means a setting doesn't look like paper (mode, port, or an account id not starting with DU). Fix `.env`.
- `ok: false` with `no IB account state received` usually means the gateway wasn't logged in yet: check the VNC window / IBKR Mobile and run step 4 again.
- `no balances received` means `IB_ACCOUNT_ID` isn't an account of the paper login (IB logs `Invalid account code`). Paper ids are usually `DU` plus 7 digits; check for a missing digit.
- `exec-connect timeout`, with `Existing session detected` in `docker compose logs ib-gateway`: the paper username is logged in somewhere else (Client Portal, TWS, IBKR Mobile). Log out there, then `docker compose restart ib-gateway` and run step 4 again.
- Connection errors: make sure `docker compose ps` shows the gateway healthy.

## 6. Stop
```bash
docker compose down        # keeps data volumes
```

Reminder: IBKR needs a fresh login approval every Monday (docs/research/08, B6). Repeat step 3 then.
