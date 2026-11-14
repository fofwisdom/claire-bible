# Handoff Report — HW-4 Adversarial Verifier

**State Folder**: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_challenger_hw_1`
**Author**: `teamwork_challenger_hw_1`
**Recipient**: Parent Orchestrator (`21ab5432-86d2-4f40-befa-f9c2b7b06ba1`)
**Date**: 2026-11-14T13:44:00Z
**Verdict**: **APPROVE**

---

## 1. Observation

Direct observations from code inspection and test execution:

1. **Concentric Layout Geometry**:
   - `src/claire/static/js/app.js` (lines 5204-5219, 5262-5269, 5303):
     - Center document node: rendered at `(0, 0)` with `<circle cx="0" cy="0" r="14" />`.
     - Ring 1 radius: computed as `Math.round(Math.cos(angle) * 78 * 10) / 10` and `Math.round(Math.sin(angle) * 78 * 10) / 10`. Measured Euclidean distance equals 78.0px (tolerance < 0.5px).
     - Ring 2 radius: computed as `Math.round(Math.cos(angle) * 148 * 10) / 10` and `Math.round(Math.sin(angle) * 148 * 10) / 10`. Measured Euclidean distance equals 148.0px (tolerance < 0.5px).
     - SVG element has `viewBox="-160 -160 320 320"` and `role="region"`.

2. **Ring 1 and Ring 2 Capacity Limits**:
   - `src/claire/static/js/app.js` (lines 5206-5207, 5246-5248):
     - `allDocEntities = docNodes(dc.id, dc)` sorts entities descending by degree. `r1 = allDocEntities.slice(0, 24)` restricts Ring 1 to 24 entities maximum.
     - Ring 2 candidate selection sorts candidates by degree descending, then takes `r2 = candidates.slice(0, 12)` to restrict Ring 2 to 12 entities maximum.
     - Tested with 30 document entities: Ring 1 contains exactly 24 nodes. The remaining 6 nodes are omitted from Ring 1.
     - Tested with 20 external candidates: Ring 2 contains exactly 12 nodes. The remaining 8 candidates are omitted from Ring 2.

3. **Unshown Entity Count Text**:
   - `src/claire/static/js/app.js` (lines 5328-5334):
     - Unshown count calculates: `Math.max(0, allDocEntities.length - 24) + Math.max(0, r2CandidatesCount - 12)`.
     - When total is greater than zero, renders `<p class="ctx-local-graph-unshown">` with localized string (`+{count} more entities not shown` in English, `외 {count}개 엔티티 미표시` in Korean).
     - Tested with 30 document entities (6 unshown) and 20 external candidates (8 unshown): text displays `14`.
     - When limits are not exceeded, the text element is not rendered.

4. **Visual Line Grammar**:
   - `src/claire/static/css/workspace.css` (lines 750-752) & `src/claire/static/js/app.js` (lines 5275-5300):
     - Lines from center to Ring 1: `<line class="ctx-lg-doc-edge">`, styled with `stroke-width: 0.8px` and `opacity: 0.6`.
     - Standard relations: `<line class="ctx-lg-edge">`, styled with `stroke-width: 1.2px` and solid stroke (no `stroke-dasharray`).
     - Provisional relations: `<line class="ctx-lg-edge provisional" stroke-dasharray="3 3">`, styled with `stroke-width: 1.2px`.
     - Multi-edge deduplication (`app.js:5288`): solid edge takes precedence over provisional edge between the same pair of entities.

5. **Pre-Graph Loading Fallback**:
   - `src/claire/static/js/app.js` (lines 5223-5273):
     - When `graphInitialized` is false or `allEdges` is null, Ring 2 candidates and edge lines are skipped.
     - The SVG renders Ring 1 nodes and document center node only.
     - Triggers `initGraph()`. When `initGraph()` finishes fetching `GET /graph`, lines 1748-1753 trigger `renderDocPanel(curDocPanelData)`. This re-renders the SVG with Ring 2 nodes and edge lines.

6. **Interactive Selection and Zero POST Mutations**:
   - `src/claire/static/js/app.js` (lines 5314, 5481-5487):
     - Clicking an entity node triggers `selectDocEntity(id)`, which calls `loadNode(id)` / `focusNode(id)`.
     - Dispatches `GET /node?id=...`. Monitored network traffic confirms zero `POST` requests.
     - Detail panel displays entity information and functional backlink button.

7. **Accessibility Tree and Keyboard Navigation**:
   - `src/claire/static/js/app.js` (lines 5314-5316):
     - Each entity node is wrapped in `<g class="ctx-lg-node" role="button" tabindex="0" aria-label="[label]">`.
     - Keyboard event listener handles `Enter` and ` ` (Space) keys.
     - Verified that pressing `Enter` selects the entity node via `GET /node`.
     - Verified that pressing `Space` selects the entity node via `GET /node`.
     - Transparent `<circle r="14" fill="none" pointer-events="all" />` provides enlarged tap target for pointer and touch interactions.

8. **Cross-View Hover Synchronization**:
   - `src/claire/static/js/app.js` (lines 5175-5202, 5314, 5366, 6292) & `src/claire/static/js/reader.js` (lines 2188-2205):
     - Hovering a local graph node adds `.has-hover` to the SVG, `.hover-active` to the node and incident edges, `.entity-hover-active` to matching rows in the Entities tab, Knowledge Map card, and left entity list.
     - Also updates reader marks highlight via `CSS.highlights.set('claire-entity-hover', ...)`.
     - Hovering a row in the Entities tab adds `.entity-hover-active` to the row and highlights the entity node in the local graph.
     - Mouse leave removes all active hover classes and clears `claire-entity-hover`.

9. **Automated Test Execution**:
   - Command: `cd e2e && npx playwright test workspace.spec.js -g "HW-4"`
   - Result: 1 passed in 10.9 seconds. Zero page errors observed.
   - Comprehensive challenge harness executed and verified all 9 target behaviors.

---

## 2. Logic Chain

1. **Concentric Placement and Radii**:
   - Observation 1 shows exact mathematical radius calculations: `r = 78` for Ring 1 and `r = 148` for Ring 2.
   - Empirical measurements confirm all node centers lie precisely on circles of radius 78 and 148 centered at (0, 0).
   - Conclusion: Concentric geometry satisfies the design requirement.

2. **Capacity Filtering and Degre Sorting**:
   - Observation 2 demonstrates that `allDocEntities.slice(0, 24)` and `candidates.slice(0, 12)` enforce hard upper bounds.
   - When fed with 30 document entities and 20 external candidates, exactly 24 and 12 nodes render.
   - Both sets are sorted descending by degree, ensuring only the most significant entities are shown.
   - Conclusion: Capacity limit handling is verified and robust.

3. **Unshown Counter Integrity**:
   - Observation 3 proves that unshown counts from both rings sum correctly into `totalUnshown`.
   - The counter appears only when `totalUnshown > 0`.
   - Localized strings in `en.json` and `ko.json` correctly display the number.
   - Conclusion: Counter logic operates correctly.

4. **Visual Grammar Conformance**:
   - Observation 4 confirms that solid lines represent factual relations (`stroke-width: 1.2px`), dashed lines represent provisional relations (`stroke-dasharray="3 3"`), and thin gray lines represent document-to-entity links (`stroke-width: 0.8px`).
   - Dual-edge precedence ensures solid lines override dashed lines if both relation types connect the same entity pair.
   - Conclusion: Visual line grammar adheres to Design Section 7.

5. **Pre-Graph Fallback and Network Invariance**:
   - Observation 5 and 6 demonstrate that initial rendering without graph data renders Ring 1 without errors, and updates when graph data arrives.
   - Selection triggers only idempotent `GET /node` requests. Zero data mutations or `POST` requests occur.
   - Conclusion: State transitions and network interactions are non-destructive and safe.

6. **Accessibility and Hover Synchronization**:
   - Observation 7 and 8 prove that keyboard navigation (`Enter` and `Space`), screen reader semantics (`role="button"`, `tabindex="0"`, `aria-label"`), and enlarged touch targets are present and functional.
   - Bidirectional hover synchronization connects local graph, Entities tab, Knowledge Map card, entity list, and reader highlights.
   - Conclusion: Accessibility and cross-view synchronization satisfy specification criteria.

---

## 3. Caveats

1. **Ring 2 Angular Collision under Low Node Count**:
   - Observation: When Ring 2 candidate count is less than or equal to 4 (`r2.length <= 4`), line 5263 uses `cand.targetAngle`. If multiple candidates connect exclusively to the same Ring 1 neighbor, both candidates compute identical `targetAngle` values.
   - Empirical test: Two candidates connected to the same hub entity received identical angles (`-1.570796...`), placing both nodes at `x = 0, y = -148`. The two nodes overlap and visually obscure one another.
   - Blast Radius: Visual overlap occurs only when external candidates count is between 1 and 4, and multiple candidates share the exact same connected Ring 1 node. It does not cause JavaScript errors or application crashes.
   - Recommended Mitigation for Future Refinement: When candidate count is less than or equal to 4, distribute angles or apply an angular offset if two candidates share the same target angle.
   - Assessment: This does not invalidate functional conformance to the HW-4 contract.

2. **Reader CSS Custom Highlight Dependency**:
   - Synchronization with reader text marks relies on `CSS.highlights`. In environments without CSS Custom Highlight support, the code safely falls back without throwing exceptions.

---

## 4. Conclusion

**Verdict: APPROVE**

Empirical verification confirms that HW-4 (Local Graph and Hover Connections) meets all functional, geometric, accessibility, and visual line grammar requirements:
- Concentric layout at radii 78 and 148 is mathematically accurate.
- Ring 1 (max 24) and Ring 2 (max 12) capacity limits and degree sorting operate as specified.
- Unshown entity count text is accurate and localized.
- Visual line grammar (thin, solid, dashed, dual precedence) conforms to Design Section 7.
- Interactive selection executes via `GET /node` with zero `POST` requests.
- Accessibility tree requirements (`role="button"`, `tabindex="0"`, `aria-label"`, enlarged tap targets, and Enter/Space activation) are fully met.
- Cross-view hover synchronization functions bidirectionally across all views.
- Official automated Playwright test `workspace.spec.js -g "HW-4"` passes cleanly in 10.9 seconds.

---

## 5. Verification Method

To independently verify this evaluation:

1. **Run Playwright HW-4 End-to-End Test**:
   ```bash
   cd /home/fow/Projects/claire-bible/e2e && npx playwright test workspace.spec.js -g "HW-4"
   ```
   Expected: 1 passed in ~11s.

2. **Run Pytest Unit Suite**:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py -k "test_document_detail_includes_entity_aliases_and_preserves_detail"
   ```
   Expected: 1 passed.

3. **Verify Git Working Tree Status**:
   ```bash
   git status --short
   ```
   Expected: Implementation files remain clean and unmodified by this verifier.
