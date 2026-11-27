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
| `src/bible/web/chrome.py` | Single-source builder for the shared application bar (`render_appbar_html()`), command palette (`render_cmdpalette_html()`), and ingestion modals (`render_ingest_modals_html()`). Future routes (`themes`, `kb`, etc.) must use this module through `WebUIRendererPort` instead of duplicating chrome HTML. |
| `src/bible/templates/partials/common_appbar.html` | Shared `<header class="appbar">` partial rendered across the workspace, `/syndication`, and the console (`/account/*`, `/settings/*`, `/moderation/*`, `/admin/*`). |
| `src/bible/templates/partials/common_cmdpalette.html` | Shared `<dialog id="cmdpalette" closedby="any">` markup rendered on every page. |
| `src/bible/templates/partials/common_ingest_modals.html` | Shared centered modal dialogs `#modal-ingest`, `#modal-reingest`, and `#modal-regen`. |
| `src/bible/templates/index.html` | Markup of the workspace: expandable common chrome markers, list pane, center views, context panel, `#modal-dedup` / `#modal-purge`, bottom bar. |
| `src/bible/static/css/appbar.css` | Shared rules of the application bar, popover menus (`#pop-ingest-menu`, `#pop-hamburger-menu`), command palette dialog, and `.claire-dialog` modals. |
| `src/bible/static/css/workspace.css` | Grid, list pane, context panel, home view, ingest cockpit, narrow screens. |
| `src/bible/static/css/reader.css` | Reader text and the entity mark highlight. |
| `src/bible/static/css/local_graph.css` | Shared styles for the inline radial local graph SVG (`.ctx-local-graph`) and hover dimming/highlighting across Workspace and Silvercord. |
| `src/bible/static/css/silvercord.css` | Styles for the unified Silvercord document shell: sticky top bar, mobile bottom bar, `hidden="until-found"` tab panels, anchor-positioned tab underline, desktop `360px` graph column, mobile modal `<dialog>` bottom sheet, and Opinions prototype. |
| `src/bible/static/js/appbar.js` | Shared `ClaireAppBar`: theme toggle, `closedBy` backdrop close fallback, native popover & fallback toggle controller, `#palettetrigger` binder. |
| `src/bible/static/js/ingest_dialogs.js` | Shared `ClaireIngestDialogs`: dropdown dispatcher for `[data-ingest-cmd]` (`ingest`, `reingest`, `regen`) and unified modal controllers. |
| `src/bible/static/js/local_graph.js` | Shared `ClaireLocalGraph`: `renderSvg()`, `bindInteractions()`, `setEntityHoverHighlight()`, and `clearEntityHoverHighlight()` used by both Workspace (`app.js`) and Silvercord (`silvercord.js`). |
| `src/bible/static/js/silvercord.js` | Unified Silvercord shell controller: `본문`/`의견` switching with scroll preservation and `#srail` sync, lazy `GET /silvercord/{token}/graph` fetch, desktop/mobile pull-out graph panel, CSS Custom Highlight API body entity marks, Entity Detail drill-down, and ActivityPub `의견` visual prototype. |
| `src/bible/static/js/app.js` | Workspace state, home entry, lists, context panel, home cards, local graph, workspace dialog adapters (`openIngest`, `openReingest`, `openRegen`, `openDedup`, `openPurge`), palette configuration. |
| `src/bible/static/js/reader.js` | `setCenterView()`, the reader, `docMetaHtml()`, entity marks. |
| `src/bible/static/js/command_palette.js` | `ClaireCommandPalette`, shared by the workspace, `/syndication`, and the console. |
| `src/bible/static/js/icons.js` | `ClaireIcons`: line icons (Section 9). |
| `src/bible/static/css/console_ui.css` | Canonical Console UI stylesheet for `/account/*`, `/settings/*`, `/admin/*`, and `/moderation/*`, plus `.console-shell` / `body[data-active-area]` scoped overrides for `.claire-*` classes (Section 11). |
| `src/bible/static/css/pipeline_flow.css` | Styles for the shared Proposal 4 (Gemini Neon) 5-stage optical pipeline component and `/about` public overview layout. |
| `src/bible/static/js/pipeline_flow.js` | Shared `ClairePipelineFlow` (`mount`, `mountAll`) interactive 5-stage pipeline controller mounted on `[data-pipeline-mount]` across `/about` and `/support/pipeline`. |
| `src/bible/static/js/console_ui.js` | `ClaireConsoleUI`: client HTML builder helpers for console sections (Section 11). |
| `src/claire/api/accounts_routes/console_ui.py` | Typed Python HTML builders for server-rendered console sections (Section 11). |
| `src/claire/api/accounts_routes/pipeline.py` | Shared Gemini Neon JSON serializer (`build_pipeline_view_data`, `render_pipeline_mount_html`) and `/support/pipeline` section builder (`support_pipeline_body`). |
| `src/claire/api/templates/console_shell.html` | Shared shell template for `/account/*`, `/settings/*`, `/admin/*`, and `/moderation/*` (Section 11). |
| `src/claire/api/templates/public_about.html` | Public `/about` page template hosting the shared Gemini Neon pipeline mount, KPI grid, philosophy cards, and supported sources table. |
| `src/bible/web/pages.py` | Builds the workspace, `/syndication`, and Silvercord standalone bundles with `src/bible/web/chrome.py` and gives pure slot-filling helpers (`localize_page`, `render_graph_page`, `render_syndication_page`, `render_shared_page`). |
| `src/bible/templates/share.html` | Markup of the unified Silvercord document page (`/silvercord/{token}`): header bar, `본문` and `의견` panels, mobile bottom bar, `#sc-desktop-aside`, `#sc-mobile-dialog`, `#sc-graph-panel-template`, `#srail`, and `#sttmodal`. |
| `src/claire_bible/web/pages.py` | Loads settings, themes, and locale data ([`I18N.md`](I18N.md) §4) into `render_graph_html`, `render_syndication_html`, and `shared_html`. |

---

## 2. Application Bar

**[code]** Every main page (workspace `/`, `/syndication`, console `/account/*`, `/settings/*`, `/moderation/*`, and `/admin/*`, and future routes such as themes or kb) renders the unified header from `src/bible/web/chrome.py` (`src/bible/templates/partials/common_appbar.html`) styled by `src/bible/static/css/appbar.css` and controlled by `src/bible/static/js/appbar.js`. It has three parts:

1. Start (`.bar-brand-group`): brand link (`Claire Bible`), page-specific theme picker, workspace list toggle (`#listtoggle`).
2. Center (`.appbar-center`): always includes the unified command palette trigger button (`#palettetrigger`, keyboard shortcut `/`) inside `.appbar-palette-slot`, followed by page-specific center content (workspace view tabs `#centertabs` and `#streamtabs`, or syndication stream title, or console title/badge). The bar has no text `<input>`.
3. End (`.appbar-end`): optional page actions to the left (`#addbtn` ingest dropdown on workspace & `/syndication`, `#btn-note-new` on `/syndication`; the bar has no context panel button, see §3.3), and then the shared controls ordered strictly from left to right:
   - Notification bell (`#notifybtn`, hidden until the list request of an owner session succeeds) with the count `#notify-count` and the list `#pop-notify` (`popover="auto"`): the last 20 notifications, "Mark all as read", and a link to `/account/notifications`. An item opens its entry address and becomes read ([NOTIFICATIONS.md](NOTIFICATIONS.md) §6).
   - User Home link (`#homelinkbtn`, `data-icon="home"`)
   - Syndication link (`#syndlink`, `data-icon="rss"`, hidden on `/` until owner permission check passes; `#menusyndlink` in `#pop-hamburger-menu` also uses `data-icon="rss"`)
   - Ingest Queue quick panel trigger (`#queuebtn`, `data-icon="layers"`, hidden until `GET /api/me/ingest-queue` succeeds for an owner session) with `#queue-count`, `#queue-live-dot`, and `#pop-queue` (`popover="auto"`)
   - Light/dark switch (`#themebtn`, directly left of `#hamburger-btn`)
   - Hamburger menu button (`#hamburger-btn`, rightmost edge) opening `#pop-hamburger-menu` (`popover="auto"`, `focusgroup="menu nomemory"`)

- **Ingest Queue quick panel (`#pop-queue`)**:
  - Data source and polling: for owner sessions (`logoutShown()` or `setLogoutVisible(true)`), `appbar.js` polls `GET /api/me/ingest-queue` (4s while `#pop-queue` is open or jobs are running or queued, 20s when idle, paused when `document.hidden`; `401`/`403`/`404` hides `#queuebtn` and stops polling). `ClaireIngestDialogs` (`ingest_dialogs.js`) calls `window.ClaireAppBar.refreshIngestQueue()` after successful ingest, re-ingest, or regenerate submissions. Failed items (`can_retry`) post to `POST /api/me/ingest-queue/{inbox_id}/retry` (`202` queued).
  - Unified header and view switcher: `.queue-head` shows the `layers` title, the unified 4-cell slot meter (`#queue-slot-meter`, `Slots {busy}/{max}` with 4 `.slot-cell` bars), `#queue-view-toggle` (directly left of `#queue-close-btn`, persisted in `localStorage` key `claireQueueViewMode`), and `#queue-close-btn`. Both views keep `#queue-slot-meter` identical and link the footer to `/support/pipeline` with the active/waiting count summary.
  - Option A (`data-queue-view="ledger"`, default): `focusgroup="tablist nomemory"` filter tabs (`All`, `Active`, `Agent`, `History`), grouped ordered lists (`.qa-row`) with 3px left semantic rails, `#1` queued ordinal pills, native `<progress max="5">` + 5-step stage pills (`Fetch`, `Extract`, `Resolve`, `Detail`, `Save`), retry action, and document link (`doc_url`).
  - Option C (`data-queue-view="compact"`): inline filter chips (`.qc-chip-btn`) and dense expandable rows (`.qc-row-btn[aria-expanded]`) that open inline details with the status message, ID, mode, and retry or document actions. Spec: `e2e/ingest_queue_panel.spec.js`. [code]

- **Top bar omissions and Hamburger contents**: `#authstate` and a top-level sign-out button are removed from `.appbar`. Inside `#pop-hamburger-menu`, `src/bible/templates/partials/common_appbar.html` includes `#menuaboutlink` (`→ /about`, `nav.about`) above `#menu-account-sep` / `__HAMBURGER_ACCOUNT_HTML__`, and every page renders the four-item common bottom menu in fixed top-to-bottom order (`#menusyndlink` → `/syndication`, `#moderationlinkbtn` → `/moderation`, `#consolelinkbtn` → `/admin` with `wrench` or `/account` with `settings`, and `#menu-logout-form` → `POST /logout`; hidden on anonymous/non-owner scopes). On public pages (`public_appbar_html` in `src/claire/api/public_kb_routes.py`), `.pub-appbar-nav` displays `/`, `/directory`, `/kb`, and `/about` in the start group.
- **Ingest dropdown and unified modal popups**: `#addbtn` toggles `#pop-ingest-menu` (`popover="auto"`) with three actions (`ingest`, `reingest`, `regen`). Selecting any option (or `#dedupbtn` / `#purgebtn` in workspace's `#pop-hamburger-menu`) opens centered `<dialog closedby="any">` popups (`#modal-ingest`, `#modal-reingest`, `#modal-regen`, plus `#modal-dedup` and `#modal-purge` on workspace) instead of rendering forms inside `#detailpane`. Inside `#modal-ingest`, the renditions fieldset renders `.ingest-rendition-policy` (`ws.ingest.renditions_policy.summary`, `.standard`, `.concise`, `.academic`) explaining the 3 rendition templates (`[decision]`, relocated from `/about`).
- **Extraction choice of an ingest** (design decision AED4-rev): `#modal-ingest` (workspace and `/syndication`) and the item dialog `#modal-item-ingest` (feed and timeline items) have the fieldset "추출 방법" (`.ingest-choice-grid.extraction`). `ClaireIngestDialogs` renders it from `extraction_modes` of `GET stats`: with an API, `provider` ("API로 바로 적재") first and checked, then `client` ("에이전트 클라이언트 대기"); without an API, only `client` and the hint `#ingest-extraction-hint`. Each new dialog checks the first choice again. The choice `client` sets `disabled` on the fieldsets `[data-api-only]` (amount and effort). The request sends `extraction_mode`. A staged result shows `#ingest-staged` ("에이전트 클라이언트 대기 중") in `#panel`. The re-ingest and regenerate dialogs have no choice: they extract with the API. Spec: `e2e/ingest_extraction_mode.spec.js`. [code]
- **Console area separation**: `/account/*`, `/settings/*`, and `/admin/*` ("Settings & Admin Console") render only the workspace, AI, KB, syndication, and admin sidebar groups (no `/moderation/*` links), while `/moderation/*` ("Knowledge Moderation") renders only the `moderation` sidebar group (`/moderation/kb`, plus `/moderation/oversight` and `/moderation/themes` for administrators).
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
- A document row (`docItemHtml()`) has the star, the watch and unread marks, the title, the source type, and the summary. It has no contribution function: no row menu "contribute" or "never contribute" and no contribution badge (owner decision 2026-11-19: contribution is a function of `/syndication` and `/settings/kb`, not of the profile page). `GET /documents` does not send `theme_match` or `never_contribute`. The E2E test `e2e/kb_workspace.spec.js` ("no contribution functions") checks it in multi-theme mode.
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

### 4.3 Home Cards and Conditional Proposal B Layout

**[code]** `renderHome()` fills `#homewrap` and calls `ClaireUserHome.refreshWorkspaceHome(theme)` (`src/bible/static/js/user_home_ap.js`, styled by `src/bible/static/css/user_home.css`). The center pane is a size container (`container-type:inline-size`); container queries at `820px` switch between wide two-column Proposal B (`#home-ap-profile` full-width at top, `#home-ap-timeline` left column, KB cards right column, `#home-mobile-tabs` hidden) and narrow mobile-only tabs (`#home-mobile-tabs` visible with `Timeline` and `Knowledge Base` tabs). On Shared KBs (`/kb/{number}/` or `theme.is_personal === false`), `#home-ap-profile`, `#home-ap-timeline`, and `#home-mobile-tabs` stay hidden. A card without data is hidden. No card shows a placeholder number.

| Element | Content | Source |
| :--- | :--- | :--- |
| `#home-ap-profile` (personal User Home only) | ActivityPub handle, display name, bio `summary`, follower/following/post counts, owner Note compose/profile edit buttons or visitor Follow dialog | `GET /home-profile` |
| `#home-ap-timeline` (personal User Home only) | Concise `Timeline` (`타임라인`) feed with `All`/`Note`/`Article` filters and cursor pagination | `GET /home-timeline` |
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

- Commands: home, graph, ingest, light theme, dark theme, and purge (owner only, on a personal knowledge base).
- Documents: up to 10 from the loaded list, then up to 10 from `GET /documents` with `q` (the keywords `is:missing-detail` and `is:mismatched` set the `detail` filter).
- Entities: the graph nodes in the client.
- One row that runs `runFullTextSearch()` with the input text: it selects the document list, puts the text in `#q`, and calls `doDocServerSearch()`.

Each row calls a function that exists. `/syndication` loads the same module with its own configuration.

---

## 8. Ingest Cockpit and Active Jobs

**[code]** Server: each ingest, re-ingest, and regenerate runs in `active_job()` (`src/claire/ingest/active.py`; kinds `ingest` in `ingest/pipeline.py`, `reingest` and `regenerate` in `ingest/service.py`). Each job has a file `data/active_jobs/<job_id>.json`; `active_ingest.json` is a copy of the primary job for older clients. Limits: 4 slots (`_MAX_EXPENSIVE_JOBS`), a job without an update for 600s is stale, a completed job stays for 30s. `GET /stats` gives `active_jobs` and `active_ingest`, filtered by theme access ([`THEMES.md`](THEMES.md) §4.3).

**[code]** Regenerate: the dialog `#modal-regen` has one run button, `#btn-submit-regen` in the footer; the dry run result (`POST /regenerate` with `dry_run`) only shows the targets. A target with `error` in the result is a failure: when all targets failed, the job ends with stage `error` and the panel shows `ws.regen.failed_title` and the errors; when some failed, the job message counts them and the panel shows `ws.regen.partial_failed`. `/syndication` keeps the dialog open with the error.

**[code]** Client: the `matrix` view (`#matrixwrap`) is the ingest cockpit. It has the status, theme, and kind badges, the target document, the percentage and the elapsed time, a stepper of the five stages `fetch`, `extract`, `resolve`, `detail`, `done` (`#cockpit-desktop-stepper`; `#cockpit-mobile-stepper` below 768px), a telemetry bar with the progress message of the server, and the heatmap table (`#matrix-table`) when `heatmap_matrix` exists. `startFastIngestPolling()` polls `GET /stats` every 1.5s during a job. The progress messages come from the server and are not catalog text ([`I18N.md`](I18N.md) §6).

---

## 9. Line Icons

**[code]** All icons of the web UI are inline SVG line icons with `stroke: currentColor`. Markup uses an empty `<span data-icon="name">`; `ClaireIcons.hydrate()` fills it. Scripts use `ClaireIcons.svg(name)`; the console renderers use `icon_html(name)` (`src/claire/api/accounts_routes/common.py`). The view navigation and the bar buttons of `index.html` have inline `<svg class="lineicon">` elements. Exceptions and the test: [`PRINCIPLES.md`](../PRINCIPLES.md) G1 (`tests/test_ui_line_icons.py`).

---

## 10. Tests

| Kind | Test |
| :--- | :--- |
| End-to-end | `e2e/workspace.spec.js` (home view, document context, local graph, entity marks, palette, narrow screens), `e2e/silvercord_rendering.spec.js` (public KB parity, `/workspace` entry, desktop pull-out graph, mobile bottom bar & modal `<dialog>`, Opinions prototype, private `link_access=1` isolation), `e2e/app_bar.spec.js`, `e2e/ingest_queue_panel.spec.js` (Ingest Queue quick panel Option A/C, unified 4-cell slot meter, retry, theme & mobile parity), `e2e/line_icons.spec.js`, `e2e/workspace_i18n.spec.js`, `e2e/console_ui_consistency.spec.js` (unified console card/button/heading styles, switch controls, sticky TOC, and responsive layout across `/account/*`, `/settings/*`, `/admin/*`, and `/moderation/*`), `e2e/account_area.spec.js` (account area paths, old path redirects, administrator WebMCP page) |
| Markers and runtime | `tests/test_graphview.py`, `tests/test_graphview_runtime.py`, `tests/test_silvercord_rendering.py` |
| Console UI contract | `tests/test_console_ui_contract.py` (Python builder unit tests, route-level shell and non-nested card contract checks, non-admin CL11 isolation, static JS and partial guardrails) |
| Active jobs | `tests/test_active_job_slots.py`, `tests/test_active_jobs_theme_isolation.py` |

Tests that fail before a change: [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) §3.

---

## 11. Console UI System

**[code]** All routes under `/account/*`, `/settings/*`, `/admin/*`, and `/moderation/*` render inside `src/claire/api/templates/console_shell.html`. The shell loads `src/bible/static/css/console_ui.css`, `src/bible/static/js/console_shell.js`, and `src/bible/static/js/console_ui.js` in that order (with `console_ui.js` before `manage_panels.js`).

### 11.1 Stylesheet (`src/bible/static/css/console_ui.css`)

- **Shell and responsive navigation**: `.console-shell`, `.console-body`, `.console-sidebar`, `.console-main`, `.console-layout` (`.has-toc` when `#console-section-body` contains 3 or more visible `h2` headings), `.console-toc`, `.console-mob-bar`, and `.console-drawer`.
- **Unboxed host and cards**: `.console-host` provides a gap-spaced flex column without card borders. Top-level cards (`.console-subcard`, `.card`, `.pref-card`) share identical background, border, border-radius (`0.625rem`), padding (`1rem 1.125rem`), and heading typography (`0.9375rem`, weight `700`).
- **Controls and data blocks**: `.console-card-head`, `.is-split` (`.console-split-meta`, `.console-split-ctrl`), `.console-field`, `.console-form-row`, `.console-form-grid`, `.console-actions`, `.console-kv`, `.pref-input`, `.pref-select`, `.console-textarea`, `.pref-btn` (`.pref-btn-accent`, `.pref-btn-pri`, `.pref-btn-danger`, `.pref-btn-sm`), native checkbox switches (`input[type="checkbox"][switch]`), `.pref-table-wrap` / `.pref-table`, `.pref-notice`, `.result`, `.pref-stat-row` / `.pref-stat`, `.pref-pill`, and `.proto-badge`.
- **Console-scoped `.claire-*` overrides**: Rules prefixed with `.console-shell` (and `body[data-active-area] dialog.claire-dlg`) align `.claire-card`, `.claire-btn`, `.claire-input`, `.claire-select`, `.claire-textarea`, `.claire-note`, `.claire-tag`, `.claire-srow`, `.claire-kpis`, `.claire-tw`, and `.claire-table` with the console design system while keeping public pages and workspace pages unchanged. Error states use `var(--danger)` and never `var(--error)`.

### 11.2 Python Builders (`src/claire/api/accounts_routes/console_ui.py`)

`console_ui.py` exports 12 typed HTML builder functions that escape untrusted text and validate card tags (`section`, `div`, `form`, `article`) and attribute names:

| Function | Output |
| :--- | :--- |
| `console_card` | `<section class="console-subcard">` (or `div`, `form`, `article`) with optional icon, badge, hint, and `.console-card-head` actions |
| `console_form_card` | `<form class="console-subcard">` wrapper around `console_card` with `action` and `method` attributes |
| `console_host` | Unboxed `<div class="console-host">` container with optional `outer_id` and `data-*-panel-host` / `data-console-host` attributes |
| `console_field` | `<label class="console-field">` with escaped label text, control HTML, and optional `.console-field-hint` |
| `console_toggle_row` | `.pref-row` row with `<input type="checkbox" switch>` |
| `console_table` | `<div class="pref-table-wrap"><table class="pref-table">` with escaped header cells |
| `console_notice` | `<div class="pref-notice" data-tone="...">` notice banner |
| `console_kv` | `<dl class="console-kv">` key-value list supporting plain tuples or row mappings |
| `console_result` | `<p class="result" role="status" aria-live="polite">` feedback line |
| `console_stat_row` | `<div class="pref-stat-row">` KPI grid container |
| `console_actions` | `<div class="console-actions">` button and link row |
| `console_pill` | `<span class="pref-pill" data-tone="...">` status pill |

### 11.3 Client Builders (`src/bible/static/js/console_ui.js`)

`window.ClaireConsoleUI` exposes client-side equivalents for dynamic panels (`manage_panels.js`, `console_admin.js`, `console_kb.js`, and sibling console scripts):

- `esc(v)` and `icon(name)`
- `card(opts)`, `field(opts)`, `toggleRow(opts)`, `notice(html, tone, extraClass, attrs)`, `pill(text, tone, extraClass, attrs)`, `table(opts)`, `statRow(items, attrs)`, `kv(rows, attrs)`, `result(id, attrs)`, `btn(label, opts)`, and `actions(html, extraClass)`

`window.ClaireConsoleShell` (`console_shell.js`) runs `enhance(targetRoot)` on load, on tab changes (`[role="tab"]` clicks and `claire:tabchange`), and on DOM mutations inside `#console-section-body` to enhance split cards, inject `td[data-label]`, update `#console-toc`, and hydrate line icons.

### 11.4 Shared Pipeline View (`/support/pipeline`)

- **[code] [decision]** `/support/pipeline` (`SUPPORT_PIPELINE_SPEC` in `src/claire/api/accounts_routes/console.py`) renders inside `console_shell.html`, loading `/static/css/pipeline_flow.css` via `$extra_styles_html` and `/static/js/pipeline_flow.js` via `$extra_scripts_html`. `support_pipeline_body` in `src/claire/api/accounts_routes/pipeline.py` keeps `data-toc-tabs="1"` across `flow`, `media`, and admin-only `records` tabs.
- **[code] [decision]** In the `flow` tab (`Q4`), a single top-level `.console-subcard#pipeline-graph` replaces the legacy prose cards (`_api_flow_card`, `_client_flow_card`, `_other_ai_card`) and hosts `render_pipeline_mount_html(build_pipeline_view_data(...))`: `.claire-pipeline-mount[data-pipeline-mount][data-pipeline-json-id="claire-pipeline-data"]` plus `<script id="claire-pipeline-data" type="application/json">` with `<`, `>`, and `&` Unicode-escaped.
- **[code] [decision]** `ClairePipelineFlow` (`src/bible/static/js/pipeline_flow.js`) renders the Proposal 4 (Gemini Neon) 5-stage optical prism shared with `/about`: `[data-p4-mode="provider"|"client"]` extraction fork toggle, `#p4-motion-btn` motion Pause/Resume toggle, 5 clickable `.p4-node[data-p4-stage]` beats (`ArrowLeft`/`ArrowRight`/`ArrowUp`/`ArrowDown`/`Home`/`End` and `[data-p4-step]`), Beat 03 rendition switcher vs AED9 nonce shield, Beat 04 interactive 5-Gate optical meter (`showSimulators: True` for logged-in members scoped to their own BYOK/account models, `Q5`), Beat 05 SVG constellation, `details.p4-acc` accordions, and strict `showJev: True` gating to `viewer="admin"` only (`jev-latest`, `gemini-decision-1`, `#pipeline-recent`, `#pipeline-fallbacks`, and `/admin/*` links remain hidden from `member` and `public` viewers).
- **[code]** The `media` tab retains `#pipeline-stt-order`, `#pipeline-vlm`, and `#pipeline-fixed`; the `records` tab retains admin-only `#pipeline-recent` and `#pipeline-fallbacks`. Tests: `tests/test_support_pipeline.py`, `e2e/support_pipeline.spec.js`.

---

## 12. Silvercord Document View (`/silvercord/{token}`)

**[code]** Every readable document request to `GET /silvercord/{token}` renders `src/bible/templates/share.html` with `src/bible/static/css/local_graph.css`, `src/bible/static/css/silvercord.css`, `src/bible/static/js/local_graph.js`, and `src/bible/static/js/silvercord.js` (bundled into `_SHARED_HTML` by `src/bible/web/pages.py`). Design principles are in [`SILVERCORD_RENDERING_DESIGN.md`](../design/SILVERCORD_RENDERING_DESIGN.md).

- **Responsive Chrome and Switcher**:
  - Desktop (`>= 820px`): `#sc-bar` (`48px` sticky header) shows the brand link, KB badge (`#sc-kb-badge`, or generic link-only badge when `link_only` is true), `본문` | `의견` tablist (`#sc-tablist-top`, `role="tablist"`, `focusgroup="tablist nomemory"`), graph toggle (`#sc-graph-toggle-top`), conditional workspace link (`#sc-ws-link` pointing to `/silvercord/{token}/workspace`), and theme switch (`#sc-theme-btn`, `localStorage` key `claireTheme`).
  - Mobile (`< 820px`, `SQ3:B`): `#sc-bar` shrinks to a `44px` slim bar; `#sc-bottom-bar` renders a fixed/sticky bottom bar with `본문` | `의견` inside `#sc-tablist-bottom` (`role="tablist"`) and a separate `#sc-graph-toggle-bot` button outside `role="tablist"`.
  - Tab switching (`switchTab()`): inactive panels use `hidden="until-found"` with padding on `.sc-panel-inner` and a `beforematch` listener for browser Find-in-Page. Active tabs render a `border-block-end` indicator animated via CSS Anchor Positioning (`@supports (position-anchor: auto)`) with a `::after` fallback and keyboard arrow navigation (`ArrowLeft`/`ArrowRight`/`Home`/`End`). Switching from `본문` to `의견` saves `window.scrollY` and hides `#srail`; returning to `본문` restores `savedDocScrollY` and updates `#srail`.
- **Pull-Out Local Graph Panel and Shared `ClaireLocalGraph`**:
  - Closed by default on initial load (`aria-expanded="false"`, no `.sc-graph-open`). First open lazily fetches `GET /silvercord/{token}/graph` (`credentials: "same-origin"`) and caches it for the page session, showing loading and retryable error states.
  - Desktop (`>= 1100px`): opens a right-hand `360px` grid column (`#sc-desktop-aside`) beside the reading area. Hovering a Ring 1 node or row calls `ClaireLocalGraph.setEntityHoverHighlight()` and activates CSS Custom Highlight API marks (`claire-entity-hover`) in the document body (`applySilvercordEntityMarks()`).
  - Compact/Mobile (`< 1100px`): opens a native modal `<dialog id="sc-mobile-dialog" closedby="any">` bottom sheet via `showModal()` (`overscroll-behavior: contain` on `:modal` and `::backdrop`, plus outside-bounds pointer fallback) and hides `#srail` while open.
  - Sub-tabs `로컬 그래프` (`ClaireLocalGraph.renderSvg()`, Ring 1 + Ring 2 + shared documents or blocked-documents notice on `link_only`) and `엔티티 (N)` drill down in-panel (`SQ5:A`) to Entity Detail with `#gp-back-btn`, relations jump links, source documents (or blocked count on `link_only`), and conditional `/entity/{token}` link.
- **ActivityPub `의견` Visual Prototype**:
  - Public KB documents render `#op-public-content` with `--proto`/`--proto-bg` prototype badges, `--remote`/`--remote-bg` ActivityPub target metadata (`/ap/objects/{object_id}`), disabled composer/reaction buttons (`disabled`, `aria-disabled="true"`), sample thread preview, inline SVG line icons only (`ClaireIcons.svg()`), and zero emojis. Private `link_only` documents render `#op-private-content` (non-federated notice).
