# Retirement of Markdown in Claire

- **Document Identifier**: `DESIGN-MARKDOWN-RETIREMENT-20261113`
- **Status**: Draft. Owner decisions RD22 to RD25 and MX1 to MX4 are locked (2026-11-13). MD-1, MD-3 (backend), MD-4, and MD-5 are done; MD-2 is replaced by RD25 (Section 7).
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
| RD25 | No conversion of Markdown bodies. The project has one participant and all bodies are AsciiDoc. The update **deletes** at once: the Markdown bodies that remain (one time, at the first start of the version) and all Markdown code (the sealed code of RD23 is removed, not kept as a reference). RD25 replaces Section 3 rules 3 to 5 and unit MD-2. |

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

## 3. Seal of the Markdown Setting (RD23, changed by RD25)

RD25 changes this section: the sealed code and the read path of old Markdown bodies are deleted; rules 1, 2, and 6 stay. Section 3.1 has the RD25 rules.

1. **Closed.** `render_format` has the value `adoc` only. A server with `CLAIRE_RENDER_FORMAT=md` (or `markdown`) starts, uses `adoc`, and writes one warning in the log. Operators cannot manage the server files or the database themselves; a value that worked before must not stop startup.
2. **Commands.** `--format` of `format-migrate`, `backfill-detail`, and the other commands accepts `adoc` only.
3. **Sealed code.** The code that only the Markdown setting reaches (Markdown body prompts, `render_md_to_html` and its fallback parser, the `md` branches of the format commands) gets the marker comment `SEALED (RD23): not maintained; reference for the Markdown export (MARKDOWN_RETIREMENT_DESIGN.md)`. Sealed code gets no fixes, no new features, and no new tests. A test of sealed code that fails is removed, not repaired. Sealed code that no path reaches can be deleted when the export (MD-3) no longer needs it as a reference.
4. **Old documents.** No migration changes `detail`, `detail_format`, or `detail_html` of an existing row. A document with compiled HTML shows that HTML. A document without compiled HTML gets the sealed fallback parser, as it is now. This read path is the one use of sealed code that stays active; it is not maintained either.
5. **No automatic rewrite.** A rewrite of a Markdown body to AsciiDoc uses the LLM and has a cost. It is not automatic. The owner of a theme starts it with `format-migrate` (`adoc`). The console shows the count of Markdown bodies per theme.
6. **Column default.** New rows get `detail_format` = `adoc` from the code that writes them. The column default `'md'` and the reading rule "empty means `md`" stay, because old rows without a value are Markdown. No schema change is necessary.

---

### 3.1 Deletion (RD25)

1. **Bodies.** At the first start of the version, `claire.db` deletes `detail` and `detail_html` of each document with `detail_format` `md` or `markdown`, and sets `detail_format` = `adoc`. The document, its raw text, its extraction, and its graph stay. The key `markdown_bodies_purged` in `meta` makes the step run one time only. The existing path for missing bodies (`claire backfill-detail`) writes an AsciiDoc body again. The owner decided this deletion (2026-11-13); it is not an operator task.
2. **Code.** `render_md_to_html`, `render_detail_prompt_md`, the Markdown branch of the mock provider, the Markdown branches of `set_document_detail`, the client function `renderMarkdown`, and the `marked` script are deleted. `render_to_html` and the client `renderContent` render AsciiDoc only. Text that the client AsciiDoc renderer cannot handle is shown as escaped plain text.
3. **Defaults.** The column default of `documents.detail_format` is `adoc` for a new database, and an empty value reads as `adoc`.
4. **Setting.** `CLAIRE_RENDER_FORMAT=md` still does not stop startup (Section 3 rule 2).

## 4. Markdown Outside the Body (RD24)

Each use of Section 2 changes as follows:

| Use | Change |
| :--- | :--- |
| Tables and lists in the raw text of fetchers (web, ODT, UTTU, HoYoLAB) | AsciiDoc table (`|===`) and list syntax. `table_budget.py` finds the AsciiDoc table pattern. The extraction prompts already ask for AsciiDoc. |
| Vision transcripts | The prompts ask for AsciiDoc (or plain text with the structure of the cards). |
| Telegram messages | `parse_mode="HTML"` with escaped text. Telegram Markdown escape rules are not used. |
| Graph view labels | No change: the label cleanup removes marks of old documents; it writes no Markdown. |
| Markdown source files (`.md`, `.markdown`) | No change (MX4): ingestion must read them, so the text file fetcher keeps them as a source. Claire does not interpret the Markdown of a source. |
| Export | The only producer of Markdown (Section 5). |

---

## 5. Markdown Export

1. The export makes a Markdown file from an ingested AsciiDoc document (MX1: one document).
2. The export is a read-only function: it writes nothing in the database.
3. **Place in the user interface (owner, 2026-11-13).** The federation client in `/syndication` offers the export in the context menu (right click) of each item of the knowledge base view. Neither the design of `/syndication` nor the code has a context menu yet; the menu is part of FP4-5 (the client view). Until then only the backend exists.
4. **Backend.** `GET /document/export?id=...&format=markdown` (access `read`, the rule of `GET /document`). The answer is `text/markdown; charset=utf-8` with `Content-Disposition: attachment`. `markdown` is the only format; another value gives 400.
5. **Conversion (MX2).** `render/markdown_export.py` converts the AsciiDoc subset of the body prompt without a dependency: section titles, strong, emphasis, highlight (to strong), links, images, lists, tables (GFM), source, literal, quote, admonition, and sidebar blocks, thematic breaks, anchors, and cross references. A construct that it does not know goes out as text.
6. **File (MX3).** A YAML front matter header (`title`, `source`, `date`, `summary`, `claire_document_id`), the title as a level 1 heading, then the body.

---

## 6. Relation to Federation (RD21)

A published document sends its summary and its share link, not its body ([`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) Section 5.5). Thus the body format has no effect on the fediverse, and federation needs no Markdown.

---

## 7. Work Units

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| MD-1 | Seal and close the setting (Section 3 rules 1 to 3 and 6). Done 2026-11-13. | A server with `CLAIRE_RENDER_FORMAT=md` starts and ingests an AsciiDoc body. An old Markdown document shows its compiled HTML. | — | S |
| MD-2 | Replaced by RD25: one-time deletion of Markdown bodies and deletion of all Markdown code (Section 3.1). Done 2026-11-13. | The first start deletes a Markdown body once and keeps an AsciiDoc body (`tests/test_e2e_markdown_export.py`). | MD-1 | S |
| MD-4 | Raw text of the fetchers and `table_budget.py` in AsciiDoc (Section 4). Done 2026-11-13. | A web page with a table gives an AsciiDoc table in the raw text, and the table budget counts it. | MD-1 | M |
| MD-5 | Vision prompts and Telegram messages without Markdown (Section 4). Done 2026-11-13. | A bot message with `_` and `*` in a title arrives as text. | — | S |
| MD-3 | Markdown export (Section 5). Backend done 2026-11-13; the context menu item comes with FP4-5. | An end-to-end test exports an AsciiDoc document as a Markdown file (`tests/test_e2e_markdown_export.py`). | MD-1, MX1 to MX3 | M |

Documentation: `ENVIRONMENT_VARIABLES.md` (`CLAIRE_RENDER_FORMAT`), `COMMANDS.md` (`--format`), `API_HTTP_CONTRACT.md` and `openapi.yaml` (export route of MD-3).

---

## 8. Decided Items (owner, 2026-11-13)

All items are decided: MX1 (a), one document from the context menu of `/syndication`; MX2 (b); MX3 with the front matter; MX4 (a), the `.md` source stays because ingestion must read it. The table keeps the options for reference.

### 8.1 Options

| ID | Item | Options | Proposal | Needed before |
| :--- | :--- | :--- | :--- | :--- |
| MX1 | Where the export starts | (a) One document: a button in the document view and a route. (b) A theme: an archive of Markdown files. (c) Both. | (a) first, then (b) | MD-3 |
| MX2 | Conversion from AsciiDoc to Markdown | (a) From the compiled HTML with an HTML-to-Markdown library (new dependency). (b) A small converter of the AsciiDoc subset that ingestion writes (no dependency). (c) An external tool such as Pandoc (system dependency in the image). | (b): the ingestion prompt limits the AsciiDoc syntax; constructs that the converter does not know go out as text **[unverified]** until a test covers the syntax of the prompt | MD-3 |
| MX3 | Content of the file | Body only, or body with a header (title, source URL, date, summary, tags as front matter) | Body with a YAML front matter header | MD-3 |
| MX4 | Markdown source files (`.md`, `.markdown`) | (a) Read them as plain text, as now (Claire does not interpret Markdown). (b) Refuse them. | (a): the file is a source, not Markdown that Claire writes | MD-4 |
