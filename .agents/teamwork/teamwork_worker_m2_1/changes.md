# Changes Report — Milestone M2 (R3 & R5)

## 1. Overview
This report lists the changes made for Requirement R3 and Requirement R5.

## 2. Modified Files

### 2.1 `src/claire/api/oauth.py`
- Location: lines 896 to 925 in `handle_token`.
- Purpose: Enforce account binding integrity for RFC 8693 Token Exchange.
- Changes:
  - Removed the unauthenticated fallback branch that called `exchange_session_token` and `validate_session_scope`.
  - Added strict validation of `accounts_store.validate_session_binding(conn, subject_token)`.
  - Returned HTTP 400 `invalid_grant` with description `"Subject token is invalid, expired, or unbound"` if the binding is missing or the account ID is null.
  - Verified account status through `AccountStore(conn).get(binding.account_id)`.
  - Returned HTTP 400 `invalid_grant` with description `"Account is <status>"` if the account status is not in `CREDENTIAL_ACCEPTING_STATUSES`.
  - Returned HTTP 400 `invalid_grant` with description `"Account not found"` if the account does not exist.
  - Prevented silent fallback to admin account ID 1.
  - Set `exchange_account_id` to the integer value of `binding.account_id`.
  - Updated `oauth_tokens` atomically with `exchange_account_id` immediately after token creation.

### 2.2 `tests/test_oauth_token_exchange.py`
- Location: new test file.
- Purpose: Comprehensive unit test coverage for RFC 8693 Token Exchange account binding integrity.
- Test Cases Added:
  - `test_token_exchange_missing_subject_token`: Verifies HTTP 400 `invalid_request` with `"Missing subject_token"`.
  - `test_token_exchange_valid_bound_session_token_admin`: Verifies successful exchange for admin account ID 1.
  - `test_token_exchange_valid_bound_session_token_member`: Verifies successful exchange for member account ID 2.
  - `test_token_exchange_unbound_session_token_rejected`: Verifies HTTP 400 `invalid_grant` for unbound session tokens.
  - `test_token_exchange_invalid_subject_token_rejected`: Verifies HTTP 400 `invalid_grant` for non-existent tokens.
  - `test_token_exchange_expired_subject_token_rejected`: Verifies HTTP 400 `invalid_grant` for expired tokens.
  - `test_token_exchange_unapproved_subject_token_rejected`: Verifies HTTP 400 `invalid_grant` for unapproved tokens.
  - `test_token_exchange_inactive_or_suspended_account_rejected`: Verifies HTTP 400 `invalid_grant` for suspended or disabled accounts.
  - `test_token_exchange_deleted_account_rejected`: Verifies HTTP 400 `invalid_grant` for deleted accounts.
  - `test_token_exchange_non_admin_never_acquires_admin_account_id`: Verifies that member accounts (IDs 10, 11, 12) never acquire admin account ID 1 during token exchange or token refresh.

### 2.3 `tests/test_syndication_route.py`
- Location: lines 248 to 266.
- Purpose: Verify prototype syndication sealing and isolation contract.
- Changes:
  - Added test `test_prototype_syndication_sealed_and_isolated`.
  - Verified that unauthenticated requests to `/prototype/syndication` are rejected with HTTP 401, 403, or 404.
  - Verified that authenticated requests to `/prototype/syndication` serve the self-contained reference prototype.
  - Verified that live `/syndication` does not use seed data or prototype client factories.
