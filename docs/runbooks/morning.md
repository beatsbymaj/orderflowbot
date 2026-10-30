# Weekday morning start-up (Windows PC)

The "FlowForge morning" scheduled task runs `scripts/windows/flowforge-morning.ps1` at 09:20
(PC clock, Eastern time) Monday to Friday. You only need to:

- **Mondays:** approve the IBKR login on your phone when it asks (IBKR rule, about once a week).
- Keep the PC plugged in and not shut down.

What it does, in order, writing everything to `data\morning-<date>.log`:

1. Starts Docker Desktop if needed.
2. Starts the IB Gateway and waits up to 15 minutes for the login.
3. Runs `auto-release`: the kill switch goes off only if the node, the watchdog or a drill
   pulled it and IBKR shows 0 shares and 0 orders (ADR-0017). A kill you pulled stays on.
   Halts are never cleared.
4. Until `reports\paper\drills-passed.json` exists: at 09:40 ET runs
   `paper-drill all` (instruments, round-trip, stop, takeover, restart). A failure stops the
   morning there; nothing else starts that day.
5. Starts the watchdog and the paper node. `PAPER_NODE_ARGS` in `.env` sets the node's flags
   (today: `--symbols=SPY,QQQ,AAPL,MSFT,NVDA --allow-iex --walkforward reports/phase5-iex-run2/walkforward.json`,
   which is shadow: no setup has passed the walk-forward, so no entries).

Install once, in an admin PowerShell in the repo folder:

    powershell -ExecutionPolicy Bypass -File scripts\windows\install-morning-task.ps1

Remove: `Unregister-ScheduledTask -TaskName "FlowForge morning" -Confirm:$false`.
Run by hand any time: `powershell -ExecutionPolicy Bypass -File scripts\windows\flowforge-morning.ps1`.
On a market holiday the drills fail to get a quote and the morning stops; no orders are sent.
