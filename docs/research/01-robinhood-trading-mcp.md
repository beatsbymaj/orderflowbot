# 01: Robinhood Trading MCP and the Agentic/MCP account

- **Author:** FlowForge Research Scout
- **Date:** 2026-10-19 (all sources accessed 2026-10-19)
- **Scope:** Official Robinhood Trading MCP (`https://agent.robinhood.com/mcp/trading`) and the Robinhood "Agentic" / "MCP" account. Research only: nothing was signed in to, no account was created, no trading tool was called.
- **Status labels:** **VERIFIED** means an official Robinhood source states it, and that source is cited. **UNVERIFIED** means it is missing from official sources, comes only from third parties (labeled as such), or is inferred.

> **Method caveat:** The research sandbox's egress proxy refused a CONNECT tunnel to `agent.robinhood.com` (HTTP 403 from our own proxy, not from Robinhood). WebFetch of the URL also failed with a generic client error. So **no MCP `initialize`/`tools/list` and no `.well-known/oauth-*` metadata could be observed**. Every protocol-level detail below comes from documentation and is not observed behavior. This probe is the first thing to repeat from a FlowForge host that has open egress.

---

## 1. Summary table

| # | Question | Finding | Status | Src |
|---|---|---|---|---|
| 1 | Does the MCP exist? | Yes. Robinhood documents `https://agent.robinhood.com/mcp/trading` as the "Robinhood Trading MCP". | VERIFIED | S1, S2 |
| 2 | Launch date | Announced Wed 2026-05-27 as a beta (stocks first). There is no official newsroom post with a date. | UNVERIFIED (third-party: TechCrunch) | S10 |
| 3 | Rollout / waitlist | Built-in Robinhood Agents: "still rolling out. Not all customers have access." No waitlist is mentioned for the external MCP. Third-party reports say rollout to "all eligible users" is complete, and that report is itself unverified. | Partially VERIFIED (S3); rest UNVERIFIED (S12) | S3, S12 |
| 4 | Who can access | You need a primary individual investing account in good standing. The MCP account is a separate *self-directed individual investing account* dedicated to the agent, and you can hold at most 10 such accounts including it. Onboarding starts when you authenticate the agent. | VERIFIED | S1, S2 |
| 5 | Region | Only the US site (`/us/en/`) documents it. Crypto via the agent is unavailable in some states, including NY. Non-US availability is not stated. | US: VERIFIED by implication; others: UNVERIFIED | S1, S5 |
| 6 | Read scope | The agent gets **read access to ALL your Robinhood accounts** (account numbers, positions, balances, orders and transactions) but **can trade only in the Agentic/MCP account**. | VERIFIED | S1, S2, S3 |
| 7 | Tool list | About 90 tool names across 10 categories are published (see §3). | VERIFIED (names and one-line descriptions only) | S5 |
| 8 | Tool input/output schemas | Not published. They are only discoverable via `tools/list` after OAuth, and our probe was blocked. | UNVERIFIED | — |
| 9 | Order types (agent) | Market (share-based), Market (dollar-based), Limit, Stop limit, Stop market. The built-in agent page also lists Take profit/stop loss. "Advanced order" tools exist. | VERIFIED | S3, S5 |
| 10 | Trailing stop | Supported in the Robinhood app for equities. **Not listed** among agent order types. Whether `place_advanced_order` covers it is unknown. | App: VERIFIED; via MCP: UNVERIFIED | S6, S5 |
| 11 | Extended hours / 24h | Not mentioned in any agent doc. | UNVERIFIED | — |
| 12 | Fractional | Dollar-based market orders are supported, and `get_equity_tradability` reports fractional eligibility. | VERIFIED (dollar-based) | S5 |
| 13 | Short selling | Not mentioned. The account is cash or **limited margin**: "margin borrowing isn't currently available in Agentic accounts", which implies no shorting. | No borrowing: VERIFIED (S4); no shorting: UNVERIFIED inference | S4 |
| 14 | Options | Single-leg place/cancel/exercise tools exist. **"Robinhood Agents cannot place multi-leg options orders."** | VERIFIED | S5 |
| 15 | Rate limits | Not published. A third-party vendor says there is no broker-side frequency cap. | UNVERIFIED | S13 |
| 16 | OAuth flow | Docs say only "authenticate" (Claude Code: `/mcp` → authenticate). One third party (Nango) says "OAuth 2.0 with dynamic client registration", and another (Aeon skill) says refresh tokens rotate and there is no static key. OAuth 2.1/PKCE, scopes and token lifetime are not stated anywhere. | UNVERIFIED (beyond "interactive authenticate" step) | S1, S14, S15 |
| 17 | Spending limit | **There is no per-trade or daily spend cap in the docs.** The "dedicated budget" is just the cash you choose to fund the Agentic account with. Chat token billing for the built-in agent is a separate prepaid balance and does not apply to the external MCP. | VERIFIED (that the budget equals funded balance; that no cap is documented) | S2, S3, S4, S9 |
| 18 | Confirmation vs autonomous | Trade approvals are **OFF by default for MCP accounts (external agents)** and "still rolling out for MCP accounts". When off, the agent places trades without per-order confirmation. "Certain trades may still require your approval even when trade approvals are turned off." Every trade sends a notification. | VERIFIED | S5, S9 |
| 19 | Self-hosted Python client | Robinhood lists named clients plus "**any localhost platform**", and third parties report unattended CI use with refresh tokens. Nothing official addresses headless or server-side use, the Python `mcp` SDK, or a client allowlist. | "localhost platforms" VERIFIED; headless Python UNVERIFIED | S1, S2, S15 |
| 20 | Client allowlist | None documented. Named: Claude Code, Claude Desktop, ChatGPT, Codex, Codex CLI, Cursor, Grok, Perplexity, OpenClaw, Replit, AWS Quick, Poke, any localhost platform. "For other platforms, contact support." | VERIFIED (list); enforcement UNVERIFIED | S1, S2 |
| 21 | Order latency | No official numbers. | UNVERIFIED | — |
| 22 | Terms on automated use | Agentic disclosures say you are responsible for agent trades, and Robinhood "does not control, supervise, monitor, recommend, or audit" agents. The Customer Agreement says Robinhood may restrict or terminate access at its discretion, and orders via APIs "may be rejected". No dedicated public "MCP/Agentic Account Agreement" was found. | Disclosures VERIFIED; dedicated agreement UNVERIFIED | S1, S8, S9, S7 |

---

## 2. Existence, launch, access

- **Endpoint.** The *Agentic Trading overview* and *Onboarding an external agent* help articles both give the MCP URL `https://agent.robinhood.com/mcp/trading`. Claude Code setup is `claude mcp add robinhood-trading --transport http https://agent.robinhood.com/mcp/trading`, then `/mcp`, select `robinhood-trading` and authenticate. This means a **remote HTTP (streamable HTTP) transport with a browser-based authentication step**. (S1, S2) VERIFIED.
- **Product page.** "Paste one URL into your MCP config to connect most agents out of the box", followed by: create an agentic account, fund it, run your strategy. It covers equities, options and crypto. Safety features listed are a "dedicated budget", "notifications on each trade" and "disconnect at any time from the app". (S9) VERIFIED.
- **Launch.** TechCrunch (2026-05-27) reports a beta launch on 2026-05-27 with stocks only at first, and options and crypto "planned". The current help docs now include options and crypto tools. A quote from Abhishek Fatehpuria (VP Product) talks about customers bringing "their own tools, LLMs, and agents". (S10) UNVERIFIED (third-party). We found no official newsroom post.
- **Rollout.** For built-in agents, "Robinhood Agents is still rolling out. Not all customers have access." (S3) VERIFIED. For MCP accounts, "Trade approvals are still rolling out for MCP accounts. Not all customers have access." (S5) VERIFIED. RuntimeWire relays a Virtuals claim that rollout to all eligible users is complete, with ~100k accounts by late June. (S12) UNVERIFIED.
- **Eligibility.** You need a primary individual investing account in good standing. The MCP account is a self-directed individual investing account, with a cap of 10 such accounts including the MCP account. Onboarding into the MCP account is triggered when you first authenticate. Activation, reactivation or tax-certification issues can block it. (S2, S4) VERIFIED.
- **Account type.** For the built-in Agentic account, "limited margin" is the default if you accept the margin agreement and "cash" if you deselect it. "It doesn't provide borrowing power or leverage, and margin borrowing isn't currently available in Agentic accounts." (S4) VERIFIED for the built-in Agentic account. Whether the *MCP* account follows the same cash/limited-margin choice is UNVERIFIED but likely.
- **Terminology.** Robinhood uses two account names. The "Agentic account" belongs to the built-in Robinhood Agent. The "MCP account" belongs to external agents via the Trading MCP. Some tools (crypto) refer to "your MCP or agentic accounts". (S5)

## 3. Tool list (names as published; schemas NOT published)

Source: *Trading with your agent* (S5), accessed 2026-10-19. Robinhood notes that built-in and external agents "can access a slightly different set." Treat this list as indicative and **always trust the live `tools/list`**.

- **Account/portfolio/other:** `get_accounts`, `get_portfolio`, `get_realized_pnl`, `get_pnl_trade_history`, `search`, `get_limited_margin_upgrade_info`, `get_trade_approvals`, `decline_trade_approval`, `get_trade_approval_setting`
- **Watchlists:** `get_watchlists`, `get_watchlist_items`, `get_option_watchlist`, `get_popular_watchlists`, `create_watchlist`, `update_watchlist`, `follow_watchlist`, `unfollow_watchlist`, `add_to_watchlist`, `remove_from_watchlist`, `add_option_to_watchlist`, `remove_option_from_watchlist`
- **Market data:** `get_equity_historicals` (OHLCV), `get_equity_fundamentals`, `get_financials`, `get_equity_price_book` (L2, up to 4 symbols), `get_equity_technical_indicators`, `get_earnings_results`, `get_earnings_calendar` (≤31 days), `get_indexes`, `get_index_quotes`, `get_equity_news`, `get_earnings_transcript` (*built-in only*), `get_sec_filing_index`, `get_sec_filing`, `get_sec_filing_facts`, `get_sec_filing_facts_catalog`, `get_index_historicals`, `get_politician_trades`, `get_equity_analyst_ratings`
- **Equities:** `get_equity_positions`, `get_equity_tax_lots`, `get_equity_quotes` (up to 20 symbols), `get_equity_orders`, `get_equity_tradability`, `review_equity_order` (simulate and pre-trade warnings), `place_equity_order`, `cancel_equity_order`
- **Options:** `get_option_level_upgrade_info`, `get_option_chains`, `get_option_historicals`, `get_option_instruments`, `get_option_quotes`, `get_option_positions`, `get_option_orders`, `review_option_order`, `place_option_order`, `cancel_option_order`, `exercise_option`, `cancel_option_exercise`
- **Crypto:** `get_currency_pairs`, `get_crypto_quotes`, `get_crypto_positions`, `get_crypto_orders`, `preview_crypto_order`, `place_crypto_order`, `cancel_crypto_order`, `get_crypto_account_onboarding_info`, `get_crypto_tax_lots`
- **Scanner:** `get_scans`, `get_scanner_filter_specs`, `create_scan`, `run_scan`, `update_scan_filters`, `update_scan_config`, `get_scanner_datapoints`, `preview_scan`
- **Alerts:** `get_alerts`, `get_alert_log`, `create_alert`, `update_alert`, `delete_alert`, `mark_alerts_read`
- **Advanced orders:** `get_advanced_orders`, `place_advanced_order`, `cancel_advanced_order`, `review_advanced_order`
- **Legend (external agent only):** `get_legend_charts`, `get_legend_indicators`, `get_legend_indicator_catalog`, `get_legend_layouts`, `set_legend_chart_settings`, `add_legend_indicator`, `update_legend_indicator`, `remove_legend_indicator`

**Schemas:** Robinhood does not publish parameter or response schemas, error codes, idempotency keys, or a client-order-id field. Without auth these can't be obtained, so all of it is UNVERIFIED. A third-party vendor (PolicyLayer, 2026-06-19) said there were "twelve" tools at that time (S13). The much larger official list suggests the toolset has grown quickly, so **tool drift is real**.

## 4. Order types and trading constraints

- **Agent-placeable order types:** "Market (share-based)", "Market (dollar-based)", "Limit", "Stop limit", "Stop market". (S5) VERIFIED. The built-in agent overview additionally lists "Take profit/stop loss" (S3), and "Available order types depend on the asset and your account's trading eligibility." (S3) VERIFIED.
- **Robinhood app order types (not agent-specific):** equities support market, limit, stop, stop limit, take profit/stop loss and trailing stop. Market orders are GFD, and the others are GFD or GTC. Robinhood "doesn't currently support bracket orders, Market-on-Close orders, or Market-on-Open orders." (S6) VERIFIED. **Trailing stop via MCP is UNVERIFIED.** It may be part of `place_advanced_order`.
- **Extended hours / 24-hour market:** no agent doc mentions them. UNVERIFIED.
- **Fractional:** dollar-based market orders are supported, and `get_equity_tradability` reports fractional eligibility. (S5) VERIFIED. Fractional *limit* orders are UNVERIFIED.
- **Short selling:** not mentioned. Agentic accounts have no margin borrowing (S4), so assume **no shorting**. That is an inference and stays UNVERIFIED.
- **Options:** single-leg only. "Robinhood Agents cannot place multi-leg options orders for you." (S5) VERIFIED. Options level requirements are per the account's options approval.
- **Crypto:** trading only, with no transfer, stake or lend. It isn't available in some states, including NY, and moving to a supported state restores access after 45 days. (S5) VERIFIED. (FlowForge is equities-only, so this is informational.)

## 5. Spending limit: how it actually works

- The **only documented spending control is the funded cash balance** of the dedicated Agentic/MCP account. The product page calls this "a dedicated budget". You choose how much to fund, by bank transfer, transfer from another Robinhood account, or asset transfer. (S3, S4, S9) VERIFIED.
- **No per-trade, per-day or per-symbol caps are documented** by Robinhood. (S3, S4, S5) VERIFIED as "not documented". A third-party vendor (S13) states explicitly that there is no per-trade cap, no daily cap and no symbol allow-list. UNVERIFIED.
- The agent's *trading* is confined to that account. Its *read* scope covers all your accounts. (S1, S2) VERIFIED.
- Under limited margin, unsettled sale proceeds can be reused, but there is no leverage. (S4) VERIFIED.
- Robinhood's own (pre-release, employee-testing) agent agreement says: "Do not deposit more than you are prepared to lose." (S11). Note: S11 is an *employee testing* agreement for the built-in agent and is **not** the customer terms for the external MCP.
- The **Agentic Credit Card** (separate product, via a banking MCP) does have user-set monthly limits (S10, third-party). Do not confuse it with trading.

## 6. Confirmation vs autonomous mode

- "Trade approvals are turned on by default for Robinhood Agents (built-in agents)" and "are turned off by default for MCP accounts (external agents)." "Trade approvals are still rolling out for MCP accounts. Not all customers have access." (S5) VERIFIED.
- With approvals off, "your agent can place eligible trades for you without your manual confirmation of each order." "Certain trades may still require your approval even when trade approvals are turned off." (S5) VERIFIED. **Which trades trigger forced approval is not documented.** UNVERIFIED.
- With approvals on, the agent only *proposes* trades, and you must place them manually in the Robinhood app. MCP tools `get_trade_approvals`, `decline_trade_approval` and `get_trade_approval_setting` exist, but **no "approve" tool** is listed, so approval is human-only in the app. (S5) VERIFIED.
- You get a notification on every agent trade and can disconnect at any time from the app. (S9) VERIFIED.
- The default for an external agent is therefore **autonomous**. FlowForge must implement its own pre-trade risk gates.

## 7. Auth, transport, and self-hosted Python use

- **Transport:** HTTP (`--transport http` in Claude Code), meaning MCP streamable HTTP. (S1, S2) VERIFIED.
- **Auth:** official docs say only "authenticate", an interactive step that also triggers MCP account onboarding. (S1, S2) VERIFIED. **No official documentation of OAuth version, DCR, PKCE, scopes, redirect-URI rules, access-token TTL, refresh-token rotation or revocation.** UNVERIFIED. Third-party reports:
  - Nango (integration vendor): "uses OAuth 2.0 with dynamic client registration". (S14) UNVERIFIED.
  - Aeon skill (open-source agent): tokens are stored as secrets, refreshed at the start of each run, and "rotating refresh tokens" are used. There is "no static key", and a 401 means the refresh failed. (S15) UNVERIFIED. If accurate, this is the pattern FlowForge needs: one interactive consent, then a headless refresh.
  - Neither claim could be checked against `/.well-known/oauth-protected-resource` or `/.well-known/oauth-authorization-server` because our egress was blocked.
- **Self-hosted Python (`mcp` SDK, streamable HTTP and OAuth):** Robinhood supports "any localhost platform" (S1, S2) VERIFIED, and there's no published client allowlist. If the server follows the MCP authorization spec (DCR plus authorization-code flow), the official Python SDK's OAuth client provider should work, but this is **UNVERIFIED**. Risks:
  - (a) DCR may restrict redirect URIs, for example to loopback only, which needs a one-time interactive login on a machine with a browser.
  - (b) Refresh-token TTL or rotation may force periodic human re-auth.
  - (c) Robinhood may fingerprint or allowlist clients. Nothing says it does, and the "contact support" line for unlisted platforms hints at a soft policy.
- **No static API key exists** (S15, third-party). There is no official REST trading API alternative documented here.

## 8. Rate limits and latency

- **Rate limits:** not published anywhere official. UNVERIFIED. A third-party vendor claims there is no broker-side frequency cap (S13). Also UNVERIFIED and not something to rely on. Assume 429s or throttling may occur, and design for backoff.
- **Latency:** no official numbers. UNVERIFIED. Expect an MCP JSON-RPC round trip over HTTPS, plus Robinhood order routing (PFOF market makers). The full path is order submit to ack to fill, measured via `get_equity_orders`. FlowForge must measure this itself in paper-like small-size live tests.

## 9. Terms on automated / agentic / algorithmic use

- **Agentic disclosures** (S1, S9, S3): "involves significant risk, including the possible loss of your entire investment". Agents "can make errors, misinterpret instructions, or act on outdated information". "Robinhood does not control, supervise, monitor, recommend, or audit agents". "You are responsible for your agent's actions, including the trades it places". Data shared with an AI provider is governed by that provider's terms. Brokerage is through Robinhood Financial LLC, with clearing by Robinhood Securities, LLC. Robinhood Labs, LLC provides the agent technology and is not a broker-dealer or investment adviser. VERIFIED.
- **Customer Agreement** (S7, version at the CDN URL is dated "Revised June 22, 2020", so it **may be stale**): the account is self-directed and you are solely responsible for orders. Third-party access you grant is at your risk, with indemnity (§4, §5.K, §17). Automated systems carry risk and Robinhood has no liability (§17). Orders via API products "are not vetted until they are received by Robinhood" and "may [be] reject[ed]" (§33.E). Robinhood may restrict access or terminate the account at its sole discretion (§16). VERIFIED for that version. **Whether a newer Customer Agreement or a dedicated MCP/Agentic Account Agreement adds limits on algorithmic use is UNVERIFIED.** We found none publicly.
- **Employee-testing "AI Agent Agreement"** (S11) is pre-release and covers the built-in agent, not FlowForge's case. It notes automations "as frequently as every minute" and that "you should not rely on the availability or operation of Trade Approvals". Use it as a signal of direction, not as binding terms.
- **Nothing found prohibits** a user's own self-hosted autonomous agent via the MCP. The "localhost platform" support and the autonomous default point the other way. UNVERIFIED as a legal conclusion.

---

## 10. Design implications for FlowForge

### RobinhoodMCPAdapter

1. **Transport and auth.** Use the official `mcp` Python SDK streamable-HTTP client with its OAuth client provider. Do the first consent interactively (operator runbook), then persist and rotate tokens in a secrets store with atomic writes, because refresh tokens may be single-use. On 401 or refresh failure, **halt trading (fail closed)** and page the operator. Never retry auth in a loop. Treat the account as unusable until re-auth.
2. **Read scope is account-wide.** The token can read *all* of the user's Robinhood accounts. Filter to the MCP account ID at the adapter boundary. Never log other accounts' numbers. Treat the token as highly sensitive.
3. **FlowForge owns all risk limits.** Robinhood offers no per-trade or daily caps and approvals are OFF by default, so FlowForge must enforce max notional per order, per day, per symbol, a position cap, a symbol allowlist, a max order rate and a kill switch *before* calling `place_equity_order`. Fund the MCP account only with the amount you can afford to lose. The funded balance is the only hard broker-side backstop.
4. **Always review before place.** Call `review_equity_order` and parse warnings. If the review fails or shows unexpected warnings, abort. Robinhood does not enforce this sequence, so FlowForge must.
5. **Order-type mapping.** Map strategy intents only to market (share or dollar), limit, stop market and stop limit. Reject trailing-stop, extended-hours, short and multi-leg intents at the adapter unless live discovery proves support. Emulate trailing stops client-side if needed, accepting the risk if FlowForge is offline.
6. **Idempotency and reconciliation.** No client-order-id is documented, so after any timeout or ambiguous response, **reconcile via `get_equity_orders`** before resubmitting to avoid duplicate orders. Keep a local order journal and reconcile against `get_equity_positions` on startup and periodically.
7. **"Certain trades may still require approval."** An order can come back pending human approval. Handle that as a distinct non-filled state with a timeout and cancel path via `get_trade_approvals`.
8. **Backoff.** There are no published rate limits, so use conservative client-side throttling (e.g., ≤1 order/sec, tunable), exponential backoff on 429/5xx, and circuit breakers.

### Fail-closed tool discovery

- On every session start, run `initialize` then `tools/list`. Validate that the **required set** is present (`get_accounts`, `get_portfolio`, `get_equity_positions`, `get_equity_orders`, `get_equity_quotes`, `get_equity_tradability`, `review_equity_order`, `place_equity_order`, `cancel_equity_order`) and that each `inputSchema` matches a **pinned schema hash or shape** checked into the repo.
- If any required tool is missing, renamed, or has a schema that changed incompatibly, **disable trading (read-only or halt)** and alert. Never let an LLM "figure out" a changed order schema. Expect tool drift: third parties reported 12 tools in June 2026 versus ~90 today.
- Expose only an **allowlist** of tools to any LLM component. Never expose `place_option_order`, `exercise_option`, crypto, watchlist-mutation, alert or Legend tools.
- Record the server's `serverInfo`, protocol version and full `tools/list` snapshot per session for audit.

---

## 11. Open questions (to resolve from an unblocked host or with Robinhood support)

1. What does unauthenticated `POST /mcp/trading` (`initialize`) return? Expect a 401 with a `WWW-Authenticate` header carrying `resource_metadata`. What do the `/.well-known/oauth-protected-resource` and `oauth-authorization-server` documents say about DCR, PKCE, scopes, token TTL and refresh rotation?
2. Are arbitrary DCR clients and redirect URIs accepted (loopback, custom HTTPS)? Is there a client allowlist?
3. What are the access and refresh token lifetimes, and how often is human re-consent required?
4. What are the exact `inputSchema`/`outputSchema` for `review_equity_order`, `place_equity_order` and `place_advanced_order`? Is there time-in-force (GFD/GTC), an extended-hours flag, trailing-stop support, or a client order id?
5. What are the rate limits and error codes?
6. Which trades force approval when approvals are off? Can trade approvals be turned on for MCP accounts yet (it's "still rolling out")?
7. Is the MCP account cash or limited-margin, and is that selectable? What PDT treatment applies under limited margin with < $25k?
8. Is there a current Customer Agreement newer than the 2020 PDF found, and is there a dedicated MCP/Agentic terms document?
9. Is it available outside the US? (Assume US-only.)
10. What is realistic submit-to-ack-to-fill latency? Measure with minimal-size live orders.

---

## 12. Sources (all accessed 2026-10-19)

Official Robinhood:
- **S1** Agentic Trading overview (help center): https://www.robinhood.com/us/en/support/articles/agentic-trading-overview/
- **S2** Onboarding an external agent (help center): https://robinhood.com/us/en/support/articles/onboarding-an-external-agent/
- **S3** Robinhood Agents overview (help center): https://robinhood.com/us/en/support/articles/robinhood-agents-overview/
- **S4** Setting up an agent (help center): https://robinhood.com/us/en/support/articles/setting-up-an-agent/
- **S5** Trading with your agent (help center; tool list, order types, approvals): https://robinhood.com/us/en/support/articles/trading-with-your-agent/
- **S6** Order types (help center): https://robinhood.com/us/en/support/articles/order-types/
- **S7** Customer Agreement PDF (rev. 2020-06-22; may be stale): https://cdn.robinhood.com/assets/robinhood/legal/Customer%20Agreement.pdf
- **S8** Agentic trading and cards hub: https://robinhood.com/us/en/support/agentic-trading/
- **S9** Agentic Trading product page: https://robinhood.com/us/en/agentic-trading/
- **S11** AI Agent Agreement (employee testing / pre-release, built-in agent): https://cdn.robinhood.com/assets/robinhood/legal/friday_agent_agreement.md
- MCP endpoint (probe blocked by sandbox egress proxy, 403 on CONNECT; WebFetch client error): https://agent.robinhood.com/mcp/trading

Third-party (UNVERIFIED, for context only):
- **S10** TechCrunch, "Robinhood now lets your AI agents trade stocks" (2026-05-27): https://techcrunch.com/2026/05/27/robinhood-now-lets-your-ai-agents-trade-stocks/
- **S12** RuntimeWire, "Virtuals says Robinhood opened agentic trading to all eligible users": https://runtimewire.com/article/virtuals-says-robinhood-completed-agentic-trading-rollout
- **S13** PolicyLayer blog (2026-06-19): https://policylayer.com/robinhood
- **S14** Nango docs, Robinhood MCP integration: https://nango.dev/docs/api-integrations/robinhood-mcp.md
- **S15** skills.sh, aeonfun/aeon robinhood-mcp skill: https://www.skills.sh/aeonfun/aeon/robinhood-mcp
- HackerNoon, "How to build an autonomous AI trading agent on Robinhood" (2026-09-24): https://hackernoon.com/how-to-build-an-autonomous-ai-trading-agent-on-robinhood
