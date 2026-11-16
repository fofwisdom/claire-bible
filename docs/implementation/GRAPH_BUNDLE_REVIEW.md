# Graph Bundles, Resolution Decision Data, Review Evidence, and Jev Evaluation

Basis: commit d15ae8e0 (2026-11-16) · Readers: agents that change entity resolution records, the graph bundle, the heatmap, the decision stream, or the TypeSafe AI Jev integration · Design principles: [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](../design/GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [JEV_SHADOW_EVALUATION_DESIGN.md](../design/JEV_SHADOW_EVALUATION_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](../design/DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md)

This document tells what the code does now. Anchors are file paths and symbol names, not line numbers. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: [code], [measured], [decision], [unverified].

---

## 1. Files

| File | Role |
| :--- | :--- |
| `src/claire/extract/decision.py` | `ResolutionDecision`, `HeatmapMatrixData`, `attach_resolution_meta`, `get_resolution_log_from_meta`, `rollback_resolution`, `get_all_resolution_decisions`, `build_fallback_matrix`, `evaluate_decision_matrix` |
| `src/claire/extract/resolver.py` | `resolve_or_create` (gated resolution), `_execute_reversible_merge`, `_trace_entry`, constants `AUTO_MERGE`, `CANDIDATE_FLOOR`, `MAX_JUDGE`, `ACRONYM_MIN` |
| `src/claire/extract/jev.py` | `JevClient`, `JevShadow`, `make_jev_shadow`, `build_same_entity_request`, `parse_noul_answers`, `entity_snapshot` |
| `src/claire/ingest/pipeline.py` | Calls the resolver with the Jev settings, writes `resolution_log` and `heatmap_matrix` into `documents.meta`, sends the `heatmap_matrix` progress event |
| `src/claire/graph_bundle.py` | `create_graph_bundle`, `import_graph_bundle`, `GRAPH_BUNDLE_FORMAT_VERSION`, `GRAPH_BUNDLE_TTL_SECONDS` |
| `src/claire/graph_bundle_review.py` | `export_review_evidence`, `count_review_tables`, `REVIEW_SCHEMA_VERSION`, `REVIEW_TABLES` |
| `src/claire/api/server.py` | `rollback_resolution_route`, `resolution_decisions_route`, `create_graph_bundle_route`, `download_graph_bundle_route` |
| `src/claire/static/js/app.js` | `renderHeatmapMatrix`, `renderMiniMatrixBannerHtml`, `renderDecisionStreamCenter`, `triggerRollback`, `isDecisionStreamEnabledForActiveTheme` |
| `src/claire/static/js/reader.js` | `renderReader`: heatmap banner on top of a document |
| `scripts/verify_jev_system1.py` | Offline Jev benchmark on a golden set, with the Go / No-Go gate |

---

## 2. Resolution decision record

### 2.1 Storage [code]

- One `ResolutionDecision` for each resolved entity. `attach_resolution_meta` writes the list to `documents.meta["resolution_log"]` and sets `documents.meta["has_decision_stream"]`. There is no separate table.
- Fields: `entity`, `stage`, `decision`, `candidate`, `score`, `reason`, `target_entity_id`, `source_entity_id`, `rollback_payload`, `timestamp`, and the optional review fields `candidates`, `input_snapshot`, `gate_params`, `shadow`.
- `stage` values that the resolver writes: `exact_match`, `alias_match`, `acronym_match`, `high_confidence_vector`, `borderline_llm_judge`, `jev_hybrid_auto_merge`, `no_match`. `_merge` (old helper) writes `merge`.
- `decision` values: `MERGE`, `CREATE_NEW`. After a rollback, the route changes the stored item to `ROLLED_BACK` and adds `rolled_back: true`.

### 2.2 Gate order in `resolve_or_create` [code]

1. Gate 1, no embedding: normalized name, then each new alias, then acronym to full name (same type, `ACRONYM_MIN = 3`). Each hit must also pass `passes_hard_invariants`. A hit merges with score 1.0. These decisions have no `candidates` trace.
2. Candidate collection: one embedding, vector search (limit 16) and FTS (limit 8). Vector hits at or above the borderline threshold are candidates. Lower hits go to the relational or multi-hop candidate lists. FTS hits have no cosine score.
3. Gate 2: `passes_hard_invariants` rejects a candidate (outcome `invariant_reject`).
4. Gate 3: score at or above the auto threshold merges (outcome `auto_merge`, stage `high_confidence_vector`).
5. Optional Jev filter or hybrid step (Section 4.4).
6. Gate 4: the System 2 judge, at most `MAX_JUDGE = 3` calls for each entity. `judge_same` merges (stage `borderline_llm_judge`). `judge_different` keeps the candidate as a relational candidate. Over the limit, the outcome is `judge_cap`. Without a judge function, the outcome is `no_judge`.
7. No merge: a new entity, stage `no_match`, decision `CREATE_NEW`.

Thresholds come from settings `sim_tier_auto_merge` (0.93), `sim_tier_borderline` (0.72), `sim_tier_relational` (0.70), `sim_tier_multihop` (0.55), and `vector_adaptive_centering` (`_get_tier_thresholds`). Values in parentheses are the defaults in `src/claire/config.py`.

### 2.3 Candidate trace [code]

When the candidate loop starts, `_finish` writes:

- `candidates`: one entry for each candidate (at most 16 vector + 8 FTS). Keys: `id`, `origin` (`vector` or `fts`), `score` (`null` for FTS), `outcome`, `name`, `type`, `cand_obs_count`, `judge_ms` (judge calls only), `jev_noul` (when Jev answered). Candidates after the deciding one get `not_reached`.
- Outcome values: `invariant_reject`, `auto_merge`, `jev_filtered`, `jev_hybrid_auto_merge`, `judge_same`, `judge_different`, `judge_cap`, `no_judge`, `not_reached`.
- `input_snapshot`: `name`, `type`, `aliases`, `observations`, `provisional` of the new entity.
- `gate_params`: `auto`, `borderline`, `relational`, `max_judge`, `adaptive_center`, `embedding_dim`, `embedding_hash_fallback`.
- A judge merge of an FTS-only candidate stores `score=None` and the reason "Judge confirmed SAME (FTS candidate, no vector score)".

### 2.4 Rollback [code]

- `_execute_reversible_merge` stores `rollback_payload` with `target_entity_id`, `added_aliases`, `added_observations`, `added_source`, `prev_type`. It does not store moved relations or a snapshot of a removed entity.
- `rollback_resolution` removes the added aliases, observations, and source from the target entity and restores `prev_type`.
- `POST /resolution/rollback` (role `owner`, `src/claire/api/security.py`) takes `document_id` and `entity`, rolls back the first matching `MERGE` decision, and marks it `ROLLED_BACK`.
- The web UI does not call it. The rollback button in the decision stream is disabled, and `triggerRollback` shows the message `ws.stream.rollback_off_hint` ("The atomic rollback is not tested, thus it is off for safety.").
- Document merge (`merge_documents` in `src/claire/store/db.py`) deletes the loser documents. It writes no tombstone.

---

## 3. Heatmap matrix [code]

- After resolution, the pipeline calls `evaluate_decision_matrix`. It always returns `build_fallback_matrix`: rows are the extracted entity names; columns are at most 30 candidates (decision candidates first, then relational and multi-hop candidates, then other entities). Cell values come from the recorded decision scores, then name equality, containment, token overlap, and `difflib` ratio. `generated_by` is always `fallback_vector`.
- Jev never makes the heatmap. Old documents can have `generated_by: "jev"`; those came from a removed local stub, not from Jev (`HEATMAP_GENERATOR_NOTES` in `src/claire/graph_bundle_review.py`).
- Storage: the pipeline writes the matrix to `documents.meta["heatmap_matrix"]` and sends a progress event with `stage: "heatmap_matrix"`.
- Active ingest: the active ingest record (`src/claire/ingest/active.py`) also holds the matrix while the ingest runs.
- Browser: `app.js` keeps the matrix in `sessionStorage` key `doc_matrix_<id>` and shows it during ingest (`renderHeatmapMatrix`). `reader.js` shows a banner above the document until the user confirms it. The banner also reads the matrix from the document data when the session key is absent.
- Confirmation (`confirmPurgeMatrix`): a user who can write calls `POST /document/matrix/purge` (role `owner`, body `{id}`). The route removes `heatmap_matrix` from `documents.meta` (`purge_document_matrix` in `src/claire/store/db.py`) and from the active ingest record (`purge_active_matrix`). The browser removes the session key and sets the flag `doc_matrix_confirmed_<id>` in `sessionStorage`, the account storage, and `localStorage`. The document purge route also calls `purge_active_matrix` for the purged documents.
- Thus the matrix stays in the database from the end of the ingest until the first confirmation by a writer. A reader without write permission only hides it in the browser.

---

## 4. Decision stream view [code]

- `GET /resolution/decisions` (role `read`): query `limit` (1 to 500, default 100) and `offset`. `get_all_resolution_decisions` reads the 200 newest documents that have a `resolution_log`, flattens their decisions, and sorts them newest first.
- The center view `stream` (`#streamwrap`, `renderDecisionStreamCenter`) lists the decisions with filters `ALL`, `MERGE`, `CREATE_NEW`, `ROLLED_BACK`. The document context panel opens it when the document has decisions.
- The home card "recent decisions" (`renderHomeDecisions`) shows only when the theme has `decision_stream_enabled === true` (`isDecisionStreamEnabledForActiveTheme`). No server code sets this field, thus the card never shows.
- [decision] [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §2 says that the decision stream pages are off for all themes and can be removed.

---

## 5. TypeSafe AI Jev evaluation

### 5.1 Activation [code]

- `make_jev_shadow` returns an evaluator only when all are true: the provider context is an administrator (members get `None`), `enable_jev` is on, and `jev_api_key` is not empty. Settings: `CLAIRE_ENABLE_JEV`, `CLAIRE_JEV_API_KEY`, `CLAIRE_JEV_BASE_URL`, `CLAIRE_JEV_TIMEOUT` ([`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)), and the `jev` block of `data/providers.json` (`enabled`, `api_key`, `base_url`, `timeout`, `model`, `mode`, `filter_threshold`, `auto_merge_threshold`; `ProviderManager`).
- `jev_model` default `jev-latest`. The model is set only in the web UI ([`PROVIDERS.md`](PROVIDERS.md)).
- One evaluator is kept for the current settings, thus the circuit state stays across documents.

### 5.2 Request and response [code]

- `JevClient.systemone`: `POST {base_url}/systemone` with `Authorization: Bearer <key>`, body `{model, state, questions}`. No retry.
- `build_same_entity_request`: one `noul` question for each candidate (keys `c1`, `c2`, ...), at most `SHADOW_MAX_CANDIDATES = 24`. `entity_snapshot` keeps at most 10 aliases and 6 observations of 280 characters.
- `parse_noul_answers` reads `answers.<key>.noul`, skips missing and non-finite values, and clamps to 0..1.
- Status values in the record: `ok`, `partial`, `http_<code>`, `timeout`, `network`, `bad_response`, `circuit_open`, `no_candidates`, `error`. `error` keeps at most 200 characters of the response body.
- Circuit: after 3 failures in sequence, calls stop for 600 s (`CIRCUIT_FAILURE_THRESHOLD`, `CIRCUIT_COOLDOWN_S`).
- Calls run in a shared pool of 4 threads. The threads do HTTP only; they do not touch the database. `collect` waits at most `timeout + 1` s and never raises.
- Jev calls are not written to `telemetry.db`. Latency and tokens are only in the decision record.

### 5.3 Shadow mode (default, `jev_mode = "shadow"`) [code]

- The resolver reads all candidates before the loop, so that a merge does not change the Jev input.
- It submits all candidates in one request before the loop, and collects the result in `_finish`. The call runs at the same time as the judge.
- The result changes no decision. It goes to `decision.shadow` and `candidates[].jev_noul`.

### 5.4 Filter and hybrid modes [code]

`ALLOWED_JEV_MODES = ("shadow", "filter", "hybrid")` (`src/claire/provider_manager.py`). The administrator console sets the mode.

- The resolver calls Jev after Gate 2 and Gate 3, only for the borderline candidates, and waits for the answer before the judge.
- With status `ok`, the borderline candidates are sorted by `noul` (high first).
- Filter: a candidate with `noul < jev_filter_threshold` (default 0.20) gets outcome `jev_filtered` and no judge call.
- Hybrid: the filter rule, plus an automatic merge (stage `jev_hybrid_auto_merge`, score = `noul`) when the top `noul` is at least `jev_auto_merge_threshold` (default 0.90), the second is below 0.50, and the gap is at least 0.40. Otherwise the record gets `hybrid_ambiguous_to_llm: true` and the judge decides.
- In these modes, Jev does change decisions. The shadow-mode statement "Jev does not decide" is true only in shadow mode.

---

## 6. Graph bundle

### 6.1 Bundle [code]

- Create: `claire export-graph [-t THEME] [-o PATH] [--json]` (CLI), or `POST /graph/bundle` (role `owner`). Download: `GET /graph/bundle?token=…` (token only). A bundle expires after 24 hours (`GRAPH_BUNDLE_TTL_SECONDS`).
- Archive: `.tar.zst` with `manifest.json` and `graph_export.db`. The export database has `entities`, `relations`, `documents` (`id`, `title`, `url`, `source_type`, `created_at`), and the `review_*` tables.
- `manifest.json`: `bundle_version` (`GRAPH_BUNDLE_FORMAT_VERSION = 2`), counts, `graph_export_sha256` (hash of the whole export database, review tables included), and `review` (the summary that `export_review_evidence` returns).
- Import: `claire import-graph <bundle> [-t THEME] [--target-db PATH] [--json]`. Import does not copy `review_*` tables. The result reports `bundle_version` (1 when absent) and `review_tables` (row counts). Format v1 bundles import.
- The procedure that uses a bundle for graph checks in development mode: [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md) §11.2.

### 6.2 Review evidence tables (`REVIEW_SCHEMA_VERSION = 2`) [code]

| Table | Content | Source |
| :--- | :--- | :--- |
| `review_meta` | Key-value JSON: `review_schema_version`, `generated_at`, `thresholds`, `pipeline`, `coverage`, `embeddings`, `candidate_pairs`, `judge_calls`, `shadow` | Settings and the sections below |
| `review_decisions` | One row for each `resolution_log` item: all record fields; JSON for `rollback_payload`, `candidates`, `input_snapshot`, `gate_params`, `shadow` | `documents.meta` |
| `review_heatmaps` | `generated_by`, row and column counts, thresholds, `created_at` | `documents.meta["heatmap_matrix"]` |
| `review_candidate_pairs` | `entity_a < entity_b`, `score`, `band` | Stored entity embeddings |
| `review_judge_calls` | `judge_same_entity` and `judge_relationship` calls: time, document, provider, model, input size, duration, status | `telemetry.db` |
| `review_shadow_pairs` | One row for each traced candidate of a decision with a `shadow` record: names, types, origin, vector score, outcome, `judge_ms`, `jev_noul`, Jev status and model, final stage and decision, `merged` | `documents.meta` |

Rules:

- **Failure isolation.** `_guarded` runs each section (decisions, candidate pairs, judge calls). A failure writes `{"status": "error", "error": <exception type>}` and the bundle continues.
- **Secrets.** `pipeline` has only presence flags for `jev_api_key` and `gemini_api_key`. `_safe_url` removes user information, query, and fragment from `jev_base_url`. `raw_text` and embedding vectors are not exported.
- **Candidate pairs** (`_export_candidate_pairs`): only exported entities. Hash fallback vectors are removed (`looks_like_hash_fallback`) and counted. Only the most frequent dimension is used; the other dimensions are counted as `excluded_other_dim`. With adaptive centering, the group mean is removed. For each entity, the top 10 neighbours at or above the multi-hop threshold are kept, without duplicates. `band` is `auto_merge`, `borderline`, `relational`, or `multihop`. Back end: numpy in blocks of 256 rows; without numpy, pure Python up to 400 entities. More than 20,000 entities or a matrix over 128 MiB gives `status: "skipped"` with a reason.
- **Judge calls** (`_export_judge_calls`): `telemetry.db` opens read-only. Only rows whose `document_id` is in the exported documents are copied (theme isolation). Columns such as `output_snippet` and `error_message` are not copied. Successful calls without `document_id` are only aggregated in `unattributed_success` (count, duration p50/p95/max, input size p50/p95).
- **Shadow summary** (`_shadow_summary`): decisions with a shadow record, counts by status and model, latency p50/p95/max, total input tokens, agreement with the System 2 judge at thresholds 0.5 and 0.9 (cells `both_same`, `both_different`, `jev_same_llm_different`, `jev_different_llm_same`), and the Jev "same" rate for each outcome.
- `coverage.heatmap_generator_notes` marks old `jev` heatmaps as stub output.

---

## 7. Limits for the reading of evidence

- [code] Decisions before the trace code have no `candidates`; `coverage.decisions_with_candidates` shows the coverage. Decisions before 2026-10-31 have no `shadow` record.
- [code] Candidate state in the bundle is the state at bundle time, not at decision time. `cand_obs_count` shows the observation count at decision time.
- [code] Offline candidate pairs compare stored entity embeddings, not the query embedding of the ingest. They only approximate the Gate 3 distribution.
- [code] A judge provider that fails returns `False`, so the trace cannot tell a failure from `judge_different`. Compare with `review_judge_calls.status`.
- [code] `review_judge_calls` covers only the retention period of `telemetry.db` (about 30 days).
- [code] In one Jev request, all questions share one `state`. A single-candidate request (as in `scripts/verify_jev_system1.py`) can give different scores. [unverified] The size of this difference.
- [decision] The System 2 judge is not ground truth. People label pairs from this pool.

---

## 8. Tests

| Test | Scope |
| :--- | :--- |
| `tests/test_graph_bundle_review.py` | Review tables, hash fallback removal, dimension groups, pair bands, telemetry theme isolation and column list, secrets, import skips review tables, section failure isolation, shadow pairs and agreement cells, schema v2 manifest |
| `tests/test_safe_gating_resolver.py` | Gate order and candidate trace outcomes, `input_snapshot`, `gate_params` |
| `tests/test_jev_shadow.py` | Request contract, `noul` parsing, error status, circuit, settings gate, resolver integration (no decision change, pre-merge candidate state, Gate 1 makes no call, FTS merge `score=None`) |
| `tests/test_feature_routing_and_lineage.py` | Filter and hybrid modes, gate order, hybrid single-winner guard |
| `tests/test_decision_stream_and_heatmap.py` | Decision record round trip, `fallback_vector` heatmap regardless of Jev settings, matrix purge from the document and the active ingest, purge route authorization |
| `tests/test_resolution_benchmark.py`, `tests/eval_resolution_benchmark.py` | Resolution benchmark harness (`src/claire/extract/benchmark.py`) |
