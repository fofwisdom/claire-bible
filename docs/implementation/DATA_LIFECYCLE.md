# Data Lifecycle: Raw Artifacts, Purge, Tombstones, and Focus

Basis: commit d15ae8e0 (2026-11-16) · Design principles: [`DATA_LIFECYCLE_AND_PURGE_DESIGN.md`](../design/DATA_LIFECYCLE_AND_PURGE_DESIGN.md), [`ZSTD_COMPRESSION_MIGRATION_DESIGN.md`](../design/ZSTD_COMPRESSION_MIGRATION_DESIGN.md), [`FOCUS_STANDARDIZATION_AND_PURGE_DESIGN.md`](../design/FOCUS_STANDARDIZATION_AND_PURGE_DESIGN.md) · Commands: [`COMMANDS.md`](COMMANDS.md) §3.8 and §3.15 · Settings: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md)

This document tells how the code now stores raw source text, how it purges documents, how tombstones stop a purged document from coming back, and how it stores the focus of a document. It uses file paths and function names as anchors.

Evidence tags: **[code]** read in the source at the basis commit. **[decision]** set by the owner.

## 1. Raw artifact storage

Code: `src/claire/store/raw.py`.

- **[code] Location and name.** The extracted source text of a document is in `data/raw/artifacts/<doc_id>.txt.zst` (Zstandard). A legacy file `<doc_id>.txt.gz` (gzip) can still exist. `artifact_paths` returns the files that exist for one document (zero, one, or both).
- **[code] Write.** `save_artifact` compresses with level 3 by default, writes a temporary file `.tmp-<doc_id>-*.zst` in the same directory (`tempfile.mkstemp`), calls `flush` and `fsync`, replaces the target with `os.replace`, and syncs the directory (`_sync_dir`). Only after this does it delete the legacy `.txt.gz`. On an error it deletes the temporary file, and the old files stay.
- **[code] Read.** `load_artifact` reads `.txt.zst` first. It calls the one-shot `zstd.decompress`, which also reads a frame without a content size. If the `.txt.zst` file is damaged (`ZstdError`, `EOFError`, `OSError`, `ValueError`, `UnicodeDecodeError`), it logs a warning and reads the `.txt.gz`. If no file can be read, it returns `None`.
- **[code] Library.** The code uses `compression.zstd` of the Python standard library (Python 3.14 and later), not the `zstandard` package: that package has no wheel for Python 3.15. The bytes of a frame are the same as before (level 3, content size in the header, checksum), so files of the two libraries are readable by each other.
- **[code] Thread safety.** `_get_compressor` keeps one zstd compression context for each thread (`threading.local`), keyed by level and checksum flag. Threads never share one context. Each `zstd.decompress` call uses its own context.
- **[code] Delete.** `remove_artifact` deletes all files from `artifact_paths`. Document merge and the purge cascade use these helpers, so both formats are deleted.
- **[code] Migration.** `migrate_artifacts` converts `.txt.gz` files to `.txt.zst`. For each file it compares the SHA-256 of the gzip content with the SHA-256 of a zstd round trip. If they are different, it keeps the `.gz` and records an error. It syncs the directory every `batch_fsync_interval` files (100) and at the end. The CLI is `claire artifact-migrate` (dry run by default, `--apply` to change files, `--level`, `--stop-on-error`, `--json`). See [`COMMANDS.md`](COMMANDS.md) §3.15.
- **[code] No automatic pruning.** The module does not delete raw artifacts by age or size.
- **[code] Backups** copy the artifact files as normal files; the backup code does not filter by extension (see [`OPERATIONS.md`](OPERATIONS.md), section 백업과 복원).

## 2. Lifecycle policy

- **[code] Settings.** `CLAIRE_DATA_LIFECYCLE` (`purgeable` or `append-only`, default `purgeable`, other values are refused) and `CLAIRE_ALLOW_PURGE` (boolean, default true), in `src/claire/core/config.py`.
- **[code] Rule.** `Settings.is_purge_allowed` is true when the lifecycle is `purgeable` **or** `CLAIRE_ALLOW_PURGE` is true. Thus purge is blocked only when the lifecycle is `append-only` and `CLAIRE_ALLOW_PURGE` is false.
- **[code] Effect.** When purge is blocked, `claire purge` prints the policy and exits with code 1, and `POST /purge` and `POST /document/purge` return 403. `claire preflight` shows the lifecycle and whether purge is allowed.

## 3. Purge

### 3.1 Surfaces

- **[code] CLI.** `claire purge [<target>]` with `--doc-id`, `--token`, `--url`, `--canonical-url`, `--pattern`, `--reason` (default `manual_purge`), `--no-tombstone`, `--untombstone`, `--apply`, `--dry-run`, `--yes`, and `-t/--theme`. Without `--apply` it is a dry run. With `--apply` it shows the targets and asks for confirmation unless `--yes` is given; in a non-interactive shell without `--yes` it does not purge and prints the command with `--yes`. `--untombstone` removes tombstones instead of purging.
- **[code] HTTP.** `POST /purge` and `POST /document/purge` (route table: namespace owner). The handler also requires the scope `owner`. The body has `target` (or `id`, `doc_id`), `dry_run`, `tombstone` (default true), and `reason` (default `web_purge`).
- **[code] Target resolution** uses `resolve_document_targets` (principle D1).

### 3.2 Cascade

`purge_document_cascade` in `src/claire/store/db.py` does these steps:

1. **[code]** Read the target documents (limited to the theme when a theme is given) and collect the files: all raw artifacts (`artifact_paths`), images `data/images/<doc_id>_*`, and attachments under `data/raw/attachments/<doc_id>/`. A dry run stops here and returns the counts.
2. **[code]** Remove the graph contributions of the documents (`rollback_document_graph_contributions`).
3. **[code]** In one `BEGIN IMMEDIATE` transaction: write tombstones (unless disabled), delete rows from `expand_queue`, `refresh_queue`, `doc_shares`, `document_snapshots`, `extractions`, `proposals`, `raw_inbox`, and `documents`, and retract published federation objects of these documents (`federation.retract_objects`, which makes a tombstone and a Delete task). On an error the transaction rolls back.
4. **[code]** Delete the collected files and the attachment directories.
5. **[code]** Trigger CDN invalidation for the documents (`media.trigger_cdn_invalidation`; errors are ignored).
6. **[code]** Run `heal_graph`: clean stale document references in entity and relation sources, remove dangling relations, ghost entities, and orphan embeddings, and rebuild the FTS index.
7. **[code]** Run `PRAGMA wal_checkpoint(TRUNCATE)` and `VACUUM`. The report tells if `VACUUM` ran.

The HTTP handler also removes the active ingest matrix of each purged document (`purge_active_matrix`).

### 3.3 Tombstones

- **[code] Table.** `purged_tombstones` in the knowledge database: `id`, `kb_id`, `url`, `canonical_url`, `content_hash`, `reason`, `purged_at`. Indexes on `(kb_id, canonical_url)`, `(kb_id, content_hash)`, and a unique index on `(id, kb_id)`.
- **[code] Guard.** `is_tombstoned` returns true when the URL, canonical URL, or content hash matches a tombstone of the theme. The ingest pipeline (`src/claire/ingest/pipeline.py`) and link expansion (`already_ingested` in `src/claire/expand/links.py`, canonical URL only) call it and drop the document.
- **[code] Lifting.** `remove_tombstones` (CLI `claire purge --untombstone`) deletes tombstones so that the source can be ingested again.

### 3.4 Audit

- **[code]** `audit_residuals` counts tombstones and tombstone violations (a document that matches a tombstone by id, canonical URL, or content hash). With a pattern it also counts matches in documents, inbox, extractions, snapshots, entity and relation sources, raw artifact files, and images, and it reports free pages that `VACUUM` can recover. CLI: `claire audit [<target>] [--pattern ...]`; `claire doctor` shows the tombstone count. See [`COMMANDS.md`](COMMANDS.md) §3.8.

## 4. Focus of a document

- **[decision]** The only term is "focus" (초점). See [`GLOSSARY.md`](GLOSSARY.md).
- **[code] Storage.** The focus of a document is `documents.meta["focus"]`. `set_document_focus` writes or removes it and also removes the legacy key. `get_document_focus` reads it.
- **[code] Legacy data.** `_migrate` in `src/claire/store/db.py` copies a legacy meta key into `focus` when `focus` is empty and then removes the legacy key from all documents. It runs on every migration and is idempotent.
- **[decision] Seal (CT28).** The ingest focus is sealed. No entry point reads a focus: HTTP ingest, re-ingest, and regenerate, the feed and timeline item ingest, the syndication dispatch, Telegram, and the CLI. A `focus` or `default_focus` field is an unknown field and is ignored. Design: [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) §13.7.
- **[code] Stored values.** `documents.meta["focus"]`, `themes.default_focus`, and `syndication_dispatches.applied_focus` stay as data. No code reads them: the render (`ensure_document_detail`) and the providers do not fall back to `doc.meta["focus"]`, and the document API, the reader, and the share page do not show it. The syndication dispatch removes `focus` from the copied meta.
- **[code] Telegram.** `parse_message_focus` and `parse_caption_composite` still separate the URL from the text after a separator (`|`, an empty line, a `[초점]`/`focus:` prefix, `--focus`), so that the URL is ingested. The bot ignores the text and replies once with `bot.focus.sealed`. The HTTP ingest routes use the same parser and ignore the text.
- **[code] Pipeline.** The focus parameters of `ingest`, `ensure_document_detail`, the prompts, and the providers stay without a caller until a later review. `tests/test_focus_seal.py` checks that no entry point gives a focus to the pipeline.
