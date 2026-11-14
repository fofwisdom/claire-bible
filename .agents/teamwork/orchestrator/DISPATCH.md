# Dispatch Record

## 2026-11-09T08:51:11Z
Execute sealing and cleanup tasks in Claire Bible to prepare for account, IdP, and Fedify MIT sidecar integration.

Working directory: /home/fow/Projects/claire-bible
Integrity mode: development

Requirements:
- R1. profile_public Toggle and Account Contract Seal
- R2. Reserved Usernames Synchronization
- R3. Token Exchange Account Binding Integrity
- R4. PROJECT.md Status Update and Hygiene
- R5. Syndication Prototype Sealing and Cleanup
- R6. Engineering Rules

## 2026-11-09T10:17:49Z
Server was restarted. All 3 explorer survey handoffs have completed and are available:
- .agents/teamwork/teamwork_explorer_survey_1/handoff.md (R1 & R2: account profile_public and reserved usernames)
- .agents/teamwork/teamwork_explorer_survey_2/handoff.md (R3 & R5: token exchange binding and syndication cleanup)
- .agents/teamwork/teamwork_explorer_survey_3/handoff.md (R4 & R6: PROJECT.md hygiene and CI baseline)

Please resume execution: synthesize the survey findings, decompose implementation milestones, update PROJECT.md / plan.md, and proceed with worker dispatch.


## 2026-11-14T12:30:57Z
Implement units HW-4 (Local graph and hover links) and HW-5 (Entity marks and server aliases) from `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` as production features for the Claire Bible web application, delivered in a single combined commit.

Integrity mode: development

Reference material:
- Design document: `docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md` (Sections 6.4, 7, 8, 9, 12)
- Existing commits: HW-1 (`212d50c0`), HW-2 (`5d372178`), HW-3 (`1147c28b`)

## Requirements

### R1. HW-4: Local Graph and Hover Connections
Implement the local graph in the Links tab of the document context panel and cross-view hover links.
- Render an inline SVG drawing in the Links tab representing the open document at the center, its entities in Ring 1 (up to 24 entities with highest degree), and related entities from other documents in Ring 2 (up to 12 entities connected via graph relations). If entity counts exceed these limits, display text below indicating the unshown entity count.
- Apply the visual grammar: solid lines for non-provisional relations, dashed lines for provisional relations (`dashes`), and thin lines connecting the document to Ring 1 entities. Before `GET /graph` is loaded, display Ring 1 only.
- Support interactive selection: clicking an entity selects it (`GET /node`). Hovering over an entity highlights the same entity across the local graph, lists, cards, and reader text marks.
- Expose each entity in the accessibility tree as an accessible button with its name.

### R2. HW-5: Entity Marks in Reader Text and Server Aliases
Implement entity marks in reader text using the CSS Custom Highlight API without modifying the DOM tree, and update the server `GET /document` endpoint.
- Update `GET /document` to return `nodes[].aliases` containing the alias list for each entity, while keeping `detail` and `detail_html` identical.
- Highlight entity candidate mentions (names and aliases of the document's entities) in summary and detail text following matching rules: lowerCase comparison; ASCII boundary checks; short name handling (>= 2 characters, 2-3 ASCII characters case-sensitive); longest candidate first; no mark if a candidate belongs to multiple entities; skip highlighting if text exceeds 400,000 characters or candidate names exceed 300.
- Ensure `textContent` and `innerHTML` of `#rbody` remain identical whether marks are enabled or disabled (`localStorage` switch `claireEntityMarks`).
- Clicking marked text selects the entity (`GET /node`) without initiating any mutation or POST request. Highlights must not appear on the share page.

### R3. Single Commit, Project Rules, and Documentation Updates
Combine HW-4 and HW-5 into a single Git commit adhering to repository guidelines.
- Use ASD-STE100 for all code comments, documentation, and test descriptions.
- Write the Git commit message in English.
- Do not hard-wrap code lines.
- Write unit tests only for module interfaces, I/O operations, or external library calls, and write at least one end-to-end (E2E) test in Playwright.
- Update `docs/implementation/IMPLEMENTATION_STATUS.md`, `docs/implementation/GLOSSARY.md`, `docs/implementation/API_HTTP_CONTRACT.md`, and `docs/implementation/openapi.yaml` in the commit.

## Verification Resources
- Run standard pytest suite: `pytest`
- Run Playwright E2E tests: `npx playwright test e2e/workspace.spec.js`

## Acceptance Criteria

### HW-4 Local Graph
- [ ] In the Links tab of the document context panel, the local graph displays Ring 1 and Ring 2 entities according to specified degree limits and line grammar.
- [ ] Clicking an entity node in the local graph selects the entity and opens its details.
- [ ] Hovering over an entity highlights corresponding items across lists, cards, and reader text.
- [ ] At least one automated E2E test verifying local graph rings and entity selection passes.

### HW-5 Entity Marks & Server Aliases
- [ ] `GET /document` includes `nodes[].aliases` while `detail` and `detail_html` remain identical before and after.
- [ ] T1: `#rbody` `textContent` and `innerHTML` are strictly identical whether highlights are on or off.
- [ ] T2: Clicking a highlight triggers only `GET /node` with no POST request initiated.
- [ ] T3: Highlights do not appear on the share page of the document.
- [ ] T4: Match cases (word boundaries, short acronyms, overlaps, collisions) pass automated verification.

### Repository Standards & Deliverable
- [ ] All new and existing automated tests (unit and Playwright E2E) pass.
- [ ] Implementation documentation (`IMPLEMENTATION_STATUS.md`, `GLOSSARY.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml`) is updated.
- [ ] Code comments, tests, and documentation adhere to ASD-STE100, lines are not hard-wrapped, and the commit message is in English.
- [ ] All changes are committed in a single git commit.
