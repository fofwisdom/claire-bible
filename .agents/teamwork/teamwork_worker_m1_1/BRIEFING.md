# BRIEFING — 2026-11-14T13:28:00Z

## Mission
Implement Milestone M1: HW-5 Server Aliases, HW-4 Local Graph in Links Tab, and HW-5 Reader Text Entity Marks with full localization, documentation, and tests.

## 🔒 My Identity
- Archetype: teamwork_worker
- Roles: implementer, qa, specialist
- Working directory: /home/fow/Projects/claire-bible
- State folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1
- Original parent: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Milestone: Milestone M1

## 🔒 Key Constraints
- Strict zero DOM mutation on #rbody (CSS Custom Highlight API).
- Server aliases: nodes[].aliases in queries.document_detail(), detail/detail_format/detail_html strictly unchanged.
- Local Graph: Ring 1 top 24 radius 78, Ring 2 top 12 radius 148, line grammar, a11y, cross-view hover synchronization.
- Reader text marks: candidate generation (name+aliases, drop <2 chars, 2-3 ascii char case-sensitive, others case-insensitive, word boundary / Korean particle handling, longest first, suppress collisions, max 400k chars / 300 names limit, click selects entity via GET /node without POST, no marks on share page).
- Exclusive file write ownership adhered to.
- ASD-STE100 technical English for comments and docs. Do not hard-wrap code lines.
- No dummy/facade implementations, genuine logic.

## Current Parent
- Conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Updated: not yet

## Task Summary
- **What to build**: HW-5 Server Aliases in queries.py, HW-4 Local Graph in app.js/workspace.css, HW-5 Reader Text Entity Marks in reader.js/reader.css/index.html, localization en/ko.json, docs updates, tests in test_graphview.py and e2e/workspace.spec.js.
- **Success criteria**: All tests pass (pytest and Playwright), zero DOM mutation on #rbody, cross-view hover synch works, correct line grammar and ring layouts.
- **Interface contracts**: docs/implementation/API_HTTP_CONTRACT.md, docs/implementation/openapi.yaml
- **Code layout**: src/claire/*, tests/*, e2e/*, docs/implementation/*

## Key Decisions Made
- Server aliases: In `src/claire/store/queries.py` `document_detail()`, added `"aliases": e.aliases or []` to each node while leaving `detail`, `detail_format`, `detail_html` strictly preserved.
- Local graph geometry: Ring 1 placed 24 entities at radius 78; Ring 2 placed up to 12 candidates at radius 148 sorted by angle towards connected Ring 1 nodes. Center at (0, 0).
- Local graph interaction & a11y: Each SVG node is given `role="button"`, `tabindex="0"`, `aria-label="[label]"`, Enter/Space keydown, click calling `selectDocEntity()`, transparent hit-target circle of radius 14 with `pointer-events="all"` to ensure reliable pointer interactions.
- Line grammar: Center to Ring 1 uses 0.8px line; standard edges use 1.2px solid lines; provisional edges use 1.2px dashed lines (`stroke-dasharray="3 3"`).
- Cross-view hover synchronization: `setEntityHoverHighlight(id)` and `clearEntityHoverHighlight()` propagate hover highlight across local graph SVG, Entities tab row (`.ctx-entity-row`), Knowledge Map (`.home-map-row`), left entity list (`.entitem`), and reader CSS custom highlight (`claire-entity-hover`).
- Reader entity marks (CSS Custom Highlight API): Strict zero DOM mutation on `#rbody`. Matching rules implemented with word boundaries and Korean particle check, 2-3 char ASCII case-sensitive matching, longest-first greedy allocation, collision suppression, and limit bailouts (>400k text chars or >300 names). `#rlegend` positioned outside `#rbody`. Excluded on share page.

## Artifact Index
- `.agents/teamwork/teamwork_worker_m1_1/handoff.md` — 5-component completion handoff report

## Change Tracker
- **Files modified**:
  - `src/claire/store/queries.py`: Added `aliases` to `document_detail()` node items
  - `src/claire/static/js/app.js`: Local graph SVG rendering, line grammar, rings, hover synchronization, accessible SVG nodes
  - `src/claire/static/js/reader.js`: Reader text marks via CSS Custom Highlight API, collision suppression, zero DOM mutation, hover sync
  - `src/claire/static/css/workspace.css`: Styling for local graph SVG, edges, provisional dashes, cross-view hover styles
  - `src/claire/static/css/reader.css`: Styling for `::highlight(claire-entity)`, `::highlight(claire-entity-hover)`, and `.rlegend`
  - `src/claire/templates/index.html`: Added `#rlegend` container outside `#rbody`
  - `src/claire/static/locales/en.json`: English localization keys for local graph and reader marks
  - `src/claire/static/locales/ko.json`: Korean localization keys for local graph and reader marks
  - `docs/implementation/IMPLEMENTATION_STATUS.md`: Updated status table for HW-4 and HW-5
  - `docs/implementation/GLOSSARY.md`: Added entries for Local Graph and Entity Mark
  - `docs/implementation/API_HTTP_CONTRACT.md`: Documented `nodes[].aliases` in `GET /document`
  - `docs/implementation/openapi.yaml`: Added `aliases` array schema to `GET /document`
  - `src/claire/static/docs/openapi.yaml`: Static OpenAPI schema synchronization
  - `tests/test_graphview.py`: Added unit test `test_document_detail_includes_entity_aliases_and_preserves_detail`
  - `e2e/workspace.spec.js`: Added E2E tests for HW-4 and HW-5 (T1, T2, T3, T4)
- **Build status**: PASS (all 53 pytest tests pass, Playwright HW-4 and HW-5 pass in 11.8s)
- **Pending issues**: None

## Quality Status
- **Build/test result**: pytest 53 passed in 2.72s; Playwright HW-4 & HW-5 2 passed in 11.8s
- **Lint status**: Clean
- **Tests added/modified**: 1 unit test in `test_graphview.py`, 2 comprehensive E2E tests in `workspace.spec.js`

## Loaded Skills
- None
