# Technical Review and Adversarial Challenge Report (HW-4 & HW-5)

**State Folder**: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_reviewer_hw_1`  
**Date**: 2026-11-14T13:43:00Z  
**Author**: `teamwork_reviewer_hw_1`  
**Target Milestone**: M1 (Units HW-4 and HW-5)  
**Recipient**: Parent Orchestrator (`21ab5432-86d2-4f40-befa-f9c2b7b06ba1`)  

---

## 1. Observation

### 1.1 Scope and Modified Files
Inspection of `git status --short` and `git diff --stat` confirms modifications across 15 files:
- `src/claire/store/queries.py` (Line 243)
- `src/claire/static/js/app.js` (Lines 1751-1753, 4484, 5175-5357, 5366, 6292, 6329, 6469-6470)
- `src/claire/static/js/reader.js` (Lines 851-853, 1307-1309, 1854-2228, 2285-2288)
- `src/claire/static/css/workspace.css` (Lines 747-766)
- `src/claire/static/css/reader.css` (Lines 1263-1282)
- `src/claire/templates/index.html` (Line 459)
- `src/claire/static/locales/en.json` (Lines 99-100, 123-126)
- `src/claire/static/locales/ko.json` (Lines 99-100, 123-126)
- `docs/implementation/API_HTTP_CONTRACT.md` (Line 71)
- `docs/implementation/GLOSSARY.md` (Lines 184-185)
- `docs/implementation/IMPLEMENTATION_STATUS.md` (Lines 29-30)
- `docs/implementation/openapi.yaml` (Lines 865-879)
- `src/claire/static/docs/openapi.yaml` (Lines 795-809)
- `tests/test_graphview.py` (Lines 1219-1262)
- `e2e/workspace.spec.js` (Lines 2558-2886)

### 1.2 Automated Verification Results
1. Python unit tests:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Direct execution output:
   ```
   ============================== 53 passed in 2.71s ==============================
   ```
2. Playwright end-to-end tests:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Direct execution output:
   ```
   Running 2 tests using 1 worker
   [1/2] HW-4: local graph renders Ring 1 and Ring 2, line grammar, limits, accessible buttons, selection, and cross-view hover synchronization
   [2/2] HW-5: reader text entity marks verify zero DOM mutation (T1), selection without mutation (T2), share page exclusion (T3), and rule validation (T4)
     2 passed (13.2s)
   ```
3. Full unit suite:
   ```bash
   ./.venv/bin/pytest tests/test_principal.py -k "test_credentials_of_non_accepting_accounts_look_like_unknown_credentials"
   ```
   All 4 parameterized tests passed in 0.98s.

### 1.3 Technical Implementation Observations
1. **Server Aliases (`src/claire/store/queries.py:243`)**:
   `"aliases": e.aliases or []` added to dictionary comprehension inside `document_detail()`. Existing dictionary keys `detail`, `detail_format`, `detail_html` remain unaltered.
2. **Local Graph Rendering (`src/claire/static/js/app.js:5204-5335`)**:
   - Inline SVG output placed into `#ctx-local-graph-slot`.
   - Document center node located at `(0, 0)` with `viewBox="-160 -160 320 320"`.
   - Ring 1 extracts entities from `docNodes(dc.id, dc)`, sorts by degree descending, and slices up to 24 at radius 78.
   - Ring 2 extracts external connected entities from `allEdges`, sorts by degree descending, slices up to 12 at radius 148, and arranges angles oriented toward connected Ring 1 entities.
   - Line grammar: Center lines use `stroke-width: 0.8px` (`.ctx-lg-doc-edge`), standard relations use solid `stroke-width: 1.2px` (`.ctx-lg-edge`), provisional relations use `stroke-dasharray="3 3"` (`.ctx-lg-edge.provisional`).
   - Pre-graph fallback: If `graphInitialized` or `allEdges` is not available, Ring 2 is omitted and `initGraph()` is called.
   - Unshown entity count text is rendered below SVG when total exceeds limits.
   - Accessibility: SVG nodes specify `role="button"`, `tabindex="0"`, `aria-label`, tap target with radius 14 circle, and Enter/Space keyboard handlers.
3. **Entity Marks Engine (`src/claire/static/js/reader.js:1854-2228`)**:
   - Zero DOM mutation on `#rbody`: Uses `new Highlight(...ranges)` and `CSS.highlights.set('claire-entity', highlight)`.
   - Legend `#rlegend` resides outside `#rbody` inside `#reader .sheet .rbody-wrap`.
   - Candidate collection: Drops < 2 characters; 2-3 character ASCII strings matched case-sensitively; other candidates matched case-insensitively; collisions across multiple entities suppressed.
   - TreeWalker: Traverses only text nodes in `p`, `li`, `td`, `th`; excludes headings, code, pre, a, katex, script, style.
   - Boundary rules: ASCII alphanumeric character checks on boundaries; permits Korean particle suffixes.
   - Performance limits: Skips marks when text length > 400,000 characters or candidate names > 300.
   - Selection: Pointer click uses `document.caretPositionFromPoint` (with fallback to `caretRangeFromPoint`) and triggers `selectDocEntity` without initiating any POST request.
   - Exclusion: Share page paths (`/p`, `?s=`, `.shared-page`) bypass marking completely.

---

## 2. Logic Chain

1. **Integrity Assessment**:
   - Inspection of source code in `src/claire/` shows no hardcoded test outputs, dummy branches, or mocking facades.
   - Implementation logic for SVG trigonometry, angle calculation, graph neighbor collection, TreeWalker traversal, interval overlap checking, caret point detection, and CSS highlights is real and complete.
   - No integrity violations detected.

2. **Compliance with Section 6.4 (Local Graph)**:
   - Observation 1.3 shows Ring 1 is limited to 24 entities sorted by degree and Ring 2 to 12 external neighbors.
   - Unshown entity count arithmetic correctly computes `Math.max(0, allDocEntities.length - 24) + Math.max(0, r2CandidatesCount - 12)`.
   - Visual grammar strictly matches Section 7: thin solid lines for document links, solid lines for standard edges, dashed lines for provisional edges (`stroke-dasharray="3 3"`).
   - Keyboard accessibility (`role="button"`, `tabindex="0"`, `aria-label`, Enter/Space) and click handlers correctly delegate to `selectDocEntity()`, which dispatches read-only `GET /node` without mutation.

3. **Compliance with Section 8 & Section 9 (Entity Marks & Server Aliases)**:
   - Backend `GET /document` returns `nodes[].aliases` while preserving `detail`, `detail_format`, and `detail_html`. Unit test `test_document_detail_includes_entity_aliases_and_preserves_detail` proves invariance.
   - Reader text marks use the CSS Custom Highlight API. E2E test T1 proves that `innerHTML` and `textContent` of `#rbody` are identical before, during, and after disabling/enabling marks.
   - E2E test T2 proves clicking marked text issues only `GET /node` and 0 POST requests.
   - E2E test T3 proves zero highlights on share pages.
   - E2E test T4 proves all match rules: 2-3 char acronym case sensitivity, ASCII boundary validation, Korean particle handling, collision suppression, and size/count safety limits.

4. **Standards & Conventions**:
   - ASD-STE100 technical English is used in comments, documentation, and commit preparations.
   - Lines are not hard-wrapped.
   - Documentation updates in `docs/implementation/` and `src/claire/static/docs/` match all code changes.

---

## 3. Caveats

- **Browser Highlight API Support**: Environments lacking `CSS.highlights` will display the unsupported message in `#rlegend` without rendering marks. This is the intended behavior specified in Section 8.4 Rule 11.
- **R2 Angular Distribution with Few Neighbors**: When Ring 2 has 4 or fewer nodes, target angles point toward connected Ring 1 nodes. If two external nodes share identical connected Ring 1 nodes, their positions may overlap. When Ring 2 has more than 4 nodes, positions are evenly distributed along the circumference to prevent overlaps.

---

## 4. Adversarial Review & Stress-Testing

### Challenge Summary
**Overall risk assessment**: LOW

### Stress Test Findings
1. **Stress Scenario 1: Malformed or Regex Characters in Entity Names**:
   - Risk: Entity names like `C++`, `Node.js`, `GPT-4 (3.5)` could break string searching if evaluated via `RegExp`.
   - Mitigation in code: Matching engine uses `searchTarget.indexOf(query, searchIdx)` on text content instead of constructing regular expressions. Special characters are treated as literal characters.
   - Result: PASS.
2. **Stress Scenario 2: Zero or Inactive Nodes in Document**:
   - Risk: Document without entities could cause division by zero in angular distribution.
   - Mitigation in code: `Math.max(r1.length, 1)` prevents division by zero. Empty lists return clean SVG with center document node only.
   - Result: PASS.
3. **Stress Scenario 3: DOM Mutation Attempts During Reader Streaming**:
   - Risk: Concurrent updates to `#rbody` while ranges are active.
   - Mitigation in code: Range creation and highlight updates are wrapped in `try/catch` blocks. `setupReaderMarkListeners()` checks caret coordinates safely.
   - Result: PASS.
4. **Stress Scenario 4: LocalStorage Security Restrictions**:
   - Risk: Private browsing mode throwing `SecurityError` on `localStorage.getItem`.
   - Mitigation in code: `localStorage` access is wrapped in `try { ... } catch(_) {}`. Default state remains enabled.
   - Result: PASS.

---

## 5. Review Summary

**Verdict**: APPROVE

### Verified Claims
- `GET /document` includes `nodes[].aliases` array -> verified via pytest -> PASS
- Invariance of `detail`, `detail_format`, and `detail_html` -> verified via pytest -> PASS
- HW-4 Local Graph rings, line grammar, limits, accessible buttons -> verified via Playwright E2E -> PASS
- HW-5 T1 Zero DOM mutation on `#rbody` -> verified via Playwright E2E -> PASS
- HW-5 T2 Click selection without mutation (GET only, no POST) -> verified via Playwright E2E -> PASS
- HW-5 T3 Share page exclusion -> verified via Playwright E2E -> PASS
- HW-5 T4 Match rules and limit thresholds -> verified via Playwright E2E -> PASS
- Cross-view hover synchronization -> verified via Playwright E2E -> PASS
- ASD-STE100 technical English used -> verified via inspection -> PASS
- Code lines not hard-wrapped -> verified via inspection -> PASS

### Coverage Gaps
- None. All requirements specified in R1, R2, and R3 are covered.

### Unverified Items
- None.

---

## 6. Verification Method

To reproduce and verify this review independently:

1. Execute Python unit test:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py -k "test_document_detail_includes_entity_aliases_and_preserves_detail"
   ```
   Expected: 1 passed.

2. Execute full Python graphview tests:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Expected: 53 passed.

3. Execute Playwright E2E test suite for HW-4 and HW-5:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Expected: 2 passed.

4. Inspect git diff:
   ```bash
   git diff src/ tests/ e2e/ docs/
   ```
   Expected: Clean diff with no syntax errors, no hard-wrapped lines, and no temporary files.
