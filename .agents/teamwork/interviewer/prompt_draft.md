Execute sealing and cleanup tasks in Claire Bible to prepare for account, IdP, and Fedify MIT sidecar integration.

Working directory: /home/fow/Projects/claire-bible
Integrity mode: development

## Requirements

### R1. `profile_public` Toggle and Account Contract Seal
Support the `profile_public` toggle (0 or 1) in `PATCH /api/me` and `/settings/account`, sealing the canonical account contract.

### R2. Reserved Usernames Synchronization
Add Fedify federation and IdP-related reserved words (`inbox`, `outbox`, `actor`, `actors`, `ap`, `fedify`, `federation`, `nodeinfo`, `oauth`, `auth`, `followers`, `following`, `shared_inbox`, etc.) to `RESERVED_USERNAMES` in `src/claire/accounts/username_policy.py`, and synchronize tests (`tests/test_username_policy.py`, `tests/test_route_surface_contract.py`, etc.).

### R3. Token Exchange Account Binding Integrity
Enforce account binding integrity for `exchange_account_id` in RFC 8693 Token Exchange (`urn:ietf:params:oauth:grant-type:token-exchange`) within `src/claire/api/oauth.py`, blocking fallbacks to admin account ID 1.

### R4. `PROJECT.md` Status Update and Hygiene
Clean up and update remaining items from the previous Admin-Settings P2 milestone in `PROJECT.md`.

### R5. Syndication Prototype Sealing and Cleanup
Cleanly seal or clean up legacy seed-based syndication prototype elements (`/prototype/syndication` and seed components) without breaking existing tests, leaving the approach to the team's judgment.

### R6. Engineering Rules
- Use ASD-STE100 for all non-user communication.
- Write Git commit messages in English only.
- Do not hard-wrap code lines.
- Follow test policy: unit tests for module interfaces, I/O, and external calls; at least one E2E test if new features are added.

## Acceptance Criteria

### Account and Security Contracts
- [ ] `PATCH /api/me` accepts `profile_public` toggle (0 or 1) and updates the canonical account state.
- [ ] `/settings/account` provides the `profile_public` toggle control corresponding to the backend contract.
- [ ] `RESERVED_USERNAMES` in `src/claire/accounts/username_policy.py` contains all specified Fedify/IdP reserved words, and route surface/username policy tests pass.
- [ ] RFC 8693 Token Exchange in `src/claire/api/oauth.py` strictly prevents unauthorized fallback to admin account ID 1.

### Documentation and Hygiene
- [ ] `PROJECT.md` reflects current status with Admin-Settings P2 remnants resolved.
- [ ] Legacy seed-based syndication prototype elements are sealed or cleaned up without test regressions.
- [ ] Git commit messages are written in English only, and code lines are not hard-wrapped.
- [ ] Unit tests for module interfaces/IO/external calls and E2E coverage meet repository rules.
- [ ] Full test suite and `./scripts/ci.sh` pass cleanly.
