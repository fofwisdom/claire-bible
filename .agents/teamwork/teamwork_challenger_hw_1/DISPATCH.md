## 2026-11-14T13:30:38Z
You are a teamwork_challenger subagent.
Your role: HW-4 Adversarial Verifier.
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_challenger_hw_1

Reference documents:
- Authoritative request: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
- Scope document: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md
- Orchestration plan: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/plan.md
- Worker handoff report: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/handoff.md

Adversarial challenge tasks:
1. Empirically verify HW-4 Local Graph and Hover Connections in client UI:
   - Check concentric layout: center document node at (0, 0), Ring 1 at radius 78, Ring 2 at radius 148.
   - Test Ring 1 capacity limits: max 24 entities sorted descending by degree.
   - Test Ring 2 capacity limits: max 12 external entities connected via allEdges.
   - Test unshown entity count text: verify correct count appears below SVG when limits are exceeded.
   - Test visual line grammar: thin lines to center, solid lines for non-provisional edges, dashed lines (stroke-dasharray="3 3") for provisional edges.
   - Test pre-graph loading fallback: verify Ring 1 renders before GET /graph loads, and updates when graph data arrives.
   - Test interactive selection: verify clicking node calls selectDocEntity(id) -> GET /node without any POST request.
   - Test accessibility: verify each entity node is exposed as an accessible button with name in accessibility tree (role="button", tabindex="0", aria-label). Test Enter and Space key activation.
   - Test cross-view hover synchronization: verify hovering an entity highlights the same entity across local graph, Entities tab, Knowledge Map card, left entity list, and reader marks.
2. Run automated tests and write challenge harnesses or scripts if needed:
   - cd e2e && npx playwright test workspace.spec.js -g "HW-4"
3. Write your full challenge report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_challenger_hw_1/handoff.md
   Include explicit verdict: APPROVE or REQUEST_CHANGES.
4. Send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1) with your verdict.
Use ASD-STE100 technical English. Do not hard-wrap code lines.
