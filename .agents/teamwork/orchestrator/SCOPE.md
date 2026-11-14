# Scope: HW-4 (Local Graph and Hover Links) and HW-5 (Entity Marks and Server Aliases)

## Architecture
This project implements units HW-4 and HW-5 from `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` as production features for Claire Bible, delivered in a single combined commit.

The architecture establishes:
1. **HW-4 Local Graph in Links Tab**:
   - Renders an inline SVG drawing inside `#ctx-local-graph-slot` in the Links tab of the document context panel.
   - Layout geometry: Document center node at `(0, 0)`; Ring 1 (radius 78) showing up to 24 document entities from `dc.nodes` sorted descending by degree; Ring 2 (radius 148) showing up to 12 external entities related via `allEdges` from `GET /graph`.
   - Before `GET /graph` is loaded, displays Ring 1 only. Re-renders automatically when graph data arrives.
   - Text below the SVG displays the total count of unshown entities when document entities exceed 24 or Ring 2 candidate entities exceed 12.
   - Visual line grammar: Thin gray lines connecting document center to Ring 1 entities; solid lines for non-provisional relations; dashed lines (`stroke-dasharray="3 3"`) for provisional relations (`dashes === true`). Line colors follow graph edge style (`var(--border)`).
   - Accessibility: Each entity node in the SVG is an accessible button with `role="button"`, `tabindex="0"`, `aria-label="[entity name]"`, responsive to mouse click and keyboard Enter/Space.
   - Selection: Clicking an entity node triggers `selectDocEntity(id)` -> `loadNode(id)` -> `GET /node?id=...` without initiating any POST request or data mutation.
   - Cross-view hover synchronization: Hovering an entity highlights the same entity across the local graph, Entities tab, Knowledge Map card, left entity list, and reader text marks.
2. **HW-5 Server Aliases in `GET /document`**:
   - Updates `document_detail()` in `src/claire/store/queries.py` to include `"aliases": e.aliases or []` in each node of `nodes`.
   - Keeps `detail`, `detail_format`, and `detail_html` strictly identical before and after.
3. **HW-5 Entity Marks in Reader Text**:
   - Implements entity text marks using the CSS Custom Highlight API (`CSS.highlights` with `Highlight` and `Range` objects) on `#rbody`.
   - Zero DOM mutation guarantee: `textContent` and `innerHTML` of `#rbody` remain strictly identical whether marks are on or off (`localStorage` switch `claireEntityMarks`).
   - Legend: `#rlegend` is placed outside `#rbody` (in `#reader .sheet` under `.rbody-wrap`) to preserve `#rbody` DOM tree.
   - Matching rules: Case-insensitive via `toLowerCase()`, except 2-3 ASCII characters which match case-sensitively. Names < 2 characters ignored.
   - ASCII boundary checks: Matched candidate must have non-ASCII-alphanumeric boundary characters if start/end is ASCII alphanumeric (Korean particles allowed).
   - Longest candidate first: Candidates sorted by length descending; overlapping matches dropped.
   - Collision suppression: Candidate strings matching names/aliases of 2 or more entities in the document receive no mark.
   - Scope: `TreeWalker` processes text nodes in `p`, `li`, `td`, `th` in summary and detail; excludes headings, code, pre, links, and math (`.katex`).
   - Limits: Text length > 400,000 characters or candidate count > 300 names halts marking and updates legend text.
   - Pointer interaction: Clicking marked text uses caret position to select the entity (`GET /node`) without POST or mutation.
   - Share page: Entity marks are excluded on the share page.
4. **Documentation and Deliverable**:
   - Updates `docs/implementation/IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml`, and `src/claire/static/docs/openapi.yaml`.
   - All comments, tests, and documentation adhere to ASD-STE100, English commit message, no hard-wrapped lines.
   - All changes delivered in a single combined git commit.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | `LocalGraphSvg` | Inline SVG container inside `#ctx-local-graph-slot` | M1 | Design §6.4 |
| 2 | `LocalGraphCenterNode` | Center node representing the current document | M1 | Design §6.4 |
| 3 | `LocalGraphRing1` | Ring 1 entities (up to 24 sorted by degree descending) | M1 | Design §6.4 |
| 4 | `LocalGraphRing2` | Ring 2 entities (up to 12 external related entities) | M1 | Design §6.4 |
| 5 | `LocalGraphDocLines` | Thin gray lines from document center to Ring 1 entities | M1 | Design §6.4, §7 |
| 6 | `LocalGraphRelationLines` | Solid lines for facts, dashed lines for provisional edges | M1 | Design §6.4, §7 |
| 7 | `LocalGraphPreloadFallback` | Render Ring 1 only before `GET /graph` loads | M1 | Design §6.4 |
| 8 | `LocalGraphUnshownCount` | Text below SVG displaying count of unshown entities | M1 | Design §6.4 |
| 9 | `LocalGraphSelection` | Click/keyboard selection of entity node -> `GET /node` | M1 | Design §6.4 |
| 10 | `LocalGraphA11yButtons` | Accessible button semantics (`role="button"`, `aria-label`) | M1 | Design §6.4 |
| 11 | `CrossViewHoverSync` | Cross-view hover synchronization across SVG, lists, cards, text | M1 | Design §6.4, §8 |
| 12 | `ServerDocumentAliases` | `GET /document` returns `nodes[].aliases` as list of strings | M1 | Design §9 |
| 13 | `DetailInvariance` | `detail` and `detail_html` remain strictly identical | M1 | Design §8.3 R1 |
| 14 | `CssCustomHighlightApi` | Reader marks via `CSS.highlights` and `Range` objects | M1 | Design §8.3 R2 |
| 15 | `ZeroDomMutation` | `#rbody` `textContent` and `innerHTML` identical with marks on/off | M1 | Design §8.3 R2, §8.5 T1 |
| 16 | `CandidateFilteringShortNames` | Discard < 2 chars, 2-3 ASCII chars case-sensitive | M1 | Design §8.4 rule 4 |
| 17 | `CandidateCollisionSuppression` | No mark if candidate belongs to multiple entities | M1 | Design §8.4 rule 6 |
| 18 | `CandidateLongestFirst` | Longest candidate first, discard overlapping matches | M1 | Design §8.4 rule 5 |
| 19 | `AsciiBoundaryCheck` | Non-alphanumeric boundary check, permit Korean particles | M1 | Design §8.4 rule 3 |
| 20 | `TextScopeLimitation` | Match `p, li, td, th`, exclude headings, code, pre, links, math | M1 | Design §8.4 rule 7 |
| 21 | `PerformanceLimitGuards` | Skip marks if text > 400,000 chars or candidates > 300 names | M1 | Design §8.3 R7 |
| 22 | `ReaderMarkSelection` | Click marked text selects entity via `GET /node` (zero POST) | M1 | Design §8.3 R5, §8.5 T2 |
| 23 | `SharePageExclusion` | No entity marks on `/p?s=...` share page | M1 | Design §8.5 T3 |
| 24 | `LocalStorageSwitch` | Toggle marks via `claireEntityMarks` key | M1 | Design §8.4 rule 9 |
| 25 | `UnitAndE2eTests` | Unit tests in `test_graphview.py` and Playwright E2E in `workspace.spec.js` | M1 | Design §12 |
| 26 | `DocumentationUpdates` | Update status, glossary, contract, and openapi specifications | M1 | Design §12.2 |
| 27 | `SingleGitCommit` | Combine all HW-4 and HW-5 changes into a single git commit | M1 | Request R3 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | HW-4 & HW-5 Production Features, Tests & Git Commit | Features 1-27 | Survey (Phase 0) | DONE |

## Interface Contracts
### 1. `GET /document` (Server)
- Each item in `nodes` array contains:
  `{"id": str, "label": str, "group": str, "observations": list[str], "aliases": list[str]}`
- `detail` and `detail_html` properties remain completely invariant.

### 2. Local Graph Inline SVG (Client)
- Mount container: `#ctx-local-graph-slot` inside `renderDocLinksTab(dc)`.
- SVG element: `<svg class="ctx-local-graph" viewBox="-160 -160 320 320" role="region" aria-label="...">`
- Center node: Document icon and label at `(0, 0)`.
- Ring 1 nodes: Up to 24 entities from `dc.nodes`, sorted descending by degree, placed at radius 78.
- Ring 2 nodes: Up to 12 external entities connected to Ring 1 via `allEdges`, placed at radius 148.
- Lines:
  - Document to Ring 1: `<line class="ctx-lg-doc-edge" x1="0" y1="0" x2="x" y2="y">` (thin line, `stroke-width="0.8"`).
  - Inter-entity standard: `<line class="ctx-lg-edge" ...>` (solid line, `stroke-width="1.2"`).
  - Inter-entity provisional: `<line class="ctx-lg-edge provisional" stroke-dasharray="3 3" ...>` (dashed line, `stroke-width="1.2"`).
- Entity node accessibility: `<g role="button" tabindex="0" aria-label="[entity name]" data-entity-id="[id]">`.
- Below SVG: `<p class="ctx-local-graph-unshown">` displaying `+{count} more entities not shown` when unshown > 0.

### 3. CSS Custom Highlight API & Reader Marks (Client)
- Highlight names: `CSS.highlights.set('claire-entity', new Highlight(...ranges))` and `CSS.highlights.set('claire-entity-hover', ...)`.
- `#rbody` DOM tree: Strictly untouched (no elements, no attributes, no text mutations added).
- Legend element: `#rlegend` placed outside `#rbody` in `#reader .sheet`.
- Hover sync interface:
  - `window.setEntityHoverHighlight(entityId)`
  - `window.clearEntityHoverHighlight()`
  - `window.setReaderEntityMarkHover(entityId)`
  - `window.clearReaderEntityMarkHover()`

## Code Layout
- Target Files:
  - `src/claire/store/queries.py`
  - `src/claire/static/js/app.js`
  - `src/claire/static/js/reader.js`
  - `src/claire/static/css/workspace.css`
  - `src/claire/static/css/reader.css`
  - `src/claire/templates/index.html`
  - `src/claire/static/locales/en.json`
  - `src/claire/static/locales/ko.json`
  - `docs/implementation/IMPLEMENTATION_STATUS.md`
  - `docs/implementation/GLOSSARY.md`
  - `docs/implementation/API_HTTP_CONTRACT.md`
  - `docs/implementation/openapi.yaml`
  - `src/claire/static/docs/openapi.yaml`
  - `tests/test_graphview.py`
  - `e2e/workspace.spec.js`
