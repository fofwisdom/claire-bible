# Graph Bundles, Resolution Decision Data, Review Evidence, and Jev Evaluation

Basis: commit d15ae8e0 (2026-11-16); §5 and §6.2: the commit that makes Jev shadow only (2026-11-20) · Readers: agents that change entity resolution records, the graph bundle, the heatmap, the decision stream, or the TypeSafe AI Jev integration · Design principles: [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](../design/GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [JEV_SHADOW_EVALUATION_DESIGN.md](../design/JEV_SHADOW_EVALUATION_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](../design/DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md)

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
- `stage` values that the resolver writes: `exact_match`, `alias_match`, `acronym_match`, `high_confidence_vector`, `borderline_llm_judge`, `no_match`. `_merge` (old helper) writes `merge`. Old records can have `jev_hybrid_auto_merge` (removed mode, §5.4).
- `decision` values: `MERGE`, `CREATE_NEW`. After a rollback, the route changes the stored item to `ROLLED_BACK` and adds `rolled_back: true`.

### 2.2 Gate order in `resolve_or_create` [code]

1. Gate 1, no embedding: normalized name, then each new alias, then acronym to full name (same type, `ACRONYM_MIN = 3`). Each hit must also pass `passes_hard_invariants`. A hit merges with score 1.0. These decisions have no `candidates` trace.
2. Candidate collection: one embedding, vector search (limit 16) and FTS (limit 8). Vector hits at or above the borderline threshold are candidates. Lower hits go to the relational or multi-hop candidate lists. FTS hits have no cosine score.
3. Gate 2: `passes_hard_invariants` rejects a candidate (outcome `invariant_reject`).
4. Gate 3: score at or above the auto threshold merges (outcome `auto_merge`, stage `high_confidence_vector`).
5. Gate 4: the System 2 judge, at most `MAX_JUDGE = 3` calls for each entity. `judge_same` merges (stage `borderline_llm_judge`). `judge_different` keeps the candidate as a relational candidate. Over the limit, the outcome is `judge_cap`. Without a judge function, the outcome is `no_judge`.
6. No merge: a new entity, stage `no_match`, decision `CREATE_NEW`.

Thresholds come from settings `sim_tier_auto_merge` (0.93), `sim_tier_borderline` (0.72), `sim_tier_relational` (0.70), `sim_tier_multihop` (0.55), and `vector_adaptive_centering` (`_get_tier_thresholds`). Values in parentheses are the defaults in `src/claire/config.py`.

### 2.3 Candidate trace [code]

When the candidate loop starts, `_finish` writes:

- `candidates`: one entry for each candidate (at most 16 vector + 8 FTS). Keys: `id`, `origin` (`vector` or `fts`), `score` (`null` for FTS), `outcome`, `name`, `type`, `cand_obs_count`, `judge_ms` (judge calls only), `jev_noul` (when Jev answered). Candidates after the deciding one get `not_reached`; they keep `name` and `type` (before 2026-11-20 they had none).
- Outcome values: `invariant_reject`, `auto_merge`, `judge_same`, `judge_different`, `judge_cap`, `no_judge`, `not_reached`. Old records can have `jev_filtered` and `jev_hybrid_auto_merge` (removed modes, §5.4).
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

- `make_jev_shadow` returns an evaluator only when all are true: the job is of the server administrator account (`is_admin` or `server_admin`: with the server credentials or with the personal key of the administrator; members get `None`), `enable_jev` is on, and `jev_api_key` is not empty. `settings_for_context` keeps the Jev settings only for a context with `server_admin=True`; a member copy has `enable_jev=False` and no Jev key. Settings: `CLAIRE_ENABLE_JEV`, `CLAIRE_JEV_API_KEY`, `CLAIRE_JEV_BASE_URL`, `CLAIRE_JEV_TIMEOUT` ([`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)), and the `jev` block of `data/providers.json` (`enabled`, `api_key`, `base_url`, `timeout`, `model`; `ProviderManager`).
- The old keys `mode`, `filter_threshold`, and `auto_merge_threshold` of the `jev` block (`RETIRED_JEV_KEYS`) have no effect: a stored value stays in the file, and a value in a `PATCH /providers` request is not stored. [decision] owner, 2026-11-20.
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

### 5.3 Shadow role (the only role) [code]

- The resolver reads all candidates before the loop, so that a merge does not change the Jev input.
- It submits all candidates in one request before the loop (also the candidates that Gate 2 rejects later), and collects the result in `_finish`. The call runs at the same time as the judge.
- The result changes no decision. It goes to `decision.shadow` (with `role: "shadow"`) and `candidates[].jev_noul`.
- A candidate that the loop did not reach (`not_reached`) keeps its name and type in the trace (pre-merge state).

### 5.4 Removed filter and hybrid modes [decision]

- Owner decision 2026-11-20: Jev has no decision role, whatever the administrator selects. The filter and hybrid modes (2026-11-15) are removed from the code and from the console. Reason: the evaluation of 2026-11-20 ([`JEV_EVALUATION.md`](JEV_EVALUATION.md)) did not show the precision for a decision role.
- [measured] The filter mode was used in production from 2026-11-16 to 2026-11-18. Records of that time keep the outcome `jev_filtered`; their `shadow` record has no `role`. The bundle gives them `jev_role = "filter"` (§6.2).

---

## 6. Graph bundle

### 6.1 Bundle [code]

- Create: `claire export-graph [-t THEME] [-o PATH] [--json]` (CLI), `POST /graph/bundle` (scope `owner`, capability `settings`: the owner of a personal knowledge base; in a shared knowledge base a moderator or the admin, Section 6.3), or `POST /api/me/data-bundles` (the personal knowledge base of the account). Download: `GET /graph/bundle?token=…` (token only). A bundle expires after 24 hours (`GRAPH_BUNDLE_TTL_SECONDS`).
- Archive: `.tar.zst` with `manifest.json` and `graph_export.db`. The export database has `entities`, `relations`, `documents` (`id`, `title`, `url`, `source_type`, `created_at`), `contributions` (Section 6.3; empty for a personal knowledge base), and the `review_*` tables.
- `manifest.json`: `bundle_version` (`GRAPH_BUNDLE_FORMAT_VERSION = 4`; 3 added the key `kb_id`, 4 added the table `contributions`), `kb_id`, counts, `shared` (true for a shared knowledge base), `contributions_count`, `graph_export_sha256` (hash of the whole export database, review tables included), and `review` (the summary that `export_review_evidence` returns).
- Import: `claire import-graph <bundle> [-t THEME] [--target-db PATH] [--json]`. Import does not copy the `review_*` tables or the table `contributions`. The result reports `bundle_version` (1 when absent) and `review_tables` (row counts). Format v1 bundles import.
- The procedure that uses a bundle for graph checks in development mode: [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md) §11.2.
- The review evidence has only the decisions and heatmaps of the exported documents (`_export_decisions_and_heatmaps`). The source database has the documents of all knowledge bases of the server, thus a document of another knowledge base never goes into the bundle.

### 6.2 Review evidence tables (`REVIEW_SCHEMA_VERSION = 3`) [code]

| Table | Content | Source |
| :--- | :--- | :--- |
| `review_meta` | Key-value JSON: `review_schema_version`, `generated_at`, `thresholds`, `pipeline`, `coverage`, `embeddings`, `candidate_pairs`, `judge_calls`, `shadow` | Settings and the sections below |
| `review_decisions` | One row for each `resolution_log` item: all record fields; JSON for `rollback_payload`, `candidates`, `input_snapshot`, `gate_params`, `shadow` | `documents.meta` |
| `review_heatmaps` | `generated_by`, row and column counts, thresholds, `created_at` | `documents.meta["heatmap_matrix"]` |
| `review_candidate_pairs` | `entity_a < entity_b`, `score`, `band` | Stored entity embeddings |
| `review_judge_calls` | `judge_same_entity` and `judge_relationship` calls: time, document, provider, model, input size, duration, status | `telemetry.db` |
| `review_shadow_pairs` | One row for each traced candidate of a decision with a `shadow` record: names, types, origin, vector score, outcome, `judge_ms`, `jev_noul`, Jev status and model, final stage and decision, `merged`, and (schema 3) `decided_at`, `jev_role`, `jev_latency_ms` | `documents.meta` |

Rules:

- **Failure isolation.** `_guarded` runs each section (decisions, candidate pairs, judge calls). A failure writes `{"status": "error", "error": <exception type>}` and the bundle continues.
- **Secrets.** `pipeline` has only presence flags for `jev_api_key` and `gemini_api_key`. `_safe_url` removes user information, query, and fragment from `jev_base_url`. `raw_text` and embedding vectors are not exported.
- **Candidate pairs** (`_export_candidate_pairs`): only exported entities. Hash fallback vectors are removed (`looks_like_hash_fallback`) and counted. Only the most frequent dimension is used; the other dimensions are counted as `excluded_other_dim`. With adaptive centering, the group mean is removed. For each entity, the top 10 neighbours at or above the multi-hop threshold are kept, without duplicates. `band` is `auto_merge`, `borderline`, `relational`, or `multihop`. Back end: numpy in blocks of 256 rows; without numpy, pure Python up to 400 entities. More than 20,000 entities or a matrix over 128 MiB gives `status: "skipped"` with a reason.
- **Judge calls** (`_export_judge_calls`): `telemetry.db` opens read-only. Only rows whose `document_id` is in the exported documents are copied (theme isolation). Columns such as `output_snippet` and `error_message` are not copied. Successful calls without `document_id` are only aggregated in `unattributed_success` (count, duration p50/p95/max, input size p50/p95). Only the host CLI providers write telemetry rows; the Google AI Studio provider (also a personal key) writes none, thus its judge calls are not in this table (`judge_calls.coverage_note`). The judge latency of every provider is in `coverage.judge_latency_from_trace` (judged candidates, `judge_ms` p50 and p95 from the trace).
- **Jev role of a decision** (`_shadow_role`): the recorded `role`; else `filter` when a candidate has a filter-mode outcome (`jev_filtered`, `jev_hybrid_auto_merge`); else `unrecorded`.
- **Shadow summary** (`_shadow_summary`): decisions with a shadow record, decisions by role (`decisions_by_role`), counts by status and model, latency p50/p95/max, total input tokens, agreement with the System 2 judge at thresholds 0.5 and 0.9 (cells `both_same`, `both_different`, `jev_same_llm_different`, `jev_different_llm_same`), and the Jev "same" rate for each outcome.
- `coverage.heatmap_generator_notes` marks old `jev` heatmaps as stub output.

### 6.3 Bundle of a shared knowledge base [code]

Design: [SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) Section 13.2 ("a data bundle of a shared knowledge base: moderators and the admin").

- Route: `POST /kb/{number}/graph/bundle` (scope `owner`, capability `settings` in `ROUTE_POLICY`). A member and a collaborator get 403 `kb_capability_required`. The registry records the account that made the bundle (`create_graph_bundle_route`), thus the bundle is in the list of `GET /api/me/data-bundles` of that account. The console control is the card "Data bundle" (`#kb-bundle-card`) on the settings tab of `/settings/kb/{number}` (`console_kb.js`).
- Content (`_shared_kb_scope`): a document is in the bundle when it is not hidden and its contribution is `visible`. This is the rule of a fork (design Section 9.8 rule 5): pending, hidden, rejected, removed, failed, and merging copies are not in the bundle. A moderator also sees the pending and hidden copies in the console, but the bundle has only the content that each reader sees.
- An entity is in the bundle when one of its source documents is in it; a relation when its two entities and one of its source documents are in it. `sources` keeps only the documents of the bundle.
- The table `contributions` (`_contribution_rows`) has one row for each document of the bundle: `number` (public number of the contribution), `document_id`, `origin`, `contributor_account` (the public number of the account with the credit; NULL for a deleted account and for an account of another server), `remote_credit` (the actor with the credit on another server), `remote_source`, `remote_source_kb`, `approved_at`, `contributed_at`, `revised_at`. It has no internal account ID, no display name, no reason of a decision, no reviewer, no curator, and no source document of a personal knowledge base.
- The CLI `claire export-graph -t <kb>` makes the same bundle for a shared knowledge base and prints the count of contributions.
- Tests: `tests/test_kb_bundle.py` (end-to-end: the roles, the content, the download, the registry; the bundle of a personal knowledge base has an empty table `contributions`), `e2e/kb_manage.spec.js` (the console control).

---

## 7. Limits for the reading of evidence

- [code] Decisions before the trace code have no `candidates`; `coverage.decisions_with_candidates` shows the coverage. Decisions before 2026-10-31 have no `shadow` record.
- [code] Candidate state in the bundle is the state at bundle time, not at decision time. `cand_obs_count` shows the observation count at decision time.
- [code] Offline candidate pairs compare stored entity embeddings, not the query embedding of the ingest. They only approximate the Gate 3 distribution.
- [code] A judge provider that fails returns `False`, so the trace cannot tell a failure from `judge_different`. Compare with `review_judge_calls.status`; for the Google AI Studio provider no such row exists.
- [code] `review_judge_calls` covers only the retention period of `telemetry.db` (about 30 days).
- [code] In one Jev request, all questions share one `state`. A single-candidate request (as in `scripts/verify_jev_system1.py`) can give different scores. [unverified] The size of this difference.
- [decision] The System 2 judge is not ground truth. People label pairs from this pool.

---

## 8. Tests

| Test | Scope |
| :--- | :--- |
| `tests/test_graph_bundle_review.py` | Review tables, hash fallback removal, dimension groups, pair bands, telemetry theme isolation and column list, secrets, import skips review tables, section failure isolation, shadow pairs and agreement cells, schema v3 manifest, `decided_at`, `jev_role`, `jev_latency_ms`, judge latency from the trace, role of a decision |
| `tests/test_safe_gating_resolver.py` | Gate order and candidate trace outcomes, `input_snapshot`, `gate_params` |
| `tests/test_jev_shadow.py` | Request contract, `noul` parsing, error status, circuit, settings gate, accounts that get Jev (administrator with server credentials and with a personal key; no member), resolver integration (no decision change, pre-merge candidate state, Gate 1 makes no call, FTS merge `score=None`) |
| `tests/test_feature_routing_and_lineage.py` | Jev never changes a decision (low and high scores), Jev gets the candidates that Gate 2 rejects, `not_reached` keeps the name |
| `tests/test_decision_stream_and_heatmap.py` | Decision record round trip, `fallback_vector` heatmap regardless of Jev settings, matrix purge from the document and the active ingest, purge route authorization |
| `tests/test_resolution_benchmark.py`, `tests/eval_resolution_benchmark.py` | Resolution benchmark harness (`src/claire/extract/benchmark.py`) |
