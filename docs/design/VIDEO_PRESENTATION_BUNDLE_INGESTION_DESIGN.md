# VMware Explore Video and Presentation PDF as One Document

- **Status**: Implemented. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §10. The active waiting fix of 2026-09-22 is part of this design now.
- **Date**: 2026-09-04 · active waiting 2026-09-22 · condensed 2026-11-16
- **Related**: [`VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md`](VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md), [`TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md`](TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md)

## Goal / problem

A VMware Explore session page can give a Presentation PDF next to the video. The talk and the slides describe one event. The PDF link is not in the static HTML: it appears only after the page renders and the user selects the Presentation tab. On a slow server, the tab can appear seconds later than on a fast machine.

## Decisions in force

1. **One document per session.** The video URL stays the identity of the document. The PDF is a component of that document and an extra source, not a second document. Reason: one knowledge event must give one graph node set, with one extraction.
2. **Get and check all parts before any LLM call or database write.** Reason: a document must not be stored with half of its sources.
3. **"Absent" and "could not decide" are different states.** The PDF is absent only when the rendered session detail is complete and has no Presentation area. If the page did not render enough to decide, the ingest fails with a recoverable error. Reason: a false "absent" loses a PDF that the publisher gives.
4. **An advertised PDF that fails stops the ingest.** Download, validation, or extraction failure is an error; Claire does not store the transcript alone. Reason: the user asked for the session, and the session includes its slides.
5. **Wait for the real element, not for a fixed time.** The renderer waits for the Presentation tab to attach, up to a bound, then clicks it. If the tab does not come, it returns the DOM so that the readiness check decides. Reason (2026-09-22 incident): a fixed 2.5 s wait on a 2 vCPU server read the page before hydration and failed a valid session; a retry two minutes later succeeded on a warm cache.
6. **Accept only PDF links from the explicit Presentation area, at most three.** Reason: other PDF links on the page (related videos, navigation, tracking) are not the slides.
7. **Download only from an allowed host, over HTTPS, with checks at every redirect**: public address only, size limit while streaming, PDF media type, and PDF signature. Remove query strings from stored URLs. Reason: a downloaded binary must be the expected file from the expected place; signed query values are secrets.
8. **Reuse the PDF parser, but do not cut appendix or references from slides.** Reason: slides are not papers; their last pages are content.
9. **Keep the binary as a versioned file, before the database write.** A changed PDF adds a new version and keeps the old one in history. An existing standalone PDF document is referenced, not deleted or absorbed. Reason: raw first; no silent loss.
10. **Give each component a fair share of the prompt budget.** Reason: a flat cut of the combined text drops the PDF at the end.
11. **Keep the session title as the title.** The title inside the PDF is often a speaker name; it is kept as a separate field.

## Rejected alternatives

- **Separate documents for video and PDF, joined later by the same-subject source merge.** The merge runs after the parent is stored and depends on an LLM judgment; a publisher-given attachment needs a deterministic, atomic path.
- **Collect every `.pdf` link on the page.** It takes unrelated files.
- **Fixed sleep before the DOM check.** It caused the 2026-09-22 failure (decision 5).
- **One shared downloader for web pages and required attachments now.** The web fetch follows redirects and buffers whole responses; a required attachment needs per-hop checks and a stream limit. Merge them only when a second attachment source exists.

## Limits for later changes

- A second site with attachments should reuse the composition and storage contract, not the VMware-specific discovery.
- Keep the error states distinct (absent, discovery failed, download failed, invalid, extract failed, incomplete media).
