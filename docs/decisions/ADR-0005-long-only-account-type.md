# ADR-0005: Long-only launch (revised for IBKR)

- Status: Proposed (2026-10-19, revision 2); needs operator confirmation
- Context: The first version assumed the Robinhood agentic account, which has no margin borrowing. With IBKR, a margin account (≥$2,000) allows shorting, subject to borrow availability and Reg SHO Rule 201. See docs/research/05 and 08.
- Decision: Launch long-only anyway. Short-side playbook signals act as exit and no-entry signals until long-side paper results meet the gates; enabling shorts is a separate operator decision recorded here. Use a margin account for flexibility, but the risk engine enforces gross exposure ≤ equity (no leverage).
- Consequences: Simpler first slices and smaller tail risk. Shorts can be added later without changing the adapter.
