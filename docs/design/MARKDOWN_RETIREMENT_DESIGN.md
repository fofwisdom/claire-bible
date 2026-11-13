# Retirement of Markdown in Claire

- **Document Identifier**: `DESIGN-MARKDOWN-RETIREMENT-20261113`
- **Status**: Draft. Owner decisions RD22, RD23, and RD24 are locked (2026-11-13). The open items of Section 7 need owner decisions before the units that name them.
- **Date**: 2026-11-13
- **Revises**: [`DUAL_FORMAT_ADOC_DESIGN.md`](DUAL_FORMAT_ADOC_DESIGN.md) (frozen history document: Markdown and AsciiDoc as two body formats of ingestion).
- **Related Documents**:
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) RD21 (a published document sends its summary and its share link, not its body) and RD22
  - [`PRINCIPLES.md`](../PRINCIPLES.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[unverified]**.

---

## 1. Decisions

The owner decisions (2026-11-13) **[decision]**:

| ID | Decision |
| :--- | :--- |
| RD22 | Ingestion stops making Markdown bodies. AsciiDoc is the only body format that ingestion writes. |
| RD23 | The Markdown setting (`CLAIRE_RENDER_FORMAT=md` and the code paths behind it) is **sealed and closed**. It is not maintained. The sealed code stays only as a reference for the future Markdown export. |
| RD24 | In all of Claire, Markdown is used at one place only: the **export** of an ingested AsciiDoc document. No other function writes, asks for, or sends Markdown. |

Scope of RD24: the functions of the product (code in `src/`, `ops/`, `sidecar/`, prompts, and messages to users). The documentation files of the repository (`*.md`) are not product functions and are not part of RD24.

---

## 2. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| `CLAIRE_RENDER_FORMAT` selects the body format of ingestion. The default is `adoc`; the value `md` (or `markdown`) is accepted. | **[code]** `config.py` (`render_format`, `_parse_render_format`) |
| `documents.detail_format` has the column default `'md'`. Rows without a value read as `md`. | **[code]** `store/db.py` |
| When `detail_html` is empty, readers call `render_to_html(detail, detail_format)`. `render_md_to_html` tries the libraries `markdown` and `markdown_it`; neither is in `uv.lock`, so the image uses the fallback parser (headings, quotes, paragraphs). | **[code]** `render/aot.py`; `uv.lock` |
| `claire format-migrate` and `claire backfill-detail --format` write bodies again with the LLM; `--format` accepts `md` and `adoc`. | **[code]** `cli.py` |
| Fetchers write tables and lists of the source as Markdown in the raw text: web pages, ODT files, UTTU, HoYoLAB. `table_budget.py` finds tables by the Markdown table pattern. | **[code]** `ingest/fetchers/web.py` (`_format_html_tables_to_markdown`), `odt.py`, `uttu.py`, `hoyolab.py` (`format_post_to_markdown`), `extract/table_budget.py` |
| The vision prompts ask the model for a transcript "in clean Markdown". | **[code]** `extract/vision/gemini_vision.py` |
| The Telegram bot sends messages with `parse_mode="Markdown"`. | **[code]** `telegram_bot.py` |
| The text file fetcher reads `.md` and `.markdown` source files as text. The upload route serves `.md` as `text/markdown`. | **[code]** `ingest/fetchers/textfile.py`, `ingest/registry.py`, `api/server.py` |
| The graph view removes Markdown, AsciiDoc, LaTeX, and HTML marks from labels. | **[code]** `graphview.py` |
| No Markdown export of a document body exists. The Obsidian Vault export is deprecated. | **[code]** `store/vault.py` |

---

## 3. Seal of the Markdown Setting (RD23)

1. **Closed.** `render_format` has the value `adoc` only. A server with `CLAIRE_RENDER_FORMAT=md` (or `markdown`) starts, uses `adoc`, and writes one warning in the log. Operators cannot manage the server files or the database themselves; a value that worked before must not stop startup.
2. **Commands.** `--format` of `format-migrate`, `backfill-detail`, and the other commands accepts `adoc` only.
3. **Sealed code.** The code that only the Markdown setting reaches (Markdown body prompts, `render_md_to_html` and its fallback parser, the `md` branches of the format commands) gets the marker comment `SEALED (RD23): not maintained; reference for the Markdown export (MARKDOWN_RETIREMENT_DESIGN.md)`. Sealed code gets no fixes, no new features, and no new tests. A test of sealed code that fails is removed, not repaired. Sealed code that no path reaches can be deleted when the export (MD-3) no longer needs it as a reference.
4. **Old documents.** No migration changes `detail`, `detail_format`, or `detail_html` of an existing row. A document with compiled HTML shows that HTML. A document without compiled HTML gets the sealed fallback parser, as it is now. This read path is the one use of sealed code that stays active; it is not maintained either.
5. **No automatic rewrite.** A rewrite of a Markdown body to AsciiDoc uses the LLM and has a cost. It is not automatic. The owner of a theme starts it with `format-migrate` (`adoc`). The console shows the count of Markdown bodies per theme.
6. **Column default.** New rows get `detail_format` = `adoc` from the code that writes them. The column default `'md'` and the reading rule "empty means `md`" stay, because old rows without a value are Markdown. No schema change is necessary.

---

## 4. Markdown Outside the Body (RD24)

Each use of Section 2 changes as follows:

| Use | Change |
| :--- | :--- |
| Tables and lists in the raw text of fetchers (web, ODT, UTTU, HoYoLAB) | AsciiDoc table (`|===`) and list syntax. `table_budget.py` finds the AsciiDoc table pattern. The extraction prompts already ask for AsciiDoc. |
| Vision transcripts | The prompts ask for AsciiDoc (or plain text with the structure of the cards). |
| Telegram messages | `parse_mode="HTML"` with escaped text. Telegram Markdown escape rules are not used. |
| Graph view labels | No change: the label cleanup removes marks of old documents; it writes no Markdown. |
| Markdown source files (`.md`, `.markdown`) | Open item MX4 (Section 7). |
| Export | The only producer of Markdown (Section 5). |

---

## 5. Markdown Export

1. The export makes a Markdown file from an ingested AsciiDoc document. An old Markdown document goes out as it is.
2. The export is a read-only function: it writes nothing in the database.
3. The sealed Markdown code of Section 3 is a reference for the export (for example the handling of `==highlight==` marks). The export does not call sealed code; it has its own code.

---

## 6. Relation to Federation (RD21)

A published document sends its summary and its share link, not its body ([`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) Section 5.5). Thus the body format has no effect on the fediverse, and federation needs no Markdown.

---

## 7. Work Units

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| MD-1 | Seal and close the setting (Section 3 rules 1 to 3 and 6) | A server with `CLAIRE_RENDER_FORMAT=md` starts and ingests an AsciiDoc body. An old Markdown document shows its compiled HTML. | — | S |
| MD-2 | Console count of Markdown bodies per theme, and the rewrite start (Section 3 rule 5) | A theme with one Markdown body shows the count 1; after the rewrite it shows 0. | MD-1 | S |
| MD-4 | Raw text of the fetchers and `table_budget.py` in AsciiDoc (Section 4) | A web page with a table gives an AsciiDoc table in the raw text, and the table budget counts it. | MD-1 | M |
| MD-5 | Vision prompts and Telegram messages without Markdown (Section 4) | A bot message with `_` and `*` in a title arrives as text. | — | S |
| MD-3 | Markdown export (Section 5) with the decisions MX1 to MX3 | An end-to-end test exports one AsciiDoc document and one old Markdown document as Markdown files. | MD-1, MX1 to MX3 | M |

Documentation: `ENVIRONMENT_VARIABLES.md` (`CLAIRE_RENDER_FORMAT`), `COMMANDS.md` (`--format`), `API_HTTP_CONTRACT.md` and `openapi.yaml` (export route of MD-3).

---

## 8. Open Items

| ID | Item | Options | Proposal | Needed before |
| :--- | :--- | :--- | :--- | :--- |
| MX1 | Where the export starts | (a) One document: a button in the document view and a route. (b) A theme: an archive of Markdown files. (c) Both. | (a) first, then (b) | MD-3 |
| MX2 | Conversion from AsciiDoc to Markdown | (a) From the compiled HTML with an HTML-to-Markdown library (new dependency). (b) A small converter of the AsciiDoc subset that ingestion writes (no dependency). (c) An external tool such as Pandoc (system dependency in the image). | (b): the ingestion prompt limits the AsciiDoc syntax; constructs that the converter does not know go out as text **[unverified]** until a test covers the syntax of the prompt | MD-3 |
| MX3 | Content of the file | Body only, or body with a header (title, source URL, date, summary, tags as front matter) | Body with a YAML front matter header | MD-3 |
| MX4 | Markdown source files (`.md`, `.markdown`) | (a) Read them as plain text, as now (Claire does not interpret Markdown). (b) Refuse them. | (a): the file is a source, not Markdown that Claire writes | MD-4 |
