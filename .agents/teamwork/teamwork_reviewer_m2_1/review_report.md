# Quality and Adversarial Review Report — Milestone M2 (R3 & R5)

## Review Summary

**Verdict**: APPROVE

Worker M2 correctly implemented Requirement R3 and Requirement R5.
The implementation eliminated all unauthenticated fallback paths in RFC 8693 Token Exchange.
The token exchange handler strictly binds issued tokens to valid account IDs.
Non-admin tokens cannot acquire admin account ID 1.
The syndication prototype route `/prototype/syndication` remains sealed and isolated under permission `_system("read")`.
All 53 target tests and all 109 syndication tests passed without regressions.
No integrity violations were found.

## Findings

### [Minor] Finding 1: Pre-existing Repository Test Mismatch in `tests/test_security_hotfixes.py`

- What: Test `test_escape_helpers_escape_single_quote[adoc_parser.js-re]` fails in the full CI suite.
- Where: `tests/test_security_hotfixes.py:291` and `src/claire/static/js/renderers/adoc_parser.js:6`.
- Why: Commit `c4d0e1e` on `main` added a `quote` argument to `esc()`. The test regex still looks for `function esc(s) {`.
- Scope: This defect exists on `origin/main` and is unrelated to Milestone M2 code changes.
- Suggestion: Update the test regex in `tests/test_security_hotfixes.py:291` to allow the optional parameter.

## Verified Claims

- Unauthenticated fallback eliminated in `src/claire/api/oauth.py` -> verified via code inspection and test execution -> pass
- Unbound subject tokens rejected with HTTP 400 `invalid_grant` -> verified via `tests/test_oauth_token_exchange.py` -> pass
- Issued tokens bound strictly to `exchange_account_id` -> verified via database inspection in unit tests -> pass
- Non-admin subject token cannot acquire admin account ID 1 -> verified via exchange and token rotation tests -> pass
- Route `/prototype/syndication` sealed under `_system("read")` -> verified via route surface and permissions tests -> pass
- All 5 syndication test suites pass without regression -> verified via 109 tests execution -> pass
- Target Milestone M2 test suite passes -> verified via `pytest` (53 tests passed) -> pass

## Coverage Gaps

No coverage gaps identified. The new test file `tests/test_oauth_token_exchange.py` covers missing, invalid, unbound, expired, suspended, and member tokens.

## Unverified Items

None. All required claims were independently verified.

---

## Adversarial Challenge Report

## Challenge Summary

**Overall risk assessment**: LOW

The code modifications are minimal, clean, and fail-closed.
The handler enforces strict account binding before token creation.
Database updates execute immediately before HTTP responses return.

## Challenges

### [Low] Challenge 1: Privilege Escalation via Actor Token or RFC 8693 Parameters

- Assumption challenged: The token exchange handler only evaluates `subject_token` and ignores forged delegation parameters.
- Attack scenario: An attacker provides `actor_token` or custom grant parameters to impersonate admin account ID 1.
- Blast radius: Unauthorized privilege escalation to administrator account.
- Mitigation: The handler only parses `subject_token` and resolves account identity strictly from `validate_session_binding`. It ignores all untrusted parameters.
- Result: Pass.

### [Low] Challenge 2: Inactive or Suspended Account Token Exchange

- Assumption challenged: Suspended or disabled users cannot exchange session tokens for OAuth access tokens.
- Attack scenario: A suspended account holder retains an active session token and attempts RFC 8693 token exchange.
- Blast radius: Suspended accounts could continue reading system resources via MCP or API.
- Mitigation: The handler explicitly calls `AccountStore(conn).get(binding.account_id)` and rejects accounts not in `CREDENTIAL_ACCEPTING_STATUSES` with HTTP 400 `invalid_grant`.
- Result: Pass.

### [Low] Challenge 3: Refresh Token Privilege Mutation

- Assumption challenged: Rotating an OAuth token issued through token exchange preserves the original member account ID.
- Attack scenario: A member client exchanges a token and then requests token refresh to acquire default admin account ID 1.
- Blast radius: Member elevation to admin account ID 1 during token rotation.
- Mitigation: `refresh_oauth_token` updates only `token`, `refresh_token`, and `expires_at` on the existing row. The `account_id` column does not change.
- Result: Pass. Verified in `test_token_exchange_non_admin_never_acquires_admin_account_id`.

### [Low] Challenge 4: Prototype Route Exposure or Mock Leakage

- Assumption challenged: The reference prototype `/prototype/syndication` is isolated and does not leak prototype mocks to live syndication.
- Attack scenario: Unauthenticated visitors access prototype UI or live syndication loads prototype seed objects.
- Blast radius: Information leakage or prototype code execution in production routes.
- Mitigation: Route `/prototype/syndication` requires `_system("read")`. Anonymous access receives HTTP 401, 403, or 404. Live route `/syndication` contains zero prototype seeds or mock factories.
- Result: Pass. Verified in `test_prototype_syndication_sealed_and_isolated`.

## Stress Test Results

- Missing `subject_token` -> Returns HTTP 400 `invalid_request` -> Pass
- Unbound session token (`account_id IS NULL`) -> Returns HTTP 400 `invalid_grant` -> Pass
- Suspended account session -> Returns HTTP 400 `invalid_grant` ("Account is suspended") -> Pass
- Member account session exchange -> Issued token has `account_id == 2` (`!= 1`) -> Pass
- Member account token refresh -> Rotated token retains `account_id == 2` (`!= 1`) -> Pass
- Anonymous request to `/prototype/syndication` -> Rejection status code in {401, 403, 404} -> Pass

## Unchallenged Areas

None. All relevant attack surfaces for R3 and R5 were investigated.
