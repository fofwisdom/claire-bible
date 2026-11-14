# Handoff Report — HW-4 Local Graph and Hover Connections Investigation

## 1. Observation

### 1.1 Context Panel Slot and Tab Architecture
- File `src/claire/static/js/app.js` at line 5173 defines `renderDocLinksTab(dc)`:
```javascript
function renderDocLinksTab(dc){
  let h = '<div id="ctx-local-graph-slot"></div>';
  const shared = computeSharedDocs(dc);
  h += '<section aria-labelledby="ctx-shared-heading"><div id="ctx-shared-heading" class="ctx-sec-head"><span>' + esc(tr('context.doc.shared_docs')) + '</span></div>';
```
- In commit HW-3 (`1147c28b`), `#ctx-local-graph-slot` was created as the dedicated mount container for the local graph in the Links tab.
- In `src/claire/static/js/app.js` at lines 5342-5395, `renderDocPanel(dc)` builds the panel HTML and executes:
```javascript
  if(tab==='links'){
    h+=renderDocLinksTab(dc);
  } else if(tab==='entities'){
    h+=renderDocEntitiesTab(dc, ns);
  } else { ... }
  panel.innerHTML=h;
```
- This confirms that `renderDocLinksTab(dc)` can directly inject the inline SVG markup into `#ctx-local-graph-slot`.

### 1.2 Entity Extraction and Degree Sorting
- File `src/claire/static/js/app.js` at lines 5102-5122 defines `docNodes(docId, dc)`:
```javascript
function docNodes(docId, dc){
  if(dc && Array.isArray(dc.nodes) && dc.nodes.length > 0){
    const out = dc.nodes.map(n => {
      const existing = (allNodes && typeof allNodes.get === 'function') ? allNodes.get(n.id) : null;
      return {
        id: n.id,
        label: n.label || n.name || n.id,
        group: n.group || n.type || 'Concept',
        degree: existing ? (existing.degree || 0) : 0,
        sources: existing ? (existing.sources || [docId]) : [docId],
        obs: n.observations || (existing ? existing.obs : []),
      };
    });
    out.sort((a,b)=> (b.degree||0)-(a.degree||0));
    return out;
  }
  const out=[]; if(!allNodes) return out;
  allNodes.forEach(n=>{ if((n.sources||[]).includes(docId)) out.push(n); });
  out.sort((a,b)=> (b.degree||0)-(a.degree||0));
  return out;
}
```
- The function sorts entities by degree in descending order.

### 1.3 Asynchronous Graph Loading Lifecycle
- File `src/claire/static/js/app.js` at lines 1508-1530:
```javascript
let graphInitialized = false;
let graphSuspended = false;

function initGraph(onComplete){
  if(graphInitialized){
    if(onComplete) onComplete();
    return;
  }
  fetch(apiUrl('graph')).then(r=>{ if(!r.ok) throw new Error('graph fetch HTTP '+r.status); return r.json(); }).then(d=>{
    graphInitialized = true;
    const G = window.ClaireGraph;
    if(G && G.DataSet && G.Network){
      const rawNodes = ((d && d.nodes) || []).map(n => ({ ...n, size: nodeRadius(n.degree), font: { size: nodeFontSize(n.degree) } }));
      const rawEdges = (d && d.edges) || [];
      applyTopologicalStructure(rawNodes, rawEdges);
      allNodes = new G.DataSet(rawNodes);
      allEdges = new G.DataSet(rawEdges);
...
```
- File `src/claire/static/js/app.js` at lines 1748-1751 contains:
```javascript
  if(activeDoc && !selectedNodeId){
    if(curReaderDocData && curReaderDocData.id===activeDoc) renderDocPanel(curReaderDocData);
    else loadDocPanel(activeDoc);
  }
```
- When `initGraph()` finishes, if `activeDoc` is set, `renderDocPanel(curReaderDocData)` executes automatically.

### 1.4 Visual Grammar and Edge Data Model
- File `src/claire/store/queries.py` lines 61-75 defines the edge structure returned by `GET /graph`:
```python
    edges = [
        {
            "id": f"e{i}",
            "from": r.source_id,
            "to": r.target_id,
            "label": r.type,
            "arrows": "to",
            "dashes": r.provisional,
        }
        for i, r in enumerate(
            r for r in rels if r.source_id in visible_ent_ids and r.target_id in visible_ent_ids
        )
    ]
```
- Each edge object contains `from`, `to`, `label`, and `dashes` (boolean for provisional relations).
- In `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 6.4:
  - "Center: the document. Ring 1: the entities of the document (`nodes` of `GET /document`). Ring 2: entities of other documents that have a relation with a ring 1 entity (the edges of `GET /graph`)."
  - "Limits: ring 1 shows the 24 entities with the highest degree; ring 2 shows 12. A text below the drawing gives the number of entities that are not shown. The Entities tab has all of them."
  - "Lines: a solid line for a relation, a dashed line for a provisional relation (`dashes`), a thin gray line from the document to each ring 1 entity."
  - "The line color follows the edge style of the full graph (`idleEdgeStyle()` in `app.js`). The local graph adds no color rule of its own."
  - "Before `GET /graph` is loaded, the drawing has ring 1 only."

### 1.5 Interactive Selection and Entity Loading
- File `src/claire/static/js/app.js` lines 5317-5323:
```javascript
function selectDocEntity(id){
  if(centerView === 'graph'){
    focusNode(id);
  } else {
    loadNode(id, true, false);
  }
}
```
- `loadNode(id, true, false)` sets `selectedNodeId = id`, sets context panel open, fetches `GET /node?id=id`, and renders `renderPanel(node)`.
- No HTTP POST request or data mutation is executed.

### 1.6 Current Elements Displaying Entities
- Context panel Entities tab: `src/claire/static/js/app.js:5202`:
  `<button type="button" class="ctx-entity-row nodebtn" role="listitem" data-entity-id="..." ...>`
- Home overview Knowledge Map card: `src/claire/static/js/app.js:6128`:
  `<button type="button" class="home-map-row" role="listitem" data-home-ent="..." ...>`
- Left entity list: `src/claire/static/js/app.js:4482`:
  `<div class="entitem..." role="listitem" data-ent-id="..." ...>`

### 1.7 Test Execution Results
- `uv run pytest tests/test_graphview.py` passed (52 tests passed in 2.60s).
- `npx --prefix e2e playwright test --config e2e/playwright.config.js -g "HW-3"` passed (1 passed in 9.8s).

---

## 2. Logic Chain

### LC1: Inline SVG Injection in Links Tab
- Observation 1.1 shows `#ctx-local-graph-slot` exists inside `renderDocLinksTab(dc)` at line 5174.
- `renderDocLinksTab(dc)` returns HTML that `renderDocPanel(dc)` inserts into `panel.innerHTML`.
- Therefore, a synchronous helper `renderLocalGraphHtml(dc)` can construct an `<svg class="ctx-local-graph" ...>` string and replace `#ctx-local-graph-slot` with this inline SVG and its unshown counter element.

### LC2: Ring 1 Node Placement and Limit
- Observation 1.2 shows `docNodes(dc.id, dc)` extracts document entities sorted by degree descending.
- The specification sets a maximum limit of 24 entities for Ring 1.
- Taking `const r1 = allDocEntities.slice(0, 24);` gives the Ring 1 nodes.
- Unshown Ring 1 count is `Math.max(0, allDocEntities.length - 24)`.
- Using radius `R1 = 78` and angle `ang[e.id] = -Math.PI / 2 + (i * 2 * Math.PI / r1.length)` places each node at `pos[e.id] = [Math.cos(ang[e.id]) * 78, Math.sin(ang[e.id]) * 78]`.

### LC3: Ring 2 Node Placement and Limit
- Observation 1.3 and 1.4 show `allEdges` contains graph relations when `graphInitialized` is true.
- Related entities are nodes connected to Ring 1 entities in `allEdges` where the node ID is not in the open document's entity set (`inDocSet = new Set(allDocEntities.map(e => e.id))`).
- Candidate nodes are sorted by degree descending, with tie-break on entity label.
- The specification sets a maximum limit of 12 entities for Ring 2.
- Taking `const r2 = candidates.slice(0, 12);` gives the Ring 2 nodes.
- Unshown Ring 2 count is `Math.max(0, candidates.length - 12)`.
- Total unshown entities displayed below the drawing is `unshownRing1 + unshownRing2`.
- Angular positions for Ring 2 are calculated from the average angle of connected Ring 1 entities, sorted by angle to avoid overlapping lines, with radius `R2 = 148`.

### LC4: Visual Line Grammar
- Observation 1.4 specifies three line types:
  1. Document center (0, 0) to each Ring 1 entity: thin line (`stroke-width="0.8"`, `stroke="var(--border)"`, `opacity="0.6"`).
  2. Non-provisional relation (`!edge.dashes`): solid line (`stroke-width="1.2"`, `stroke="var(--border)"`, `opacity="0.6"`).
  3. Provisional relation (`edge.dashes === true`): dashed line (`stroke-width="1.2"`, `stroke-dasharray="3 3"`, `stroke="var(--border)"`, `opacity="0.6"`).
- Stroke color matches graph edge style (`var(--border)` or `T().edge`) without custom line colors, satisfying decision HD12.

### LC5: Pre-Graph Loading Lifecycle
- Observation 1.3 shows `graphInitialized` indicates whether `initGraph()` has completed.
- When `graphInitialized` is false or `!allNodes || !allEdges`:
  - Ring 1 entities are extracted from `dc.nodes` via `docNodes(dc.id, dc)`.
  - Ring 2 entities are set to empty (`r2 = []`).
  - No inter-entity relation lines are drawn.
  - The SVG displays the document center, Ring 1 entities, and Ring 1 to center lines.
- When `initGraph()` finishes, lines 1748-1751 call `renderDocPanel(curReaderDocData)` if `activeDoc` is present.
- This automatically re-renders `renderDocLinksTab(dc)` with Ring 2 entities and relation lines once data arrives.

### LC6: Interactive Selection
- Observation 1.5 shows `selectDocEntity(id)` invokes `loadNode(id, true, false)` when not in graph view.
- Attaching `onclick="selectDocEntity(id)"` to SVG entity buttons sends `GET /node?id=id`.
- The entity detail opens in `#panel`, and the backlink returns the user to the document context panel. No data mutation or POST request occurs.

### LC7: Cross-View Hover Synchronization
- Observation 1.6 shows existing views mark entity items with data attributes:
  - Context panel: `data-entity-id`
  - Knowledge Map card: `data-home-ent`
  - Entity list: `data-ent-id`
- Adding `setEntityHoverHighlight(id)` and `clearEntityHoverHighlight()` allows:
  1. Local graph SVG: toggle `.hover-active` on the matching `.ctx-lg-node` and connected `.ctx-lg-edge` elements, and toggle `.has-hover` on `.ctx-local-graph`.
  2. Context panel: toggle `.entity-hover-active` on `.ctx-entity-row[data-entity-id="id"]`.
  3. Home Knowledge Map card: toggle `.entity-hover-active` on `.home-map-row[data-home-ent="id"]`.
  4. Left entity list: toggle `.entity-hover-active` on `.entitem[data-ent-id="id"]`.
  5. Reader marks: invoke hook `window.setReaderEntityMarkHover(id)` / `window.clearReaderEntityMarkHover()` (for HW-5).
- Hovering an entity in any of these components highlights all matching representations synchronously.

### LC8: Accessibility Tree Exposure
- In SVG, `<g role="button" tabindex="0" aria-label="${label}" data-entity-id="${id}">` maps directly to an accessible button in the browser accessibility tree per W3C SVG-AAM.
- Adding keyboard listeners for `Enter` and `Space` triggers `selectDocEntity(id)`.
- Screen readers announce each node as an accessible button with the entity name.

---

## 3. Caveats

1. Concurrency with HW-5:
   - HW-5 implements reader text marks using the CSS Custom Highlight API.
   - The hover synchronization function in HW-4 must check if reader highlighting functions exist (`typeof window.setReaderEntityMarkHover === 'function'`) before calling them to avoid errors when HW-5 is not yet loaded.
2. SVG Label Truncation:
   - Entity names can be long. Labels should be clipped or constrained with `text-overflow` or length checks if names exceed 20 characters to prevent clipping at the SVG viewport edge.
3. Multiple Edges Between Same Entities:
   - Multiple relation records between the same two entities in `allEdges` should be deduped to avoid rendering redundant duplicate lines.

---

## 4. Conclusion

HW-4 requires modifications across four main code locations and one test file:

1. `src/claire/static/js/app.js`:
   - Implement `renderLocalGraphHtml(dc)`:
     - Center node representing the document (`(0, 0)`).
     - Ring 1 calculation: top 24 entities from `docNodes(dc.id, dc)` sorted by highest degree.
     - Ring 2 calculation: top 12 entities from other documents connected via `allEdges`, sorted by degree, arranged angularly.
     - Pre-graph loading state: display Ring 1 only if `!graphInitialized || !allNodes || !allEdges`.
     - Visual grammar: thin lines to center, solid lines for non-provisional relations, dashed lines (`stroke-dasharray="3 3"`) for provisional relations.
     - Unshown count paragraph below SVG if `unshownTotal > 0`.
     - Accessible button elements (`<g role="button" tabindex="0" aria-label="...">`) with click and keydown handlers.
   - Implement cross-view hover synchronization:
     - `setEntityHoverHighlight(id)` and `clearEntityHoverHighlight()`.
     - Wire hover events on local graph nodes, context panel entity rows, home card rows, and left entity list items.
   - Update `renderDocLinksTab(dc)` to invoke `renderLocalGraphHtml(dc)`.

2. `src/claire/static/css/workspace.css`:
   - Add styles for `.ctx-local-graph`, `.ctx-lg-node`, `.ctx-lg-edge`, `.hover-active`, `.has-hover`, and `.ctx-local-graph-unshown`.
   - Add styles for `.entity-hover-active` across `.ctx-entity-row`, `.home-map-row`, and `.entitem`.

3. `src/claire/static/locales/en.json` and `src/claire/static/locales/ko.json`:
   - Add keys:
     - `context.doc.local_graph_aria`: "Local graph of this document" / "이 문서의 로컬 그래프"
     - `context.doc.local_graph_unshown`: "+{count} more entities not shown" / "외 {count}개 엔티티 미표시"

4. `docs/implementation/IMPLEMENTATION_STATUS.md` and `docs/implementation/GLOSSARY.md`:
   - Record HW-4 completion, limits (Ring 1 max 24, Ring 2 max 12), and glossary definition for Local Graph.

5. `e2e/workspace.spec.js`:
   - Add Playwright E2E tests verifying:
     - Local graph renders Ring 1 and Ring 2 with correct limits and visual line grammar.
     - Pre-graph loading behavior (displays Ring 1 only).
     - Selection of entity node triggers `GET /node` and opens entity details without mutation.
     - Hovering entity node highlights corresponding items across lists and cards.
     - Accessibility tree exposure: entity nodes have `role="button"` and accessible names.
     - Keyboard navigation: Tab focuses entity node, Enter key selects entity.

---

## 5. Verification Method

### 5.1 Pytest Suite
Run the test suite using `uv`:
```bash
uv run pytest tests/test_graphview.py
```
Expected result: 52 passed.

### 5.2 Playwright E2E Suite
Run the Playwright E2E tests with the configuration file:
```bash
npx --prefix e2e playwright test --config e2e/playwright.config.js -g "HW-3"
```
Expected result: Existing context panel tests pass without regression.

### 5.3 HW-4 Verification Checklist
When HW-4 is implemented, verify the following conditions:
1. Open document `doc-1` in reader view.
2. In the context panel Links tab, verify the inline SVG `<svg class="ctx-local-graph">` exists.
3. Verify Ring 1 nodes count <= 24 and Ring 2 nodes count <= 12.
4. Verify non-provisional relation lines have solid strokes and provisional lines have dashed strokes (`stroke-dasharray`).
5. Verify document to Ring 1 lines have thin strokes (`0.8px`).
6. Verify entity nodes have `role="button"` and `aria-label` matching the entity label.
7. Click an entity node in the local graph; verify `selectedNodeId` is updated, context panel switches to entity detail, and no HTTP POST request is sent.
8. Hover over an entity node in the local graph; verify matching elements in context panel, home cards, and left entity list receive `.entity-hover-active`.
9. Verify unshown count paragraph displays when total entities exceed ring limits.

### 5.4 Invalidation Conditions
This analysis is invalidated if:
- The context panel slot `#ctx-local-graph-slot` is removed or renamed in `renderDocLinksTab`.
- The data structure of `allEdges` or `GET /graph` removes the `dashes` attribute.
- The W3C SVG-AAM specification is replaced by a pattern requiring HTML `<foreignObject>` for accessible buttons.
