# Home Overview, Context Panel, and Local Graph Design

- **Document Identifier**: `DESIGN-HOME-OVERVIEW-CONTEXT-PANEL-20261114`
- **Status**: Implemented (units HW-1 to HW-8 completed on 2026-11-15; HD14). The owner accepted the visual prototype and decided HD1 to HD13 on 2026-11-14.
- **Date**: 2026-11-14 · Base Commit `59f8788b`
- **Visual prototype**: [`prototypes/home_workspace_prototype.html`](prototypes/home_workspace_prototype.html) (one static file, mock data, no network call)
- **Related Documents**:
  - [`PRINCIPLES.md`](../PRINCIPLES.md)
  - [`WEBUI_HOME_ENTRY_AND_GRAPH_EXPLORATION_REDESIGN.md`](WEBUI_HOME_ENTRY_AND_GRAPH_EXPLORATION_REDESIGN.md) (frozen 2026-11-02; `enterHome` and the proposals P1 to P7)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (FP4, the stage pipeline RD14)
  - [`I18N_DESIGN.md`](I18N_DESIGN.md) (ID5)
  - [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md), [`GRAPH_ENGINE.md`](../implementation/GRAPH_ENGINE.md), [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]** (confirmed in source at the base commit), **[measured]** (seen in a browser, with the condition), **[decision]** (owner policy), **[unverified]**.

---

## 1. Objective and Scope

### 1.1 Objective

1. The home page of an account starts with an overview, not with the full graph.
2. The page has one list on the left side. The right side is a context panel that opens only when a context exists (a document, an entity, or the graph).
3. While the owner reads a document, the graph shows only the connections of that document, in the context panel.
4. The look stays the same: the tokens of `theme.css`, the fonts, the borders, and the card style do not change.

### 1.2 Problems at the Base Commit

| Problem | Evidence |
| :--- | :--- |
| The first screen is the full graph. It occupies the center, but it answers no question of the reader. | **[code]** `resolveHomeView()` returns `'graph'` when no job is active. The frozen design (Section 6.1) records the same diagnosis. |
| The right pane is always 360px wide. Without a selection it shows only help text. | **[measured]** Seed database of `e2e/seed_db.py`, 1440×900: the grid columns are `280px 800px 360px`. |
| The management buttons, with the permanent delete (`소각`), are always visible at the top of the right pane. | **[code]** `#moremenu .action-btn-row` in `index.html`. |
| The left list changes with the view, and the user cannot select it. | **[code]** `listMode()` and `syncListPane()` in `app.js`. |
| A maintenance notice is a banner above all views. | **[code]** `ClaireStatusBanner.show('format_missing', …)` from the `format_status` of `GET /documents`. |

### 1.3 Non-Goals

- The graph engine (`static/js/graph/*.js`) does not change: layout, physics, renderer, and input stay as [`GRAPH_ENGINE.md`](../implementation/GRAPH_ENGINE.md) describes them.
- The ingest cockpit (`matrix` view), the reader rendering pipeline, the presentation view, and the decision stream view do not change (HD7).
- No new library and no content delivery network script (HD10).
- No "continue reading" card and no stored reading position (HD5).
- The split view (text and graph side by side) and the "no graph" option of the prototype are not built. The owner selected the context panel (HD3).
- Improvements for tablet widths come after the implementation (HD8).

---

## 2. Owner Decisions (2026-11-14)

| ID | Topic | Decision |
| :--- | :--- | :--- |
| HD1 | First screen | The overview is the first screen. The full graph opens from the graph tab and from the knowledge map card. |
| HD2 | Sidebars | One list on the left side and a context panel on the right side. The context panel opens only when a context exists. |
| HD3 | Graph while reading | The local graph of the open document is in the context panel. |
| HD4 | Icons of the view navigation | Line icons in one color replace the emoji icons. |
| HD5 | Continue reading | Not necessary. The card is not built. |
| HD6 | Entity marks in the text | Accepted on one condition: the marks must not damage the entity extraction and must not make a contamination path. Section 8 is the investigation, and its rules are the condition. |
| HD7 | Presentation and decision stream tabs | They stay as they are, with their feature switches. |
| HD8 | Tablet widths | Improvement after the implementation. |
| HD9 | Priority | The units of this design (Section 12) come before all other work that is not implemented. |
| HD10 | Dependencies | The proposal was free of the dependencies that the project had. The result adds no dependency. |
| HD11 | Isolated entities and pairs | They stay on the graph canvas, as the engine shows them at the base commit. |
| HD12 | Relation lines in the full graph | No color for each relation type. The owner followed the recommendation of Section 14. |
| HD14 | Stage strip before FP4-5 (2026-11-15) | Unit HW-8 does not wait for FP4-5. The strip uses the counts of routes that exist (Section 5.6). |
| HD13 | Home title | `Claire Bible — 홈` (ko) and `Claire Bible — Home` (en), catalog key `home.title`. |

---

## 3. Facts at the Base Commit

All items are **[code]** unless a different tag is given.

1. **Layout.** `#wrap` is a grid: `280px minmax(420px,1fr) var(--detail-width,360px)` (`workspace.css`). `body.detail-compact` makes the right column 56px wide. Breakpoints: `mobileMQ` is `(max-width:720px)` and `compactMQ` is `(max-width:1100px)` (`app.js`).
2. **Home entry.** `enterHome({reason})` is the one function for the page load, the brand link, and a theme switch. It calls `resetWorkspaceState()`, reads `GET /stats`, and applies `resolveHomeView()`: `'job'` (the `matrix` view), `'graph+banner'`, or `'graph'`. `HOME_TITLE` is `Claire Bible — 지식 그래프`.
3. **Center views.** `setCenterView()` (`reader.js`) knows `graph`, `reader`, `presentation`, `stream`, and `matrix`. It writes `document.body.dataset.centerView`.
4. **List pane.** The reader view shows the document list and the graph view shows the entity list. Limits: [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 1.
5. **Right pane.** `#detailpane` has the action buttons (`#moremenu`), the panel `#panel` (`renderPanel()` for a node, `renderDocPanel()` for a document), and the footer.
6. **Search.** `#q` in `#centerhead` is the search field of the view (`onCenterSearchInput()`, `updateCenterSearchMode()`).
7. **Data that the client has.**
   - `GET /stats`: the counts of `documents`, `entities`, `relations`, and other tables, `active_jobs`, `active_ingest`.
   - `GET /documents`: rows with the newest first, `next_before`, `total`, and `format_status` (`needs_migration`, `missing_detail_docs`, `mismatched`).
   - `GET /graph`: nodes with `id`, `label`, `group`, `sources` (document identifiers), `obs`, `degree`; edges with `from`, `to`, `label`, `dashes` (provisional). A node has no time field.
   - `GET /document?id=`: `summary`, `detail`, `detail_format`, `detail_html`, `url`, `extra_sources`, `source_type`, `fetched_at`, `author`, `published_at`, `orig_chars`, `raw_chars`, `raw_truncated`, `has_raw_file`, `nodes` (`id`, `label`, `group`, `observations`), `resolution_log`, and the parser flags. `nodes` has no aliases.
   - `GET /node?id=`: name, type, `aliases`, `observations`, `neighbors` (`rel`, `dir`, `provisional`), `documents`.
   - `GET /resolution/decisions`: the decisions of the theme with `limit` and `offset`.
   - `GET /api/feeds`: the feeds of the account with `items` and `unread` for each feed.
8. **Feature switches.** The presentation view needs `presentation_enabled` of the theme. The presentation toggle and the decision stream pages are off for all themes at this time ([`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 2).
9. **Localization.** The catalogs `static/locales/{en,ko}.json` and `ClaireI18n` (`i18n.js`) exist. `index.html`, `app.js`, and `reader.js` have fixed Korean text; unit IP2-3 of [`I18N_DESIGN.md`](I18N_DESIGN.md) is not done.
10. **End-to-end tests.** `e2e/workspace.spec.js` has 37 tests. Ten lines read `data-center-view` or the home title. Five mobile tests and four other tests fail before this work ([`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 3).
11. **Popover and dialog.** `syndication.html` uses `popover="auto"` and loads the popover polyfill from a content delivery network. `index.html` uses none of them.

---

## 4. Page Structure

### 4.1 Header Bar

From left to right:

1. Brand link (`enterHome`), theme picker (not changed), list toggle (`aria-expanded`). The list toggle comes after the two controls that identify the knowledge base, and its open state has a neutral fill, not the accent color (Section 16).
2. View navigation: Home, Reader, Graph, Presentation, Decision stream. The last two show only when their feature switch is on, as at the base commit (HD7).
3. Command palette button (`#palettetrigger`): it opens the palette (Section 11). The header has no input field. The `#q` field is the filter of the list pane (Section 4.3, Section 16).
4. Ingest button (owner and collaborator only, the same condition as `canIngest()`).
5. Manage menu: re-ingest, regenerate, deduplicate, path, synthesis, and the permanent delete as the last item behind a separator. Each item calls the function that the button of `#moremenu` calls today.
6. Authentication state, settings link, light and dark switch, context panel toggle.

Rules:

- The view navigation is a `<nav>` element with buttons and `aria-current="page"` on the active view. It is not a `tablist`: the buttons change the view of the application and its history, and a tab pattern is only for panels inside one view.
- The manage menu is an element with the `popover` attribute and `role="menu"`. The browser gives the light dismiss and the Escape key. A script sets the position before the menu opens and moves the focus with the arrow keys. A browser without the Popover API gets the same element with a class switch (no polyfill, HD10).
- The icons are inline SVG line icons that use `currentColor` (HD4). No emoji in the view navigation.
- `#centerhead` goes away. The center pane gets its height.

### 4.2 Main Grid

```text
[ list pane 280px ] [ center minmax(0,1fr) ] [ context panel 348px or 0 ]
```

- The list pane closes to 0 with the list toggle. The state is in `localStorage` (`claireListOpen`).
- The context panel has a width only when it is open. `body.detail-compact` and the 56px column go away.
- The transition of the grid columns stays (0.2s), and it stops with `prefers-reduced-motion`.

### 4.3 List Pane

- A switch with two buttons, Documents and Entities, selects the list in each view. The default is the list of the view: entities in the graph view, documents in all other views. A selection of the user stays until the next change of the view.
- One filter field (`#q`) is below the switch. It filters the list that shows: documents by title and summary (Enter starts the search in the document bodies, and the result shows in the document list), entities by name (the graph shows the same entities). A change of the list removes the text of the field (Section 16).
- The lists, their paging, and their limits do not change ([`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 1).

### 4.4 Center Views

| `data-center-view` | Content | Change |
| :--- | :--- | :--- |
| `home` | Overview (Section 5) | New |
| `reader` | Document reader | The meta line gets the source badge (Section 7.1) |
| `graph` | Full graph, legend, degree control, camera control | Not changed |
| `presentation`, `stream`, `matrix` | As at the base commit | Not changed |

### 4.5 Context Panel

| State | Content |
| :--- | :--- |
| No context (the home view without a selection, an empty reader) | Closed. The toggle in the header is disabled. |
| A document is open | Document context with three tabs (Section 6) |
| An entity is selected (in a list, the local graph, the graph, a card, or a mark) | Entity detail: the content of `renderPanel()`. A back link goes to the document context when a document is open. |
| Graph view without a selection | Insight lists (Section 5.3, the same lists as the knowledge map card) |

- The user can close the panel. The closed state is in `localStorage` (`claireContextOpen`). A new selection of an entity opens the panel again. A new document opens it only when the stored state is open.
- The Escape key removes the entity selection first, and then it does nothing more on a wide screen.
- The three tabs are a real `tablist`: `role="tab"` with `aria-selected`, one `role="tabpanel"`, the arrow keys move the selection (the selection follows the focus), and the selected tab has a line below it (not only a color).

### 4.6 Narrow Screens

Up to 720px (`mobileMQ`):

- The view navigation moves to a bar at the bottom: Home, List, Reader, Graph, Context.
- The list pane is a drawer from the left side and the context panel is a sheet from the bottom. They use the drawer functions that exist (`openDocDrawer()`, `closeAllDrawers()`, `#drawerbackdrop`).
- While a drawer or the sheet is open: it has `role="dialog"` and `aria-modal="true"`, the center pane is `inert`, the Escape key and the backdrop close it, and the focus goes back to the button that opened it. On a wide screen the same elements are plain `aside` elements without the dialog role.

From 721px to 1100px (`compactMQ`): the context panel is an overlay above the center pane, as the detail drawer is today. More work for these widths comes later (HD8).

### 4.7 Home Entry and History

- `resolveHomeView()` returns `'home'`, `'job'`, or `'home+banner'`. The rules for the active job do not change.
- `enterHome()` applies the `home` view. `initGraph()` runs also for the home view, because the lists and the counts of the home view use the graph data. The graph pane is hidden, thus the engine does not run its render loop until the graph view opens.
- `HOME_TITLE` comes from the catalog key `home.title`. The history state keeps the `pane` field and gets the view name `home`.
- A deep link (`?doc=`) opens the reader as today.

---

## 5. Overview (Home View)

Rule for all cards (`PRINCIPLES.md` C1, C2): a card shows only stored facts and counts from a route. When the source of a card is absent or fails, the card is not shown. No placeholder number and no estimate.

### 5.1 Facts Line

Theme label, then documents, entities, and relations from `GET /stats`, then the time of the last ingestion: `fetched_at` of the first row of `GET /documents`.

### 5.2 Recent Ingestion Card

The first five rows of `GET /documents` (the request that the list makes; no second request). Each row: source type, title, relative time, and the entity count of the document. The entity count is the number of graph nodes whose `sources` has the document identifier; it shows only after `GET /graph` is loaded. A click opens the reader.

The source badge of a row (Section 7.1) shows only when the row has the necessary fields. **[code]** `documents_list()` (`src/claire/store/queries.py`) does not return raw text fields (`has_raw_file`, `raw_chars`, `raw_truncated`), thus the source badge stays in the reader (unit HW-3).

### 5.3 Knowledge Map Card

Three lists of five entities each. A click selects the entity and opens the context panel; the view does not change.

| List | Source | Rule |
| :--- | :--- | :--- |
| Hubs | `degree` of the graph nodes | Highest first |
| Bridges | `sources.length` of the graph nodes | Highest first; equal values in the order of `degree`. An entity that is in no document is not in the list. |
| Added in the last 7 days | `created_at` of the graph nodes (new field, Section 9) | Newest first; the list is not shown when no node is in the period |

Mini map: one static drawing of the node positions on a 2D canvas, without a render loop and without input. A click opens the graph view. The positions come from the layout code of the engine without its renderer (`Simulation`: `setTopology()`, `snapToHomes()`, `warmup()`). **[code]** The simulation runs without a `Network` instance. The dot size and the line opacity follow the node density of the box, nodes with one connection or less are lighter in a dense graph, the nodes with the most connections are drawn last, and the drawing stays above the hint text. A check with a graph of 2900 entities and 4712 relations (2026-11-15) gave one filled area with the fixed dot size of the first implementation.

### 5.4 Recent Decisions Card

The first four rows of `GET /resolution/decisions`. The card shows only when the decision stream switch is on; at this time the switch is off for all themes, thus the card is not shown. The label of a row comes from its `stage` (Section 7.2).

### 5.5 Maintenance Card

Owner only. The card shows only rows with a count above 0, and it is not shown when it has no row.

| Row | Source | Command in the row (`PRINCIPLES.md` C5) |
| :--- | :--- | :--- |
| Documents without the readable detail | `format_status.missing_detail_docs` | `./cb-manuscript app format-migrate --apply` |
| Documents with a different format | `format_status.mismatched` | `./cb-manuscript app format-migrate --apply` |

The banner states `format_missing` and `format_mismatch` move to this card: `ClaireStatusBanner` does not show them. All other banner notices stay (a job in a different theme, errors).

A row for failed ingestions needs a count that `GET /stats` does not give by status. **[unverified]** The meaning of the `raw_inbox` count; the row is not built until a route gives the count of failed items.

### 5.6 Stage Strip

Three links in the order of the `/syndication` pipeline (RD14): collection, ingestion, publication. The home only links to the stages; the federation client stays in `/syndication` (RD10).

The strip is for the owner (HD14). Each stage is a link to `/syndication`. Each count comes from a route that exists; a route that is absent (404 in single mode) or fails gives no count, and the stage shows its link only (`PRINCIPLES.md` C1, C2).

| Stage | Counts | Source |
| :--- | :--- | :--- |
| Collection | Feeds, unread feed items | `GET /api/feeds` (number of feeds, sum of `unread`) |
| Collection | Following | `GET /api/federation/following?limit=1` (`total`) |
| Ingestion | Documents, jobs in progress | `GET /stats` (`documents`, active items of `active_jobs`) |
| Publication | Followers, tasks that wait | `GET /api/federation/status` (`followers.total`, `pending_tasks`) |

When unit FP4-5 builds the stage bar of `/syndication`, the strip takes the counts of that bar (timeline items, mentions, published documents), and the three links go to the three stages. The header link to `/syndication` (`#syndlink`) stays.

---

## 6. Document Context (Context Panel)

The content of `renderDocPanel()` goes into three tabs. No field goes away.

### 6.1 Links Tab

1. **Local graph** (Section 6.4).
2. **Documents that share entities.** For the open document D: for each other document X, the number of entities of D whose `sources` has X. The five documents with the highest number show, with the number. The computation is in the client from the graph nodes, and it is deterministic. The titles come from `loadDocsById()`.

### 6.2 Entities Tab

The entity list of the document (`docNodes()`), with the type, the first observation, and the degree. A click selects the entity.

### 6.3 Source Tab

| Block | Fields | Source |
| :--- | :--- | :--- |
| Source text | URL, more sources, fetch time, source type, author, publication time, character counts, the truncation and parser notices of `docMetaHtml()`, the link to the raw file | `GET /document` |
| Model outputs | Summary present, detail present, entity count | `GET /document` |
| Resolution | Number of entries of `resolution_log` for each stage class (Section 7.2), link to the decision record | `resolution_log` |

The Markdown export is not in this tab. It comes from the context menu of an item in `/syndication` (RD25).

### 6.4 Local Graph

An inline SVG drawing, made from the data that the client has. It is not a second instance of the graph engine.

- Center: the document. Ring 1: the entities of the document (`nodes` of `GET /document`). Ring 2: entities of other documents that have a relation with a ring 1 entity (the edges of `GET /graph`).
- Limits: ring 1 shows the 24 entities with the highest degree; ring 2 shows 12. A text below the drawing gives the number of entities that are not shown. The Entities tab has all of them.
- Lines: a solid line for a relation, a dashed line for a provisional relation (`dashes`), a thin gray line from the document to each ring 1 entity.
- The line color follows the edge style of the full graph (`idleEdgeStyle()` in `app.js`). The local graph adds no color rule of its own.
- Interaction: a click on an entity selects it. A hover on an entity marks the same entity in the lists and in the text marks (Section 8), and the reverse. Each entity is a button in the accessibility tree with its name.
- Before `GET /graph` is loaded, the drawing has ring 1 only.

---

## 7. Visual Grammar: Solid for Facts, Dashed for Inferences

One rule for all screens of this design. It makes principle A2 visible.

| Line | Meaning | Examples |
| :--- | :--- | :--- |
| Solid | The source text, or a result of deterministic code | Source badge, shared entity count, a relation that is not provisional, a resolution by name, alias, or acronym |
| Dashed | A provisional item, or a decision by a model | Provisional relation, a resolution by embedding or by the model, entity marks |

The line style carries the meaning; a color alone never does.

The summary and the detail have no label "model output" (P1, 2026-11-15): ingestion is always the work of a model. The reader meta line shows the model of the latest extraction, to the right of the source badge.

### 7.1 Source Badge

| Condition (`GET /document`) | Badge |
| :--- | :--- |
| The raw text is stored and `raw_truncated` is false | "Source text kept" (solid) |
| `raw_truncated` is true | "Source text kept in part" (solid), with the two character counts |
| No raw text | No badge |

### 7.2 Stage Classes of a Resolution

| `stage` (`extract/resolver.py`) | Class | Line |
| :--- | :--- | :--- |
| `exact_match`, `alias_match`, `acronym_match` | Rule | Solid |
| `high_confidence_vector` | Embedding similarity, with the score | Dashed |
| `borderline_llm_judge` | Model decision, with the score | Dashed |
| `no_match` | New entity | Solid |

A stage that is not in this table shows its stage name as text, without a class (`PRINCIPLES.md` C1).

---

## 8. Entity Marks in the Text: Investigation and Rules (HD6)

### 8.1 Question

Can a mark on an entity name in the reader text damage the entity extraction, or make a path for contamination of the knowledge data?

### 8.2 Facts

All items are **[code]**.

1. **Extraction input.** `extract_resolve_store()` (`ingest/pipeline.py`) calls `provider.extract(doc, …)` with the document that has `raw_text`. No route takes text from the reader page.
2. **Stored bodies have many readers.** `get_document_detail()` is read by `expand/follow.py`, `expand/research.py`, `ingest/pipeline.py`, `ingest/service.py`, and `store/queries.py`. `detail_html` is read by `support_bundle.py`, `graphview.py`, `render/media_transform.py`, `cli.py`, `store/queries.py`, `store/db.py`, `accounts/server_tier.py`, `api/usercontent.py`, and `api/accounts_routes/federation.py`. Thus a mark in a stored body goes into model prompts of the expansion, into the share page, and into the `usercontent` origin.
3. **No stored position of a mention.** `Entity` (`ontology/base.py`) has `name`, `aliases`, `observations`, and `sources`. It has no offset into the text. Thus a mark is always a new comparison of strings when the page shows the text. It is not data of the pipeline.
4. **The entity set of a document.** `document_entities()` gives the entities whose `sources` has the document. This set is the result of the resolution, and a part of it comes from model decisions.
5. **The client does not read the reader text back.** `app.js` and `reader.js` have no call of `getSelection`, `innerText`, or a read of `#rbody`. The clipboard gets only the share URL and the transcript text. `POST /research` sends `query`, `node_id`, and `doc_id`; the server makes the context from the database.
6. **Names are not trusted text.** An entity name comes from a model that read remote content.

### 8.3 Risks and Controls

| # | Risk | Control |
| :--- | :--- | :--- |
| R1 | A mark goes into a stored body and from there into prompts, the share page, the export, or an index. | The server makes no mark. `detail`, `detail_html`, and all routes that give them do not change. The marks exist only in the home reader page of the browser. |
| R2 | A mark changes the text that a copy, a selection, or a later function reads from the page. | The marks use the CSS Custom Highlight API: `Range` objects on the text nodes, a `Highlight` in `CSS.highlights`, and the style in `::highlight()`. The document tree of the text does not change: no element, no attribute, and no text is added. `textContent` and `innerHTML` of `#rbody` are the same with the marks on and off. |
| R3 | A wrong match shows a relation that does not exist (a guess, `PRINCIPLES.md` C1). | The match rules of Section 8.4. A string that fits two entities gets no mark. |
| R4 | The reader thinks that the pipeline found the entity at this position. | The mark is a dotted underline (the inference line of Section 7), and the legend text says: "a dotted underline = a string that is equal to a name of an entity of this document". |
| R5 | A mark starts a write operation. | A click on a mark only selects the entity (`GET /node`). It adds nothing to the synthesis set and starts no research. |
| R6 | An entity name runs as markup. | The names are used only as strings for the comparison and as text of buttons that exist. No name goes into an HTML string. |
| R7 | A long text or many names make the page slow. | One pass with a `TreeWalker` after the text is rendered. No marks when the text has more than 400,000 characters or the document has more than 300 names; the legend text then says that the marks are off for this document. |
| R8 | The marks are the only way to an entity. | The marks are a visual aid. The Entities tab and the local graph have each entity as a button for the keyboard and for a screen reader. A highlight is not in the accessibility tree. |

### 8.4 Match Rules

1. **Candidates.** The name and the aliases of the entities of the open document only (`nodes` of `GET /document`, with the new field `aliases`, Section 9).
2. **Comparison.** The text and the candidate after `toLowerCase()`. **[code]** The server key `normalize_name()` uses `casefold()`; the two functions differ for some letters (for example `ß`), and then the mark is absent. No other normalization: no stem, no partial word, no similarity.
3. **Borders.** When a candidate starts with an ASCII letter or digit, the character before the match must not be an ASCII letter or digit. The same rule applies to the end. A Korean particle after the name is permitted (`Fedify에서`).
4. **Short names.** A candidate with fewer than 2 characters gets no mark. A candidate of 2 or 3 ASCII letters must match with the same case (`Go`, `AI`).
5. **Order.** Longest candidate first. A match that overlaps an earlier match is dropped.
6. **Two entities.** When the same candidate string belongs to two entities of the document, the string gets no mark.
7. **Where.** Text of paragraphs, list items, and table cells of the summary and the detail. Not in headings, code, preformatted text, links, and math (`.katex`).
8. **When.** After the sanitizer and after `applyMathRendering()`. The ranges are made again when the text is rendered again.
9. **Switch.** `localStorage` key `claireEntityMarks` (default on). Off removes the two highlights from `CSS.highlights`.
10. **Pointer.** A click on marked text selects the entity when no text is selected. The position comes from `document.caretPositionFromPoint()` (or `caretRangeFromPoint()`).
11. **No fallback.** A browser without `CSS.highlights` shows no marks, and the legend text says so. The page does not put elements around the text as a fallback, because that is the change of the document tree that R2 forbids.

### 8.5 Result

With the controls of Section 8.3, no path exists from a mark to the extraction input, to a stored body, to an index, or to a prompt. The entity extraction does not change. The risk that stays is a wrong visual match (R3), which the rules of Section 8.4 limit and the legend text explains.

The condition of HD6 is satisfied when these tests pass (unit HW-5):

| Test | Check |
| :--- | :--- |
| T1 (end-to-end) | `textContent` and `innerHTML` of `#rbody` are equal with the marks on and off. |
| T2 (end-to-end) | A click on a mark sends only `GET /node`. No `POST` request starts. |
| T3 (end-to-end) | The share page of the same document has no highlight. |
| T4 (unit, route output) | `GET /document` gives equal `detail` and `detail_html` before and after the unit; only `nodes[].aliases` is new. |

The match function is deterministic code. It gets a test with a table of cases (borders, short names, two entities), run by the end-to-end test in the page.

---

## 9. Server Changes

No schema change. Two read-only fields:

| Route | Field | Value | Unit |
| :--- | :--- | :--- | :--- |
| `GET /graph` | `nodes[].created_at` | `entities.created_at` (epoch seconds) | HW-2 |
| `GET /document` | `nodes[].aliases` | The alias list of the entity | HW-5 |

Each unit updates [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md) and [`openapi.yaml`](../implementation/openapi.yaml) in its commit. The size of `GET /graph` grows by one number for each node.

---

## 10. Localization and Accessibility

1. **Catalog keys from the start (ID5).** Each new text of this design is a key in `static/locales/en.json` and `ko.json`, under `home.*`, `context.*`, and `nav.*`. The text that this design does not touch stays for unit IP2-3. IP2-3 comes after the units of this design for the header, the view navigation, and the right pane, because this design replaces them.
2. **Dialogs.** The command palette is a `<dialog>` element opened with `showModal()`, with a name (`aria-labelledby` or `aria-label`) and `autofocus` on the first field. The ingest form is not a dialog: it stays in the context panel, as at the base commit. The browser gives the focus limits, the Escape key, and the return of the focus; the page adds no `role="dialog"`, no focus trap, and no Escape handler for them. `closedby="any"` gives the close by a click outside; a browser without `closedby` gets a click handler that checks the rectangle of the dialog. `dialog:modal` and `dialog::backdrop` have `overscroll-behavior: contain`, and the backdrop has `overflow: hidden` (without it the backdrop is not a scroll container and the rule has no effect), thus the page behind does not scroll. The page does not lock the scroll on `html` or `body`.
3. **Cards.** The center pane is a size container (`container-type: inline-size`). The card grid is one column by default and gets its columns from container queries, because the width of the center pane changes with the two side panes.
4. **Motion.** The transitions of the panes stop with `prefers-reduced-motion`.
5. **Selected state.** A selected tab or switch button has a line or a border change, not only a color. The active view button has a visible outline in forced colors mode.

---

## 11. Command Palette

One dialog for search, movement, and commands, opened with the `/` key, the button of the header, or the manage menu. The `/` key does not open the palette while the focus is in a field that takes text or while a dialog is open. Ctrl+K is not used: a browser uses that key (Section 16).

- Sources: commands (home, graph, ingest, light and dark), documents (`GET /documents` with the filter of the list), entities (the graph nodes in the client), and one row that starts the search in the document bodies with the text of the input field.
- A row runs a function that exists. The palette makes no new write path.
- Unit FP4-5 also specifies a command palette for `/syndication`. The two pages use one script module and one set of catalog keys. The unit that comes first makes the module.

---

## 12. Work Units

Each unit ends with passing tests and one commit. Each unit records its result in the implementation documents in the same commit ([`docs/README.md`](../README.md)).

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| HW-1 | Header bar (Section 4.1), main grid and context panel on demand (4.2, 4.5), list switch (4.3), `home` view with the facts line and the recent ingestion card (5.1, 5.2), home entry (4.7), narrow screens (4.6), line icons, catalog keys | The page load and the brand link open the `home` view. Without a selection the center pane has the width of the right column. The manage menu has all actions of `#moremenu`. | — | L |
| HW-2 | Knowledge map card with `created_at` (5.3, 9), mini map, recent decisions card (5.4), maintenance card and the banner change (5.5) | The three lists agree with the graph data of the seed database. The two format banners do not show; the card shows their counts. | HW-1 | M |
| HW-3 | Document context with the tabs Links (shared documents), Entities, and Source (6.1 to 6.3), source badge and stage classes (7) | Each field of `renderDocPanel()` at the base commit is in one of the three tabs. | HW-1 | M |
| HW-4 | Local graph (6.4) and the hover link between the graph, the lists, and the cards | The local graph of a seed document shows its entities in ring 1 and their neighbors in ring 2. A click selects the entity. | HW-3 | M |
| HW-5 | Entity marks (8) with `nodes[].aliases` (9) | Tests T1 to T4 pass. | HW-3 | M |
| HW-6 | Graph view: insight lists in the context panel without a selection; entity detail from each view | A click on a list row in the home view opens the entity detail and does not change the view. | HW-2 | S |
| HW-7 | Command palette (11) | The `/` key opens the palette. A document row opens the reader. | HW-1 | S |
| HW-8 | Stage strip (5.6) | The strip shows for the owner with the counts of the routes that exist. A route that fails gives no count. | HW-1 | S |

Order (HD9, HD14): HW-1 to HW-8 come before the units of [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) and [`I18N_DESIGN.md`](I18N_DESIGN.md) that are not done.

Units that can be done in any order after HW-1: HW-2, HW-3, and HW-7.

### 12.1 Tests

The test policy of the owner applies: unit tests only for interfaces between modules, input and output, and calls of external libraries; a minimum of one end-to-end test for each new feature.

| Kind | Target | Unit |
| :--- | :--- | :--- |
| Unit (route output) | `GET /graph` gives `created_at` for each node | HW-2 |
| Unit (route output) | `GET /document` gives `nodes[].aliases`; `detail` and `detail_html` do not change (T4) | HW-5 |
| End-to-end | Home view at the page load and from the brand link; the context panel is closed without a selection and opens for a document | HW-1 |
| End-to-end | Knowledge map lists and the maintenance card with the seed database | HW-2 |
| End-to-end | Tabs of the document context with the arrow keys; the Source tab fields | HW-3 |
| End-to-end | Local graph rings and the selection of an entity | HW-4 |
| End-to-end | T1 to T3 and the match cases | HW-5 |
| End-to-end | Entity detail from a list row in the home view | HW-6 |
| End-to-end | Palette opens, filters, and opens a document | HW-7 |

Changes to tests that exist (HW-1): the tests that expect the graph as the first view (`data-center-view="graph"`, the title `Claire Bible — 지식 그래프`) expect `home`. The tests of the graph view first open the graph view. The element identifiers that stay (`#docs`, `#doclist`, `#entlist`, `#q`, `#net`, `#reader`, `#rbody`, `#panel`, `#detailpane`, `#centertab-*`) keep their names, thus the other selectors of `e2e/workspace.spec.js` stay valid. The tests that fail before this work stay in the list of [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 3; HW-1 adds one mobile test for the bottom bar and the sheet.

### 12.2 Documentation Updates

| Document | Change | Unit |
| :--- | :--- | :--- |
| [`IMPLEMENTATION_STATUS.md`](../implementation/IMPLEMENTATION_STATUS.md) Section 1 | Code positions of the list switch and the context panel; new limits (local graph limits, mark limits) | HW-1, HW-4, HW-5 |
| [`API_HTTP_CONTRACT.md`](../implementation/API_HTTP_CONTRACT.md), [`openapi.yaml`](../implementation/openapi.yaml) | The two fields of Section 9 | HW-2, HW-5 |
| [`GLOSSARY.md`](../implementation/GLOSSARY.md) | Home view, context panel, local graph, entity mark | HW-1, HW-4, HW-5 |
| [`GRAPH_ENGINE.md`](../implementation/GRAPH_ENGINE.md) | The use of the simulation for the mini map, if it is built | HW-2 |
| `docs/screenshots/` and the root `README.md` | New screenshots of the home view and the reader | HW-6 |

---

## 13. Differences from the Prototype

The owner accepted the prototype. These items differ in the design, with the reason.

| Prototype | Design | Reason |
| :--- | :--- | :--- |
| "Continue reading" card | Removed from the prototype and the design | HD5 |
| "Entities +N, relations +M" for each recent document | Entity count of the document | The graph data has no record of the document that added an entity first |
| Content hash, provider name, and Markdown export in the Source tab | Removed | `GET /document` does not give the first two; the export belongs to `/syndication` (RD25) |
| Row for failed ingestions in the maintenance card | Not built at this time | No route gives the count (Section 5.5) |
| Stage strip with new posts, mentions, and published documents | Done with FP4-5 (2026-11-15): the strip has the counts of `GET /api/federation/timeline?limit=1`, and its links go to the three stages | — |
| Relation colors by type and a legend of relation lines in the graph view | The graph view does not change | HD12 |
| Isolated entities and pairs removed from the map | They stay on the canvas | HD11 |
| The header search opens the command palette | First implementation: the header kept the search field of the view. Changed on 2026-11-15: as in the prototype (Section 16) | One field with a different function in each view was not clear |
| Split view and "no graph" options, and the comparison switches | Not built | HD3; the switches are a tool of the prototype |
| A force layout written for the prototype | The mini map uses the layout code of the engine | One layout in the product |

---

## 14. Closed Items (2026-11-14)

No item is open.

| ID | Item | Decision |
| :--- | :--- | :--- |
| O1 | Take isolated entities and pairs off the graph canvas and keep them only in the entity list. | Not done. They stay on the canvas (HD11). |
| O2 | Color the relation lines by relation type in the full graph. | Not done (HD12). Recommendation that the owner followed: the canvas has one color code, the entity type. A second color code for lines makes the two codes compete, and the many relation types have no stable set of colors. The relation type shows as text: the label of the lines of the selected entity, and the groups of the entity detail. The line style keeps its one meaning, solid or provisional (Section 7). |
| O3 | The text of the home title. | Accepted (HD13). |

---

## 15. Recorded Changes (2026-11-15), Implemented

The owner recorded these items, and then changed P2 after a look at the implemented page. The three items are implemented. The table has the first record; Section 15.2 has the result.

| ID | Item | Record |
| :--- | :--- | :--- |
| P1 | The label "model output" (`모델 산출`, `모델 생성`) | The label goes away from the summary block, the detail heading, and the Source tab. Reason (owner): ingestion in Claire Bible is always the work of an LLM, thus the label says nothing. The meaning of its hover text stays (an output of a model; it does not replace the source text; it can be made again). In its place, the page shows the model that the ingestion used, because a later ingestion with a better model is possible. **[code]** The `extractions` table has the columns `provider` and `model` for each document; `GET /document` does not give them at this time. The work needs a read-only field on `GET /document` (the provider and the model of the latest extraction) and its contract update. Where the model shows (reader meta line, Source tab, or the two) is a decision for the implementation request. Section 7 changes with it: the dashed line stays for provisional items and entity marks, and the rule for "an output of a model" on the summary and the detail goes away. |
| P2 | The badge "source text kept" (`원문 보존`) | The owner does not see its purpose: the link "open the source" (`원문 열기`) already tells the reader that a source exists. The proposal of Section 15.1 is not decided. |
| P3 | The Home button from the reader view goes to the graph view first and then to the home view | A defect. **[code]** `enterHome()` calls `resetWorkspaceState()`, which calls `closeReader()`; `closeReader()` calls `setCenterView('graph')` when the view is `reader`. Then `enterHome()` sets the `home` view. The fix: the reader closes without a change of the view when the home entry is the cause. An end-to-end test checks that `data-center-view` does not get the value `graph` between `reader` and `home`. |

### 15.1 Proposal for P2 (Not Used)

The owner did not take this proposal: the implemented badge is correct, and the defect was the second notice of the same fact (Section 15.2).

Facts **[code]**: "open the source" is a link to the URL on the remote site. It shows only when the document has a web URL. It does not say that Claire has a copy. The badge says a different thing: Claire has the raw text in its own store (`has_raw_file`, `raw_chars`), which is principle A1. The two can differ: the remote page can be gone while the copy stays, a document from a file or a note has a copy and no URL, and a copy can be cut (`raw_truncated`).

Proposal: remove the badge as a decoration, and show only the cases that change what the reader can do.

1. **No badge in the normal case.** A complete copy is the normal state, thus it gets no visual element. The solid-line badge of Section 7.1 goes away.
2. **One control for the copy.** The link to the stored copy (`원본 열기`, the raw file route) is next to "open the source" in the reader meta line, for a user who can read it. The two links have names that show the difference: the source on the remote site, and the copy in Claire.
3. **A notice only for an exception.** When the copy is cut (`raw_truncated`), the meta line keeps the notice with the two character counts, as `docMetaHtml()` shows it today. When a document has no stored copy, the Source tab says so in one line.
4. **The Source tab keeps the facts.** Fetch time, source type, and character counts stay there as text, without a badge.

Result: the reader page has fewer elements, and principle A1 shows as a function (the copy opens) and not as a label.

### 15.2 Result

| ID | Result |
| :--- | :--- |
| P1 | The label "model output" is gone from the summary, the detail, and the Source tab. `GET /document` gives `extraction_provider` and `extraction_model` (the latest row of `extractions`; null without a row). The reader meta line shows the model as a badge to the right of the source badge; its hover text keeps the meaning of the old label. The Source tab shows the provider and the model as text. |
| P2 | The source badge stays. A cut source text had two notices with the same counts (the old truncation tag and the source badge); the old truncation tags are gone from the reader meta line, and their reason is the hover text of the source badge (`data-trunc`: `references`, `appendix`, `appendix-references`, `stt`, `limit`). The other tags of the meta line (parser fallback, encoding flaw, scanned PDF, bundle, focus, STT, CC, image transcription) have the style of the source badge (`.prov.det`). The share page keeps its truncation tag, because it has no source badge; it gets the same style. |
| P3 | `enterHome()` sets `homeEntryActive` while it resets the workspace, thus `closeReader()` does not change the view to the graph. The view goes from the reader to the home view in one step. |
| HW-8 | Done with FP4-5: the strip has the timeline counts of `GET /api/federation/timeline?limit=1`, and its links go to the three stages of `/syndication`. |

## 16. Search Structure Change (2026-11-15), Implemented

Owner decision after the use of the first implementation: each field has one function, as in the prototype.

| Item | Before | After |
| :--- | :--- | :--- |
| Header | Input field `#q`; its function changed with the view (document filter, graph node search, decision stream filter) | Button `#palettetrigger` that opens the command palette |
| List pane | No filter field | Filter field `#q` below the list switch; it filters the list that shows |
| Decision stream | Filter through the header field | Filter field `#streamq` in the toolbar of the stream view |
| Palette shortcut | Ctrl+K (Cmd+K) | `/` (also on `/syndication`) |
| Search in document bodies | Enter in the header field (reader view only) | Enter in the document filter, or the row of the palette; the result shows in the document list |
| List toggle | First control of the header; accent color while the list is open | After the theme picker; neutral fill while the list is open |
| Palette background | `var(--panel)`, a variable that does not exist (transparent dialog) | `var(--card-bg)` |

The two buttons of the list switch are wider (padding only). The full form of the prototype for the switch and the theme picker (buttons of equal width with counts) is not built; the owner looks at it again later.
