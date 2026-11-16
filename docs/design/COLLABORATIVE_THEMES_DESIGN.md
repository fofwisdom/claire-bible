# Collaborative Themes: One Knowledge Base for Each Account, Shared Themes Under `/themes`

- **Document Identifier**: `DESIGN-COLLABORATIVE-THEMES-20261116`
- **Status**: Draft. Owner decisions CT1 to CT10 are locked (2026-11-16). Section 13 has the open questions.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Related Documents**:
  - [`THEMES.md`](../implementation/THEMES.md) (current theme implementation)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (namespaces, theme numbers, collection contract §7)
  - [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) (RD27 to RD29; this document replaces RD29 rule 1)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (idea I1, theme as a `Group` actor)
  - [`PER_ACCOUNT_FEATURES_DESIGN.md`](PER_ACCOUNT_FEATURES_DESIGN.md) (account backup)
  - [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[unverified]**.

---

## 1. Goal

1. An account has exactly one knowledge database: its **knowledge base** (today: the default theme, `is_default = 1`).
2. A **theme** is a collaborative knowledge database. No account owns it. The instance serves themes under `/themes` with the collection principles of Mastodon Collections (time-ordered numbers, two-level collection, cursor pages; namespace design §7).
3. The server admin gives the **moderator** role of a theme to accounts.

---

## 2. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| All knowledge tables are partitioned by `theme_id`. All accounts use one `claire.db`. | **[code]** `store/db.py`, THEMES.md §2 |
| Each theme has an owner: `themes.account_id`. An account has one default theme (`idx_themes_account_default`) and can make more themes up to the tier limit `max_themes`. | **[code]** `store/db.py`, `api/server.py` |
| `themes.account_id` was added with `ALTER TABLE` and allows NULL. The migration of v15 sets NULL values to the admin (1). | **[code]** `store/db.py` (`_ensure_column(conn, "themes", "account_id", ...)`) |
| Network mode serves themes at `/@{u}/themes/{number}/`. Root `/themes/{number}/` sends a 308 to the owner path. | **[code]** `api/namespace.py`, namespace design §6.2 |
| The `users` and `theme_members` tables (v14) exist, but no code reads or writes them. `theme_members` refers to `users`, not to `accounts`. | **[code]** grep of `src/claire` |
| `documents` has no column for the account that added the document. | **[code]** `store/db.py` |
| `extractions.raw_response` keeps the model output of each extraction. | **[code]** `store/db.py` |
| `data/themes.json` is a copy of the registry. `reload()` reads entries that are only in the JSON file and sets the admin (1) as owner when an entry has no `account_id`. | **[code]** `store/theme.py` (`_save_registry`, `reload`) |
| `federation_objects` has `account_id` (the actor), `theme_id`, and `published_by_account_id`. | **[code]** `store/db.py` |
| The development database has one account and four themes (one default). The production server has one account, the admin. | **[code]** read of `data/claire.db`; owner, 2026-11-16 |

---

## 3. Decisions

| ID | Decision | Source |
| :--- | :--- | :--- |
| CT1 | An account has exactly one knowledge database, its knowledge base. An account cannot make more knowledge databases. | **[decision]** owner, 2026-11-16 |
| CT2 | A theme is a collaborative knowledge database that no account owns. The instance serves it under `/themes/{number}/` in single mode and in network mode. The admin gives and removes the moderator role. | **[decision]** owner, 2026-11-16 |
| CT3 | Each active account can make a theme. | **[decision]** owner, 2026-11-16 |
| CT4 | A document enters a theme only as a **contribution** from the knowledge base of a member. The theme keeps a copy and a record of the contributor. The theme builds its own graph from the copy. Nobody ingests a URL or a file directly into a theme. | **[decision]** owner, 2026-11-16 |
| CT5 | Only members of a theme contribute. Each theme sets its join policy (`open` or `approval`). Moderators hide or remove contributions and remove or ban members after the contribution. Read visibility is a separate setting. | **[decision]** owner, 2026-11-16 |
| CT6 | The migration changes each existing theme that is not a default theme (admin themes included) into a theme. The old owner becomes its first moderator. The number and the URL stay; `/@{u}/themes/{number}/` sends a 308 to `/themes/{number}/`. | **[decision]** owner, 2026-11-16 |

| CT7 | A contribution is a donation to the community of the theme. This is true also when the contributor ingested the document with its own API key. The theme keeps the contribution when the contributor leaves, is banned, or purges its account; an account purge sets the contributor to "deleted account". After a moderator approves a contribution, the contributor cannot withdraw it. | **[decision]** owner, 2026-11-16 (OQ1, approval rule added the same day) |
| CT8 | The founder of a theme becomes its first moderator. The admin can remove the role. | **[decision]** owner, 2026-11-16 (OQ2) |
| CT9 | `/@{u}/themes` is the collection of the themes where `u` is an active member and that the requester can read. | **[decision]** owner, 2026-11-16 (OQ3) |
| CT10 | Background LLM work (watch, refresh queue, expand queue) does not run on themes in v1. | **[decision]** owner, 2026-11-16 (OQ5) |

CT4 and CT5 replace RD29 rule 1 (collaborator ingest after an invitation) of `DATA_STORE_LAYOUT_DESIGN.md` §5.

The production server has only the admin account (owner, 2026-11-16, OQ6). Thus the CT6 migration makes the admin the first moderator of all converted themes.

Proposals in this document that wait for the owner are in Section 13.

---

## 4. Terms

| Term | Meaning | Storage |
| :--- | :--- | :--- |
| Knowledge base | The one knowledge database of an account. All ingest goes here. | `themes` row with `is_default = 1`, `account_id` = the account |
| Theme | A collaborative knowledge database. | `themes` row with `is_default = 0`, `account_id IS NULL` |
| Member | An account with an active membership of a theme. | `theme_memberships.state = 'active'` |
| Moderator | A member with the role `moderator`. | `theme_memberships.role = 'moderator'` |
| Founder | The account that made a theme. A value for the record; it gives no right. | `themes.created_by_account_id` |
| Contribution | A copy of a document of a knowledge base in a theme, with its contributor. | `theme_contributions` and a `documents` row in the theme |

**[proposal]** The code keeps the name `themes` for the partition table and `theme_id` for the partition column. A rename touches about 15 tables and all store functions and gives no function. The UI and the documents use "knowledge base" and "theme" as above.

---

## 5. Roles and Permissions

### 5.1 Matrix of a Theme T

| Principal | Read (T public) | Read (T members only) | Join | Contribute | Moderate T | Give moderator role | Archive T | Delete T |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Anonymous | yes | 404 | 404 | 404 | 404 | 404 | 404 | 404 |
| Active account, not a member | yes | 404 | yes (policy) | 403 | 403 | 403 | 403 | 403 |
| Pending member | yes | 404 | — | 403 | 403 | 403 | 403 | 403 |
| Banned account | yes | 404 | 403 | 403 | 403 | 403 | 403 | 403 |
| Member | yes | yes | — | yes | 403 | 403 | 403 | 403 |
| Moderator | yes | yes | — | yes | yes | 403 | yes | 403 |
| Admin | yes | yes | yes | yes (as a member) | yes | yes | yes | yes |

Rules:

1. One function decides the matrix (today `_theme_accessible` and `_get_theme_ctx`). Routes do not check access on their own.
2. A response that must hide a members-only theme gives the same 404 as for an unknown number (PRINCIPLES C1, THEMES.md §3).
3. **[proposal]** The admin reads all themes, members-only themes included, because the admin moderates all themes. The admin does not read the knowledge base of another account; the current rule for knowledge bases stays.
4. "Moderate T" means: decide join requests, remove and ban members, approve, hide, and remove contributions, change the label, the description, the visibility, and the join policy, and run graph maintenance (`/dedup/*`, `/resolution/*`, `/entity/primary-label`).

### 5.2 Credential Scopes

The credential scopes `owner`, `collaborator`, and `readonly` (namespace design §8.2) stay delegations inside one account.

| Scope | Knowledge base of the account | Themes where the account is a member |
| :--- | :--- | :--- |
| `owner` | read and write | all rights of the account in T |
| `collaborator` | read | **[proposal]** read and contribute; no moderation, no join, no leave |
| `readonly` | read | read |

With this rule, `themes.is_collaborator_accessible` has no reader (Section 9.4).

### 5.3 Knowledge Base

Only the account writes to its knowledge base. `is_public` of the knowledge base controls anonymous read, as today. Moderators have no right on a knowledge base.

---

## 6. Paths

| Path | Single mode | Network mode |
| :--- | :--- | :--- |
| `/` | Knowledge base of the admin | Public directory (unchanged) |
| `/@{u}/` | 404 | Knowledge base of `u` |
| `/themes` | Collection of the themes that the requester can read | Same |
| `/themes/{number}/…` | Theme (pinned) | Theme (pinned) |
| `/themes/{number}/…` where the number is a knowledge base | 308 to `/…` | 308 to `/@{u}/…` when the requester can read it, else 404 |
| `/@{u}/themes/{number}/…` (old links) | — | Theme: 308 to `/themes/{number}/…`. Knowledge base of `u`: 308 to `/@{u}/…`. Else 404 |
| `/@{u}/themes` | — | Collection of the themes where `u` is an active member and that the requester can read (CT9) |

Rules:

1. Root `/themes/{number}/` pins the theme with no namespace account (`namespace_account_id = None`, `pinned_theme_id` = the theme). This changes N2' of the namespace design §6.3.
2. `/themes` uses the collection contract of the namespace design §7.4 and the cache rules of §7.5. Anonymous requests see public themes; authenticated requests also see members-only themes where the account is a member. The filter `?joined=true` gives only the themes where the requester is an active member.
3. Each item of the collection has `number`, `label`, `description`, `icon`, `visibility`, `join_policy`, `state`, `members` (count of active members), `contributions` (count of visible contributions), and `url`.
4. Theme numbers stay unique in the instance (`idx_themes_number`). A converted theme keeps its number (CT6).

---

## 7. Contribution

### 7.1 Flow

1. A member opens a document of its knowledge base and selects "Contribute to a theme". The list shows the active themes where the account is an active member.
2. `POST /themes/{number}/contributions` with `{"document": "<public reference of the source document>"}`.
3. Checks, in this order:
   1. The principal can contribute to T (Section 5).
   2. T has the state `active`.
   3. The source document is in the knowledge base of the account of the principal.
   4. T has no visible or hidden contribution with the same `canonical_url` or the same `content_hash`. When it has one, the response is 409 with the URL of that contribution. The server makes no copy.
   5. T has no tombstone (`purged_tombstones`) for the same `canonical_url`. When it has one, the response is 409 (a moderator removed this source).
4. One transaction in `claire.db`:
   1. Insert a copy of the `documents` row with a new document ID and `theme_id` = T.
   2. Copy the latest `extractions` row of the source document to T.
   3. Insert a `theme_contributions` row with the state `merging`.
   4. Insert a `jobs` row of the kind `contribution_merge` in T.
5. The `contribution_merge` job builds the graph of T from the copied extraction (entity resolution, relations, embeddings) without a new LLM extraction. When the source document has no extraction, the job runs a normal extraction. The job uses the provider context of the contributor.
6. When the job completes, the contribution gets the state `visible`. When it fails, it gets the state `failed`; the contributor or a moderator can try again. The recovery loop treats this job like other jobs.

**[unverified]** The current pipeline can build the graph from a stored `raw_response` without a new LLM call. Unit CT-4 checks this first. When it cannot, the job runs a normal extraction with the provider context of the contributor.

**[unverified]** Files in `data/raw` and `data/images` are keyed by document ID (`store/raw.py`). Unit CT-4 decides if the copy copies the files or keeps a reference to the source files. A reference must not break when the contributor deletes the source document.

### 7.2 Rules

1. The copy is independent. A later change or deletion of the source document does not change the contribution. **[proposal]** An explicit update of a contribution is a later change (Section 13, OQ7).
2. Contributions are visible after the merge job. No moderator approval is necessary before (CT5). Approval does not control visibility; it accepts the donation for the community (CT7).
3. A contributor can withdraw its own contribution only while no moderator has approved it (`approved_at IS NULL`). A withdrawal removes the contribution like a moderator removal, but makes no tombstone. After the approval, a withdrawal request gets 409 with the code `contribution_approved`; the contributor can ask a moderator to remove it.
4. A contribution is a donation to the community of T (CT7). When its contributor leaves, is banned, or purges its account, the contribution stays in T. Only a moderator removal or a withdrawal of the contributor (rule 3) removes it.
5. Moderator actions:
   - **Approve**: sets `approved_at` and `approved_by_account_id`. A moderator can approve a contribution with the state `visible` or `hidden`. An approval is final: no route clears it. A moderator removal of an approved contribution stays possible.
   - **Hide**: `documents.hidden = 1` and state `hidden`. Readers and search do not show the document. The action is reversible.
   - **Remove**: the existing document purge in T (graph sources, embeddings, FTS), a tombstone for the `canonical_url`, and deletion of the `theme_contributions` row.
   - Each action writes a row in `theme_moderation_log`.
6. Ingest limits of an account (RD29 rule 2) count the LLM use of a contribution for the contributor. The LLM cost that the contributor paid with its own API key, for the first ingest or for the merge job, is part of the donation (CT7). The theme does not give it back.

---

## 8. Membership and Moderation

### 8.1 Theme Lifecycle

| Action | Who | Effect |
| :--- | :--- | :--- |
| Create (`POST /themes`) | Each active account (CT3), up to its limit | Shared theme, state `active`, join policy `approval`, visibility public. The founder becomes the first moderator (CT8). |
| Change metadata (`PATCH /themes/{number}`) | Moderator, admin | Label, description, icon, visibility, join policy |
| Archive | Moderator, admin | State `archived`: read-only. No join, no contribution, no merge job. |
| Unarchive | Moderator, admin | State `active` |
| Delete (`DELETE /themes/{number}`) | Admin only | Same as `delete_theme(purge=True)` today. Dry-run first (PRINCIPLES D2, D3). |

**[proposal]** The tier limit `max_themes` becomes the number of active themes that an account founded (`created_by_account_id`, state `active`). The knowledge base is not counted.

Labels of themes need not be unique. The number is the identifier. The old rule `(account_id, label)` applies to nothing after CT1 and is removed. With this rule, the migration does not change labels.

`CLAIRE_MULTI_THEME=0` keeps its meaning: it locks `POST /themes`.

### 8.2 Join and Leave

| Route | Who | Effect |
| :--- | :--- | :--- |
| `POST /themes/{number}/membership` | Active account | `open`: active member. `approval`: pending member. Banned: 403. Archived theme: 403. |
| `DELETE /themes/{number}/membership` | Member or pending member | Leave. The last moderator cannot leave (409); the admin must give the role to another member first. |
| `GET /themes/{number}/members` | Member, moderator, admin | Collection (contract §7.4) of active members. Moderators and the admin also get pending and banned entries. |
| `PATCH /themes/{number}/members/{account}` | Moderator, admin | `{"state": "active" \| "banned"}` (approve or ban), or delete the membership (remove). A moderator cannot change the role of another moderator. |
| `PUT /api/admin/themes/{number}/moderators/{account}` | Admin | Give the moderator role. The account becomes an active member when it is not one. |
| `DELETE /api/admin/themes/{number}/moderators/{account}` | Admin | Remove the moderator role. The account stays a member. |

When a theme has no moderator (for example after an account purge, or after the admin removes the role of the founder), the admin is the only moderator. The admin console shows these themes first.

### 8.3 Console

| Page | Who | Content |
| :--- | :--- | :--- |
| `/settings/themes` | Account | The themes where the account is a member or a pending member, the themes it founded and the founder limit, the button to make a theme, and the link to `/themes` |
| `/settings/themes/{number}` | Moderator | Members, join requests, contributions with approve, hide, and remove (contributions that wait for approval first), the moderation log, theme settings |
| `/admin/themes` | Admin | All themes with counts, themes without a moderator, the moderator assignment |

New UI text uses keys of the English and Korean catalogs from the start (I18N design).

---

## 9. Data Model: `claire.db` Version 25

### 9.1 `themes`

The table keeps its columns. The meaning of two columns becomes strict:

| Kind | `is_default` | `account_id` |
| :--- | :---: | :--- |
| Knowledge base | 1 | the account (NOT NULL) |
| Theme | 0 | NULL |

New columns:

```sql
ALTER TABLE themes ADD COLUMN created_by_account_id INTEGER;            -- value, no foreign key; NULL after an account purge
ALTER TABLE themes ADD COLUMN join_policy TEXT NOT NULL DEFAULT 'approval'; -- 'open' | 'approval'
ALTER TABLE themes ADD COLUMN state TEXT NOT NULL DEFAULT 'active';        -- 'active' | 'archived'
ALTER TABLE themes ADD COLUMN archived_at REAL;
```

Visibility stays in `is_public` (1: public, 0: members only).

The store functions check the two kinds of the table above (SQLite cannot add a `CHECK` constraint with `ALTER TABLE`). A test checks that no row has `is_default = 1 AND account_id IS NULL` or `is_default = 0 AND account_id IS NOT NULL`.

### 9.2 New Tables

```sql
CREATE TABLE IF NOT EXISTS theme_memberships (
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    public_id INTEGER UNIQUE NOT NULL,          -- time-ordered number, collection cursor
    role TEXT NOT NULL CHECK (role IN ('member', 'moderator')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'active', 'banned')),
    requested_at REAL NOT NULL,
    decided_at REAL,
    decided_by_account_id INTEGER,              -- value
    PRIMARY KEY (theme_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_theme_memberships_account ON theme_memberships(account_id, state);

CREATE TABLE IF NOT EXISTS theme_contributions (
    public_id INTEGER PRIMARY KEY,              -- time-ordered number, collection cursor
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,                  -- the copy in the theme
    contributor_account_id INTEGER,             -- value; NULL after an account purge ("deleted account", CT7)
    source_document_id TEXT,                    -- value; the document of the knowledge base at contribution time
    state TEXT NOT NULL CHECK (state IN ('merging', 'visible', 'hidden', 'failed')),
    contributed_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    approved_at REAL,                           -- NULL: the contributor can withdraw (CT7)
    approved_by_account_id INTEGER,             -- value
    UNIQUE (theme_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_theme_contributions_theme ON theme_contributions(theme_id, public_id);
CREATE INDEX IF NOT EXISTS idx_theme_contributions_contributor ON theme_contributions(contributor_account_id);

CREATE TABLE IF NOT EXISTS theme_moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    actor_account_id INTEGER,                   -- value
    action TEXT NOT NULL,                       -- 'approve_member', 'ban_member', 'remove_member', 'approve_contribution', 'hide_contribution', 'unhide_contribution', 'remove_contribution', 'grant_moderator', 'revoke_moderator', 'archive', 'unarchive', 'update_settings'
    target TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_theme_moderation_log_theme ON theme_moderation_log(theme_id, id);
```

Store boundary (RD27 R2): a new module `store/theme_membership.py` reads and writes these three tables. No SQL of another module reads them.

### 9.3 Removed Schema

| Object | Reason |
| :--- | :--- |
| Table `theme_members` | No reader; refers to `users`; replaced by `theme_memberships` |
| Table `users` | No reader since v15 (`accounts` replaced it) |

The migration uses the operator rule: when a table to drop has rows, the migration renames it to `<name>_retired_v25`, writes a warning, and continues. Account purge and instance purge include the renamed tables.

### 9.4 Candidates for Later Removal

| Object | Condition |
| :--- | :--- |
| `themes.is_collaborator_accessible` | No reader after Section 5.2 |
| `themes.db_path`, `themes.fqdn` | **[unverified]** Readers in `graph_bundle.py`, `cli.py` (`import-graph`), and `namespace.py` legacy hosts. Remove when no reader stays. |
| `data/themes.json` | The DB is the canonical registry. The JSON copy fills `account_id = 1` for entries without an owner, and that is wrong for themes. CT-1 stops `reload()` from reading entries that are only in the JSON file, then stops writing the file. |

---

## 10. Migration (Version 25)

The migration completes on its own and keeps all data (operator rule). A second start changes nothing.

1. Add the columns and tables of Section 9.
2. For each `themes` row with `is_default = 0` and `account_id IS NOT NULL`:
   1. `created_by_account_id = account_id`, `account_id = NULL`, `join_policy = 'approval'`, `state = 'active'`.
   2. When the old owner account has the state `active` or `suspended`, insert a membership `(role 'moderator', state 'active')`. When the account has the state `deleting` or `purged`, insert nothing; the admin moderates the theme (Section 8.2).
   3. For each `documents` row of the theme, insert a `theme_contributions` row with `contributor_account_id` = the old owner, `source_document_id = NULL`, the state `hidden` when `documents.hidden = 1`, else `visible`, and `approved_at` = the time of the migration with `approved_by_account_id = NULL`. The old owner made these documents in the theme; they are accepted donations from the start.
3. Drop or rename `theme_members` and `users` (Section 9.3).
4. In one transaction for each theme. When a theme fails, the migration writes a warning, leaves that theme as it was, and tries again at the next start. The code treats a theme that is not converted (`is_default = 0 AND account_id IS NOT NULL`) as a theme with that account as moderator until the conversion completes.

Effects of CT6 on links:

- Old links `/@{u}/themes/{number}/…` of converted themes get a 308 to `/themes/{number}/…` (Section 6).
- `federation_objects` rows of converted themes stay. The account actor of the old owner keeps them (Section 11.3).

---

## 11. Effects on Other Subsystems

### 11.1 Ingest

1. All ingest entry points target the knowledge base of the account: HTTP `/ingest*`, CLI, Telegram, feeds, the ingest of a federation item, MCP tools.
2. An ingest request pinned to a theme (`/themes/{number}/ingest`) gets 403 with the code `theme_ingest_by_contribution` and a hint to contribute from the knowledge base.
3. Background LLM work does not run on themes in v1 (CT10): no watch, no refresh queue, no expand queue. A theme has no account that pays for it. A moderator can start maintenance (re-extract, regenerate) by hand with its own provider context. The workers skip theme rows; the converted themes lose their watch and queue entries at the CT6 migration (the migration sets `watch_enabled = 0` and deletes the queue rows of themes).

### 11.2 Account Purge and Backup

| Data | Effect of an account purge |
| :--- | :--- |
| Knowledge base | Deleted (today's behavior) |
| Memberships | Deleted (`ON DELETE CASCADE`) |
| Contributions | Kept in the themes; `contributor_account_id` set to NULL ("deleted account", CT7) |
| `themes.created_by_account_id` | Set to NULL |
| `theme_moderation_log.actor_account_id` | Kept as a value |

- An account backup has only the knowledge base. The instance backup includes themes.
- Restore of a backup made before version 25: the restore restores the knowledge base and skips the themes in the archive. The result lists the skipped themes. It does not stop.
- A data bundle (`/graph/bundle`) of a theme: moderators and the admin.

### 11.3 Federation

1. The account actor publishes only documents of its knowledge base.
2. Publish from a theme is refused until idea I1 (theme as a `Group` actor) has a design. A collaborative theme is a natural candidate for I1; this document does not design it.
3. Published objects of converted themes stay with their actor. When a moderator removes such a document, the publish outbox sends a `Delete`, as for a document purge today.

### 11.4 Telegram, MCP, and CLI

- Telegram: ingest goes to the knowledge base. `/theme` selects the knowledge base or a theme where the account is a member, for search and read. A contribution command is a later change.
- MCP: read tools accept themes where the account is a member and public themes. Write tools accept only the knowledge base.
- CLI: `claire theme` lists and manages themes for the admin. CLI text is English (ASD-STE100).

### 11.5 Search

Search across the knowledge base and the themes of an account is not in this design. Search stays in one partition.

---

## 12. Security

| ID | Risk | Control |
| :--- | :--- | :--- |
| CS1 | A request reads a members-only theme through a path that skips the matrix | One function decides access (Section 5.1). Tests call each `namespaced` route with a non-member. |
| CS2 | A contribution copies a document of another account | The source check uses the account of the principal and the knowledge base of that account only (Section 7.1, step 3.3). |
| CS3 | A banned account comes back with a new join request | `state = 'banned'` stays until a moderator removes it. Join gives 403. |
| CS4 | A moderator gives itself more rights | Only the admin gives or removes the moderator role. A moderator cannot change another moderator. |
| CS5 | Spam themes | Founder limit (Section 8.1). The admin archives or deletes themes. |
| CS6 | A members-only theme is in a shared cache | Collection and theme responses use the cache rules of namespace design §7.5 (`private, no-cache` for authenticated responses). |

---

## 13. Open Questions

Answered on 2026-11-16: OQ1 (CT7), OQ2 (CT8), OQ3 (CT9), OQ5 (CT10), OQ6 (Section 3).

| ID | Question | Proposal |
| :--- | :--- | :--- |
| OQ4 | Is a third visibility, "accounts of this instance only", necessary? | No for v1. Public and members only. |
| OQ7 | Can a contributor update a contribution when the source document changes? | Later change. v1 keeps a snapshot. |
| OQ8 | Can a `collaborator` credential contribute? | Yes (Section 5.2). |
| OQ9 | Does the code keep the names `themes` and `theme_id` for the partition (Section 4)? | Yes. A rename gives no function. |
| OQ10 | Does the admin read members-only themes (Section 5.1, rule 3)? | Yes, to moderate them. Knowledge bases of other accounts stay hidden. |
| OQ11 | Does the tier limit `max_themes` count the active themes that an account founded (Section 8.1)? | Yes. The knowledge base is not counted. |

---

## 14. Work

| Unit | Scope | Completion criteria |
| :--- | :--- | :--- |
| CT-1 | `claire.db` version 25 (Section 9), migration (Section 10), `store/theme_membership.py`, `ThemeManager` without `(account_id, label)` uniqueness, `reload()` without JSON-only entries | A version 24 database with four themes starts as version 25 with one knowledge base, three themes, the old owner as moderator, and one contribution for each document. A second start changes nothing. |
| CT-2 | Access matrix (Section 5), namespace paths and redirects (Section 6), `/themes` collection | Non-members get 404 for members-only themes on each `namespaced` route. Old `/@{u}/themes/{number}/` links get 308. |
| CT-3 | Membership and moderation routes (Section 8.2), admin moderator routes, moderation log | Join with `open` and `approval`, approve, ban, leave, last-moderator rule, admin role assignment |
| CT-4 | Contribution route and `contribution_merge` job (Section 7), approve, hide, remove, withdraw | A document of a knowledge base appears in the theme graph without a new LLM extraction when an extraction exists. A duplicate gets 409. A withdrawal after the approval gets 409. |
| CT-5 | Ingest entry points (Section 11.1), workers without theme rows (CT10), Telegram, MCP, CLI, account purge and backup (Section 11.2), federation publish rule (Section 11.3) | A pinned ingest to a theme gets 403. The watch, refresh, and expand workers take no theme row. Account purge removes memberships and keeps contributions with a NULL contributor. |
| CT-6 | Console pages (Section 8.3) with English and Korean catalog keys | E2E test: account A makes a theme, account B joins, A approves, B contributes a document, A hides it, the admin gives B the moderator role. |
| CT-7 | Implementation documents: `THEMES.md`, `SCHEMA_VERSIONING.md`, `ACCOUNTS.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml` | The documents describe the code after CT-1 to CT-6. This design document is removed after the implementation. |
