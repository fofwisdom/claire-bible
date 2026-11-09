# Challenge Report — Milestone M1 (Requirement R2)

## Challenge Summary

**Overall risk assessment**: LOW
**Verdict**: APPROVE

Requirement R2 synchronizes `RESERVED_USERNAMES` in `src/claire/accounts/username_policy.py` with Fedify federation and IdP reserved words.
Empirical verification confirms that all 21 Fedify and IdP terms exist in `RESERVED_USERNAMES`.
All exact terms and compound usernames are rejected correctly.
Valid non-reserved usernames are not falsely rejected.

## Challenges

### [Low] Challenge 1: Fragment Splitting on Multi-Word Reserved Tokens (`well_known`)

- **Assumption challenged**: Fragment splitting rejects all compound usernames that contain any reserved term.
- **Attack scenario**: A user tries to register `well_known_hub`. `_FRAGMENT_SPLIT_RE` splits on underscores. The resulting fragments are `well`, `known`, and `hub`. Neither `well` nor `known` is in `RESERVED_USERNAMES`. Therefore, `well_known_hub` is accepted, while `wellknown_hub` is rejected.
- **Blast radius**: Low. The exact identifier `well_known` is blocked. RFC 5785 uses `/.well-known/` with dot and hyphen characters. The grammar already rejects dots and hyphens. Thus, route collision does not occur.
- **Mitigation**: If administrators want to block `well_known_*` variants, add `well` to `RESERVED_USERNAMES` or check full names before underscore splitting. No immediate fix is necessary.

### [Low] Challenge 2: Length Boundary and Check Order for Two-Character Reserved Terms (`ap`, `me`)

- **Assumption challenged**: Two-character terms in `RESERVED_USERNAMES` return `UsernameRejected("reserved")`.
- **Attack scenario**: An attacker tries to register `ap`. The validation function executes syntax validation before reserved list lookup. Because minimum length is 3 characters, `validate_username("ap")` raises `UsernameRejected("syntax")`, not `reserved`.
- **Blast radius**: None. The system safely blocks registration. In compound names (`ap_node`, `node_ap`, `ap42`), syntax passes and fragment validation correctly raises `UsernameRejected("reserved")`.
- **Mitigation**: None needed. The behavior matches design section 8.1 specification order (syntax before reserved).

### [Low] Challenge 3: Risk of False Rejections for Words Containing Reserved Substrings

- **Assumption challenged**: Adding short reserved words (`ap`, `auth`, `actor`, `inbox`) causes false rejections of valid words (`apple`, `author`, `reactor`, `spinbox`).
- **Attack scenario**: A legitimate user attempts to register normal names like `apple`, `author`, `reactor`, `spinbox`, `kim_lee`.
- **Blast radius**: None. `username_fragments` splits only on underscores and digits (`[_0-9]+`). It does not split substrings within alphabetic sequences.
- **Mitigation**: Retain `_FRAGMENT_SPLIT_RE = re.compile(r"[_0-9]+")`.

## Stress Test Results

- Exact Fedify/IdP terms (`inbox`, `outbox`, `actor`, `oauth`, `auth`, `fedify`, `shared_inbox`, `activitypub`, etc.) → rejected → actual: `UsernameRejected("reserved")` → PASS
- Short exact terms (`ap`, `me`) → rejected by syntax (< 3 characters) → actual: `UsernameRejected("syntax")` → PASS
- Compound prefix (`inbox_123`, `actor_admin`, `oauth_service`) → rejected → actual: `UsernameRejected("reserved")` → PASS
- Compound suffix (`my_oauth_app`, `node_ap`, `hub_fedify`) → rejected → actual: `UsernameRejected("reserved")` → PASS
- Compound numeric (`inbox42`, `actor1`, `fedify99`) → rejected → actual: `UsernameRejected("reserved")` → PASS
- Case insensitivity (`INBOX`, `My_OAuth_App`, `Actor_Admin`) → normalized to lowercase and rejected → actual: `UsernameRejected("reserved")` → PASS
- Non-ASCII homoglyphs (`аdmin`, `Kim`) → rejected before normalization → actual: `UsernameRejected("syntax")` → PASS
- Valid words with reserved substrings (`apple`, `author`, `reactor`, `spinbox`, `robot`, `allison`, `memo`) → accepted → actual: accepted → PASS
- Valid personal usernames (`kim`, `lee`, `park`, `kim_lee`, `alex_smith`) → accepted → actual: accepted → PASS
- Maximum length 30 characters with reserved fragment (`a` * 24 + `_inbox`) → rejected → actual: `UsernameRejected("reserved")` → PASS
- Maximum length 31 characters (`a` * 25 + `_inbox`) → rejected by syntax → actual: `UsernameRejected("syntax")` → PASS
- AccountStore registration integration (`store.validate_username`, `store.create_member`) → rejected on reserved terms → actual: `UsernameRejected("reserved")` → PASS
- Route surface contract suite (`tests/test_route_surface_contract.py`) → 14 routes pass without regression → actual: 14 passed → PASS
- Account routes suite (`tests/test_account_routes.py`, `tests/test_settings_account.py`) → all routes pass → actual: 38 passed → PASS

## Unchallenged Areas

- Fedify runtime sidecar network traffic: Out of scope for Milestone M1 (sealed at policy layer).
