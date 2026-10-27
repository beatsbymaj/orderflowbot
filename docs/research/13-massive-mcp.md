# 13 — Massive remote MCP server for Claude Code

Accessed 2026-10-27. Adds to doc 03 (Massive market data, 2026-10-19), row 25.

## What it is
- Hosted MCP server at `https://mcp.massive.com/`, HTTP transport [M1][M2].
- Sign-in is **OAuth to your Massive account**. No API key is copied, stored or sent in a header [M1][M2].
- It can search Massive's REST endpoints, read their parameter docs, and run any REST call [M2].
- What it returns "mirrors your Massive entitlements". Data outside your plan (example given: Options without an Options plan) returns an access-denied error [M1][M2].
- Session limits: 64 MB working storage per session, up to 10 concurrent workspaces [M2]. No rate limit is stated on the page; the REST plan's own limits presumably apply (UNVERIFIED).
- A self-hosted version exists (github.com/massive-com/mcp_massive) for large downloads [M2]. Doc 03 (2026-10-19) recorded it as "experimental and could be subject to breaking changes" [S24 in doc 03]. The 2026-10-27 quickstart page no longer says so; treat it as experimental anyway.

## Cost
- A free **Basic** account is enough to connect [M1].
- Basic is **end-of-day data only** for stocks (doc 03 row 5, 2026-10-19). Delayed intraday starts at Starter $29/mo; real-time trades + quotes need Stocks Advanced $199/mo (doc 03). Prices not re-checked today.
- Nothing is bought by adding the config. Upgrading is the operator's call.

## How FlowForge uses it
- **Development aid only.** Claude Code sessions use it to look up endpoints, check field names and pull sample data while writing code.
- It is **not** a bot data feed. The bot's data stays Alpaca + IBKR (ADR-0007). Swapping feeds needs a new ADR and the operator's decision.
- No code in `src/` calls it. Risk code never reads it.
- The config file `.mcp.json` holds only the public server URL. The OAuth token is kept by Claude Code on the operator's machine, never in git.

## Tested 2026-10-27 (free Basic plan)
- After the operator signed in, the tools `search_endpoints`, `call_api`, `query_data` and `workspace` loaded in claude.ai cloud Claude Code sessions as well as on the PC.
- `call_api /v2/aggs/ticker/SPY/prev` returned the 2026-10-26 daily bar: o 762.25, h 767.73, l 761.63, c 765.37, v 31,737,797, n 490,499.
- On the PC the `claude` command isn't on PATH when Claude Code comes from the desktop app. `claude mcp add` was run with the app's bundled `claude.exe`, at user scope. A chat that was already open had to be restarted before `/mcp` listed the server.

## UNVERIFIED
- REST rate limits that apply through the MCP server on the Basic plan.

## Sources
- M1 — Massive, "Claude Code" client guide: https://massive.com/docs/ai-tools/clients/claude-code (accessed 2026-10-27)
- M2 — Massive, AI tools quickstart: https://massive.com/docs/ai-tools/quickstart (accessed 2026-10-27)
