# Runbook: practice backtest on past prices

What it does: downloads 2 years of 1-minute prices, then replays them to see what a simple
strategy would have done. **No orders go anywhere**, not even paper. No IBKR login needed.

## 1. One-time: get a free Alpaca data key
1. Go to https://alpaca.markets and sign up (free). You don't need to add money.
2. Log in. On the home page, find **API Keys** and click **Generate New Keys**.
3. You'll see a **Key** and a **Secret**. Keep that page open for step 3. The secret is only shown once.
4. Never paste these keys into a chat, email or GitHub.

## 2. Get the new code
- With git: `git pull`.
- Without git (Windows): download the ZIP from GitHub, unzip it, and copy your old `.env` file into the new folder.

## 3. Put the key in `.env`
In PowerShell, inside the folder: `notepad .env`. Add these two lines at the bottom, with your values:
```
ALPACA_API_KEY_ID=your-key
ALPACA_API_SECRET_KEY=your-secret
```
Save and close Notepad.

## 4. Download the prices (once, a few minutes)
Start Docker Desktop and wait until it says it is running. Then:
```
docker compose --profile backtest run --rm bars-download
```
You should see one line per symbol, like `SPY: 196000 bars -> data/bars/SPY_1min_sip.csv`.
If it says `subscription does not permit`, use the IEX feed instead:
```
docker compose --profile backtest run --rm bars-download python -m flowforge.data.alpaca_bars --out data/bars --feed iex
```

## 5. Run the backtest (about a minute)
```
docker compose --profile backtest run --rm backtest
```
It prints a short summary in plain words and the path of the full report.

## 6. Look at the results
Open the folder `reports\backtest-<date-time>\` and double-click `report.html`. Also there:
`trades.csv` (every trade), `journal.jsonl` (every signal, risk decision, order and fill) and `summary.json`.

Options (add after `backtest`, e.g. `... run --rm backtest python -m flowforge.backtest.run --data data/bars --equity 30000`):
`--equity` starting money (default 10000, like the paper account), `--symbols`, `--stress-bps`.
