# Link Expansion

Basis: schema v36 (2026-11-18) · Readers: agents that change link expansion, its filters and gate, or the same-subject source merge. · Design reasons and planned work: [EXPANSION_DESIGN.md](../design/EXPANSION_DESIGN.md) · Terms: [GLOSSARY.md](GLOSSARY.md)

This document tells what the code does now. The extraction is in [EXTRACTION.md](EXTRACTION.md). The content guard of the web fetcher is in [INGESTION.md](INGESTION.md). Evidence tags follow [PRINCIPLES.md](../PRINCIPLES.md) F1. Anchors are file paths and function names.

## 0. Terms and identifiers

| Term (Korean / English) | Identifiers |
|---|---|
| 링크 확장 / link expansion: follow the links of an ingested document, depth one | `expand` (package `src/claire/expand/`, `expand_queue`, `expand-loop`, `expand-run`, tier feature `expand`, `CLAIRE_EXPAND_MAX`, `CB_EXPAND_*`) |
| 자동 확장 / automatic expansion | `auto_expand`, `src/claire/expand/automatic.py`, source `expand:<parent id>` |
| 확장 제안 / expansion proposal | `src/claire/expand/proposal.py`, source `expand-proposal` |
| 확장 게이트 / expansion gate | `src/claire/expand/gate.py`, provider method `judge_gate`, telemetry call type `judge_gate` |
| 동일 주제 출처 병합 / same-subject source merge | setting `same_subject_merge`, judgement field `same_subject`, table `same_subject_shadow`, meta `extra_sources` |
| 맥락 조사 / contextual research (not link expansion; it uses the expansion gate) | `src/claire/research.py` |

Schema v35 changed the stored source names: `onehop:<parent id>` became `expand:<parent id>`, and `telegram-expand` became `expand-proposal` (`_migrate_v35_expansion_source_names` in `src/claire/store/db.py`). The telemetry rows with call type `judge_research` became `judge_gate` (`_migrate_gate_call_type` in `src/claire/store/telemetry.py`). [code]

## 1. Two paths after an ingest

At the end of `ingest` in `src/claire/ingest/pipeline.py`, when `expand_max > 0` and the document is not partial: [code]

- **Automatic expansion** (`auto_expand` is true): `dbm.enqueue_expand` adds the document to `expand_queue`. `IngestService._ingest_now` sets `auto_expand` only when `Settings.auto_expand` is true, the effective `expand_max` is above 0, and the source does not start with `expand:`, `recover`, `replay`, or `refresh`.
- **Expansion proposal** (`auto_expand` is false): `find_candidates` in `src/claire/expand/proposal.py` puts up to `expand_max` URLs into `report.candidates`. The Telegram bot shows a button. When the user confirms, the bot ingests each URL with `source="expand-proposal"` and `expand_max=0`, without an LLM gate.

`IngestService.regenerate_components` also adds the document to the queue after a successful graph re-extraction when `Settings.auto_expand` is true, `Settings.expand_max > 0`, and the document is not partial. [code]

The `expand-loop` daemon and the `expand-run` command call `IngestService.run_expand_queue`, which calls `IngestService.expand_document` for each pending item and marks it `done` or `error`. [code]

`expand_max`, `auto_expand`, and `same_subject_merge` come from `data/ingest_limits.json`, which the server administrator edits in `/admin/limits` (section "링크 확장 및 주기 재확인"). The loop daemons read the file at each cycle. The Telegram bot uses the values from its start. [code]

## 2. Candidate filter (no LLM)

`build_candidates` in `src/claire/expand/automatic.py` (automatic expansion) and `find_candidates` (expansion proposal) share these filters from `src/claire/expand/links.py`: [code]

- `is_blocked`: exact hosts in `_SKIP_HOSTS` (social, video, Google, `w3.org`, `schema.org`, `gravatar.com`), host suffixes in `_SKIP_HOST_SUFFIXES` (`info.arxiv.org`, `cornell.edu`, `creativecommons.org`, `doi.org`), and path prefixes in `_SKIP_PATH_PREFIXES` (`/about`, `/login`, `/terms`, `/feed`, and others).
- Duplicates by `canonicalize_url`, and the URL of the parent itself.
- `already_ingested`: a tombstoned URL, a URL that is the `canonical_url` of a document in the theme, or a URL that `find_document_by_extra_source` finds in the `extra_sources` of a document (a document that an earlier merge made).
- `prioritize_odt_links` (`src/claire/ingest/fetchers/odt.py`) puts ODT links first.

Differences: `build_candidates` reads only `doc.meta["links"]`, keeps the anchor text from `doc.meta["link_anchors"]`, and stops at `PREFILTER_CAP = 30`. `find_candidates` also scans URLs in the raw text and stops at `expand_max`. [code]

## 3. Selection, gate, and the same-subject judgement (LLM)

`IngestService.expand_document` in `src/claire/ingest/service.py`: [code]

1. `require_feature("expand")` checks the server tier feature. An administrator passes.
2. `build_parent_context` makes the context: title, latest summary, and the first 2000 characters of the detail (or of the raw text when there is no detail).
3. `provider.select_followups(context, candidates)` returns the indexes to follow. A provider without this method follows nothing. The list is cut to `expand_max` (or to the `limit` argument).
4. For each selected URL, `default_fetch` fetches the child once. A fetch error counts as `skipped`.
5. `provider.judge_gate(parent_title, context, child_preview)` scores the child. The preview is the child title and the first 4000 characters of its text. The result has `relevance`, `quality`, `same_subject`, `interpretation`, and `reason` (`ResearchJudgement` in `src/claire/extract/provider.py`). A provider without this method passes with 1.0 and 1.0 and `same_subject=False`.
6. `passes_gate` (`src/claire/expand/gate.py`) requires `relevance >= RELEVANCE_MIN` (0.7) and `quality >= QUALITY_MIN` (0.6). Contextual research uses the same gate.
7. A child that passes is ingested as a new document with `source="expand:<parent id>"`, `expand_max=0`, and the fetched child as `prefetched` (counter `stored`). It does not go into the queue again, so the depth is always one.
8. When `Settings.same_subject_merge` is `"shadow"` and the provider has `judge_gate`, `dbm.record_same_subject_shadow` writes one row to `same_subject_shadow` for each child that passed the gate: parent ID, child URL, child document ID (NULL when the ingest found a duplicate or failed), `same_subject`, relevance, quality, interpretation, and reason (counter `shadowed`; the item of `followed` gets `same_subject`). In the mode `"off"` (the default), the judgement is not kept.

The Gemini judge returns 0 scores when the response cannot be parsed (fail-closed). It raises rate-limit and policy errors to the caller. [code]

Result of `expand_document`: `{document_id, candidates, selected, stored, shadowed, skipped, followed[]}`. [code]

### 3.1 Same-subject source merge: off, shadow records only

Owner decision (2026-11-18): the merge is off by default, and only the shadow mode can be enabled, to collect data about its value. No setting value merges: `same_subject_merge` accepts `"off"` and `"shadow"` only (`IngestLimitManager.update_config`, the validator of `Settings`). [code]

A verdict is the decision of a person, so only the WebUI gives it. The server administrator reviews the records in the card "동일 주제 출처 병합 검토" on `/admin/limits` (`initExpansionReview` in `src/claire/static/js/console_admin.js`): each record shows the parent and the child (titles and links), the model judgement with its scores and reason, and the current verdict, with the buttons "같은 주제", "다른 주제", and "판정 삭제" and an optional note. The card also shows the report. It uses `GET /api/admin/expansion/shadow` and `PUT /api/admin/expansion/shadow/{record_id}/verdict` ([API_HTTP_CONTRACT.md](API_HTTP_CONTRACT.md)). The admin sees and judges only the records of the knowledge bases of the admin account; member knowledge bases are private. The verdict keeps the account that gave it (`verdict_by`). [code]

`claire expand-shadow` (`cmd_expand_shadow` in `src/claire/cli.py`) reads the records for agents. It does not change data: [code]

- `list [-t THEME] [--limit N] [--pending] [--json]` shows the records, the newest first, with the titles of the parent and the child (`list_same_subject_shadow`). `--pending` shows only records without a verdict.
- `report [-t THEME] [--json]` compares the model judgement with the verdicts (`same_subject_shadow_report`). The positive class is "same subject". Precision = TP / (TP + FP), recall = TP / (TP + FN), accuracy = (TP + TN) / records with a verdict. A value is null ("no data") when its denominator is 0. Records without a verdict do not count.

The columns `verdict` (`same`: the child is the same subject as the parent, so the merge would be correct; `different`: it is not), `verdict_note`, `verdict_at`, and `verdict_by` keep the verdict (`set_same_subject_verdict`; schema v36). [code]

The records follow the documents: a purge (`purge_document_cascade`) deletes the records of a purged parent or child, `dedup-merge` moves them to the keeper, and the deletion of a knowledge base, the reset of a knowledge base, the purge of an account, and the conversion to a shared knowledge base delete the rows of that knowledge base. [code]

### 3.2 Merged sources of earlier merges

The merge function is removed (owner request 2026-11-21). The idea of a document merge is in [REVERSIBLE_MERGE_IDEAS.md](../ideas/REVERSIBLE_MERGE_IDEAS.md). [code]

Documents that earlier merges made keep their `extra_sources`. A document with `extra_sources` gets the merged extraction budget (`effective_merged_extract_char_budget`, [INGESTION.md](INGESTION.md) §7.2) and the longer detail prompt with retries (`_MERGED_DETAIL_MIN_CHARS`, 1000). [code]

## 4. Display of merged sources

`get_document_extra_sources` gives the list. The document API (`src/claire/store/queries.py`) returns it as `extra_sources`. The main web UI (`src/claire/static/js/app.js`), the reader (`src/claire/static/js/reader.js`), and the share page (`src/claire/templates/share.html`) show the sources of documents that earlier merges made. [code]

## 5. Tests

`tests/test_same_subject_merge.py` (modes off and shadow, the refused value, no new candidate for a merged source), `tests/test_schema_v35.py` (source names, shadow records, telemetry), `tests/test_schema_v36.py` (the verdict columns of a v35 database, verdicts and report, account scope), `tests/test_e2e_same_subject_shadow.py` (setting, records, verdicts through the admin API, and the report command), `tests/test_expand.py`, `tests/test_expand_automatic.py`, `tests/test_research.py`, and `e2e/admin_limits.spec.js` (the WebUI section and the review card). The mock provider (`MockProvider` in `src/claire/extract/provider.py`) returns `same_subject=False` only when the text has `별개주제`, rejects text with `무관`, and skips candidates with `skip` in the URL or anchor. [code]

## 6. Known limits

- [code] `CLAIRE_EXPAND_CHAR_BUDGET` and `CLAIRE_RESEARCH_CONTEXT_BUDGET` exist in `Settings`, in the limit manager, and in the WebUI, but no code reads them. The lengths are fixed in the code:
  - `build_parent_context` (`expand/automatic.py`) and `build_context` (`research.py`) keep 2000 characters of the document detail (or of the raw text).
  - `IngestService.expand_document` gives the judge 4000 characters of the fetched child.
  - `extract/prompts.py` cuts again: `research_prompt` keeps 8000 characters of the context, `judge_gate_prompt` keeps 6000 of the context and 8000 of the report, `select_followups_prompt` keeps 6000 of the context.
- [code] Automatic expansion is on by default (`auto_expand=True`, `expand_max=5`). Members need the tier feature `expand` or their own key. Shared knowledge bases are not expanded.
- [unverified] The accuracy of `same_subject` on real model output is not measured (M5 of the original plan). The shadow records with verdicts and `expand-shadow report` are the tools for this measurement.
