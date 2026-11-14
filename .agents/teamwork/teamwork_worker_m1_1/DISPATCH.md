## 2026-11-14T12:50:49Z
You are a teamwork_worker subagent.
Your role: Production Feature Worker (Milestone M1).
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1

Mandatory reference documents:
- Authoritative request: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
- Scope document: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md
- Orchestration plan: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/plan.md
- Survey synthesis: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SURVEY_SYNTHESIS.md
- Spec Miner handoff: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_spec_miner_hw_1/handoff.md
- Local Graph Explorer handoff: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2/handoff.md
- Reader Marks Explorer handoff: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/handoff.md

Exclusive file write ownership:
- src/claire/store/queries.py
- src/claire/static/js/app.js
- src/claire/static/js/reader.js
- src/claire/static/css/workspace.css
- src/claire/static/css/reader.css
- src/claire/templates/index.html
- src/claire/static/locales/en.json
- src/claire/static/locales/ko.json
- docs/implementation/IMPLEMENTATION_STATUS.md
- docs/implementation/GLOSSARY.md
- docs/implementation/API_HTTP_CONTRACT.md
- docs/implementation/openapi.yaml
- src/claire/static/docs/openapi.yaml
- tests/test_graphview.py
- e2e/workspace.spec.js

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Implementation requirements:
1. HW-5 Server Aliases:
   - In src/claire/store/queries.py: In document_detail(), add "aliases": e.aliases or [] to each item in nodes. Ensure detail, detail_format, and detail_html remain strictly unchanged.
2. HW-4 Local Graph in Links Tab:
   - In src/claire/static/js/app.js and src/claire/static/css/workspace.css:
     - Render inline SVG in #ctx-local-graph-slot inside renderDocLinksTab(dc).
     - Document center node at (0, 0).
     - Ring 1: Top 24 entities from dc.nodes sorted descending by degree, radius 78.
     - Ring 2: Top 12 entities connected via allEdges from other documents, radius 148.
     - Pre-graph loading state: Display Ring 1 only before GET /graph loads; automatically re-render when graph data arrives.
     - Visual line grammar: Thin gray lines (0.8px) from center to Ring 1; solid lines (1.2px) for non-provisional relations; dashed lines (1.2px, stroke-dasharray="3 3") for provisional relations (dashes === true). Line color follows graph edge style.
     - Accessibility: Each entity node in SVG has role="button", tabindex="0", aria-label="[entity name]", and Enter/Space keyboard handlers.
     - Interactive selection: Clicking an entity node calls selectDocEntity(id) -> GET /node without any POST request or mutation.
     - Cross-view hover synchronization: Hovering an entity highlights the same entity across the local graph, Entities tab, Knowledge Map card, left entity list, and reader text marks.
     - Unshown entity count text below SVG if entities exceed limits (>24 Ring 1 or >12 Ring 2).
3. HW-5 Reader Text Entity Marks:
   - In src/claire/static/js/reader.js, src/claire/static/css/reader.css, and src/claire/templates/index.html:
     - Use CSS Custom Highlight API (CSS.highlights.set('claire-entity', ...), CSS.highlights.set('claire-entity-hover', ...)).
     - Strict zero DOM mutation guarantee: textContent and innerHTML of #rbody must remain strictly identical whether marks are on or off (localStorage switch claireEntityMarks).
     - Place legend #rlegend outside #rbody (in #reader .sheet under .rbody-wrap) to preserve #rbody DOM tree.
     - Match rules: Candidate strings from dc.nodes (name and aliases). Names < 2 chars dropped. 2-3 ASCII chars match case-sensitively; all other names match case-insensitively via toLowerCase(). ASCII boundary checks (start/end must be preceded/followed by non-alphanumeric, Korean particles allowed). Longest candidate first, drop overlapping matches. Collision suppression (no mark if candidate belongs to multiple entities). Traversal scope: text nodes in p, li, td, th in summary and detail; exclude headings, code, pre, links, and math (.katex).
     - Limits: If text > 400,000 chars or candidates > 300 names, skip marking and update legend text.
     - Pointer interaction: Clicking marked text uses caret position to select entity via GET /node without any POST request.
     - Share page: Entity marks must not be applied on share page.
4. Locales & Documentation:
   - Add required localization strings in src/claire/static/locales/en.json and ko.json.
   - Update docs/implementation/IMPLEMENTATION_STATUS.md Section 1 status table.
   - Update docs/implementation/GLOSSARY.md (Local graph and entity mark entries).
   - Update docs/implementation/API_HTTP_CONTRACT.md (GET /document aliases field).
   - Update docs/implementation/openapi.yaml and src/claire/static/docs/openapi.yaml.
5. Verification & Tests:
   - Add unit test in tests/test_graphview.py verifying nodes[].aliases and detail/detail_html preservation.
   - Add automated Playwright E2E tests in e2e/workspace.spec.js verifying:
     - HW-4: Local graph rings (Ring 1 max 24, Ring 2 max 12), line grammar, unshown count text, a11y tree buttons, entity selection (GET /node), cross-view hover synchronization.
     - HW-5: T1 (#rbody textContent and innerHTML strictly identical with marks on and off), T2 (click mark triggers GET /node with zero POST), T3 (no marks on share page), T4 (match cases: boundaries, 2-3 char acronyms, overlaps, collisions, limits).
   - Run tests:
     - pytest tests/test_graphview.py
     - cd e2e && npx playwright test workspace.spec.js
     Verify all tests pass cleanly.
6. Adhere to repository rules:
   - ASD-STE100 technical English for comments and docs.
   - Do not hard-wrap code lines.
7. Write your full completion report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/handoff.md
   Include exact file diffs, test commands, and passing output.
   When complete, send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1).
