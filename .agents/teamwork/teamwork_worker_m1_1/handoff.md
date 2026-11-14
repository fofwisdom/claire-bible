# Handoff Report — Production Feature Worker (Milestone M1)

**State Folder**: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1`
**Date**: 2026-11-14T22:28:30Z
**Author**: `teamwork_worker_m1_1`
**Recipient**: Parent Orchestrator (`21ab5432-86d2-4f40-befa-f9c2b7b06ba1`)

---

## 1. Observation

### 1.1 Scope and File Ownership
The dispatch instructions assigned Milestone M1 implementation covering:
1. **HW-5 Server Aliases**: Return entity aliases in `GET /document` while strictly preserving `detail`, `detail_format`, and `detail_html`.
2. **HW-4 Local Graph in Links Tab**: Render concentric SVG graph (Ring 1 max 24, Ring 2 max 12) with visual line grammar, keyboard and click accessibility, cross-view hover synchronization, and unshown count.
3. **HW-5 Reader Text Entity Marks**: Use CSS Custom Highlight API to mark entities with strict zero DOM mutation on `#rbody`, longest-first greedy matching, acronym and boundary rules, collision suppression, and selection without mutation.
4. **Localization and Documentation**: Locale strings in `en.json` and `ko.json`, entries in `IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, and `openapi.yaml`.
5. **Unit and E2E Tests**: Unit test in `tests/test_graphview.py` and automated Playwright E2E tests in `e2e/workspace.spec.js`.

Exclusive file write ownership adhered to:
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

### 1.2 Implemented Changes
- **`src/claire/store/queries.py`**:
  Line 243: Added `"aliases": e.aliases or []` to the node items list in `document_detail()`. `detail`, `detail_format`, and `detail_html` remain unchanged.
- **`src/claire/static/css/workspace.css`**:
  Lines 747-766: Added CSS for `#ctx-local-graph-slot`, `.ctx-local-graph`, `.ctx-lg-doc-edge`, `.ctx-lg-edge`, `.ctx-lg-edge.provisional`, `.ctx-lg-center`, `.ctx-lg-node`, `.ctx-local-graph-unshown`, `.has-hover`, `.hover-active`, `.entity-hover-active`.
- **`src/claire/static/css/reader.css`**:
  Lines 1263-1282: Added CSS for `::highlight(claire-entity)`, `::highlight(claire-entity-hover)`, and `#rlegend`.
- **`src/claire/templates/index.html`**:
  Line 459: Added `<div id="rlegend" class="rlegend" aria-live="polite"></div>` outside `#rbody` inside `#reader .sheet` under `.rbody-wrap`.
- **`src/claire/static/locales/en.json` & `ko.json`**:
  Added `context.doc.local_graph_aria`, `context.doc.local_graph_unshown`, `reader.marks.legend`, `reader.marks.limits_exceeded`, `reader.marks.unsupported`, `reader.marks.disabled`.
- **`docs/implementation/*` & `src/claire/static/docs/openapi.yaml`**:
  Updated status table in `IMPLEMENTATION_STATUS.md`, added glossary definitions in `GLOSSARY.md`, updated `GET /document` row in `API_HTTP_CONTRACT.md`, and added `aliases` array property to the schema in both `openapi.yaml` files.
- **`src/claire/static/js/app.js`**:
  - Implemented `renderLocalGraphHtml(dc)`: Center at (0, 0), Ring 1 up to 24 entities at radius 78, Ring 2 up to 12 candidates at radius 148 sorted towards connected Ring 1 angles.
  - Implemented visual line grammar: Center-to-Ring-1 lines (0.8px), solid edge lines (1.2px), provisional dashed lines (1.2px, `stroke-dasharray="3 3"`).
  - Implemented accessible SVG nodes: `role="button"`, `tabindex="0"`, `aria-label`, click handler calling `selectDocEntity()`, Enter/Space keyboard handlers, and a transparent `<circle r="14" fill="none" pointer-events="all" />` tap target.
  - Implemented cross-view hover synchronization: `setEntityHoverHighlight(id)` and `clearEntityHoverHighlight()`.
  - Exposed closure getters for `allNodes` and `allEdges` on `window.claireDebug`.
- **`src/claire/static/js/reader.js`**:
  - Implemented `applyEntityMarks(dc)` and `clearEntityMarks()` using CSS Custom Highlight API.
  - Verified strict zero DOM mutation on `#rbody` (`textContent` and `innerHTML` are unmodified).
  - Implemented `collectEntityCandidates(dc)` with 2-character drop, 2-3 char ASCII case-sensitive match, case-insensitive match for others, longest-first sort, and collision suppression.
  - Implemented word boundary matching respecting ASCII non-alphanumeric boundaries and Korean postpositions (은/는/이/가/을/를/의/에/에서/로/으로/와/과/도/만).
  - Implemented performance limits: bails out if text length > 400,000 characters or candidate names > 300.
  - Implemented caret-based click selection triggering `GET /node` without POST mutations.
  - Excluded marks on share page (`/p` or `?s=`).
  - Added hover synchronization via `setReaderEntityMarkHover(id)` and `clearReaderEntityMarkHover()`.
- **`tests/test_graphview.py`**:
  Added unit test `test_document_detail_includes_entity_aliases_and_preserves_detail` verifying alias extraction and detail preservation.
- **`e2e/workspace.spec.js`**:
  Added comprehensive tests:
  - `HW-4: local graph renders Ring 1 and Ring 2, line grammar, limits, accessible buttons, selection, and cross-view hover synchronization`
  - `HW-5: reader text entity marks verify zero DOM mutation (T1), selection without mutation (T2), share page exclusion (T3), and rule validation (T4)`

### 1.3 Test Execution Output
1. Unit test command:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Result:
   ```
   ============================== 53 passed in 2.72s ==============================
   ```
2. Playwright E2E command:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Result:
   ```
   Running 2 tests using 1 worker
   [1/2] HW-4: local graph renders Ring 1 and Ring 2, line grammar, limits, accessible buttons, selection, and cross-view hover synchronization
   [2/2] HW-5: reader text entity marks verify zero DOM mutation (T1), selection without mutation (T2), share page exclusion (T3), and rule validation (T4)
     2 passed (11.8s)
   ```

---

## 2. Logic Chain

1. **Server Aliases (HW-5)**:
   In `src/claire/store/queries.py`, `document_detail()` retrieves `ents = [e for e in ents if e.id in entity_ids]`. Each Entity model object possesses an `aliases` property. Adding `"aliases": e.aliases or []` supplies alias metadata to the frontend while leaving `detail`, `detail_format`, and `detail_html` completely unaltered. Observation 1.2 and unit test passing in Observation 1.3 confirm correctness.

2. **Zero DOM Mutation (HW-5 T1)**:
   Traditional DOM wrapping (such as wrapping terms in `<mark>` or `<span>`) breaks DOM tree references, cursor tracking, and text streaming. By using `CSS.highlights.set('claire-entity', new Highlight(...ranges))`, the DOM tree of `#rbody` is never modified. The E2E test asserts `expect(textBefore).toBe(textAfter)` and `expect(htmlBefore).toBe(htmlAfter)` when marks are applied, turned off, and re-applied, proving zero DOM mutation.

3. **No POST Mutation on Selection (HW-5 T2 & HW-4)**:
   Clicking an entity in the local graph or clicking an entity mark in reader text invokes `selectDocEntity(id)`, which calls `loadNode(id)` / `focusNode(id)`. This dispatches a single `GET /node?id=...&docs=brief` request. Both E2E tests monitor all network traffic and assert that `req.method() === 'GET'` and no `POST` request is dispatched.

4. **Concentric Local Graph Layout (HW-4)**:
   Center document node is rendered at `(0, 0)`. Ring 1 entities are sliced to `min(nodes.length, 24)` and positioned along radius 78 using evenly distributed angles. Ring 2 candidates are extracted from `allEdges` connecting Ring 1 entities to external nodes not in the document, sorted by degree, capped at 12, positioned at radius 148, and oriented towards their connected Ring 1 nodes. Edges are distinguished by `stroke-dasharray="3 3"` for provisional links. Unshown entity count text is rendered if Ring 1 > 24 or Ring 2 > 12.

5. **Cross-View Hover Synchronization**:
   `setEntityHoverHighlight(id)` adds active hover classes to matching nodes and edges in the local graph, `.ctx-entity-row` in the Entities tab, `.home-map-row` in the Knowledge Map, `.entitem` in the left list, and `claire-entity-hover` in reader CSS highlights. `clearEntityHoverHighlight()` removes them. The E2E test verifies hover activation and cleanup across these views.

---

## 3. Caveats

- **CSS Custom Highlight API Browser Support**: Modern Chromium (Chrome 105+), Safari 17.2+, and Firefox 111+ support `CSS.highlights`. In environments without `CSS.highlights`, `applyEntityMarks` safely falls back by updating `#rlegend` with the unsupported message without raising any errors.
- **Database Entity Label State in Full E2E Runs**: An earlier test in `workspace.spec.js` executes alias promotion on `ent-1`, which updates its representative name from "엔티티 A" to "별칭 A1". The HW-4 accessible label assertion checks for `/엔티티 A|별칭 A1/` to ensure resilience whether run standalone or as part of the full sequence.

---

## 4. Conclusion

Milestone M1 (HW-4 Local Graph and HW-5 Entity Marks & Server Aliases) is fully implemented, verified, and ready for consolidation:
- Genuine logic throughout; no dummy facades or hardcoded values.
- Strict zero DOM mutation on `#rbody` guaranteed.
- All 15 exclusively owned files modified cleanly.
- ASD-STE100 technical English used in comments, docstrings, and documentation.
- All unit tests (53 passed) and automated E2E tests (HW-4 and HW-5 passed) pass cleanly without errors.

---

## 5. Verification Method

To independently verify the implementation:

1. **Run Unit Tests**:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py -k "test_document_detail_includes_entity_aliases_and_preserves_detail"
   ```
   Expected: 1 passed.
   Run full unit suite:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Expected: 53 passed.

2. **Run E2E Playwright Tests**:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Expected: 2 passed.

3. **Verify Git Diff and Clean File Status**:
   ```bash
   git status --short
   git diff --stat
   ```
   Expected: Only the 15 designated files and agent state files are modified.

## 6. Exact File Diffs

```
 docs/implementation/API_HTTP_CONTRACT.md     |   2 +-
 docs/implementation/GLOSSARY.md              |   2 +
 docs/implementation/IMPLEMENTATION_STATUS.md |   2 +
 docs/implementation/openapi.yaml             |  15 ++
 e2e/workspace.spec.js                        | 330 ++++++++++++++++++++++++
 src/claire/static/css/reader.css             |  23 ++
 src/claire/static/css/workspace.css          |  19 ++
 src/claire/static/docs/openapi.yaml          |  15 ++
 src/claire/static/js/app.js                  | 176 ++++++++++++-
 src/claire/static/js/reader.js               | 367 +++++++++++++++++++++++++++
 src/claire/static/locales/en.json            |   8 +-
 src/claire/static/locales/ko.json            |   8 +-
 src/claire/store/queries.py                  |   1 +
 src/claire/templates/index.html              |   1 +
 tests/test_graphview.py                      |  47 ++++
 15 files changed, 1008 insertions(+), 8 deletions(-)
```
