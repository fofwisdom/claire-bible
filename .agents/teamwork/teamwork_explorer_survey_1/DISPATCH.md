## 2026-11-09T08:52:56Z

You are Survey Explorer 1.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1/
You must follow ASD-STE100 rules for technical writing.
Do not write or modify source code files. You are a read-only exploration agent.

Task:
1. Read the authoritative user request at:
   /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. Investigate Requirement R1 (profile_public toggle and account contract seal):
   - Find where PATCH /api/me is defined and processed.
   - Find the account model and database schema for profile_public.
   - Check default value, type (integer 0 or 1 / boolean), and validation.
   - Find where /settings/account is rendered (templates and JavaScript).
   - Check how the UI toggle control is displayed, read, and submitted.
3. Investigate Requirement R2 (Reserved usernames synchronization):
   - Inspect src/claire/accounts/username_policy.py (RESERVED_USERNAMES set/list).
   - Identify missing reserved names for Fedify and IdP:
     inbox, outbox, actor, actors, ap, fedify, federation, nodeinfo, oauth, auth, followers, following, shared_inbox, and any related words.
   - Inspect tests/test_username_policy.py, tests/test_route_surface_contract.py, and other test files that verify reserved usernames.
   - Check if any current tests or routes conflict with these reserved names.
4. Document all findings:
   - Write your full report to:
     /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1/survey_report.md
   - Write your handoff report to:
     /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1/handoff.md
5. Update your progress.md regularly with timestamps.
6. When done, send a message to your orchestrator with a summary and path to your handoff report.
