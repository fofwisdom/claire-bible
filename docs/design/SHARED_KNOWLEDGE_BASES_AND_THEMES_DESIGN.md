# Shared Knowledge Bases and Themes: One Personal Knowledge Base for Each Account, Collaborative Knowledge Bases, and Themes as Collections

- **Document Identifier**: `DESIGN-SHARED-KB-AND-THEMES-20261116`
- **Status**: Draft, Rev. 2. Owner decisions CT1 to CT16 are locked (2026-11-16). Section 15 has the open questions.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Revisions**:
  - Rev. 1 (2026-11-16, file `COLLABORATIVE_THEMES_DESIGN.md`): a theme was one collaborative database.
  - Rev. 2 (2026-11-16): a **theme** is a collection of knowledge bases with the same subject. The collaborative database is a **shared knowledge base**. The registry table becomes `knowledge_bases` and the partition column becomes `kb_id` (CT16). New: subject judgement, root page listing, bulk contribution, curation, fork, contribution updates.
- **Related Documents**:
  - [`THEMES.md`](../implementation/THEMES.md) (current implementation of the partition)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (namespaces, numbers, collection contract §7, embedding space §10.4)
  - [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) (RD27 to RD29; this document replaces RD29 rule 1)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (idea I1, `Group` actor)
  - [`PER_ACCOUNT_FEATURES_DESIGN.md`](PER_ACCOUNT_FEATURES_DESIGN.md) (account backup)
  - [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[unverified]**.

---

## 1. Goal

1. An account has exactly one knowledge database: its **personal knowledge base**.
2. A **shared knowledge base** is a collaborative knowledge database. No account owns it. The admin gives the **moderator** role.
3. Different accounts can make shared knowledge bases about the same subject. The server finds knowledge bases with the same subject from their entities and groups them in a **theme**. A theme is a collection of knowledge bases, served under `/themes` with the principles of Mastodon Collections. The root page lists themes so that people find the knowledge bases.
4. Moderators curate from other knowledge bases of the same theme. An account contributes to all shared knowledge bases of a theme in one step. Knowledge bases can be forked.

---

## 2. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| All knowledge tables are partitioned by `theme_id`. All accounts use one `claire.db`. | **[code]** `store/db.py`, THEMES.md §2 |
| `theme_id` occurs about 1810 times in 51 Python modules. `entities_fts` has no partition column. `client.db` has `linked_theme_id` (value). | **[code]** grep of `src/claire` |
| Each theme has an owner (`themes.account_id`). An account has one default theme (`idx_themes_account_default`) and can make more up to the tier limit `max_themes` (default 5). | **[code]** `store/db.py`, `api/server.py`; namespace design §10.3 |
| `themes.account_id` allows NULL (added with `ALTER TABLE`). | **[code]** `store/db.py` |
| Network mode serves themes at `/@{u}/themes/{number}/`. Root `/themes/{number}/` sends a 308 to the owner path. | **[code]** `api/namespace.py` |
| Numbers come from `timestamp_id(table, ...)`: `(unix_ms << 16) | low16`, where `low16` depends on the table name. Two tables can give the same number in theory. | **[code]** `ids.py` |
| The embedding space is fixed for each theme (`embed_provider`, `embed_model`); search reads only vectors of that model. | namespace design §10.4 |
| The tables `users` and `theme_members` (v14) have no reader and no writer. | **[code]** grep of `src/claire` |
| `documents` has no column for the account that added the document. `extractions.raw_response` keeps the model output. | **[code]** `store/db.py` |
| `reload()` reads entries that are only in `data/themes.json` and gives them the admin (1) as owner. | **[code]** `store/theme.py` |
| The production server has one account, the admin. The development database has one account and four themes (one default). | owner, 2026-11-16; read of `data/claire.db` |

---

## 3. Decisions

| ID | Decision |
| :--- | :--- |
| CT1 | An account has exactly one personal knowledge base. It cannot make more personal knowledge bases. |
| CT2 | A shared knowledge base is a collaborative knowledge database that no account owns. It is served at `/kb/{number}/` in single mode and in network mode. The admin gives and removes the moderator role. |
| CT3 | Each active account can make shared knowledge bases. There is no limit on the number (OQ11). |
| CT4 | A document enters a shared knowledge base only as a contribution from a personal knowledge base, as a curation, or as a fork copy. Nobody ingests a URL or a file directly into a shared knowledge base. |
| CT5 | Only members contribute directly to a shared knowledge base; a direct contribution is visible after the merge job. Each shared knowledge base sets its join policy (`open`, `approval`). All shared knowledge bases and all themes are public: there are no members-only knowledge bases (OQ10) and no visibility "accounts of this server only" (OQ4). |
| CT6 | The migration changes each existing theme that is not a default theme (admin themes included) into a shared knowledge base. The old owner becomes its first moderator. The number stays. Old links get a 308 to `/kb/{number}/`. |
| CT7 | A contribution is a donation to the community of the shared knowledge base, also when the contributor ingested the document with its own API key. The contribution stays when the contributor leaves, is banned, or purges its account; an account purge sets the contributor to "deleted account". After a moderator approves a contribution, the contributor cannot withdraw it. |
| CT8 | The founder of a shared knowledge base becomes its first moderator. The admin can remove the role. |
| CT9 | `/@{u}/kb` is the collection of the shared knowledge bases where `u` is an active member. (Rev. 1 used `/@{u}/themes` for this; Rev. 2 moves it because a theme is now a collection of knowledge bases.) |
| CT10 | Watch, refresh, and expand run only in personal knowledge bases. A contributor follows changes in its personal knowledge base and sends them to shared knowledge bases as contribution updates (OQ7). Shared knowledge bases do not run these workers. |
| CT11 | A theme is a collection of knowledge bases with the same subject. It is served at `/themes` and `/themes/{number}` with the collection contract (namespace design §7.4). The root page lists themes. |
| CT12 | A knowledge type (`real` or `fictional`) is selected at creation. The server judges subjects only among knowledge bases of the same type, publishes the result automatically, and the admin adjusts it. |
| CT13 | An account selects a theme, not a knowledge base, and contributes to all shared knowledge bases of the theme in one step (bulk contribution). Membership is not necessary. Each contribution waits until a moderator of that knowledge base approves it. |
| CT14 | A moderator curates documents from other knowledge bases of the same theme, public personal knowledge bases included. |
| CT15 | An account forks a shared knowledge base or a public personal knowledge base into a new shared knowledge base. |
| CT16 | Names do not confuse the management of themes with the management of knowledge bases (OQ9, OQ11): the registry becomes a new table `knowledge_bases` and the old `themes` table is removed; the partition column becomes `kb_id`; the name `themes` is used only for collections. |

CT4, CT5, and CT13 replace RD29 rule 1 (collaborator ingest after an invitation) of `DATA_STORE_LAYOUT_DESIGN.md` §5.

Because the production server has only the admin account, the CT6 migration makes the admin the first moderator of all converted knowledge bases.

**Interpretation to confirm (OQ12)**: the owner wrote "the knowledge type is selected at theme creation". This document reads it as the creation of a shared knowledge base (Rev. 1 called it a theme), because the server makes themes automatically (CT12). If accounts make themes directly, Section 8 changes.

---

## 4. Terms

| Term | Meaning |
| :--- | :--- |
| Knowledge base | A knowledge database: the partition of documents, entities, relations, embeddings, and the other knowledge tables. Two kinds: personal and shared. |
| Personal knowledge base | The one knowledge base of an account (today: the default theme). All ingest goes here. Public or private. |
| Shared knowledge base | A collaborative knowledge base without an owner. Always public. |
| Theme | A collection of knowledge bases with the same subject and the same knowledge type. |
| Knowledge type | `real` (knowledge about the real world) or `fictional` (knowledge about an invented world). The server never puts knowledge bases of different types in one theme. Thus "London" of a novel does not join a theme about the real city. |
| Member, moderator, founder | As in Rev. 1, for a shared knowledge base. |
| Contribution | A copy of a document in a shared knowledge base, with the account that gets the credit. Origins: `direct`, `bulk`, `curation`, `fork`, `migration`. |
| Submission | One bulk contribution: some documents of a personal knowledge base sent to all shared knowledge bases of a theme. |
| Revision | A newer version of the source document of a contribution, waiting for approval (CT10). |

---

## 5. Names (CT16)

### 5.1 Database

| Before | After |
| :--- | :--- |
| Table `themes` | Table `knowledge_bases` (`ALTER TABLE themes RENAME TO knowledge_bases`; the foreign keys of the knowledge tables follow the rename) |
| Column `themes.is_default` | `knowledge_bases.is_personal` |
| Column `theme_id` in each knowledge table, in `federation_objects`, `jobs`, and the queues | `kb_id` |
| Indexes `idx_<table>_theme...`, `idx_themes_*` | `idx_<table>_kb...`, `idx_knowledge_bases_*` (drop and create) |
| `client.db` `linked_theme_id` | `linked_kb_id` |
| — | New table `themes` for collections (Section 11.2) |

**[unverified]** `telemetry.db`, `auth.db`, the job files `data/jobs/*.json`, and the graph bundle format can have `theme_id` fields. Unit CT-0 makes the list and renames them with a reader that accepts both names for files made before.

A query that still uses the old `themes` columns (for example `is_default`) fails on the new `themes` table with "no such column". The error is loud; no query reads collections by mistake.

### 5.2 Code and API

- Python: `theme_id` → `kb_id`, `ThemeManager` → `KnowledgeBaseManager`, `ThemeInfo` → `KnowledgeBaseInfo`, `store/theme.py` → `store/knowledge_base.py`, `tests/test_theme_id_explicit.py` → `tests/test_kb_id_explicit.py`.
- HTTP: path `/kb/{number}/`, query and body `kb`, header `X-Claire-KB`. The old query and body `theme` and the header `X-Claire-Theme` stay accepted with RFC 8594 `Deprecation` and `Sunset` headers, as for internal IDs (THEMES.md §4.1).
- MCP tools: parameter `kb`; `theme` stays accepted as an alias for the same period.
- WebUI: `localStorage` keys change once at the first load (as in namespace design §7.3).
- CLI: `claire kb ...`; `claire theme ...` is for themes (collections). CLI text is English (ASD-STE100).

### 5.3 Numbers

Knowledge bases keep their numbers. The generator of knowledge base numbers and the generator of theme numbers refuse a number that the other table already has and try again with the next `seq`. Thus one number never names a knowledge base and a theme, and old `/themes/{number}/` links resolve without doubt (Section 7).

---

## 6. Roles and Permissions

### 6.1 Shared Knowledge Base K

| Principal | Read | Join | Direct contribution | Bulk contribution (pending) | Fork | Moderate K | Give moderator role | Archive K | Delete K |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Anonymous | yes | 404 | 404 | 404 | 404 | 404 | 404 | 404 | 404 |
| Active account, not a member | yes | yes (policy) | 403 | yes | yes | 403 | 403 | 403 | 403 |
| Pending member | yes | — | 403 | yes | yes | 403 | 403 | 403 | 403 |
| Banned account | yes | 403 | 403 | K is not a target | yes | 403 | 403 | 403 | 403 |
| Member | yes | — | yes | yes | yes | 403 | 403 | 403 | 403 |
| Moderator | yes | — | yes | yes | yes | yes | 403 | yes | 403 |
| Admin | yes | yes | yes (as a member) | yes | yes | yes | yes | yes | yes |

"Moderate K" means: decide join requests; remove and ban members; approve, reject, hide, and remove contributions; approve and reject revisions; curate (CT14); change label, description, icon, join policy, and knowledge type; run graph maintenance (`/dedup/*`, `/resolution/*`, `/entity/primary-label`).

### 6.2 Theme T

| Principal | Read | Bulk contribution | Adjust (Section 8.3) |
| :--- | :---: | :---: | :---: |
| Anonymous | yes | 404 | 404 |
| Active account | yes | yes | 403 |
| Admin | yes | yes | yes |

### 6.3 Personal Knowledge Base P of Account A

| Principal | Read | Write | Fork | Curation source |
| :--- | :---: | :---: | :---: | :---: |
| A | yes | yes | — | — |
| Other active account | when P is public | 403 or 404 | when P is public | — |
| Moderator of a knowledge base in a theme where P is attached | when P is public | 403 | when P is public | when P is public |
| Anonymous | when P is public | 404 | 404 | — |

A private personal knowledge base gives 404 to all accounts except A (today's rule).

### 6.4 Credential Scopes

| Scope | Personal knowledge base | Shared knowledge bases |
| :--- | :--- | :--- |
| `owner` | read and write | all rights of the account |
| `collaborator` | read | **[proposal, OQ8]** read, direct contribution, bulk contribution; no moderation, no join, no leave, no fork |
| `readonly` | read | read |

### 6.5 Rules

1. One function decides each matrix. Routes do not check access on their own.
2. A private personal knowledge base and an unknown number give the same 404 (PRINCIPLES C1).

---

## 7. Paths

| Path | Content |
| :--- | :--- |
| `/` | Single mode: personal knowledge base of the admin. Network mode: public directory with themes first (Section 8.4). |
| `/@{u}/` | Network mode: personal knowledge base of `u` |
| `/kb` | Collection of shared knowledge bases. `?joined=true`: only those where the requester is an active member. |
| `/kb/{number}/…` | Shared knowledge base (pinned). A number of a personal knowledge base gets a 308 to `/@{u}/…` (single mode: `/…`) when the requester can read it, else 404. |
| `/@{u}/kb` | Collection of the shared knowledge bases where `u` is an active member (CT9) |
| `/themes` | Collection of themes |
| `/themes/{number}` | One theme: HTML page and JSON collection of its knowledge bases (content negotiation of namespace design §6.5) |
| `/themes/{number}/…` with a number of a knowledge base (old links) | Shared: 308 to `/kb/{number}/…`. Personal: 308 to `/@{u}/…` (single mode `/…`) when readable, else 404. |
| `/@{u}/themes/{number}/…` (old links) | Same as the line above |
| `/@{u}/themes` (old) | 308 to `/@{u}/kb` |
| `/themes/{number}` of a merged theme | 308 to the theme it was merged into |

The namespace middleware pins `/kb/{number}/` with `namespace_account_id = None`. It does not pin `/themes/{number}`; a theme is not a partition.

---

## 8. Themes

### 8.1 Knowledge Type

1. The founder selects the knowledge type when it makes a shared knowledge base (CT12, OQ12). Moderators and the admin can change it later.
2. A personal knowledge base has the type `real` by default. Its owner can change it. **[proposal, OQ13]**
3. The migration gives `real` to all existing knowledge bases.

### 8.2 Subject Judgement

The judgement uses only data that exists. It makes no LLM call. **[proposal]** for the method; unit CT-5 calibrates the numbers with development data **[unverified]**.

1. **Signature of a knowledge base.** The 256 entities with the highest weight. The weight of an entity is the number of visible documents that are its sources. The key is `(type, norm_name)`; aliases add their normal forms. Only visible documents of public knowledge bases count. A knowledge base with fewer than 5 visible documents has no signature.
2. **Weight across the server.** For each knowledge type, each key gets `idf = ln(N / df)`, where `N` is the number of knowledge bases with a signature and `df` the number of signatures that have the key. Common entities ("United States") get a low weight.
3. **Similarity.** Weighted Jaccard of the tf-idf vectors (sum of minimums divided by sum of maximums). When two knowledge bases use the same embedding model (namespace design §10.4), the cosine of their entity embedding centroids is a second signal: `score = 0.7 × entity + 0.3 × embedding`. With different models, `score = entity`.
4. **Clustering.** Average-linkage agglomerative clustering of the shared knowledge bases of one type. Stop when the next merge has `score < theme_match_threshold` (admin setting). Each shared knowledge base with a signature is in exactly one computed theme; a theme can have one knowledge base.
5. **Personal knowledge bases.** A public personal knowledge base attaches to each theme of its type where its average score with the members is at least `theme_match_threshold`. It can attach to several themes. It never makes a theme alone and does not change the clustering of shared knowledge bases. **[proposal, OQ13]**
6. **No signature.** A knowledge base without a signature (new, empty, or small) keeps its current theme memberships. A new shared knowledge base made from a theme page joins that theme. A fork joins the themes of its source.
7. **Stable identity.** A new cluster takes the number of the existing theme with the highest overlap of members (Jaccard of the member sets at least 0.5). Else it gets a new number. A theme without members gets the state `empty`: lists do not show it and its number gives 404, or 308 when it was merged.
8. **Label.** The computed label is the names of the two entities with the highest summed weight in the theme, for example "Entity A · Entity B". The description lists the top 10 entities. The admin can set a label; then later runs do not change it.
9. **Schedule.** A background loop runs the judgement once a day and when a knowledge base has 20 or more new visible documents since its last signature. The admin can start a run. Each run is one transaction for the themes and their members.

### 8.3 Admin Adjustments

Later runs keep each adjustment.

| Adjustment | Storage | Effect on later runs |
| :--- | :--- | :--- |
| Rename (lock the label) | `themes.label_locked = 1` | The label stays |
| Pin a knowledge base to a theme | `theme_knowledge_bases.pinned = 1` | The membership stays; the knowledge base can also be in a computed theme |
| Exclude a knowledge base from a theme | `theme_knowledge_bases.excluded = 1` | The run never adds it to that theme |
| Merge theme A into B | A: state `merged`, `merged_into_theme_id = B`; members of A pinned to B | `/themes/{A}` gives 308 to B |
| Split | The admin makes a new theme and pins knowledge bases to it | The pinned members stay |

### 8.4 Root Page and Theme Page

- **Network mode `/`**: the public directory shows themes first, ordered by recent activity (the last approved contribution in a member). Each entry shows the label, the knowledge type, the number of shared knowledge bases, the number of visible contributions, the top entities, and a link to `/themes/{number}`. A link opens the collection `/themes`.
- **Single mode**: **[proposal, OQ17]** `/` stays the home of the personal knowledge base of the admin. The home overview gets a theme card with a link to `/themes`.
- **Theme page `/themes/{number}`**: the knowledge bases of the theme (shared first, then attached personal knowledge bases), the top entities, and the actions "Contribute to this theme" (Section 9.2), "Make a shared knowledge base in this theme", and "Fork" for each knowledge base.
- Each item of a collection has `number`, `label`, `description`, `knowledge_type`, `state`, counts, and `url`.

---

## 9. Contributions

### 9.1 Direct Contribution

`POST /kb/{number}/contributions` with `{"document": "<public reference>"}`. The flow and checks of Rev. 1 stay:

1. The principal can contribute directly to K (Section 6.1); K is `active`; the source document is in the personal knowledge base of the account; K has no contribution with the same `canonical_url` or `content_hash` (409 with its URL); K has no tombstone for the `canonical_url` (409).
2. One transaction: copy the `documents` row with a new ID into K, copy the latest `extractions` row, insert a contribution (`origin 'direct'`, state `merging`), insert a `contribution_merge` job.
3. The merge job builds the graph of K from the copied extraction without a new LLM extraction; without an extraction it runs a normal extraction. Embeddings use the fixed embedding model of K (OQ19). State `visible` when the job completes, `failed` when it fails (the contributor or a moderator can try again).

**[unverified]** The pipeline can build the graph from a stored `raw_response`. **[unverified]** How the copy handles files in `data/raw` and `data/images` (copy or reference). Unit CT-4 checks both first.

### 9.2 Bulk Contribution (CT13)

1. `POST /themes/{number}/submissions` with `{"documents": [...]}` (at most 20 documents, **[proposal, OQ16]**).
2. Targets: the shared knowledge bases of the theme in the state `active` where the account is not banned. The server keeps the target list of the submission.
3. For each pair of a document and a target K:
   - Duplicate or tombstone in K: the pair is skipped. The submission records the reason.
   - Else: copy the `documents` row into K with `hidden = 1`, copy the extraction, and insert a contribution (`origin 'bulk'`, state `pending`, `submission_id`). No graph yet.
4. A moderator of K approves: `approved_at` is set and a `contribution_merge` job runs with the provider context of the contributor; then the state is `visible`. A moderator rejects: the copy and the contribution are deleted; the submission records "rejected".
5. The contributor can withdraw a pending contribution (CT7).
6. An account can have at most 100 pending contributions in one shared knowledge base (**[proposal, OQ16]**). Above this, the pair is skipped with the reason `pending_limit`.
7. The submission page shows the state of each pair to the contributor.

### 9.3 Approval and Withdrawal (CT7)

1. Approval does not control the visibility of a direct contribution; it accepts the donation. A bulk contribution becomes visible only after approval.
2. A contributor can withdraw its contribution only while `approved_at IS NULL`. After approval, a withdrawal gets 409 with the code `contribution_approved`.
3. An approval is final. A moderator can still remove an approved contribution.

### 9.4 Contribution Updates (CT10, OQ7)

1. Watch, refresh, re-ingest, and regenerate change documents only in personal knowledge bases.
2. When a source document changes, each contribution with that `source_document_id` gets `source_changed_at`. The personal knowledge base shows the contributor which contributions are older than the source.
3. The contributor sends an update for one contribution or for all contributions of the source document in one step. Each update is a revision: a hidden copy of the new version in K, without graph.
4. A moderator of K approves the revision: the merge job removes the graph sources of the old copy, merges the new copy, and the contribution points to the new copy. A moderator rejects: the revision copy is deleted. **[proposal, OQ15]** Each revision waits for approval, also for a contribution that is not approved; the visible version stays until the approval.
5. Approval of the contribution does not stop updates; it stops only withdrawal.

### 9.5 Moderator Actions on Contributions

| Action | Effect |
| :--- | :--- |
| Approve | Sets `approved_at`, `approved_by_account_id`. Pending: starts the merge job. |
| Reject | Pending only: deletes the copy and the contribution. |
| Hide | `documents.hidden = 1`, state `hidden`. Reversible. |
| Remove | Document purge in K (graph sources, embeddings, FTS), tombstone for the `canonical_url`, deletion of the contribution. |
| Approve or reject a revision | Section 9.4 |

Each action writes a row in `kb_moderation_log`.

### 9.6 Curation (CT14)

1. A moderator of K opens another knowledge base S of a theme of K (shared, or an attached public personal knowledge base) and selects documents.
2. `POST /kb/{number}/curations` with `{"source": "<number of S>", "documents": [...]}`.
3. Each document is copied into K as a contribution with `origin 'curation'`, `curator_account_id` = the moderator, `source_kb_id` = S, and the credit: for a shared S, the contributor of the document in S; for a personal S, the owner of S. The contribution is approved by the curator and becomes visible after the merge job (provider context of the curator).
4. Only visible documents of S can be curated. Duplicate and tombstone checks are as in Section 9.1.

### 9.7 Fork (CT15)

1. An active account forks a shared knowledge base or a public personal knowledge base: `POST /kb/{number}/forks` (for a personal knowledge base, the number of that knowledge base).
2. The fork is a new shared knowledge base: label "<source label> (fork)", the same knowledge type, the same embedding model, `forked_from_kb_id`, `forked_at`. The account becomes founder and moderator.
3. The fork copies the visible documents with their extractions, entities, relations, embeddings, and FTS rows directly in one job. It makes no LLM call and no new embedding.
4. Each copied document gets a contribution with `origin 'fork'`, `curator_account_id` = the account that forked, the credit of the source (contributor in a shared source, owner of a personal source), and `approved_at` = the fork time.
5. Hidden, pending, and failed documents of the source are not copied.
6. The fork joins the themes of its source (Section 8.2, rule 6).

---

## 10. Membership and Moderation

### 10.1 Lifecycle of a Shared Knowledge Base

| Action | Who | Effect |
| :--- | :--- | :--- |
| Create (`POST /kb`) | Each active account (CT3), no limit | State `active`, join policy `approval`, knowledge type from the founder. The founder becomes the first moderator (CT8). |
| Change settings (`PATCH /kb/{number}`) | Moderator, admin | Label, description, icon, join policy, knowledge type |
| Archive, unarchive | Moderator, admin | `archived`: read-only; no join, no contribution, no merge job; bulk targets exclude it |
| Delete (`DELETE /kb/{number}`) | Admin only | Same as `delete_theme(purge=True)` today; dry-run first (PRINCIPLES D2, D3) |

Labels need not be unique; the number is the identifier. `CLAIRE_MULTI_THEME=0` locks `POST /kb`. The tier field `max_themes` has no function after CT1 and CT3 and is removed from the tier and the console.

### 10.2 Join, Leave, and Moderator Assignment

| Route | Who | Effect |
| :--- | :--- | :--- |
| `POST /kb/{number}/membership` | Active account | `open`: active member. `approval`: pending. Banned or archived: 403. |
| `DELETE /kb/{number}/membership` | Member, pending member | Leave. The last moderator cannot leave (409). |
| `GET /kb/{number}/members` | Everyone | Collection of active members. Moderators and the admin also get pending and banned entries. |
| `PATCH /kb/{number}/members/{account}` | Moderator, admin | Approve, ban, or remove. A moderator cannot change another moderator. |
| `PUT`, `DELETE /api/admin/kb/{number}/moderators/{account}` | Admin | Give or remove the moderator role |

A shared knowledge base without a moderator has the admin as its only moderator. The admin console shows these first.

### 10.3 Console

| Page | Who | Content |
| :--- | :--- | :--- |
| `/settings/kb` | Account | Its memberships, the shared knowledge bases it founded, its submissions with the state of each pair, contributions with a newer source (Section 9.4), the button to make a shared knowledge base |
| `/settings/kb/{number}` | Moderator | Members and join requests; pending contributions and revisions first; contributions with approve, reject, hide, and remove; curation; the moderation log; settings |
| `/admin/kb` | Admin | All shared knowledge bases, those without a moderator, moderator assignment |
| `/admin/themes` | Admin | Themes, the last judgement run, `theme_match_threshold`, the adjustments of Section 8.3 |

New UI text uses keys of the English and Korean catalogs from the start.

---

## 11. Data Model

### 11.1 `claire.db` Version 26: Names Only

Version 26 makes the renames of Section 5.1 and nothing else. Behavior does not change.

### 11.2 `claire.db` Version 27: Shared Knowledge Bases and Themes

**`knowledge_bases`**

| Kind | `is_personal` | `account_id` | `is_public` |
| :--- | :---: | :--- | :--- |
| Personal | 1 | the account (NOT NULL) | 0 or 1 |
| Shared | 0 | NULL | 1 |

```sql
ALTER TABLE knowledge_bases ADD COLUMN created_by_account_id INTEGER;              -- value; NULL after an account purge
ALTER TABLE knowledge_bases ADD COLUMN join_policy TEXT NOT NULL DEFAULT 'approval'; -- 'open' | 'approval'
ALTER TABLE knowledge_bases ADD COLUMN state TEXT NOT NULL DEFAULT 'active';        -- 'active' | 'archived'
ALTER TABLE knowledge_bases ADD COLUMN archived_at REAL;
ALTER TABLE knowledge_bases ADD COLUMN knowledge_type TEXT NOT NULL DEFAULT 'real';  -- 'real' | 'fictional'
ALTER TABLE knowledge_bases ADD COLUMN forked_from_kb_id INTEGER;                   -- value
ALTER TABLE knowledge_bases ADD COLUMN forked_at REAL;
ALTER TABLE knowledge_bases DROP COLUMN is_collaborator_accessible;                 -- no reader (Section 6.4)
```

The store functions keep the kinds of the table above. A test checks that no row breaks them.

**Collaboration tables** (store module `store/kb_collab.py`)

```sql
CREATE TABLE IF NOT EXISTS kb_memberships (
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    public_id INTEGER UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('member', 'moderator')),
    state TEXT NOT NULL CHECK (state IN ('pending', 'active', 'banned')),
    requested_at REAL NOT NULL,
    decided_at REAL,
    decided_by_account_id INTEGER,
    PRIMARY KEY (kb_id, account_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_memberships_account ON kb_memberships(account_id, state);

CREATE TABLE IF NOT EXISTS kb_contributions (
    public_id INTEGER PRIMARY KEY,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,                  -- the copy in the shared knowledge base
    origin TEXT NOT NULL CHECK (origin IN ('direct', 'bulk', 'curation', 'fork', 'migration')),
    contributor_account_id INTEGER,             -- value; the account with the credit; NULL = deleted account (CT7)
    curator_account_id INTEGER,                 -- value; for 'curation' and 'fork'
    source_kb_id INTEGER,                       -- value
    source_document_id TEXT,                    -- value
    submission_id INTEGER,                      -- value; kb_submissions.public_id for 'bulk'
    state TEXT NOT NULL CHECK (state IN ('pending', 'merging', 'visible', 'hidden', 'failed')),
    approved_at REAL,                           -- NULL: the contributor can withdraw (CT7)
    approved_by_account_id INTEGER,
    source_changed_at REAL,                     -- Section 9.4
    contributed_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (kb_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_kb ON kb_contributions(kb_id, state, public_id);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_contributor ON kb_contributions(contributor_account_id);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_source ON kb_contributions(source_document_id);

CREATE TABLE IF NOT EXISTS kb_contribution_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_public_id INTEGER NOT NULL REFERENCES kb_contributions(public_id) ON DELETE CASCADE,
    staged_document_id TEXT NOT NULL,           -- hidden copy of the new version, no graph
    state TEXT NOT NULL CHECK (state IN ('pending', 'merging', 'failed')),
    submitted_at REAL NOT NULL,
    decided_at REAL,
    decided_by_account_id INTEGER
);

CREATE TABLE IF NOT EXISTS kb_submissions (
    public_id INTEGER PRIMARY KEY,
    theme_id INTEGER NOT NULL,                  -- value; the theme can merge later
    account_id INTEGER,                         -- value
    target_kb_ids TEXT NOT NULL,                -- JSON array, kept at submission
    skipped TEXT NOT NULL DEFAULT '[]',         -- JSON array of {kb, document, reason}
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    actor_account_id INTEGER,                   -- value
    action TEXT NOT NULL,                       -- approve_member, ban_member, remove_member, approve_contribution, reject_contribution, hide_contribution, unhide_contribution, remove_contribution, approve_revision, reject_revision, curate, grant_moderator, revoke_moderator, archive, unarchive, update_settings
    target TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_moderation_log_kb ON kb_moderation_log(kb_id, id);
```

**Theme tables** (store module `store/themes.py`)

```sql
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER UNIQUE NOT NULL,             -- time-ordered number; never equal to a knowledge base number (Section 5.3)
    label TEXT NOT NULL,
    label_locked INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    knowledge_type TEXT NOT NULL CHECK (knowledge_type IN ('real', 'fictional')),
    state TEXT NOT NULL CHECK (state IN ('active', 'empty', 'merged')),
    merged_into_theme_id INTEGER REFERENCES themes(id),
    top_entities TEXT NOT NULL DEFAULT '[]',    -- JSON
    last_activity_at REAL,
    computed_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS theme_knowledge_bases (
    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    source TEXT NOT NULL CHECK (source IN ('computed', 'admin', 'created', 'fork')),
    score REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    excluded INTEGER NOT NULL DEFAULT 0,        -- 1: not a member; later runs do not add it
    PRIMARY KEY (theme_id, kb_id)
);
CREATE INDEX IF NOT EXISTS idx_theme_knowledge_bases_kb ON theme_knowledge_bases(kb_id);

CREATE TABLE IF NOT EXISTS kb_signatures (
    kb_id INTEGER PRIMARY KEY REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    knowledge_type TEXT NOT NULL,
    embed_model TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL,                     -- JSON: [[type, norm_name, weight], ...]
    centroid BLOB,                              -- NULL without embeddings
    visible_documents INTEGER NOT NULL,
    computed_at REAL NOT NULL
);
```

Store boundary (RD27 R2): only `store/kb_collab.py` reads and writes the collaboration tables; only `store/themes.py` reads and writes the theme tables. The judgement reads entities through store functions of the knowledge tables.

### 11.3 Removed Schema

| Object | Version | Reason |
| :--- | :--- | :--- |
| Old table `themes` (as registry) | 26 | Renamed to `knowledge_bases` (CT16) |
| Tables `theme_members`, `users` | 27 | No reader; `theme_members` refers to `users` |
| Column `is_collaborator_accessible` | 27 | No reader |
| `data/themes.json` | 26 | The DB is the canonical registry. The JSON copy gives the admin as owner to entries without one, which is wrong for shared knowledge bases. The file is renamed to `themes.json.retired`; nothing reads or writes it. |

A table to drop that has rows is renamed `<name>_retired_v27`; the migration writes a warning and continues (operator rule). Account and server purge include renamed tables.

Candidates for later removal: `knowledge_bases.db_path` and `knowledge_bases.fqdn` when no reader stays **[unverified]** (`graph_bundle.py`, `cli.py` `import-graph`, legacy hosts in `namespace.py`).

---

## 12. Migration

Both versions complete on their own at startup and keep all data (operator rule). A second start changes nothing.

### 12.1 Version 26

1. Rename the table, the columns, and the indexes of Section 5.1 in one transaction.
2. Rename `data/themes.json` to `themes.json.retired`.
3. `client.db` (next version): rename `linked_theme_id` to `linked_kb_id`.

### 12.2 Version 27

1. Add the columns and tables of Section 11.2.
2. For each knowledge base with `is_personal = 0` and `account_id IS NOT NULL`, in one transaction for each knowledge base:
   1. `created_by_account_id = account_id`, `account_id = NULL`, `is_public = 1`, `join_policy = 'approval'`, `state = 'active'`, `knowledge_type = 'real'`.
   2. When the old owner has the state `active` or `suspended`, insert a membership (`moderator`, `active`). Else insert none; the admin moderates.
   3. For each document, insert a contribution: `origin 'migration'`, credit to the old owner, state `hidden` when `documents.hidden = 1` else `visible`, `approved_at` = the migration time.
   4. Set `watch_enabled = 0` on its documents and delete its rows in `refresh_queue` and `expand_queue` (CT10).
   5. When a step fails, write a warning, leave the knowledge base as it was, and try again at the next start. Until then the code treats it as a shared knowledge base with that account as moderator.
3. Drop or rename `theme_members` and `users` (Section 11.3).
4. The first subject judgement runs in the background after startup, not in the migration.

`federation_objects` of converted knowledge bases stay with the actor of the old owner (Section 13.3).

---

## 13. Effects on Other Subsystems

### 13.1 Ingest and Workers

1. All ingest entry points target the personal knowledge base: HTTP `/ingest*`, CLI, Telegram, feeds, the ingest of a federation item, MCP tools.
2. An ingest pinned to a shared knowledge base gets 403 with the code `kb_ingest_by_contribution`.
3. Watch, refresh, and expand take no row of a shared knowledge base (CT10). A moderator can start re-extraction or regeneration by hand with its own provider context.

### 13.2 Account Purge and Backup

| Data | Effect of an account purge |
| :--- | :--- |
| Personal knowledge base | Deleted |
| Memberships | Deleted |
| Contributions, curations, and fork copies with the credit of the account | Kept; `contributor_account_id = NULL` (CT7; personal sources: OQ14) |
| `created_by_account_id`, `curator_account_id` | Set to NULL |
| Pending submissions of the account | Pending contributions deleted |
| `kb_moderation_log.actor_account_id` | Kept as a value |

- An account backup has only the personal knowledge base. The server backup has all.
- Restore of a backup made before version 27 restores the personal knowledge base and skips the other knowledge bases of the archive; the result lists them.
- A data bundle of a shared knowledge base: moderators and the admin.

### 13.3 Federation

1. The account actor publishes only documents of its personal knowledge base.
2. Publish from a shared knowledge base waits for idea I1 (`Group` actor). A shared knowledge base or a theme is a candidate for I1; this document does not design it.
3. Published objects of converted knowledge bases stay with their actor. A moderator removal sends `Delete` through the publish outbox.

### 13.4 Telegram, MCP, CLI, Search

- Telegram: ingest goes to the personal knowledge base. `/kb` selects the personal knowledge base or a shared knowledge base for search and read.
- MCP: read tools accept public knowledge bases; write tools accept only the personal knowledge base.
- CLI: `claire kb` for knowledge bases, `claire theme` for themes (list, run judgement, adjust).
- Search stays in one knowledge base. Search across the knowledge bases of a theme is a later change.

### 13.5 Embeddings

Each knowledge base keeps its fixed embedding model (namespace design §10.4). The founder selects it from the models that the server allows. A fork keeps the model of its source. **[proposal, OQ19]** The merge job embeds with the provider context of the contributor when it serves that model, else with the tier provider of the server when it serves that model, else the contribution gets `failed` with the code `embedding_model_unavailable`.

---

## 14. Security

| ID | Risk | Control |
| :--- | :--- | :--- |
| CS1 | A request reads a private personal knowledge base through a path that skips the matrix | One function decides access (Section 6). Tests call each `namespaced` route with another account. |
| CS2 | A contribution copies a document of another account | The source check uses only the personal knowledge base of the account of the principal. |
| CS3 | A banned account comes back | `banned` stays until a moderator removes it; join gives 403; bulk targets exclude the knowledge base. |
| CS4 | A moderator gives itself more rights | Only the admin gives or removes the moderator role. |
| CS5 | Spam through bulk contributions | Limits of Section 9.2 (OQ16); pending contributions are hidden until approval. |
| CS6 | Curation or fork copies private content | Only public personal knowledge bases and only visible documents. When a personal knowledge base becomes private, the judgement removes its signature and theme attachments at once. When an owner makes its personal knowledge base public, the console tells it that public documents can be curated and forked. |
| CS7 | The judgement exposes entities of private knowledge bases | Signatures come only from public knowledge bases. Theme descriptions use only signature entities. |
| CS8 | Old links show a different object | Section 5.3: one number never names a knowledge base and a theme. |

---

## 15. Open Questions

Answered on 2026-11-16: OQ1 (CT7), OQ2 (CT8), OQ3 (CT9), OQ4 and OQ10 (CT5), OQ5 and OQ7 (CT10), OQ6 (Section 3), OQ9 and OQ11 (CT16, CT3).

| ID | Question | Proposal |
| :--- | :--- | :--- |
| OQ8 | Can a `collaborator` credential contribute (direct and bulk)? | Yes (Section 6.4). |
| OQ12 | Is the knowledge type selected when a shared knowledge base is made (this document), or do accounts make themes directly and select the type there? | At the creation of a shared knowledge base. The server makes themes. |
| OQ13 | Public personal knowledge bases in themes: attach to several themes by score, never make a theme alone, type `real` by default and set by the owner? | Yes (Section 8.1, 8.2). |
| OQ14 | When the owner of a personal knowledge base deletes a document, makes the knowledge base private, or purges the account, what happens to curated and forked copies? | They stay, like donations. The console notice of CS6 tells the owner before publication. A purge sets the credit to "deleted account". |
| OQ15 | Does each revision wait for moderator approval? | Yes. The visible version stays until the approval. |
| OQ16 | Limits of bulk contribution | 20 documents for each submission; 100 pending contributions for each account in one shared knowledge base. |
| OQ17 | Root page in single mode | `/` stays the home of the admin; a theme card on the home overview and `/themes`. |
| OQ18 | How long do the old API names (`theme`, `X-Claire-Theme`, `/themes/{number}/` for knowledge bases) stay? | The same period and headers as the internal theme IDs. |
| OQ19 | Who pays the embeddings of the merge job? | Section 13.5. |
| OQ20 | Numbers of the judgement (256 entities, 5 documents, 0.7/0.3, threshold) | Unit CT-5 calibrates them with development data; the threshold is an admin setting. |

---

## 16. Work

| Unit | Scope | Completion criteria |
| :--- | :--- | :--- |
| CT-0 | Names (Section 5): `claire.db` version 26, `client.db` rename, code, API aliases with `Deprecation` and `Sunset`, MCP alias, WebUI keys, `themes.json` retirement | No behavior change: all tests pass after the rename. A version 25 database starts as version 26 with the same rows. Old API names work with the deprecation headers. |
| CT-1 | `claire.db` version 27 (Section 11.2), migration (Section 12.2), `store/kb_collab.py`, `store/themes.py` | A version 26 database with four knowledge bases starts as version 27 with one personal and three shared knowledge bases, the admin as moderator, and one approved contribution for each document. A second start changes nothing. |
| CT-2 | Access matrices (Section 6), paths and redirects (Section 7), collections `/kb`, `/@{u}/kb`, `/themes` | Old `/themes/{number}/` and `/@{u}/themes/{number}/` links get 308 to `/kb/{number}/`. A private personal knowledge base gives 404 on each route to other accounts. |
| CT-3 | Membership and moderation (Section 10), admin moderator routes, moderation log | Join with `open` and `approval`, approve, ban, leave, last-moderator rule, admin assignment |
| CT-4 | Direct contribution, merge job, approve, reject, hide, remove, withdraw, revisions (Sections 9.1, 9.3 to 9.5) | Graph without a new LLM extraction when an extraction exists; duplicate 409; withdrawal after approval 409; an approved revision replaces the copy |
| CT-5 | Subject judgement (Section 8.2), admin adjustments (Section 8.3), root page and theme page (Section 8.4) | Two shared knowledge bases with overlapping entities and the same type are in one theme; with different types they are not; adjustments survive a new run; theme numbers stay across runs |
| CT-6 | Bulk contribution (Section 9.2) | Pending copies are not visible; each moderator approves or rejects alone; limits apply |
| CT-7 | Curation and fork (Sections 9.6, 9.7) | A fork has the same visible documents and graph without LLM calls; curation from a public personal knowledge base works and from a private one gives 404 |
| CT-8 | Ingest entry points, workers, purge, backup, federation, Telegram, MCP, CLI (Section 13) | A pinned ingest to a shared knowledge base gets 403; workers take no shared row; purge keeps contributions with a NULL credit |
| CT-9 | Console pages (Section 10.3) with English and Korean catalog keys | E2E test: A makes a shared knowledge base (`real`); B makes a similar one; both are in one theme on the root page; C sends a bulk contribution from the theme page; each moderator approves; A curates from B; D forks A. |
| CT-10 | Implementation documents: `THEMES.md` (becomes the knowledge base document), `SCHEMA_VERSIONING.md`, `ACCOUNTS.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml`, `COMMANDS.md`, `GLOSSARY.md` | The documents describe the code. This design document is removed after the implementation. |
