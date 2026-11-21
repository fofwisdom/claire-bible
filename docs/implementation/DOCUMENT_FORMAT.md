# Document Body Format Implementation Reference

Basis: commit d15ae8e0 (2026-11-16). Readers: agents that change the body of a document, its rendering, or its export. Design principles: [`ASCIIDOC_ENHANCEMENT_DESIGN.md`](../design/ASCIIDOC_ENHANCEMENT_DESIGN.md) (the AsciiDoc body) and [`MARKDOWN_RETIREMENT_DESIGN.md`](../design/MARKDOWN_RETIREMENT_DESIGN.md) (RD22 to RD25, MX1 to MX4).

This document tells what the code does now. Use file paths and function names as anchors; find them with `grep -n "<name>" <file>`. Evidence tags: **[code]** (examined in the source at the basis commit), **[decision]** (owner decision), **[unverified]**.

## 1. One Body Format: AsciiDoc

The readable body of a document (`documents.detail`) is AsciiDoc. No other body format exists.

- **[code]** `Settings.render_format` (`CLAIRE_RENDER_FORMAT`) accepts `adoc` and `asciidoc`, and stores `adoc`. The old values `md` and `markdown` do not stop startup: `_parse_render_format` in `src/claire/config.py` writes one warning and uses `adoc`. Other values fail validation.
- **[code]** The `--format` option of `ingest`, `reingest`, `reextract`, `backfill-detail`, `reprocess`, `regenerate`, `format-status`, `format-migrate`, and `video-reprocess` has the one choice `adoc` (`src/claire/cli.py`).
- **[code]** `documents.detail_format` has the column default `'adoc'`. An empty value reads as `adoc` (`get_document_detail_format` in `src/claire/store/db.py`). `set_document_detail` always writes `adoc`; its `format` argument is kept for the callers.
- **[code]** `format-status` and `format-migrate` still exist. They count and select bodies whose `detail_format` is not `adoc`, or bodies that are missing (`documents_needing_detail_format`).
- **[code] [decision]** RD25: the step `_purge_markdown_bodies` in `src/claire/store/db.py` runs in `_migrate` one time. It sets `detail` and `detail_html` to NULL and `detail_format` to `adoc` for each document with `detail_format` `md` or `markdown`. The document, its raw text, its extraction, and its graph stay. The key `markdown_bodies_purged` in `meta` stops a second run. `claire backfill-detail` writes a missing body again in AsciiDoc.

## 2. Body Authoring by the LLM

- **[code]** `render_detail_prompt` in `src/claire/extract/prompts.py` always calls `render_detail_prompt_adoc`. The prompt asks for a Korean AsciiDoc document with these rules:
  - Pure AsciiDoc. No Markdown syntax. A thematic break is `'''` only. Strong is `*x*`, emphasis is `_x_`, highlight is `#x#`. A link is `https://example.com[text]`. Quotes and code use `[quote]` and `[source,lang]` blocks.
  - A `[quote]` block holds only a direct quote: a sentence of the source in its source language. A Korean translation goes in the next paragraph. A translated, summarized, or paraphrased statement goes in a paragraph or a `[NOTE]`. The attribution is the person or work that said or wrote the sentence, never a concept label. A citation mark of the source author (for example `(Smith, 2007)`) does not become the attribution, and the citation marks of different sentences do not merge into one attribution. The attribution has no comma, because a comma separates the attribution and the citation (`[quote, attribution, citation]`).
  - Written style (`~한다`, `~이다`). Sections with `==` and `===`. Lists with `*`.
  - Code with callouts (`<1>`). `[NOTE]` or `[IMPORTANT]` in one or two places only. Tables (`|===`) keep all values of the source.
  - Highlight for one or two phrases only. Proper names stay in their source form.
  - Math in `stem:[...]` or `$...$`, block math in `[latexmath]` with `++++`, or `$$...$$`.
  - Anchors `[#id]` on main sections of long documents, and cross references `<<id, label>>`.
  - No facts that the source does not state. A section that truncation cut is left out.
  - No author line under the document title.
  - The length hint for merged documents changes the prompt. The prompt can take a focus text, but no entry point gives one (focus seal, [`DATA_LIFECYCLE.md`](DATA_LIFECYCLE.md) §4).
- **[code]** `sanitize_rendered_detail` removes an author line under the document title and the attribution of a video channel in `[quote, ...]`.
- **[code]** `set_document_detail` removes a leading link to the original (`remove_leading_original_link`) before it stores the body.
- **[code]** Summaries are plain text. The summary prompts ask for no AsciiDoc and no Markdown, and `clean_plain_summary` removes marks that leak.

## 3. Rendering

### 3.1 Ahead-of-time HTML on the server

- **[code]** `src/bible/adoc/aot.py` converts AsciiDoc to semantic HTML without a dependency. `render_to_html` calls `render_adoc_to_html`; its `format` argument has no effect. Core callers call it through `claire.core.ports.get_document_renderer().render_to_html`.
- **[code]** The converter escapes HTML first. Then it converts:
  - inline marks: strong, emphasis, highlight, links, code, sub- and superscript, math spans (`stem:`, `latexmath:`, `asciimath:`, `$...$`, `\(...\)`) to `<span class="math inline">`, and cross references (`<<id>>`, `<<id, label>>`, `xref:id[label]`) to `<a class="xref">`;
  - blocks: sections, quote, admonition, source with callout badges (`conum`) and callout lists (`colist`), math blocks (`[latexmath]`, `[stem]`, `[asciimath]`) to `<div class="mathblock">`, tables with column and row spans and alignment, nested lists, images;
  - anchors `[#id]`, `[[id]]`, and anchors in a section title, to `id` attributes;
  - the thematic break: only a line of three or more `'` becomes `<hr>`. A line `---` stays text.
- **[code]** Sub- and superscript follow Asciidoctor: `~x~` and `^x^` mark text without whitespace only. A tilde with a digit on both sides is a range (`15~29세`, `2014~19년`), not a subscript mark (`_SUBSCRIPT_RE`). `adoc_parser.js` has the same rules.
- **[code]** `set_document_detail` stores `detail_html` from `render_to_html` when the caller gives no HTML. It also sets the status of the presentation of the document to `stale` (see [`PRESENTATION.md`](PRESENTATION.md)).
- **[code]** `get_document_detail_html` renders and stores `detail_html` on read when it is empty. `recompile_all_detail_html` (CLI `recompile-html`) renders all bodies again with the current converter.
- **[code]** `RENDERER_REVISION` in `src/bible/adoc/aot.py` identifies the converter output. When it is not the value of the key `aot_renderer_revision` in `meta`, `_recompile_stale_detail_html` in `_migrate` renders `detail_html` of all stored bodies again, one time. Increase it when a change of the converter changes stored HTML.
- Tests: `tests/test_adoc_render.py`.

### 3.2 Display in the browser

- **[code]** The reader (`src/bible/static/js/reader.js`) and the share page (`src/bible/templates/share.html`) insert `detail_html` after `DOMPurify.sanitize`. When `detail_html` is empty, they render `detail` in the browser with `renderContent` of `src/bible/static/js/renderers/adoc_parser.js`. `renderContent` renders AsciiDoc only. The summary also goes through `renderContent`.
- **[code]** The pages load KaTeX (math) and DOMPurify from `unpkg.com` with subresource integrity hashes. The browser does not parse AsciiDoc with Asciidoctor.js.
- **[code]** The `usercontent` host renders a document at `/render/doc/{id}` (`src/claire/api/usercontent.py`). It changes the media references in the stored HTML at render time with `transform_media_urls_in_html` (`src/bible/adoc/media_transform.py`, called through `DocumentRendererPort`).
- **[code]** Graph view labels and previews remove Markdown, AsciiDoc, LaTeX, and HTML marks (`sanitize_preview_text` in `src/bible/web/metadata.py`). This removes marks of old text; it writes no Markdown.

## 4. Raw Text from Fetchers

- **[code]** Fetchers write tables of the source as AsciiDoc tables with `adoc_table` and `adoc_cell` of `src/claire/ingest/fetchers/adoc_text.py`. Users: `web.py`, `odt.py`, and the project plugins `claire.uttu` and `claire.hoyolab` (through `claire.plugin_sdk`). The HoYoLAB fetcher (`format_post_to_text` in `plugins/ingest_adapter/claire.hoyolab/hoyolab.py`) writes AsciiDoc sections.
- **[code]** The vision prompts (`src/claire/extract/vision/gemini_vision.py`) ask for a transcript in AsciiDoc.
- **[code]** `src/claire/extract/table_budget.py` finds AsciiDoc, Markdown, and HTML tables. The Markdown pattern stays for old raw text and for Markdown source files.
- **[code] [decision]** MX4: the text file fetcher (`textfile.py`) reads `.md` and `.markdown` files as plain text. Claire does not interpret the Markdown of a source.
- **[code]** The Telegram bot sends messages with `parse_mode="HTML"` and escaped values (`src/claire/telegram_bot.py`).

## 5. Markdown Export

This is the only place where Claire writes Markdown (RD24) **[decision]**.

- **[code]** Route: `GET /document/export?id=<document ID>&format=markdown` in `src/claire/api/server.py` (`document_export_route`). Access rule: `read`, the rule of `GET /document`. `format` is `markdown` (also accepted: `md`; default `markdown`). Another value gives 400 `unsupported_format`. No `id` gives 400. An unknown document gives 404. An anonymous request does not find hidden documents.
- **[code]** The answer is `text/markdown; charset=utf-8` with `Content-Disposition: attachment`. The ASCII file name is `claire-<id>.md`; the UTF-8 file name is the title (120 characters at most) with `.md`. The route writes nothing in the database.
- **[code]** Conversion (MX2): `adoc_to_markdown` in `src/bible/adoc/markdown_export.py`, without a dependency. It converts section titles, strong, emphasis, highlight (to strong), links, images, lists, tables (GFM), source, literal, quote, admonition, and sidebar blocks, thematic breaks, anchors, and cross references. An unknown construct goes out as text.
- **[code]** File (MX3): `document_markdown` writes a YAML front matter header (`title`, `source`, `date`, `summary`, `claire_document_id`), then the title as a level 1 heading when the body does not start with one, then the body.
- **[code]** User interface (MX1): in `/syndication`, the article menu of the reader and the context menu of an item of the knowledge base view have the command `export-md` (`src/bible/templates/syndication.html`, `src/bible/static/js/syndication_live.js`). The command opens the export route with the theme of the view.
- Test: `tests/test_e2e_markdown_export.py`.
