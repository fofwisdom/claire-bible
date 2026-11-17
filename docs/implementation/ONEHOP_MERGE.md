# One-Hop Expansion and Same-Subject Merge

Basis: commit d15ae8e0 (2026-11-16) · Readers: agents that change the one-hop expansion, its filters and gates, or the merge of a same-subject source into its parent. · Design reasons and planned work: [EXPAND_FILTERING_DESIGN.md](../design/EXPAND_FILTERING_DESIGN.md)

This document tells what the code does now. The extraction that runs after a merge is in [EXTRACTION.md](EXTRACTION.md). The content guard of the web fetcher is in [INGESTION.md](INGESTION.md). Evidence tags follow [PRINCIPLES.md](../PRINCIPLES.md) F1. Anchors are file paths and function names.

Source code comments name the old file `ONEHOP_MERGE_DESIGN.md` and its sections §3.1 (the `same_subject` field), §3.2 to §3.3 (the three-way branch and the merge), §3.3a (snapshot and restore), and §3.3b (the budget and the detail length). In this document they are §3, §4, §4.1, and §4.2.

## 1. Two paths after an ingest

At the end of `ingest` in `src/claire/ingest/pipeline.py`, when `expand_max > 0` and the document is not partial: [code]

- **Automatic** (`auto_expand` is true): `dbm.enqueue_expand` adds the document to `expand_queue`. `IngestService._ingest_now` sets `auto_expand` only when `Settings.auto_expand` is true, the effective `expand_max` is above 0, and the source does not start with `onehop`, `recover`, `replay`, or `refresh`.
- **Proposal** (`auto_expand` is false): `find_candidates` in `src/claire/expand/onehop.py` puts up to `expand_max` URLs into `report.candidates`. The Telegram bot shows a button. When the user confirms, the bot ingests each URL with `source="telegram-expand"` and `expand_max=0`, without an LLM gate.

`IngestService.regenerate_components` also adds the document to the queue after a successful graph re-extraction when `Settings.auto_expand` is true, `Settings.expand_max > 0`, and the document is not partial. [code]

The `expand-loop` daemon and the `expand-run` command call `IngestService.run_expand_queue`, which calls `IngestService.expand_document` for each pending item and marks it `done` or `error`. [code]

## 2. Candidate filter (no LLM)

`build_candidates` in `src/claire/expand/follow.py` (automatic path) and `find_candidates` (proposal path) share these filters from `src/claire/expand/onehop.py`: [code]

- `_is_blocked`: exact hosts in `_SKIP_HOSTS` (social, video, Google, `w3.org`, `schema.org`, `gravatar.com`), host suffixes in `_SKIP_HOST_SUFFIXES` (`info.arxiv.org`, `cornell.edu`, `creativecommons.org`, `doi.org`), and path prefixes in `_SKIP_PATH_PREFIXES` (`/about`, `/login`, `/terms`, `/feed`, and others).
- Duplicates by `canonicalize_url`, and the URL of the parent itself.
- `_already_ingested`: a tombstoned URL, a URL that is the `canonical_url` of a document in the theme, or a URL that `find_document_by_extra_source` finds in the `extra_sources` of a document.
- `prioritize_odt_links` (`src/claire/ingest/fetchers/odt.py`) puts ODT links first.

Differences: `build_candidates` reads only `doc.meta["links"]`, keeps the anchor text from `doc.meta["link_anchors"]`, and stops at `PREFILTER_CAP = 30`. `find_candidates` also scans URLs in the raw text and stops at `expand_max`. [code]

## 3. Selection and gate (LLM)

`IngestService.expand_document` in `src/claire/ingest/service.py`: [code]

1. `require_feature("expand")` checks the server tier feature. An administrator passes.
2. `build_parent_context` makes the context: title, latest summary, and the first 2000 characters of the detail (or of the raw text when there is no detail).
3. `provider.select_followups(context, candidates)` returns the indexes to follow. A provider without this method follows nothing. The list is cut to `expand_max` (or to the `limit` argument).
4. For each selected URL, `default_fetch` fetches the child once. A fetch error counts as `skipped`.
5. `provider.judge_research(parent_title, context, child_preview)` scores the child. The preview is the child title and the first 4000 characters of its text. The result has `relevance`, `quality`, and `same_subject` (`ResearchJudgement` in `src/claire/extract/provider.py`, default `same_subject=True`). A provider without this method passes with 1.0 and 1.0 and `same_subject=False`.
6. `passes_gate` requires `relevance >= RELEVANCE_MIN` (0.7) and `quality >= QUALITY_MIN` (0.6). These constants are in `src/claire/expand/research.py`, and the contextual research flow uses the same gate.

The Gemini judge returns 0 scores when the response cannot be parsed (fail-closed). It raises rate-limit and policy errors to the caller. [code]

## 4. Three-way result

| Gate | `same_subject` | Action | Counter |
|---|---|---|---|
| fails | any | Discard | `skipped` |
| passes | true | Merge into the parent (§4.1) | `merged` (or `skipped` when the merge fails) |
| passes | false | Ingest as a new document with `source="onehop:<parent id>"`, `expand_max=0`, and the fetched child as `prefetched` | `stored` |

A child that is stored as a new document does not go into the queue again, because its source starts with `onehop` and its `expand_max` is 0. A merge makes no new document and no queue item. So the depth is always one. [code]

### 4.1 Merge

`merge_source_into_document` in `src/claire/ingest/pipeline.py`: [code]

1. It refuses the merge when the child already exists as another document: same `content_hash`, same `canonical_url`, or a near duplicate (`near_duplicate_document`, parent excluded). The error is `duplicate: already exists as document <id>`.
2. It appends `{url, canonical_url, title, source_type, added_at}` to `meta["extra_sources"]`.
3. It appends the child text to the parent raw text after a separator line, a `[추가 출처: <title>]` line, and the child URL. The stored text is not cut.
4. `dbm.update_document_content` writes the new text, a new content hash, and the new meta. `save_artifact` writes the merged text as the artifact of the parent.
5. `extract_resolve_store` runs again with the parent ID.
6. On any failure, it writes the snapshot of the parent row (title, raw text, content hash, fetch time, meta) back and returns `{"merged": False, "error": ...}`. Entities and relations that the failed run already committed stay.

`expand_document` records the merge in `raw_inbox` with `source="onehop:<parent id>"` and the parent `document_id`, with status `done` or `error`. [code]

### 4.2 Budget and detail length of a merged document

- A document with `extra_sources` gets the merged extraction budget (`effective_merged_extract_char_budget`, by default two times `extract_char_budget`). See [INGESTION.md](INGESTION.md) §7.2. [code]
- The detail prompt asks for "A4 2~4" pages times the scale and tells the model to include each source. When the result is shorter than `_MERGED_DETAIL_MIN_CHARS` (1000), the Gemini, Antigravity, and Codex providers try again with scale 2 and then 4. They keep the last result. [code]

## 5. Display of merged sources

`get_document_extra_sources` gives the list. The document API (`src/claire/store/queries.py`) returns it as `extra_sources`. The main web UI (`src/claire/static/js/app.js`), the reader (`src/claire/static/js/reader.js`), and the share page (`src/claire/templates/share.html`) show the sources. [code]

## 6. Tests

`tests/test_onehop_merge.py`, `tests/test_expand.py`, `tests/test_expand_follow.py`, `tests/test_research.py`. The mock provider (`MockProvider` in `src/claire/extract/provider.py`) returns `same_subject=False` only when the text has `별개주제`, rejects text with `무관`, and skips candidates with `skip` in the URL or anchor. [code]

## 7. Known limits

- [code] `CLAIRE_EXPAND_CHAR_BUDGET` and `CLAIRE_RESEARCH_CONTEXT_BUDGET` exist in `Settings` and in the limit manager, but the expansion code does not read them. The context length (2000) and the child preview length (4000) are fixed in the code.
- [code] The merge restore does not remove entities and relations that a failed re-extraction already committed (§4.1 step 6).
- [unverified] The accuracy of `same_subject` on real Gemini output is not measured. The milestone for this measurement (M5 of the original plan) was not done.
