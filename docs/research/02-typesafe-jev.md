# 02 — TypeSafe Jev ("System One" decision model)

**Researched:** 2026-10-19 by the Research Scout
**Scope:** Whether TypeSafe's Jev can serve as FlowForge's fast decision/triage layer (a "JEV ops gate").
**Method:** I checked official TypeSafe sources (docs.typesafe.ai, typesafe.ai, the typesafe-ai GitHub org) and the official registries (PyPI, npm). For the integrations, I used each framework's own official docs. No account was created and no API key was used. Every source in this file was accessed on 2026-10-19.

> Naming: TypeSafe writes the model as **"Jev"** (not "JEV") and the yes/no primitive as **"Noul"**. The product category is **"System One Models"**, and the HTTP endpoint is `/v1/systemone`. So the user's description of Jev as TypeSafe's System One model is correct.

---

## 1. Findings table

| # | Claim | Status | Source |
|---|---|---|---|
| 1 | The API endpoint is `POST https://api.typesafe.ai/v1/systemone`, with `Authorization: Bearer <key>` and a JSON body | VERIFIED | [S1] |
| 2 | The request body is `state` (string/object/array) + `model` + `questions` (a map of IDs to questions) | VERIFIED | [S1] |
| 3 | Model IDs: `jev-1.13.0` (current) and `jev-1.14.0` (available from 2026-10-14). Aliases: `jev-latest` → 1.13.0 and `jev-preview` → 1.14.0 | VERIFIED | [S2] |
| 4 | A version can be pinned by sending the versioned ID. The response `model` field reports the version that answered | VERIFIED | [S2], [S1] |
| 5 | A model deprecation/sunset policy is published | UNVERIFIED (not stated) | [S2] |
| 6 | `noul` returns P(yes) in [0,1] and has no `confidence` field | VERIFIED | [S1], [S3] |
| 7 | `choice` returns `choice`, `probabilities` (summing to 1) and `confidence`. Max 255 options | VERIFIED | [S1] |
| 8 | `score` returns `score` (probability-weighted, can fall between levels), `legend`, `probabilities` and `confidence`. 2–10 levels | VERIFIED | [S1] |
| 9 | Price is $0.042 per M input tokens ($42 per B). Output tokens are free | VERIFIED | [S2], [S10] |
| 10 | Context limit is 64k tokens per request and 32k per (state + one question). An opt-in header raises both to 262k | VERIFIED | [S2] |
| 11 | Body limit is 32 MiB, nesting limit is 64, and numbers must be finite | VERIFIED | [S1] |
| 12 | Rate limits are set per organization (tokens/s and requests/s) and shown in the console. **No numbers are published** | VERIFIED (no numbers) / numbers UNVERIFIED | [S2] |
| 13 | Latency SLA or p50/p99 figures | **UNVERIFIED** (the docs give none) | [S2] |
| 14 | Homepage marketing example of 0.114 s per run and a "193.6x faster" claim | VERIFIED as a vendor claim only, not a benchmark | [S10] |
| 15 | Vendor confidence formulas and example thresholds (0.5 floor, 0.9 for high-stakes actions). The vendor says to tune thresholds on your own data | VERIFIED | [S4] |
| 16 | Probabilities are "calibrated" | **UNVERIFIED / CONFLICTING**: the homepage says "calibrated probabilities", but the docs never claim calibration and say Score levels are "weak in numerical calibration" | [S10], [S4], [S5] |
| 17 | Python SDK `typesafe-sdk` v0.7.5 (PyPI, 2026-10-14), Python >=3.10, MIT, repo `typesafe-ai/typesafe-sdk-python` | VERIFIED | [S6], [S7], [S13] |
| 18 | JS/TS SDK `@typesafe-ai/sdk` v0.7.0 (npm, 2026-10-14), Node.js >=22, repo `typesafe-ai/typesafe-sdk-js` | VERIFIED | [S8], [S9], [S14] |
| 19 | Python SDK defaults: 30 s total timeout budget and 2 retries with backoff (0.5 s initial, 5 s max). It retries 408, 429 and 5xx | VERIFIED | [S15] |
| 20 | LangChain integration: `langchain-typesafe` (PyPI 0.0.1a3, alpha) and `@langchain/typesafe` (npm 0.0.2) | VERIFIED | [S16], [S17], [S13], [S14] |
| 21 | LiteLLM integration: proxy pass-through `/typesafe/v1/systemone` plus a `typesafe/` model type. No streaming | VERIFIED | [S18] |
| 22 | Pipecat integration: `pipecat.classifiers.jev` (deprecated in 1.13.0) moved to `pipecat.classifiers.typesafe.jev.classifier`. pipecat-ai 1.13.0 was released 2026-10-19 | VERIFIED (module exists; constructor params not reviewed) | [S19], [S13] |
| 23 | Customer data is not used for training | VERIFIED | [S11], [S12] |
| 24 | A specific retention period for API inputs | **UNVERIFIED** (none stated; the MCA lets TypeSafe keep derived Telemetry "in perpetuity") | [S11], [S12], [S20] |
| 25 | Zero data retention is available for enterprise customers through sales | VERIFIED | [S21] |
| 26 | Hosted in the US | VERIFIED (per the Privacy Policy) | [S11] |
| 27 | SLA/uptime commitment | None exists; the MCA disclaims it | [S20] |
| 28 | Liability cap is the greater of 12 months' fees or $50. Lost profits are excluded. "May produce inaccurate or erroneous output" | VERIFIED | [S20] |
| 29 | Restrictions in the Acceptable Use Policy on financial or automated trading use | **UNVERIFIED** (AUP not reviewed) | — |
| 30 | An **official** open-source Jev trading bot demo | **NOT FOUND** | [S22], [S23], [S24] |
| 31 | A third-party Monad market-making bot (Jarrod Watts) and a "jev-trader" repo | **UNVERIFIED** (third-party promotional article; no repo link) | [S25] |

---

## 2. Details

### 2.1 API and base URL
- `POST https://api.typesafe.ai/v1/systemone` [S1]. Optional headers are `Content-Type: application/json` and `Content-Encoding` (gzip, x-gzip or zstd; one layer only).
- Request fields:
  - `state`: the content to evaluate. It can be a string, a JSON object or an array.
  - `model`: the model ID.
  - `questions`: a map of question IDs you choose to Question objects.
  - Every question has a `type` and `instructions`. `criteria` depends on the type.
- Response fields: `model` (the versioned ID), `answers` (keyed by question ID) and `usage.input_tokens` / `usage.output_tokens` [S1].
- Error codes [S1]:
  - 400 (`max_tokens_exceeded` = context overflow)
  - 401
  - 413 (body over 32 MiB)
  - 415
  - 422
  - 429 (rate limit)
  - 529 (overloaded)
  - TypeSafe advises exponential backoff for 429 and 529.
- No rate-limit response headers are documented. The SDKs honor `retry-after` when it is present [S2].
- Experimental features exist but are out of scope here: image input and a `box` primitive that only works on `jev-preview` [S9].

### 2.2 Models and versioning
- `jev-latest` → `jev-1.13.0`, described as stable and official. It is the SDK default [S2].
- `jev-preview` → `jev-1.14.0`, available since 2026-10-14. It tracks any newer release, official or not [S2].
- The docs say aliases move when a release ships. They recommend sending the **versioned ID** if confidence thresholds were tuned against a specific version [S2].
- **Pinning is supported.** No deprecation window is published.

### 2.3 Primitives

| Primitive | Purpose | Request `criteria` | Response |
|---|---|---|---|
| `noul` | Yes/no | Optional `{true: "...", false: "..."}` | `noul`: P(yes) in [0,1]. **No `confidence` field.** |
| `choice` | One of N unordered options (max 255) | Required map `option → description or null` | `choice`, `probabilities` (sums to 1), `confidence` |
| `score` | Position on ordered levels (2–10) | Required ordered array of level descriptions | `score` (float), `legend`, `probabilities`, `confidence` |

Shared behavior [S3]:
- Answers are always constrained to the options or levels you supply.
- Questions in one request are evaluated independently and in parallel. There is no cap on question count beyond the context limits.
- **All three primitives return probabilities.**

### 2.4 Latency, pricing, limits
- **Pricing:** $0.042 per M input tokens. Output tokens are free [S2].
  - Example: a 2k-token feature snapshot with 5 questions (~3k tokens) costs about $0.000126 per call.
  - At 10k calls per day, that is about $1.26 per day. This is an estimate, not a vendor figure.
- **Context limits:**
  - 64k tokens per request and 32k tokens for state plus any single question.
  - The `TypeSafe-Danger-Zone-Long-Context: true` header raises both limits to 262k. TypeSafe says long context is slower and less accurate [S2].
- **Rate limits:** set per organization and shown in the console. No figures are public [S2].
- **Latency:** the docs publish **no latency figures** [S2].
  - The homepage shows one example run at 0.114 s against 8.566 s for an LLM, plus a headline "193.6x faster" claim [S10].
  - These are vendor marketing numbers. FlowForge must measure latency itself.

### 2.5 Confidence and calibration guidance [S4]
- Choice confidence is `(p_max − 1/n)/(1 − 1/n)`.
- Score confidence is `max(0, 1 − weighted distance / MAD_unif)`.
- Noul has no confidence field. The docs suggest `|2p − 1|` as a comparable number.
- Escalation pattern:
  - Act when confidence is high.
  - Confirm or flag when it is medium.
  - Do not act when it is low.
- Example cutoffs: below 0.5 means unsure and should go to a human. Above 0.9 is acceptable for a high-stakes action, after confirmation.
- Vendor advice: "Start with conservative thresholds, test with your own data."
- Known weaknesses in Jev 1.13 [S5]:
  - "Jev is not a calculator." Counting is unreliable.
  - Dates are treated as text, so ordering and window checks are unreliable.
  - Score levels are "weak in numerical calibration".
  - It leans toward the first option in a Choice.
  - Large or irrelevant state reduces accuracy.
  - It does not treat state as adversarial.
- **These weaknesses are directly relevant to trading feature snapshots**, which are mostly numbers and timestamps.

### 2.6 SDKs and integrations

| Package | Registry | Latest version | Release date | Notes |
|---|---|---|---|---|
| `typesafe-sdk` (Python) | PyPI | 0.7.5 | 2026-10-14 | Python >=3.10. Method `client.system_one(...)`. Sync and async clients. `response_model` takes a pydantic model (0.7.0). zstd compression on by default (0.7.5) [S7], [S13] |
| `@typesafe-ai/sdk` (TS/JS) | npm | 0.7.0 | 2026-10-14 | Node >=22. First public release was 0.5.7 on 2026-09-12 [S9], [S14] |
| `langchain-typesafe` | PyPI | 0.0.1a3 | 2026-09-20 | Alpha. `TypeSafeClassifier` Runnable. Experimental middleware [S16] |
| `@langchain/typesafe` | npm | 0.0.2 | 2026-10-01 | Default timeout 30000 ms, maxRetries 2 [S17] |
| LiteLLM | proxy feature | n/a | n/a | Pass-through `/typesafe/*` or `typesafe/` model in `model_list`. No streaming. Cost logged under the versioned model name [S18] |
| Pipecat | `pipecat-ai` 1.13.0 | 1.13.0 | 2026-10-19 | `pipecat.classifiers.typesafe.jev.classifier`. The old path is deprecated [S19] |

- Python SDK retry defaults [S15]:
  - `max_retries=2`, `backoff_initial=0.5s`, `backoff_max=5.0s`, jitter 0.25.
  - It honors `retry-after` and retries 408, 429 and 5xx.
  - **`timeout=30.0s` is a total budget that includes retries.** That is far too long for a trading gate, so FlowForge must override it.
- The SDKs are pre-1.0 and have had breaking changes in the last month: msgspec→pydantic in Python 0.7.0, and Score criteria changed in JS 0.6.0 [S7], [S9]. **Pin exact versions.**

### 2.7 Data retention and privacy
- **No training on customer data.** The Privacy Policy states this, and so does MCA §4.1 (no training without consent) [S11], [S20].
- **Retention:**
  - No fixed period is stated. Data is kept "as long as reasonably necessary" [S11], [S12].
  - MCA §4.1 lets TypeSafe keep Telemetry derived from Customer Data **in perpetuity** [S20].
  - MCA §10.3 says TypeSafe is under no obligation to retain Customer Data.
- **ZDR** is available on enterprise plans through sales@typesafe.ai [S21].
- **Hosting:** the US [S11]. Sub-processors are listed at trust.typesafe.ai/subprocessors (not reviewed) [S12].
- **SOC 2:** not mentioned in the documents reviewed. UNVERIFIED.
- **SLA:** none. There is an accuracy disclaimer, and liability is capped at the greater of 12 months' fees or $50 [S20].
- **Implication:** feature snapshots must not contain account IDs, positions, P&L or strategy identifiers unless ZDR is in place. Send anonymized or derived features only.

### 2.8 Trading bot demo
- **Official TypeSafe sources: no trading bot demo exists.**
  - The docs Demos page lists only a "Smart home assistant demo" [S22].
  - The typesafe-ai GitHub org has 10 public repos and none are trading-related. "Overwatch" is a cloud/ML-workload monitor [S23], [S24].
- **Third-party report (UNVERIFIED):** a promotional article describes a Monad (Kuru MON-USDC) market-making bot attributed to Jarrod Watts, plus a "jev-trader" branch. It links an X post, not a repository [S25]. The architecture it describes:
  - Deterministic code computes features and Avellaneda-Stoikov pricing.
  - One Jev call runs a batch of questions.
  - A policy engine maps the answers to actions.
  - A hard risk gate can veto any order.
  - Post-only quotes are placed every ~300 ms block.
- This is crypto market-making, not US equities. No source code was verified.

---

## 3. Design implications for a "JEV ops gate" service

1. **Default NO TRADE.** Any error, timeout, 4xx/5xx, 429, 529, schema mismatch, or below-threshold answer resolves to `NO_TRADE`. Jev may only veto or permit a trade that the deterministic risk layer already approved. It can never originate or size one. This matches the vendor's own escalation pattern [S4] and the MCA's "independently evaluate output" clause [S20].
2. **Hard timeouts.**
   - Override the SDK's 30 s total budget [S15]. Proposed defaults: ~250–500 ms per call and `max_retries=0` on the hot path, or at most 1 retry if the budget allows.
   - Values must come from measured p99 latency in our own region. Vendor latency is UNVERIFIED.
   - Treat 529 and 429 as "gate closed" rather than retrying past the decision deadline.
   - Add a circuit breaker after N consecutive failures.
3. **Pin the model.** Send `jev-1.13.0`, never `jev-latest` or `jev-preview`.
   - Assert that `response.model` matches the pinned ID, and fail closed if it does not.
   - Re-tune thresholds before any version bump (`jev-1.14.0` already exists) [S2].
4. **Pin the SDKs** to exact versions (`typesafe-sdk==0.7.5`, `@typesafe-ai/sdk@0.7.0`). Both are pre-1.0 and churn often.
5. **Keep numbers out of Jev's job.**
   - Jev 1.13 is weak at arithmetic, counting, dates and Score calibration [S5]. Do all numeric thresholds, comparisons and time-window logic in code.
   - Send Jev pre-digested, semantic state, for example `"spread_regime": "wide"` rather than raw floats.
   - Keep state small, since irrelevant fields reduce accuracy.
   - Randomize or rotate Choice option order to detect first-option bias.
6. **Prefer Noul or Choice over Score for gating.** Score magnitudes are weakly calibrated [S5].
   - Use Noul with `|2p−1|` as the confidence measure, or Choice with an explicit `"none_of_the_above"` / `"uncertain"` option [S3].
7. **Track calibration with Brier scores.**
   - Log every call: request hash, pinned model, the `model` returned, and the full `probabilities` / `noul` values.
   - Join each call to the realized outcome label, for example "was the trade-blocking condition actually present?" or "did the triaged event matter?".
   - Compute the Brier score `mean((p − y)²)` per question ID, per model version, and per rolling window. Use the multiclass Brier for Choice.
   - Add reliability diagrams and ECE.
   - Alert and fall back to NO TRADE if the Brier score degrades past a baseline.
   - Do not trust the homepage's "calibrated" claim [S10] until our own data supports it.
8. **Run in shadow mode first.** Log Jev's decisions without acting on them until enough labeled outcomes exist to set thresholds. This follows the vendor's "test with your own data" advice [S4].
9. **Data minimization.** Strip identifiers, account data and P&L from the state. Pursue ZDR before production [S21]. Retention is otherwise open-ended [S11], [S20].
10. **Cost is negligible, but rate limits are unknown.** Fetch org limits from the console. Budget calls per tick and fail closed on 429.
11. **Prompt-injection hygiene.** Jev does not treat state as adversarial [S5]. If news or social text goes into the state, isolate it in separate questions and never let it override risk rules.

---

## 4. Open questions
1. What are our org's actual rate limits (RPS and TPS)? They are only visible in the console after signup.
2. What are the real p50/p99 latencies from our deployment region? No figures are published.
3. Is there a deprecation window for pinned versions such as `jev-1.13.0`?
4. Does the Acceptable Use Policy restrict automated financial or trading decisions? Not reviewed; it must be read before any production use.
5. What are the cost and terms of ZDR? Is there a SOC 2 report or Trust Center attestation (trust.typesafe.ai)?
6. What are the exact retention periods for request logs and Telemetry?
7. How does `jev-1.14.0` differ from 1.13 on the weaknesses that matter to us (numbers, dates)? There is no 1.14 jaggedness page in the index.
8. Is the third-party Monad "jev-trader" bot's source code public anywhere?
9. What are the Pipecat classifier constructor defaults? Low priority, since Pipecat is a voice framework and not relevant to FlowForge.

---

## Sources (all accessed 2026-10-19)
- [S1] TypeSafe API reference: https://docs.typesafe.ai/api
- [S2] TypeSafe Models (IDs, aliases, pricing, limits, rate limits): https://docs.typesafe.ai/models
- [S3] TypeSafe Primitives overview: https://docs.typesafe.ai/primitives
- [S4] TypeSafe Confidence: https://docs.typesafe.ai/confidence
- [S5] Jev 1.13 jaggedness (known limitations): https://docs.typesafe.ai/model-jaggedness/jev-1.13.md
- [S6] TypeSafe Python SDK docs: https://docs.typesafe.ai/sdk/python.md
- [S7] Python SDK changelog: https://docs.typesafe.ai/sdk/python/changelog.md
- [S8] TypeSafe JavaScript SDK docs: https://docs.typesafe.ai/sdk/javascript.md
- [S9] JavaScript SDK changelog: https://docs.typesafe.ai/sdk/javascript/changelog.md
- [S10] TypeSafe homepage: https://typesafe.ai
- [S11] TypeSafe Privacy Policy (last updated 2025-11-19): https://typesafe.ai/legal/privacy-policy
- [S12] TypeSafe Data Processing Agreement: https://typesafe.ai/legal/data-processing
- [S13] PyPI JSON API: https://pypi.org/pypi/typesafe-sdk/json, https://pypi.org/pypi/langchain-typesafe/json, https://pypi.org/pypi/pipecat-ai/json
- [S14] npm registry: https://registry.npmjs.org/@typesafe-ai/sdk, https://registry.npmjs.org/@langchain/typesafe
- [S15] Python SDK Retries: https://docs.typesafe.ai/sdk/python/api/retries.md
- [S16] LangChain Python TypeSafe provider: https://docs.langchain.com/oss/python/integrations/providers/typesafe.md
- [S17] LangChain JS TypeSafe provider: https://docs.langchain.com/oss/javascript/integrations/providers/typesafe.md
- [S18] LiteLLM TypeSafe pass-through: https://docs.litellm.ai/docs/pass_through/typesafe.md
- [S19] Pipecat reference: https://reference-server.pipecat.ai/en/latest/api/pipecat.classifiers.jev.html and https://reference-server.pipecat.ai/en/latest/api/pipecat.classifiers.jev.classifier.html
- [S20] TypeSafe Master Customer Agreement: https://typesafe.ai/legal/mca
- [S21] TypeSafe Legal (docs): https://docs.typesafe.ai/legal.md
- [S22] TypeSafe Demos: https://docs.typesafe.ai/demos.md
- [S23] TypeSafe GitHub org: https://github.com/typesafe-ai
- [S24] typesafe-ai/Overwatch: https://github.com/typesafe-ai/Overwatch
- [S25] Third-party article, not official: https://youmind.com/ar/landing/x-viral-articles/jev-hft-trading-system
- Supplementary (third-party, used only for discovery): https://www.promptfoo.dev/docs/providers/typesafe/ and https://docs.typesafe.ai/llms.txt
