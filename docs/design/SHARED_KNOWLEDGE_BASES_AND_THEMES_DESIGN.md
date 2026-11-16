# Shared Knowledge Bases and Themes: One Personal Knowledge Base for Each Account, Collaborative Knowledge Bases, and Themes as Collections

- **Document Identifier**: `DESIGN-SHARED-KB-AND-THEMES-20261116`
- **Status**: Planned, not implemented (draft, Rev. 8). Owner decisions CT1 to CT37 are locked (2026-11-16). No question of this design is open (Section 15). Work starts from the visual prototype [`prototypes/console_modern_prototype.html`](prototypes/console_modern_prototype.html) (mock data; owner, 2026-11-16): it shows this design, the federation design, and the settings and admin console of [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md). The first prototype [`prototypes/shared_knowledge_federation_prototype.html`](prototypes/shared_knowledge_federation_prototype.html) is kept for reference.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Revisions**:
  - Rev. 1 (2026-11-16, file `COLLABORATIVE_THEMES_DESIGN.md`): a theme was one collaborative database.
  - Rev. 2 (2026-11-16): a **theme** is a collection of knowledge bases with the same subject. The collaborative database is a **shared knowledge base**. The registry table becomes `knowledge_bases` and the partition column becomes `kb_id` (CT16). New: subject judgement, root page listing, bulk contribution, curation, fork, contribution updates.
  - Rev. 3 (2026-11-16): answers to OQ8 and OQ12 to OQ20: reputation and campaign message (CT18), contribution without a theme (CT21), automatic revisions (CT20), fork of shared knowledge bases only (CT15), server-paid embeddings (CT24), activity records at account purge (CT19).
  - Rev. 4 (2026-11-16): `collaborator` is again a role in a shared knowledge base, given by moderators (CT17); the role model with capabilities, credential scopes, external IdP rules, and step-up authentication (Section 6). Old API names are closed with no fallback; only the path or a required parameter selects a knowledge base (CT23). Answers to OQ21 to OQ23 (CT18, CT26, CT27). The ingest focus is sealed (CT28). Review of a contributor proposal: automatic contribution and partnerships (Sections 9.9 and 9.10), links between duplicates later (Section 13.6).
  - Rev. 5 (2026-11-16): answers to OQ24 to OQ28 (CT29 to CT34). Automatic contribution is a server switch in the federation menu (CT31). Shared knowledge bases aim at partnerships between servers; the new design [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) starts idea I2 (CT32). Single mode sends `/` to `/@{username}/` of the admin (CT33, replaces CT22).
  - Rev. 6 (2026-11-16): terms as in Mastodon (CT36): **federation** is the open exchange between servers, and **relay** is the server-level subscription; the term "partnership" is retired. Themes share ingested documents between servers without an agreement (CT35). The agreement between two accounts is renamed mutual collaboration (Section 9.10). The exchange between servers is designed in [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md).
  - Rev. 7 (2026-11-16): the relay is an idea outside this design; it moved to [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) with the server-level partnership of the federation design Rev. 1 to Rev. 3 (owner: the plan grew too wide). The mutual collaboration (Section 9.10) is an exchange inside the server from the contributor proposal, not a part of that partnership; it stays here. The owner answered OQ29: CT37.
  - Rev. 8 (2026-11-16): the visual prototype is the base for the work (owner). The console pages of Section 10.3 get their place, tabs, and layout from [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md). The workspace stays; the prototype shows only the parts that this design adds (Sections 8.4, 10.3).
- **Related Documents**:
  - [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) (layout, groups, and tabs of the console; console and workspace)
  - [`THEMES.md`](../implementation/THEMES.md) (current implementation of the partition)
  - [`ACCOUNTS.md`](../implementation/ACCOUNTS.md) (principal, credentials, OIDC, step-up authentication)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (namespaces, numbers, collection contract §7, embedding space §10.4)
  - [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) (IdP rules §8.2, Claire as authorization server §8.3)
  - [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) (RD27 to RD29; this document replaces RD29 rule 1)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (ideas I1 and I2)
  - [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (federation of shared knowledge between servers, idea I2)
  - [`PER_ACCOUNT_FEATURES_DESIGN.md`](PER_ACCOUNT_FEATURES_DESIGN.md) (account backup)
  - [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[unverified]**.

---

## 1. Goal

1. An account has exactly one knowledge database: its **personal knowledge base**. Later functions for the person use it.
2. A **shared knowledge base** is a collaborative knowledge database. No account owns it. The admin gives the **moderator** role; moderators give the **collaborator** role.
3. Different accounts can make shared knowledge bases about the same subject. The server finds knowledge bases with the same subject from their entities and groups them in a **theme**, also across servers through federation (CT35). A theme is a collection of knowledge bases, served under `/themes` with the principles of Mastodon Collections. The root page lists themes so that people find the knowledge bases.
4. An account makes one decision, "contribute this document", or sets a rule once so that its matching documents go automatically. The server selects the theme and the targets. Moderators curate from other knowledge bases of the same theme. Shared knowledge bases can be forked.

---

## 2. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| All knowledge tables are partitioned by `theme_id`. All accounts use one `claire.db`. `SCHEMA_VERSION` is 25. | **[code]** `store/db.py`, THEMES.md §2 |
| `theme_id` occurs about 1810 times in 51 Python modules. `entities_fts` has no partition column. `client.db` has `linked_theme_id` (value). | **[code]** grep of `src/claire` |
| Each theme has an owner (`themes.account_id`). An account has one default theme (`idx_themes_account_default`) and can make more up to the tier limit `max_themes` (default 5). | **[code]** `store/db.py`, `api/server.py`; namespace design §10.3 |
| `themes.account_id` allows NULL (added with `ALTER TABLE`). | **[code]** `store/db.py` |
| Network mode serves themes at `/@{u}/themes/{number}/`. Root `/themes/{number}/` sends a 308 to the owner path. | **[code]** `api/namespace.py` |
| Numbers come from `timestamp_id(table, ...)`: `(unix_ms << 16) | low16`, where `low16` depends on the table name. Two tables can give the same number in theory. | **[code]** `ids.py` |
| The embedding space is fixed for each theme (`embed_provider`, `embed_model`); search reads only vectors of that model. | namespace design §10.4 |
| The tables `users` and `theme_members` (v14) have no reader and no writer. | **[code]** grep of `src/claire` |
| `documents` has no column for the account that added the document. `extractions.raw_response` keeps the model output. | **[code]** `store/db.py` |
| `reload()` reads entries that are only in `data/themes.json` and gives them the admin (1) as owner. | **[code]** `store/theme.py` |
| `_get_theme_ctx` selects the theme from the pinned path; else from a reference in the query `theme`, the body `theme`, or the header `X-Claire-Theme`; else the default theme of the account. A request without a namespace and without an account uses account 1. Internal IDs as references get deprecation headers. | **[code]** `api/server.py` `_get_theme_ctx`, `_extract_theme_ref` |
| A credential has one scope: `owner`, `collaborator`, or `readonly`. A `collaborator` credential ingests only into themes that are not the default theme and have `is_collaborator_accessible = 1`; it cannot read a private default theme. A `readonly` credential reads the private themes of its account. | **[code]** `api/server.py` `do_ingest`, `api/namespace.py` `can_view_theme` |
| Credentials are in the `auth_sessions` of `claire.db` (Telegram links), in `auth.db` (sessions), in `data/auth/api_tokens.json` (API tokens), in `oauth_tokens`, and in the environment (`CLAIRE_INJECT_TOKEN`, `CLAIRE_COLLABORATOR_TOKEN`, `CLAIRE_READONLY_TOKEN`, always the admin). | **[code]** `store/db.py`, `store/token_store.py`, ACCOUNTS.md §3 |
| OAuth tokens of Claire as authorization server have only the scope `readonly` (`scopes_supported`). | **[code]** `api/oauth.py` |
| The Telegram command `/webco` gives a collaborator web link for other persons ("default theme protected"); `/webro` gives a read-only link. | **[code]** `telegram_bot.py`, locale key `bot.web.link_collab` |
| Routes with the access level `collaborator`: ingest, re-ingest, regenerate, presentation, raw document, federation publish and retract, syndication dispatch. | **[code]** `api/security.py` `ROUTE_POLICY` |
| External IdP login (OIDC, GitHub) is implemented. An identity is found only by `(iss, sub)`. Sign-up needs an invitation. An IdP claim (groups included) cannot make or promote an admin. A back-channel logout ends sessions only. | **[code]** `api/oidc.py`, ACCOUNTS.md §10; IdP design §8.2 |
| `require_recent_auth` protects password, BYOK keys, account lifecycle, server changes, IdP settings, and federation settings. A bearer credential has no `authenticated_at` and cannot pass it. | **[code]** ACCOUNTS.md §5; IdP design §8.4 |
| Ingest, re-ingest, regenerate, Telegram (separator `|`, empty line, caption), the CLI, and syndication dispatch take a focus. When a request has none, `themes.default_focus` applies. `documents.meta.focus` and `syndication_dispatches.applied_focus` keep it. | **[code]** `api/server.py` `do_ingest`, `store/db.py`, `telegram_bot.py`, `cli.py` |
| Re-ingest fetches the stored source again (`target` is a document ID or a URL); it takes no new content from the request. | **[code]** `api/server.py` |
| The production server has one account, the admin. It runs in network mode, and the admin has a USERNAME. The development database has one account (state `setup_required`, no USERNAME) and four themes (one default). No external server uses the HTTP API. | owner, 2026-11-16; read of `data/claire.db` |

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
| CT7 | A contribution is a donation to the community of the shared knowledge base, also when the contributor ingested the document with its own API key. Ingested content is mostly external knowledge, and the server cannot know which part is knowledge of the account. The contribution stays when the contributor leaves, is banned, or purges its account; an account purge sets the credit to "deleted account". After a moderator approves a contribution, the contributor cannot withdraw it. |
| CT8 | The founder of a shared knowledge base becomes its first moderator. The admin can remove the role. |
| CT9 | `/@{u}/kb` is the collection of the shared knowledge bases where `u` is an active member. |
| CT10 | Watch, refresh, and expand run only in personal knowledge bases. Shared knowledge bases do not run these workers. A new version of a source document goes to its copies in shared knowledge bases automatically (CT20). |
| CT11 | A theme is a collection of knowledge bases with the same subject. It is served at `/themes` and `/themes/{number}` with the collection contract (namespace design §7.4). The root page lists themes. |
| CT12 | The founder selects the knowledge type (`real` or `fictional`) when it makes a shared knowledge base (OQ12). The server judges subjects only among knowledge bases of the same type, publishes the result automatically, and the admin adjusts it. |
| CT13 | A contribution to a theme goes to all shared knowledge bases of the theme in one step. Membership is not necessary. Each contribution waits until a reviewer of that knowledge base approves it. |
| CT14 | Reviewers curate documents from other knowledge bases of the same theme, attached public personal knowledge bases included. |
| CT15 | Only a shared knowledge base can be forked, into a new shared knowledge base. A personal knowledge base is never forked as a whole; its public documents are available only by curation (CT14). |
| CT16 | Names do not confuse the management of themes with the management of knowledge bases (OQ9, OQ11): the registry becomes `knowledge_bases`; the partition column becomes `kb_id`; the name `themes` is used only for collections. |
| CT17 | **Roles (OQ8, Rev. 4).** `collaborator` returns to its first meaning: a person who works with others on shared knowledge. It is a role in a shared knowledge base that a moderator gives, with a part of the rights of a moderator. It is no longer a credential scope. The personal knowledge base stays for the functions of the person. Section 6 has the role model. |
| CT18 | **Knowledge type of a personal knowledge base (OQ13, OQ21).** A personal knowledge base has no knowledge type, and its owner cannot set one: the one personal knowledge base keeps all ingest of the account, and nothing makes sure that an account keeps fiction out of it. The judgement treats a personal knowledge base as `real`. A reputation of the account makes fiction in the personal knowledge base costly in the exchange. The numbers of Section 8.6 are the first admin settings. The account, the reviewers on the review page, and the admin see the level; it is not public. Each ingest shows a standing campaign message: fiction goes to a shared knowledge base of the type `fictional`. |
| CT19 | **Copies and account purge (OQ14).** A copy made while its source was public stays when the source becomes private, when the owner deletes the source document, and when the owner purges its account. At the purge, the account can select to delete its activity records; this never changes the content of a knowledge base. Text that the account wrote and posted itself (for example replies, Section 13.6) is deleted. |
| CT20 | **Revisions (OQ15).** A revision is automatic. It comes only from a new fetch of the same source by watch, refresh, or re-ingest; the account cannot change its content. The merge job applies it without approval. A reviewer can stop the updates of a contribution. |
| CT21 | **Contribution without a theme (OQ16).** An account makes one decision: "contribute this document". It does not select a knowledge base and need not select a theme. The server selects the theme from the entities of the document and sends the contribution to the reviewers of the shared knowledge bases of that theme. The review page shows the decisions of the other reviewers. |
| CT22 | **Single mode root (OQ17).** Replaced by CT33. |
| CT23 | **Old API names (OQ18, Rev. 4).** The old names (`theme` in query and body, the header `X-Claire-Theme`, the MCP parameter `theme`, internal IDs as references) are closed at once. No code reads them; they are unknown fields and are ignored. No function falls back to a default knowledge base when a reference is missing: the path or a required parameter selects the knowledge base (Section 5.2). Old web links keep their 308 (Section 7). |
| CT24 | **Embedding cost (OQ19).** The server pays the embeddings of the merge job: the merge job embeds with the provider of the server. The merge job makes no LLM call. |
| CT25 | **Numbers (OQ20).** Unit CT-5 calibrates the numbers of the judgement with development data. Thresholds are admin settings. |
| CT26 | **Revisions of copies (OQ22).** The principles of CT19 and CT20 apply: a later change of visibility of the source does not change a copy, and revisions are automatic. Thus each copy follows the revisions of its root document while the root document exists, also after its personal knowledge base becomes private. |
| CT27 | **`/webco` (OQ23).** The Telegram command `/webco` and its collaborator web link are removed. |
| CT28 | **Ingest focus sealed.** Tests showed that the focus does not help because of the behavior of the LLM, and it is a large surface for abuse: it is a free-text prompt input, and contributions copy its result into shared knowledge bases. The focus is sealed at all entry points (Section 13.7). A later review decides if it comes back. |
| CT29 | **Fixed roles (OQ24).** The roles `member`, `collaborator`, and `moderator` are fixed in v1. Moderators do not split capabilities. Section 6.3 has the judgement and the triggers for a new review. |
| CT30 | **Scope `write` (OQ25).** API tokens get the scope `write` now. OAuth tokens stay `readonly` in this design; a `write` OAuth scope is a separate change. |
| CT31 | **Automatic contribution (OQ26).** Section 9.9 applies. The admin turns the function on or off for the server with the switch `kb_auto_contribution` in the ActivityPub federation menu (`/admin/federation`). |
| CT32 | **Exchange between servers (OQ27).** Shared knowledge bases and themes share knowledge between servers. In network mode, the root page shows this information. BookWyrm and similar projects are references; a reference needs a clean-room implementation because of the licenses. [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) has the design. Rev. 5 called this "partnerships between servers"; CT35 and CT36 replace that meaning. |
| CT33 | **Single mode and federation default.** When the ActivityPub functions are complete, the federation of shared knowledge is on by default, also in single mode. Thus the admin sets the USERNAME (the handle) at the first setup in each mode. Single mode shows no root page of network mode: `/` sends the request to `/@{username}/` of the admin (federation design §15). |
| CT34 | **Old `collaborator` tokens (OQ28).** API tokens with the scope `collaborator` become `readonly`. The console lists them once. |
| CT35 | **Themes across servers without an agreement.** Themes share ingested documents between servers through federation, with no agreement between admins. Shared knowledge bases of other servers join local themes. The reviewers of each shared knowledge base decide each contribution and curation across servers. The controls of the admin are one-sided: switches and domain blocks (federation design §6, §8). |
| CT36 | **Terms.** As in Mastodon: **federation** is the open exchange between servers, and a **relay** is a server-level subscription for announcements. The term "partnership" is retired: the bilateral agreement between servers is removed, and the agreement between two accounts (Section 9.10) is renamed **mutual collaboration** (federation design §3). The relay is an idea outside this design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md), Rev. 7). |
| CT37 | **Mutual collaboration (OQ29).** The mutual collaboration between two accounts (Section 9.10) stays. It is an exchange inside a server, from the contributor proposal. It also works between accounts of different servers with grants (federation design §12.2). |

CT4, CT5, and CT13 replace RD29 rule 1 (collaborator ingest after an invitation) of `DATA_STORE_LAYOUT_DESIGN.md` §5.

Because the production server has only the admin account, the CT6 migration makes the admin the first moderator of all converted knowledge bases.

---

## 4. Terms

| Term | Meaning |
| :--- | :--- |
| Knowledge base | A knowledge database: the partition of documents, entities, relations, embeddings, and the other knowledge tables. Two kinds: personal and shared. |
| Personal knowledge base | The one knowledge base of an account (today: the default theme). All ingest goes here. Public or private. No knowledge type (CT18). |
| Shared knowledge base | A collaborative knowledge base without an owner. Always public. Always has a knowledge type. |
| Theme | A collection of knowledge bases with the same subject and the same knowledge type. |
| Knowledge type | `real` (knowledge about the real world) or `fictional` (knowledge about an invented world). The server never puts knowledge bases of different types in one theme. Thus "London" of a novel does not join a theme about the real city. |
| Member, collaborator, moderator | Roles of an account in a shared knowledge base (Section 6.2). The founder is the first moderator. |
| Reviewer | A collaborator or a moderator: an account with the capability `review`. |
| Capability | A named right in a shared knowledge base, for example `review` or `people` (Section 6.2). A role is a fixed set of capabilities. |
| Contribution | A copy of a document in a shared knowledge base, with the account that gets the credit. Origins: `direct`, `bulk`, `auto`, `curation`, `fork`, `migration`. |
| Root document | The document in a personal knowledge base from which a chain of copies started. Revisions come from it. |
| Revision | A newer version of a root document, applied to its copies automatically (CT20). |
| Submission | One request "contribute these documents". Each document becomes a submission item with a theme and its target knowledge bases. |
| Automatic contribution | Contributions that a rule of the account makes without a request (Section 9.9). |
| Federation | The exchange of shared knowledge between servers without an agreement between admins (CT35, CT36). |
| Relay | A service actor that sends public announcements of shared knowledge bases to all its subscriber servers (CT36). An idea outside this design. |
| Known server | A Claire server that this server exchanges shared knowledge with (federation design §6.2). |
| Mutual collaboration | An agreement of two accounts: each becomes a collaborator in the new shared knowledge bases of the other (Section 9.10). |
| Document match | The theme or the knowledge base that fits a document best (Section 8.5). |
| Fiction-like document | A document of a personal knowledge base whose document match is a theme of the type `fictional`. |
| Reputation | The exchange level of an account: `good`, `limited`, or `low` (Section 8.6). |

---

## 5. Names and Selection of a Knowledge Base (CT16, CT23)

### 5.1 Database

| Before | After |
| :--- | :--- |
| Table `themes` | Table `knowledge_bases` (`ALTER TABLE themes RENAME TO knowledge_bases`; the foreign keys of the knowledge tables follow the rename) |
| Column `themes.is_default` | `knowledge_bases.is_personal` |
| Column `theme_id` in each knowledge table, in `federation_objects`, `jobs`, and the queues | `kb_id` |
| Indexes `idx_<table>_theme...`, `idx_themes_*` | `idx_<table>_kb...`, `idx_knowledge_bases_*` (drop and create) |
| `client.db` `linked_theme_id` | `linked_kb_id` |
| — | New table `themes` for collections (Section 11.2) |

**[unverified]** `telemetry.db`, `auth.db`, the job files `data/jobs/*.json`, and the graph bundle format can have `theme_id` fields. Unit CT-0 makes the list. The version 26 migration rewrites stored files once; no reader accepts both names. The graph bundle format gets a new format version; the importer reads each format version by its own rule.

A query that still uses the old `themes` columns (for example `is_default`) fails on the new `themes` table with "no such column". The error is loud; no query reads collections by mistake.

### 5.2 Selection of a Knowledge Base (CT23)

1. **Namespaced routes.** Only the path selects the knowledge base: `/@{u}/` selects the personal knowledge base of the account of the namespace; `/kb/{number}/` selects that knowledge base. In single mode the other root paths of the personal knowledge base get 308 to `/@{username}/…` of the admin (CT33). No query field, body field, or header selects a knowledge base on these routes.
2. **Routes without a namespace that act on one knowledge base** (console panels under `/settings/*`, MCP tools that read): the parameter `kb` (number) is required. A request without it gets 400 with the code `kb_required`. There is no default.
3. **Write to the personal knowledge base** (ingest, MCP ingest tools): the target is always the personal knowledge base of the account of the credential (CT4). These routes take no knowledge base reference.
4. **Single mode.** The root paths redirect to the namespace of the admin account (CT33). This is the definition of single mode, not a fallback. While the admin has no USERNAME (`setup_required`), there is no target, and requests get the page "setup not complete" (federation design §15). In network mode, a namespaced route without a prefix gives 404 (namespace design, N rules) **[unverified]**; unit CT-0 checks it.
5. **Old names.** No code reads `theme`, `X-Claire-Theme`, or an internal ID as a reference. They are unknown fields like all other unknown fields. Because only rules 1 to 3 select a knowledge base, an ignored field cannot change the target of a request.

### 5.3 Code and API

- Python: `theme_id` → `kb_id`, `ThemeManager` → `KnowledgeBaseManager`, `ThemeInfo` → `KnowledgeBaseInfo`, `store/theme.py` → `store/knowledge_base.py`, `tests/test_theme_id_explicit.py` → `tests/test_kb_id_explicit.py`. `_extract_theme_ref` and `resolve_theme_ref` with their deprecation path are removed.
- HTTP: path `/kb/{number}/`; the parameter `kb` only on the routes of Section 5.2 rule 2. On the routes of themes (`/themes`, `/submissions`), `theme` is the number of a theme.
- MCP tools: parameter `kb`, required for read tools. Tool schemas list only the current parameters.
- WebUI: navigation between knowledge bases uses paths (`/kb/{number}/`), not a header. `localStorage` keys change once at the first load (as in namespace design §7.3).
- CLI: `claire kb ...`; `claire theme ...` is for themes (collections). CLI text is English (ASD-STE100).

### 5.4 Numbers

Knowledge bases keep their numbers. The generator of knowledge base numbers and the generator of theme numbers refuse a number that the other table already has and try again with the next `seq`. Thus one number never names a knowledge base and a theme, and old `/themes/{number}/` links resolve without doubt (Section 7).

---

## 6. Roles and Permissions

### 6.1 Role Model (CT17)

The rights of a request are the capabilities of the roles of the account, limited by the scope of the credential. The way the account signed in never adds a right.

| Layer | Values | Storage | Who changes it |
| :--- | :--- | :--- | :--- |
| Identity | Password, IdP identities | `auth.db` (`account_identities`) | The account. An identity never gives a role (Section 6.7). |
| Server role | `admin`, `member` | `accounts.role` | Fixed: account 1 is the admin (ACCOUNTS.md) |
| Knowledge base role | Owner of the personal knowledge base; `member`, `collaborator`, or `moderator` of a shared knowledge base | `knowledge_bases.account_id`, `kb_memberships.role` | Moderators give `collaborator`; the admin gives `moderator` |
| Credential scope | `owner`, `write`, `readonly` | Sessions, API tokens, OAuth tokens, environment tokens | The account when it makes the credential (Section 6.6) |

### 6.2 Capabilities of a Shared Knowledge Base K

| Capability | Anonymous | Account, not a member | Member | Collaborator | Moderator | Admin |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `read` | yes | yes | yes | yes | yes | yes |
| `join`: request membership, leave | 404 | yes (policy) | leave | leave | leave (not the last) | yes |
| `contribute_direct` | 404 | 403 | yes | yes | yes | yes |
| `contribute_theme`: contribution to a theme of K | 404 | yes (pending) | yes (pending) | yes, no approval | yes, no approval | yes |
| `fork` | 404 | yes | yes | yes | yes | yes |
| `review`: approve, reject, hide, unhide, stop and restart updates | 404 | 403 | 403 | yes | yes | yes |
| `curate` | 404 | 403 | 403 | yes | yes | yes |
| `maintain`: graph maintenance (`/dedup/*`, `/resolution/*`, `/entity/primary-label`), re-extraction, regeneration, presentations | 404 | 403 | 403 | yes | yes | yes |
| `people`: decide join requests; ban and remove members and collaborators; give and remove `collaborator` | 404 | 403 | 403 | 403 | yes | yes |
| `settings`: label, description, icon, seed entities, join policy, knowledge type, archive | 404 | 403 | 403 | 403 | yes | yes |
| `remove`: remove a contribution (purge and tombstone) | 404 | 403 | 403 | 403 | yes | yes |
| `moderators`: give and remove `moderator` | 404 | 403 | 403 | 403 | 403 | yes |
| `delete` K | 404 | 403 | 403 | 403 | 403 | yes |

Rules:

1. A pending member has the capabilities of "Account, not a member". A banned account has only `read` and `fork`; K is not a target of its contributions.
2. Contributions of collaborators and moderators need no approval. With the reputation `low`, each contribution waits for approval (Section 8.6).
3. A moderator cannot change another moderator. A collaborator cannot change any role.
4. A collaborator must be an active member first. To remove the role makes it a member again.
5. Each route declares one capability. One function decides it from the role, the reputation, and the scope.

### 6.3 Custom Roles: Judgement (CT29)

Question of the owner: does a moderator need to split the rights of a collaborator into smaller rights?

Judgement: **no custom roles and no split of capabilities in v1.** The roles of Section 6.2 are fixed.

1. The work in a shared knowledge base has three kinds: content work (review, curation, maintenance), people and settings, and destructive acts (remove, delete). The collaborator role has the content work, which is the large and frequent work. The moderator keeps people, settings, and remove. The admin keeps moderators and delete. Each kind has one owner role.
2. Each split capability adds a column to the access matrix and a test case to each route (CS1). With fixed roles the matrix stays small enough for a full test.
3. A wrong custom setting is a security risk, and moderators of small communities seldom have time to set fine permissions.
4. The code uses capabilities, not role names, in each check (Section 6.2, rule 5). A later custom role is a new set of capabilities in a table, not a change of the checks.
5. Triggers for a new review: moderators of 2 or more shared knowledge bases ask for one capability without the others, or a shared knowledge base has more than 10 collaborators.

### 6.4 Theme T

| Principal | Read | Contribution | Adjust (Section 8.3) |
| :--- | :---: | :---: | :---: |
| Anonymous | yes | 404 | 404 |
| Active account | yes | yes | 403 |
| Admin | yes | yes | yes |

### 6.5 Personal Knowledge Base P of Account A

| Principal | Read | Write | Curation source |
| :--- | :---: | :---: | :---: |
| A | yes | yes | — |
| Other active account | when P is public | 403 or 404 | — |
| Reviewer of a knowledge base in a theme where P is attached | when P is public | 403 | when P is public |
| Anonymous | when P is public | 404 | — |

A private personal knowledge base gives 404 to all accounts except A (today's rule). Nobody forks P (CT15). Only A has rights in P; there are no collaborators of a personal knowledge base.

### 6.6 Credential Scopes (CT30, CT34)

A scope limits what a credential does for its account. It never adds a capability.

| Scope | Personal knowledge base of the account | Shared knowledge bases and themes | Account, credentials, rules |
| :--- | :--- | :--- | :--- |
| `owner` | All rights | All capabilities of the account | Yes; step-up actions need a recent sign-in (Section 6.8) |
| `write` | Read; ingest; publish and retract | Read; `contribute_direct`, `contribute_theme`, submissions, withdraw a pending contribution of the account | No |
| `readonly` | Read (private included, as today) | Read | No |

1. `write` replaces the credential scope `collaborator`. It is for the tools of the account: an AI agent, a script, a bot. It never has `join`, `fork`, `review`, `curate`, `maintain`, `people`, `settings`, `remove`, or role changes: these are decisions of the person.
2. Migration (auth.db next version, `claire.db` `auth_sessions`, `data/auth/api_tokens.json`): sessions with the scope `collaborator` end (they came from `/webco` links, CT27). API tokens with the scope `collaborator` become `readonly`, the smallest scope; the console lists them once with a notice. The environment token `CLAIRE_COLLABORATOR_TOKEN` is not read any more.
3. OAuth tokens keep only `readonly` in this design. A `write` OAuth scope (for MCP agents, Section 9.9) is a separate change with its own consent text and a Telegram approval push (CT30).

Route review (unit CT-2):

| Routes with the access level `collaborator` today | After |
| :--- | :--- |
| `/ingest`, `/ingest-stream`, `/reingest`, `/reingest-stream`, `/regenerate`, `/regenerate-stream`, `/document/regenerate` | `write`, personal knowledge base only (CT4) |
| `/document/presentation/generate`, `compose`, `compile`, `PUT /document/presentation/adoc` | `owner`; in a shared knowledge base also the capability `maintain` |
| `/document/raw` | `write`, with the read rule of the knowledge base |
| `/api/federation/publish`, `/api/federation/retract`, `/api/syndication/dispatch` | `write`. **[unverified]** Unit CT-2 checks that each takes only documents of the personal knowledge base of the account. |
| New: `POST /kb/{number}/contributions`, `POST /submissions`, withdraw | `write` |
| New: membership, review, curation, maintenance, people, settings, remove, roles, automatic rules, mutual collaborations | `owner` |

The integration removes: the column `is_collaborator_accessible`; the collaborator branches in `can_view_theme`, `_theme_accessible`, `list_themes(collaborator=...)`, `do_ingest`, and the MCP tools; the CLI options `--collaborator` and `--no-collaborator`; the body fields `collaborator_accessible` and `collaborator`; the console texts about collaborator access of each theme; the Telegram command `/webco` (CT27). The text of the token pages uses `write` for the middle scope.

### 6.7 External IdP

1. An IdP login gives an account and nothing more. The current rules stay: identity by `(iss, sub)` only, no merge by email, sign-up only with an invitation.
2. No IdP claim (groups, roles, email domain, tenant) gives or changes a server role, a knowledge base role, a credential scope, a mutual collaboration, or an automatic rule. Reason: the IdP administrator is outside the trust boundary of the server. A group change in the IdP would give moderation rights without a decision in Claire.
3. A membership linked to an IdP group (for example a Keycloak group as the members of a shared knowledge base) is not in v1. A later design can allow it only for the role `member`, only after a moderator turns it on for one knowledge base, and with a check at each sign-in.
4. A back-channel logout ends the sessions of the account and the tokens issued in those sessions. Roles, memberships, rules, and mutual collaborations stay.
5. A suspended account keeps its roles in storage, but they give no rights. Its automatic rule pauses. Its mutual collaborations pause. A shared knowledge base whose only moderator is suspended appears in `/admin/kb` as "without a moderator".
6. An account with only an IdP login can have each role.

### 6.8 Step-Up Authentication and Audit

1. These actions need `require_recent_auth` (a recent sign-in in a cookie session): give or remove `collaborator` or `moderator`; delete a shared knowledge base; request or accept a mutual collaboration; turn on an automatic rule or make it wider. Thus a bearer credential never changes a role or a standing rule.
2. Ban, remove, and the other review actions do not need step-up. Moderators must act fast.
3. Each role change and each review action writes a row in `kb_moderation_log`. An admin action on moderators also writes `admin_audit`.

### 6.9 Rules

1. One function decides each matrix. Routes do not check access on their own.
2. A private personal knowledge base and an unknown number give the same 404 (PRINCIPLES C1).
3. A test lists each route with its capability and its minimum scope. A new route without both fails the test.

---

## 7. Paths

| Path | Content |
| :--- | :--- |
| `/` | Single mode: temporary redirect (302) to `/@{username}/` of the admin (CT33). Network mode: public directory with themes and federated servers (Section 8.4). |
| `/@{u}/` | Personal knowledge base of `u`. In single mode only the admin has this path (CT33). |
| `/kb` | Collection of shared knowledge bases. `?joined=true`: only those where the requester is an active member. `?type=real` or `?type=fictional`. |
| `/kb/{number}/…` | Shared knowledge base (pinned). A number of a personal knowledge base gets a 308 to `/@{u}/…` (single mode: `/…`) when the requester can read it, else 404. |
| `/@{u}/kb` | Collection of the shared knowledge bases where `u` is an active member (CT9) |
| `/themes` | Collection of themes. `?type=real` or `?type=fictional`. |
| `/themes/{number}` | One theme: HTML page and JSON collection of its knowledge bases (content negotiation of namespace design §6.5) |
| `/submissions` | `POST`: contribute documents (Section 9.2). `GET`: collection of the submissions of the requester. |
| `/submissions/{number}` | One submission with the state of each item and each target |
| `/themes/{number}/…` with a number of a knowledge base (old links) | Shared: 308 to `/kb/{number}/…`. Personal: 308 to `/@{u}/…` (single mode `/…`) when readable, else 404. |
| `/@{u}/themes/{number}/…` (old links) | Same as the line above |
| `/@{u}/themes` (old) | 308 to `/@{u}/kb` |
| `/themes/{number}` of a merged theme | 308 to the theme it was merged into |

The namespace middleware pins `/kb/{number}/` with `namespace_account_id = None`. It does not pin `/themes/{number}` or `/submissions`; a theme is not a partition, and a submission belongs to the account of the credential.

---

## 8. Themes

### 8.1 Knowledge Type

1. The founder selects the knowledge type when it makes a shared knowledge base (CT12). Moderators and the admin can change it later.
2. A personal knowledge base has no knowledge type (`knowledge_type IS NULL`, CT18). No route sets it. The judgement treats it as `real`.
3. The migration gives `real` to converted shared knowledge bases and NULL to personal knowledge bases.

### 8.2 Subject Judgement

The judgement uses only data that exists. It makes no LLM call. **[proposal]** for the method; unit CT-5 calibrates the numbers with development data (CT25) **[unverified]**.

1. **Signature of a knowledge base.** The 256 entities with the highest weight. The weight of an entity is the number of visible documents that are its sources. The key is `(type, norm_name)`; aliases add their normal forms. Only visible documents of public knowledge bases count. In a personal knowledge base, fiction-like documents do not count (Section 8.5). A knowledge base with fewer than 5 counted documents has no signature.
2. **Weight across the server.** For each knowledge type, each key gets `idf = ln(N / df)`, where `N` is the number of knowledge bases with a signature and `df` the number of signatures that have the key. Common entities ("United States") get a low weight.
3. **Similarity.** Weighted Jaccard of the tf-idf vectors (sum of minimums divided by sum of maximums). When two knowledge bases use the same embedding model (namespace design §10.4), the cosine of their entity embedding centroids is a second signal: `score = 0.7 × entity + 0.3 × embedding`. With different models, `score = entity`.
4. **Clustering.** Average-linkage agglomerative clustering of the shared knowledge bases of one type. Stop when the next merge has `score < theme_match_threshold` (admin setting). Each shared knowledge base with a signature is in exactly one computed theme; a theme can have one knowledge base.
5. **Personal knowledge bases.** A public personal knowledge base attaches to each `real` theme where its average score with the members is at least `theme_match_threshold`, when the reputation of its account is not `low` (Section 8.6). It can attach to several themes. It never makes a theme alone and does not change the clustering of shared knowledge bases.
6. **No signature.** A knowledge base without a signature (new, empty, or small) keeps its current theme memberships. A new shared knowledge base made from a theme page joins that theme. A fork joins the themes of its source.
7. **Stable identity.** A new cluster takes the number of the existing theme with the highest overlap of members (Jaccard of the member sets at least 0.5). Else it gets a new number. A theme without members gets the state `empty`: lists do not show it and its number gives 404, or 308 when it was merged.
8. **Label.** The computed label is the names of the two entities with the highest summed weight in the theme, for example "Entity A · Entity B". The description lists the top 10 entities. The admin can set a label; then later runs do not change it.
9. **Theme signature.** Each run stores the signature of each theme (`themes.signature`) for the document match (Section 8.5), then computes the document matches, the reputations (Section 8.6), and the automatic contributions (Section 9.9).
10. **Schedule.** A background loop runs the judgement once a day and when a knowledge base has 20 or more new visible documents since its last signature. The admin can start a run. Each run is one transaction for the themes and their members.
11. **Remote knowledge bases.** Shared knowledge bases of known servers take part in the judgement like local shared knowledge bases (CT35, federation design §8.2).

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

- **Network mode `/`**: the public directory shows themes first, ordered by recent activity (the last approved contribution in a member). Each entry shows the label, the knowledge type, the number of local and remote shared knowledge bases, the number of visible contributions, the top entities, and a link to `/themes/{number}`. A link opens the collection `/themes`. The page also shows the federated servers and the recent activity across servers (federation design §8.3).
- **Single mode** (CT33): no root page. `/` sends the request to `/@{username}/` of the admin. The home overview of the personal knowledge base has a theme card with a link to `/themes`.
- **Theme page `/themes/{number}`**: the knowledge bases of the theme (shared first, then attached personal knowledge bases), the top entities, and the actions "Contribute to this theme" (Section 9.2), "Make a shared knowledge base in this theme", and "Fork" for each shared knowledge base.
- Each item of a collection has `number`, `label`, `description`, `knowledge_type`, `state`, counts, and `url`.
- **The workspace stays.** The personal knowledge base at `/@{u}/` keeps the workspace that is in service (WEBUI.md). This design adds parts to it and removes the focus control; it does not replace it. The visual prototype shows only the added parts ([`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) §7). The UI of `/kb/{number}/` is question CQ4 of that design.

### 8.5 Document Match

The document match routes contributions (CT21, Section 9.9) and finds fiction-like documents (CT18). It makes no LLM call.

1. **Keys of a document.** The `(type, norm_name)` keys of the entities that have the document as a source.
2. **Weight.** `idf` over the signatures of all knowledge bases of both types, because a document of a personal knowledge base has no type.
3. **Theme signature.** The union of the keys of the signatures of the shared knowledge bases of the theme (pinned members included, attached personal knowledge bases not included).
4. **Score with a theme or a knowledge base.** The idf-weighted part of the keys of the document that are in the signature: `Σ idf(k) for k in document ∩ signature`, divided by `Σ idf(k) for k in document`.
5. **Seed entities.** A shared knowledge base without a signature can have seed entities (names that its moderators give, Section 10.1). The server changes each name to its normal form and finds the keys with that `norm_name`. The seed score is the idf-weighted part of the seed keys that the document has. **[unverified]** Unit CT-5 calibrates `seed_match_threshold`.
6. **Theme match.** The theme with the highest score, when the score is at least `document_match_threshold` (admin setting) and the document has at least 3 keys. With the same score, the theme with more shared knowledge bases. Else the document has no match.
7. **When.** The ingest job computes the theme match of a new document after the extraction. Each judgement run computes the matches of the visible documents of personal knowledge bases again. Table `kb_document_matches` keeps the results.

### 8.6 Reputation (CT18)

The reputation of an account controls how far its personal knowledge base and its contributions go in the exchange. It never controls reading, ingest into the personal knowledge base, publication through the actor of the account, or membership. The numbers below are the first admin settings; unit CT-11 calibrates them.

| Signal | Value |
| :--- | :--- |
| Fiction share | Fiction-like documents divided by visible documents of the personal knowledge base. A fiction-like document does not count when its contribution in a shared knowledge base of the type `fictional` is visible or approved. |
| Approval rate | In the last 180 days: approved contributions divided by decided contributions of the account. Rejections and removals count only with the reason codes `off_subject`, `wrong_knowledge_type`, `low_quality`, and `spam` (Section 9.3). The decisions of one shared knowledge base count for at most one third of the decisions. No value with fewer than 10 decisions. |

| Level | Condition |
| :--- | :--- |
| `good` | No other level applies |
| `limited` | Fiction share at least 0.2, or approval rate below 0.5 |
| `low` | Fiction share at least 0.5, or approval rate below 0.2 |

| Effect | `good` | `limited` | `low` |
| :--- | :--- | :--- | :--- |
| Theme attachment of the public personal knowledge base | Yes | Yes, listed after the others with a mark | No; thus also no curation source |
| Pending contributions for each shared knowledge base | 100 | 20 | 5 |
| Order in the review queue | First | After `good` | Last |
| Direct contribution as a member | Visible after the merge job | Visible after the merge job | Waits for approval |
| Contribution as a collaborator or moderator, and contribution under a mutual collaboration | No approval | No approval | Waits for approval |
| Automatic contribution (Section 9.9) | Yes | Yes, 10 documents for each run and target | Paused |

The account sees its level, its signals, and each fiction-like document that counts, with its matched theme, in `/settings/kb`. Each such document has the action "Contribute to this theme". Reviewers see the level and the approval rate of the contributor on the review page. The admin sees all levels. Other accounts and anonymous requests do not see a reputation.

### 8.7 Campaign Message at Ingest (CT18)

1. **Standing message.** Each ingest form of the WebUI shows a message each time: fiction (novels, comics, games, films, invented worlds) goes to a shared knowledge base of the type `fictional`; fiction in the personal knowledge base lowers the reputation. The message has links to `/themes?type=fictional` and to "Make a shared knowledge base". The account cannot close it for good.
2. **Notice for a fiction-like document.** When the document match of a new document is a `fictional` theme, the result of the ingest job has a notice `{"code": "fiction_like_document", "document": ..., "theme": ...}`. The WebUI, the Telegram reply, the MCP tool result, and the CLI show it with the action "Contribute to this theme".
3. **Other surfaces.** Telegram, MCP, and the CLI show the standing message with the first ingest of each day for each account.
4. UI text uses keys of the English and Korean catalogs. CLI text is English (ASD-STE100).

---

## 9. Contributions

### 9.1 Direct Contribution

`POST /kb/{number}/contributions` with `{"document": "<public reference>"}`.

1. Checks: the principal has `contribute_direct` in K (Section 6.2) and a scope that allows it (Section 6.6); K is `active`; the source document is in the personal knowledge base of the account and has a completed extraction (else 409 `document_not_extracted`); K has no contribution with the same `canonical_url` or `content_hash` (409 with its URL); K has no tombstone for the `canonical_url` (409).
2. One transaction: copy the `documents` row with a new ID into K, copy the latest `extractions` row, insert a contribution (`origin 'direct'`, root = the source document, state `merging`), insert a `contribution_merge` job. With the reputation `low`, the copy is hidden and the state is `pending` (Section 8.6).
3. The merge job builds the graph of K from the copied extraction without an LLM call (CT24). Embeddings use the fixed embedding model of K and the provider of the server. State `visible` when the job completes, `failed` when it fails (the contributor or a reviewer can try again).

**[unverified]** The pipeline can build the graph from a stored `raw_response`. **[unverified]** How the copy handles files in `data/raw` and `data/images` (copy or reference). Unit CT-4 checks both first.

### 9.2 Contribution to a Theme (CT13, CT21)

1. `POST /submissions` with `{"documents": [...]}`, and optional `"theme": <number>`. At most 20 documents for each request (admin setting). The theme page sends its number.
2. For each document, the theme is the theme of the request (`routed_by 'account'`), else the theme match (`routed_by 'server'`, Section 8.5).
3. No theme: the item gets `theme_id = NULL`, and the response gives the code `no_theme` for the document with three actions: select a theme, contribute directly to a shared knowledge base where the account is a member (Section 9.1), or make a shared knowledge base.
4. Targets: the shared knowledge bases of the theme in the state `active` where the account is not banned. The item keeps the target list.
5. For each target K:
   - Duplicate or tombstone in K: skipped with the reason.
   - The account has the pending limit of its reputation in K (Section 8.6): skipped with the reason `pending_limit`.
   - The account is a collaborator or a moderator of K and its reputation is not `low`: as a direct contribution (Section 9.1), with `origin 'bulk'`.
   - Else: copy the `documents` row into K with `hidden = 1`, copy the extraction, and insert a contribution (`origin 'bulk'`, state `pending`, root = the source document, `submission_item_id`). No graph yet.
6. A reviewer of K approves: `approved_at` is set and the merge job runs (Section 9.1, step 3); then the state is `visible`. A reviewer rejects with a reason code: the copy is deleted and the contribution gets the state `rejected`.
7. The contributor can withdraw a pending contribution (CT7). The row is deleted.
8. `GET /submissions/{number}` and `/settings/kb` show the theme, the score, and the state of each target to the contributor.

### 9.3 Review and Help for Decisions (CT21)

1. The review queue of K lists pending contributions first, in the order of the reputation of the contributor (Section 8.6), then by age.
2. For each pending contribution, the review page shows:
   - the decisions on the same submission item in the other target knowledge bases: approved, rejected with the reason codes, pending;
   - the score of the document with the theme, and its score with the signature of K;
   - the reputation level and the approval rate of the contributor;
   - the origin (`bulk` or `auto`).
3. Reason codes for reject and remove: `off_subject`, `wrong_knowledge_type`, `duplicate`, `low_quality`, `spam`, `other`.
4. The server never decides for a reviewer.
5. `/admin/themes` shows for each shared knowledge base the agreement of its decisions with the majority decision of the other knowledge bases on the same items (items with 3 or more decisions). The admin uses it to find reviewers who decide very differently.

### 9.4 Approval and Withdrawal (CT7)

1. Approval does not control the visibility of a direct contribution; it accepts the donation. A pending contribution becomes visible only after approval.
2. A contributor can withdraw its contribution only while `approved_at IS NULL`. After approval, a withdrawal gets 409 with the code `contribution_approved`.
3. An approval is final. A moderator can still remove an approved contribution.

### 9.5 Revisions (CT10, CT20, CT26)

1. Watch, refresh, and re-ingest fetch sources again only in personal knowledge bases. A revision starts when the `content_hash` of a root document D changes. Regenerate and a re-ingest with the same content make no revision. A document from an uploaded file or a pasted text has no new fetch and no revision. **[unverified]** `documents.content_hash` is the hash of the fetched source content; unit CT-4 checks it.
2. The server finds the contributions with `root_document_id = D` and `updates_frozen = 0`, in each origin, also after the personal knowledge base of D becomes private (CT26).
3. A visible or hidden contribution: the new version and its extraction are copied into K without graph; the merge job removes the graph sources of the old copy, merges the new copy, and points the contribution to it. No approval. The state stays (a hidden contribution stays hidden). A row in `kb_contribution_revisions` records it.
4. A pending contribution: the new version replaces the pending copy. The reviewer reviews the latest version.
5. When D is deleted or its account is purged, the copies keep their last version (CT19).
6. A reviewer can stop and restart the updates of a contribution (`updates_frozen`). A reviewer can hide a contribution at any time.

### 9.6 Review Actions on Contributions

| Action | Capability | Effect |
| :--- | :--- | :--- |
| Approve | `review` | Sets `approved_at`, `approved_by_account_id`. Pending: starts the merge job. |
| Reject | `review` | Pending only, with a reason code: deletes the copy; state `rejected`. |
| Hide, unhide | `review` | `documents.hidden`, state `hidden` or back. Reversible. |
| Stop or restart updates | `review` | `updates_frozen = 1` or `0` (Section 9.5) |
| Remove | `remove` | With a reason code: document purge in K (graph sources, embeddings, FTS), tombstone for the `canonical_url`; state `removed`. |

Each action writes a row in `kb_moderation_log`.

### 9.7 Curation (CT14)

1. A reviewer of K opens another knowledge base S of a theme of K (shared, or an attached public personal knowledge base) and selects documents.
2. `POST /kb/{number}/curations` with `{"source": "<number of S>", "documents": [...]}`.
3. Each document is copied into K as a contribution with `origin 'curation'`, `curator_account_id` = the reviewer, `source_kb_id` = S, and the credit: for a shared S, the contributor of the document in S; for a personal S, the owner of S. The root comes from the source: for a shared S, the root of the contribution in S; for a personal S, the document itself. The contribution is approved by the curator and becomes visible after the merge job.
4. Only visible documents of S can be curated. Duplicate and tombstone checks are as in Section 9.1.
5. The copy stays and follows revisions when S becomes private; it stays when its owner deletes the source or purges the account (CT19, CT26).

### 9.8 Fork (CT15)

1. An active account forks a shared knowledge base: `POST /kb/{number}/forks`. With the number of a personal knowledge base, the request gets 403 with the code `kb_fork_shared_only` when the requester can read it, else 404.
2. The fork is a new shared knowledge base: label "<source label> (fork)", the same knowledge type, the same embedding model, the same seed entities, `forked_from_kb_id`, `forked_at`. The account becomes founder and moderator.
3. The fork copies the visible documents with their extractions, entities, relations, embeddings, and FTS rows directly in one job. It makes no LLM call and no new embedding.
4. Each copied document gets a contribution with `origin 'fork'`, `curator_account_id` = the account that forked, the credit and the root of the source contribution, and `approved_at` = the fork time.
5. Hidden, pending, rejected, removed, and failed documents of the source are not copied.
6. The fork joins the themes of its source (Section 8.2, rule 6).

### 9.9 Automatic Contribution (CT31)

Review of a contributor proposal (2026-11-16): "When a space about a subject appears, the documents that I collected and that fit go there automatically." This extends CT21 from one decision for each document to one decision for each rule.

**Server switch.** The function works only while the switch `kb_auto_contribution` in `server_settings` is `on`. The admin sets it in `/admin/federation`. The default is `off` (federation design KD4). When the admin turns it off, all rules pause and keep their settings; no run starts. Automatic contributions to other servers also need the switch on the receiving server (federation design §12.3).

1. **Rule.** An account has at most one automatic rule (`kb_auto_rules`). The rule selects the targets:
   - `memberships`: the shared knowledge bases where the account is an active member, collaborator, or moderator;
   - `mutual`: the shared knowledge bases that a mutual collaborator founded (Section 9.10);
   - `themes`: the shared knowledge bases of the themes that the account follows for this rule.
2. **Candidates.** For each target K: the visible documents of the personal knowledge base with a score with K (Section 8.5: the signature of K, else the theme signature, else the seed entities) at least `auto_match_threshold`. Not candidates: documents with the flag "never contribute"; documents from pasted text; documents from uploaded files unless the rule includes them; documents with a contribution in K in any state (a rejected or removed document never goes again to the same K); documents with a tombstone in K.
3. **Path.** A candidate goes to K as a direct contribution when the account is a member (Section 9.1), with no approval when the account is a collaborator or a moderator, and as a pending contribution in all other cases. The origin is `auto`. The reputation rules apply (Section 8.6).
4. **When.** After the extraction of a new document; when a new shared knowledge base gets a signature or seed entities; when a mutual collaboration starts; when the rule is turned on; and after each judgement run. Thus "a short wait" after a new space appears.
5. **Limits.** At most 50 documents for each run and target (admin setting), and the pending limit of the reputation.
6. **Safety.** Only an `owner` session with a recent sign-in turns the rule on or makes it wider (Section 6.8). Before it turns on, the console shows a dry run: the documents that would go now to each target (PRINCIPLES D2). The account can pause the rule at once and can withdraw each pending contribution. `/settings/kb` lists each automatic contribution.
7. **AI of the account.** An account can also use its own AI agent for the selection: the MCP tool `kb_candidates` (read: candidates for a knowledge base or a theme, with scores) and the tool `kb_submit` (scope `write`, Section 6.6). The server itself makes no LLM call for the selection.
8. A rejection of an automatic contribution counts in the approval rate as each other rejection. A rule that sends documents that do not fit lowers the reputation of its account.

### 9.10 Mutual Collaboration (CT37)

Review of the same proposal: "When friends agree, a new space gets their documents automatically after a short wait." This section is an agreement between two accounts (Rev. 5 called it a partnership; CT36). It works between accounts of one server, and between accounts of different servers through federation with grants (federation design §12.2).

1. Account A requests a mutual collaboration with account B; B accepts. Both actions need a recent sign-in (Section 6.8). Either account ends it at any time.
2. Effect: when A founds a shared knowledge base, B becomes a collaborator in it at once (`decided_by_account_id` = A, log action `grant_collaborator`), and the reverse. When the rule of B has `mutual`, the documents of B that fit go to the new knowledge base without approval (Section 9.9).
3. The end of a mutual collaboration does not change roles that it gave; moderators change them. New knowledge bases are not affected after the end.
4. A mutual collaboration never gives `people`, `settings`, `remove`, or `moderator`.
5. A suspended or purged account loses its mutual collaborations (suspension: paused; purge: deleted).

---

## 10. Membership and Moderation

### 10.1 Lifecycle of a Shared Knowledge Base

| Action | Who | Effect |
| :--- | :--- | :--- |
| Create (`POST /kb`) | Each active account (CT3), no limit | The founder selects the knowledge type (required) and can give up to 20 seed entities. State `active`, join policy `approval`. The founder becomes the first moderator (CT8). Mutual collaborators of the founder become collaborators (Section 9.10). |
| Change settings (`PATCH /kb/{number}`) | Capability `settings` | Label, description, icon, seed entities, join policy, knowledge type |
| Archive, unarchive | Capability `settings` | `archived`: read-only; no join, no contribution, no merge job; contributions to a theme and automatic contributions do not target it |
| Delete (`DELETE /kb/{number}`) | Admin only, step-up | Same as `delete_theme(purge=True)` today; dry-run first (PRINCIPLES D2, D3) |

Labels need not be unique; the number is the identifier. `CLAIRE_MULTI_THEME=0` locks `POST /kb`. The tier field `max_themes` has no function after CT1 and CT3 and is removed from the tier and the console.

### 10.2 Join, Leave, and Roles

| Route | Who | Effect |
| :--- | :--- | :--- |
| `POST /kb/{number}/membership` | Active account | `open`: active member. `approval`: pending. Banned or archived: 403. |
| `DELETE /kb/{number}/membership` | Member, collaborator, moderator, pending member | Leave. The last moderator cannot leave (409). |
| `GET /kb/{number}/members` | Everyone | Collection of active members with their roles. Accounts with `people` also get pending and banned entries. |
| `PATCH /kb/{number}/members/{account}` | Capability `people` | Approve, ban, or remove; give or remove `collaborator` (step-up). A moderator cannot change another moderator. |
| `PUT`, `DELETE /api/admin/kb/{number}/moderators/{account}` | Admin, step-up | Give or remove the moderator role |

A shared knowledge base without an active moderator has the admin as its only moderator. The admin console shows these first.

### 10.3 Console

| Page | Who | Content |
| :--- | :--- | :--- |
| `/settings/kb` | Account | Its memberships and roles, the shared knowledge bases it founded, its submissions with the theme and the state of each target, its automatic rule with the dry run and the list of automatic contributions, its mutual collaborations, its reputation with the signals and the fiction-like documents (Section 8.6), the button to make a shared knowledge base |
| `/settings/kb/{number}` | Reviewer | Collaborator: the review queue with the help of Section 9.3, curation, maintenance. Moderator: also members and roles, join requests, remove, the moderation log, settings. |
| `/admin/kb` | Admin | All shared knowledge bases, those without an active moderator, moderator assignment, failed merge jobs |
| `/admin/federation` | Admin | The switch `kb_auto_contribution` (CT31), the known servers and the domain blocks (federation design §6.5) |
| `/admin/themes` | Admin | Themes, the last judgement run, `theme_match_threshold`, `document_match_threshold`, `seed_match_threshold`, `auto_match_threshold`, the reputation numbers and limits, the adjustments of Section 8.3, the agreement of decisions (Section 9.3) |

New UI text uses keys of the English and Korean catalogs from the start.

[`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) gives the group, the tabs, and the layout of each page (its Section 5), with these effects on the table above:

1. `/settings/kb` has the tabs: personal knowledge base, submissions, automatic rule, reputation, memberships and mutual collaboration. It takes the function of `/settings/themes` (question CQ2 of that design).
2. `/settings/kb/{number}` has the tabs: queue, contributions, curation; for a moderator also members and roles, settings, moderation log.
3. `/admin/kb` has the tabs: all, failures and warnings. Its name is question CQ5 of that design.
4. `/admin/themes` keeps its path, and its content today (the themes of the admin account, the theme slot counts) goes away.
5. Sections that exist get changes from this design: tokens (CT30, CT34), Telegram (CT27, CT28), account deletion (CT19), backups (Section 13.2), MCP tools (Section 13.4), server tier (`max_themes`, Section 10.1).

---

## 11. Data Model

### 11.1 `claire.db` Version 26: Names Only

Version 26 makes the renames of Section 5.1 and nothing else. Behavior does not change.

### 11.2 `claire.db` Version 27: Shared Knowledge Bases and Themes

**`knowledge_bases`**

| Kind | `is_personal` | `account_id` | `is_public` | `knowledge_type` |
| :--- | :---: | :--- | :--- | :--- |
| Personal | 1 | the account (NOT NULL) | 0 or 1 | NULL |
| Shared | 0 | NULL | 1 | `real` or `fictional` |

```sql
ALTER TABLE knowledge_bases ADD COLUMN created_by_account_id INTEGER;              -- value; NULL after an account purge
ALTER TABLE knowledge_bases ADD COLUMN join_policy TEXT NOT NULL DEFAULT 'approval'; -- 'open' | 'approval'
ALTER TABLE knowledge_bases ADD COLUMN state TEXT NOT NULL DEFAULT 'active';        -- 'active' | 'archived'
ALTER TABLE knowledge_bases ADD COLUMN archived_at REAL;
ALTER TABLE knowledge_bases ADD COLUMN knowledge_type TEXT CHECK (knowledge_type IN ('real', 'fictional'));  -- NULL for a personal knowledge base (CT18)
ALTER TABLE knowledge_bases ADD COLUMN seed_entities TEXT NOT NULL DEFAULT '[]';    -- JSON names, at most 20 (Section 8.5)
ALTER TABLE knowledge_bases ADD COLUMN forked_from_kb_id INTEGER;                   -- value
ALTER TABLE knowledge_bases ADD COLUMN forked_at REAL;
ALTER TABLE knowledge_bases DROP COLUMN is_collaborator_accessible;                 -- CT17
```

The column `default_focus` stays as data and no code reads it (CT28).

The store functions keep the kinds of the table above. A test checks that no row breaks them.

**Collaboration tables** (store module `store/kb_collab.py`)

```sql
CREATE TABLE IF NOT EXISTS kb_memberships (
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    public_id INTEGER UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('member', 'collaborator', 'moderator')),
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
    document_id TEXT,                           -- the copy in the shared knowledge base; NULL after a rejection or a removal
    origin TEXT NOT NULL CHECK (origin IN ('direct', 'bulk', 'auto', 'curation', 'fork', 'migration')),
    contributor_account_id INTEGER,             -- value; the account with the credit; NULL = deleted account (CT7)
    curator_account_id INTEGER,                 -- value; for 'curation' and 'fork'
    source_kb_id INTEGER,                       -- value; the knowledge base the copy came from
    source_document_id TEXT,                    -- value
    root_kb_id INTEGER,                         -- value; the personal knowledge base where the chain started; NULL for 'migration'
    root_document_id TEXT,                      -- value; revisions come from this document (Section 9.5)
    submission_item_id INTEGER,                 -- value; kb_submission_items.id for 'bulk'
    state TEXT NOT NULL CHECK (state IN ('pending', 'merging', 'visible', 'hidden', 'failed', 'rejected', 'removed')),
    decision_reason TEXT NOT NULL DEFAULT '',   -- reason code of a rejection or a removal (Section 9.3)
    approved_at REAL,                           -- NULL: the contributor can withdraw (CT7)
    approved_by_account_id INTEGER,
    updates_frozen INTEGER NOT NULL DEFAULT 0,  -- 1: a reviewer stopped the revisions
    revised_at REAL,
    decided_at REAL,
    contributed_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (kb_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_kb ON kb_contributions(kb_id, state, public_id);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_contributor ON kb_contributions(contributor_account_id, decided_at);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_root ON kb_contributions(root_document_id, kb_id);
CREATE INDEX IF NOT EXISTS idx_kb_contributions_item ON kb_contributions(submission_item_id);

CREATE TABLE IF NOT EXISTS kb_contribution_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_public_id INTEGER NOT NULL REFERENCES kb_contributions(public_id) ON DELETE CASCADE,
    previous_document_id TEXT,
    document_id TEXT NOT NULL,                  -- the copy of the new version
    content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('merging', 'done', 'failed')),
    created_at REAL NOT NULL,
    completed_at REAL
);

CREATE TABLE IF NOT EXISTS kb_submissions (
    public_id INTEGER PRIMARY KEY,
    account_id INTEGER,                         -- value; NULL after an account purge
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_submission_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_public_id INTEGER NOT NULL REFERENCES kb_submissions(public_id) ON DELETE CASCADE,
    source_document_id TEXT NOT NULL,           -- value; document of the personal knowledge base
    theme_id INTEGER,                           -- value; NULL when no theme fits; the theme can merge later
    routed_by TEXT NOT NULL CHECK (routed_by IN ('server', 'account')),
    match_score REAL,
    target_kb_ids TEXT NOT NULL DEFAULT '[]',   -- JSON array, kept at submission
    skipped TEXT NOT NULL DEFAULT '[]'          -- JSON array of {kb, reason}
);
CREATE INDEX IF NOT EXISTS idx_kb_submission_items_submission ON kb_submission_items(submission_public_id);

CREATE TABLE IF NOT EXISTS kb_auto_rules (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('active', 'paused')),
    to_memberships INTEGER NOT NULL DEFAULT 0,
    to_mutual INTEGER NOT NULL DEFAULT 0,
    include_uploads INTEGER NOT NULL DEFAULT 0,
    activated_at REAL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_auto_rule_themes (
    account_id INTEGER NOT NULL REFERENCES kb_auto_rules(account_id) ON DELETE CASCADE,
    theme_id INTEGER NOT NULL,                  -- value; a merged theme is replaced by its target at each run
    PRIMARY KEY (account_id, theme_id)
);

CREATE TABLE IF NOT EXISTS kb_contribution_blocks (
    document_id TEXT PRIMARY KEY,               -- document of a personal knowledge base: "never contribute"
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_mutual_collaborations (
    account_low INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    account_high INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    requested_by_account_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'active')),
    created_at REAL NOT NULL,
    decided_at REAL,
    PRIMARY KEY (account_low, account_high),
    CHECK (account_low < account_high)
);

CREATE TABLE IF NOT EXISTS kb_moderation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    actor_account_id INTEGER,                   -- value; NULL for the server (automatic actions)
    action TEXT NOT NULL,                       -- approve_member, ban_member, remove_member, grant_collaborator, revoke_collaborator, approve_contribution, reject_contribution, hide_contribution, unhide_contribution, remove_contribution, freeze_updates, unfreeze_updates, curate, grant_moderator, revoke_moderator, archive, unarchive, update_settings
    target TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',            -- free text of the actor
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_moderation_log_kb ON kb_moderation_log(kb_id, id);

CREATE TABLE IF NOT EXISTS account_reputation (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    level TEXT NOT NULL CHECK (level IN ('good', 'limited', 'low')),
    fiction_share REAL NOT NULL,
    fiction_like_documents INTEGER NOT NULL,
    approval_rate REAL,                         -- NULL with fewer than 10 decisions
    decisions INTEGER NOT NULL,
    computed_at REAL NOT NULL
);
```

**Theme tables** (store module `store/themes.py`)

```sql
CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number INTEGER UNIQUE NOT NULL,             -- time-ordered number; never equal to a knowledge base number (Section 5.4)
    label TEXT NOT NULL,
    label_locked INTEGER NOT NULL DEFAULT 0,
    description TEXT NOT NULL DEFAULT '',
    knowledge_type TEXT NOT NULL CHECK (knowledge_type IN ('real', 'fictional')),
    state TEXT NOT NULL CHECK (state IN ('active', 'empty', 'merged')),
    merged_into_theme_id INTEGER REFERENCES themes(id),
    top_entities TEXT NOT NULL DEFAULT '[]',    -- JSON, for display
    signature TEXT NOT NULL DEFAULT '[]',       -- JSON keys, for the document match (Section 8.5)
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
    knowledge_type TEXT NOT NULL,               -- 'real' for a personal knowledge base (CT18)
    embed_model TEXT NOT NULL DEFAULT '',
    entities TEXT NOT NULL,                     -- JSON: [[type, norm_name, weight], ...]
    centroid BLOB,                              -- NULL without embeddings
    visible_documents INTEGER NOT NULL,
    computed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_document_matches (
    document_id TEXT PRIMARY KEY,               -- document of a personal knowledge base
    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    theme_id INTEGER,                           -- value; NULL when no theme fits
    knowledge_type TEXT,                        -- type of that theme
    score REAL,
    computed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_document_matches_kb ON kb_document_matches(kb_id, knowledge_type);
```

Store boundary (RD27 R2): only `store/kb_collab.py` reads and writes the collaboration tables, the automatic rules, the mutual collaborations, and `account_reputation`; only `store/themes.py` reads and writes the theme tables. The judgement reads entities through store functions of the knowledge tables. The reputation and the automatic contribution read document matches through store functions of `store/themes.py`.

### 11.3 Credential Stores

The next versions of the credential stores make the changes of Section 6.6: the scope value `collaborator` is no longer valid; sessions with it are deleted; API tokens with it become `readonly`. The CHECK constraints and validators accept `owner`, `write`, and `readonly`.

### 11.4 Removed Schema

| Object | Version | Reason |
| :--- | :--- | :--- |
| Old table `themes` (as registry) | 26 | Renamed to `knowledge_bases` (CT16) |
| Tables `theme_members`, `users` | 27 | No reader; `theme_members` refers to `users` |
| Column `is_collaborator_accessible` | 27 | CT17 |
| `data/themes.json` | 26 | The DB is the canonical registry. The JSON copy gives the admin as owner to entries without one, which is wrong for shared knowledge bases. The file is renamed to `themes.json.retired`; nothing reads or writes it. |

A table to drop that has rows is renamed `<name>_retired_v27`; the migration writes a warning and continues (operator rule). Account and server purge include renamed tables.

Candidates for later removal: `knowledge_bases.db_path` and `knowledge_bases.fqdn` when no reader stays **[unverified]** (`graph_bundle.py`, `cli.py` `import-graph`, legacy hosts in `namespace.py`).

---

## 12. Migration

All versions complete on their own at startup and keep all data (operator rule). A second start changes nothing.

### 12.1 Version 26

1. Rename the table, the columns, and the indexes of Section 5.1 in one transaction.
2. Rewrite the stored files of Section 5.1 that have `theme_id` once.
3. Rename `data/themes.json` to `themes.json.retired`.
4. `client.db` (next version): rename `linked_theme_id` to `linked_kb_id`.

### 12.2 Version 27

1. Add the columns and tables of Section 11.2. Personal knowledge bases keep `knowledge_type = NULL`.
2. For each knowledge base with `is_personal = 0` and `account_id IS NOT NULL`, in one transaction for each knowledge base:
   1. `created_by_account_id = account_id`, `account_id = NULL`, `is_public = 1`, `join_policy = 'approval'`, `state = 'active'`, `knowledge_type = 'real'`.
   2. When the old owner has the state `active` or `suspended`, insert a membership (`moderator`, `active`). Else insert none; the admin moderates.
   3. For each document, insert a contribution: `origin 'migration'`, credit to the old owner, no root (no revisions), state `hidden` when `documents.hidden = 1` else `visible`, `approved_at` = the migration time.
   4. Set `watch_enabled = 0` on its documents and delete its rows in `refresh_queue` and `expand_queue` (CT10).
3. A knowledge base whose transaction fails stays as it was. Its row breaks the kinds of Section 11.2, and the store treats such a row as unavailable: each route gives 404, and `/admin/kb` shows it with a warning. The next start tries again. No code gives it a meaning by a fallback.
4. Drop or rename `theme_members` and `users` (Section 11.4).
5. The first subject judgement, the document matches, and the reputations run in the background after startup, not in the migration.

`federation_objects` of converted knowledge bases stay with the actor of the old owner (Section 13.3).

---

## 13. Effects on Other Subsystems

### 13.1 Ingest and Workers

1. All ingest entry points target the personal knowledge base: HTTP `/ingest*`, CLI, Telegram, feeds, the ingest of a federation item, MCP tools.
2. An ingest pinned to a shared knowledge base gets 403 with the code `kb_ingest_by_contribution`.
3. After the extraction, the ingest job computes the document match (Section 8.5), adds the campaign notice when it applies (Section 8.7), and starts the automatic contribution of the account when its rule is active (Section 9.9).
4. Watch, refresh, and expand take no row of a shared knowledge base (CT10). A new content hash in a personal knowledge base starts the revisions of Section 9.5.
5. A reviewer can start re-extraction or regeneration of a document of a shared knowledge base by hand with its own provider context (capability `maintain`).

### 13.2 Account Purge and Backup

| Data | Effect of an account purge |
| :--- | :--- |
| Personal knowledge base | Deleted |
| Memberships and roles | Deleted |
| Contributions, curations, and fork copies with the credit of the account | Kept; `contributor_account_id = NULL` (CT7, CT19). This includes copies made while the personal knowledge base was public. |
| Revisions | Copies with a root in the deleted personal knowledge base keep their last version |
| Pending contributions of the account | Deleted; they are not yet content of a knowledge base |
| Automatic rule, contribution blocks, mutual collaborations | Deleted |
| `created_by_account_id`, `curator_account_id`, `approved_by_account_id`, `kb_moderation_log.actor_account_id` | Set to NULL ("deleted account") |
| `account_reputation`, `kb_document_matches` of the account | Deleted |

The purge confirmation has the option "Delete my activity records" (CT19). With this option the purge also deletes the submissions and submission items of the account and clears the `reason` texts that the account wrote in `kb_moderation_log`; the moderation actions stay without an actor. The option never changes documents, graphs, or contributions of a knowledge base. The confirmation tells the account that its contributions stay.

- An account backup has only the personal knowledge base. The server backup has all.
- Restore of a backup made before version 27 restores the personal knowledge base and skips the other knowledge bases of the archive; the result lists them.
- A data bundle of a shared knowledge base: moderators and the admin.

### 13.3 Federation

1. The account actor publishes only documents of its personal knowledge base.
2. Publish from a shared knowledge base waits for idea I1 (`Group` actor). A shared knowledge base or a theme is a candidate for I1; this document does not design it.
3. Published objects of converted knowledge bases stay with their actor. A moderator removal sends `Delete` through the publish outbox.

### 13.4 Telegram, MCP, CLI, Search

- Telegram: ingest goes to the personal knowledge base. `/kb` selects the personal knowledge base or a shared knowledge base for search and read. The ingest reply shows the notices of Section 8.7. `/webco` is removed (CT27). The help text has no focus syntax (Section 13.7).
- MCP: read tools need `kb` (Section 5.2); write tools accept only the personal knowledge base. The ingest tool result has the notices of Section 8.7. New tools `kb_candidates` and `kb_submit` (Section 9.9).
- CLI: `claire kb` for knowledge bases, `claire theme` for themes (list, run judgement, adjust).
- Search stays in one knowledge base. Search across the knowledge bases of a theme is a later change.

### 13.5 Embeddings (CT24)

Each knowledge base keeps its fixed embedding model (namespace design §10.4). The founder selects the model of a shared knowledge base from the models that the provider of the server serves. A fork keeps the model of its source. The merge job (contribution, curation, revision) embeds with the provider of the server. When that provider does not serve the model of K, the contribution gets `failed` with the code `embedding_model_unavailable`, and `/admin/kb` shows it.

### 13.6 Later Work (Not in This Design)

1. **Versions and replies.** When the server publishes an ingested document, accounts give other versions of it based on the original, and other accounts react with recommendation scores and ActivityPub replies in a thread. At an account purge, the replies and the other text that the account wrote and posted itself are deleted; ingested content stays (CT19).
2. **Merge decisions between servers (owner note on CT24).** Each server resolves entities in its own merge job, and the results differ between servers. Exchange between servers (idea I2) needs its own design, for example: federate the merge decisions of another server and propose an integrated ingest to the reviewers of the receiving server.
3. **Links between duplicates (contributor note).** v1 copies documents into each shared knowledge base. Links between duplicate knowledge across knowledge bases, in place of copies, wait until there is a real need.
4. **Ingest focus.** A later review decides if the sealed focus (CT28) comes back or is removed.
5. **Exchange between servers.** [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) designs it (CT32, CT35).

### 13.7 Seal of the Ingest Focus (CT28)

1. No entry point reads a focus: HTTP `/ingest*`, `/reingest*`, `/regenerate*`, `/document/regenerate`; the Telegram separator `|`, the empty-line separator, and the caption; the CLI option; the MCP tools; the syndication dispatch. A focus field is an unknown field and is ignored (CT23). The UI controls and the help texts for the focus are removed.
2. The Telegram parser still separates the URL from the text after a separator, so that the URL is ingested; the text after the separator is ignored, and the reply says once that the focus is off.
3. The pipeline gets no focus. The stored values (`documents.meta.focus`, `knowledge_bases.default_focus`, `syndication_dispatches.applied_focus`) stay as data, and no code reads them: re-ingest, refresh, and regenerate do not use a stored focus.
4. The focus code of the pipeline and the prompts stays without a caller until the later review (Section 13.6). A test checks that no entry point gives a focus to the pipeline.
5. The options for length and effort are not a focus and stay.

---

## 14. Security

| ID | Risk | Control |
| :--- | :--- | :--- |
| CS1 | A request reads a private personal knowledge base through a path that skips the matrix | One function decides access (Section 6). Tests call each `namespaced` route with another account. |
| CS2 | A contribution copies a document of another account | The source check uses only the personal knowledge base of the account of the principal. |
| CS3 | A banned account comes back | `banned` stays until a moderator removes it; join gives 403; contributions to a theme and automatic contributions do not target the knowledge base. |
| CS4 | A moderator gives itself more rights | Only the admin gives or removes the moderator role. A moderator cannot change another moderator. |
| CS5 | Spam through contributions to themes | Limits of Sections 8.6, 9.2, and 9.9; pending contributions are hidden until approval; the queue puts accounts with a low reputation last. |
| CS6 | Curation copies private content | Only public personal knowledge bases and only visible documents. When a personal knowledge base becomes private, the judgement removes its signature and theme attachments at once. When an owner makes its personal knowledge base public, the console tells it that public documents can be curated, and that curated copies stay and follow revisions when the knowledge base becomes private later (CT19, CT26). |
| CS7 | The judgement exposes entities of private knowledge bases | Signatures come only from public knowledge bases. Theme descriptions use only signature entities. Document matches of a private personal knowledge base are visible only to its account. |
| CS8 | Old links show a different object | Section 5.4: one number never names a knowledge base and a theme. |
| CS9 | A token of a tool changes roles or moderates | The scope `write` has no capability of a person (Section 6.6). Role changes need step-up, which a bearer credential cannot pass (Section 6.8). |
| CS10 | Reviewers lower the reputation of an account on purpose | The decisions of one shared knowledge base count for at most one third; only reason codes about the content count; the admin sees the agreement of decisions (Section 9.3). |
| CS11 | A wrong document match lowers a reputation | The account sees each counted document and its matched theme. The reputation never stops reading, ingest, or publication. The admin changes `document_match_threshold`. |
| CS12 | An IdP claim gives rights | No claim gives a role, a scope, a mutual collaboration, or a rule (Section 6.7). |
| CS13 | A collaborator sends bad content without review | Moderators remove the role at once; each contribution of a collaborator is in the log; the reputation `low` sends its contributions to review. |
| CS14 | An automatic rule publishes private notes | Pasted text never goes; uploaded files only when the rule includes them; the flag "never contribute"; dry run and step-up before the rule turns on; pause at once (Section 9.9). |
| CS15 | A mutual collaborator misuses the collaborator role | Either account ends the mutual collaboration at once; moderators remove the role; a mutual collaboration never gives `people`, `settings`, or `remove` (Section 9.10). |
| CS16 | A request goes to a wrong knowledge base by a default | Only the path or a required parameter selects a knowledge base (Section 5.2). Tests call each route without its reference and expect 400 or 404. |
| CS17 | The focus injects instructions into the extraction | The focus is sealed (Section 13.7). |

---

## 15. Open Questions

Answered on 2026-11-16: OQ1 (CT7), OQ2 (CT8), OQ3 (CT9), OQ4 and OQ10 (CT5), OQ5 and OQ7 (CT10), OQ6 (Section 3), OQ8 (CT17), OQ9 and OQ11 (CT16, CT3), OQ12 (CT12), OQ13 and OQ21 (CT18), OQ14 (CT19), OQ15 (CT20), OQ16 (CT21), OQ17 (CT22, then CT33), OQ18 (CT23), OQ19 (CT24), OQ20 (CT25), OQ22 (CT26), OQ23 (CT27), OQ24 (CT29), OQ25 (CT30), OQ26 (CT31), OQ27 (CT32), OQ28 (CT34), OQ29 (CT37).

The federation between servers has no open question; its decisions KD1 to KD9 are in [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) §2.1.

No question of this design is open. The questions about the console and the workspace (CQ1 to CQ6) are in [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) §11.

---

## 16. Work

| Unit | Scope | Completion criteria |
| :--- | :--- | :--- |
| CT-0 | Names and selection (Section 5): `claire.db` version 26, file rewrite, `client.db` rename, code, selection by path or required `kb` only, removal of the old names and of the internal ID references, WebUI navigation by path, `themes.json` retirement | All tests pass after the rename. A version 25 database starts as version 26 with the same rows. A request with `theme` or `X-Claire-Theme` gives the same result as without it. Each route that needs a knowledge base and has no reference in the path gives 400 `kb_required` without `kb`. |
| CT-1 | `claire.db` version 27 (Section 11.2), migration (Section 12.2), `store/kb_collab.py`, `store/themes.py` | A version 26 database with four knowledge bases starts as version 27 with one personal knowledge base (`knowledge_type` NULL) and three shared knowledge bases (`real`), the admin as moderator, and one approved contribution for each document. A second start changes nothing. A failed conversion leaves a row that gives 404 and shows in `/admin/kb`. |
| CT-2 | Role model and capabilities (Section 6), credential scopes and their migration (Sections 6.6, 11.3), IdP rules, step-up, route review, removal of the `is_collaborator_accessible` code and `/webco`, paths and redirects (Section 7), collections `/kb`, `/@{u}/kb`, `/themes` | A test lists each route with its capability and minimum scope. A `write` token ingests into the personal knowledge base and contributes, and gets 403 for review, people, settings, and roles. A bearer credential cannot change a role. An IdP group claim changes no right. Old `collaborator` sessions end and old `collaborator` tokens read only. Old `/themes/{number}/` links get 308 to `/kb/{number}/`. |
| CT-3 | Membership, roles, and moderation (Section 10), admin moderator routes, moderation log | Join with `open` and `approval`, approve, ban, leave, last-moderator rule, give and remove `collaborator` with step-up, admin assignment |
| CT-4 | Direct contribution, merge job without LLM and with the provider of the server, review actions, withdraw, automatic revisions, stop updates (Sections 9.1, 9.4 to 9.6) | Graph without an LLM call; duplicate 409; withdrawal after approval 409; a new content hash of the root updates the copies without approval, also after the source becomes private; a stopped copy stays |
| CT-5 | Subject judgement (Section 8.2), document match and seed entities (Section 8.5), admin adjustments (Section 8.3), root page and theme page (Section 8.4), calibration (CT25) | Two shared knowledge bases with overlapping entities and the same type are in one theme; with different types they are not; adjustments survive a new run; theme numbers stay across runs; a document with the entities of a theme matches that theme |
| CT-6 | Contribution to a theme with routing (Section 9.2), review help (Section 9.3) | A document without a selected theme goes to the shared knowledge bases of its matched theme; no match gives `no_theme`; pending copies are not visible; each reviewer decides alone and sees the other decisions; contributions of collaborators need no approval; limits apply |
| CT-7 | Curation and fork (Sections 9.7, 9.8) | A fork of a shared knowledge base has the same visible documents and graph without LLM calls; a fork of a personal knowledge base gets 403 `kb_fork_shared_only`; curation from a public personal knowledge base works and from a private one gives 404 |
| CT-8 | Ingest entry points, workers, purge with the option for activity records, backup, federation, Telegram, MCP, CLI (Section 13) | A pinned ingest to a shared knowledge base gets 403; workers take no shared row; purge keeps contributions with a NULL credit; the option deletes submissions and reason texts and no content |
| CT-9 | Console pages (Section 10.3) with English and Korean catalog keys, in the layout of [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) (after its unit CL-1) | E2E test: A makes a shared knowledge base (`real`); B makes a similar one; both are in one theme on the root page; C ingests a document and contributes it without a theme; the server routes it to that theme; each moderator approves and sees the decision of the other; A gives C the role `collaborator`; C curates from B; D forks A; E ingests a fiction document and sees the fiction-like notice. |
| CT-10 | Implementation documents: `THEMES.md` (becomes the knowledge base document), `SCHEMA_VERSIONING.md`, `ACCOUNTS.md`, `API_HTTP_CONTRACT.md`, `openapi.yaml`, `COMMANDS.md`, `GLOSSARY.md`, `ENVIRONMENT_VARIABLES.md` | The documents describe the code. This design document is removed after the implementation. |
| CT-11 | Reputation (Section 8.6) and campaign message (Section 8.7) | Fiction-like documents raise the fiction share; the level changes the theme attachment, the pending limit, the queue order, the direct contribution, and the automatic contribution; an approved contribution to a `fictional` shared knowledge base removes the document from the count; the standing message and the notice show on each surface |
| CT-12 | Seal of the ingest focus (Section 13.7). Independent of the other units; it can come first. | No entry point gives a focus to the pipeline; stored focus values stay and are not read; the Telegram URL with a separator is ingested without the focus; the UI has no focus control |
| CT-13 | Automatic contribution with its server switch (Section 9.9), and mutual collaboration (Section 9.10) | E2E test: the admin turns on `kb_auto_contribution`; A and B make a mutual collaboration; B turns on its rule with `mutual` after the dry run; A makes a shared knowledge base with seed entities; after the run, the matching documents of B are visible in it without approval; a document with "never contribute" and a pasted text do not go; a document that a reviewer rejected does not go again. |
