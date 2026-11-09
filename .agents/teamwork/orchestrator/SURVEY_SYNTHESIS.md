# Survey Synthesis: Account, IdP, and Fedify Preparation

## 1. Consensus Findings

### Requirement R1: `profile_public` Toggle and Account Contract Seal
- The `accounts` database table already contains the column `profile_public INTEGER NOT NULL DEFAULT 0`.
- The `Account` model in `src/claire/store/accounts.py` defines `profile_public: bool`.
- `AccountStore` in `src/claire/store/accounts.py` needs a method to update `profile_public`.
- `PATCH /api/me` in `src/claire/api/accounts_routes/me.py` currently permits only `display_name`. It must accept `profile_public` (0 or 1) and return the updated account in `me_payload`.
- `me_payload` must include `"profile_public": 1 if account.profile_public else 0`.
- `/settings/account` in `src/claire/api/accounts_routes/console.py` must remove the obsolete hint note and render an interactive switch control in network mode. Rule S9 (hide control in single mode) must remain intact.
- `src/claire/static/js/console_account.js` must submit `PATCH /api/me` with `{"profile_public": checked ? 1 : 0}` when the toggle changes.
- Existing tests in `tests/test_account_routes.py` must be updated to expect `profile_public` in `GET /api/me` response.

### Requirement R2: Reserved Usernames Synchronization
- `RESERVED_USERNAMES` in `src/claire/accounts/username_policy.py` must add Fedify and IdP reserved words:
  `inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, plus recommended related words (`sharedinbox`, `webfinger`, `idp`, `oidc`, `openid`, `activitypub`, `wellknown`, `well_known`).
- `tests/test_username_policy.py` must update `SPEC_RESERVED` to match.
- Zero conflicts exist with existing routes or tests.

### Requirement R3: Token Exchange Account Binding Integrity
- In `src/claire/api/oauth.py` (lines 884-930), RFC 8693 Token Exchange contains an unauthenticated fallback branch (lines 906-921).
- In this branch, `exchange_account_id` remains `None`. Because `dbm.create_oauth_token` uses default `ADMIN_ACCOUNT_ID = 1`, unbound tokens receive admin credentials.
- Fix: Remove lines 906-921. Require `binding is not None` and `binding.account_id is not None`. Return HTTP 400 `invalid_grant` with description `"Subject token is invalid, expired, or unbound"`. Pass explicit `account_id=exchange_account_id` to token creation.
- Add comprehensive automated tests in `tests/test_oauth_token_exchange.py`.

### Requirement R4: `PROJECT.md` Status Update and Hygiene
- Phase P2 is complete in code but marked `IN_PROGRESS` and `PLANNED` in `PROJECT.md`.
- Mark Phase P2 milestones as complete.
- Retitle `PROJECT.md` and define current milestones for Requirements R1 through R6.

### Requirement R5: Syndication Prototype Sealing and Cleanup
- Five existing test suites assert the `/prototype/syndication` route and its mock factories.
- Deleting the prototype breaks existing test suites.
- Resolution: Keep `/prototype/syndication` as a sealed reference prototype with authentication (`_system("read")`). Keep live `/syndication` clean and independent of prototype seeds. Ensure all 5 test files remain passing.

### Requirement R6: Baseline CI and Engineering Rules
- CI script `scripts/ci.sh` runs bash syntax checks, docker compose config checks, volume assertions, `uv lock --check`, and `pytest -q`.
- Deterministic baseline failure in `tests/test_security_hotfixes.py` line 291: regex does not match `function esc(s, quote)` introduced in `adoc_parser.js`. Fix regex pattern.
- Flake notice: `tests/test_e2e_presentation_resilience.py` has a 100ms threshold that can fail under heavy CPU load during the 5-minute full suite. It passes in isolation.

## 2. Interface Contracts
- `PATCH /api/me`: Accepts JSON `{"profile_public": 0|1}` and/or `{"display_name": string}`. Returns HTTP 200 with complete `me_payload` containing `profile_public: 0|1`.
- `POST /oauth/token` (grant_type `urn:ietf:params:oauth:grant-type:token-exchange`): Requires valid session binding. Rejects unbound tokens with HTTP 400 `invalid_grant`. Never issues tokens bound to admin account ID 1 unless subject token belongs to admin.
- `username_policy.py`: Rejects usernames containing Fedify/IdP reserved words.

## 3. Milestone Decomposition
- **Milestone 1 (M1)**: Account & Username Surface Contracts (R1 & R2)
- **Milestone 2 (M2)**: OAuth Token Exchange Integrity & Syndication Prototype Sealing (R3 & R5)
- **Milestone 3 (M3)**: Hygiene, Baseline Hotfix, PROJECT.md Update & CI Verification (R4 & R6)
