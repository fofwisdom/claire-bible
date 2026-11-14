## 2026-11-14T12:33:52Z
[Message] timestamp=2026-11-14T12:33:52Z sender=21ab5432-86d2-4f40-befa-f9c2b7b06ba1 priority=MESSAGE_PRIORITY_HIGH content=You are a teamwork_spec_miner subagent.
Your role: Specification Investigator (Design & Contracts).
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_spec_miner_hw_1

Task instructions:
1. Read the authoritative request file: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. Investigate the design specifications:
   - Read docs/design/HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md (specifically Sections 6.4, 7, 8, 9, 12 in detail).
   - Review past commits HW-1 (212d50c0), HW-2 (5d372178), HW-3 (1147c28b) using git show or git log.
   - Inspect existing implementation docs: docs/implementation/IMPLEMENTATION_STATUS.md, docs/implementation/GLOSSARY.md, docs/implementation/API_HTTP_CONTRACT.md, docs/implementation/openapi.yaml.
3. Extract and specify all detailed requirements and edge cases for:
   - HW-4: Local Graph and Hover Connections (Ring 1 max 24 sorted by degree, Ring 2 max 12 via graph relations, unshown entity counts text, line grammar with solid/dashes/thin, behavior before GET /graph loads, click selection GET /node, hover synchronization, a11y accessible buttons).
   - HW-5: Entity Marks in Reader Text & Server Aliases (GET /document nodes[].aliases, detail/detail_html preservation, CSS Custom Highlight API rules, lowerCase comparison, ASCII boundary checks, 2-3 char case sensitivity, longest match first, collision discard, 400,000 char & 300 candidates limits, textContent & innerHTML preservation on #rbody, localStorage switch claireEntityMarks, click selection GET /node, share page exclusion).
   - Documentation updates required in IMPLEMENTATION_STATUS.md, GLOSSARY.md, API_HTTP_CONTRACT.md, and openapi.yaml.
4. Write your full analysis and handoff report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_spec_miner_hw_1/handoff.md
5. Keep progress updated in /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_spec_miner_hw_1/progress.md.
6. When complete, send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1) confirming completion and referencing the handoff path.
All comments and documentation must comply with ASD-STE100.
Do not hard-wrap code lines.
