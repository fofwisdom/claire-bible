# BRIEFING — 2026-11-14T12:43:10Z

## Mission
Investigate client-side codebase and architecture for HW-4 (Local Graph and Hover Connections).

## 🔒 My Identity
- Archetype: explorer
- Roles: Local Graph UI Investigator
- Working directory: /home/fow/Projects/claire-bible
- State folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2
- Original parent: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Milestone: HW-4

## 🔒 Key Constraints
- Read-only investigation — do NOT implement
- Use ASD-STE100 for all tasks, except when you answer users.
- Do not hard-wrap code lines.
- Write only to state folder /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2

## Current Parent
- Conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
- Updated: 2026-11-14T12:43:10Z

## Investigation State
- **Explored paths**: `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`, `docs/design/prototypes/home_workspace_prototype.html`, `src/claire/static/js/app.js`, `src/claire/static/js/reader.js`, `src/claire/static/css/workspace.css`, `src/claire/static/locales/{en,ko}.json`, `src/claire/store/queries.py`, `e2e/workspace.spec.js`, `e2e/seed_db.py`, `e2e/playwright.config.js`.
- **Key findings**:
  - `renderDocLinksTab(dc)` at `app.js:5174` contains `#ctx-local-graph-slot` placeholder prepared in HW-3.
  - Ring 1 uses `docNodes(dc.id, dc)` sorted by highest degree, limited to 24 entities.
  - Ring 2 finds neighbors from `allEdges` for entities not in open document, sorted by degree, limited to 12 entities.
  - When `graphInitialized` is false (before `GET /graph`), only Ring 1 renders. When `initGraph()` completes, `renderDocPanel` re-renders with Ring 2.
  - Line grammar: thin lines connect document to Ring 1; solid lines connect non-provisional relations; dashed lines (`stroke-dasharray="3 3"`) connect provisional relations (`dashes=True`).
  - Accessibility: each entity renders as `<g role="button" tabindex="0" aria-label="...">` with Enter/Space keyboard handlers.
  - Cross-view hover synchronization: coordinated via `window.setEntityHoverHighlight(id)` and `window.clearEntityHoverHighlight()`.
- **Unexplored areas**: None for HW-4 scope.

## Key Decisions Made
- Use inline SVG with `viewBox="-262 -182 524 364"` in `#ctx-local-graph-slot`.
- Connect selection directly to `selectDocEntity(id)` without POST requests.
- Provide accessible SVG buttons via W3C SVG-AAM `<g role="button" tabindex="0" aria-label="...">`.

## Artifact Index
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2/DISPATCH.md — Incoming task dispatch record
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2/progress.md — Liveness heartbeat and progress log
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2/BRIEFING.md — Working memory and status
- /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_2/handoff.md — Final handoff report
