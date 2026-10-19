# ADR-0004: Jev pinned to jev-1.13.0, short deadline, NO TRADE on failure

- Status: Proposed (2026-10-19)
- Context: Jev's API (`/v1/systemone`) supports version pinning; `jev-latest` is 1.13.0 and `jev-preview` is 1.14.0. The Python SDK defaults to 30 s with retries. TypeSafe documents weakness at arithmetic, counting, dates and Score calibration. No published rate limits or SLA. See docs/research/02-typesafe-jev.md.
- Decision: Pin `jev-1.13.0` in config. Trade-gate calls get a hard 750 ms deadline (proposed) with no retries; timeout or error means NO TRADE. Numeric logic is computed in code and sent as labelled facts. Noul/Choice gate; Score only grades after our own calibration. Every call logged; Brier and reliability per question; a gate is disabled on drift and kept only if it beats the rules-only baseline out of sample.
- Consequences: Jev can only remove trades, never add risk. Version upgrades are config commits validated in replay.
