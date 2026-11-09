## 2026-11-09T11:06:55Z
You are Worker M3.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m3_1/
You must follow ASD-STE100 rules for all non-user communication.
Do not hard-wrap code lines.
Write Git commit messages in English only.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive file ownership:
- PROJECT.md
- tests/test_security_hotfixes.py
- .agent-work/admin-settings/P2.md

Inputs to read first:
1. /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_3/survey_report.md
3. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_3/handoff.md
4. /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md

Tasks:
1. Implement Requirement R4 (PROJECT.md status update and hygiene):
   - Inspect PROJECT.md at the repository root.
   - Update Phase P2 milestones: mark M1, M2, M3 as COMPLETED.
   - Update PROJECT.md to reflect the current milestone: Account, IdP, and Fedify Preparation Sealing and Cleanup.
   - Include all features (Requirements R1 through R6) in the Feature Inventory and Milestones table.
   - Record interface contracts and code layout accurately.
   - Clean up any obsolete notes.

2. Implement Requirement R6 (Baseline test fix and CI gate):
   - Inspect tests/test_security_hotfixes.py around line 291:
     - Notice that test_escape_helpers_escape_single_quote fails because adoc_parser.js defines `function esc(s, quote) {`, whereas the regex pattern expects `function esc(s) {`.
     - Update the regex pattern to match `function esc\(s(?:,\s*quote)?\) \{.*?\n\}\n` (or `function esc\(s[^\)]*\) \{.*?\n\}\n`).
     - Run `uv run pytest tests/test_security_hotfixes.py -k test_escape_helpers_escape_single_quote -v` and ensure it passes.
   - Verify code formatting: Ensure no hard-wrapped code lines are introduced in any modified file.
   - Run the full verification suite:
     `bash ./scripts/ci.sh`
     Verify that all checks pass (syntax checks, docker compose config, uv lock check, and full pytest suite).

3. Documentation and Handoff:
   - Write your changes report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m3_1/changes.md
   - Write your handoff report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m3_1/handoff.md
   - When finished, send a message to your orchestrator with a summary and the path to your handoff report.
