## 2026-11-14T13:30:18Z

You are a teamwork_reviewer subagent.
Your role: Technical Reviewer (Code & Contracts).
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_reviewer_hw_1

Reference documents:
- Authoritative request: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
- Scope document: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md
- Orchestration plan: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/plan.md
- Worker handoff report: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/handoff.md

Review tasks:
1. Examine git diff and all modified files:
   - src/claire/store/queries.py
   - src/claire/static/js/app.js
   - src/claire/static/js/reader.js
   - src/claire/static/css/workspace.css
   - src/claire/static/css/reader.css
   - src/claire/templates/index.html
   - src/claire/static/locales/en.json, ko.json
   - docs/implementation/*
   - tests/test_graphview.py
   - e2e/workspace.spec.js
2. Evaluate technical correctness against docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md (Sections 6.4, 7, 8, 9, 12):
   - HW-4: Inline SVG in #ctx-local-graph-slot, document center node, Ring 1 (max 24 by degree), Ring 2 (max 12 from other docs), visual line grammar (0.8px thin center lines, solid regular lines, dashed provisional lines), pre-graph load fallback (Ring 1 only), unshown entity count, interactive selection (GET /node), a11y button semantics, cross-view hover synchronization.
   - HW-5: nodes[].aliases in GET /document, strict invariance of detail and detail_html, CSS Custom Highlight API on #rbody, zero DOM mutation, candidate matching rules (2-char drop, 2-3 char ASCII case-sensitive, case-insensitive for others, ASCII boundaries, Korean particles, longest-first greedy, collision suppression, >400k char or >300 names limits), click selection without POST, share page exclusion.
3. Execute builds and tests to verify:
   - pytest tests/test_graphview.py
   - cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
4. Write your full review report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_reviewer_hw_1/handoff.md
   Include explicit verdict: APPROVE or REQUEST_CHANGES.
5. Send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1) with your verdict.
Use ASD-STE100 technical English. Do not hard-wrap code lines.
