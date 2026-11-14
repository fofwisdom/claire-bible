This is a single self-contained fix; keep it small and focused. Implement unit HW-6 (Graph view insight lists in context panel without selection, and entity detail navigation from each view) from `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` as a production feature for the Claire Bible web application.

Working directory: /home/fow/Projects/claire-bible
Integrity mode: development

Reference material:
- Design document: `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` (Sections 4.5, 5.3, 12, 12.1)
- Completed commits: HW-1 (`212d50c0`), HW-2 (`5d372178`), HW-3 (`1147c28b`), HW-4 + HW-5 (`666ceeed`)

## Requirements

### R1. Graph View Insight Lists in Context Panel
In the graph view (`data-center-view="graph"`), when no entity or node is selected, render the insight lists in the context panel (`#detailpane`):
- Display the same three lists as the knowledge map card (Section 5.3):
  1. Hubs (sorted by node degree descending).
  2. Bridges (sorted by sources count descending, tie-broken by degree).
  3. Added in the last 7 days (sorted by `created_at` descending, hidden if no nodes are in the period).
- Clicking an entity in these insight lists selects that entity and opens its entity detail in the context panel without navigating away from the graph view.

### R2. Entity Detail from Each View and Back Link
Support inspecting entity details in the context panel from any view (home view, reader view, graph view):
- Clicking an entity in a list, card, local graph, full graph, or reader mark opens the entity detail in the context panel without changing the current center view.
- When an entity detail is open while a document is open in the reader, provide a back link/action in the context panel to return directly to the document context.
- When an entity is selected, pressing Escape deselects the entity first before closing the panel.

### R3. Repository Standards, E2E Testing, and Non-Goals
Adhere strictly to project conventions:
- Use ASD-STE100 for all code comments, documentation, and test descriptions.
- Write Git commit messages in English only.
- Do not hard-wrap code lines.
- Write at least one Playwright E2E test in `e2e/workspace.spec.js` covering entity detail opening and navigation across views.
- Non-goal: Screenshot updates in `docs/screenshots/` and root `README.md` are out of scope for this run.

## Verification Resources
- Run standard pytest suite: `pytest`
- Run Playwright E2E tests: `npx playwright test e2e/workspace.spec.js`

## Acceptance Criteria

### Context Panel Behavior and Navigation
- [ ] In the graph view without an active selection, the context panel displays the three insight lists (Hubs, Bridges, Added in the last 7 days).
- [ ] Clicking an entity row from an insight list selects the entity and displays entity details in the context panel without leaving the graph view.
- [ ] In the home view, clicking an entity list row opens the entity detail in the context panel and preserves the home view.
- [ ] In the reader view, viewing an entity detail displays a back link to return to the document context panel.
- [ ] At least one automated Playwright E2E test in `e2e/workspace.spec.js` verifies opening entity details from the home view and graph view insight lists.
- [ ] All code comments, documentation, and tests follow ASD-STE100, code lines are not hard-wrapped, and Git commit messages are written in English only.