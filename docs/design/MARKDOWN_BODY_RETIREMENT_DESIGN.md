# Retirement of Markdown Bodies in Ingestion

- **Document Identifier**: `DESIGN-MARKDOWN-BODY-RETIREMENT-20261113`
- **Status**: Draft. Owner decision RD22 is locked (2026-11-13). The open items of Section 6 need owner decisions before unit MD-3 starts.
- **Date**: 2026-11-13
- **Revises**: [`DUAL_FORMAT_ADOC_DESIGN.md`](DUAL_FORMAT_ADOC_DESIGN.md) (frozen history document: Markdown and AsciiDoc as two body formats of ingestion).
- **Related Documents**:
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) RD21 (a published document sends its summary and its share link, not its body) and RD22
  - [`PRINCIPLES.md`](../PRINCIPLES.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[unverified]**.

---

## 1. Decision (RD22)

The owner decision (2026-11-13) **[decision]**:

1. Ingestion stops making Markdown bodies. AsciiDoc is the only body format that ingestion writes.
2. Markdown stays as an **export** format only: a document body can leave Claire as a Markdown file.

Scope: the body that ingestion writes (`documents.detail` with `detail_format`). A Markdown **source** file (`.md`, `.markdown`) is a different thing: the text file fetcher reads it as text, as it reads `.txt` and `.rst` **[code]** (`ingest/fetchers/textfile.py`). This design does not change source files.

---

## 2. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| `CLAIRE_RENDER_FORMAT` selects the body format of ingestion. The default is `adoc`; the value `md` (or `markdown`) is accepted. | **[code]** `config.py` field `render_format` and `_parse_render_format` |
| `documents.detail_format` has the column default `'md'`. Rows without a value read as `md`. | **[code]** `store/db.py` (`detail_format TEXT DEFAULT 'md'`, `coalesce(detail_format, 'md')`) |
| A body has compiled HTML in `documents.detail_html` when ingestion compiled it. When `detail_html` is empty, readers call `render_to_html(detail, detail_format)`. | **[code]** `render/aot.py`, `api/federation_routes.py` |
| `render_md_to_html` tries the libraries `markdown` and `markdown_it`. Neither is in `uv.lock`, so the container image always uses the fallback parser: headings, quotes, and paragraphs only; inline marks stay as text. | **[code]** `render/aot.py`; `uv.lock` |
| The owner keeps the fallback parser and adds no Markdown library (2026-11-13). | **[decision]** |
| `claire format-migrate` and `claire backfill-detail --format` write bodies again in a selected format with the LLM. `--format` accepts `md` and `adoc`. | **[code]** `cli.py` |
| No Markdown export of a document body exists. The Obsidian Vault export is deprecated. | **[code]** `store/vault.py` (module note) |

---

## 3. Rules

1. **Ingestion writes AsciiDoc only.** `render_format` has the value `adoc` only. The commands `format-migrate`, `backfill-detail`, and the other commands with `--format` accept `adoc` only.
2. **An old setting does not stop the server.** A server with `CLAIRE_RENDER_FORMAT=md` (or `markdown`) starts, uses `adoc`, and writes one warning in the log. Operators cannot manage the server files or the database themselves; a value that worked before must not stop startup.
3. **Existing Markdown documents stay readable without an operator action.** No migration changes `detail`, `detail_format`, or `detail_html` of an existing row. A document with compiled HTML shows that HTML. A document without compiled HTML gets the fallback parser of `render/aot.py`, as now. The fallback stays for reading only.
4. **No automatic rewrite.** A rewrite of a Markdown body to AsciiDoc uses the LLM and has a cost. It is not automatic. The owner of a theme can start it with the existing rewrite path (`format-migrate` with `adoc`). The console shows the count of Markdown bodies per theme, so that the owner can decide.
5. **The column default changes for new rows only.** New rows get `detail_format` = `adoc` from the code that writes them. The column default `'md'` and the reading rule "empty means `md`" stay, because old rows without a value are Markdown. No schema change is necessary.
6. **Markdown export.** The export makes a Markdown file from a document body. The source is the AsciiDoc body (or the Markdown body of an old document, which goes out as it is). The export is a read-only function: it writes nothing in the database.
7. **Other uses of Markdown stay.** The Markdown source file of an ingest (Section 1, scope), Markdown in chat messages, and Markdown in documentation are not part of this decision.

---

## 4. Relation to Federation (RD21)

A published document sends its summary and its share link, not its body ([`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) Section 5.5). Thus the body format of a document has no effect on the fediverse. The fallback parser of rule 3 serves only the share page and the readers of Claire.

---

## 5. Work Units

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| MD-1 | `render_format` accepts `adoc` only, with the log warning of rule 2. `--format` choices are `adoc` only. Prompts and code paths that write Markdown bodies are removed. New rows get `detail_format` = `adoc`. | A server with `CLAIRE_RENDER_FORMAT=md` starts and ingests an AsciiDoc body. An old Markdown document shows its compiled HTML. | — | M |
| MD-2 | Console count of Markdown bodies per theme, and the rewrite start through the existing path (rule 4) | A theme with one Markdown body shows the count 1; after the rewrite it shows 0. | MD-1 | S |
| MD-3 | Markdown export (rule 6) with the decisions of Section 6 | An end-to-end test exports one AsciiDoc document and one old Markdown document as Markdown files. | MD-1, Section 6 | M |

Documentation: `ENVIRONMENT_VARIABLES.md` (`CLAIRE_RENDER_FORMAT`), `COMMANDS.md` (`--format`), `API_HTTP_CONTRACT.md` and `openapi.yaml` (export route of MD-3).

---

## 6. Open Items (owner decisions before MD-3)

| ID | Item | Options | Proposal |
| :--- | :--- | :--- | :--- |
| MX1 | Where the export starts | (a) One document: a button in the document view and a route. (b) A theme: an archive of Markdown files. (c) Both. | (a) first, then (b) |
| MX2 | Conversion from AsciiDoc to Markdown | (a) From the compiled HTML with an HTML-to-Markdown library (new dependency). (b) A small converter of the AsciiDoc subset that ingestion writes (no dependency). (c) An external tool such as Pandoc (system dependency in the image). | (b): the ingestion prompt limits the AsciiDoc syntax, so a small converter covers it; constructs that it does not know go out as text **[unverified]** until a test covers the syntax of the prompt |
| MX3 | Content of the file | Body only, or body with a header (title, source URL, date, summary, tags as front matter) | Body with a YAML front matter header |
