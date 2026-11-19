# Web UI: Workspace, Home Overview, Context Panel, Reader, and Search

Basis: commit d15ae8e0 (2026-11-16) · Evidence tags: [`PRINCIPLES.md`](../PRINCIPLES.md) F1 ([code], [measured], [decision], [unverified])

This document tells how the web workspace works now. The design principles are in [`HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`](../design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md). Other current documents:

- Code positions and known limits of the list pane, the home view, and the context panel: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §1. This document does not repeat them.
- Graph engine (layout, renderer, input, camera): [`GRAPH_ENGINE.md`](GRAPH_ENGINE.md).
- Interface language and catalogs: [`I18N.md`](I18N.md).
- Routes and fields: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md), [`openapi.yaml`](openapi.yaml).
- Terms (목록, 문맥 패널, and others): [`GLOSSARY.md`](GLOSSARY.md).

---

## 1. Files

| File | Content |
| :--- | :--- |
| `src/claire/ui_chrome.py` | Single-source builder for the shared application bar (`render_appbar_html()`), command palette (`render_cmdpalette_html()`), and ingestion modals (`render_ingest_modals_html()`). Future routes (`themes`, `kb`, etc.) must use this module instead of duplicating chrome HTML. |
| `src/claire/templates/partials/common_appbar.html` | Shared `<header class="appbar">` partial rendered across the workspace, `/syndication`, and the console (`/settings/*`, `/admin/*`). |
| `src/claire/templates/partials/common_cmdpalette.html` | Shared `<dialog id="cmdpalette" closedby="any">` markup rendered on every page. |
| `src/claire/templates/partials/common_ingest_modals.html` | Shared centered modal dialogs `#modal-ingest`, `#modal-reingest`, and `#modal-regen`. |
| `src/claire/templates/index.html` | Markup of the workspace: expandable common chrome markers, list pane, center views, context panel, `#modal-dedup` / `#modal-purge`, bottom bar. |
| `src/claire/static/css/appbar.css` | Shared rules of the application bar, popover menus (`#pop-ingest-menu`, `#pop-hamburger-menu`), command palette dialog, and `.claire-dialog` modals. |
| `src/claire/static/css/workspace.css` | Grid, list pane, context panel, home view, ingest cockpit, narrow screens. |
| `src/claire/static/css/reader.css` | Reader text and the entity mark highlight. |
| `src/claire/static/js/appbar.js` | Shared `ClaireAppBar`: theme toggle, `closedBy` backdrop close fallback, native popover & fallback toggle controller, `#palettetrigger` binder. |
| `src/claire/static/js/ingest_dialogs.js` | Shared `ClaireIngestDialogs`: dropdown dispatcher for `[data-ingest-cmd]` (`ingest`, `reingest`, `regen`) and unified modal controllers. |
| `src/claire/static/js/app.js` | Workspace state, home entry, lists, context panel, home cards, local graph, workspace dialog adapters (`openIngest`, `openReingest`, `openRegen`, `openDedup`, `openPurge`), palette configuration. |
| `src/claire/static/js/reader.js` | `setCenterView()`, the reader, `docMetaHtml()`, entity marks. |
| `src/claire/static/js/command_palette.js` | `ClaireCommandPalette`, shared by the workspace, `/syndication`, and the console. |
| `src/claire/static/js/icons.js` | `ClaireIcons`: line icons (Section 9). |
| `src/claire/graphview.py` | Builds the workspace and `/syndication` bundles using `ui_chrome.py` and resolves translation slots for each locale ([`I18N.md`](I18N.md) §4). |

---

## 2. Application Bar

**[code]** Every main page (workspace `/`, `/syndication`, console `/settings/*` and `/admin/*`, and future routes such as themes or kb) renders the unified header from `src/claire/ui_chrome.py` (`src/claire/templates/partials/common_appbar.html`) styled by `src/claire/static/css/appbar.css` and controlled by `src/claire/static/js/appbar.js`. It has three parts:

1. Start (`.bar-brand-group`): brand link (`Claire Bible`), page-specific theme picker, workspace list toggle (`#listtoggle`).
2. Center (`.appbar-center`): always includes the unified command palette trigger button (`#palettetrigger`, keyboard shortcut `/`) inside `.appbar-palette-slot`, followed by page-specific center content (workspace view tabs `#centertabs` and `#streamtabs`, or syndication stream title, or console title/badge). The bar has no text `<input>`.
3. End (`.appbar-end`): optional page actions to the left (`#addbtn` ingest dropdown on workspace & `/syndication`, `#btn-note-new` on `/syndication`; the bar has no context panel button, see §3.3), light/dark switch (`#themebtn`), and then the three shared rightmost icons ordered strictly from the rightmost edge inward:
   - Hamburger menu button (`#hamburger-btn`, rightmost edge) opening `#pop-hamburger-menu` (`popover="auto"`, `focusgroup="menu nomemory"`)
   - Syndication link (`#syndlink`, hidden on `/` until owner permission check passes)
   - User Home link (`#homelinkbtn`, left of `#syndlink`)

- **Top bar omissions and Hamburger contents**: `#authstate` and a top-level sign-out button are removed from `.appbar`. Console navigation (`#consolelinkbtn`: `/admin` with `wrench` icon for admin, `/settings` with `settings` icon for non-admin owner; server-stripped on anonymous/read-only) and Sign out (`#menu-logout-form` → `POST /logout`) live inside `#pop-hamburger-menu` under `<hr id="menu-account-sep"/>`, below any page-specific hamburger sections (`#graph-section` on workspace, view/list-style items on `/syndication`).
- **Ingest dropdown and unified modal popups**: `#addbtn` toggles `#pop-ingest-menu` (`popover="auto"`) with three actions (`ingest`, `reingest`, `regen`). Selecting any option (or `#dedupbtn` / `#purgebtn` in workspace's `#pop-hamburger-menu`) opens centered `<dialog closedby="any">` popups (`#modal-ingest`, `#modal-reingest`, `#modal-regen`, plus `#modal-dedup` and `#modal-purge` on workspace) instead of rendering forms inside `#detailpane`.
- The workspace brand link and `#homelinkbtn` use `<a href="./">`. A plain left click calls `enterHome({reason:'brand'})` or `enterHome({reason:'nav'})`. Ctrl, Cmd, or a middle click uses the browser default (a new tab is a new page load).
- Popovers use native `commandfor` / `command="toggle-popover"` and CSS anchor positioning (`position-area: block-end span-inline-start`) where supported, with `appbar.js` providing a click/viewport-clamped positioning fallback on older browsers. The light and dark switch writes `localStorage` key `claireTheme` on every page.

---

## 3. Grid, List Pane, and Context Panel

### 3.1 Grid

**[code]** `#wrap` is a CSS grid. Breakpoints in `app.js`: `mobileMQ` is `(max-width:720px)`, `compactMQ` is `(max-width:1100px)`.

| Width | Columns | Context panel |
| :--- | :--- | :--- |
| 1101px and more | `var(--list-col,280px) minmax(420px,1fr) var(--ctx-col,var(--detail-width,360px))`. `body.list-closed` sets the list column to 0; `body.ctx-closed` sets the context column to 0. | A grid column of 360px (`--detail-width` in `theme.css`), only when it is open. |
| 721px to 1100px | `280px minmax(0,1fr)` (list column 0 when closed) | A drawer above the center pane. |
| 720px and less | One column | A sheet from the bottom. The list pane is a drawer from the left side. |

The grid columns have a 0.2s transition. It stops with `prefers-reduced-motion`.

### 3.2 List Pane

**[code]** `aside#docs` has one list at a time: documents (`#doclist`) or entities (`#entlist`).

- `listMode()` returns the list. The default is entities in the graph view and documents in all other views. The switch `#listswitch` sets `listOverride`. `setCenterView()` clears `listOverride`, thus a selection of the user stays until the next change of the view.
- The filter field `#q` (in `#listfilter`) filters the list that shows. Documents: `onDocqInput()` calls `renderDocs()`, which requests the first page of `GET /documents` with the text when the typing stops for 300ms (`DOC_FILTER_DELAY`). Entities: `onSearchInput()` highlights the matching entities in the graph after 550ms. A change of the list clears the field (`resetListFilter()`). Escape clears it.
- Enter in the document filter calls `doDocServerSearch()`: `POST /search` with `limit: 20`, `mode: 'hybrid'` for a signed-in scope and `'fts'` for an anonymous visitor, and `summarize` only for the owner. The result replaces the document list. An `AbortController` and the counter `currentSearchSeq` drop a late response. The server makes the last decision about the mode and the limits (`server.py`, `POST /search`).
- `toggleListPane()` opens and closes the pane. The state is in the account-scoped `localStorage` key `claireListOpen` (`getAccountStorage()` prefixes each key with the account key).

### 3.3 Context Panel

**[code]** `aside#detailpane` holds `#panel`. `hasContext()` is true when:

- a form or a result is pinned in the panel (`contextPinned`: ingest, regenerate, research, synthesis), or an entity is selected, in each view; or
- the view is not `home` and a document is open, or the view is `graph`.

`isContextOpen()` is `hasContext()` and the user state `contextUserOpen` (account-scoped key `claireContextOpen`). A `MutationObserver` on `#panel` calls `syncWorkspaceLayout()` when the panel content changes.

The panel has one close button at each width: `#detailclose` in its own head (`closeContextPanel()`). The header bar has no context button (owner request 2026-11-19: three close places became one). The open buttons call `openContextPanel()` and never close:

- `#rctxbtn` in the head bar of the reader (`#reader .rtools`), in the place of the old close button of the reader (removed 2026-11-18: the reader is a center view, not a modal).
- `#gctxbtn` on the graph canvas, below the top bars of the graph view (in the zero-height row `#gctxrow`).
- `#botnav-context` in the bottom bar (720px and less). Its `aria-expanded` shows the open sheet; a click on it while the sheet is open does nothing.

`syncWorkspaceLayout()` hides `#rctxbtn` and `#gctxbtn` while the panel (the drawer on a narrow screen) is open or no context exists. When the panel opens from an open button, the focus goes to `#detailclose`; when the panel closes with the focus in it, the focus goes to the open button of the view (`contextOpenButton()`). Limit: above 720px, the presentation, decision stream, and matrix views have no open button (HD7: they stay as they are). In those views the panel opens with an entity selection or a pinned form, and the user state is the same for all views.

| Context | Content of `#panel` |
| :--- | :--- |
| Document open | Three tabs (Section 5) |
| Entity selected | `renderPanel()`: entity detail. A back link goes to the document context when a document is open. |
| Graph view without a selection | `renderGraphInsights()`: the three lists of the knowledge map card (Section 4.4) |

On a narrow screen an open drawer or sheet gets `role="dialog"` and `aria-modal="true"`, and `#centerwrap` gets `inert`. On a wide screen the same elements have no dialog role.

---

## 4. Home Entry and Home View

### 4.1 Center Views

**[code]** `setCenterView(mode)` (`reader.js`) knows `home`, `reader`, `graph`, `presentation`, `stream`, and `matrix`. It writes `document.body.dataset.centerView`.

`initialCenterView()` selects the first view: `reader` on a narrow screen or in the installed application (`shouldDefaultToReader()`), `graph` for a link with `?node=`, else `home`.

### 4.2 Home Entry

**[code]** `enterHome({reason})` is the one function for the brand link, the Home button, the bottom bar, the palette command, and a theme switch:

1. It calls `resetWorkspaceState()` with `homeEntryActive` set, thus `closeReader()` does not change the view to the graph on the way.
2. It sets the `home` view, `document.title = HOME_TITLE` (catalog key `home.title`), and `history.replaceState({pane:'graph', modal:null, view:'home'}, …)`. The URL has no query: the path selects the knowledge base (CT23).
3. It reads `GET /stats` (`cache: 'no-store'`) and calls `resolveHomeView(stats, activeThemeId)`:
   - `'job'`: an active job of this theme. The view changes to `matrix` (Section 8).
   - `'home+banner'`: an active job of a different theme. The home view and an information banner with a link to that theme.
   - `'home'`: all other cases, also a failed `GET /stats`.
4. For `home` it calls `initGraph()` (or `applyView()`), because the cards use the graph data. The graph pane is hidden, thus the engine does not draw.

`resetWorkspaceState()` clears the search, the selection, the path, the reader, the drawers, and the filters, and sets the minimum degree from the node count (0, 1, 2, or 5 at 80, 180, and 400 nodes). It does not clear the synthesis set. `resetHome()` is an alias of `enterHome({reason:'brand'})`.

### 4.3 Home Cards

**[code]** `renderHome()` fills `#homewrap`. The center pane is a size container (`container-type:inline-size`); container queries give the card grid its columns. A card without data is hidden. No card shows a placeholder number.

| Element | Content | Source |
| :--- | :--- | :--- |
| `#home-facts` | Theme label, documents, entities, relations, time of the last ingestion | `GET /stats`; `fetched_at` of the first document row |
| `#home-flow` (owner only) | Three links to `/syndication?stage=` with `collect`, `ingest`, or `publish`, with counts | `GET /api/feeds`, `GET /api/federation/following?limit=1`, `GET /api/federation/status`, `GET /api/federation/timeline?limit=1`, `GET /stats`. A route that fails gives no count. |
| `#home-recent` | First five visible documents: source type, title, time, entity count | Document list; the entity count is the number of graph nodes whose `sources` has the document |
| `#home-decisions` | First four decisions with the stage class | `GET /resolution/decisions?limit=4`; only when the decision stream switch of the theme is on |
| `#home-map` | Knowledge map: mini map and three lists (Section 4.4) | `GET /graph` |
| `#home-maint` (owner only) | Rows for `missing_detail_docs` and `mismatched` above 0, each with the command `./cb-manuscript app format-migrate --apply` and a copy button | `format_status` of `GET /documents` |

A click on a document row opens the reader. A click on an entity row calls `loadNode()` and opens the context panel; the view does not change.

### 4.4 Knowledge Map

**[code]** `getGraphInsightLists()` gives three lists of five entities each:

- Hubs: highest `degree` first.
- Bridges: highest `sources.length` first, then `degree`. An entity without a document is not in the list.
- Added in the last 7 days: `created_at` (from `GET /graph`) in the last 7 days, newest first. The column is hidden when the list is empty.

The mini map (`drawHomeMiniMap()`) is one static drawing on a 2D canvas. `computeHomeMiniLayout()` runs the engine `Simulation` (`setTopology()`, `snapToHomes()`, `warmup()`) without a `Network` instance and caches the result for each theme, node count, and edge count. A click opens the graph view.

---

## 5. Document Context

**[code]** `loadDocPanel()` calls `renderDocPanel()`, which renders the document context in three tabs (`DOC_CTX_TABS`: `links`, `entities`, `source`). The tabs are a `tablist`; a `keydown` handler on `#panel` moves the selection with the arrow keys (`switchDocCtxTab()`).

| Tab | Content |
| :--- | :--- |
| Links | Local graph (`renderLocalGraphHtml()`) and up to five documents that share entities with the open document (`computeSharedDocs()`), with the count |
| Entities | All entities of the document (`docNodes()`, highest degree first) with type, first observation, and degree |
| Source | Source text facts (URL, more sources, fetch time, source type, author, publication time, character counts, notices, raw file link), the provider and the model of the latest extraction, and the resolution entries for each stage class |

Local graph: an inline SVG. Center: the document. Ring 1: up to 24 entities of the document, highest degree first. Ring 2: up to 12 entities of other documents that have a relation with a ring 1 entity, from the edges of `GET /graph`. A text gives the number of entities that are not shown. Before `GET /graph` is loaded, the drawing has ring 1 only. A hover on an entity calls `setEntityHoverHighlight()`, which marks the same entity in the lists, the cards, and the reader marks.

Stage classes (`resolutionStageBadge()`): `exact_match`, `alias_match`, `acronym_match` give "rule" (solid); `high_confidence_vector` and `borderline_llm_judge` give the class with the score (dashed); `no_match` gives "new entity" (solid). Another stage shows its name as text.

---

## 6. Reader

### 6.1 Meta Line

**[code]** `docMetaHtml(dc, options)` (`reader.js`) makes the first line below the title (`p.docmeta`):

- Left part: links to the source. "Open the source" when `url` is a web URL; "open the raw file" (`/document/raw?id=`) when `has_raw_file` is true and the user can ingest or `can_access_raw` is true; "open the transcript" for an STT document; the presentation PDF link.
- Right part (`.docmeta-tags`): tags of the ingestion, all with the classes `prov det`: parser fallback, PDF encoding flaw, scanned PDF, bundle (CC×PDF or STT×PDF), STT, CC, image transcription, the source badge, and the model badge.
- Source badge: `data-source-badge="full"` when raw text is stored; `data-source-badge="partial"` with the two character counts when `raw_truncated` is true. The reason of the cut is the hover text and `data-trunc` (`references`, `appendix`, `appendix-references`, `stt`, `limit`).
- Model badge (`.model-badge`): `extraction_model` (or `extraction_provider`) of `GET /document`, to the right of the source badge.
- The meta line has no author and no publication date. The Source tab of the context panel shows them (Section 5).

The share page keeps its own truncation tag, because it has no source badge.

### 6.2 Entity Marks

**[code]** `applyEntityMarks()` (`reader.js`) marks the names of the entities of the open document in the reader text with the CSS Custom Highlight API (`CSS.highlights`, highlights `claire-entity` and `claire-entity-hover`, style `::highlight(claire-entity)` in `reader.css`). The document tree does not change.

- Candidates: names and aliases of `nodes` of `GET /document`. The identifier is not a candidate. A name of fewer than 2 characters is not a candidate. A name of 2 or 3 ASCII letters or digits must match with the same case; other names match after `toLowerCase()`.
- A candidate string that belongs to two entities gets no mark. Longest candidate first; an overlapping match is dropped. An ASCII letter or digit directly before or after a match blocks it.
- Text nodes: inside `p`, `li`, `td`, `th`; not inside headings, `code`, `pre`, `a`, `.katex`.
- Limits: no marks when the text has more than 400,000 characters or the document has more than 300 candidate names. `#rlegend` then says that the marks are off.
- No marks without `CSS.highlights`, on the share page, or when `localStorage` key `claireEntityMarks` is `'0'`.
- A click on a mark (position from `caretPositionFromPoint()` or `caretRangeFromPoint()`) selects the entity with `GET /node`. It starts no write.

---

## 7. Command Palette

**[code]** `<dialog id="cmdpalette" closedby="any">` opens with `showModal()`. The `/` key opens it when the focus is not in a text field and no dialog is open (Ctrl+K is not bound). `#palettetrigger` in the shared `.appbar-center` opens it on every page, and `#palettebtn` inside `#pop-hamburger-menu` also opens it on the workspace. A browser without `closedby` gets a click handler in `appbar.js` / `command_palette.js` that checks the dialog rectangle.

Rows of the workspace configuration (`ClaireCommandPalette.init()` in `app.js`):

- Commands: home, graph, ingest, light theme, dark theme.
- Documents: up to 10 from the loaded list, then up to 10 from `GET /documents` with `q` (the keywords `is:missing-detail` and `is:mismatched` set the `detail` filter).
- Entities: the graph nodes in the client.
- One row that runs `runFullTextSearch()` with the input text: it selects the document list, puts the text in `#q`, and calls `doDocServerSearch()`.

Each row calls a function that exists. `/syndication` loads the same module with its own configuration.

---

## 8. Ingest Cockpit and Active Jobs

**[code]** Server: each ingest, re-ingest, and regenerate runs in `active_job()` (`src/claire/ingest/active.py`; kinds `ingest` in `ingest/pipeline.py`, `reingest` and `regenerate` in `ingest/service.py`). Each job has a file `data/active_jobs/<job_id>.json`; `active_ingest.json` is a copy of the primary job for older clients. Limits: 4 slots (`_MAX_EXPENSIVE_JOBS`), a job without an update for 600s is stale, a completed job stays for 30s. `GET /stats` gives `active_jobs` and `active_ingest`, filtered by theme access ([`THEMES.md`](THEMES.md) §4.3).

**[code]** Client: the `matrix` view (`#matrixwrap`) is the ingest cockpit. It has the status, theme, and kind badges, the target document, the percentage and the elapsed time, a stepper of the five stages `fetch`, `extract`, `resolve`, `detail`, `done` (`#cockpit-desktop-stepper`; `#cockpit-mobile-stepper` below 768px), a telemetry bar with the progress message of the server, and the heatmap table (`#matrix-table`) when `heatmap_matrix` exists. `startFastIngestPolling()` polls `GET /stats` every 1.5s during a job. The progress messages come from the server and are not catalog text ([`I18N.md`](I18N.md) §6).

---

## 9. Line Icons

**[code]** All icons of the web UI are inline SVG line icons with `stroke: currentColor`. Markup uses an empty `<span data-icon="name">`; `ClaireIcons.hydrate()` fills it. Scripts use `ClaireIcons.svg(name)`; the console renderers use `icon_html(name)` (`src/claire/api/accounts_routes/common.py`). The view navigation and the bar buttons of `index.html` have inline `<svg class="lineicon">` elements. Exceptions and the test: [`PRINCIPLES.md`](../PRINCIPLES.md) G1 (`tests/test_ui_line_icons.py`).

---

## 10. Tests

| Kind | Test |
| :--- | :--- |
| End-to-end | `e2e/workspace.spec.js` (home view, document context, local graph, entity marks, palette, narrow screens), `e2e/app_bar.spec.js`, `e2e/line_icons.spec.js`, `e2e/workspace_i18n.spec.js` |
| Markers and runtime | `tests/test_graphview.py`, `tests/test_graphview_runtime.py` |
| Active jobs | `tests/test_active_job_slots.py`, `tests/test_active_jobs_theme_isolation.py` |

Tests that fail before a change: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §3.
