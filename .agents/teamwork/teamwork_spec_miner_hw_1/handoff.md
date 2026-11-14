# Specification Investigation Report: HW-4 & HW-5

## 1. Observation

This investigation examined the design specifications, existing code, git history, and implementation documentation for units HW-4 and HW-5.

### 1.1 Direct File Observations
- `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 6.4 (Lines 243-253):
  "An inline SVG drawing, made from the data that the client has. It is not a second instance of the graph engine.
  - Center: the document. Ring 1: the entities of the document (`nodes` of `GET /document`). Ring 2: entities of other documents that have a relation with a ring 1 entity (the edges of `GET /graph`).
  - Limits: ring 1 shows the 24 entities with the highest degree; ring 2 shows 12. A text below the drawing gives the number of entities that are not shown. The Entities tab has all of them.
  - Lines: a solid line for a relation, a dashed line for a provisional relation (`dashes`), a thin gray line from the document to each ring 1 entity.
  - The line color follows the edge style of the full graph (`idleEdgeStyle()` in `app.js`). The local graph adds no color rule of its own.
  - Interaction: a click on an entity selects it. A hover on an entity marks the same entity in the lists and in the text marks (Section 8), and the reverse. Each entity is a button in the accessibility tree with its name.
  - Before `GET /graph` is loaded, the drawing has ring 1 only."
- `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 7 (Lines 256-266):
  "Visual Grammar: Solid for Facts, Dashed for Inferences
  Solid: The source text, or a result of deterministic code (Source badge, shared entity count, a relation that is not provisional, a resolution by name, alias, or acronym).
  Dashed: An output of a model, or a provisional item (Summary block, detail label, provisional relation, a resolution by embedding or by the model, entity marks)."
- `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 8.3 & 8.4 (Lines 305-331):
  R2: "The marks use the CSS Custom Highlight API: `Range` objects on the text nodes, a `Highlight` in `CSS.highlights`, and the style in `::highlight()`. The document tree of the text does not change: no element, no attribute, and no text is added. `textContent` and `innerHTML` of `#rbody` are the same with the marks on and off."
  R4: "The mark is a dotted underline (the inference line of Section 7), and the legend text says: 'a dotted underline = a string that is equal to a name of an entity of this document'."
  R5: "A click on a mark only selects the entity (`GET /node`). It adds nothing to the synthesis set and starts no research."
  R7: "One pass with a `TreeWalker` after the text is rendered. No marks when the text has more than 400,000 characters or the document has more than 300 names; the legend text then says that the marks are off for this document."
  Match Rules 1-11:
  1. Candidates: Name and aliases of document entities only (`nodes` of `GET /document`).
  2. Comparison: `toLowerCase()`. No stem, no partial word, no similarity.
  3. Borders: ASCII letter or digit at start requires non-ASCII-alphanumeric preceding character. ASCII letter or digit at end requires non-ASCII-alphanumeric following character. Korean particles permitted.
  4. Short names: < 2 characters ignored. 2-3 ASCII characters match case-sensitively.
  5. Order: Longest candidate first. Overlapping matches dropped.
  6. Two entities: String belonging to multiple entities gets no mark.
  7. Where: Text of paragraphs, list items, and table cells of summary and detail. Not in headings, code, preformatted text, links, and math (`.katex`).
  8. When: After sanitizer and `applyMathRendering()`. Recreated when text rerenders.
  9. Switch: `localStorage` key `claireEntityMarks` (default on). Off removes highlights from `CSS.highlights`.
  10. Pointer: Click on marked text selects entity when no text is selected. Caret from `document.caretPositionFromPoint()` or `caretRangeFromPoint()`.
  11. No fallback: Browser without `CSS.highlights` shows no marks; legend states this.
- `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 9 (Lines 349-360):
  "GET /document: nodes[].aliases: The alias list of the entity (HW-5)."
- `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` Section 12.1 & 12.2:
  Tests: Unit test for `GET /document` `nodes[].aliases` while `detail` and `detail_html` remain unchanged (T4). End-to-end tests for local graph rings and selection (HW-4), and T1-T3 + match cases (HW-5).
  Documentation updates: `IMPLEMENTATION_STATUS.md` Section 1, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml`.
- Existing codebase inspection:
  - `src/claire/store/queries.py` lines 236-245: `document_detail()` populates `nodes` without `aliases`. `ents` comes from `dbm.document_entities()` where each `Entity` object already has `e.aliases`.
  - `src/claire/static/js/app.js` line 5174: `renderDocLinksTab()` already contains placeholder `<div id="ctx-local-graph-slot"></div>`.
  - `src/claire/static/js/app.js` line 866: `idleEdgeStyle(scale)` returns `{opacity, width}`.
  - `src/claire/static/js/reader.js` lines 846-850: `renderReader(dc)` sets `#rbody.innerHTML`, runs `applyMathRendering(body)`, `updateReaderRail()`, and `setupReaderAccessibleBlocks(body)`.

---

## 2. Logic Chain

1. **Local Graph Placement and Rendering (HW-4)**:
   - Observation: `app.js` line 5174 reserves `#ctx-local-graph-slot` in the Links tab of the document context panel.
   - Observation: Section 6.4 dictates an inline SVG drawing made from client-side data without creating a second engine instance.
   - Deduction: When the Links tab renders for document `dc`, an inline SVG is injected into `#ctx-local-graph-slot`. The SVG renders the document node at center, Ring 1 entities at an inner radius, and Ring 2 entities at an outer radius.
   - Deduction: Ring 1 entities are extracted from `dc.nodes` (or `docNodes(dc.id, dc)`), sorted descending by degree, and capped at 24.
   - Deduction: Ring 2 entities are extracted from `allEdges` where edges connect a Ring 1 entity to an external entity (an entity not belonging to `dc.nodes`). Ring 2 entities are sorted descending by degree and capped at 12.
   - Deduction: When `allEdges` or `GET /graph` is not yet available, Ring 2 is omitted and only Ring 1 is rendered. When `GET /graph` finishes loading, the local graph re-renders.
   - Deduction: If document entities exceed 24 or Ring 2 candidate entities exceed 12, an explanatory text is rendered below the SVG showing the count of unshown entities.
   - Deduction: Lines between the document center and Ring 1 entities are thin solid gray lines. Lines between entities follow full graph line styling (`idleEdgeStyle()`); non-provisional relations use solid lines and provisional relations use dashed lines.
   - Deduction: Each entity in the SVG is wrapped with accessible semantics (`role="button"`, `tabindex="0"`, `aria-label="<entity name>"`) and keyboard handlers (Enter/Space). Clicking calls `selectDocEntity(id)` which initiates `GET /node?id=...`.
   - Deduction: Hovering over an entity triggers cross-view highlight across the SVG, Entities tab, left entity list, cards, and reader text marks.

2. **Server Aliases and Document Invariance (HW-5)**:
   - Observation: `src/claire/store/queries.py` line 237 maps `document_entities` to `nodes`. `Entity` in `src/claire/ontology/base.py` and `src/claire/store/db.py` contains `aliases`.
   - Deduction: Adding `"aliases": e.aliases or []` to each item in `nodes` provides aliases to the client while `detail`, `detail_format`, and `detail_html` remain identical.
   - Deduction: `API_HTTP_CONTRACT.md`, `docs/implementation/openapi.yaml`, and `src/claire/static/docs/openapi.yaml` must document `nodes[].aliases`.

3. **Entity Marks in Reader Text (HW-5)**:
   - Observation: Section 8.3 R2 mandates the CSS Custom Highlight API (`CSS.highlights` with `Highlight` and `Range` objects) and prohibits any DOM modification on `#rbody`.
   - Deduction: Tests T1 requires `textContent` and `innerHTML` of `#rbody` to be strictly identical whether highlights are on or off. Therefore, no `<span>` or markup elements are inserted into `#rbody`.
   - Deduction: In `reader.js`, after `applyMathRendering(body)` in `renderReader(dc)`, an entity mark engine is executed.
   - Deduction: If text length > 400,000 characters or candidate count > 300, marking is skipped and the legend explains that marks are disabled for the document.
   - Deduction: If `window.CSS` or `CSS.highlights` is unsupported, marking is skipped and the legend notes lack of browser support.
   - Deduction: Candidate strings include the entity name and all aliases. Candidates < 2 characters are dropped. Acronyms of 2-3 ASCII characters match case-sensitively; all other candidates match case-insensitively via `toLowerCase()`.
   - Deduction: Boundary checking verifies that ASCII-alphanumeric start/end characters are preceded/followed by non-ASCII-alphanumeric characters. Overlapping matches are resolved by sorting candidates by length descending and discarding overlaps. If a candidate string maps to multiple entities in the document, it is discarded.
   - Deduction: A `TreeWalker` traverses text nodes within `p, li, td, th` in summary and detail blocks, excluding headings, code, pre, links, and math elements.
   - Deduction: Two highlights are registered in `CSS.highlights`: `claire-entity` (dotted underline style) and `claire-entity-hover` (hover highlight). When `localStorage.getItem('claireEntityMarks') === '0'`, both highlights are removed from `CSS.highlights`.
   - Deduction: Clicking marked text uses `document.caretPositionFromPoint` or `caretRangeFromPoint` to identify the entity at the click coordinate and invokes `selectDocEntity(id)`, issuing only `GET /node` without any `POST` request (satisfying T2).
   - Deduction: Highlights are not applied on `share.html` (satisfying T3).

---

## 3. Caveats

1. **TreeWalker Scope**: The match algorithm operates on individual text nodes within allowed block containers (`p, li, td, th`). If an entity mention is split across inline formatting elements (for example, part bold and part normal), standard text-node matching will not span across the element boundary. This is standard behavior and avoids DOM tree mutation.
2. **Browser Support for CSS Custom Highlight API**: While Chromium and Safari modern versions support `CSS.highlights`, headless environments or older browsers without `CSS.highlights` will safely show no highlights per rule 8.4 item 11.
3. **Pre-existing E2E Failures**: Per `IMPLEMENTATION_STATUS.md` Section 3, five mobile tests and two ingest tests have pre-existing baseline failures unrelated to HW-4 or HW-5. Verification must focus on new and existing workspace suite tests.

---

## 4. Conclusion

The specification for HW-4 and HW-5 is complete, deterministic, and fully verifiable:
- HW-4 introduces the inline SVG local graph in the Links tab with Ring 1 (max 24) and Ring 2 (max 12), visual line grammar (solid, dashed, thin gray), degree sorting, unshown count text, bi-directional hover synchronization, accessible buttons, and pre-graph load fallback.
- HW-5 adds `nodes[].aliases` to `GET /document` with invariant `detail` and `detail_html`, and implements client-side reader text marks via `CSS.highlights` with exact matching rules, 400,000 char / 300 name safety limits, zero DOM mutation, `localStorage` toggle, and click selection without POST mutation.
- Documentation updates are required in `IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, and both copies of `openapi.yaml`.

---

## 5. Verification Method

1. **Server Unit Tests (`pytest`)**:
   - Run: `pytest tests/test_graphview.py`
   - Test T4: Verify `GET /document` returns `nodes[].aliases` as a list of strings for each node, and verify `detail` and `detail_html` are identical.
2. **End-to-End Tests (`playwright`)**:
   - Run: `npx playwright test e2e/workspace.spec.js`
   - HW-4 E2E test: Open document in reader, inspect Links tab local graph SVG:
     - Verify Ring 1 entities (max 24) and Ring 2 entities (max 12) rendered.
     - Verify line grammar: thin lines to center, solid lines for standard edges, dashed lines for provisional edges.
     - Verify unshown entity text when counts exceed limits.
     - Verify accessible buttons exist in SVG with names and keyboard activation.
     - Verify click on entity node triggers `GET /node` and displays details.
     - Verify hovering highlights corresponding items in lists and text.
   - HW-5 E2E test:
     - T1: Check `textContent` and `innerHTML` of `#rbody` are identical with `claireEntityMarks` enabled and disabled.
     - T2: Click on marked text in `#rbody`; verify network traffic contains only `GET /node` and zero POST requests.
     - T3: Navigate to `/share/<token>`; verify no highlight ranges or CSS highlights are applied.
     - T4: Automated table of match cases: ASCII boundaries (`Fedify에서` matches, `FedifyX` does not), 2-3 char acronym case sensitivity (`Go` matches, `go` does not), collision discard, overlap discard.
3. **Documentation Inspection**:
   - Check `git diff` on `docs/implementation/IMPLEMENTATION_STATUS.md`, `docs/implementation/GLOSSARY.md`, `docs/implementation/API_HTTP_CONTRACT.md`, `docs/implementation/openapi.yaml`, and `src/claire/static/docs/openapi.yaml`.

---

## Features Discovered

| # | Category | Feature | Description | Inputs | Outputs | Error Behavior | Discovered Via |
|---|----------|---------|-------------|--------|---------|----------------|----------------|
| 1 | HW-4 Local Graph | Inline SVG Container | SVG element rendered within `#ctx-local-graph-slot` in the Links tab of the context panel | Document context data `dc` | `<svg class="local-graph-svg" ...>` | Empty container if document has no data | Design doc §6.4, `app.js` |
| 2 | HW-4 Local Graph | Center Document Node | Visual center node representing the current document | Document ID, Title | SVG `<g>` center node at `(cx, cy)` | Fallback default title if absent | Design doc §6.4 |
| 3 | HW-4 Local Graph | Ring 1 Entities | Inner circle showing entities of the current document, sorted by degree descending, up to 24 | `dc.nodes` from `GET /document`, degree from `allNodes` | SVG entity nodes on circle radius $R_1$ | Up to 24 nodes displayed | Design doc §6.4 |
| 4 | HW-4 Local Graph | Ring 2 Entities | Outer circle showing entities of other documents related to Ring 1 entities via graph relations, up to 12 | `allEdges` from `GET /graph`, excluding document's own entities | SVG entity nodes on circle radius $R_2$ | Up to 12 nodes displayed | Design doc §6.4 |
| 5 | HW-4 Local Graph | Document to Ring 1 Lines | Thin solid gray lines connecting document center to each Ring 1 entity | Node coordinates | SVG `<line>` elements with thin gray stroke | None | Design doc §6.4, §7 |
| 6 | HW-4 Local Graph | Inter-Entity Relation Lines | Lines representing relations between entities; solid for non-provisional, dashed for provisional | `allEdges` items between displayed nodes | SVG `<line>` with solid or `stroke-dasharray="4 3"` | None | Design doc §6.4, §7 |
| 7 | HW-4 Local Graph | Pre-Graph Load State | Renders Ring 1 only before `GET /graph` has loaded; updates when graph data arrives | `allEdges === null` condition | SVG with Ring 1 only | Gracefully renders Ring 1 without throwing | Design doc §6.4 |
| 8 | HW-4 Local Graph | Unshown Entity Text | Text displayed below SVG indicating count of unshown entities when limits (>24 Ring 1 or >12 Ring 2) are exceeded | Total document entities, total Ring 2 candidates | `<p class="local-graph-unshown">` with localized string | Hidden/empty when counts are within limits | Design doc §6.4, R1 |
| 9 | HW-4 Local Graph | Entity Node Selection | Clicking an entity node selects it and opens entity detail panel | User mouse click or keyboard Enter/Space | Triggers `selectDocEntity(id)` -> `GET /node?id=...` | Ignores invalid ID | Design doc §6.4 |
| 10 | HW-4 Local Graph | Cross-View Hover Synchronization | Hovering an entity highlights it across local graph, Entities tab, left entity list, cards, and reader text | Pointer enter/leave on entity node | Dispatches hover state to `CSS.highlights` and UI rows | Clears highlight on pointer leave | Design doc §6.4, §8 |
| 11 | HW-4 Local Graph | Accessibility Button Semantics | Exposes each entity node as an accessible button with its name in the accessibility tree | Entity label | SVG `<g role="button" tabindex="0" aria-label="...">` | Fallback to entity ID if label empty | Design doc §6.4, §10 |
| 12 | HW-5 Server | Server Aliases in `GET /document` | `GET /document` response includes `nodes[].aliases` containing list of string aliases | Document ID | JSON response with `nodes[].aliases` | `aliases: []` if no aliases | Design doc §9, queries.py |
| 13 | HW-5 Server | Document Detail Invariance | `detail`, `detail_format`, and `detail_html` remain strictly unchanged before and after alias field addition | Document ID | Same text and HTML payloads as baseline | None | Design doc §8.3 R1, §8.5 T4 |
| 14 | HW-5 Entity Marks | CSS Custom Highlight API | Highlighting via `CSS.highlights`, `Highlight`, and `Range` objects with zero DOM manipulation | Document text and entity candidates | Visual highlight in browser rendering engine | Inactive if browser lacks `CSS.highlights` | Design doc §8.3 R2, §8.4 |
| 15 | HW-5 Entity Marks | Zero DOM Mutation Guarantee | Guarantees `#rbody` `textContent` and `innerHTML` are strictly identical whether marks are on or off | DOM inspection | Identical string content | None | Design doc §8.3 R2, §8.5 T1 |
| 16 | HW-5 Entity Marks | Candidate Filtering: Short Names | Discards candidates < 2 characters; matches 2-3 ASCII characters case-sensitively | Candidate string | Filtered candidate list | Skipped if length < 2 | Design doc §8.4 rule 4 |
| 17 | HW-5 Entity Marks | Candidate Filtering: Collision Discard | Discards candidate string if it matches names/aliases of 2 or more entities in the document | Candidate mapping across entities | Discarded ambiguous candidates | Skipped if collision detected | Design doc §8.4 rule 6 |
| 18 | HW-5 Entity Marks | Candidate Ordering: Longest First | Sorts candidates by length descending; discards overlapping matches | Candidate list | Non-overlapping match ranges | Shorter overlapping match dropped | Design doc §8.4 rule 5 |
| 19 | HW-5 Entity Marks | ASCII Boundary Checking | Verifies ASCII alphanumeric boundaries at start and end of match; permits Korean particles | Text position and matched string | Match accepted or rejected | Rejected if adjacent to ASCII alphanumeric | Design doc §8.4 rule 3 |
| 20 | HW-5 Entity Marks | Text Scope Limitation | Matches text nodes inside `p, li, td, th` of summary and detail; excludes headings, code, pre, links, math | DOM TreeWalker | Filtered text nodes | Excluded elements skipped | Design doc §8.4 rule 7 |
| 21 | HW-5 Entity Marks | Performance Limit Guard | Disables marks if text length > 400,000 characters or candidate count > 300 | Character count, candidate count | Highlight generation aborted; legend updated | No crash or freeze on huge documents | Design doc §8.3 R7 |
| 22 | HW-5 Entity Marks | Click Selection without Mutation | Click on marked text uses caret position to select entity via `GET /node`, with no POST request | Mouse click on marked text | Selection of entity node | No action if text is being selected | Design doc §8.3 R5, §8.5 T2 |
| 23 | HW-5 Entity Marks | Share Page Exclusion | Entity marks are never applied on the document share page (`/share/...`) | Share page context | Unmarked reader text | None | Design doc §8.3 R1, §8.5 T3 |
| 24 | HW-5 Entity Marks | LocalStorage Toggle Switch | User toggle via `localStorage` key `claireEntityMarks` (default on); removing highlights when off | Storage key value ("1" or "0") | Added or removed highlights from `CSS.highlights` | Defaults to enabled if key unset | Design doc §8.4 rule 9 |
| 25 | HW-5 Entity Marks | Legend Display | Explanatory legend for dotted underline mark, performance limits, and browser support | Status of marks | Legend text element in reader view | Informational only | Design doc §8.3 R4, R7, §8.4 rule 11 |
| 26 | Documentation | IMPLEMENTATION_STATUS Update | Documents code positions of local graph and entity marks, and registers limits (24/12 graph, 400k/300 marks) | Updated features | Markdown update in Section 1 | None | Design doc §12.2 |
| 27 | Documentation | GLOSSARY Update | Adds canonical glossary entries for Local Graph (로컬 그래프) and Entity Mark (엔티티 마크) | Terminology definitions | Markdown update in GLOSSARY.md | None | Design doc §12.2 |
| 28 | Documentation | API Contract & OpenAPI Update | Documents `nodes[].aliases` in `API_HTTP_CONTRACT.md`, `docs/implementation/openapi.yaml`, `src/claire/static/docs/openapi.yaml` | Route schema | Schema documentation updates | None | Design doc §9, §12.2 |

---

## Edge Cases

| # | Feature | Input | Observed Behavior |
|---|---------|-------|-------------------|
| 1 | HW-4 Local Graph | Document with 0 entities | SVG renders document center node only; Ring 1 and Ring 2 are empty; no unshown count text |
| 2 | HW-4 Local Graph | Document with 1 to 24 entities | All entities rendered in Ring 1; Ring 1 unshown count is 0 |
| 3 | HW-4 Local Graph | Document with > 24 entities (e.g. 30 entities) | Top 24 entities by degree rendered in Ring 1; unshown text displays remainder (6 entities) |
| 4 | HW-4 Local Graph | Connected external entities <= 12 | All connected external entities rendered in Ring 2; Ring 2 unshown count is 0 |
| 5 | HW-4 Local Graph | Connected external entities > 12 (e.g. 18 entities) | Top 12 external entities rendered in Ring 2; unshown text includes remainder (6 entities) |
| 6 | HW-4 Local Graph | External entity connected to multiple Ring 1 entities | External entity appears exactly once in Ring 2, with lines connecting to each related Ring 1 entity |
| 7 | HW-4 Local Graph | Relations between two Ring 1 entities | Relation line drawn directly between the two Ring 1 entities (solid if standard, dashed if provisional) |
| 8 | HW-4 Local Graph | Context panel opened before `GET /graph` completes | Renders Ring 1 immediately; Ring 2 and relation lines populate seamlessly once `GET /graph` resolves |
| 9 | HW-4 Local Graph | Graph fetch fails with network error | Ring 1 remains displayed; panel does not crash; error logged to console |
| 10 | HW-4 Local Graph | Light / Dark theme toggle | Stroke colors update to match `THEMES[curTheme()].edge` and `idleEdgeStyle()` |
| 11 | HW-4 Local Graph | Keyboard navigation in SVG | Focus moves through entities with Tab key; Enter or Space triggers entity selection |
| 12 | HW-5 Entity Marks | Candidate string < 2 characters (e.g. "C") | Dropped from candidate list; no mark created |
| 13 | HW-5 Entity Marks | 2-3 ASCII character acronym in different case (e.g. candidate "AI", text "ai") | Case-sensitive mismatch; no mark created |
| 14 | HW-5 Entity Marks | 2-3 ASCII character acronym in exact case (e.g. candidate "AI", text "AI") | Case-sensitive match accepted; mark created |
| 15 | HW-5 Entity Marks | Non-acronym candidate with case variation (e.g. candidate "Python", text "python") | Case-insensitive match accepted; mark created |
| 16 | HW-5 Entity Marks | ASCII word boundary failure at start (e.g. candidate "Graph", text "BioGraph") | Boundary check detects preceding ASCII letter 'o'; match rejected |
| 17 | HW-5 Entity Marks | ASCII word boundary failure at end (e.g. candidate "Graph", text "Graphics") | Boundary check detects following ASCII letter 'i'; match rejected |
| 18 | HW-5 Entity Marks | Korean particle attached to ASCII candidate (e.g. candidate "Fedify", text "Fedify에서") | Following character '에' is non-ASCII; boundary check passes; mark created |
| 19 | HW-5 Entity Marks | Korean candidate attached to Korean particle (e.g. candidate "클레어", text "클레어는") | Preceding and following characters are not ASCII-alphanumeric; match accepted |
| 20 | HW-5 Entity Marks | Two entities share candidate alias (e.g. Ent1 and Ent2 both have alias "BERT") | Collision detected; candidate string "BERT" discarded; neither entity marked |
| 21 | HW-5 Entity Marks | Overlapping candidate strings (e.g. "Graph Engine" and "Graph") | "Graph Engine" (longer) matches first; "Graph" (shorter) within that span is dropped |
| 22 | HW-5 Entity Marks | Candidate inside code block or preformatted text (`<code>`, `<pre>`) | Excluded by TreeWalker; no mark created |
| 23 | HW-5 Entity Marks | Candidate inside link (`<a>`) or heading (`<h2>`) | Excluded by TreeWalker; no mark created |
| 24 | HW-5 Entity Marks | Candidate inside mathematical formula (`.katex`) | Excluded by TreeWalker; no mark created |
| 25 | HW-5 Entity Marks | Document text exceeds 400,000 characters | Processing halted; no highlights added; legend explains marks are disabled |
| 26 | HW-5 Entity Marks | Candidate count exceeds 300 | Processing halted; no highlights added; legend explains marks are disabled |
| 27 | HW-5 Entity Marks | User selects text across marked phrase and clicks | `window.getSelection().isCollapsed` is false; selection navigation aborted; no entity load |
| 28 | HW-5 Entity Marks | Click on marked text | Caret identified within mark Range; entity selected; only `GET /node` sent; no POST sent |
| 29 | HW-5 Entity Marks | Document rendered in `/share/<token>` | Highlights omitted; `share.html` contains no entity mark scripts or highlights |
| 30 | HW-5 Entity Marks | `claireEntityMarks` set to "0" in `localStorage` | Both `claire-entity` and `claire-entity-hover` removed from `CSS.highlights` |
| 31 | HW-5 Entity Marks | Browser without `CSS.highlights` support | Safe no-op; no elements inserted; legend indicates browser lacks highlight support |
| 32 | HW-5 Entity Marks | Reader content re-rendered (e.g. zoom change, tab switch) | Old ranges cleared; fresh ranges generated and registered in `CSS.highlights` |
| 33 | HW-5 Server | Entity has empty or null aliases in database | `GET /document` serializes `aliases: []`; no null pointer exceptions |
