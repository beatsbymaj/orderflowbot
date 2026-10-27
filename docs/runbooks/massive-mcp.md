# Runbook: connect Claude Code to Massive market data

Goal: let Claude Code look up Massive (formerly Polygon.io) market data while it helps build FlowForge. This does **not** change where the bot gets its prices. It costs nothing on Massive's free Basic plan. Background: docs/research/13-massive-mcp.md.

The repo already has a file called `.mcp.json` that tells Claude Code where Massive's server is. There is no password or key in it. You sign in once in your web browser.

## Step 1: Make a free Massive account
1. Go to https://massive.com and click **Sign up**.
2. Pick the free **Basic** plan. You do not need a credit card for it.

## Step 2: Install Claude Code on your Windows PC (skip if you already have it)
1. Press the **Windows key**, type `PowerShell`, and open it.
2. Follow the install steps at https://claude.ai/code (Windows section).
3. Type `claude --version` and press **Enter**. If you see a version number, it worked.

## Step 3: Open Claude Code in the FlowForge folder
1. In PowerShell, go to your FlowForge folder. Example:
   `cd $HOME\OneDrive\Desktop\orderflowbot-phase2`
   (use the folder where you unzipped the newest copy of the repo; it must contain `.mcp.json`)
2. Type `claude` and press **Enter**.
3. Claude Code asks if you trust the `massive` server from this project. Choose **Yes / approve**.

No `.mcp.json` in your folder? Type this instead, once, before step 2: `claude mcp add --transport http massive https://mcp.massive.com/`

## Step 4: Sign in to Massive
1. Inside Claude Code, type `/mcp` and press **Enter**.
2. Pick **massive**, then pick **Authenticate**.
3. Your web browser opens. Log in to Massive and click **Allow**.
4. Go back to PowerShell. `massive` should now say **connected**.

## Step 5: Try it
Ask Claude Code: "Using Massive, what was SPY's closing price yesterday?"
If you get a price, you're done.

## Good to know
- **Free plan = end-of-day data.** Asking for live or intraday trades and quotes gives an "access denied" error. That is normal. Paid plans start at $29/month; don't buy one without deciding it first.
- **Never trades.** Massive only gives data. The bot still trades only through IB Gateway with the risk limits in `config/risk_limits.yaml`.
- **Sign out / remove:** in Claude Code type `/mcp`, pick **massive**, and choose the sign-out (clear authentication) option.
