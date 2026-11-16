# Zstandard Compression of Raw Artifacts

- **Status**: Implemented. Design principles only. Current behavior: [`DATA_LIFECYCLE.md`](../implementation/DATA_LIFECYCLE.md) §1.
- **Date**: 2026-09-09 · condensed 2026-11-16

## Goal / problem

Each document keeps its extracted source text as a compressed file, so that a later prompt or model can process it again without a new fetch (principle A1). The first format was gzip. Re-extraction, re-indexing, and merges read these files often, and gzip decompression was slow. The corpus grew to thousands of papers, laws, and Bible translations.

The change of format must not lose one document, and old files must stay readable.

## Decisions in force

- **Zstandard at level 3 is the format for new files.** Reason: a measurement on three real corpus types (short HTML, long articles, 1 MB legal text) showed that level 3 compresses much faster than gzip and gives a smaller or equal size. Higher levels gave almost no size gain and much lower speed. Decompression speed does not depend on the level.
- **Old gzip files stay readable (dual read).** The reader tries the new file first and then the old file. A damaged new file causes a warning and a read of the old file, not a crash. Reason: no downtime, and no data loss during a partial migration.
- **A write is atomic.** Write a temporary file, sync it, rename it, sync the directory, and only then delete the old gzip file. Reason: a power loss or a full disk must not leave a half-written file or remove the only good copy.
- **One compression context for each thread.** Reason: a shared zstd context in many threads crashed the process (segmentation fault). One new context for each call is slower.
- **The migration proves each file.** It compares the hash of the original bytes with the hash of a zstd round trip before it deletes the gzip file. Reason: zero data loss is required.
- **The migration syncs the directory in batches.** Each file is synced, but the directory sync happens every N files and at the end. Reason: a directory sync for each file made a large migration slow.
- **Helpers own the file names.** Callers do not build artifact paths with an extension. Purge and merge use helpers that find and delete all formats. Reason: a hard-coded extension leaves residues after a format change.
- **The migration is a dry run by default** (principle D3).

## Rejected alternatives

- **Stay with gzip.** Rejected: slow decompression in frequent operations.
- **Streaming compression for all files.** Rejected: for files below about 10 MB the one-shot call was 1.2 to 3.5 times faster. Streaming is only the fallback for frames without a content size.
- **A global compressor object.** Rejected: not thread-safe.

## Limits for later changes

- A new artifact format must keep the dual read of all older formats until a migration removed them.
- The `zstandard` package is a self-contained wheel. It must not add a system library to the container image.
