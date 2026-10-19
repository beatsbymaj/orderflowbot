# FlowForge — Phase 0 summary (revision 2, 2026-10-19)

Research is done; nothing has been built and no account, key or money was touched. This revision reflects your choices: **NautilusTrader**, **Interactive Brokers** instead of Robinhood, trading **as an individual**, and a cheaper data source. Details and dated sources: `docs/research/01–08`; decisions in `DECISIONS.md`; ADRs in `docs/decisions/` (0001–0003 superseded by 0006–0008).

## What research found
- **A paid data feed can't be dropped.** Free feeds carry only IEX (~4.7% of volume) while off-exchange prints are ~49%, so the tape would be mostly invisible. IBKR's own API ticks are complete but capped at ~2–5 symbols.
- **Cheaper than Massive and better: Alpaca Algo Trader Plus, $99/mo.** Full consolidated tape for unlimited symbols, venue and condition codes, NBBO, nanosecond timestamps (Massive's stream was ms), history since 2016. **Massive is dropped.**
- **Real depth is now possible on a few names:** IBKR Nasdaq TotalView, $16.50/mo, up to 3 symbols at once. Everything else stays trades + NBBO, with inferred features labelled as inferred.
- **NautilusTrader fits now** because it has an IB adapter and runs the same strategy code in backtest and live. But v1 is end-of-line and **v2 is still a release candidate (2.0.0rc7, needs Python 3.13)**, with its IB adapter mid-rewrite and open bugs around duplicate orders on reconciliation.
- **IBKR needs you once a week.** Headless Gateway isn't supported, a login approval is required every Monday, and the login-automation tool IBC was archived on 2026-09-01. Fractional shares aren't available through the API.
- **Shorting becomes possible** on an IBKR margin account (≥$2,000). I still propose starting long-only.
- **PDT:** replaced by intraday margin rules on 2026-06-04, but brokers can keep the old rule per account until October 2027, so we confirm your IBKR account's status.
- **Jev** is unchanged: pin `jev-1.13.0`, 750 ms deadline, no answer means no trade.

## Proposed stack
NautilusTrader 2.0.0rc7 (pinned) on Python 3.13 · IB adapter + IB Gateway in Docker · custom Alpaca SIP data client · IBKR TotalView on active names · Jev gate as a Nautilus actor · FlowForge risk guard layered on Nautilus's risk engine plus an independent watchdog · Redis Streams for node output · Postgres + TimescaleDB · FastAPI + React. Paper on **IBKR paper**. Data cost about **$120/mo** (vs $199+ for Massive).

## Blockers
1. **No real money on a release candidate.** Live waits for Nautilus 2.0.0 final, the IB adapter rewrite merged, and our own paper soak tests passing. Building, backtesting and paper can proceed now.
2. **Weekly human login at IBKR.** Fully unattended running isn't possible; the system will stop trading and ping your phone if the Monday login isn't done.
3. **Alpaca data terms.** Whether Alpaca's data agreement allows trading at another broker is unverified; I'd ask Alpaca before subscribing.

## What I need from you
1. Go-ahead for Phase 1.
2. Approve the data plan: **Alpaca $99 + IBKR TotalView (recommended)**, or IBKR-only data (~$14.50, 2–5 symbols) for the first phases.
3. Long-only at launch, shorts added after paper results? (recommended: yes)
4. The QuantForge repo path.
5. Your risk limit values, or confirm the spec defaults.
6. At each step: IBKR Pro account (paper first), Alpaca account for data, TypeSafe key. Keys go in `.env`, never in chat.

Nothing goes live without your explicit confirmation at each stage.
