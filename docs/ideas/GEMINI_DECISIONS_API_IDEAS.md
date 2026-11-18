# Ideas: Gemini Decisions API for Closed-Set Judgements

- **Status**: Ideas only. They have no design for implementation. Do not implement from this document. A design starts only after an owner decision.
- **Date**: 2026-11-18
- **Basis**: commit `1632885b` (the production build has the same commit, schema v36). A graph bundle and an account-scope support bundle of 2026-11-18 from the owner. The Google blog post of 2026-11-11 and the AI Studio documentation of the Decisions API.
- **Related**: [`JEV_SHADOW_EVALUATION_DESIGN.md`](../design/JEV_SHADOW_EVALUATION_DESIGN.md), [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md), [`PROVIDERS.md`](../implementation/PROVIDERS.md), [`EXPANSION.md`](../implementation/EXPANSION.md)

---

## 1. Summary

The Gemini Decisions API can replace or support some LLM judgements of Claire. The order from the production data:

| Order | Target | Assessment |
| :--- | :--- | :--- |
| 1 | Relation judgement `judge_relationship` | Largest opportunity. No second engine exists. p50 is 15.7 s for each call. The shape (`noul` and `choice`) fits. The code does not keep the reason text, thus nothing is lost. The relation judgement has no decision record yet; that record must come first. |
| 2 | Entity resolution `judge_same_entity` | Best fit of shape, but Jev already removes the latency (p50 209 ms). Gemini is an alternative engine to compare with Jev. For each engine, only 2 positive pairs exist for an accuracy evaluation. |
| 3 | Watch classification `classify_watch` | p50 16.9 s for each new ingest. `noul` and `choice` can replace it. A wrong answer has a small effect. |
| — | Expansion gate `judge_gate`, `select_followups` | The shape fits, but the bundles have no data for them. Assessment is on hold. |
| — | Generation, theme judgement, content guard | Not applicable (Section 6). |

---

## 2. The API

The performance and price values are vendor claims. They are not verified.

- An endpoint for closed-set decisions. Model `gemini-decision-1`. The model scores the options in one forward pass and returns probabilities. It does not generate text.
- `POST https://generativelanguage.googleapis.com/v1beta/decisions`, header `x-goog-api-key`. Python: `client.decisions.create(...)`, `google-genai` 3.2 or later (the project has 3.3 or later).
- Request: `model`; `input` (one shared input: text, image, audio, video, PDF); `questions` (a map of 1 to 1,000 questions).
- Question types:

| Type | Use | Answer |
| :--- | :--- | :--- |
| `noul` | yes or no | `noul` (0.01 to 0.99), `confidence` |
| `choice` | one of 2 to 255 options | `choice`, `probabilities` for each option, `confidence` |
| `score` | ordered levels (2 to 255, 2 to 10 recommended) | weighted `score`, `probabilities` for each level, `confidence` |

- Context limit: 262,144 tokens for the shared input and all questions together. A limit for each question is not stated.
- **No reason text.** Output tokens are always 0.
- Latency (claim): 150 to 250 ms end to end, almost the same from 1 to 100 questions.
- Price: USD 0.10 for each 1M input tokens, output free. The shared input is billed one time for each request.
- Rate limits: free tier 200 RPM, 50,000 TPM, 10,000 RPD. Tier 1: 1,500 RPM, 1M TPM, 50,000 RPD.
- Not supported: Batch API, context caching, Flex and Priority inference.
- Errors: HTTP 400 for safety blocks (`safety`, `prohibited_content`, and others), 429 `RESOURCE_EXHAUSTED` for limits.
- Not stated: determinism, version pinning policy, data region.

### 2.1 Relation to Jev

The request format is almost the same as the Jev format. Thus the change is one more engine adapter, not a new function.

| Item | Jev (`src/claire/extract/jev.py`) | Gemini Decisions |
| :--- | :--- | :--- |
| Endpoint | `POST {base_url}/systemone` | `POST /v1beta/decisions` |
| Authentication | `Authorization: Bearer` | `x-goog-api-key` |
| Shared input | `state` | `input` |
| Yes or no criteria | `criteria.true`, `criteria.false` | `criteria.true_description`, `false_description` |
| Answer | `answers.<key>.noul` | `answers.<key>.noul`, `confidence` |
| Question types | `noul` (in the current client) | `noul`, `choice`, `score` |

---

## 3. Production Measurements

Production settings in the bundle: provider `antigravity` (administrator host CLI), model `gemini-3.8-flash` and later `gemini-4-hydrogen-medium`. A Gemini API key is set. Jev is on, model `jev-1.14.0`, role `shadow` at bundle time; the filter mode was used from 2026-11-16 to 2026-11-18 (331 `jev_filtered`).

### 3.1 Judge calls (graph bundle, 2026-09-27 to 2026-11-18)

| Call | Count | Daily median (max) | p50 | p95 | p99 | Total | Sum for each document p50 / max |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `judge_same_entity` | 1,963 | 35.5 (143) | 9.8 s | 12.9 s | 16.6 s | 5.6 h | 114 s / 532 s (sequential) |
| `judge_relationship` | 1,179 | 23 (79) | 15.7 s | 21.3 s | 28.0 s | 5.3 h | 139 s / 552 s (parallel) |

- Input size p50: 1,645 characters (same entity), 3,217 characters (relation).
- Failures: same entity 3 (2 CLI errors, 1 timeout); relation 6 (3 timeouts, 2 blocks, 1 CLI error).

### 3.2 One day of telemetry (support bundle, 12 documents)

| Call | Count | p50 |
| :--- | :--- | :--- |
| `extract_json`, `extract_summary` | 12, 12 | 47.5 s |
| `render_detail` | 13 | 44.4 s |
| `judge_relationship` | 22 | 18.3 s |
| `classify_paper` | 2 | 18.7 s |
| `classify_watch` | 6 | 16.9 s |
| `judge_same_entity` | 6 | 11.0 s |

### 3.3 Entity resolution

- 1,998 decision records: `no_match` 1,165, `exact_match` 759, `alias_match` 50, **LLM judge merge 20**, `acronym_match` 4. Thus about 1.0% of the 1,963 judge calls merged.
- Candidate outcomes: `invariant_reject` 607, `judge_different` 496, `judge_cap` 418, `jev_filtered` 331, `not_reached` 4, `judge_same` 2.

### 3.4 Jev shadow (2026-10-30 to 2026-11-18)

- 283 decisions, 1,795 candidate pairs, 0 failures, 1,653 pairs scored. Latency p50 209 ms, p95 509 ms, max 1,780 ms. Input tokens 610,996 (about 2,160 for each decision).
- Agreement with the LLM judge (477 judged pairs):

| Threshold | Both same | Both different | Only Jev same | Only Jev different |
| :--- | :--- | :--- | :--- | :--- |
| 0.5 | 2 | 473 | 2 | 0 |
| 0.9 | 1 | 475 | 0 | 1 |

- Filter simulation: below 0.2 (the current default), 460 of 475 `judge_different` pairs skip the judge and 0 of 2 `judge_same` pairs are lost. Below 0.5: 473 skip, 0 lost. Two positives give no evidence of recall.
- All 403 scored `judge_cap` candidates have a Jev score below 0.10.

---

## 4. Assessment of Each Target

### 4.1 Relation judgement (order 1)

- No second engine. Each call is an LLM call of 15 to 20 s. The total time is near the total of the same-entity judge.
- No loss of reason text: the cross-link step in `src/claire/ingest/pipeline.py` keeps type, direction, and confidence only.
- Shape: relation present → `noul`; 10 relation types → `choice`; 3 directions → `choice`. At most 10 candidates for each document (`max_relation_judges_per_doc`), thus one request with 30 questions.
- A calibrated probability can go to `relations.confidence`. Now the LLM writes its own number (mostly 0.85, 0.92, 0.95).
- Gap: no decision record for the relation judge (0 `cross_link` records). The count of "no relation" answers is unknown. Relations with confidence other than 1.0 after the first relation judge: 397. Bidirectional relations count two times and 1.0 values are not counted, thus the positive rate is only an estimate of about 30%.

### 4.2 Entity resolution (order 2)

- One `noul` question for each candidate. The Jev evaluation frame can be used again: shadow, filter, hybrid, circuit breaker, `review_shadow_pairs`, the agreement summary in bundles.
- Jev already removes the latency, thus the comparison is Gemini against Jev.
- Reasons for Gemini: the Jev p95 (509 ms) is a little above the gate of the design (p95 below 500 ms); one vendor (Google, key exists) makes the scope of Jev decision 11 (data goes to a third party in the United States) smaller; cost is very small (the Jev input of 611k tokens at the Gemini price is about USD 0.06 for all 283 decisions; tokenizers differ).
- Limit: only 2 positive pairs. A labeled sample of near variants (versions, editions, product and vendor) is necessary before a role grows.

### 4.3 Watch classification (order 3)

- One call for each new ingest, p50 16.9 s. Replacement: `noul` (watch) and `choice` (interval 1, 7, or 30 days).
- The short reason (`llm: ...`) is lost.
- Low risk: a wrong answer changes only the recheck schedule, and a failure does not stop the ingest (`ensure_watch_classification`).

### 4.4 Expansion gate (on hold)

- `judge_gate`: two `score` questions (relevance, quality) and one `noul` (same subject). `select_followups`: one `noul` for each link.
- The bundles have no data for these calls. The `same_subject_shadow` records with verdicts are in the production database only; they are good labeled data.
- The interpretation and reason of `judge_gate` show on the administrator review card and are lost on replacement. The `research` step before the gate uses most of the time, thus the latency gain is small.

### 4.5 Paper classification (low)

- Only `is_paper` can become `noul`. The author and title extraction is generation. Two calls instead of one give no gain.

---

## 5. Not Applicable

| Target | Reason |
| :--- | :--- |
| `extract`, `summarize`, `render_detail`, `compose_presentation`, `research` | Generation, not a closed-set decision. |
| Theme judgement (`theme_judgement.py`), automatic contribution (`auto_contribution.py`) | The design makes no LLM call on purpose. Results must be reproducible across federation. |
| Content guard (`ingest/fetchers/guard.py`) | Deterministic heuristics (principle A2). No reason to add a network call to each fetch. |
| Embeddings | Not a judgement. |

---

## 6. Constraints and Risks

1. **No reason text.** Where people read the reason (expansion review card, `interpretation`), use the API only as a filter or a shadow.
2. **Principle C2.** In the filter and hybrid modes, a failure records a status and the judge decides, as with Jev. If the API decides alone, a failure must fail the step and must not go to the LLM silently.
3. **Thresholds.** `noul` is in 0.01 to 0.99. The hybrid defaults (0.90 merge, 0.20 filter) are for Jev. Calibrate each engine.
4. **Data and tier.** The pricing summary says that free-tier input is used to improve products (not seen in the page text). Production needs a paid tier (Tier 1 or higher). The data region is not stated. A notice at the level of Jev decision 11 is necessary.
5. **Version pinning.** Only `gemini-decision-1` exists and the pinning policy is not stated. Check Jev decision 8 (pin the model during an evaluation).
6. **Account scope.** Jev is for administrators only. Gemini Decisions can run on the BYOK Gemini key of a member. Access for members is an owner decision.
7. **Capacity.** The free-tier limit (10,000 requests a day) is sufficient for the current load (max 143 + 79 calls a day).
8. **AGENTS.md.** The rule about instructions to an agent of another vendor applies to agents. A model API call from product code is in the same category as `GeminiProvider` now.

---

## 7. Possible Next Steps (owner decision for each)

1. **Relation judgement record.** For each cross-link judgement, keep an input summary, the LLM result (presence, type, direction, confidence), and the duration in the decision record and in the bundle. This gives evidence of relation quality, also without the Decisions API.
2. **Relation judgement shadow.** Send the same candidates to Gemini Decisions in one request, and keep the result next to the record. No decision effect.
3. **Second engine for entity resolution.** Add a Gemini adapter next to the Jev adapter. Ask Jev, Gemini, and the LLM judge about the same pairs and compare.
4. **Labeled sample.** Label near-variant pairs of entity resolution and pairs of the relation judge. The verdict method of the same-subject shadow is a model.
5. **Role.** After the sample, use the Go / No-Go gate (zero false merges, precision at least 98%, p95 below 500 ms) to select replace, filter, or reject for each engine.

---

## 8. Limits of the Measurements

- The judge call values are the telemetry rows in the bundle (from 2026-09-27).
- The support bundle is one day of one account. It does not show the whole server.
- The positive rate of the relation judge is an estimate from the relation table.
- The latency, accuracy, and price of Gemini Decisions are vendor claims, not measured with Claire data.
- Side finding (not about this API): Jev gave 0.97 to `Antrea CNI [Tool]` and `Antrea [Framework]`, but the type invariant rejected the merge. The type rule can block a real same entity.
