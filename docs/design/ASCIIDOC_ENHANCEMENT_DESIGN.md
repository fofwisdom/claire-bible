# AsciiDoc Body of a Document

- **Status**: Implemented. Design principles only. Current behavior: [`DOCUMENT_FORMAT.md`](../implementation/DOCUMENT_FORMAT.md) §2 (authoring) and §3 (rendering).
- **Date**: 2026-08-20 (dual format design), 2026-09-01, revised 2026-09-26 · condensed 2026-11-16. This document also keeps the principles of the retired `DUAL_FORMAT_ADOC_DESIGN.md`.
- **Related**: [`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) (AsciiDoc is the only body format, RD22 to RD25)

## Goal

The body of a document is a text that the LLM writes from the source. A reader must understand the main points, the context, and the important details without the source. Technical sources need more structure than plain paragraphs: quotes with attribution, code with callouts, notes, comparison tables, math, and links inside a long text. AsciiDoc has these constructs as standard syntax. Markdown needs extensions for most of them.

## Decisions in Force

- **The body is a readable article, not a summary.** It has several paragraphs in written style. It states only facts of the source, and it is in proportion to the source: a short source gives a short body. A section that truncation cut is left out, not guessed. Reason: the body is read in place of the source, so an invented fact is a defect.
- **Use few, practical constructs.** Quote, source code with callouts, notes, and tables are the core. Highlight is for one or two phrases, and notes are for one or two places. Images are only those that teach (diagrams, charts, architecture). Reason: a display of every AsciiDoc feature makes the text harder to read.
- **Math and cross references are part of the body syntax.** Math keeps formulas of papers without loss. Anchors and cross references let a reader move in a long body. Reason: sources of the knowledge base include papers and long technical texts.
- **Strict format purity.** A body uses only standard AsciiDoc. The thematic break is `'''` only. The renderer does not accept Markdown syntax in an AsciiDoc body; Markdown marks stay visible as text. When a body has mixed syntax, correct the prompt or write the body again. Do not add an exception to the parser for non-standard syntax, also when someone asks for it. Reason: a tolerant parser hides prompt defects and makes the format ambiguous.
- **Render ahead of time on the server.** The server converts the body to HTML when it stores the body, and keeps the HTML. The browser does not parse AsciiDoc with a large script that needs `unsafe-eval`. Reason: a strict content security policy, and an immediate display. The HTML is sanitized again in the browser before display.
- **The body does not carry knowledge graph data.** Authors, affiliations, tags, and other metadata belong to the knowledge graph. The body has no author line and no attribute variables. Reason: one place for each fact.
- **The body is a reading layer.** Knowledge graph extraction uses the raw text, not the body, so the AsciiDoc marks do not change the graph. Functions that give context to an LLM can use the body, because its structure helps the model.

## Rejected Alternatives

- **Markdown as the body format, or Markdown and AsciiDoc both.** Rejected: Markdown lacks the constructs above, and two formats were retired by RD22.
- **Transclusion (`include::`) between documents.** Rejected: each document is complete. The knowledge graph and the synthesis connect documents.
- **CSV tables.** Rejected: a body table is a short comparison, not a data set.
- **Attributes and conditionals.** Rejected: one body per document, and metadata is in the knowledge graph.
- **Client-side Asciidoctor.js.** Rejected: it needs `unsafe-eval` and a large download.

## Limits for Later Changes

- Export of several bodies together (PDF, EPUB, an archive) is not designed. The 2026-09 design put it with a future binder function that groups bodies; no binder design exists. The Markdown export of one document is the only export (RD24).
- A new body construct needs three changes in step: the body prompt, the server renderer, and the Markdown export converter.
