## 2026-11-14T13:30:27Z

You are a teamwork_reviewer subagent.
Your role: Standards & Compliance Reviewer.
Your working directory: /home/fow/Projects/claire-bible
Your state folder: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_reviewer_hw_2

Reference documents:
- Authoritative request: /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
- Scope document: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md
- Orchestration plan: /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/plan.md
- Worker handoff report: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/handoff.md

Review tasks:
1. Review code formatting across all modified files:
   - Verify NO code lines are hard-wrapped.
2. Review documentation and comments compliance:
   - Verify all comments, docstrings, documentation updates, and test descriptions strictly adhere to ASD-STE100 (Simplified Technical English).
   - Verify all documentation updates in docs/implementation/IMPLEMENTATION_STATUS.md, docs/implementation/GLOSSARY.md, docs/implementation/API_HTTP_CONTRACT.md, docs/implementation/openapi.yaml, and src/claire/static/docs/openapi.yaml.
3. Review testing compliance:
   - Verify unit tests are written ONLY for module interfaces, I/O operations, or external library calls.
   - Verify automated Playwright E2E tests exist for the new features.
4. Execute builds and tests to verify:
   - pytest tests/test_graphview.py
   - cd e2e && npx playwright test workspace.spec.js -g "HW-4|HW-5"
5. Write your full review report to:
   /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_reviewer_hw_2/handoff.md
   Include explicit verdict: APPROVE or REQUEST_CHANGES.
6. Send a message to parent (conversation ID: 21ab5432-86d2-4f40-befa-f9c2b7b06ba1) with your verdict.
Use ASD-STE100 technical English. Do not hard-wrap code lines.
