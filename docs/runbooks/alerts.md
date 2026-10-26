# Phone alerts (ntfy or Telegram)

FlowForge can send alerts to your phone: problems, the kill switch, the watchdog stepping in,
rejected orders, and the morning and evening reports. You can use ntfy, Telegram, or both.
Alerts never affect trading: if your phone can't be reached, the bot carries on the same.

Everything here runs in PowerShell, inside the FlowForge folder. Docker Desktop must be running.

## Option A: ntfy (easiest)
1. Install the **ntfy** app on your phone (App Store or Google Play).
2. Make up a long topic name nobody could guess, like `flowforge-7k2q9x4m1z`. Anyone who
   knows the name can read your alerts, so treat it like a password.
3. In the app, tap **+** and subscribe to that topic.
4. Open `.env` in Notepad and set `NTFY_TOPIC=flowforge-7k2q9x4m1z` (your name). Save.

## Option B: Telegram
1. In Telegram, open a chat with **@BotFather**, send `/newbot` and follow the steps. It
   gives you a token (looks like `123456:ABC...`). Keep it secret.
2. Send any message to your new bot.
3. In your browser open `https://api.telegram.org/bot<TOKEN>/getUpdates` (put your token in).
   Find `"chat":{"id":` and copy the number after it.
4. In `.env` set `TELEGRAM_BOT_TOKEN=` (the token) and `TELEGRAM_CHAT_ID=` (the number). Save.

## Test it
```powershell
docker compose --profile paper run --rm alerts python -m flowforge.ops.alerts test
```
Your phone should show "FlowForge test". The screen prints `{"ntfy": "ok"}` (or telegram).
Anything else (like `HTTP 401`) means a value in `.env` is wrong.

## Turn it on
```powershell
docker compose --profile paper up -d alerts
```
It starts at the end of today's records, so you never get yesterday's alerts again. The
dashboard's Health screen shows "Phone alerts: OK" once a channel is set.
