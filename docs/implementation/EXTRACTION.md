# Extraction, Entity Resolution, and Linking

Basis: commit d15ae8e0 (2026-11-16) · Readers: agents that change the extraction, the entity resolution, the ontology, the cross-document linking, or the content language. · Design reasons: [CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md](../design/CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md), [KNOWLEDGE_GRAPH_LINKING_AND_CALIBRATION_DESIGN.md](../design/KNOWLEDGE_GRAPH_LINKING_AND_CALIBRATION_DESIGN.md), [PREFERRED_LANGUAGES_DESIGN.md](../design/PREFERRED_LANGUAGES_DESIGN.md), [QUALITY_GUARDRAILS_AND_SUMMARY_INTEGRITY_DESIGN.md](../design/QUALITY_GUARDRAILS_AND_SUMMARY_INTEGRITY_DESIGN.md), [INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md](../design/INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md)

This document tells what the code does now, from a fetched `Document` to entities and edges in the graph. Fetchers, PDF parsing, video, and Telegram input are in [INGESTION.md](INGESTION.md). Link expansion and the merge of a same-subject source are in [EXPANSION.md](EXPANSION.md). Provider selection, model slots, and the embedding pin are in [PROVIDERS.md](PROVIDERS.md). Setting names and defaults are in [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

Anchors are file paths and function names, not line numbers. Evidence tags follow [PRINCIPLES.md](../PRINCIPLES.md) F1: [code], [measured], [decision], [unverified].

## 1. Flow of one document

`extract_resolve_store` in `src/claire/ingest/pipeline.py` does the work for one document. New ingest, refresh, re-extraction, and the same-subject source merge all call it with the same `doc.id`. Thus a second run adds to the sources of the existing entities and does not make new ones for the same names. [code]

The steps, in order (the page `/support/pipeline` shows them with the model of each step, [PROVIDERS.md](PROVIDERS.md) §5A): [code]

1. **Content check.** `validate_extractable_content` stops the run when the text is empty, when a video document has no transcript and no presentation, or when `validate_web_content` (in `src/claire/ingest/fetchers/guard.py`) rejects a `web` or `custom` document. The result is `insufficient_content: <reason>`.
2. **Effort.** The effort selection (paper classification for PDF and ODT, else the provider effort) is in [INGESTION.md](INGESTION.md) §7.4. The value goes to `doc.meta["applied_effort"]`.
3. **Structured extraction.** `provider.extract(doc, None, effort=...)` returns an `ExtractionResult` (`src/claire/extract/provider.py`): `summary`, `key_claims`, `entities`, `relations`. An exception stops the run with `extract failed: ...`.
4. **Separate summary.** When the `summarize` slot and the `extract_graph` slot have a different provider, model, or effort, `provider.summarize_document` makes the summary again and replaces `result.summary`.
5. **Extraction log.** `dbm.log_extraction` keeps the raw model response with the provider, the model, and the prompt version. Changes to post-processing can use it again without a new model call.
6. **Detail render.** `ensure_document_detail` makes the readable detail text. A failure sets `report.detail_rendered = False` and does not stop the ingest.
7. **Entity resolution.** Each extracted entity goes through `classify_entity_type` and then `resolve_or_create` (§4).
8. **Decision record.** `attach_resolution_meta` (`src/claire/extract/decision.py`) writes `doc.meta["resolution_log"]`. `evaluate_decision_matrix` writes `doc.meta["heatmap_matrix"]` from at most 30 candidate entities.
9. **Relations from the document.** Each extracted relation must name two different entities from step 7. `classify_relation_type` and `validate_relation` check it (§3). A failure increments `relations_rejected`.
10. **Cross-document links.** The relation judge examines the relation candidates from step 7 (§6).
11. **Model lineage.** The stages and models go to `doc.meta["model_lineage"]`.

## 2. Model input and output

### 2.1 Text budgets

The stored text budget (fetch time), the prompt budget of `doc_to_prompt` (extraction time), the table exemption of `src/claire/extract/table_budget.py`, and the full-content option are in [INGESTION.md](INGESTION.md) §7 and §8. In short: fetchers can cut the stored text and record `raw_truncated`, `orig_chars`, and `raw_chars` in `doc.meta`; the prompt budget is a separate, second limit; a merged document gets two times the single budget by default. [code]

### 2.2 Output language and style

The extraction prompt (`_SYS` in `src/claire/extract/prompts.py`, `PROMPT_VERSION = "extract-v9"`) tells the model to write `summary`, `observations`, and `key_claims` in Korean written style, for all source languages. Since `extract-v9` the prompt gives the Korean name of `summary` as 초록 (owner decision 2026-11-25; the code name stays `summary`). Entity names and aliases stay in their original form. The prompt asks for English or original-language aliases of localized proper nouns. The preferred languages do not change this output language. [code]

The body prompt of each rendition template has the fixed rule "truncation": it does not write detail for a section that the cut made incomplete. [code]

Rendition templates, 정리본 템플릿 (design [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) Section 5.2): `src/claire/extract/rendition_templates.py` builds each template from parts: `CORE_RULES` (the 14 rules of the current prompt, word for word, in their order), `FIXED_RULES` (core rules that no template replaces or omits; a template can only add lines to them), `TRAITS` (rule texts that more than one template uses: `no_source_tables`, `representative_formulas`), and the changes of each template (`replace`, `append`, `omit`, `insert_after`, `first_section`, `image_hint`). The rules get their numbers when a template builds its instructions. The set: `standard` (기본: the two traits, owner decision O17), `concise` (간결: a short scope, a fixed section order, and the two traits, O18 and O20), and `academic` (학술: no changes, thus the current prompt; the owner changes it later), each with a version. `academic` has the core rules without changes (the prompt of before the templates; a one-time check on 2026-11-26 showed the same text with and without a focus). The test checks the fixed rules in each template and that no two templates build the same instructions. `rendition_template(id)` raises `UnknownRenditionTemplate` for an identifier outside the set; there is no default template. Test: `tests/test_rendition_templates.py`. [code]

Use of the templates (phase E3a, 2026-11-26): `prompts.py` keeps no copy of the body rules. `render_detail_prompt(body, images, merged=, template=, scale=, focus=)` and `build_extraction_prompt_bundle(ontology_block, template=)` take a required template identifier; `build_instructions` puts the focus of the request after the scope sentence. Each provider has `render_detail(doc, format, focus, *, template)`. `ensure_document_detail` makes the 대표 정리본 with `PRIMARY_RENDITION_TEMPLATE` (`standard`) and records its version and model in `document_renditions` (`record_primary`). The agent client spec has `prompt_template` (`standard`) and `template_version`; the commit of a payload records the row with the provider `agent_client`. Data: [SCHEMA_VERSIONING.md](SCHEMA_VERSIONING.md) v50. [code]

Additional renditions (phase E3b, 2026-11-26): an ingest request selects `concise` and `academic` besides the 대표 정리본 (`claire.ingest.renditions`: `parse_rendition_selection`, `run_ingest_renditions`). With the server API, `IngestService.add_renditions` runs `pipeline.render_additional_rendition` for each template that the document does not have, from the stored raw text, with the effort rule of the body (`_body_effort`); it fetches nothing, and each run is one model call in the job slot of the account. The result has one item for each template: `made`, `existing`, or `failed` with the error. With an agent client, `client_extraction.request_client_renditions` writes the templates into `documents.meta.rendition_requests` of the staged document; `get_staged_source` gives `rendition_prompts` (one prompt for each template) at offset 0; the payload field `renditions` must have exactly these templates, each body passes `_validate_asciidoc_detail`, and the commit stores them with the provider `agent_client` and clears the requests. "정리본 추가" for a stored document uses the server API only (`POST /api/v1/kbs/{kb}/documents/{doc_id}/renditions`, 409 `document_not_extracted` while the document waits for an agent client); an agent client writes renditions only with a staged extraction. Tests: `tests/test_e2e_renditions_ingest.py`, `tests/test_client_extraction_ae5.py` (`test_additional_renditions_go_with_the_staged_task_and_its_submit`). [code]

### 2.3 Content languages

`Settings.preferred_languages` is the comma-separated list of the job ([`ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md`](../design/ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md)): `settings_for_context` puts the list of the account of the job (its own list, else the server default language) into the job settings; settings without an account read the server default language from `server_settings.default_languages` (`_ServerLanguagesSettingsSource`, `admin_context`). No environment variable sets it; `CLAIRE_PREFERRED_LANGUAGES` was copied one time at schema v46. `Settings.effective_preferred_languages` keeps the order and has `en` (at the end when the list has no `en`); an empty list gives `["en"]`. `Settings.effective_vlm_language` is the first item. [code] Tests: `tests/test_config.py`, `tests/test_preferred_languages.py`.

Consumers: [code]

- Caption and track selection in `plugins/ingest_adapter/claire.youtube/adapter.py`, `plugins/ingest_adapter/claire.naver_clip/adapter.py`, and `src/claire/ingest/fetchers/video.py`.
- The VLM target language in `src/claire/extract/vision/gemini_vision.py`, `src/claire/extract/vision/mock_vision.py`, `src/claire/ingest/fetchers/web.py`, and `plugins/ingest_adapter/claire.hoyolab/hoyolab.py` (also the HoYoLAB API language tag).
- The language tag in `plugins/ingest_adapter/claire.uttu/adapter.py` (project plugin `claire.uttu`).
- The `preflight` output line `preferred langs` in `src/claire/cli.py`.

The extraction prompts, the detail render, and the search tokenizer ([I18N.md](I18N.md) §7) do not read this setting. The entity label language of a viewer (`src/claire/ontology/label_language.py`) and the UI locale are different settings.

## 3. Ontology

`src/claire/ontology/types.py` has a closed set of 11 entity types (`Tool`, `Framework`, `Model`, `Paper`, `Article`, `Repo`, `Concept`, `Person`, `Org`, `Event`, `Note`) and 11 relation types (`implements`, `alternative_to`, `competes_with`, `authored_by`, `cites`, `part_of`, `uses`, `integrates_with`, `improves`, `derived_from`, `related_to`). [code]

`src/claire/ontology/registry.py`: [code]

- `classify_entity_type` and `classify_relation_type` match without case. A value outside the set stays as written and is marked provisional. It is not rejected.
- When the model fills `proposed_type`, `dbm.log_proposal` records the proposal.
- `validate_relation` checks the domain and range table `RELATION_DOMAIN_RANGE`. Only `authored_by` has a limit now: its target must be `Person` or `Org`. A provisional relation type passes as provisional. A provisional entity type skips the check.
- `ontology_prompt_block` puts the type lists and descriptions into the extraction prompt.

## 4. Entity resolution

`resolve_or_create` in `src/claire/extract/resolver.py` merges an extracted entity into an existing entity of the same theme, or makes a new entity. It returns a `ResolutionResult`. This is a tuple `(entity, created)` with the added fields `relational_candidates`, `multihop_candidates`, and `decision`. [code]

### 4.1 Gates

| Gate | Check | Embedding call | Stage name in the decision |
|---|---|---|---|
| 1a | The normalized new name is the name or an alias of an existing entity | no | `exact_match` |
| 1b | A normalized new alias is the name or an alias of an existing entity | no | `alias_match` |
| 1c | Acronym and full name (`ACRONYM_MIN = 3`), only with the same type | no | `acronym_match` |
| 2 | `passes_hard_invariants` on each candidate | — | `invariant_reject` in the trace |
| 3 | Vector score at or above `sim_tier_auto_merge` | one | `high_confidence_vector` |
| 4 | Borderline candidates go to the LLM judge, at most `MAX_JUDGE = 3` calls for each entity | — | `borderline_llm_judge` |
| 5 | No merge: new entity, and its vector is stored | — | `no_match` (`CREATE_NEW`) |

`normalize_name` (`src/claire/ontology/base.py`) folds case and spaces and removes quotes, brackets, and end punctuation. [code]

Gates 1a to 1c also apply `passes_hard_invariants`. The embedding (`embed_fn`) is called only after gate 1 has no match. [code]

Candidates for gates 3 and 4: up to 16 vector hits (`vstore.search`) and up to 8 FTS hits (`dbm.fts_search`). An FTS-only candidate has the score 0.0 and can only go to the judge. [code]

### 4.2 Hard invariants and type compatibility

`passes_hard_invariants(new_name, new_type, cand_name, cand_type, provisional)` returns False when: [code]

1. Both names have version numbers and the numbers are different.
2. One name is a two-letter upper-case token (for example `AI`) and the normalized names are not equal.
3. The normalized names are different and `is_type_compatible` is False.

When the normalized names are equal, the type is not checked. `is_type_compatible` is True for equal types, for a provisional entity, or when one type is in `GENERIC_ENTITY_TYPES = {"Concept", "Note"}`. [code]

### 4.3 Merge, type refinement, and rollback

`_execute_reversible_merge` adds the new aliases, observations, and the document ID to the candidate. When the candidate type is generic and the new type is specific, the candidate gets the specific type. The rollback payload keeps the added items and `prev_type`. `rollback_resolution` in `src/claire/extract/decision.py` removes the added items and sets `prev_type` again. The HTTP route is `POST /resolution/rollback`. [code] Tests: `tests/test_safe_gating_resolver.py`, `plugins/ingest_adapter/claire.hoyolab/tests/test_claire_hoyolab_e2e_vlm_ingest.py`.

### 4.4 The LLM judge

`judge_same_entity_prompt` in `src/claire/extract/prompts.py` asks for one word, SAME or DIFFERENT. Its examples include translations, transliterations, honorific variants, and short names of organizations, and also items of the same category that are different. When the Gemini judge fails with an error that is not a retry or policy error, it returns False (keep separate). [code]

A candidate that the judge calls DIFFERENT goes to `relational_candidates`. [code]

### 4.5 Score tiers

| Setting | Default | Use |
|---|---|---|
| `sim_tier_auto_merge` | 0.93 | Merge without the judge (gate 3) |
| `sim_tier_borderline` | 0.72 | Lower limit for the judge (gate 4) |
| `sim_tier_relational` | 0.70 | Lower limit for `relational_candidates` |
| `sim_tier_multihop` | 0.55 | Lower limit for `multihop_candidates` |

A vector hit below the borderline limit goes to the relational or multi-hop list by its score. A candidate that fails gate 2, or that the judge cap or a missing judge skips, goes to the relational list when its score is at or above the relational limit. [code]

`multihop_candidates` feed only the candidate list of the decision matrix (step 8 of §1). No code makes edges from them. [code]

### 4.6 Decision trace and Jev

When there are candidates, the `ResolutionDecision` keeps a trace with one entry for each candidate (outcome, score, vector or FTS source, judge time), the input snapshot, and the gate parameters. The Jev client (`src/claire/extract/jev.py`) can score the same candidates. It only records (shadow role); it never changes a decision (owner decision 2026-11-20, [GRAPH_BUNDLE_REVIEW.md](GRAPH_BUNDLE_REVIEW.md) §5). [code]

## 5. Embeddings and vector search

- **Entity text.** `format_entity_frame` in `src/claire/extract/frame.py` makes the text: `[ENTITY: name]`, `TYPE:`, `ALIASES:`, and up to 5 `OBSERVATIONS`. `GeminiProvider` embeds documents with `RETRIEVAL_DOCUMENT` (`format_v2_embed_prompt` prefix for `gemini-embedding-2`; SDK `task_type`/`title` params for `gemini-embedding-001`) at `output_dimensionality=3072`. [code]
- **Query text.** Hybrid search in `src/claire/retrieval/query.py` uses `task_type="RETRIEVAL_QUERY"`. When the KB active space is `eos_retired`, query embedding and `VectorStore.search` fail closed to FTS-only (`via == ["fts"]`). `Settings.embed_task_type` (default `SEMANTIC_SIMILARITY`) applies only when a caller gives no task type. [code]
- **Model & EOS Lifecycle.** The default embedding model is `gemini-embedding-2` (`google:gemini-embedding-2:3072:v2_prompt`). `gemini-embedding-001` (`eos_date='2028-05-14'`) remains selectable as an active legacy option. Each knowledge base tracks `active_space_id` and optional `target_space_id` in v39 (`embedding_spaces`), supporting live `AED7` dual-embed during shadow backfill. See [PROVIDERS.md](PROVIDERS.md). [code]
- **Store.** `VectorStore` in `src/claire/store/vectors.py` keeps vectors as BLOBs in the `embeddings` table and computes cosine similarity in Python. This brute-force backend is the only backend. `make_vector_store` calls `probe_sqlite_vec` but always returns the brute-force store. [code]
- **Adaptive centering.** With `adaptive_center=True`, `VectorStore.search` subtracts the mean of the stored vectors from the query and from each vector, and normalizes them again. Entity resolution and `link-relations` pass `vector_adaptive_centering` (default true). Hybrid search does not use centering. [code]
- **Fusion.** Hybrid search fuses the FTS rank and the vector rank with reciprocal rank fusion (`_rrf_fuse`, constant `RRF_K`). [code]

## 6. Cross-document links

When `enable_relation_linking` is true and the provider has `judge_relationship`, `extract_resolve_store` does this after the document relations: [code]

1. It takes the first `max_relation_judges_per_entity` items of `relational_candidates` for each resolved entity.
2. It removes duplicate pairs and pairs that already have an edge in either direction (`GraphStore.has_edge`).
3. It sorts by score and keeps `max_relation_judges_per_doc` pairs.
4. It sends a `RelationCandidate` for each pair (names, types, up to 5 aliases and observations, score, document title and summary) to the judge, in parallel threads up to `provider.max_concurrency`.
5. For a `RelationJudgement` with `has_relation` and a `relation_type`, it calls `GraphStore.add_edge` (`src/claire/store/graph.py`) with the confidence and the document as source. `backward` swaps the direction. `bidirectional` adds the reverse edge too.

`GraphStore.add_edge` rejects a self loop and a missing entity, and it rejects an edge that fails `validate_relation` unless the call marks it provisional. An existing edge with the same type, source, and target gets the new sources and confidence. A failed judge call gives no edge. The report counts `cross_relations_added` and lists `cross_linked_relations`. [code] Test: `tests/test_relation_linking.py`.

CLI: [code]

- `link-relations` (`cmd_link_relations` in `src/claire/cli.py`) does the same judge step for all entities of a theme. It uses vector pairs with a score in `[--min-score, sim_tier_auto_merge)`. `--min-score` defaults to `sim_tier_relational`, `--limit` defaults to 50. It writes edges unless `--dry-run` is given.
- `re-embed` (`reembed`, `cmd_reembed`) supports direct replacement (`replace_theme_embeddings`), non-blocking shadow backfill (`--target-space <space_id|model> --mode batch|sync`), 4-tier threshold calibration (`--calibrate`), and atomic space promotion (`--cutover`). It writes unless `--dry-run` is given.

## 7. Summary quality checks

These checks exist now. There is no common guard component. [code]

- **No mock in production.** In production, an unavailable provider raises `ProviderUnavailableError` and does not fall back to mock. See [PROVIDERS.md](PROVIDERS.md) and [PRINCIPLES.md](../PRINCIPLES.md) C2.
- **Empty summary.** When the summary is empty, the report has `summary_rendered = False` and `summary_error`. The ingest continues.
- **Markup in a summary.** `is_corrupted_summary` (`src/claire/extract/prompts.py`) finds AsciiDoc or Markdown marks. `doctor` shows the count. `regenerate --corrupted` (`IngestService.regenerate_components`, `corrupted_summary=True`) makes those summaries again. `clean_plain_summary` removes marks from a new summary.
- **Preface summary.** `is_preface_summary` finds a summary that only announces the document. The regenerate scan reports it.
- **Summary verdict in telemetry.** `evaluate_summary_verdict` in `src/claire/store/telemetry.py` returns `EMPTY`, `MOCK_PREFIX`, `RAW_SLICE_200`, `TEMPLATE_FALLBACK`, `CORRUPTED_ADOC`, `PREFACE_META`, or `REAL_LLM`. After `AntigravityProvider` removal (`AE8`, 2026-11-21), `submit_client_extraction` (`_validate_summary` in `src/claire/ingest/client_extraction.py`) calls it to reject non-`REAL_LLM` summaries from `agent_client` submissions; no provider calls `record_telemetry` for `provider_telemetry`, while old rows stay in the separate telemetry database ([TELEMETRY.md](TELEMETRY.md)).

## 8. Known limits

- [code] Entity resolution scans all entities of the theme for the acronym gate (`dbm.all_entities`) and all vectors of the theme for each vector search. The cost grows with the size of the theme.
- [code] The multi-hop candidates are collected but make no edges (§4.5).
- [code] The preferred languages do not control the extraction output language (§2.2).
- [code] For server-side provider ingestion, a failure inside the entity and relation loop of `extract_resolve_store` can leave part of the entities committed because individual store helpers commit at once (client-driven extraction in §9 fixes this via `_AtomicConnection`).

## 9. Client-driven extraction (`agent_client`, AE4 / AE5 / AED9) [code]

When an ingest request selects `extraction_mode = 'client'` (the choice of each request, design decision AED4-rev; see [INGESTION.md](INGESTION.md) §1 step 6), ingestion stages raw sources instead of calling a server LLM provider (`stage_raw` in `src/claire/ingest/pipeline.py` with `full_content=True`). A request of an agent client never runs the server API.

- **Staging and isolation (`AE4`)**: `documents.extraction_status` uses `'pending'`, `'extracted'`, or `'failed'`. Pending rows store `content_hash` and `raw_chars`, record `raw_inbox.status = 'staged'` (skipped by `recover-loop`), and stay hidden from search/query, raw source fetches, shares, research, `/u/` usercontent exports, auto-contribution, and federation until committed.
- **Claim lease & chunked read (`AE5`)**: `src/claire/ingest/client_extraction.py` exposes `stage_source`, `stage_file_bytes`, `stage_file_base64` (PDF, DOCX via `src/claire/ingest/fetchers/docx.py`, ODT, HTML, Markdown, RST, AsciiDoc, TXT up to 20 MiB), `list_staged_sources`, `claim_staged_source` (`DEFAULT_STAGED_CLAIM_TTL_SECONDS = 300.0`, single-use `claim_token`, optional `client_id`), `renew_staged_claim`, `get_extraction_spec`, `get_staged_source` (`DEFAULT_STAGED_CHUNK_LIMIT = 32000` characters or line slices), and `submit_client_extraction` (`CLIENT_EXTRACTION_SCHEMA_VERSION = "agent-extract-v1"`, supporting `dry_run=True`). The 6 MCP extraction tools under server `"claire"` (`extract_spec` read-only; `extract_stage`, `extract_queue`, `extract_claim` with `claim_token` renewal and automatic OAuth `client_id` inference, `extract_read`, and `extract_submit` requiring `write` scope) and the 7 `/api/client-extraction/*` HTTP routes ([API_AND_MCP_GUIDE.md](API_AND_MCP_GUIDE.md) §2.8, §3.2; including `multipart/form-data` `file` and JSON `content_base64` staging on `POST /api/client-extraction/stage`) wrap these functions and restrict writes to the caller's personal KB (`CT4`).
- **Four-layer prompt-injection & quality enforcement (`AED9`)**:
  1. *Framing and isolated subagent*: `get_staged_source` runs `neutralize_untrusted_source_tags` and `frame_untrusted_source` to wrap untrusted content in a per-call random-nonce `<untrusted_source_document nonce="..." document_id="..." sha256="...">` tag. Reference agent plugins (`plugins/antigravity/claire`, `plugins/claude/claire`, and `plugins/codex/claire`, built from `agent_clients/` and declarative `[adapter]` settings in `client.toml` into `fofwisdom/claire-agents` `v0.0.15`, see [`AGENT_CLIENT_COMMON_SPEC.md`](../design/AGENT_CLIENT_COMMON_SPEC.md)) expose unified `claire` + 3 subcommands (`claire ingest`/`claire regenerate`/`claire query` via `skills/claire/SKILL.md` on Antigravity with at most 3 `suggestedPrompts`; `$claire ingest`/`$claire regenerate`/`$claire query` via `skills/claire/SKILL.md` on Codex; `/claire:ingest`/`/claire:regenerate`/`/claire:query` via `skills/{ingest,regenerate,query}/SKILL.md` on Claude). All targets confine `extract_read` to an MCP-only isolated subagent (`agents/claire-extractor.md` via `rules/AGENTS.md` on Antigravity; `agents/extractor.md` on Claude, invoked internally by the hidden `skills/extract-runner/SKILL.md` shim with `user-invocable: false` and `context: fork`, and enforced by the fail-closed `hooks/hooks.json` `PreToolUse` guard directing callers to `/claire:ingest` or `claire:extractor`; `agents/claire-extractor.md` + `agents/claire-extractor.toml` with `sandbox_mode = "read-only"` on Codex, while ChatGPT web/desktop chat stays `readonly`).
  2. *Strict schema & concurrency lock*: `ClientExtractionPayload`, `ClientExtractedEntity`, `ClientExtractedRelation`, and `ClientSourceSpan` enforce `ConfigDict(extra="forbid", strict=True)`. `submit_client_extraction` verifies `claim_token`, active lease, and optimistic `expected_content_hash == documents.content_hash` (`409` on mismatch).
  3. *Deterministic validation*: before any DB write, the server runs `_validate_summary` (`evaluate_summary_verdict(..., provider_name="agent_client")`, `is_corrupted_summary`, `is_preface_summary`, `clean_plain_summary`), `_validate_asciidoc_detail` (AsciiDoc delimiter/heading/markup rules + `sanitize_rendered_detail` + `render_to_html`), `_validate_evidence_spans`, entity resolution with `passes_hard_invariants` + ontology `PROVISIONAL_TYPE_PATTERN`, and relation endpoint/type checks.
  4. *Precomputed embeddings & atomic commit (`AED7`)*: `_precompute_entity_embeddings` computes server embeddings (primary `active_space_id` plus any `target_space_id` dual-embed space) using merged entity text before opening `BEGIN IMMEDIATE` via `_AtomicConnection`. On success it commits `entities`, `relations`, `proposals`, `extractions` (`provider="agent_client"`), `extraction_status='extracted'`, provenance `meta.extraction_origin = "agent_client"`, and `raw_inbox.status = 'done'` atomically; non-fatal post-commit hooks (`_start_contribution_revisions`, `_match_document_theme`, `_auto_contribute_document`) run afterward on an independent `hook_conn`.
