# 06 — Existing assets

Reviewed 2026-10-19.

## orderflow scanner (`/mnt/project-files/orderflow/scanner/`)

A batch pandas/numpy scanner built earlier in this project for ES/NQ futures CSVs. It detects absorption, iceberg absorption, book imbalance, imbalance breakout, delta divergence and spoofing, with thresholds expressed as multiples of rolling medians (`ofscan/signals.py`, `Config`).

| Part | Reuse in FlowForge? | Why |
|---|---|---|
| Detector logic: absorption, delta divergence, stacked diagonal imbalance (`signals.py` `detect_absorption`, `detect_delta_divergence`, `stacked_imbalances`) | **Port the logic**, not the code | The feature engine must be incremental and produce identical output live and in replay. The scanner recomputes whole DataFrames in batch, so it can't satisfy the parity requirement as-is. |
| Relative thresholds (multiples of rolling medians) | **Reuse the idea** | Matches the spec's "relative to each symbol's own distribution". |
| Planted-event synthetic data + tests (`make_sample_data.py`, `tests/`) | **Reuse the pattern** | Good template for feature-engine golden tests on synthetic US-equity ticks. |
| Book-based detectors (iceberg vs shown size, book imbalance, spoof) | **Do not reuse** | Massive has no depth-of-book for US stocks (see 03). These would be faked depth. Icebergs can only be inferred from repeated prints at the NBBO. |
| Aggressor side taken from the input file | **Replace** | Equities ticks have no aggressor flag; FlowForge classifies with quote rule → Lee-Ready → tick rule. |
| HTML report | Reference only | Dashboard will be React. |

## QuantForge (Alpaca paper; FastAPI, PostgreSQL, Kelly sizing, drawdown kill switches)

**Not reviewed: path not yet provided.** Open question in DECISIONS.md. Likely reuse candidates to check once available: the Alpaca client and order idempotency, the risk engine's drawdown kill switches and their tests, the Postgres schema/migrations, and FastAPI health endpoints. Kelly sizing would be subordinate to the spec's fixed 0.5%-risk-per-trade cap (Kelly may only size *down*).

## Revision 2026-10-19 (IBKR + Alpaca data)
With IBKR Nasdaq TotalView (≤3 symbols at once, price-level, Nasdaq only), the scanner's book-based detectors (book imbalance, spoof, iceberg vs shown size) become portable **for those symbols only**, labelled as Nasdaq-book-only. All other symbols still have no depth.
