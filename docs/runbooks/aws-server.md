# Runbook: run FlowForge on an AWS cloud computer (paper only)

Goal: FlowForge keeps running when your PC is asleep. We rent one small computer from
Amazon (AWS Lightsail). It runs the same Docker setup as your PC. **Paper only**: nothing
in this runbook touches real money, and the node still refuses anything but IBKR paper.

Why Lightsail and not Vercel or Supabase: Vercel stops a program after 5 to 30 minutes and
Supabase only stores data. The bot and the IBKR login program must stay on all day.
Sources and prices: docs/research/18-hosting.md.

**Cost:** about **$44 a month** (8 GB memory, 2 processors, 160 GB disk, 5 TB data). You pay
for every hour it exists, even when stopped. Deleting it stops the bill.

What still runs on your PC: the big jobs (downloading months of ticks, features,
walk-forward). The server only needs the gateway, the node, the watchdog, the dashboard,
the phone alerts and the daily reports.

## Two rules that do not change
1. **Monday:** approve the IBKR login on your phone (IBKR Mobile) when it asks.
2. **One IBKR login at a time.** While the market is open, stay logged out of the IBKR app
   and website on every device. If you log in, the server's gateway gets kicked off.
   Also: **only one gateway may run.** Stop the one on your PC before starting the server's
   (Part 4, step 1).

## Part 1: rent the computer (about 10 minutes, this is where money starts)
1. Go to https://lightsail.aws.amazon.com and sign in to AWS.
2. Safety first, set a spending alarm: in the AWS search bar type **Budgets**, then
   **Create budget** → **Use a template** → **Monthly cost budget**, amount **60**, your
   email. AWS emails you if the bill heads past $60.
3. Back in Lightsail, click **Create instance**.
4. **Location:** Virginia (us-east-1), any zone.
5. **Platform:** Linux/Unix. **Blueprint:** "OS Only" → **Ubuntu 24.04 LTS**.
6. Click **+ Add launch script**. Open `scripts/server/launch-script.sh` from the repo on
   GitHub, copy all of it and paste it into the box. It installs Docker, sets New York
   time, adds swap and downloads the code.
7. **Plan:** the **$44** one (8 GB memory, 2 vCPUs, 160 GB SSD). Pick "General purpose" with IPv4.
8. **Name:** `flowforge`. Click **Create instance**.
9. Wait 5 minutes for it to finish starting.

## Part 2: lock the door
1. Click the `flowforge` instance → **Networking** tab.
2. Delete the **HTTP (80)** rule if there is one. The dashboard is never opened to the internet.
3. Edit the **SSH (22)** rule: choose **Restrict to IP address**, type your home IP (Google
   "what is my ip" on your PC), and tick **Allow Lightsail browser SSH/RDP**. Save.

## Part 3: put your settings on it
1. Click the orange **>_ Connect using SSH** button. A black window opens in your browser.
   That window is the server. You type commands there.
2. Check the launch script finished:
   ```bash
   cat ~/LAUNCH-DONE.txt
   ```
   It should say `flowforge launch script done`. If not, wait 2 minutes and try again.
3. Close the black window and open it again (this lets you use Docker without `sudo`).
4. Make the settings file. On your PC, open `.env` in the FlowForge folder with Notepad and
   copy everything. In the black window:
   ```bash
   cd ~/orderflowbot
   nano .env
   ```
   Paste (right-click → Paste, or Ctrl+Shift+V), then press **Ctrl+O**, **Enter**, **Ctrl+X**.
   Then lock it so only you can read it:
   ```bash
   chmod 600 .env
   grep -c . .env
   ```
   The `.env` holds your passwords. It stays on the server and your PC only, never on GitHub.
5. (When the IEX walk-forward is done) copy `reports/phase5-iex/walkforward.json` from the PC.
   Without it the node runs in **shadow** (watches, journals signals, places no entries),
   which is fine to start with. Claude on your PC can copy it over for you.
6. Build the bot (takes 5 to 10 minutes):
   ```bash
   docker compose --profile paper --profile data --profile ops build
   ```

## Part 4: switch over from the PC
1. **On your PC** (PowerShell, in the FlowForge folder): stop the PC's copy so only one
   gateway logs in.
   ```powershell
   docker compose --profile paper stop
   docker compose stop ib-gateway
   ```
2. **On the server** (black window), start everything once by hand and look at the result:
   ```bash
   ~/orderflowbot/scripts/server/flowforge-day.sh morning
   tail -40 ~/orderflowbot/data/server-logs/$(date +%F).log
   ```
   Approve the IBKR login on your phone if it asks. The log should end with the node
   starting, or with `node NOT started` and the reason (kill switch on, preflight refused).
3. Turn on the daily routine (Monday to Friday: start at 8:30 and again at 9:20 New York
   time, stop at 4:10 pm):
   ```bash
   crontab ~/orderflowbot/scripts/server/crontab.txt
   crontab -l
   ```

The routine never starts the node when the kill switch is on or a halt is set. A person
looks first and runs `docker compose --profile ops run --rm riskctl release`. Same rule as
on the PC.

## Part 5: see the dashboard on your phone (free, private)
The dashboard only listens inside the server. Tailscale makes a private tunnel that only
your own devices can use.
1. Make a free account at https://tailscale.com and install the Tailscale app on your phone
   (and PC if you like). Sign in with the same account.
2. In the black window:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
   Open the link it prints and sign in. Then:
   ```bash
   sudo tailscale serve --bg 8080
   ```
   It prints an address like `https://flowforge.your-name.ts.net`. Open it on your phone
   with Tailscale switched on. (If it asks to turn on HTTPS certificates, say yes.)

Never use `tailscale funnel` for the dashboard: that makes it public.

## Every day
Nothing. The routine runs by itself. Phone alerts and the 9:00 pre-market check still come.
Mondays: approve the IBKR login on your phone.

## Updating the code later
```bash
cd ~/orderflowbot && git pull && docker compose --profile paper --profile data --profile ops build
```
Do this after the market closes. The new code runs from the next morning's start.

## Stopping the bill
Lightsail → `flowforge` → **Delete**. Copy anything you want to keep from `data/` and
`reports/` first.

## If something goes wrong
| You see | What it means | What to do |
|---|---|---|
| Log: `kill switch or halt is set: node NOT started` | someone or something turned the kill switch on | read the alert, check IBKR paper, then `riskctl release` |
| Log: `preflight refused` | the settings point at something other than paper | check `.env` (paper port, `DU` account) |
| Node won't start on Monday | the gateway is waiting for your phone approval | approve it; the 9:20 run starts the node |
| Gateway keeps restarting, IBKR says "existing session" | you are logged into IBKR somewhere else | log out of the IBKR app and website |
| Black window: `permission denied ... docker.sock` | the Docker group is not active yet | close the window and open it again |
