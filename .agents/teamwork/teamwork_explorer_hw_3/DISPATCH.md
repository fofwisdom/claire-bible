## 2026-11-14T12:34:14Z
From: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1
Priority: MESSAGE_PRIORITY_HIGH

You are a teamwork_explorer subagent.
Your role: Reader Marks & Test Investigator.
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3

Task instructions:
1. Read the authoritative request file: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. Investigate the codebase for HW-5 (Entity marks in reader text and server aliases) and test infrastructure:
   - Server endpoint: Inspect GET /document handler in Python codebase (e.g., in src/claire/api/ or web handlers). Determine where nodes and entities are formatted. Specify how to add nodes[].aliases without changing detail or detail_html.
   - Reader text: Inspect reader text rendering (#rbody, summary and detail rendering in static/js or templates).
   - CSS Custom Highlight API: Inspect browser API support and usage patterns. Verify requirements: lowerCase matching, ASCII boundaries, 2-3 character case sensitivity, longest match first, collision suppression (no mark if candidate belongs to multiple entities), limits (400,000 text chars, 300 candidates), strict preservation of textContent and innerHTML on #rbody, localStorage switch claireEntityMarks, selection click behavior (GET /node only, zero POST/mutation), share page exclusion.
   - Test infrastructure & baseline:
     - Run or inspect pytest test suites (e.g., pytest).
     - Run or inspect Playwright E2E tests (e.g., npx playwright test e2e/workspace.spec.js).
     - Check how documents, entities, and nodes are seeded/mocked in tests.
     - Determine where new unit tests and Playwright E2E tests should be placed according to repository testing rules (unit tests only for module interfaces, I/O operations, external library calls; at least one E2E test).
3. Identify all files to be modified or created, test commands, and baseline results.
4. Write your full analysis and handoff report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/handoff.md
5. Keep progress updated in /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_hw_3/progress.md.
6. When complete, send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1) confirming completion and referencing the handoff path.
All comments and documentation must comply with ASD-STE100.
Do not hard-wrap code lines.
