# Handoff Report: Forensic Audit for Milestone M2

Report Type: Hard (Task complete)  
Author: Forensic Auditor M2  
Working Directory: /home/fow/Projects/claire-bible/.agents/teamwork/teamwork_auditor_m2_1/  
Target Recipient: Orchestrator (466a086f-7210-4e3e-9220-ce86cf072b6c)  

## 1. Observation

- File `src/claire/api/oauth.py` lines 894-925:
  Worker removed the unauthenticated fallback branch.
  Worker added validation with `accounts_store.validate_session_binding(conn, subject_token)`.
  If `binding is None or binding.account_id is None`, the handler returns HTTP 400 with `error="invalid_grant"` and `error_description="Subject token is invalid, expired, or unbound"`.
  The handler verifies account status with `accounts_store.AccountStore(conn).get(binding.account_id)`.
  If `acc.status not in accounts_store.CREDENTIAL_ACCEPTING_STATUSES`, the handler returns HTTP 400 with `error="invalid_grant"` and `error_description=f"Account is {acc.status}"`.
  The handler handles `accounts_store.AccountNotFound` and returns HTTP 400 with `error_description="Account not found"`.
  The handler executes `UPDATE oauth_tokens SET account_id=? WHERE token=?` with the bound integer `exchange_account_id` and commits.
  The connection closes in a `finally` block.

- File `tests/test_oauth_token_exchange.py`:
  The file contains 10 unit tests for RFC 8693 token exchange.
  Tests check missing tokens, valid admin tokens, valid member tokens, unbound tokens, non-existent tokens, expired tokens, unapproved tokens, suspended/disabled accounts, deleted accounts, and non-admin account isolation across token refreshes.
  Command `uv run pytest tests/test_oauth_token_exchange.py -v` ran 10 tests and all 10 tests passed in 1.24s.

- File `tests/test_syndication_route.py` lines 248-266:
  The file contains test `test_prototype_syndication_sealed_and_isolated`.
  The test checks that unauthenticated requests to `/prototype/syndication` fail with 401, 403, or 404.
  The test checks that authenticated requests to `/prototype/syndication` return the isolated prototype.
  The test checks that live `/syndication` contains no prototype seeds or mock factories.
  Command `uv run pytest tests/test_syndication_route.py -v` ran 25 tests and all 25 tests passed in 1.98s.

- Command `uv run pytest tests/test_oauth_flow.py tests/test_token_oauth_mcp_isolation.py e2e/test_syndication_e2e.py -v`:
  The command ran 18 tests and all 18 tests passed in 1.83s.

- Command `bash ./scripts/ci.sh`:
  CI executed 2,790 tests. 2,789 tests passed, 1 test failed in `tests/test_security_hotfixes.py::test_escape_helpers_escape_single_quote[adoc_parser.js-re]`.
  The failure is caused by pre-existing commit `c4d0e1e` which updated `function esc(s)` in `src/claire/static/js/renderers/adoc_parser.js`.
  This pre-existing issue does not involve Milestone M2 files.

## 2. Logic Chain

1. Observation 1 shows that unauthenticated and unbound tokens return HTTP 400.
2. The code path that fell back to `ADMIN_ACCOUNT_ID = 1` was completely deleted.
3. Therefore, an unbound subject token cannot acquire admin account ID 1.
4. Observation 1 shows that `exchange_account_id` is set strictly to `int(binding.account_id)` and written to `oauth_tokens`.
5. Therefore, non-admin users receive tokens tied strictly to their own account ID.
6. Observation 2 shows that real SQLite database operations and HTTP requests verify all error and success branches.
7. No hardcoded return values or test-specific bypasses exist in `oauth.py`.
8. Observation 3 shows that `/prototype/syndication` is sealed behind system authentication, while live `/syndication` does not expose prototype artifacts.
9. All empirical tests executed independently by the auditor passed cleanly.
10. Therefore, the work product satisfies integrity criteria and the verdict is CLEAN.

## 3. Caveats

- One pre-existing test failure exists in the repository test suite (`tests/test_security_hotfixes.py` line 306). It was introduced by commit `c4d0e1e` on `adoc_parser.js` before Milestone M2.
- No changes to `src/claire/store/db.py` were made because the update query in `oauth.py` achieves account binding without schema modifications.

## 4. Conclusion

- Verdict: CLEAN.
- The implementation in `src/claire/api/oauth.py` satisfies Requirement R3.
- The syndication test contract in `tests/test_syndication_route.py` satisfies Requirement R5.
- No integrity violations, facade implementations, or hardcoded outputs were found.

## 5. Verification Method

To verify these findings independently, run these commands:
```bash
uv run pytest tests/test_oauth_token_exchange.py -v
uv run pytest tests/test_syndication_route.py -v
uv run pytest tests/test_oauth_flow.py tests/test_token_oauth_mcp_isolation.py e2e/test_syndication_e2e.py -v
```
To invalidate these findings:
1. Show that an unauthenticated or unbound session token can obtain an OAuth token.
2. Show that a member account token can write or read with admin account ID 1.
3. Show that live `/syndication` leaks prototype seeds or mock factories.
