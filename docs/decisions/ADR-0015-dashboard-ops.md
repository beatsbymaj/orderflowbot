# ADR-0015: Phase 6 dashboard, phone alerts and daily reports

- Status: Proposed (2026-10-26), for the operator walkthrough (the Phase 6 exit gate)
- Context: ROADMAP Phase 6 asks for live, tape, signals, approvals, journal, proposals, health
  and settings screens, ntfy/Telegram alerts, pre-market and end-of-day automation, and
  runbooks. CLAUDE.md sketches node events in Redis Streams, a Postgres/TimescaleDB journal
  and a React dashboard. The operator runs everything on one Windows PC with Docker and no
  Node toolchain, and is one person.
- Decisions:
  1. **Files are the source of truth for Phase 6.** The API (`flowforge.api`, FastAPI 0.162.0,
     uvicorn 0.54.0, both pinned) reads what the node and its helpers already write: the
     JSON-lines journals (CLAUDE.md rule 6), the heartbeat, the risk state, a tape snapshot
     and the reports. Redis Streams and the TimescaleDB journal are deferred until paper
     trading needs queries across weeks; the review is part of the Phase 7 exit. Nothing is
     lost meanwhile: the journals are the record either way.
  2. **No-build dashboard instead of React.** One HTML page, one CSS file, one script, plus
     the Taste Skill `charts.js`, served by the API. Nothing to install or compile on the PC.
     Design: Taste Skill app sheet "data-terminal" (dark, seed 638214429): figures in
     JetBrains Mono, text in Public Sans, every selection a 1px accent line, colour only on
     data and status. The product's own element is the **risk budget**: each limit from
     risk_limits.yaml drawn as a track as long as the limit.
  3. **Read only, plus one button.** The only write is `POST /api/kill`, which turns the kill
     switch ON (the same file flag as riskctl and the watchdog) and journals who and why.
     There is no endpoint to turn it off, clear a halt or change a limit: those stay in
     `riskctl` (confirmation required) and in git (CLAUDE.md rule 2). The request needs the
     header `X-FlowForge-Action: engage-kill`, a same-origin `Origin` if one is sent, and the
     port is published on 127.0.0.1 only. Journal fields that look like secrets are masked
     before they leave the API; alert channels show only "set / not set".
  4. **Node additions, none in the risk path:** an `equity_mark` journal record once a minute
     (the day's equity curve), equity and day-start equity in the heartbeat, a tape snapshot
     file written every 5 s by OrderFlowStrategy (`strategies/tape.py`), and a watchdog
     "alive" file (`watchdog.json`) for the Health screen.
  5. **Trades are rebuilt from order events** (`ops/ledger.py`): fills by order id, owner by
     the order_submit / stop_submit / exit_submit records, R from the RiskGuard plan's stop.
     A fill no trade owns is shown as unreconciled on Health and in the end-of-day report.
  6. **Approvals and proposals are honest empty screens for now.** Confirmation mode is
     Phase 8 (live stage 1); in paper nothing waits for approval. Proposals are read from
     `reports/proposals/*.json`; the learning process that writes them comes after Phase 7.
  7. **Phone alerts** (`ops/alerts.py`): a separate service follows the journals and sends
     node/watchdog alerts, the kill switch, watchdog flattens, order rejects and the daily
     reports to ntfy and/or Telegram. It is never in the order path. Same text within 60 s is
     dropped, at most 20 a minute, failures journaled by HTTP status or error type only
     (a Telegram URL contains the bot token).
  8. **Daily jobs** (`ops/daily.py`): pre-market check at 09:00 ET and end-of-day report
     15 minutes after the close (13:15 on early closes), on trading days only, each once.
     Read only. Markdown + JSON in reports/daily, an ops journal record, a phone summary.
  9. **Demo mode** (`python -m flowforge.api --demo DIR`) writes one invented day into its
     own folder for the walkthrough, with a banner on every screen. It never writes into
     data/journal.
- Consequences: The walkthrough can happen before the node trades on paper (demo mode), and
  again on real paper data in Phase 7. Unverified details (docs/research/15) are designed
  around: messages are capped at 3,500 characters and a failed alert never affects trading.
