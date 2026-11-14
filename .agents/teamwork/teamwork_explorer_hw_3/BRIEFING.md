# BRIEFING — 2026-11-14T12:50:00Z

## Mission
Investigate reader marks, server aliases, CSS Custom Highlight API, and test infrastructure for HW-5.

## 🔒 My Identity
- Archetype: teamwork_explorer
- Roles: Reader Marks & Test Investigator
- Working directory: /home/fow/Projects/claire-bible
- State folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3
- Original parent: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Milestone: HW-5 Reader Marks and Test Investigation

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- All comments and documentation must comply with ASD-STE100
- Do not hard-wrap code lines
- Write unit tests only for module interfaces, I/O operations, external library calls
- Write a minimum of one E2E test when a new feature is added

## Current Parent
- Conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Updated: 2026-11-14T12:50:00Z

## Investigation State
- **Explored paths**:
  - `src/claire/store/queries.py` (`document_detail` builds `nodes` response)
  - `src/claire/api/server.py` (`document_detail_route` for `GET /document`)
  - `src/claire/static/js/reader.js` (`renderReader`, `#rbody`, `applyMathRendering`)
  - `src/claire/static/js/app.js` (`loadNode`, `selectDoc`, `docNodes`)
  - `src/claire/templates/index.html` (`#reader`, `#rbody`, script order)
  - `src/claire/templates/share.html` (`#wrap`, share page isolation)
  - `e2e/playwright.config.js`, `e2e/seed_db.py`, `e2e/workspace.spec.js`
  - `docs/implementation/` (`API_HTTP_CONTRACT.md`, `openapi.yaml`, `IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`)
- **Key findings**:
  - `document_detail` in `src/claire/store/queries.py:237-245` maps `ents` to `nodes`. Add `"aliases": e.aliases or []` to add entity aliases without changing `detail` or `detail_html`.
  - CSS Custom Highlight API (`::highlight(claire-entity)`) marks ranges without modifying `#rbody.innerHTML` or `#rbody.textContent` (satisfies T1).
  - `#rlegend` element placed outside `#rbody` displays mark status without touching `#rbody` DOM.
  - Clicking a marked range resolves character offset via `document.caretPositionFromPoint` or `document.caretRangeFromPoint`, finds entity ID, and executes `loadNode(id)` which issues `GET /node?id=...&docs=brief` (zero POST / mutation, satisfies T2).
  - Share page (`/p?s=...`) uses standalone template without reader marks logic (satisfies T3).
  - Match engine enforces: text <= 400,000 chars, candidates <= 300, collision suppression for duplicate names across entities, short name (2-3 ASCII chars) case sensitivity, longest match first, and ASCII word boundary checks.
  - Pytest baseline: 3266 passed, 12 skipped, 6 subtests passed in 340.75s (0:05:40).
  - Playwright E2E: executes with `cd e2e && npx playwright test`.
- **Unexplored areas**: None.

## Key Decisions Made
- Verified complete pytest baseline and Playwright execution requirements.

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/BRIEFING.md — persistent working memory
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/progress.md — liveness heartbeat
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/handoff.md — handoff report
