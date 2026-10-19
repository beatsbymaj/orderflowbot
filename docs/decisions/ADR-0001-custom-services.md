# ADR-0001: Custom asyncio services, patterns borrowed from NautilusTrader

- Status: Superseded by ADR-0006 (2026-10-19, operator chose NautilusTrader)
- Context: The mission specifies eight separate services on Redis Streams with a BrokerAdapter for Alpaca paper, Robinhood MCP and replay. NautilusTrader is the strongest open-source design reference, but it has no Alpaca or Robinhood adapter, runs one node per process, constrains embedding in our own asyncio/FastAPI apps, and is mid-migration to a breaking Rust v2. See docs/research/04-github-survey.md.
- Decision: Build our own Python 3.12 asyncio services. Copy Nautilus's patterns: Postgres as the record of orders/fills, single decision task per service, mandatory pre-trade risk step, explicit order state machine, restart-plus-reconcile recovery, "missing report = unknown". Port hftbacktest's latency and queue models into SimAdapter.
- Consequences: More code to own, especially the order state machine and backtester. In exchange we keep the specified service split and adapters, and no framework upgrade can change our risk path.
