# Orchestration Plan: Account, IdP, and Fedify Preparation

## Plan Summary
This plan decomposes the approved user request into three sequential milestones. Each milestone executes an iteration loop with workers, reviewers, challengers, and an auditor.

## Milestones

### Milestone 1 (M1): Account & Username Surface Contracts (R1 & R2)
- **Scope**:
  - `src/claire/store/accounts.py`: Add `update_profile_public` method.
  - `src/claire/api/accounts_routes/me.py`: Update `me_payload` to include `"profile_public"`. Accept `profile_public` (0 or 1) in `PATCH /api/me`.
  - `src/claire/api/accounts_routes/console.py`: Remove obsolete hint. Render switch `<input type="checkbox" switch data-settings-toggle="profile-public">` in network mode. Obey S9 in single mode.
  - `src/claire/static/js/console_account.js`: Add toggle change handler calling `PATCH /api/me`.
  - `src/claire/accounts/username_policy.py`: Add Fedify and IdP reserved words to `RESERVED_USERNAMES`.
  - Tests: Update `tests/test_account_routes.py`, `tests/test_username_policy.py`, `tests/test_route_surface_contract.py`. Add unit and E2E test cases.
- **Verification**: `uv run pytest tests/test_account_routes.py tests/test_username_policy.py tests/test_route_surface_contract.py`

### Milestone 2 (M2): OAuth Token Exchange Integrity & Syndication Prototype Sealing (R3 & R5)
- **Scope**:
  - `src/claire/api/oauth.py`: Remove lines 906-921 unauthenticated fallback. Require valid session binding and valid `account_id`. Return HTTP 400 `invalid_grant` if binding is missing. Ensure `account_id=exchange_account_id` is passed to token creation.
  - `tests/test_oauth_token_exchange.py`: Write comprehensive unit and integration tests for RFC 8693 token exchange. Verify rejection of unbound subject tokens. Verify bound tokens retain correct `account_id`.
  - Syndication: Keep `/prototype/syndication` as a sealed reference prototype. Keep live `/syndication` free of prototype seeds.
  - Tests: Verify all 5 syndication test suites pass (`tests/test_syndication_route.py`, `e2e/test_syndication_e2e.py`, `tests/test_e2e_syndication_namespace.py`, `tests/test_api_security.py`, `tests/test_route_surface_contract.py`).
- **Verification**: `uv run pytest tests/test_oauth_token_exchange.py tests/test_oauth_flow.py tests/test_syndication_route.py e2e/test_syndication_e2e.py`

### Milestone 3 (M3): Hygiene, Baseline Hotfix, PROJECT.md Update & Full CI Gate (R4 & R6)
- **Scope**:
  - `tests/test_security_hotfixes.py`: Update regex at line 291 to accept parameters in `function esc(...)`.
  - `PROJECT.md`: Update status of Phase P2 to completed. Retitle and document current milestones and feature inventory.
  - Engineering rules: English git commit messages, no hard-wrapped code lines, ASD-STE100 text.
  - CI: Run `./scripts/ci.sh` to confirm 100% pass across all 2755+ tests.
- **Verification**: `bash ./scripts/ci.sh`

## Verification and Gate Policy
- Worker verifies code with builds and test executions.
- Two Reviewers review correctness, security, interface conformance, and code formatting.
- Two Challengers test edge cases and error paths.
- Forensic Auditor verifies integrity and checks for cheats or workarounds.
- Gate pass requires strict AND of all reviewer, challenger, and auditor approvals.
