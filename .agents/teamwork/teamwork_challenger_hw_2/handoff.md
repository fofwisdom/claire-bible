# Handoff Report — HW-5 Adversarial Verification

**State Folder**: `/home/fow/Projects/claire-bible/.agents/teamwork/teamwork_challenger_hw_2`
**Date**: 2026-11-14T13:48:00Z
**Author**: `teamwork_challenger_hw_2` (HW-5 Adversarial Verifier)
**Recipient**: Parent Orchestrator (`21ab5432-86d2-4f40-befa-f9c2b7b06ba1`)
**Verdict**: APPROVE

---

## 1. Observation

### 1.1 Direct File Observations
- `src/claire/store/queries.py` (lines 236-246):
  ```python
  ents = dbm.document_entities(conn, document_id, theme_id=theme_id)
  nodes = [
      {
          "id": e.id,
          "label": e.name,
          "group": e.type,
          "observations": e.observations or [],
          "aliases": e.aliases or [],
      }
      for e in ents
  ]
  ```
  `aliases` is added directly from `e.aliases` with `[]` fallback. `detail`, `detail_format`, and `detail_html` properties are extracted from the document row and helper functions unchanged at lines 328-331:
  ```python
  "summary": dbm.latest_extraction_summary(conn, document_id, theme_id=theme_id) or "",
  "detail": dbm.get_document_detail(conn, document_id, theme_id=theme_id) or "",
  "detail_format": dbm.get_document_detail_format(conn, document_id, theme_id=theme_id),
  "detail_html": dbm.get_document_detail_html(conn, document_id, theme_id=theme_id) or "",
  ```

- `src/claire/static/js/reader.js`:
  - Lines 1859-1864: `isEntityMarksExcluded()` inspects location search (`s=`), pathname (`/p`), and document body class (`shared-page`).
  - Lines 1866-1872: `isAsciiAlphanumericChar(c)` strictly checks ASCII ranges `0-9`, `A-Z`, and `a-z`. Non-ASCII characters (including Korean hangul syllables) return `false`.
  - Lines 1874-1924: `collectEntityCandidates(dc)` drops candidates with length < 2, sets `isShortAscii` for ASCII length 2-3, calculates collision map `keyEntities`, discards candidates with multiple entity owners, and sorts candidates descending by length.
  - Lines 1933-1939: `findMarkableTextNodes(root)` rejects nodes inside `h1-h6`, `code`, `pre`, `a`, `.katex`, `.katex-display`, `script`, `style`, and accepts only descendants of `p`, `li`, `td`, `th`.
  - Lines 2007-2023: Performance limits guard checking `bodyText.length > 400000` and `totalCandidateNames > 300`.
  - Lines 2085-2087: Marks applied via `CSS.highlights.set('claire-entity', new Highlight(...ranges))` with zero DOM tree insertion.
  - Lines 2165-2185: Pointer click uses caret offset to select entity via `selectDocEntity(id)`, while active text selection (`!sel.isCollapsed && sel.toString().trim().length > 0`) bypasses selection to preserve copy/paste functionality.

- `src/claire/templates/index.html` (line 459):
  `<div id="rlegend" class="rlegend" aria-live="polite"></div>` is placed under `.rbody-wrap` outside `#rbody`.

### 1.2 Test Execution Commands and Verbatim Outputs
1. Automated unit test execution:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Output:
   ```
   ============================== 53 passed in 2.64s ==============================
   ```

2. Official Playwright E2E execution:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-5"
   ```
   Output:
   ```
   Running 1 test using 1 worker
   [1/1] HW-5: reader text entity marks verify zero DOM mutation (T1), selection without mutation (T2), share page exclusion (T3), and rule validation (T4)
     1 passed (9.5s)
   ```

3. Empirical Python Stress Test for Server Aliases and Invariance:
   Executed test harness covering:
   - Document with zero entities: `nodes` is `[]`, `detail` and `detail_html` invariant.
   - Entities with `None` aliases, empty list `[]`, populated list, and Korean aliases.
   - Legacy DB row with NULL `aliases` in SQLite table: correctly falls back to `[]`.
   - FastAPI endpoint `GET /document?id=d-api` under anonymous readonly mode: returns 200 with `aliases: ["Alias1", "Alias2"]`, `detail_format: "adoc"`, and unchanged `detail_html`.
   Output:
   ```
   SERVER_ALIASES_AND_INVARIANCE_VERIFIED: All tests passed successfully!
   ```

4. Empirical Adversarial Challenge Suite (`e2e/hw5_challenge.spec.js`):
   Executed 5 comprehensive stress tests covering:
   - **T1: Strict DOM Invariance, Idempotency, and Legend Placement**:
     - Captured `innerHTML`, `textContent`, `childElementCount`, and node structure.
     - Executed 5 consecutive calls to `applyEntityMarks(doc)`: `innerHTML` and `textContent` remained 100% identical.
     - Toggled `localStorage.setItem('claireEntityMarks', '0')`: `innerHTML` and `textContent` remained 100% identical.
     - Re-enabled `claireEntityMarks = '1'`: `innerHTML` and `textContent` remained 100% identical.
     - Verified `#rlegend` exists strictly outside `#rbody`.
   - **T2: Zero Mutation on Click, Strict GET /node Only, Selection Immunity**:
     - Monitored all network requests across `GET`, `POST`, `PUT`, `DELETE`, `PATCH`.
     - Clicked marked entity text: triggered `GET /node?id=ent-1` with 0 mutating requests.
     - Clicked unmarked text: 0 requests dispatched.
     - Drag-selected text (active selection): click dispatched 0 requests, ensuring selection immunity.
   - **T3: Strict Share Page Isolation and Fallback Safety**:
     - Navigated to `/p?s=23456789abcdefgh`: verified `CSS.highlights` size is 0 and body contains `shared-page`.
     - Tested `isEntityMarksExcluded` guard: simulation of `shared-page` class or `s=` query immediately cleared marks and halted processing.
   - **T4: Comprehensive Adversarial Stress Testing of Matching Engine**:
     - Boundary checks: rejected substring matches in `Pineapple`, `Apples`, `Apple123`, `99Apple`; accepted `(Apple)`, `[Apple]`, `Apple is good`.
     - Korean particles: accepted `Apple는`, `Apple에`, `Apple에서`, `Apple로`, `Apple과`, `Apple을`, `Apple이`, `Fedify에서`, `Fedify와`.
     - Short names: rejected length < 2 (`A`, `Z`).
     - 2-3 char ASCII case sensitivity: accepted `Go` and `API`; rejected `go`, `GO`, `api`, `Api`.
     - Korean 2-char: accepted `보안`.
     - Longest match first: matched `Machine Learning` and suppressed inner `Machine` and `Learning` overlaps within that span.
     - Collision suppression: candidate `Delta` and `Foobar` shared across two entities received 0 marks. Entity with self-alias `Omega` received marks.
     - Tag scope limitation: matches restricted to `p`, `li`, `td`, `th`; excluded `h1`, `code`, `pre`, `a`, `.katex`.
   - **T4 Safety Limits: Exact Boundary Stress Testing**:
     - Text length 400,000 chars: marks processed normally.
     - Text length 400,001 chars: marks halted, `#rlegend` displayed limit message.
     - Candidate count 300 names: marks processed normally.
     - Candidate count 301 names: marks halted, `#rlegend` displayed limit message.
   Output:
   ```
   5 passed (14.0s)
   ```

---

## 2. Logic Chain

1. **Server Aliases and Invariance**:
   Observation 1.1 shows `queries.py` supplies `"aliases": e.aliases or []` to the node representation list in `document_detail()`. Observation 1.3 shows empirical verification confirming `nodes[].aliases` returns the expected array across diverse entity types (including NULL database fallbacks) while `detail`, `detail_format`, and `detail_html` remain invariant.

2. **DOM Non-Destruction (Challenge T1)**:
   Observation 1.1 confirms that `reader.js` uses `CSS.highlights.set('claire-entity', new Highlight(...ranges))` and never adds elements or text nodes to `#rbody`. Observation 1.4 confirms that `textContent`, `innerHTML`, child counts, and node types remain identical before marking, after multiple repeated markings, after disabling marking, and after re-enabling marking. Observation 1.1 and 1.4 confirm `#rlegend` resides outside `#rbody`.

3. **Read-Only Selection (Challenge T2)**:
   Observation 1.1 shows `reader.js` uses pointer caret coordinates to find the hit entity in `readerActiveMarkRanges` and dispatches `selectDocEntity(hitItem.entityId)`. Observation 1.4 confirms via network request interception that clicking marked text dispatches a single `GET /node` request with zero `POST`, `PUT`, `DELETE`, or `PATCH` requests. Clicking unmarked text or clicking during active text selection generates zero requests.

4. **Share Page Exclusion (Challenge T3)**:
   Observation 1.1 and 1.4 show that `/p?s=...` does not render entity marks (`CSS.highlights` size is 0) and that `isEntityMarksExcluded()` detects `shared-page` and query parameter `s=`.

5. **Matching Grammar and Safety Boundaries (Challenge T4)**:
   Observation 1.4 confirms that boundary rules permit Korean particles while rejecting ASCII alphanumeric prefixes/suffixes, short name rules enforce case-sensitivity for 2-3 char ASCII while ignoring < 2 chars, longest-first matching prevents substring overlap, collision suppression prevents ambiguous marks when candidate strings belong to multiple entities, and exact boundaries (> 400,000 chars or > 300 candidates) halt marking safely.

---

## 3. Caveats

- **Browser CSS Custom Highlight API Compatibility**: In browser environments without `CSS.highlights` support, `applyEntityMarks()` detects absence and sets `#rlegend` to the unsupported message gracefully without DOM errors. Modern Chromium, Firefox, and Safari support this API.
- No other caveats.

---

## 4. Conclusion

The HW-5 implementation of Server Aliases and Reader Text Entity Marks satisfies all requirements and passes all empirical adversarial tests:
- `GET /document` returns entity aliases without mutating document detail fields.
- Challenge T1: Zero DOM mutation on `#rbody` is strictly maintained.
- Challenge T2: Pointer selection dispatches only `GET /node` with zero side effects.
- Challenge T3: Share page isolation is verified.
- Challenge T4: All word boundary, acronym case, longest-match overlap, collision suppression, tag scope, and safety limit rules are verified.

Verdict: **APPROVE**

---

## 5. Verification Method

To independently reproduce the verification results:

1. **Run Unit Test Suite**:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py -k "test_document_detail_includes_entity_aliases_and_preserves_detail"
   ```
   Expected: 1 passed.
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Expected: 53 passed.

2. **Run Playwright E2E Suite**:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-5"
   ```
   Expected: 1 passed.

3. **Verify Git Working Tree Cleanliness**:
   ```bash
   git status --short
   ```
   Expected: Only the 15 production files modified by the worker and agent state directories appear.
