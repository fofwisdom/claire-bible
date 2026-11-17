# Presentation Implementation Reference

Basis: commit d15ae8e0 (2026-11-16). Readers: agents that change the slide presentations of documents. Design principles: [`LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md`](../design/LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md). Commands: [`COMMANDS.md`](COMMANDS.md) §3.11.

This document tells what the code does now. Use file paths and function names as anchors; find them with `grep -n "<name>" <file>`. Evidence tags: **[code]** (examined in the source at the basis commit), **[decision]** (owner decision), **[unverified]**.

## 1. Overview

A presentation is a reveal.js slide deck of one document. It has two stages:

1. **Composition.** The LLM writes a new AsciiDoc deck with speaker notes from the body of the document. This stage costs LLM tokens.
2. **Compilation.** `asciidoctor-revealjs` converts the deck to one HTML file. This stage does not call the LLM.

**[code]** `PresentationService` in `src/claire/presentation/service.py` runs the two stages: `compose_presentation`, `compile_presentation`, and `get_or_create_presentation` (composition only when no valid deck exists, then compilation). The mechanical split of the body into slides does not exist; compilation without a composed deck fails.

## 2. Storage

- **[code]** Table `document_presentations` in `claire.db`, one row per `(document_id, kb_id)` (`src/claire/store/db.py`). Columns: the deck (`presentation_adoc`), `content_hash` (SHA-256 of the body at composition), `adoc_hash`, `cache_key` (SHA-256 of `adoc_hash:theme:transition`), `theme` (default `night`), `transition` (default `slide`), `slide_count`, `file_path`, `file_size`, `status`, `error_message`, `authoring_provider`, `authoring_model`, `prompt_version`, `compose_duration_ms`, `compile_duration_ms`, `created_at`, `updated_at`. Helpers: `save_document_presentation`, `save_presentation_adoc`, `get_document_presentation`, `get_presentation_adoc`, `update_document_presentation_status`.
- **[code]** Files: `<data_dir>/presentations/<document_id>.adoc` (the deck) and `<data_dir>/presentations/<document_id>.html` (the compiled page). `data_dir` comes from the theme settings; `get_settings_for_kb` changes only the database path, so all themes use the same folder.
- **[code]** `slide_count` is the count of `== ` and `=== ` lines plus one.

## 3. States

| Status | Meaning |
| :--- | :--- |
| `composing` | The LLM writes the deck. |
| `authored` | A deck exists, not compiled (`--no-compile`, or `PUT .../adoc` with `recompile: false`). |
| `compiling` | `asciidoctor-revealjs` runs. |
| `ready` | The HTML file exists. |
| `stale` | The body changed after the composition, or the deck is not valid for the current prompt. |
| `failed` | Composition or compilation failed. `error_message` has the scrubbed error. |

- **[code]** `set_document_detail` sets the presentation of the document to `stale` when it stores a new body.
- **[code]** A deck is valid only when it has `authoring_provider`, `presentation_adoc`, and `prompt_version` equal to `PRESENTATION_PROMPT_VERSION` (`pres-v2`, `src/claire/extract/prompts.py`). `get_presentation_metadata` reports `ready` with an invalid deck as `stale` and sets `is_ready`.
- **[code]** A request without a row reports `not_created`.
- **[code]** `document_detail` (`src/claire/store/queries.py`) gives `has_presentation` and `presentation_status` to the reader.

## 4. Composition

- **[code]** `compose_presentation_prompt_adoc` (`src/claire/extract/prompts.py`) asks for an AsciiDoc reveal.js deck (the prompt text is Korean; it names no output language):
  - one message per slide, with a conclusion sentence as the slide title;
  - a layout of headline, visual (image, table, quote, admonition, or code), and evidence;
  - at least two tables and two admonitions in the deck;
  - horizontal slides (`==`) for topics and vertical slides (`===`) for details;
  - `[.notes]` speaker notes on all slides;
  - the reveal.js header attributes, a 16:9 page of 1280 x 720;
  - no Markdown;
  - about `slide_budget - 2` to `slide_budget + 4` slides (at least 4).
- **[code]** Providers: `compose_presentation` in `GeminiProvider`, `AntigravityProvider`, `CodexProvider`, and the mock provider (`src/claire/extract/`).
- **[code]** The source text is `detail`, or `raw_text` when `detail` is empty. A document without both fails.
- **[code]** The provider is the provider of the account that owns the theme (`provider_context`). A member theme needs the tier feature `presentation` (`require_feature`); the effort is clamped (`clamp_effort`). See [`PROVIDERS.md`](PROVIDERS.md).
- **[code]** Concurrency: one composition at a time per process (`_PRES_SEMAPHORE`, a one-thread executor). Requests with the same `document_id:theme:transition` wait for the running job (`_PRES_IN_FLIGHT`).

## 5. Compilation

- **[code]** `prepare_presentation_adoc_for_compile` (`src/claire/presentation/preprocessor.py`) sets the reveal.js header attributes: theme, transition, `revealjs_center: false`, 1280 x 720, margin 0.04, PDF options, `highlight.js`, `icons: font`, `customcss` (`/static/css/reveal-claire.css`), and `revealjsdir` (`/static/vendor/reveal.js`). It changes `include::`, `sys::`, and `eval::` lines, and attributes such as `allow-uri-read` and `safe-mode`, to comments.
- **[code]** `compile_presentation_html` (`src/claire/presentation/converter.py`) runs `asciidoctor-revealjs` (or `asciidoctor -r asciidoctor-revealjs -b revealjs`) with the deck on standard input, safe mode `secure`, `allow-uri-read!`, a reduced environment, and a timeout of 30 seconds. At most two compilations run at the same time. The output goes to a temporary file, gets the HUD (Section 6), and replaces the final file.
- **[code]** `compile_presentation` returns the existing record without work when the deck, the theme, and the transition did not change, the status is `ready`, and the file exists.
- **[code]** Toolchain in the image (`Dockerfile`): `ruby`, gems `asciidoctor ~> 2.0.26` and `asciidoctor-revealjs ~> 5.2.0`. reveal.js 5.1.0 is in `src/claire/static/vendor/reveal.js/`. `claire presentation doctor` reports the compiler path (`find_asciidoctor_executable`).

## 6. Viewer

- **[code]** `inject_hud_toolbar` (`src/claire/presentation/hud.py`) adds a toolbar to the compiled page. The toolbar shows the title and the slide coordinates, and has buttons for speaker notes, overview, PDF (reveal.js `print-pdf`), link copy, and full screen. It hides itself until the pointer is near the top. It takes the light or dark theme from the parent page or from the system setting. In an `iframe`, or with `embed=true`, it is hidden. `sanitize_presentation_assets` corrects the reveal.js asset paths and the image paths, and adds `<base href="/">` and the favicon tags (`src/claire/presentation/favicon.py`).
- **[code]** `GET /p/presentation?id=<document ID>` or `?s=<share token>` (`presentation_page` in `src/claire/api/server.py`) serves the compiled file. The gate rule is `public`; `_resolve_presentation_target` finds the document by a share token (not hidden) or by ID in a theme that the request can access. The route never composes (`allow_compose=False`). When no compiled file exists, `render_unready_presentation_page` (`src/claire/presentation/views.py`) shows the status; while the status is `composing`, `compiling`, or `authored`, the page reloads every 4 seconds. A file without the current HUD gets it again before it is served.
- **[code]** The workspace (`src/claire/templates/index.html`, `src/claire/static/js/reader.js`) has the center tab `presentation`. It shows the page in an `iframe` with `embed=true`. When no deck exists, a writer sees a button that calls `POST /document/presentation/generate?wait=false` and then polls `GET /document/presentation` every 2.5 seconds for at most 120 seconds.
- **[code]** The share page (`src/claire/templates/share.html`) shows a link to `/p/presentation?s=<token>` when `has_presentation` is true.
- **[code]** The `usercontent` host also has `/p/presentation` (`src/claire/api/usercontent.py`). It serves the compiled file with a script nonce and changes the media references (`transform_media_urls_in_html`).

## 7. HTTP API

All routes are in `src/claire/api/server.py`; the access rules are in `src/claire/api/security.py`.

| Method and path | Access | Function |
| :--- | :--- | :--- |
| `GET /document/presentation?id=` | `read` | Record of the presentation, or `not_created`. |
| `POST /document/presentation/generate` | `owner` | `get_or_create_presentation`. Parameters in the query or in a JSON body: `id`, `slide_theme`, `transition`, `force`, `wait`. With `wait=false`: 200 with a `ready` record, or 202 and a background task. |
| `POST /document/presentation/compose` | `owner` | Composition, then compilation unless `no_compile`. Parameters: `id`, `focus`, `slide_budget`, `theme`, `transition`, `no_compile`. |
| `POST /document/presentation/compile` | `owner` | Compilation only. Parameters: `id`, `theme`, `transition`, `force` (default true). |
| `GET /document/presentation/adoc?id=` | `read` | The deck as JSON (`presentation_adoc`). |
| `PUT /document/presentation/adoc` | `owner` | Stores an edited deck (`authoring_provider` `manual`) and compiles it unless `recompile` is false. |
| `GET /p/presentation` | `public` | The viewer page (Section 6). |

- **[code]** `generate`, `compose`, `compile`, and `PUT .../adoc` also check the tier feature `presentation` of the theme (`_require_theme_feature`).

## 8. Per-Theme Switch

- **[code]** Each theme has `presentation_enabled` (`KnowledgeBaseInfo` in `src/claire/store/knowledge_base.py`, column `themes.presentation_enabled`, default 0). [`THEMES.md`](THEMES.md) tells where theme records are stored.
- **[code]** In single theme mode, theme 0 takes the value from `CLAIRE_PRESENTATION_ENABLED` (default `false`), not from its row (`_load_single_mode_theme0`). The default theme that the code builds (`_build_default_theme`) takes the same value.
- **[code]** Changes: `claire kb define ... --presentation` and `claire kb update <theme> --presentation | --no-presentation`, `POST /themes` and `PATCH /themes` with `presentation_enabled`, and the theme panel of the console (`src/claire/static/js/manage_panels.js`). `claire kb list` shows the value.
- **[code]** When the switch is off, every route of Section 7 refuses: the JSON routes give 403 `presentation_disabled`; `/p/presentation` gives 403 with the page of status `disabled`.
- **[code]** When the switch is off, the workspace hides the center tab (`syncPresentationFeatureUI` in `src/claire/static/js/app.js`). If the presentation view is open, it shows the empty banner with the message that the function is off and no generate button; it does not change the view (`reader.js`).
- **[code]** The CLI `claire presentation` does not check the switch. It is the operator path.
- **[code]** The `usercontent` route `/p/presentation` does not check the switch.

## 9. Tests

`tests/test_presentation.py`, `tests/test_e2e_presentation_resilience.py`, `tests/test_theme_presentation_flag.py`, `tests/test_e2e_theme_presentation_toggle.py`.
