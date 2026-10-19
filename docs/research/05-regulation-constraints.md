# 05 — Regulation & Trading Constraints (US equities, Robinhood + Alpaca paper)

**Status date:** 2026-10-19 · **Author:** Research Scout · **All sources accessed 2026-10-19**

> **Not tax or legal advice.** This is engineering research to inform FlowForge's risk-engine design. It summarizes public rule text and broker help pages as read on 2026-10-19. Rules, broker policies and API fields change; the account owner should confirm with Robinhood/Alpaca and a qualified tax professional / attorney before trading real money or making any tax election.

---

## TL;DR — what applies today (2026-10-19)

- **The FINRA pattern day trader (PDT) rule is gone.** SEC approved SR-FINRA-2025-017 on 2026-04-14 (Rel. 34-105226); FINRA Regulatory Notice 26-10 set the **effective date as 2026-06-04**. The PDT definition (4+ day trades in 5 business days), the **$25,000 minimum equity**, day-trading buying power and day-trade calls were removed from Rule 4210 and replaced by **intraday margin requirements** (IML / intraday margin deficit). [S1–S4]
- **Firm-level transition:** firms that need more time may phase in through **2027-10-20**, and FINRA tells investors their firm "might continue operating under the old day trading margin requirements during the transition." So *which regime applies is broker-specific*. [S3, S5]
- **Robinhood:** says PDT was replaced on 2026-06-04, day-trade restrictions/calls no longer apply to margin accounts, existing PDT flags removed, $25k no longer required; $2,000 margin minimum still applies; real-time monitoring blocks activity that would create/increase an intraday margin deficit. [S6]
- **Alpaca:** implemented on 2026-06-04; PDT designation, $25k minimum and day-trade counting removed; `daytrade_count`, `daytrading_buying_power`, `pattern_day_trader` fields removed from the API on 2026-07-06; pre-trade checks reject orders that would create a margin deficit. [S9–S11]
- **Cash accounts are unaffected by the change**: Reg T still prohibits freeriding (90-day freeze), and good-faith violations still exist conceptually; Robinhood cash accounts simply can't trade with unsettled sale proceeds. Settlement is **T+1** (since 2024-05-28). [S7, S13, S14, S15]
- **No trade-count cap from regulation today** — but FlowForge should keep a *self-imposed* `max_trades_per_day` as a risk control, and must model buying power / IML instead of a day-trade counter.

---

## 1. Status table

| # | Item | Current status (2026-10-19) | Verification | Sources |
|---|------|-----------------------------|--------------|---------|
| 1 | Old PDT rule (Rule 4210(f)(8)(B)): 4+ day trades in 5 business days in a margin account ⇒ PDT; $25k min equity | **Rescinded** effective 2026-06-04 (FINRA describes the trade-count designation and $25k min as removed "in their entirety") | VERIFIED (rescission). Exact "4 day trades / 5 business days" wording not re-read from the pre-2026 rule text in this pass — the investor page only says "based on trade count" | S2, S3, S4, S8 |
| 2 | SR-FINRA-2025-017 (replace day-trading margin with intraday margin) | **SEC approved** 2026-04-14, Rel. No. 34-105226 (accelerated approval, as modified by Amendment No. 1); 91 Fed. Reg. 20731 (2026-04-17) | VERIFIED (SEC order PDF); FR cite via WilmerHale secondary source | S1, S4 |
| 3 | Effective date | **2026-06-04** (Reg. Notice 26-10, published 2026-04-20; 45 days after) | VERIFIED | S2, S3 |
| 4 | Phase-in window | Firms may phase in through **2027-10-20** ("18 months" from notice); old rules may continue at a firm during transition | VERIFIED | S2, S3 |
| 5 | Intraday margin mechanics | IML-reducing transactions; intraday margin deficit to be met "as promptly as possible"; deficit outstanding until satisfied or close of 15th business day; 90-day freeze if customer makes a practice of not meeting deficits by close of 5th business day; de minimis = lesser of 5% equity or $1,000; good-faith & portfolio-margin accounts excluded from core requirement | VERIFIED | S2 |
| 6 | $2,000 minimum equity for margin | Still applies (FINRA + Robinhood + Alpaca) | VERIFIED | S3, S6, S10, S16 |
| 7 | Robinhood PDT handling | PDT replaced 2026-06-04; no day-trade restrictions or day-trade calls in margin accounts; PDT flags removed; real-time IMD prevention | VERIFIED | S6 |
| 8 | Robinhood day-trade counter UI | Not mentioned on current help page; presumed retired with PDT | **UNVERIFIED** | S6 |
| 9 | Robinhood agentic (MCP) account cash vs margin | Separate dedicated "Agentic/MCP" self-directed individual account; help pages list **long equities**, options, crypto; cash vs margin status **not stated** | **UNVERIFIED** (account type, margin/short eligibility, any agent-specific trade caps) | S17, S18 |
| 10 | Robinhood cash accounts | "Cash accounts can't trade with unsettled funds" from stock/option sales; T+1 | VERIFIED | S7, S19 |
| 11 | Good-faith violation (cash acct) | Defined by FINRA: buy with unsettled proceeds, then sell before those proceeds settle | VERIFIED (definition). Broker-specific GFV penalty counts (e.g., "3/4 GFVs in 12 months ⇒ 90-day restriction") **UNVERIFIED** | S14 |
| 12 | Freeriding (Reg T) | Prohibited; may trigger 90-day freeze (must pay in full on trade date) | VERIFIED | S13, S14 |
| 13 | Alpaca live PDT | Removed 2026-06-04; API fields removed 2026-07-06; intraday buying power; 4x intraday BP min equity lowered $25k → $2k | VERIFIED | S9, S10, S11, S12 |
| 14 | Alpaca **paper** PDT simulation | Historically paper simulated PDT protection (pre-2026 forum reports). Current paper docs don't mention PDT/IML; fields were removed API-wide. Paper presumably no longer enforces PDT, IML simulation fidelity unknown | **UNVERIFIED** | S20, S21 |
| 15 | T+1 settlement | Compliance date **2024-05-28** | VERIFIED | S15 |
| 16 | Wash sale (§1091) | 61-day window (30 days before/after); substantially identical; options/contracts count; basis adjustment §1091(d); IRA purchases trigger (Rev. Rul. 2008-5); broker 1099-B reporting mandatory only same account + same CUSIP | VERIFIED | S22, S23, S24 |
| 17 | Trader tax status / §475(f) MTM | Exists; election due by original due date of prior-year return; no late elections; wash sale rules don't apply under MTM; ordinary treatment, Form 4797 | VERIFIED (mention only) | S25 |
| 18 | Reg SHO Rule 201 (SSR) | Triggered by ≥10% decline vs prior close; applies rest of day + next day; shorts only above national best bid | VERIFIED | S26, S19b |
| 19 | LULD | Bands 5/10/20% (or lesser of $0.15/75%) vs 5-min avg; limit state >15s ⇒ 5-min pause; bands double in last 25 min for Tier 1 & Tier 2 ≤$3; LULD amended 2026-08-05 to add overnight bands | VERIFIED (core); overnight-band details **UNVERIFIED** beyond NYSE filing reference | S27, S28 |
| 20 | Market-wide circuit breakers | S&P 500: 7% / 13% (15-min halt if before 3:25 pm ET; none at/after) / 20% (halt rest of day) | VERIFIED | S27, S28 |
| 21 | Trading hours | Regular 9:30–16:00 ET. Robinhood extended 7:00–9:30 & 16:00–20:00; 24 Hour Market 20:00–7:00 (select names) Sun 8pm–Fri 8pm; market & stop orders not executed outside regular hours. Exchange overnight sessions (e.g., NYSE Arca 21:00–04:00) planned from **2026-12-06** | VERIFIED (Robinhood); exchange 23/5 start date VERIFIED as "planned" per NYSE filing | S29, S30, S28 |

---

## 2. Details

### 2.1 FINRA Rule 4210 — from PDT to intraday margin

**What it was (pre-2026-06-04).** FINRA Rule 4210 designated "pattern day traders" by trade count in margin accounts and imposed a **$25,000 minimum equity** requirement, day-trading buying power (DTBP, up to 4x maintenance excess) and day-trade margin calls; widely cited as 4+ day trades within 5 business days (exact old-rule text not re-fetched in this pass — FINRA's current investor page only says "based on trade count"). [S3, S4]

**The change.** SR-FINRA-2025-017, approved by SEC order Rel. 34-105226 (2026-04-14) "on an accelerated basis," as modified by Amendment No. 1. FINRA Reg. Notice 26-10 (2026-04-20) set the effective date at **2026-06-04** and allows firms needing more time to phase in **until 2027-10-20**. The order states that during the 45 days before effectiveness all firms had to apply the current day-trading rules. [S2, S4]

**What replaced it** (Reg. Notice 26-10 + FINRA investor insight + updated interpretations valid from 2026-06-04): [S2, S3, S8]
- Day trading margin requirements removed "in their entirety," including the PDT trade-count designation and the $25k minimum. Interp 4210(b)(4)/025 (PDT minimum equity) marked "Rescinded."
- On any day with an **IML-reducing transaction** (broadly: anything that reduces what the customer could withdraw while still meeting maintenance margin — purchases other than covering shorts, short sales, withdrawals), the firm must determine intraday margin deficits — whether or not the customer day trades. Firms may block in real time or compute at end of day and issue a call.
- Deficits must be met "as promptly as possible"; they stay outstanding until satisfied or immediately after close of the **15th business day**.
- **90-day freeze**: if a customer "makes a practice" of not satisfying deficits and fails to satisfy one by the close of the **5th business day**, the firm must block creating/increasing shorts or debit balances for 90 calendar days (or until satisfied). De minimis: deficits ≤ lesser of 5% of equity or $1,000; extraordinary circumstances.
- Maintenance margin (25% for long margin-eligible equities, house can be higher) must be maintained **throughout the day**, not just at close.
- Excluded: good-faith accounts and portfolio-margin accounts (PM < $5M has a substantially similar intraday requirement).
- The rule text addresses **margin accounts only**; cash accounts are not discussed. [S2, S4]

**Exactly what applies to FlowForge today:** No regulatory day-trade count limit and no $25k requirement at Robinhood or Alpaca (both state they implemented on 2026-06-04). In a margin account the binding constraint is **intraday buying power / maintenance margin** checked in real time; repeated unmet deficits ⇒ up to 90-day freeze on new debit/short. In a cash account the binding constraint is **settled cash** (T+1). Other brokers may still be on the old regime until 2027-10-20.

### 2.2 Cash accounts: GFV, freeriding, settlement

- **Freeriding**: buying and selling a security before paying for it violates Reg T; the broker may freeze the account for 90 days, during which purchases must be paid in full on trade date. [S13, S14]
- **Good-faith violation**: buying with unsettled sale proceeds and selling that new position before the proceeds settle. [S14]
- **Robinhood cash accounts** prevent the issue structurally: "Cash accounts can't trade with unsettled funds" from stock and option sales; proceeds settle in 1 trading day. Cash accounts also "can't simultaneously close an option and open a new one." Account type can't be switched more than once per trading day. [S7, S19]
- Practical effect for intraday trading in a cash account: each dollar of settled cash can fund roughly one round trip per day (buy then sell); the sale proceeds become usable the next trading day. Capital turnover ≈ 1x settled cash per day.
- Robinhood Instant Deposits: up to $1,000 instantly available from a pending bank deposit (behavior for sale-then-withdraw / GFV accounting not verified). [S7]

### 2.3 Robinhood specifics

- Day trading help page: "On June 4, 2026, FINRA's new intraday margin standards will replace Pattern Day Trading (PDT)." Day trade restrictions and day trade calls no longer apply to margin accounts; existing PDT flags/restrictions removed; $25k no longer required; $2,000 margin minimum still applies; Robinhood monitors in real time "to prevent activity from creating or increasing intraday margin deficits"; repeated failures ⇒ further restrictions. [S6]
- Margin: need "at least $2,000 or 100% of the security's purchase price (whichever value is less)"; below $2,000 while borrowing ⇒ margin call / liquidation risk. Robinhood may impose additional eligibility requirements. Gold is not stated as a requirement for margin on the current page (**UNVERIFIED** whether Gold is still required to enable margin). [S16]
- Short selling: margin account only, $2,000 min, rolling release, Day orders only for opening shorts, market/limit/stop; allowed in regular and extended hours, not overnight; Rule 201 notifications shown. [S19b]
- **Agentic / MCP account**: external agents must use a dedicated "Robinhood MCP account" (self-directed individual investing account, counts toward the 10-account cap; requires a primary individual account in good standing). The agent "can only place trades in your Robinhood Agentic account" but gets read access to all accounts. Agents can place market (share or dollar), limit, stop-limit, stop-market orders on **long equities**, options (no multi-leg), crypto. Trade approvals are off by default for external MCP agents; "certain trades may still require your approval." "You are ultimately responsible for the trades your AI agent places." [S17, S18]
  - **UNVERIFIED**: whether the MCP account is cash or margin (or can enable margin/shorting), whether any agent-specific daily trade/notional caps exist, and whether extended/24-hour sessions are available through the MCP. "Long equities" wording suggests no shorting via agent.

### 2.4 Alpaca (live + paper)

- Live: PDT designation, PDT restrictions, $25k minimum and day-trade counting removed on 2026-06-04; PDT-restricted accounts unrestricted. Intraday Buying Power replaces DTBP (running calc with intraday P&L). Pre-trade checks reject orders that would put the account into margin deficit. Leverage-enabled accounts: 4x intraday BP min equity lowered $25k → $2k. IMD calls; failing repeatedly within 5 business days ⇒ restriction up to 90 days. [S9, S10]
- Alpaca docs note an IMD call "must be satisfied within two business days" (house rule; stricter than FINRA's 15-day outer bound). [S10]
- API: `pattern_day_trader`, `daytrade_count`, `last_daytrade_count`, `daytrading_buying_power`, `last_daytrading_buying_power`, `dtbp_check` deprecated 2026-06-03, **removed 2026-07-06**; use `buying_power`. Old SDKs broke (C# SDK fixed in v7.2.2, 2026-07-09). [S11, S9, S21]
- All Alpaca accounts are opened as margin accounts; `multiplier`=1 is a limited 1x margin account; $2,000+ equity needed for margin and shorting. [S12]
- **Paper**: $100k default, fills against NBBO without liquidity checks, random partial fills 10% of the time, no dividends, no slippage/market impact/regulatory fees. Paper docs do not mention PDT or intraday margin. Historically (pre-2026) paper accounts enforced PDT protection ("trade denied due to pattern day trading protection"). Whether paper now simulates IML/IMD is **UNVERIFIED**. [S20, S21]
- Alpaca also enforces wash-*trade* (self-trade) protection, including in paper — an order that could cross your own opposite-side open order is rejected (HTTP 403), except bracket/OCO/trailing stop. (Note: this is distinct from the tax wash *sale* rule.) [S31]

### 2.5 Settlement — T+1

SEC compliance date **2024-05-28**; standard cycle shortened from T+2 to T+1 for stocks, bonds, munis, ETFs, certain mutual funds, exchange-traded LPs. [S15]

### 2.6 Wash sale rule (IRC §1091) — implications for intraday trading

- **Rule**: loss on sale of stock/securities is disallowed if within a window "beginning 30 days before the date of such sale... and ending 30 days after" the taxpayer acquires (or contracts/options to acquire) **substantially identical** stock or securities. Contracts and options count. [S22]
- **Basis adjustment** (§1091(d)): replacement shares take the basis of the shares sold, adjusted by the difference between purchase price and sale price — in effect the disallowed loss is added to the replacement lot's basis (and holding period tacks). Matching for partial lots is by regulation (§1091(b)/(c), Treas. Reg. 1.1091-1). [S22, S32]
- **Across accounts**: the rule is taxpayer-level. Rev. Rul. 2008-5: a repurchase in the taxpayer's IRA/Roth IRA triggers the wash sale, and the IRA basis is **not** increased (loss permanently lost). Spouse / controlled-entity purchases also commonly treated as triggering — **UNVERIFIED** in this pass (Pub 550 wash-sale section did not load). [S23]
- **Broker reporting is incomplete**: brokers must report disallowed loss on 1099-B box 1g only when sale and purchase are in the **same account**, covered securities, **same CUSIP**; other cases are optional. So Robinhood's 1099-B for the MCP account will not reflect washes against the user's primary Robinhood account, Alpaca, or an IRA. [S24]
- **Why it matters for FlowForge**: an intraday strategy that repeatedly re-enters the same ticker after a losing exit will continuously defer losses into the basis of open lots. At year-end, losses on positions that were re-bought within 30 days (including in Jan of the next year) can be disallowed in the current year. If FlowForge ends the year flat and doesn't trade the same name for 31 days, deferred losses generally get realized — but cross-account repurchases (especially IRA) can make them permanent.
- **Trader tax status / §475(f)** (mention only, not advice): taxpayers who qualify as traders (seek to profit from daily movements; substantial; continuous and regular) can elect mark-to-market. Election is due by the original due date of the **prior** year's return; late elections not allowed. Under MTM, gains/losses are ordinary (Form 4797 Part II) and "the wash sale rules... do not apply." Requires Form 3115 when changing method. [S25]

### 2.7 Short sale restriction — Reg SHO Rule 201

Triggered when a stock declines ≥10% from prior day's close; restriction applies for the remainder of the day **and the following day**; short sales permitted only at a price **above the current national best bid**. Robinhood notifies users during order entry. [S26, S19b]

### 2.8 LULD and market-wide circuit breakers

- **LULD** (9:30–16:00 ET): bands of 5%, 10%, 20% (or lesser of $0.15/75%) around the 5-minute average price by tier/price; if not back inside bands within 15 seconds ⇒ 5-minute trading pause; bands double in last 25 minutes for Tier 1 and Tier 2 ≤ $3. An LULD plan amendment approved 2026-08-05 adds **overnight price bands** (details UNVERIFIED). [S27, S28]
- **MWCB** (S&P 500 vs prior close): Level 1 (7%) or Level 2 (13%) before 3:25 pm ET ⇒ 15-minute market-wide halt; at/after 3:25 pm ⇒ no halt; Level 3 (20%) ⇒ halt for rest of day. 2026 NYSE filings (SR-NYSE-2026-43) re-word reopening after Level 3 for 23/5 trading; thresholds unchanged. [S27, S28]

### 2.9 Trading hours

- Regular: 9:30–16:00 ET Mon–Fri excl. holidays.
- Robinhood: extended 7:00–9:30 and 16:00–20:00 (fractional after-hours to 19:30); 24 Hour Market 20:00–7:00 for select stocks/ETFs, Sun 20:00–Fri 20:00. Outside regular hours only **limit** orders execute; market and stop/stop-limit/trailing orders queue for next open. GFD orders expire at end of the chosen session. [S29, S30]
- Exchanges: NYSE Arca and others plan overnight sessions 21:00–04:00 ET from **2026-12-06** (NYSE itself not implementing 23/5 "at this time"). [S28]

---

## 3. Design implications for the risk engine

### 3.1 `max_trades_per_day` logic
- **Do not implement a PDT 3-in-5 counter as a hard regulatory gate** for Robinhood or Alpaca — it no longer applies there (verified). Keep the counter code behind a broker-capability flag (`regime: "intraday_margin" | "legacy_pdt" | "cash"`) because other brokers may still run legacy PDT until 2027-10-20.
- Keep `max_trades_per_day` as a **self-imposed risk/overtrading control** (configurable, e.g. default low for v1), plus `max_round_trips_per_symbol_per_day` and `max_daily_loss` kill switch.
- Stop reading `daytrade_count` / `daytrading_buying_power` / `pattern_day_trader` from Alpaca — those fields are **removed** (2026-07-06); consumers must tolerate their absence. Use `buying_power` (and `multiplier`, `equity`, `last_equity`, `maintenance_margin`).
- Treat a broker rejection for margin deficit as a first-class, non-retryable error (log + halt new entries for the day).

### 3.2 Cash vs margin
- **Cash mode** (`account_mode=cash`): track a `settled_cash` ledger with T+1 release using the NYSE trading calendar (weekends/holidays). Buying power for new entries = settled cash only (Robinhood enforces this; Alpaca 1x is a limited margin account — model the same conservatively in paper so paper ≠ fantasy). Never sell a position bought with unsettled proceeds before those proceeds settle (GFV guard), even if the broker would allow it.
- **Margin mode**: pre-trade check against intraday maintenance excess (model IML: `equity − maintenance_requirement` after the hypothetical fill, with a house buffer, e.g. require ≥ X% headroom). Forbid any order that would create an intraday margin deficit; if a deficit is ever reported, enter **liquidation-only** mode and alert the human. Track deficit history because repeated unmet deficits ⇒ 90-day freeze.
- Enforce `equity ≥ $2,000` (+buffer) before using any leverage; below that, 1x only.
- **Robinhood agentic account**: until verified, assume **long-only, unleveraged, cash-like** behavior; no shorting module enabled for Robinhood.
- Paper (Alpaca $100k default) should be configured to the **real intended starting balance** and run in the same mode (cash-sim) as live, since paper doesn't model settlement/IML fidelity (unverified) or slippage.

### 3.3 Wash-sale tracking in the journal
- Journal every fill with: account_id (incl. external/manual accounts the user declares, e.g. primary Robinhood, IRA), symbol, CUSIP, lot id, qty, price, timestamp, fees.
- Maintain a **lot engine** that flags a wash sale when a loss-realizing sale has a buy of the same symbol/CUSIP (and later: options on it / declared "substantially identical" groups like share-class pairs) within ±30 calendar days across **all declared accounts**; compute disallowed loss and the basis/holding-period adjustment onto the replacement lot.
- Report "realized P&L (broker view)" vs "tax-estimated realized P&L (wash-adjusted)" separately; never use 1099-B as the source of truth for cross-account washes.
- Risk/strategy hooks: optional `wash_sale_cooldown` (don't re-enter a symbol within 30 days after a losing exit) — off by default for intraday strategies, but **force-on for December** (configurable) to avoid deferring losses into the next tax year; warn if a symbol traded at a loss is also held/bought in an IRA.
- Store a `tax_profile` flag (`mtm_475f: bool`) — if true, wash-sale adjustments are informational only. Do not let the bot decide this.

### 3.4 Halt and market-structure handling
- Subscribe to/poll halt status (LULD pauses, regulatory halts) via market data; on halt: cancel/park new entries for that symbol, don't submit market orders into a reopening auction, re-arm only after N minutes of normal quotes post-resume.
- LULD awareness: don't place stop-market orders that may trigger inside a limit state; prefer limit/stop-limit near bands; widen protections in the last 25 minutes when bands double.
- MWCB: if S&P 500 drop ≥ 7% (or a market-wide halt flag), go **flat/flatten-only** mode for the day; Level 3 ⇒ day over.
- Rule 201: if shorting is ever enabled, check SSR flag before any short; only allow short limit orders priced above national best bid.
- Session guard: default to **regular hours only** (9:30–16:00 ET) for v1. If extended hours are enabled: limit orders only, smaller size, wider spread checks; never rely on stop orders outside RTH (they queue). Flatten before 16:00 if the strategy is strictly intraday. Use an exchange holiday/early-close calendar. Watch for the 2026-12-06 overnight-session rollout and LULD overnight bands.

---

## 4. Open questions

1. **Robinhood MCP/Agentic account**: cash or margin? Can it enable margin, shorting, extended/24-hour orders via the MCP tools? Any agent-specific trade-count, notional or rate limits? Which trades "still require your approval" with approvals off? (Ask Robinhood support / test with tiny size.)
2. Does Robinhood still show a day-trade counter anywhere (e.g., for cash accounts) and what are its current GFV/freeriding penalty policies for cash accounts (count thresholds, restriction length)?
3. Is Robinhood Gold still required to enable margin? (Current margin page doesn't say.)
4. Does Alpaca **paper** simulate the intraday margin rule / pre-trade IMD rejections / settlement for 1x accounts? (Empirical test needed.)
5. Robinhood's house maintenance requirements per symbol (often > 25%) and whether real-time IMD prevention applies to the MCP account.
6. Exact LULD overnight band parameters (2026-08-05 amendment) and whether Robinhood's 24 Hour Market venue follows them.
7. Old Rule 4210(f)(8)(B) wording ("four or more day trades within five business days … more than 6%") — not re-fetched; only needed if a legacy-PDT broker is ever added.
8. Pub 550 wash-sale section (spouse/controlled corp, "more or less stock bought than sold", same-day gain/loss) did not load; confirm before building lot-matching rules beyond §1091 basics.

---

## 5. Sources (all accessed 2026-10-19)

- **S1** WilmerHale client alert, "SEC Approves Amendments to FINRA Rule 4210…" (2026-04-23; secondary, used for FR cite 91 FR 20731) — https://www.wilmerhale.com/en/insights/client-alerts/20260423-sec-approves-amendments-to-finra-rule-4210-replacing-day-trading-margin-requirements-with-a-modernized-intraday-margin-standard
- **S2** FINRA Regulatory Notice 26-10 — https://www.finra.org/rules-guidance/notices/26-10 (PDF: https://www.finra.org/sites/default/files/2026-04/Regulatory-Notice-26-10.pdf)
- **S3** FINRA Investor Insights, "Understanding the New Intraday Margin Requirements" — https://www.finra.org/investors/insights/intraday-margin-requirements
- **S4** SEC Release No. 34-105226, File No. SR-FINRA-2025-017 (approval order, 2026-04-14) — https://www.sec.gov/files/rules/sro/finra/2026/34-105226.pdf
- **S5** FINRA weekly update, "FINRA Adopts New Intraday Margin Standards…" — https://www.finra.org/compliance-tools/weekly-archive/04152026 (listed in search; not fetched)
- **S6** Robinhood Help Center, "Day trading" — https://robinhood.com/us/en/support/articles/day-trading/
- **S7** Robinhood Help Center, "Settlement and buying power" — https://robinhood.com/us/en/support/articles/settlement-and-buying-power/
- **S8** FINRA, "Interpretations of Rule 4210 (valid from June 4, 2026)" — https://www.finra.org/rules-guidance/guidance/interps-4210-202606
- **S9** Alpaca blog, "FINRA Retires the PDT Rule: Introducing Alpaca's New Intraday Margin Framework" (2026-04-27, updated 2026-06-04) — https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/
- **S10** Alpaca Docs, "The Intraday Margin Rule" — https://docs.alpaca.markets/us/docs/the-intraday-margin-rule ; and "Intraday Margin Rule for Non-Leverage Margin Accounts" — https://docs.alpaca.markets/us/docs/intraday-margin-rule-for-non-leverage-margin-accounts
- **S11** Alpaca changelog, "Pattern day trading and DTBP fields and endpoints are deprecated" (2026-06-03) — https://docs.alpaca.markets/us/changelog/2026-06-03-pdt-651df23 ; Alpaca forum thread (staff, 2026-05-01) — https://forum.alpaca.markets/t/pdt-change-approved-when-will-changes-take-effect/18701
- **S12** Alpaca Docs, "Trading Account" — https://docs.alpaca.markets/us/docs/account-plans
- **S13** Investor.gov, "Trading in Cash Accounts" bulletin — https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated-9
- **S14** FINRA Investor Insights, "Frequent Intraday Trading: Understanding the Basics" (2026-06-04) — https://www.finra.org/investors/insights/frequent-intraday-trading
- **S15** Investor.gov, "New T+1 Settlement Cycle – What Investors Need To Know" — https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin
- **S16** Robinhood Help Center, "How much money do I need… to invest on margin?" — https://robinhood.com/us/en/support/articles/minimum-margin/
- **S17** Robinhood Help Center, "Onboarding an external agent" — https://robinhood.com/us/en/support/articles/agentic-trading-overview/
- **S18** Robinhood Help Center, "Trading with your agent" — https://robinhood.com/us/en/support/articles/trading-with-your-agent/
- **S19** Robinhood Help Center, "Robinhood investing accounts" — https://robinhood.com/us/en/support/articles/robinhood-accounts/
- **S19b** Robinhood Help Center, "Short selling" — https://robinhood.com/us/en/support/articles/short-selling/
- **S20** Alpaca Docs, "Paper Trading" — https://docs.alpaca.markets/us/docs/paper-trading
- **S21** Alpaca forum: "Paper account - trade denied due to pattern day trading protection" (historical) — https://forum.alpaca.markets/t/paper-account-trade-denied-due-to-pattern-day-trading-protection/12048 (listed in search; not fetched); "Getting new error after deprecated pattern_day_trader property" (2026-07-08/09) — https://forum.alpaca.markets/t/getting-new-error-after-deprecated-pattern-day-trader-property/19240
- **S22** 26 U.S.C. §1091 (Cornell LII) — https://www.law.cornell.edu/uscode/text/26/1091
- **S23** IRS Rev. Rul. 2008-5 — https://www.irs.gov/pub/irs-drop/rr-08-05.pdf
- **S24** IRS, Instructions for Form 1099-B (2026), box 1g — https://www.irs.gov/instructions/i1099b
- **S25** IRS Topic No. 429, Traders in securities — https://www.irs.gov/taxtopics/tc429
- **S26** SEC Press Release 2010-26, "SEC Approves Short Selling Restrictions" — https://www.sec.gov/newsroom/press-releases/2010-26-sec-approves-short-selling-restrictions ; Rule 201 FAQ — https://www.sec.gov/files/divisions/marketreg/rule201faq.htm (listed; not fetched)
- **S27** Investor.gov glossary, "Stock Market Circuit Breakers" — https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers
- **S28** SEC Release No. 34-106380, SR-NYSE-2026-43 (notice 2026-09-15) — https://www.sec.gov/files/rules/sro/nyse/2026/34-106380.pdf
- **S29** Robinhood Help Center, "Extended-hours trading" — https://robinhood.com/us/en/support/articles/extendedhours-trading/
- **S30** Robinhood Help Center, "Weekend investing" — https://robinhood.com/us/en/support/articles/investing-on-weekends/
- **S31** Alpaca Docs, "User Protection" — https://docs.alpaca.markets/docs/user-protection
- **S32** 26 CFR §1.1091-1 (Cornell LII) — https://www.law.cornell.edu/cfr/text/26/1.1091-1 (listed; not fetched)
- IRS Publication 550 (2025) — https://www.irs.gov/publications/p550 (fetched; wash-sale section did not render — not relied on)

---

*Not tax or legal advice. FlowForge's operator is responsible for confirming current broker policies, account type, and tax treatment with Robinhood/Alpaca and a qualified professional.*
