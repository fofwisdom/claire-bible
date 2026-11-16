# PDF Text Budget and Adaptive Reasoning Effort

- **Status**: Implemented. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §5, §6, §7. Planned parser work (layout order, vision tier): [`PDF_PARSER_AND_VISION_GUARDRAILS_DESIGN.md`](PDF_PARSER_AND_VISION_GUARDRAILS_DESIGN.md).
- **Date**: 2026-08-29 · condensed 2026-11-16
- **Related**: [`INITIAL_FULL_INGESTION_AND_EFFORT_DESIGN.md`](INITIAL_FULL_INGESTION_AND_EFFORT_DESIGN.md), [`TABLE_INGESTION_DESIGN.md`](TABLE_INGESTION_DESIGN.md)

## Goal / problem

Papers and long reports carry dense concepts. Two problems existed:

1. The general web budget cut PDF text before storage and before the prompt. Text that was cut at fetch time was lost also for a later re-extraction.
2. One reasoning effort for all documents was wrong. High effort on a short note costs time and money. Medium effort on a long paper gives a poor graph and a poor detail text.

## Decisions in force

1. **PDF text has its own, larger budget.** The same PDF budget applies at fetch time, in storage, and in the prompt. Reason: the stored text must hold what the prompt can use, so that a re-extraction does not lose text.
2. **Cut the appendix and the references by default, but record the cut.** These sections use the budget and add little to the graph. The cut is visible in the document metadata and in the UI, so a user can ask for the full text.
3. **One fast, reliable parser is the default. A runtime failure falls back once to a second parser.** Reason: ingestion must not stop because of one parser defect. The fallback and its reason are recorded.
4. **Detect bad text layers and tell the user.** Missing character maps, private-use glyphs, replacement characters, control noise, mojibake, separated Hangul jamo, and scans with no text are found after extraction. The result goes to the document metadata, the ingest report, the Telegram reply, and the reader. Reason: a bad text layer gives bad entities without an error; the user must see it.
5. **Do not trust PDF metadata for author and title.** Typesetting software writes operator account names and job file names into the metadata. Claire takes the author and title from the first page text and from the classifier answer, and records the raw values. Reason: a typesetter name as an author entity poisons the graph.
6. **Effort follows the document.** A document that the classifier marks as a paper and that is long enough gets the high paper effort. Other PDF documents get the default PDF effort or the provider effort. An explicit effort from the user always wins. Reason: spend reasoning where the content needs it.
7. **The paper classifier uses the configured provider at low effort.** It does not fall back to a different provider. Reason: no silent fallback that drives behavior; the owner selects the provider.

## Rejected alternatives

- **Pick the classifier provider by the lowest declared effort across all providers.** It was the first design. It is replaced by the configured provider for the classify feature, because an automatic cross-provider choice hides which provider and key run.
- **`pypdf` as a selectable production parser.** It has no Korean CMap support and can damage the text. Preflight refuses it.
- **Docling as a built-in parser.** The dependency was too large for the container. It is removed.
- **Add the author as a prompt header outside the budget.** The first design had it. The prompt header now holds title, URL, and source type only; the reconciled author stays in the document fields.

## Limits for later changes

- Keep the fetch budget, the stored text, and the prompt budget for PDF in agreement.
- Any new parser or fallback must record which parser ran and why.
- Keep the effort rule deterministic from recorded facts (classification, length, explicit request).
