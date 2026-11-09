# Review Report: Milestone M2 Implementation

## Review Summary

**Verdict**: APPROVE

## Findings

### [Minor] Finding 1: Two-Step Insertion of OAuth Tokens
- What: `create_oauth_token` inserts a token with default `account_id = 1` before `oauth.py` updates it to `exchange_account_id`.
- Where: `src/claire/api/oauth.py`, lines 918-922.
- Why: If a crash occurs between the insert and the update, an orphaned token record stays in SQLite. The caller does not get this token because the transaction terminates with HTTP 500. But a single-step insert with `account_id` parameter is cleaner.
- Suggestion: In milestone M3 or P3 L3-e refactoring, pass `account_id` directly to `create_oauth_token`.

### [Minor] Finding 2: Lenient Validation of `subject_token_type`
- What: RFC 8693 Section 2.1 specifies `subject_token_type` as a required parameter.
- Where: `src/claire/api/oauth.py`, line 886.
- Why: The endpoint reads `subject_token_type`, but does not reject requests if this parameter is missing or has an unsupported value.
- Suggestion: Validate that `subject_token_type == "urn:ietf:params:oauth:token-type:access_token"` if strict RFC compliance is desired.

## Verified Claims

- RFC 8693 token exchange rejects missing subject token -> verified via pytest -> pass
- RFC 8693 token exchange rejects forged, invalid, or expired tokens -> verified via pytest and adversarial tests -> pass
- RFC 8693 token exchange rejects unbound session tokens -> verified via direct inspection and pytest -> pass
- RFC 8693 token exchange rejects inactive, suspended, or disabled accounts -> verified via pytest -> pass
- RFC 8693 token exchange prevents privilege escalation to admin account 1 -> verified via pytest and database queries -> pass
- Token refresh preserves the bound account ID and rejects inactive accounts -> verified via pytest -> pass
- `/prototype/syndication` remains sealed behind authentication -> verified via pytest -> pass
- Live `/syndication` remains isolated from prototype seed data and mock factories -> verified via pytest -> pass
- All 119 requested unit and route tests pass cleanly -> verified via `uv run pytest` -> pass

## Coverage Gaps

- Concurrency under extreme thread contention in `TestClient` — risk level: low — recommendation: accept risk (Starlette `TestClient` limitation with streamable HTTP manager, production ASGI servers use separate event loops or worker processes).

## Unverified Items

- None. All modified files and contracts were independently inspected and tested.

## Challenge Summary

**Overall risk assessment**: LOW

## Challenges

### [Low] Challenge 1: Intermediate State During Token Creation
- Assumption challenged: Token creation and account ID update execute safely.
- Attack scenario: A database trigger aborts the UPDATE statement after INSERT succeeds.
- Blast radius: Request fails with HTTP 500. Caller receives no token. Orphaned row stays in `oauth_tokens` with `account_id = 1`.
- Mitigation: In future refactor, pass `account_id` directly into `create_oauth_token`.

### [Low] Challenge 2: Client Submits Invalid `subject_token_type`
- Assumption challenged: Client submits standard RFC 8693 token type URI.
- Attack scenario: Client omits `subject_token_type` or sends random string.
- Blast radius: Request succeeds if `subject_token` is valid. No privilege escalation occurs.
- Mitigation: Add explicit validation for `subject_token_type`.

## Stress Test Results

- Missing subject token -> HTTP 400 invalid_request -> pass
- Non-existent subject token -> HTTP 400 invalid_grant -> pass
- Expired subject token -> HTTP 400 invalid_grant -> pass
- Unapproved subject token -> HTTP 400 invalid_grant -> pass
- Suspended or disabled account -> HTTP 400 invalid_grant -> pass
- Deleted account -> HTTP 400 invalid_grant -> pass
- Member account session token -> Token bound to member ID, never ID 1 -> pass
- Token refresh -> New token keeps member ID -> pass
- Unauthenticated access to `/prototype/syndication` -> HTTP 401/403/404 -> pass
- Authenticated access to live `/syndication` -> No prototype seed data -> pass

## Unchallenged Areas

- None within Milestone M2 boundary.
