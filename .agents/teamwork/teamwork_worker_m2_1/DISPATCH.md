## 2026-11-09T10:41:18Z

You are Worker M2.
Your working directory is /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m2_1/
You must follow ASD-STE100 rules for all non-user communication.
Do not hard-wrap code lines.
Write Git commit messages in English only.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Exclusive file ownership:
- src/claire/api/oauth.py
- tests/test_oauth_token_exchange.py
- tests/test_oauth_flow.py
- tests/test_syndication_route.py
- e2e/test_syndication_e2e.py
- tests/test_e2e_syndication_namespace.py

Inputs to read first:
1. /home/fow/Projects/claire-bible/.agents/teamwork/ORIGINAL_REQUEST.md
2. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_2/survey_report.md
3. /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_explorer_survey_2/handoff.md
4. /home/fow/Projects/claire-bible/.agents/teamwork/orchestrator/SCOPE.md

Tasks:
1. Implement Requirement R3 (Token Exchange Account Binding Integrity):
   - In src/claire/api/oauth.py around lines 884-930:
     - Remove the unauthenticated fallback branch (lines 906-921).
     - Require that `binding = accounts_store.validate_session_binding(conn, subject_token)` is NOT None.
     - If binding is None, return:
       `JSONResponse({"error": "invalid_grant", "error_description": "Subject token is invalid, expired, or unbound"}, status_code=400)`
     - Validate that binding.account_id is resolved to an existing active account in accounts_store.
     - Prevent silent fallback to admin account ID 1. When issuing the token, pass `account_id=exchange_account_id` to token generation (or update the token atomically with exchange_account_id and ensure exchange_account_id is strictly not None).
   - Write comprehensive unit tests in tests/test_oauth_token_exchange.py:
     - Test token exchange with valid bound session token: verify token is issued and bound to the subject account ID.
     - Test token exchange with unbound session token: verify HTTP 400 invalid_grant.
     - Test token exchange with non-existent or invalid subject token: verify HTTP 400 invalid_grant.
     - Test token exchange with inactive or suspended account: verify HTTP 400 invalid_grant.
     - Test that non-admin accounts NEVER acquire admin account ID 1.

2. Implement Requirement R5 (Syndication Prototype Sealing and Cleanup):
   - Review `/prototype/syndication` and seed components.
   - Cleanly seal `/prototype/syndication` as an authenticated, isolated reference prototype.
   - Ensure live `/syndication` does not use seed data or prototype components.
   - Verify all 5 syndication test suites pass without regression:
     `uv run pytest tests/test_syndication_route.py e2e/test_syndication_e2e.py tests/test_e2e_syndication_namespace.py tests/test_api_security.py tests/test_route_surface_contract.py -v`

3. Testing and Verification:
   - Run tests:
     `uv run pytest tests/test_oauth_token_exchange.py tests/test_oauth_flow.py tests/test_token_oauth_mcp_isolation.py tests/test_syndication_route.py e2e/test_syndication_e2e.py -v`
   - Verify that all tests pass.
   - Ensure no hard-wrapped code lines are introduced.
   - Write your changes report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m2_1/changes.md
   - Write your handoff report to /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_worker_m2_1/handoff.md
   - When finished, send a message to your orchestrator with a summary and the path to your handoff report.
