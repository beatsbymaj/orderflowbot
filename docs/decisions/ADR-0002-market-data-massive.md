# ADR-0002: Massive Stocks Advanced, trades + NBBO only, no depth

- Status: Superseded by ADR-0007 (2026-10-19, operator asked for a cheaper source)
- Context: Stocks Advanced ($199/mo) is the only individual Massive plan with real-time trades and NBBO over WebSocket. It is non-professional only, allows one WebSocket per asset class, delivers ms timestamps (REST is ns), and its sequence numbers can't detect gaps. Massive offers no depth of book. See docs/research/03-massive-market-data.md.
- Decision: One ingest process owns the socket and fans out over Redis Streams. Reconnects trigger REST backfill and a marked gap. Replay truncates timestamps to ms so live and replay features are identical. No depth-based features; inferred features are labelled as inferred.
- Consequences: Order flow is reconstructed from trades and NBBO, so aggressor side is probabilistic (unclassified % tracked). If FlowForge is ever run through an LLC, the data plan changes to Business plus exchange add-ons.
