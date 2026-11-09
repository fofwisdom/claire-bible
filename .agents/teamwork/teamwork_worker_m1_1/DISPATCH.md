## 2026-11-09T10:19:59Z
You are Worker M1.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/
You must follow ASD-STE100 rules for all non-user communication.
Do not hard-wrap code lines.
Write Git commit messages in English only.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive file ownership:
- src/claire/store/accounts.py
- src/claire/api/accounts_routes/me.py
- src/claire/api/accounts_routes/console.py
- src/claire/static/js/console_account.js
- src/claire/accounts/username_policy.py
- tests/test_account_routes.py
- tests/test_username_policy.py
- tests/test_route_surface_contract.py

Inputs to read first:
1. /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1/survey_report.md
3. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_1/handoff.md
4. /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md

Tasks:
1. Implement Requirement R1 (profile_public toggle and account contract seal):
   - In src/claire/store/accounts.py:
     Add update_profile_public method to AccountStore:
     `def update_profile_public(self, account_id: int, profile_public: int | bool, *, now: float | None = None) -> Account:`
     Ensure it updates the profile_public column (0 or 1) and updated_at, commits, and returns the updated Account.
   - In src/claire/api/accounts_routes/me.py:
     Update me_payload to include `"profile_public": 1 if account.profile_public else 0`.
     In me_patch (PATCH /api/me), accept body keys `{"display_name", "profile_public"}`.
     Validate profile_public is 0 or 1 (or bool that maps to 0 or 1).
     Update the account using store.update_profile_public when profile_public is provided.
     Return me_payload containing the updated profile_public value.
   - In src/claire/api/accounts_routes/console.py:
     In _settings_account_body (around lines 846-854), remove the obsolete static note data-profile-public-hint.
     In network mode, render the toggle switch control `<input type="checkbox" switch data-settings-toggle="profile-public" ...>` with appropriate checked attribute based on account.profile_public.
     Preserve rule S9 (hide control in single mode).
   - In src/claire/static/js/console_account.js:
     Add an event listener for `[data-settings-toggle="profile-public"]` change events that calls PATCH /api/me with `{ profile_public: input.checked ? 1 : 0 }`.
   - Update tests in tests/test_account_routes.py:
     Update line 183 to expect `"profile_public": 0` in GET /api/me.
     Add comprehensive tests for PATCH /api/me with profile_public=1, profile_public=0, invalid values (e.g. 2, "yes"), and combined with display_name.
     Add at least one E2E test exercising the /settings/account toggle and /api/me integration.

2. Implement Requirement R2 (Reserved usernames synchronization):
   - In src/claire/accounts/username_policy.py:
     Add Fedify federation and IdP-related reserved words to RESERVED_USERNAMES:
     `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, `sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`.
   - In tests/test_username_policy.py:
     Synchronize SPEC_RESERVED to match RESERVED_USERNAMES.
   - Verify tests in tests/test_username_policy.py and tests/test_route_surface_contract.py.

3. Testing and Verification:
   - Run tests:
     `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py -v`
   - Verify that all tests pass.
   - Ensure no hard-wrapped code lines are introduced.
   - Write your changes report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/changes.md
   - Write your handoff report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m1_1/handoff.md
   - When finished, send a message to your orchestrator with a summary and the path to your handoff report.
