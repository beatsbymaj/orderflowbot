# Kill switch, halts and the watchdog

The kill switch is the big red button. When it is on, FlowForge makes no new trades and
sells anything it holds. Anyone can turn it on. Only you (the operator) can turn it off.

Everything here runs in PowerShell, inside the FlowForge folder (the one with
`docker-compose.yml`). Docker Desktop must be running.

## See what's going on
```powershell
docker compose run --rm riskctl status
```
You get something like:
```json
{
  "kill_switch": "off",
  "halt": "none",
  "trading_day": "2026-10-27",
  "entries_today": 0,
  "node": null
}
```
- `kill_switch`: `off`, or the reason it was turned on, who did it and when.
- `halt`: `none`, `halted for the day: ...` (daily loss, too many rejected orders, kill
  switch), or `halted until re-enabled: ...` (weekly loss).
- `node`: what the trading program last reported (`null` until Phase 7 runs it).

## Turn the kill switch ON
```powershell
docker compose run --rm riskctl kill --reason "say why"
```
What happens next, in order:
1. Right away, no new entries are allowed.
2. Within about a second, the trading program cancels its stop orders and sells what it holds.
3. If it hasn't finished after 30 seconds (or the trading program isn't running), the
   watchdog does it instead.

## Turn the kill switch OFF
```powershell
docker compose run --rm riskctl release
```
It asks you to type `yes`. Trading stays stopped for the rest of today and starts again the
next trading day.

## After a weekly loss halt
If the account drops 5% from its best point this week, FlowForge sells everything and stops
until you say so. When you've looked at what happened:
```powershell
docker compose run --rm riskctl reenable
```
It asks you to type `yes`. (The daily 2% loss halt clears by itself the next day.)

## The watchdog
The watchdog is a small separate program. It starts with the paper trading stack in Phase 7:
```powershell
docker compose --profile paper up -d watchdog
```
Every second it checks two things: is the kill switch on, and is the trading program still
alive. If the trading program goes quiet for 15 seconds while it holds shares, the watchdog
turns the kill switch on and sells the shares itself. It only works on the paper account.

## Limits it enforces
All the numbers come from `config/risk_limits.yaml`, which only you change (by a commit). The
extra timing rules FlowForge added in Phase 4 (for example "cancel an entry that hasn't filled
in 5 seconds") are listed in `docs/decisions/ADR-0013-riskguard-execution.md`, point 10.
