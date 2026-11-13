# Data Store Layout: Database Files, Subsystem Versions, and the Knowledge Database of Accounts

- **Document Identifier**: `DESIGN-DATA-STORE-LAYOUT-20261113`
- **Status**: Draft. Owner decisions RD27, RD28, and RD29 are locked (2026-11-13).
- **Date**: 2026-11-13
- **Related Documents**:
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (FP4; Section 7 uses this layout)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (namespaces, theme numbers)
  - [`MULTI_THEME_ARCHITECTURE_DESIGN.md`](MULTI_THEME_ARCHITECTURE_DESIGN.md) (frozen history: one database file per theme, merged into one file in 2026-09)
  - [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[unverified]**.

---

## 1. Facts at the Base Commit

| Fact | Evidence |
| :--- | :--- |
| `claire.db` has 38 tables in one schema with one version number (`SCHEMA_VERSION`, 21). Each change of any subsystem increases the number, and about 11 test files pin the number. | **[code]** `store/db.py` |
| The schema is hand-written SQL with one idempotent migration function for each version. There is no model or migration generator. | **[code]** `store/db.py` |
| `auth.db` (authentication) and `fedify.db` (Fedify cache and message queue, `@fedify/sqlite`) are separate files. | **[code]** `store/auth_db.py`, `sidecar/fedify/src/main.ts` |
| The knowledge data (themes, documents, entities, relations, vectors) of all accounts is in `claire.db`. Each knowledge table has `theme_id`; a theme has `account_id`. Account backup, restore, and purge work by theme. | **[code]** `store/db.py`, `account_backup.py` |
| In 2026-09 the database files of each theme were merged into `claire.db` (`migrate_multi_db_to_single_db`). | **[code]** |
| The account feed tables (`feed_subscriptions`, `feed_items`) are in `claire.db` version 21. | **[code]** |

---

## 2. Decisions

| ID | Decision |
| :--- | :--- |
| RD27 | **Database files by the kind of data, each with its own version.** `claire.db` (shared assets), a new `client.db` (communication data of accounts), `auth.db`, `fedify.db`. Each file has its own schema version; a change of one subsystem does not change the version of another file. Includes R1 (stable public IRIs of assets stay in `claire.db`) and R2 (store boundaries that allow a later move of one subsystem to PostgreSQL). |
| RD28 | **Knowledge data of accounts: one knowledge database (option A) now**, with defined triggers for a move to one file for each account (option B). |
| RD29 | **Service decisions for themes of accounts:** (1) a collaborator ingests into the theme of another account only after an invitation of the owner of the theme; (2) one ingest limit that the admin sets, the same for all accounts, at first; (3) a public theme is visible outside the server only through the actor of its account; a theme actor waits for the design of idea I1. |

The owner confirmed that the layout stays valid for many users and for a later exchange between Claire Bible servers (2026-11-13).

---

## 3. Database Files (RD27)

| File | Content | Kind of data | Version |
| :--- | :--- | :--- | :--- |
| `claire.db` | Accounts, themes, documents, the knowledge graph, published objects (`federation_objects`), the publish outbox, followers, tombstones, domain blocks, instance settings (with `federation_reading`) | Shared assets and instance state; kept long | `meta.schema_version` (`SCHEMA_VERSION`) |
| `client.db` | Feeds and feed items, follows, settings of accounts for reading, the timeline, notes, reactions, quote authorizations, automatic replies, the client outbox, the media cache records | Communication data of accounts; large and pruned by retention | `client_meta.schema_version` (`CLIENT_SCHEMA_VERSION`) |
| `auth.db` | Credentials, sessions, admin audit | Security data | its own |
| `fedify.db` | Fedify key-value cache and message queue | Protocol cache of the sidecar | Fedify |

Rules:

1. **One file for all accounts.** `client.db` is not split by account: one received post goes to many accounts in one transaction (fan-out).
2. **No transaction across files.** In WAL mode SQLite commits a transaction over attached files atomically for each file only **[external: SQLite documentation, "ATTACH DATABASE" and "Write-Ahead Logging"]**. Thus each write that must be atomic stays in one file: a follow and its `follow` task are both in `client.db` (the client outbox); a publish and its `create` task are both in `claire.db` (the publish outbox). The sidecar reads both outboxes through the internal API.
3. **References across files have no foreign key.** `client.db` refers to `accounts.id`, `themes.id`, and document IDs of `claire.db` by value. The application deletes the rows of an account in both files (account purge). An item keeps its link to an ingested document (`linked_doc_id`) as a value; a deleted document leaves a link that the reader shows as gone.
4. **Exchange between Claire servers (idea I2) uses the same flow:** content from another server is collected in `client.db` and enters a theme in `claire.db` only by an ingest that the owner starts (FP4 P2).
5. **R1 Public identity of assets.** The stable public IRIs of documents, themes, and later entities are in `claire.db` (today: `federation_objects.public_id`). Whether entities and relations are exchanged is a question of the design of I2.
6. **R2 Store boundaries.** Each subsystem reads and writes its tables only through its store module (`store/feeds.py`, `store/federation_client.py`, ...). No SQL of one subsystem reads the tables of another. Then one subsystem can move to PostgreSQL alone when SQLite becomes a limit. No move is planned now (one production server, one participant, invitations limited).
7. **Startup.** The `api` process and the CLI create and migrate `client.db` at start, as they do for `auth.db`. The migration completes on its own; an operator never manages a database file (operator rule).

### 3.1 Subsystem Versions

1. `client.db` starts at version 1 with its own `client_meta` table (`schema_version`, `schema_lineage` = `claire-client`).
2. Tests compare with the constant of the file (`CLIENT_SCHEMA_VERSION`, `SCHEMA_VERSION`), not with a number, so that a new version does not change unrelated tests.

### 3.2 Move of the Feed Tables

`claire.db` version 22 moves the feed tables to `client.db`: the first start copies all rows of `feed_subscriptions` and `feed_items` with the same IDs into `client.db`, checks the counts, and then drops the tables from `claire.db`. When the copy does not complete, the tables stay in `claire.db` and the next start tries again; no row is lost. After this, `claire.db` gets no new client table.

---

## 4. Knowledge Database of Accounts (RD28)

| Option | For | Against |
| :--- | :--- | :--- |
| **A. One knowledge database (now)** | Functions across themes (search, public explore, idea I3) and the exchange between servers are simple; one migration | A large ingest of one account makes the writes of other accounts wait; a size limit for each account is difficult |
| B. One file for each account | Isolation; export, purge, backup, and size limits for each account are simple | Functions across accounts need `ATTACH` or a shared index; each migration runs on N files |
| C. One file for each theme | — | Used before and merged in 2026-09; too fine |

Triggers for a move from A to B (any one): several accounts ingest at the same time and the writes wait measurably; a size limit for each account is necessary; a legal or contract rule needs physical separation of the data of an account.

Rule now: each access to a knowledge table goes through a store function with the theme scope (and so the account scope). Then a move to B is a mechanical migration, the reverse of the merge of 2026-09.

---

## 5. Service of Themes of Accounts (RD29)

1. **Collaborators.** A collaborator credential ingests into a theme of another account only after an invitation of the owner of that theme. Without an invitation, the theme of another account is read-only for a public theme and hidden for a private theme.
2. **Ingest limit.** At first, one limit that the admin sets (documents, storage, and LLM use for each account), the same for all accounts. Limits for each account are a later change.
3. **Visibility outside the server.** A public theme is visible to other servers only through the actor of its account (published documents, FP3 and FP4). A theme actor (`Group`, idea I1) waits for its own design.

---

## 6. Work

| Unit | Scope | Completion criteria |
| :--- | :--- | :--- |
| DS-1 (with FP4-2) | `store/client_db.py` (path, connection, `client_meta`, version 1 migration with the feed tables and the FP4-2 tables), startup in the `api` process and the CLI, the move of the feed tables (`claire.db` version 22), feed code on `client.db`, account purge in both files, account backup of `client.db` rows | A version 21 database with feeds starts with the same feeds and items in `client.db`, and `claire.db` has no feed table. A second start changes nothing. |
| DS-2 | Tests compare with the version constants (Section 3.1) | No test pins a version number. |
| DS-3 (later) | RD29 rules 1 and 2 in the ingest and collaborator code | An ingest of a collaborator without an invitation is refused; the admin limit applies. |
