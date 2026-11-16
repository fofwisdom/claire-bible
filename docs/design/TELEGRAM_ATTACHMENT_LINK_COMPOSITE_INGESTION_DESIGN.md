# Telegram Composite Ingestion: One File and One Link in One Message

- **Status**: Implemented for a file (PDF, ODT, text) plus a link in the caption. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §11. Not implemented: an attached video or audio file as the primary source (the file reader does not read media); this case belongs to the planned [`TELEGRAM_VIDEO_PDF_BUNDLE_INGESTION_DESIGN.md`](TELEGRAM_VIDEO_PDF_BUNDLE_INGESTION_DESIGN.md).
- **Date**: 2026-10-08 · condensed 2026-11-16
- **Related**: [`VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`](VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md)

## Goal / problem

Users often send one Telegram message with a file and a link in the caption: slides with the session video, or a report with a reference page. Before this design, the caption was only a writing focus, and the link was not fetched. Two messages gave two separate documents. The file and the link describe one knowledge event and must give one document.

## Decisions in force

1. **Only one message is the unit.** A file and a link in the same message are combined. Claire does not join separate messages by time. Reason: a guess from timing can join unrelated sources and poison the graph.
2. **Fetch and keep both raw sources before extraction**, and record both in the document sources and components. Reason: raw first; provenance must be clear.
3. **The link is the primary source; the file supports it.** A video link plus a PDF uses the same component and presentation contract as the VMware Explore bundle, so the same reader and slide views work. Reason: the link has a public, stable identity; the video carries the timeline.
4. **Fail as a whole.** If the file or the link fails or gives empty text, no document is written, the inbox row records the error, and a retry replays both parts. Reason: a half document looks complete and is wrong.
5. **Keep the old behavior when the caption has no link.** The file is a single ingest and the caption is the focus. Theme tags, full-content and effort flags, and the focus syntax work in captions in both cases.
6. **One extraction for the combined document.** Reason: one event, one set of entities; and less quota.

## Rejected alternatives

- **Debounce or join messages that arrive close in time.** Rejected by decision 1.
- **Store the part that succeeded when the other part fails.** Rejected by decision 4.

## Limits for later changes

- Media attachments as the primary source need a local media reader first; follow the planned bundle design for that.
- New composite kinds must reuse the shared component contract, so that budgets and the reader work without special cases.
