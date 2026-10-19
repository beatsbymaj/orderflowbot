# ADR-0007: Alpaca Algo Trader Plus for the tape, IBKR for depth; drop Massive

- Status: Proposed (2026-10-19); supersedes ADR-0002
- Context: Operator (individual, non-pro) asked for something cheaper than Massive ($199) or to drop it. Free IEX feeds see ~4.7% of volume, so a consolidated feed is required. IBKR's API tick-by-tick is consolidated but limited to ~5 streams. Alpaca Algo Trader Plus ($99) is full SIP, unlimited symbols, ns timestamps, with conditions and exchange codes and NBBO. IBKR Nasdaq TotalView ($16.50 non-pro) gives price-level depth on ≤3 symbols. See docs/research/07-market-data-alternatives.md.
- Decision: Live tape and history from Alpaca via a custom Nautilus DataClient; IBKR stock data (required for trading) plus optional TotalView on active names. Cheaper pilot option: IBKR data only for 2–5 symbols.
- Consequences: ~$120/mo instead of $199+. We build and test one data adapter. Open: Alpaca data-agreement terms for trading elsewhere; TRF code mapping; round-lot quote sizes.
