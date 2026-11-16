# Retirement of Markdown in Claire

- **Status**: Implemented. Design principles only. Current behavior: [`DOCUMENT_FORMAT.md`](../implementation/DOCUMENT_FORMAT.md) §1 (one body format), §4 (raw text), §5 (Markdown export).
- **Date**: 2026-11-13 · condensed 2026-11-16
- **Related**: [`ASCIIDOC_ENHANCEMENT_DESIGN.md`](ASCIIDOC_ENHANCEMENT_DESIGN.md) (principles of the AsciiDoc body), [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) RD21

## Goal

Claire had two body formats, Markdown and AsciiDoc. Two formats doubled the prompts, the renderers, the commands, and the tests. The project has one participant, and all bodies are AsciiDoc. Markdown is a useful format for other tools, so Claire gives it as an export only.

## Decisions in Force (owner, 2026-11-13)

- **RD22. AsciiDoc is the only body format.** Ingestion makes no Markdown body. Reason: one format keeps the prompt, the renderer, and the reader simple and consistent.
- **RD23. The Markdown setting is closed.** An old Markdown value of the format setting does not stop the server. The server uses AsciiDoc and writes a warning. Reason: operators do not change the server files themselves, so a value that worked before must not break startup.
- **RD24. Markdown is used at one place only: the export of an ingested AsciiDoc document.** No other function of the product writes, asks for, or sends Markdown. This includes the raw text of fetchers, vision transcripts, and bot messages. The documentation files of the repository are not product functions.
- **RD25. No conversion.** The update deletes the remaining Markdown bodies one time and deletes all Markdown code. The document, its raw text, its extraction, and its graph stay; the normal path for a missing body writes it again in AsciiDoc. Reason: a conversion uses the LLM and has a cost, and the owner decided that all bodies are AsciiDoc. RD25 replaced the earlier plan to keep sealed Markdown code as a reference.
- **MX1. The export is for one document,** from the menus of a document in the federation client. An archive of a theme is not part of this decision.
- **MX2. The export converts the AsciiDoc subset of the body prompt without a dependency.** A construct that the converter does not know goes out as text. Reason: the body prompt limits the syntax, so a small converter is sufficient, and the image gets no new library or system tool.
- **MX3. The export file has a YAML front matter header** (title, source, date, summary, document identifier), then the title and the body. Reason: other tools read front matter.
- **MX4. Markdown source files stay a source.** Ingestion reads them as plain text. Claire does not interpret the Markdown of a source and does not write it.
- **The export is read-only.** It writes nothing in the database.
- **Federation needs no Markdown.** A published document sends its summary and its share link, not its body (RD21). Thus the body format has no effect on the fediverse.

## Rejected Alternatives

- **Keep Markdown as a second body format.** Rejected by RD22: two formats double the work and the tests.
- **Keep the sealed Markdown code as a reference for the export** (first form of RD23). Rejected by RD25: the export converter does not need it.
- **Convert old Markdown bodies to AsciiDoc with the LLM.** Rejected by RD25: cost, and no data needs it.
- **Export from the compiled HTML with an HTML-to-Markdown library, or with Pandoc** (MX2 options a and c). Rejected: a new dependency for a small, known syntax.
- **Refuse Markdown source files** (MX4 option b). Rejected: a source is not Markdown that Claire writes.

## Limits for Later Changes

- A new function that writes Markdown is a change of RD24. Ask the owner first.
- A change of the body prompt syntax must keep the export converter in step, or the new construct goes out as text.
