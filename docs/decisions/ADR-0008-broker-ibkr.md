# ADR-0008: Interactive Brokers for paper and live execution

- Status: Proposed (2026-10-19); supersedes ADR-0003
- Context: Operator chose IBKR over the Robinhood MCP. IBKR Pro is required for the API (paper too); full order types including brackets; 50 orders/sec cap; no fractional via API; headless Gateway is unsupported and a human must approve a login every Monday; IBC was archived 2026-09-01. See docs/research/08-nautilus-ibkr.md.
- Decision: Use Nautilus's IB adapter against IB Gateway in Docker (pinned digest). Paper stage runs on the IBKR paper account (replacing Alpaca paper). Protective stops are IBKR bracket children. Pre-market checklist fails closed if the Gateway isn't authenticated; the operator gets a phone prompt for the Monday login. Live stage 1 confirmation runs through FlowForge's approvals queue.
- Consequences: One adapter and one set of order semantics from paper to live. Weekly human touch is required; fully unattended operation isn't supported by IBKR.
