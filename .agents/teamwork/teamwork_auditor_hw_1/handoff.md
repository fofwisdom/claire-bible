# Forensic Audit Report and Handoff

**Work Product**: Milestone M1 (Units HW-4 and HW-5 implementation in Git working tree)
**Profile**: General Project (Development Mode per ORIGINAL_REQUEST.md)
**Verdict**: CLEAN

---

## 1. Observation

### 1.1 Git Status and Modified Files
The following 15 files contain changes for Milestone M1:
1. `src/claire/store/queries.py`
2. `src/claire/static/js/app.js`
3. `src/claire/static/js/reader.js`
4. `src/claire/static/css/workspace.css`
5. `src/claire/static/css/reader.css`
6. `src/claire/templates/index.html`
7. `src/claire/static/locales/en.json`
8. `src/claire/static/locales/ko.json`
9. `docs/implementation/IMPLEMENTATION_STATUS.md`
10. `docs/implementation/GLOSSARY.md`
11. `docs/implementation/API_HTTP_CONTRACT.md`
12. `docs/implementation/openapi.yaml`
13. `src/claire/static/docs/openapi.yaml`
14. `tests/test_graphview.py`
15. `e2e/workspace.spec.js`

### 1.2 Inspection for Anti-Cheat and Prohibited Patterns
1. **Hardcoded Test Outputs**:
   - Executed pattern search across `src/` for test runner tokens, mock identifiers (`mock-ent`, `doc-mock-rings`, `doc-t4`, `playwright`, `__playwright`).
   - Result: 0 matches found in `src/`. No test-specific conditional branches exist in production code.
2. **Facade Implementations**:
   - `src/claire/store/queries.py`: Line 243 extracts `aliases` dynamically from Entity model objects (`"aliases": e.aliases or []`).
   - `src/claire/static/js/app.js`: `renderLocalGraphHtml(dc)` implements genuine geometric SVG calculations (angles, radii 78 and 148, degree sorting, connection lines, accessible buttons).
   - `src/claire/static/js/reader.js`: `applyEntityMarks(dc)` implements genuine candidate extraction, ASCII boundary verification, case matching, length limits, caret hit-testing, and CSS Custom Highlight API management.
   - Result: No facade implementations or constant dummy returns exist.
3. **Pre-populated Artifacts**:
   - Searched for pre-populated result files in project directories. No test result artifacts exist in the Git working tree.
4. **DOM Tree Invariance**:
   - Verified that `#rbody` DOM structure, `textContent`, and `innerHTML` remain unchanged when marks are applied or removed. `#rlegend` element is located outside `#rbody` inside `.rbody-wrap`.

### 1.3 Independent Test Execution Results
1. **Unit Test Suite**:
   Command:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py -k "test_document_detail_includes_entity_aliases_and_preserves_detail"
   ```
   Output:
   ```
   tests/test_graphview.py::test_document_detail_includes_entity_aliases_and_preserves_detail PASSED [100%]
   1 passed, 52 deselected in 2.32s
   ```
   Full `tests/test_graphview.py` suite:
   ```
   ============================== 53 passed in 2.68s ==============================
   ```
2. **Playwright E2E Suite**:
   Command:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Output:
   ```
   Running 2 tests using 1 worker
   [1/2] …mits, accessible buttons, selection, and cross-view hover synchronization
   [2/2] …ithout mutation (T2), share page exclusion (T3), and rule validation (T4)
     2 passed (11.4s)
   ```
3. **Full Regression Test Suite**:
   Command:
   ```bash
   ./.venv/bin/pytest -q
   ```
   Output:
   ```
   3267 passed, 12 skipped, 7 warnings, 6 subtests passed in 377.20s (0:06:17)
   ```
4. **Code Syntax and Compilation**:
   - Python: `python -m py_compile src/claire/store/queries.py tests/test_graphview.py` exited with code 0.
   - JavaScript: `node -c src/claire/static/js/app.js src/claire/static/js/reader.js e2e/workspace.spec.js` exited with code 0.

---

## 2. Logic Chain

1. **Anti-Cheat Verification**:
   The auditor searched for suspicious string literals, test identifiers, and conditional shortcuts in `src/`. No such patterns exist. Therefore, production logic does not differentiate between test runs and real operation.

2. **Genuine Implementation Verification**:
   Inspection of `queries.py`, `app.js`, and `reader.js` proves that the features use dynamic calculations:
   - `renderLocalGraphHtml` calculates node coordinates with trigonometry (`cos`/`sin`), bounds node counts to 24 (Ring 1) and 12 (Ring 2), and generates SVG markup with keyboard navigation handlers and accessibility attributes.
   - `applyEntityMarks` uses `TreeWalker` to discover text nodes, checks word boundaries and Korean particle boundaries, enforces candidate collision suppression, and registers highlights through `CSS.highlights`.
   - Therefore, the features are genuine and production-grade.

3. **Requirement Compliance**:
   - HW-4: Local graph renders Ring 1 and Ring 2, line grammar matches specification, selection invokes `GET /node`, hover synchronizes across views, and accessibility buttons exist in the DOM tree.
   - HW-5: Server returns aliases in `GET /document` while preserving `detail` and `detail_html`. Zero DOM mutation on `#rbody` is verified. Selection does not trigger POST requests. Share page excludes highlights. Matching rules satisfy all test cases.
   - User Guidelines: ASD-STE100 technical English is used. Lines are not hard-wrapped. Unit tests target interfaces and I/O. E2E tests verify complete feature workflows.

4. **Independent Test Execution**:
   All tests were executed directly by the auditor in a clean subshell. All 53 unit tests in `test_graphview.py`, both Playwright E2E tests, and the entire 3,267-test suite passed with 0 failures.

---

## 3. Caveats

- **Browser CSS Custom Highlight API Support**:
  CSS Custom Highlight API is available in modern Chromium, Safari 17.2+, and Firefox 111+. The implementation includes defensive feature checks: when `CSS.highlights` is undefined, the application updates `#rlegend` gracefully and does not throw uncaught errors.
- **Scope Limit**:
  This audit inspected changes for Milestone M1 (HW-4 and HW-5). No unrelated files or directories were modified.

---

## 4. Conclusion

**Verdict: CLEAN**

No integrity violations, facades, hardcoded test shortcuts, or fabricated artifacts were detected. The implementation satisfies all acceptance criteria of `ORIGINAL_REQUEST.md` and passes all empirical tests.

---

## 5. Verification Method

To reproduce the audit verification independently:

1. **Verify Git Short Status**:
   ```bash
   git status --short
   ```
2. **Verify Absence of Test Runner Tokens in Production Code**:
   ```bash
   grep -rnE "(mock-ent|doc-mock-rings|doc-t4|playwright|__playwright|test-runner)" src/
   ```
   Expected: Exit code 1 (no matches).
3. **Run Unit Tests**:
   ```bash
   ./.venv/bin/pytest tests/test_graphview.py
   ```
   Expected: 53 passed.
4. **Run Playwright E2E Tests**:
   ```bash
   cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
   ```
   Expected: 2 passed.

---

## 6. Raw Evidence Output

### Phase Results
- **Check 1: Hardcoded Output Detection**: PASS — No hardcoded test responses or test runner conditionals found in `src/`.
- **Check 2: Facade Implementation Detection**: PASS — Genuine geometric layout in `app.js`, genuine highlight range calculation in `reader.js`, genuine database query mapping in `queries.py`.
- **Check 3: Pre-populated Artifact Detection**: PASS — No pre-populated test output logs or result files exist in git tree.
- **Check 4: Build and Test Execution**: PASS — 53 unit tests passed, 2 Playwright E2E tests passed, full suite (3267 passed) passed.
- **Check 5: DOM Invariance (HW-5 T1)**: PASS — `#rbody` `textContent` and `innerHTML` are strictly unchanged.
- **Check 6: Mutation Absence (HW-5 T2)**: PASS — Selection sends only `GET /node`, no POST requests.
- **Check 7: Share Page Exclusion (HW-5 T3)**: PASS — Zero highlights on share route `/p` or `?s=`.
- **Check 8: Matching Engine Rules (HW-5 T4)**: PASS — ASCII boundaries, Korean particles, acronym sensitivity, collision suppression, and limits (>400k chars, >300 names) verified.
