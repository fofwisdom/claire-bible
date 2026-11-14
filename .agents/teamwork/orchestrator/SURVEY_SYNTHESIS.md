# Survey Synthesis: HW-4 & HW-5 Units

## 1. Catalog of Inputs
1. **Source 1 (`teamwork_spec_miner_hw_1`)**:
   - Analyzed `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` (Sections 6.4, 7, 8, 9, 12).
   - Specified complete requirements, limits, visual grammar, matching rules, limits, and documentation requirements.
2. **Source 2 (`teamwork_explorer_hw_2`)**:
   - Analyzed client codebase for HW-4 (Local Graph and Hover Connections).
   - Specified inline SVG architecture, Ring 1 (max 24) and Ring 2 (max 12) layout, line styles, unshown count text, a11y buttons, click selection, and hover synchronization across all views.
3. **Source 3 (`teamwork_explorer_hw_3`)**:
   - Analyzed server endpoint `GET /document` and reader text for HW-5.
   - Specified `nodes[].aliases` addition in `queries.py` with invariant `detail`/`detail_html`.
   - Specified CSS Custom Highlight API rules, matching engine, collision suppression, short name handling, text limits, zero DOM mutation, click selection without POST, share page exclusion, and test baselines (`pytest` and `playwright`).

## 2. Consensus Findings
- **Single Git Commit**: HW-4 and HW-5 must be delivered together in a single commit adhering to ASD-STE100, English commit message, no hard-wrapped lines, unit tests for module boundaries/IO/external libs, and at least one E2E test.
- **HW-4 Local Graph**:
  - Rendered as an inline SVG inside `#ctx-local-graph-slot` in the Links tab of the context panel.
  - Document center node at `(0, 0)`.
  - Ring 1 contains up to 24 document entities from `dc.nodes` sorted descending by degree.
  - Ring 2 contains up to 12 external entities related via `allEdges` from `GET /graph`.
  - If `allEdges` or `GET /graph` is not yet loaded, render Ring 1 only; when `GET /graph` loads, re-render automatically.
  - Text below SVG shows the total count of unshown entities if Ring 1 > 24 or Ring 2 > 12.
  - Line grammar: thin gray line from document center to Ring 1; solid line for non-provisional relations; dashed line for provisional relations (`dashes === true`).
  - Accessibility: Each entity node is exposed as an accessible button with `role="button"`, `tabindex="0"`, `aria-label="[name]"`, responding to click and Enter/Space.
  - Selection: Clicking an entity node calls `selectDocEntity(id)` -> `GET /node` without mutation.
  - Hover synchronization: Hovering an entity highlights the same entity across the local graph, Entities tab, Knowledge Map card, left entity list, and reader text marks.
- **HW-5 Server Aliases**:
  - `src/claire/store/queries.py` adds `"aliases": e.aliases or []` to each node in `document_detail()`.
  - `detail` and `detail_html` remain strictly unchanged.
- **HW-5 Entity Marks in Reader Text**:
  - Uses CSS Custom Highlight API (`CSS.highlights` with `Highlight` and `Range` objects) on `#rbody`.
  - Zero DOM mutation guarantee: `textContent` and `innerHTML` of `#rbody` remain strictly identical whether marks are on or off (`localStorage` switch `claireEntityMarks`).
  - Matching rules: Case-insensitive via `toLowerCase()`, except 2-3 ASCII characters which match case-sensitively. Names < 2 characters ignored.
  - ASCII boundary checks: Matched candidate must have non-ASCII-alphanumeric boundary characters if start/end is ASCII alphanumeric (Korean particles allowed).
  - Longest match first: Candidates sorted by length descending, overlaps discarded.
  - Collision suppression: If candidate string belongs to 2 or more entities in the document, no mark is applied.
  - Traversal scope: `TreeWalker` on text nodes in `p`, `li`, `td`, `th` of summary and detail; headings, code, pre, links, and math (`.katex`) are excluded.
  - Safety limits: If text length > 400,000 chars or candidate count > 300 names, highlighting is skipped and legend updated.
  - Click navigation: Clicking marked text uses caret position to select entity via `GET /node` without POST or mutation.
  - Share page exclusion: No marks applied on share page.
- **Documentation Updates**:
  - `docs/implementation/IMPLEMENTATION_STATUS.md` (Section 1 status table)
  - `docs/implementation/GLOSSARY.md` (Local graph and entity mark entries)
  - `docs/implementation/API_HTTP_CONTRACT.md` (`GET /document` aliases field)
  - `docs/implementation/openapi.yaml` and `src/claire/static/docs/openapi.yaml` (`GET /document` aliases schema)

## 3. Resolved Conflicts / Clarifications
- **Legend Location**: The legend must be placed outside `#rbody` (in `#reader .sheet` or as `#rlegend` within `.rbody-wrap`) so that legend updates do not affect `#rbody.innerHTML` or `#rbody.textContent`.
- **Pre-existing E2E Tests**: Existing mobile and ingest failures documented in `IMPLEMENTATION_STATUS.md` Section 3 are unrelated to HW-4/HW-5. Target test commands are:
  - `pytest tests/test_graphview.py`
  - `npx playwright test e2e/workspace.spec.js` (or `cd e2e && npx playwright test workspace.spec.js`)
- **Hover Sync Hook**: HW-4 and HW-5 synchronize hover state via global functions (`setEntityHoverHighlight`, `setReaderEntityMarkHover`), checking for existence before invocation to avoid runtime errors during independent or progressive loading.

## 4. Dissenting Views / Gaps
- None. All three investigators produced harmonious, reinforcing technical specifications matching the design document.
