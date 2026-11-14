# Orchestration Plan: HW-4 & HW-5

## Plan Summary
This plan guides the execution of Milestone M1 (Units HW-4 and HW-5 from `HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`) into a single combined production git commit.

## Execution Sequence for Milestone M1
1. **Worker Dispatch**:
   - Worker implements HW-5 server aliases in `src/claire/store/queries.py`.
   - Worker implements HW-4 local graph inline SVG, line grammar, degree sorting, unshown count text, accessibility tree buttons, and cross-view hover synchronization in `src/claire/static/js/app.js` and `workspace.css`.
   - Worker implements HW-5 reader text CSS Custom Highlight API entity marks, candidate filtering, collision suppression, short name handling, text limits, zero DOM mutation, click selection without POST, and share page exclusion in `src/claire/static/js/reader.js`, `reader.css`, and `index.html`.
   - Worker adds localization strings to `src/claire/static/locales/en.json` and `ko.json`.
   - Worker writes unit test in `tests/test_graphview.py` and automated Playwright E2E tests in `e2e/workspace.spec.js` for HW-4 and HW-5 (T1-T4).
   - Worker updates implementation docs: `IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, and `openapi.yaml`.
   - Worker runs `pytest tests/test_graphview.py` and `npx playwright test e2e/workspace.spec.js` (or `cd e2e && npx playwright test workspace.spec.js`).
2. **Reviewers Dispatch (2 parallel reviewers)**:
   - Reviewer 1: Technical correctness, SVG visual grammar, CSS Custom Highlight API compliance, zero DOM mutation verification, interface contracts.
   - Reviewer 2: Repository guidelines, ASD-STE100 compliance, no hard-wrapped lines, English commit message readiness, test coverage rules.
3. **Challengers Dispatch (2 parallel challengers)**:
   - Challenger 1: Empirical verification of HW-4 (Rings 1 and 2, limits, pre-graph fallback, click selection, hover links, a11y tree buttons).
   - Challenger 2: Empirical verification of HW-5 (T1 DOM textContent/innerHTML invariance, T2 zero POST on click, T3 share page exclusion, T4 match cases).
4. **Forensic Auditor Dispatch**:
   - Teamwork Auditor independently audits the code for integrity, hardcoded test values, facade implementations, or circumvented logic.
5. **Gate Check**:
   - Strict AND evaluation of all criteria: tests pass, 2 Reviewer APPROVE, 2 Challenger APPROVE, Auditor CLEAN.
6. **Git Commit**:
   - If Gate PASS, Worker executes the single combined git commit with English commit message adhering to repository standards.
