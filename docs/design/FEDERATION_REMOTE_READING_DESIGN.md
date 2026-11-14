# Federation Client and Remote Reading Design (Phase FP4)

- **Document Identifier**: `DESIGN-FEDERATION-REMOTE-READING-20261113`
- **Status**: Draft. Owner decisions RD1 to RD11 and RD13 to RD29 are locked (2026-11-13). RD12 is deferred (Section 13). The visual prototype is [`prototypes/federation_client_prototype.html`](prototypes/federation_client_prototype.html). No work unit starts before the technical items of its unit are confirmed (Section 12).
- **Date**: 2026-11-13 · Base Commit `dad0f28c`
- **Revises**: [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) §1.4 (non-goal "Reading remote content through the native actor"), §11 item 3 (no remote HTML, replies, likes, and announces not stored), and §14.1 row FP4.
- **Carries over**: [`SYNDICATION_ENDPOINT_DESIGN.md`](SYNDICATION_ENDPOINT_DESIGN.md) §3.5.1 principle 2 (collection is not ingestion) and the role of the prototype tab "🌐 연합 클라이언트" (Section 1.3).
- **Related Documents**:
  - [`PRINCIPLES.md`](../PRINCIPLES.md)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (K10, §15 S1, D1)
  - [`FEDERATION.md`](../implementation/FEDERATION.md) (current behavior of FP1 to FP3)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]** (confirmed in source at the base commit), **[decision]** (owner policy), **[unverified]** (not confirmed; do not cite as fact). This document adds **[external]**: a statement from a public source that Section 3 names, read on 2026-11-13.

---

## 1. Objective and Scope

### 1.1 Objective

1. An account that is an actor can follow a remote actor. The remote actor can be a person or a group (for example a forum category or a community).
2. Claire stores the public posts and the boosts of the followed actors and shows them to the account owner.
3. Remote HTML renders on the `usercontent` origin only (K10). Remote media goes through the `media` host (RD3).
4. The admin can connect object storage and can move the local media to it (RD3).
5. The open item of FP3 closes: a follower removal by the owner sends `Reject(Follow)`.
6. The account owner communicates with remote users in both directions from `/syndication`: reply, quote, favourite, and boost (RD10, RD11, Section 5.4). Replies and mentions from remote users to the account come back to the same view (Section 6.6).
7. `/syndication` has the federation client (Section 1.3). The client is the first collection point and the place of all external activity of the account.

### 1.2 Non-Goals (FP4)

- Theme actors and group hosting. Section 4.2 records them as ideas without a design.
- Media attachments in notes, polls that Claire makes, direct messages, and followers-only posts (RD11). The one exception is the automatic notice of Section 6.2 rule 4 (RD16).
- Backfill of the old posts of an actor (crawl of its outbox). The context of a thread is in scope (Section 6.5).
- A Mastodon-compatible client API.
- Full-text search and knowledge graph extraction of remote posts. An owner can ingest one item into a theme with the existing ingest flow (Section 4.4).

### 1.2.1 Deferred Work (RD17)

The owner decision (2026-11-13): the basic implementation comes first. Invitations of users stay limited, so these items wait. Each one is a later task, not a non-goal.

| Item | Content |
| :--- | :--- |
| D1 Per-account moderation | Block and mute of a remote actor by the account, outbound reports (`Flag`). Until then: instance domain blocks only. |
| D2 Outbound limits per account | Rate limits for notes, replies, favourites, boosts, and follows of one account, to protect the reputation of the instance. |
| D3 Internationalization of the client | **No longer deferred (I18N_DESIGN.md ID5, 2026-11-13).** Each new screen, panel, and message of FP4 uses the i18n catalog keys from the start, with English and Korean texts in `static/locales/{en,ko}.json`. The `/syndication` page and the syndication console panels are localized as part of their rebuild in FP4 (not by IP2-2 or IP2-3). |
| D4 Connected external accounts | RD12. |

### 1.3 Federation Client in `/syndication` (RD10)

The owner decision (2026-11-13): the federation client is in `/syndication`. It is the function for two-way communication and for the first collection. It is separate from ingestion. The client does all external activity of the account: it takes collected posts into the knowledge base, and it publishes ingested documents.

| Stage | Owner of the data | Where | What enters | What leaves |
| :--- | :--- | :--- | :--- | :--- |
| Collection (수집) | Account | Federation client in `/syndication` | Posts of followed actors, boosts, replies, mentions (Sections 6, 6.6) | Replies, favourites, boosts (Section 5.4) |
| Ingestion (적재) | Theme | Knowledge base (`/syndication` knowledge base view, `/ingest`) | Only an item that the owner takes from the client (Section 4.4) | Nothing goes out directly |
| Publication (게시) | Account and theme | Federation client (the dispatch dialog of FP3-5) | An ingested document | `Article` with the summary and the share link to followers (FP3, RD21, Section 5.5) |

Rules:

1. Collection is not ingestion. A received post stays in the client and never enters a theme without an explicit action of the owner (Section 4.4).
2. The knowledge base sends nothing to remote servers by itself. Each outbound activity starts from an action of the owner in the client: a reply, a favourite, a boost, a publish, or a retract.
3. The client and the knowledge base are two views of `/syndication`, not two pages. A separate viewer page is not used.
4. The client is per account (P1). A theme switch changes the knowledge base view and the publish targets. It does not change the timeline.
5. The connected-account client model of [`SYNDICATION_ENDPOINT_DESIGN.md`](SYNDICATION_ENDPOINT_DESIGN.md) (Mastodon client API and AT Protocol through external accounts) has no backend at the base commit. RD12 decides whether the client also shows those accounts.
6. **Navigation (RD14).** The page uses the stage pipeline N2: a bar with the three stages 1 collection (수집), 2 ingestion (적재), and 3 publication (게시), each with its counts. Stage 1 opens the client. Stages 2 and 3 open the knowledge base view; stage 3 is its filter of unpublished and published documents with their reactions. Reason: the bar shows a new user the model of the system at first sight. The page also has a command palette, a flow record, and links between a post and its document. The prototype keeps the rejected proposals N1 (workspace rail) and N3 (two views side by side with drag and drop) for reference.
7. **Feeds (RD13, RD15).** RSS 2.0, Atom 1.0, and JSON Feed subscriptions are also first collection of the account, in the same client. **Baseline goal:** a Tiny Tiny RSS user moves the subscription list into Claire with OPML. Thus OPML import is required: each `outline` with `xmlUrl` becomes a feed, nested category outlines become folders (one level of folders in the first version; deeper levels join their names), and `title` or `text` is the feed title. Outlines that are not feeds (for example settings, filters, or labels that a reader application adds; **[unverified]** the exact names in a Tiny Tiny RSS export) are skipped and listed in the import report. A feed that already exists is not added again. The import report gives counts of added, skipped, and failed outlines. **Feeds work in single mode and in network mode** (RD15). They need no actor and no federation switch; in single mode the client shows the feeds and no actor tree. The auto-ingest rule of a feed is not part of the baseline; it is a later proposal. A feed has no interaction target: a "share" of a feed item is a new note with the link (the rule of [`SYNDICATION_ENDPOINT_DESIGN.md`](SYNDICATION_ENDPOINT_DESIGN.md) for quotes of RSS items). The subscribe dialog finds the feeds of a site (`<link rel="alternate">`) and an ActivityPub actor of the same site; when an actor exists, the client offers a follow instead. A feed of an actor that the account follows is merged by item URL with the timeline. Polls use conditional `GET` (`ETag`, `Last-Modified`), WebSub when the feed has a hub, backoff on `429` and `5xx`, and the private address check. `<category>` values stay raw tags until ingestion. A feed can have an auto-ingest rule to one theme: a standing rule that the owner sets, off by default.

---

## 2. Facts at the Base Commit

1. **[code] Account actor.** The actor is per account (`/ap/actors/{public_id}`). The actor rule FK4 applies: federation `on`, network mode, status `active`, `profile_public` = 1.
2. **[code] Syndication tables have no writer.** `claire.db` has `syndication_accounts`, `syndication_targets`, `syndication_items`, and `syndication_dispatches`. No code writes `syndication_items` or `syndication_targets`. The live page `/syndication` (`syndication_live.js`) reads theme documents (`/documents`, `/graph`), dispatches, `/api/federation/status`, and the publish routes. It shows no remote post.
3. **[code] Theme scope.** `syndication_items.theme_id` is `NOT NULL` and refers to `themes(id)`. The items are per theme. The federation tables are per account.
4. **[code] Outbox task kinds.** `federation_outbox.kind` has `CHECK (kind IN ('create', 'update', 'delete', 'update_actor', 'delete_actor'))`. SQLite cannot change a `CHECK` constraint in place.
5. **[code] Sidecar inbox.** One listener (`.on(Activity, …)`) sends every verified activity to Claire as an inbox event. Claire applies `Follow`, `Undo(Follow)`, `Delete` of a remote actor, and `Update` of a remote actor. All other types are `ignored`. Claire sends no `Follow`.
6. **[code] Media store.** `store/media.py` has `LocalMediaStore` (`data/media`) and `S3MediaStore` (SigV4 with `httpx`, no SDK). `get_media_store` selects S3 only from the environment variables `CLAIRE_MEDIA_S3_*`. Only the `media` host uses the store, and only to serve. The `media` host also serves legacy files from `data/images`. There is no admin setting and no migration between the stores.
7. **[code] Usercontent.** `api/usercontent.py` sanitizes HTML with `nh3`, serves it with a sandbox CSP and `frame-ancestors` of the app host, and accepts a viewing token bound to `(account_id, theme_id, doc_id, visibility_epoch, exp)`.
8. **[code] Private networks.** Fedify refuses private and loopback addresses (FT4). Claire has the setting `allow_private_networks` for its own fetchers.
9. **[code] Published objects.** `federation_objects` has `theme_id` and `document_id` as `NOT NULL` (`store/db.py`, v18). Each published object is one document. A reply or another short note of the account has no row type now.
10. **[code] Prototype client.** The tab "🌐 연합 클라이언트" exists only in `/prototype/syndication` (`syndication_client.js`, mock data). The live `/syndication` (`syndication.html`, `syndication_live.js`) has the knowledge base view only.
11. **[code] Quote vocabulary.** The sidecar lock file resolves `@fedify/vocab` 2.4.5. That version has the classes `QuoteRequest` and `QuoteAuthorization`, the `quoteAuthorization` property, and the interaction policy `canQuote`. The sidecar uses none of them at the base commit.

---

## 3. Fediverse Group Research (2026-11-13)

The owner asked for a proposal for groups based on the implementations and the trend in the fediverse (RD1). This section gives the findings.

| Item | Finding | Source |
| :--- | :--- | :--- |
| FEP-1b12 Group federation | **[external]** Status FINAL since 2023. A `Group` actor receives activities in its inbox and sends them again as `Announce` to its followers. Each group is a forum with threads and comments. | [FEP-1b12 on SocialHub](https://socialhub.activitypub.rocks/t/fep-1b12-group-federation/2724), [FEP repository](https://codeberg.org/fediverse/fep) |
| FEP-1b12 implementations | **[external]** NodeBB uses it for categories only. A NodeBB category can also follow a remote group. Pixelfed groups use FEP-1b12 with FEP-400e. WordPress ActivityPub supports it in part. **[unverified]** Lemmy, PieFed, and Mbin: no primary source was read. | [NodeBB 1b12](https://docs.nodebb.org/activitypub/fep/1b12/), [Pixelfed](https://docs.pixelfed.org/spec/ActivityPub.html), [WordPress FEDERATION.md](https://github.com/Automattic/wordpress-activitypub/blob/trunk/FEDERATION.md) |
| SWICG Groups Task Force | **[external]** "Initial CG Draft" of 2026-05-28, changed on 2026-06-25. Membership is separate from following. `Join` is not specified yet. The names of the interaction policy (`canJoin`, `canReply`) are not final. The draft lists no implementation. A support matrix was proposed in September 2026 and is not published. | [Groups draft](https://swicg.github.io/groups/), [swicg/groups #63](https://github.com/swicg/groups/issues/63) |
| Mastodon | **[external]** Version 4.6 (June 2026) added Collections: curated lists of at most 25 accounts, federated with FEP-7aa9. No source shows native groups in Mastodon. | [Mastodon 4.6 for developers](https://blog.joinmastodon.org/2026/06/mastodon-4-6-for-devs/) |
| Dedicated group software | **[external]** Bonfire showed federated groups and federated comments at FediForum (April 2026). Friendica has group pages. | [FediForum 2026-04](https://fediforum.org/2026-04/) |
| Conversation context | **[external]** FEP-7888 (`context`) and FEP-f228 (backfill, draft). Fedify has `@fedify/backfill` since 2.3.0. | [FEP-f228](https://fediverse.codeberg.page/fep/fep/f228/), [Fedify backfill](https://fedify.dev/manual/backfill) |

Conclusions:

1. A microblog user takes part in a group with `Follow` of the `Group` actor and gets the posts as `Announce`. This is stable (FEP-1b12) and has several implementations.
2. Membership, `Join`, and moderation policies are not stable (SWICG draft, mid-2026). A group design based on them can change.
3. Thus a Claire account can read groups now: FP4 receives `Announce` (RD2). No group hosting is necessary for this.

---

## 4. Storage Model and Theme Role (RD1)

The owner policy (2026-11-13, provisional): ActivityPub support is per account. A theme is a group function of Claire Bible only.

### 4.1 Decisions

The owner locked RD1 on 2026-11-13: P1 and P2 as proposed, P3 and P4 as ideas without a design (Section 4.2), and P5 as a wider cleanup (Section 4.3).

| ID | Decision | Reason |
| :--- | :--- | :--- |
| P1 | Follows and received posts belong to the account. FP4 adds the account tables `federation_following` and `federation_timeline_items` (Section 7). FP4 does not use the `syndication_*` tables. | The actor is per account (Section 2 item 1). A theme column has no meaning for a post that an account receives. |
| P2 | A theme gets a remote post only by an explicit ingest by the owner. The ingest uses the existing ingest flow with the post URL and records `linked_theme_id` and `linked_doc_id` on the item. | The theme stays a Claire function. The knowledge base keeps its review and purge rules. |
| P3 | Theme actors stay out of scope (FD12 is unchanged). A theme as a `Group` actor is an idea only (Section 4.2). | It is not clear that a theme will become a real group actor. |
| P4 | No phase FP5 is planned. A design starts only when the owner starts it (Section 4.2). | The idea is far from a design. |
| P5 | Remove the theme-scoped `syndication_*` schema that has no product use, and rebuild `syndication_dispatches` without its references to the removed tables (Section 4.3). | One production server; the syndication feature was never used there (owner, 2026-11-13). Owner policy: remove schema that is not used. |

### 4.2 Ideas Without a Design (P3, P4)

This section is the answer when someone asks about these features. Each item is an idea of the owner. None has a design, a work unit, or a schema. Do not implement any of them from this section.

| Idea | Content | State and what it needs first |
| :--- | :--- | :--- |
| I1 Theme as a group | A theme becomes a `Group` actor (FEP-1b12 subset): remote users follow the theme, and the theme sends its published documents as `Article` and `Announce`. | Not designed. It is not clear that a theme becomes a real group actor. Needs an owner decision and a design revision of this document. Possible triggers: the SWICG Groups report specifies `Join` and the policy names (Section 3), or the owner wants the stable FEP-1b12 subset. |
| I2 Exchange between Claire Bible servers only | Claire Bible servers exchange content (for example themes or published documents) with ActivityPub among themselves, not with the open fediverse. | A concept only. No protocol profile, no trust model, no discovery rule. Needs a design of its own. |
| I3 Bodies published by other users of the same instance | The prototype feature "다른 사용자 발행 본문" of [`SYNDICATION_ENDPOINT_DESIGN.md`](SYNDICATION_ENDPOINT_DESIGN.md) §3.9. | Proposal state in that document. Depends on per-user workspaces. Not part of FP4. |

Rules for a question about these ideas: say that the item is an idea without a design; give the state from this table; do not describe tables, routes, or activities for it as if they exist.

### 4.3 Schema Cleanup of `syndication_*` (P5)

Facts at the base commit:

| Table | Product use | Scope |
| :--- | :--- | :--- |
| `syndication_accounts` | **[code]** Store functions (`create_syndication_account`, token save and load) are called only by tests. No route. | Theme |
| `syndication_targets` | **[code]** No writer. | Theme |
| `syndication_items` | **[code]** No writer. Theme deletion and account purge list it for cleanup. | Theme |
| `syndication_dispatches` | **[code]** Written and read by the live `/syndication` (`POST /syndication/dispatch`, `GET /syndication/dispatches`) for the dispatch of a document to another theme. Columns `item_id` and `account_id` refer to `syndication_items` and `syndication_accounts`. | Theme |

Changes (done as `claire.db` version 20 on 2026-11-13; see [`SCHEMA_VERSIONING.md`](../implementation/SCHEMA_VERSIONING.md)):

1. Drop `syndication_targets`, `syndication_items`, and `syndication_accounts`, with their indexes, store functions, and tests. Remove them from the cleanup lists of theme deletion and account purge.
2. Rebuild `syndication_dispatches` without `item_id` and `account_id` (A3: copy all rows with the same IDs). The cross-theme dispatch keeps its behavior.
3. Safety: an operator of the server does not manage the database directly (owner, 2026-11-13). Thus the migration never stops the start and never deletes rows. It drops an empty table. It keeps a table that has rows under the name `<table>_retired_v20` and logs a warning with the row count. Theme deletion and account purge delete the rows of these tables by `theme_id`.
4. Account feeds (RD13) and connected external accounts (RD12, option B) get new account-scoped tables in their own units. They do not use the old tables.
5. Documents: `SCHEMA_VERSIONING.md`, `API_HTTP_CONTRACT.md`, and [`SYNDICATION_ENDPOINT_DESIGN.md`](SYNDICATION_ENDPOINT_DESIGN.md) (its Section on the `syndication_*` schema gets a note that this design replaces it).

### 4.4 Ingest of One Item

The route `POST /api/federation/timeline/{item_id}/ingest` with `theme_id` starts the existing URL ingest for the item `url` (or `object_iri` when `url` is absent). The owner must be able to write to the theme. The item stays in the timeline. The theme rules (visibility, purge) apply to the document, not to the item.

---

## 5. Following

### 5.1 Rules

1. Only an account that is an actor can follow (RD4). When the account stops being an actor, Claire stops to apply posts for it (Section 6.4) and keeps the rows. When the account is an actor again, the follows apply again.
2. The owner gives a handle (`@name@host`) or an actor IRI. The sidecar resolves it (RT1).
3. A follow has the states `pending`, `accepted`, and `rejected`. A remote server that approves follows by hand keeps the follow `pending` until it sends `Accept`.
4. A blocked domain cannot be followed. A new domain block changes the follows of that domain to `rejected` and makes `undo_follow` tasks.
5. Limit: at most `following_limit` follows for each account (Section 10, default 1000, RD8). The admin can increase it. The limit has three reasons: each follow adds one item for each post to each following account (storage and fan-out work), each follow is a delivery to a remote server, and a mass follow from one instance is a known spam pattern that causes other servers to block the instance.
6. Unfollow sends `Undo(Follow)` and deletes the row. The received items stay (RD5 gives their retention).
7. The `following` collection of the actor gives `totalItems` only, the same as the followers collection (RD9).

### 5.2 Outbox Task Kinds

FP4 adds these task kinds to `federation_outbox`:

| Kind | Made by | Activity |
| :--- | :--- | :--- |
| `follow` | `POST /api/federation/following` | `Follow` with the ID `{actor}#follows/{following_id}` |
| `undo_follow` | `DELETE /api/federation/following/{id}`, a new domain block | `Undo(Follow)` of the same ID |
| `reject_follow` | `DELETE /api/federation/followers` (FP2-4) | `Reject(Follow)` to the removed follower |
| `fetch_context` | Thread view of an item (Section 6.5) | No activity. The sidecar fetches the thread and sends the objects to Claire. |
| `note` | `POST /api/federation/notes` (Section 5.4) | `Create(Note)` |
| `update_note` | `PATCH /api/federation/notes/{id}` (Section 5.4 rule 12) | `Update(Note)` |
| `auto_reply` | Inbound non-public message (Section 6.2 rule 4) | `Create(Note)` addressed to the sender only |
| `delete_note` | `DELETE /api/federation/notes/{id}` | `Delete(Note)`, then a tombstone |
| `like`, `undo_like` | `POST`, `DELETE /api/federation/timeline/{item_id}/favourite` | `Like`, `Undo(Like)` |
| `announce`, `undo_announce` | `POST`, `DELETE /api/federation/timeline/{item_id}/boost` | `Announce`, `Undo(Announce)` |
| `quote_request` | `POST /api/federation/notes` with `quote_of` | `QuoteRequest` to the author of the quoted object, then `Create(Note)` with the quote |
| `quote_authorize`, `quote_reject` | Inbound `QuoteRequest` (Section 6.6), or the owner decision | `Accept` with a `QuoteAuthorization`, or `Reject` |
| `quote_revoke` | The owner revokes an authorization | `Delete` of the `QuoteAuthorization` |

The task has a new column `target_iri` for the remote actor or the remote object. The way to widen the `CHECK` constraint is technical item RT3.

### 5.3 Routes on Claire

| Method | Path | Access | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/federation/following` | `owner`, network mode | Follows of the own account, cursor pages |
| `POST` | `/api/federation/following` | `owner`, account is an actor | Follow a handle or an IRI |
| `DELETE` | `/api/federation/following/{id}` | `owner` | Unfollow |
| `GET` | `/api/federation/timeline` | `owner` | Items of the own account, cursor pages, filter `unread`, `starred` |
| `PATCH` | `/api/federation/timeline/{item_id}` | `owner` | `unread`, `starred` |
| `POST` | `/api/federation/timeline/{item_id}/ingest` | `owner` with write access to the theme | Section 4.4 |
| `POST` | `/api/federation/notes` | `owner`, account is an actor | Section 5.4 |
| `PATCH` | `/api/federation/notes/{id}` | `owner`, account is an actor | Section 5.4 rule 12 |
| `DELETE` | `/api/federation/notes/{id}` | `owner` | Section 5.4 |
| `POST`, `DELETE` | `/api/federation/timeline/{item_id}/favourite` | `owner`, account is an actor | Section 5.4 |
| `POST`, `DELETE` | `/api/federation/timeline/{item_id}/boost` | `owner`, account is an actor | Section 5.4 |

### 5.4 Outbound Interactions (RD10, RD11)

1. **Reply.** The owner writes a reply to an item. Claire stores a note of the account (`federation_notes`, Section 7.1) and makes a `note` task. The sidecar sends `Create(Note)` with `inReplyTo` = the item `object_iri`, `to` = Public, `cc` = the followers collection and the author of the parent, and a `Mention` tag for the author of the parent. A reply to a thread of a group also goes to the group (FEP-1b12, the `audience` of the parent).
2. **New note.** The same route without `in_reply_to` makes a public note that is not a reply. Mentions in the text (`@name@host`) are resolved by the sidecar and become `Mention` tags and recipients.
3. **Content.** The owner enters plain text. Claire makes the HTML (paragraphs, links, mentions) and sanitizes it with `nh3`. The limit is `note_max_chars` (Section 10, proposed default 5000). Media attachments are out of scope for the first version of this unit (proposal).
4. **Favourite and boost.** A `like` or `announce` task refers to the item `object_iri`. The item records `favourited` and `boosted`. An undo makes the `undo_*` task with the same activity ID.
5. **Quote (FEP-044f).** A note can quote one object (`quote_of`). The sidecar sends `QuoteRequest` to the author of the quoted object and then `Create(Note)` with the quote. The note shows the quote as `pending` until the `QuoteAuthorization` arrives, `approved` after it, and `refused` after a `Reject`. When the author deletes the authorization later, the quote becomes `revoked` and the note shows a link only. A server that does not answer leaves the quote `pending`; the note shows a link to the quoted object.
6. **Quote policy of own objects.** Each own note and each published document has `quote_policy` (`canQuote`): `public` (automatic approval for all), `followers` (automatic approval for followers, else refused), `manual` (the owner approves in the client), or `nobody`. The default is `public` for a note and `manual` for a published document (proposal). A change sends `Update` of the object.
7. **Delete.** The owner can delete an own note. The note becomes a tombstone (`federation_tombstones`, kind `object`) and the object IRI gives 410, the same as a retract (FP3).
8. **Object path.** A note has the IRI `/ap/objects/{public_id}` in the same number space as the published documents (RT7). The note is in the outbox collection of the actor.
9. **Own notes in the thread.** An own note is also an item of the own timeline (`in_timeline` = 1 for a reply in a thread that the account reads), so the thread view shows it.
10. **Ingest of a note.** An own note is not ingested automatically. The owner can ingest it with Section 4.4 like any other item (Section 1.3 rule 1).
11. Visibility is public only (parent design §1.4). A note is not sent when the account is not an actor; the route gives 409 `not_actor`.
12. **Composition fields (RD11, 2026-11-13).** All are required for notes:
    - **Edit.** The owner can change the text, the content warning, the language, and the tags of an own note. Claire sends `Update(Note)` with `updated`. The client shows "edited".
    - **Content warning.** An optional `summary` with `sensitive` = true. The client shows the summary first, as for inbound items (Section 8 rule 4).
    - **Language.** The note has `contentMap` with one language tag. The default is the locale of the account (`accounts.locale`), else the instance default. The owner can change it per note.
    - **Hashtags.** A `#tag` in the text becomes a `Hashtag` tag with a link, and a link in the HTML. FD8 (no hashtags) stays for published documents (`Article`) only.

### 5.5 Content of a Published Document (RD21)

The owner decision (2026-11-13): the ActivityPub object of a published document has the summary of the document and its share link. It does not have the body. The body stays on Claire: a remote user reads it on the share page.

Facts at the base commit **[code]**: the object path builds `content` from the compiled body (`detail_html`, else `render_to_html(detail, detail_format)`) and sanitizes it with `nh3` (`api/federation_routes.py`, `published_object`). The publish route makes or reuses the share of the document before the object row (`dbm.create_doc_share(..., reuse_existing=True)`, `api/accounts_routes/federation.py`), and the object `url` is the share page `/p?s={token}`. The summary of a document is in the latest extraction (`dbm.latest_extraction_summary`); `documents` has no summary column.

Rules:

1. **Content.** `content` is HTML with two parts: the summary as one paragraph (`<p>`, text escaped, no markup from the body), then a paragraph with the link to the share page (`<a href="{share url}">`). The language rule of Section 5.4 rule 12 applies: the sidecar sends `content` and `contentMap` (RT8).
2. **Summary source.** The summary is `dbm.latest_extraction_summary` of the document. A document without a summary sends the title as the first paragraph. The summary is plain text; the length limit is `article_summary_max_chars` (proposal: 500 characters, cut at a word end with "…").
3. **Share link.** The object always has a share link: `url` and the link in `content` are the same share page. The publish route makes the share when it does not exist (as now). A published document whose share has expired or was deleted gets a new share at the next publish or update; until then the object path gives the object without the link paragraph and without `url` (as now for `url`).
4. **Fields.** `type` stays `Article`. `name` is the title. `summary` (the ActivityPub content warning field) is not set: the document summary goes in `content`, so that Mastodon shows it as the post text. FD8 (no hashtags) stays.
5. **Updates.** A change of the summary or of the share link makes an `update` task, as a change of the document does now (FP3). A change of the body alone sends no `Update`, because the object does not contain the body.
6. **Body format.** The body format (AsciiDoc or old Markdown, RD22) has no effect on the object. Claire does not render a body for federation.
7. **Already published objects.** The new content applies to the next answer of the object path; no migration is necessary. The next `update` task of a document sends the new form to the followers. Claire does not send `Update` to all followers for old objects at the change (a burst of deliveries without a change that the owner made).

Work unit: FP4-P1 (Section 14).

---

## 6. Inbound Posts

### 6.1 Applied Activities

FP4 adds these inbox event types. The internal API contract version becomes 1.3.

| Activity | Condition | Effect |
| :--- | :--- | :--- |
| `Accept(Follow)` | The `Follow` ID is a follow of the recipient | State `accepted` |
| `Reject(Follow)` | The same | State `rejected` |
| `Create(Note)`, `Create(Article)`, `Create(Page)`, `Create(Question)` | The author is a followed actor. The object is public (Section 6.2). A reply is stored too (RD6, Section 6.5). | New item |
| `Update` of such an object | An item with the object IRI exists, and the author is the same | Item changed |
| `Delete` of such an object, or `Tombstone` | The same | Item deleted (`federation_timeline_items` row and the media cache entries) |
| `Announce` | The announcer is a followed actor (a person or a group). The sidecar fetches the object with Fedify (RT2). The object passes the same rules as `Create`. | New item with `announced_by_iri`. A second `Announce` of the same object by another followed actor changes nothing. |
| `Undo(Announce)` | The announcer is the same | Item deleted if no other announcer and the author is not followed |

All other types stay `ignored`. `Page` is the post type of FEP-1b12 forums. `Question` is stored as text without votes.

### 6.2 Addressing

1. An object is public when `to` or `cc` has `https://www.w3.org/ns/activitystreams#Public` (also the short forms `as:Public` and `Public`).
2. Followers-only and direct objects are not stored. The sidecar gives them to Claire as `ignored` with the reason `not_public`. Claire has no visibility model for them in FP4.
3. A domain block applies to the author, to the announcer, and to the host of the object IRI.
4. **Automatic notice for non-public messages (RD16).** When a followers-only or direct object is addressed to the account or mentions it, Claire does not store it and sends one automatic reply to the sender: a short fixed text that says that this account cannot receive non-public messages and asks for a public mention. Rules:
    - The reply is a `Note` with `inReplyTo` = the object, `to` = the sender only, and a `Mention` of the sender. It is not public and not in the outbox collection. It is the only non-public object that Claire sends.
    - At most one reply for each sender and account in `auto_reply_hours` (Section 10, default 24). Claire keeps the sender IRI and the time of the last reply only; it keeps no content.
    - No reply to an actor of type `Service` or `Application`, to an object that is a reply to an automatic reply of Claire, or to a blocked domain. These rules stop reply loops between automatic systems.
    - The text uses the locale of the account (English or Korean; D3 applies to other languages).
    - The client counts these messages ("비공개 메시지 n건 · 자동 안내함") without their content.

### 6.3 Item Content from the Sidecar

The sidecar sends a normal form, not the raw JSON-LD:

```json
{
  "object_iri": "https://remote.example/notes/1",
  "url": "https://remote.example/@bob/1",
  "type": "Note",
  "author": {"iri": "https://remote.example/users/bob", "name": "Bob", "handle": "@bob@remote.example"},
  "announced_by": {"iri": "https://forum.example/c/rust", "name": "Rust", "handle": "@rust@forum.example"},
  "name": null,
  "summary": "content warning text or null",
  "sensitive": false,
  "content_html": "<p>…</p>",
  "language": "en",
  "published_at": "2026-11-13T09:00:00Z",
  "updated_at": null,
  "attachments": [{"url": "https://remote.example/media/1.png", "media_type": "image/png", "name": "alt text", "width": 800, "height": 600, "blurhash": "…"}],
  "context": "https://forum.example/t/42"
}
```

Limits (sidecar and Claire): `content_html` at most 64 KiB (proposed), at most 8 attachments (proposed). Claire refuses a larger event with `rejected` and the reason `too_large`.

### 6.4 Fan-Out

1. A delivery to the shared inbox has no recipient. Claire finds all accounts that follow the author or the announcer and that are actors. It makes one item for each such account.
2. An account that is not an actor gets no item.
3. The event is idempotent: the item key is `(account_id, object_iri)`.

### 6.5 Replies and Thread Context (RD6)

The owner decision is automatic collection, as in Mastodon. Mastodon behavior that this section copies:

- **[unverified]** Mastodon stores the replies that it receives and fetches the parent of a reply (`inReplyTo`). Its home feed shows a reply only when the parent author is the reader, the author, or an account that the reader follows. Other replies show in the thread view.
- **[external]** Mastodon 4.4 added an opt-in fetch of the missing replies of a remote post, set by the admin. Mastodon 4.5 uses it in the context API. Sources: [Fetching replies on Mastodon](https://blog.thms.uk/2025/07/fetching-replies-mastodon/), [Mastodon 4.5 for developers](https://blog.joinmastodon.org/2025/10/mastodon-4-5-for-devs/).

Rules:

1. A reply from a followed actor, or a reply that a followed group announces (a forum comment), is an item.
2. **Parent chain.** When an item is a reply, the sidecar fetches the parents with `inReplyTo` up to `context_depth` levels (Section 10). Each parent that passes the rules of Section 6.2 is an item with `in_timeline` = 0.
3. **Timeline rule.** An item has `in_timeline` = 1 when it is not a reply, or when the parent author is the author, the account itself, or a followed actor. All other items have `in_timeline` = 0. The timeline list shows `in_timeline` = 1 only. The thread view shows all items of the thread.
4. **Thread fetch.** When the owner opens the thread view of an item, Claire makes a task `fetch_context` (Section 5.2). The sidecar reads the FEP-7888 `context` collection of the object when it is available (FEP-f228), else the `replies` collections, with `@fedify/backfill` (RT6). The limit is `context_max_items` objects for each task. A second task for the same thread within `context_refetch_minutes` is not made.
5. The sidecar gives the fetched objects to Claire with the internal endpoint `context-objects`. Claire applies the same rules as for `Create` and stores them with `in_timeline` = 0.
6. The admin can set `context_fetch` to `off`. Then rules 2 and 4 do not apply, and rule 1 still applies.

### 6.6 Inbound Interactions with the Account (RD10)

Two-way communication needs the answers of remote users who are not followed.

1. A public `Create` whose object has a `Mention` of the account, or whose `inReplyTo` is an object of the account (a note or a published document), is an item for that account even when the author is not followed. The item has `mention` = 1 and `in_timeline` = 1.
2. The domain block and the addressing rules of Section 6.2 apply. A followers-only or direct mention is not stored (`not_public`).
3. `Like` and `Announce` of an object of the account increase the counters of the object (`federation_notes` or `federation_objects`). Claire stores the actor IRI of each such activity once, so that `Undo` decreases the counter. These activities make no item.
4. The client shows a mentions filter (`mention` = 1) and the counters on the own notes and the published documents.
5. **Quote requests.** An inbound `QuoteRequest` for an own object applies the `quote_policy` of the object (Section 5.4 rule 6). Automatic approval makes a `quote_authorize` task. `manual` makes a pending request in the notifications of the client; the owner approves or refuses it. A `QuoteAuthorization` is dereferenceable at `/ap/quote-authorizations/{id}` and does not embed the quoting object (FEP-044f). The owner can revoke an authorization (`quote_revoke`).
6. Limit: `mention_rate_per_hour` items for each remote domain and account (Section 10, proposed default 100). Events above the limit are `ignored` with the reason `rate_limited`.

---

## 7. Data Model

### 7.1 Client Tables (`client.db`, RD27)

**Changed by RD27 (2026-11-13, [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md)):** the client tables below are in the new file `client.db` with its own version, not in `claire.db` version 22. `REFERENCES accounts(id)` below is a reference by value (no foreign key across files). The client outbox (`federation_client_outbox`: the kinds `follow` to `quote_revoke` of Section 5.2, with `target_iri`) is in `client.db`, so that a client action and its task are in one transaction. The publish outbox (`federation_outbox`) stays in `claire.db` with its kinds of version 18; RT3 (a rebuild of `federation_outbox`) is no longer necessary. `federation_objects.quote_policy` (FP4-9Q) stays a `claire.db` change.

FK10 applies: each table comes in the work unit that first reads it.

```sql
CREATE TABLE federation_following (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    request TEXT NOT NULL,            -- handle or IRI that the owner gave
    actor_iri TEXT,                   -- NULL until the sidecar resolves it
    actor_type TEXT,                  -- 'Person', 'Group', 'Service', 'Application', 'Organization'
    inbox_iri TEXT,
    shared_inbox_iri TEXT,
    display_name TEXT,
    state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'rejected')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (account_id, actor_iri)
);
CREATE INDEX idx_federation_following_actor ON federation_following(actor_iri);

CREATE TABLE federation_timeline_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    object_iri TEXT NOT NULL,
    url TEXT,
    object_type TEXT NOT NULL,
    author_iri TEXT NOT NULL,
    author_json TEXT NOT NULL,
    announced_by_iri TEXT,
    announced_by_json TEXT,
    name TEXT,
    summary TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    content_html TEXT NOT NULL,       -- raw; sanitized only at render (Section 8)
    language TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    context_iri TEXT,
    in_reply_to_iri TEXT,
    in_timeline INTEGER NOT NULL DEFAULT 1,
    published_at REAL,
    updated_at REAL,
    received_at REAL NOT NULL,
    unread INTEGER NOT NULL DEFAULT 1,
    starred INTEGER NOT NULL DEFAULT 0,
    linked_theme_id INTEGER,
    linked_doc_id TEXT,
    mention INTEGER NOT NULL DEFAULT 0,
    quote_of_iri TEXT,
    favourited INTEGER NOT NULL DEFAULT 0,
    boosted INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, object_iri)
);
CREATE INDEX idx_federation_timeline_account ON federation_timeline_items(account_id, received_at DESC);
CREATE INDEX idx_federation_timeline_object ON federation_timeline_items(object_iri);
CREATE INDEX idx_federation_timeline_thread ON federation_timeline_items(account_id, context_iri);

CREATE TABLE federation_account_settings (
    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
    retention_days INTEGER CHECK (retention_days IS NULL OR retention_days >= 1),  -- NULL: the instance value
    updated_at REAL NOT NULL
);

CREATE TABLE federation_notes (
    public_id INTEGER PRIMARY KEY,    -- same number space as federation_objects (RT7)
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    in_reply_to_iri TEXT,
    audience_iri TEXT,
    content_text TEXT NOT NULL,
    content_html TEXT NOT NULL,       -- made and sanitized by Claire
    mentions_json TEXT NOT NULL DEFAULT '[]',
    quote_of_iri TEXT,
    quote_state TEXT CHECK (quote_state IS NULL OR quote_state IN ('pending', 'approved', 'refused', 'revoked')),
    quote_policy TEXT NOT NULL DEFAULT 'public' CHECK (quote_policy IN ('public', 'followers', 'manual', 'nobody')),
    summary TEXT,
    sensitive INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    edited_at REAL,
    published_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE federation_auto_replies (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    sender_iri TEXT NOT NULL,
    last_sent_at REAL NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (account_id, sender_iri)
);

CREATE TABLE federation_quote_authorizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_public_id INTEGER NOT NULL,  -- the own object that is quoted
    quoting_object_iri TEXT NOT NULL,
    quoting_actor_iri TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'approved', 'refused', 'revoked')),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (object_public_id, quoting_object_iri)
);

CREATE TABLE federation_object_reactions (
    object_public_id INTEGER NOT NULL,  -- federation_objects or federation_notes
    kind TEXT NOT NULL CHECK (kind IN ('like', 'announce', 'quote')),
    actor_iri TEXT NOT NULL,
    activity_iri TEXT NOT NULL,
    received_at REAL NOT NULL,
    PRIMARY KEY (object_public_id, kind, actor_iri)
);
```

`federation_outbox` gets the new kinds and the column `target_iri` (RT3). `federation_objects` gets `quote_policy` with the default `manual` (Section 5.4 rule 6).

### 7.2 Media Cache Records

```sql
CREATE TABLE federation_media_cache (
    url_hash TEXT PRIMARY KEY,        -- sha256 of the remote URL
    remote_url TEXT NOT NULL,
    store_key TEXT,                   -- NULL until fetched
    media_type TEXT,
    byte_size INTEGER,
    state TEXT NOT NULL CHECK (state IN ('new', 'stored', 'failed', 'too_large')),
    fetched_at REAL,
    last_access_at REAL
);
```

### 7.3 Settings

The reading settings of Section 10 go to `instance_settings` (key `federation_reading`, JSON). The object storage settings go to `instance_settings` (key `media_storage`). The secret key is encrypted with `SecretService` and the additional authenticated data `media_storage:s3`. The secret is never in an API response.

---

## 8. Rendering (K10)

1. The timeline list of the federation client in `/syndication` shows author, announcer, time, the summary, and plain text of the content (tags removed on the server). The list has no remote HTML. Each item has the actions reply, favourite, boost, star, and "take into the knowledge base" (Section 4.4).
2. The body renders in a sandbox `iframe` from the `usercontent` route `GET /render/remote/{item_id}`. The viewing token is bound to `(account_id, item_id, exp)` with a new token kind `remote`.
3. The route sanitizes `content_html` with the `nh3` allowlist of `sanitize_usercontent_html`, rewrites each `img src` and each attachment URL to the `media` proxy URL (Section 9), and removes all other remote resource URLs. Links get `rel="nofollow noopener noreferrer"` and `target="_blank"`.
4. A `sensitive` item or an item with `summary` shows the summary first. The body loads after a click.
5. Development without a `usercontent` host: the S1 fallback applies (`nh3` and nonce CSP on the app origin).

---

## 9. Media Proxy and Object Storage (RD3)

### 9.1 Media Proxy

1. The render route gives each remote media URL as `/media/remote/{url_hash}?t={token}` on the `media` host. The token binds `(account_id, url_hash, exp)`.
2. At the first request, the `media` host fetches the remote URL, stores it in the media store under `remote/{url_hash}`, and records it in `federation_media_cache`. Later requests read the store.
3. Fetch rules: no private or loopback address unless `allow_private_networks` is on (development), redirect limit 3 with the same check on each hop, timeout 15 seconds, no cookie and no credential, `User-Agent` of the instance.
4. Allowed types: `image/*` except `image/svg+xml`, `video/mp4`, `video/webm`, `audio/*`. The size limits are admin settings (Section 10, RD7). A larger file is `too_large`; the render shows a link to the remote URL.
5. Response headers: `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, `Cross-Origin-Resource-Policy: same-site`, `Cache-Control: private`.
6. Eviction: a command removes cache entries with `last_access_at` older than `media_evict_days` (Section 10, RD7). A deleted item removes its entries when no other item refers to the URL.

### 9.1.1 Storage Backends (RD26)

The owner decision (2026-11-13): local storage is the default and needs no setting. S3-compatible object storage is supported, and the reference implementation for support is **Garage** (the open-source S3-compatible store of Deuxfleurs).

1. **Local (default).** `LocalMediaStore` under `data/media`, and the legacy files under `data/images` (RT4). A new server, and a server without object storage settings, uses local storage. Local storage is part of the existing backup of the data directory.
2. **S3-compatible (optional).** `S3MediaStore`. "Supported" means: the tests and the documentation use Garage; another S3-compatible server works when it gives the operations of rule 4, but it is not tested.
3. **One store at a time.** The `api` process uses one store for new writes. Reads look in the active store; with object storage active, a key that is not there is read from local storage (until the migration of Section 9.3 is complete), so that no media goes missing during the migration.
4. **Required S3 operations.** `PutObject`, `GetObject` with `Range`, `HeadObject`, and `DeleteObject`, with AWS Signature Version 4 and the header `x-amz-content-sha256`. Claire does not use object ACLs, bucket policies, versioning, object lock, server-side encryption headers, or multipart uploads (the size limits of Section 10 keep each file small). Claire does not create the bucket: the operator creates the bucket and the key on the storage server.
5. **Garage profile (defaults of the console form).**
    - Addressing: path style (`{endpoint}/{bucket}/{key}`), which `S3MediaStore` uses now **[code]**. Virtual-host style is not used, because Garage needs a root domain setting for it **[unverified]**.
    - Region: the form default is `garage`, the default `s3_region` of Garage **[unverified]**. The value must be the region of the server, because SigV4 signs it. The code default `us-east-1` stays for other servers.
    - Endpoint: the S3 API port of Garage (default `3900`) **[unverified]**; the console accepts `http` only for a private address in development, else `https`.
    - Public URL base: empty by default. Media goes through the `media` host of Claire (Section 9.1 rule 5 headers apply). A public base (for example a Garage website endpoint) is optional and is used only for media of public themes.
6. **Keys.** The S3 object keys are the media keys of Section 9.1 (`remote/{url_hash}`) and the legacy keys `images/{doc_id}_{i}{ext}`. Each key uses only `[A-Za-z0-9/_.-]` (RT5), so that no key needs percent encoding.
7. **Failure.** When the object store is not available, the `media` host answers 503 for a key that is not in local storage and logs the error. The `api` process starts also when the store is not available; the console shows the state of the last test.
8. **Backup.** The backup of the data directory does not copy the bucket. The console and `OPERATIONS.md` say that the bucket needs the backup of the storage server.

### 9.2 Object Storage Settings for the Admin

1. New console section `/admin/storage` and routes `GET`, `PUT /api/admin/storage`, `POST /api/admin/storage/test`.
2. Fields: mode (`local`, the default, or `s3`), endpoint, bucket, region, access key, secret key, optional public URL base. The form has the Garage profile of Section 9.1.1 rule 5 as defaults. The test writes, reads (with a byte range), heads, and deletes one probe object, and reports the first operation that fails. A change from `s3` back to `local` is possible; the media in the bucket stay there and the console says so.
3. Precedence: the environment variables `CLAIRE_MEDIA_S3_*` win over the admin settings. The console shows that the environment sets the store and refuses a change (RD9).
4. A change of the settings takes effect at the next start of the `api` process. The console says so.

### 9.3 Migration from Local Storage

1. Command `claire media migrate --to object-storage` with dry run by default and `--apply`, the same pattern as `artifact-migrate`.
2. Scope: the keys of `LocalMediaStore` (`data/media`) and the legacy files in `data/images` that the `media` host serves (RT4).
3. For each file: upload, read back, compare SHA-256, then record it. The local file is deleted only with `--delete-source` after all files pass.
4. The command can run again. It skips files that are already in the bucket with the same hash.
5. The admin console shows the result of the last run. The console does not start the migration (proposal: long work runs from the command line).

---

## 10. Reading Settings (RD5, RD7, RD8)

The admin sets the instance values in `/admin/federation` with `GET`, `PUT /api/admin/federation/reading`. The account owner sets only the own retention in `/settings/federation`.

| Key | Default | Range | Who |
| :--- | :--- | :--- | :--- |
| `retention_days` | 90 | 1 to 3650 | Admin. The maximum for all accounts. |
| account `retention_days` | NULL (instance value) | 1 to the instance value | Account owner |
| `following_limit` | 1000 | 1 to 100000 | Admin |
| `media_image_max_bytes` | 20 MiB | 1 MiB to 100 MiB | Admin |
| `media_av_max_bytes` | 100 MiB | 1 MiB to 1 GiB | Admin |
| `media_evict_days` | 30 | 1 to 365 | Admin |
| `context_fetch` | `on` | `on`, `off` | Admin |
| `context_depth` | 5 | 0 to 20 | Admin |
| `context_max_items` | 100 | 0 to 1000 | Admin |
| `context_refetch_minutes` | 15 | 1 to 1440 | Admin |
| `note_max_chars` | 5000 | 500 to 100000 | Admin (proposal, RD11) |
| `mention_rate_per_hour` | 100 | 1 to 10000 | Admin (proposal, RD11) |
| `auto_reply_hours` | 24 | 1 to 720 | Admin (RD16) |

Rules:

1. The retention of an account is the lower of the account value and the instance value. When the admin decreases the instance value, the account values that are higher have no effect. Claire does not change them.
2. A retention run deletes the items that are older than the retention by `received_at`, not starred, and not ingested (`linked_doc_id` is NULL). An item with `in_timeline` = 0 that is the parent of a kept item stays.
3. When the admin decreases `following_limit`, the follows that exist stay. The account cannot add a follow until it is below the limit.
4. The retention run and the media eviction run once a day in the `api` process and with the command `claire federation prune`.
5. **Ingested documents are shared assets (RD17, 2026-11-13).** An ingested document belongs to its theme and to the users of the server. No federation client action deletes it: not the retention run, not an unfollow, not a feed removal, not the deletion of the client data of an account, and not an account purge. Only the client database of the account is deleted: `federation_following`, `federation_timeline_items`, `federation_notes` (with `Delete` activities to remote servers), `federation_quote_authorizations` (with revokes), `federation_object_reactions`, `federation_auto_replies`, the feed subscriptions and their items, and the media cache entries that no other item uses. The documents keep their own provenance (source URL); they lose only the back link to the deleted item.

---

## 11. Security Risks and Controls

| ID | Risk | Control |
| :--- | :--- | :--- |
| RS1 | Script in remote HTML | `usercontent` origin, sandbox `iframe`, `nh3` allowlist, no remote resource URL except through the proxy (Section 8) |
| RS2 | Server-side request forgery through the media proxy or the `Announce` fetch | Private address check on each hop; Fedify refuses private addresses (FT4) |
| RS3 | Tracking of the viewer by remote media | All media through the proxy; the viewer never contacts the remote server |
| RS4 | Storage exhaustion | Size and type limits, item limits, retention (RD5), cache eviction (RD7), following limit (RD8), limits of the thread fetch (Section 6.5) |
| RS5 | Spam through `Announce` of objects from blocked domains | Block check on author, announcer, and object host (Section 6.2) |
| RS6 | Disclosure of the following list | `following` collection gives the total only (RD9) |
| RS7 | Object storage secret leak | Encrypted at rest, never in a response, never in the support bundle |
| RS8 | Delivery of posts to an account that stopped being an actor | Fan-out checks the actor rule at each event (Section 6.4) |
| RS9 | Mention spam from accounts that are not followed | Domain blocks, public-only rule, `mention_rate_per_hour` (Section 6.6) |
| RS10 | Script or tracking in an own note | Claire makes the HTML from plain text and sanitizes it; no remote resource in a note (Section 5.4) |

---

## 12. Technical Items to Confirm (FP4-0)

| ID | Item |
| :--- | :--- |
| RT1 | Fedify resolves a handle with WebFinger and gets the actor (`lookupObject`), in tests with local servers. FT4 refuses loopback addresses; the FP2 end-to-end test used a remote server in the test process. Confirm the same pattern for an outbound `Follow`. |
| RT2 | Fedify fetches the object of an `Announce` with a signed request (servers with authorized fetch) and refuses a private address. |
| RT3 | Way to add task kinds to `federation_outbox`: rebuild the table in an idempotent migration (A3: copy all rows, same IDs) or add a separate task table. Recommendation: rebuild, so that one poller and one lease rule stay. |
| RT4 | List of local media at the base commit: who writes `data/media` and `data/images`, and the key forms. |
| RT5 | `S3MediaStore` works with a local S3-compatible test server for put, get with byte ranges, and delete. |
| RT7 | Number space and object path for notes: `federation_notes` and `federation_objects` share `public_id` (one counter) so that `/ap/objects/{id}` and the outbox collection stay one path. Alternative: one table with nullable `theme_id` and `document_id` (needs a table rebuild, A3). |
| RT8 | Fedify sends `Create(Note)` with `inReplyTo`, `Mention` tags, and `audience`, and `Like` and `Announce` of a remote object; a Mastodon-compatible test server shows the reply in the thread. |
| RT9 | Fedify 2.4.5 vocabulary for FEP-044f: the sidecar sends `QuoteRequest`, receives `Accept` with `QuoteAuthorization`, serves an authorization with an inbox listener and an object dispatcher, and puts `canQuote` in the interaction policy of `Note` and `Article`. A Mastodon-compatible test server shows an approved quote. |
| RT10 | Garage as the reference S3 server (RD26): a local Garage server in a test (binary or container; the test is skipped when it is not installed, like the Deno tests) accepts the four operations of Section 9.1.1 rule 4 from `S3MediaStore` with path-style addressing and the region `garage`. Confirm the Garage defaults that Section 9.1.1 rule 5 marks **[unverified]** (region name, S3 port, virtual-host requirement). Fix the RT5 defect (the signed path and the sent path differ for keys that need percent encoding). |
| RT6 | `@fedify/backfill` reads a FEP-f228 `context` collection and the `replies` collections with a limit, and the parent chain with `inReplyTo`, in the Fedify version of the sidecar. |

---

### 12.1 Results

**RT9 (2026-11-13): confirmed** with `sidecar/fedify/spike/rt9_quote.ts` (two Fedify instances on loopback; `@fedify/fedify` 2.3.15 and `@fedify/vocab` 2.4.5 from the sidecar lock). The spike was not kept in the repository: the test `test_sidecar_source_layout` keeps spike code out of the sidecar directory, and this table records the results (owner, 2026-11-13).

| Item | Result | Effect on this design |
| :--- | :--- | :--- |
| `QuoteRequest` | An inbox listener for `QuoteRequest` receives it. `object` is the quoted object; `instrument` is the quoting `Note`. | None. |
| `Accept` with `QuoteAuthorization` | `result` carries the authorization with `interactingObject` and `interactionTarget`. | None. |
| Authorization path | An object dispatcher serves it; it does not embed the quoting object. | None. |
| `canQuote` | `InteractionPolicy` with `InteractionRule`. `public` → automatic `as:Public`; `followers` → automatic followers collection; `manual` → automatic author, manual `as:Public`; `nobody` → automatic author only. | The four `quote_policy` values of Section 5.4 rule 6 map to these forms. |
| Quote properties | Fedify writes `quote` (FEP-044f), `quoteUrl` with `_misskey_quote` and `quoteUri`, and `quoteAuthorization`. | Read `quote` first and accept the other keys as fallbacks on inbound objects. |
| `Reject` and revoke | Use `objectId` of a `Reject`, not `getObject()` (Fedify refetches an embedded object from another origin). After a revoke the dispatcher gives 404, and a cached lookup still returns the authorization. | The sidecar returns 410 for a revoked authorization (a wrapper, as for `/ap/objects/`). Claire changes the quote state on the `Delete` activity and never from a lookup. |

Not covered: the display of an approved quote on a Mastodon-compatible server. That needs a real server (FP4-10 end-to-end or a manual check).

**RT1 to RT8 (2026-11-13).** Local test programs, not kept in the repository: two or three Fedify instances on loopback with HTTPS (a local test CA), the sidecar config and lock (`--frozen`); Python checks on a database made by `init_db`. The repository did not change.

| Item | State | Result | Effect on this design |
| :--- | :--- | :--- | :--- |
| RT1 follow | Confirmed | WebFinger and `lookupObject` resolve a `Person` and a `Group`; `Follow` with `{actor}#follows/{id}` gets a signed `Accept`. `getObject()` of the `Accept` refetches the embedded `Follow` without the fragment and gives the actor document. | The sidecar matches `Accept` and `Reject` of a `Follow` (and of a `QuoteRequest`, RT9) by `objectId`, read before any `getObject()`, and never calls `getObject()` for them. |
| RT2 announce fetch | Confirmed | Signed fetch of an `Announce` object works in a personal inbox; in the shared inbox it needs `setSharedKeyDispatcher` (the sidecar has it). Private addresses (`127.0.0.1`, `localhost`, `::1`, `10.0.0.1`, `169.254.169.254`) are refused when `allowPrivateAddress` is false. | Keep the shared key dispatcher. |
| RT3 outbox kinds | Confirmed; rebuild | The rebuild in an idempotent migration keeps all rows and IDs, the poller index, and the lease and acknowledgement functions. `DROP TABLE` resets the AUTOINCREMENT counter. | The rebuild saves and restores the `sqlite_sequence` value of `federation_outbox`. The store row form adds `target_iri`. |
| RT4 local media | Confirmed | Only ingest writes `data/images` (`images/{doc_id}_{i}{ext}`); nothing writes `data/media`. Without a media host, the app route `/image` reads the disk only. | Section 9.3 scope is `data/images` with the same keys. `--delete-source` is refused when no media host is set (or the app route also reads the store). |
| RT5 S3 store | Confirmed with a defect | Put, get, byte ranges, and delete work with a local S3-compatible server. SigV4 signs the raw path, but the client sends the percent-encoded path, so keys with other characters fail on a real S3. The store cannot make a bucket. | Object storage keys use only `[A-Za-z0-9/_.-]` and the store refuses other keys (or FP4-1 encodes the signed path by the AWS rules). The admin test uses an existing bucket. |
| RT6 backfill | Confirmed | `@fedify/backfill` 2.3.15 (not in the sidecar lock) reads paged `context` collections, `replies` collections, and the parent chain; it has `maxItems`, `maxDepth`, and `maxRequests`; the reply tree goes depth first and costs many fetches. | FP4-6 adds the package to the sidecar config and lock. `context_max_items` → `maxItems`, `context_depth` → `maxDepth`, new setting `context_max_requests` → `maxRequests`. The reply tree runs only when the object has no `context`. |
| RT7 number space | Proposal | `public_id` is time-based and unique per table, so two tables can get the same number. Two object dispatchers on one path do not work in Fedify (the first wins). | New table `federation_object_numbers` (`public_id`, `kind`, `account_id`, `created_at`): each number row is written first in the same transaction; version 22 fills it from `federation_objects` and the object tombstones. One sidecar dispatcher for `Object` at `/ap/objects/{id}`; `ObjectRecord.type` and the tombstone `formerType` accept `Note`. The outbox collection is a union of documents and notes by `public_id`. |
| RT8 interactions | Confirmed; Mastodon display not covered | Reply with `inReplyTo`, `Mention`, `audience`, `contentMap`, content warning, and `Hashtag`; `Like`, `Announce`, their `Undo`; `Update(Note)`; `Delete` with a `Tombstone`. With a language, Fedify writes `contentMap` only. | Section 5.4 rule 12: the sidecar sends both `content` and `contentMap`. |

## 13. Owner Decisions

| ID | Topic | Decision | State |
| :--- | :--- | :--- | :--- |
| RD1 | Storage model and theme role | P1 and P2 as proposed; P3 and P4 are ideas without a design (Section 4.2); P5 is the cleanup of Section 4.3 | Locked 2026-11-13 |
| RD2 | Inbound scope | Original posts and boosts (`Announce`) | Locked 2026-11-13 |
| RD3 | Remote media | Proxy through the `media` host. The admin can connect object storage. A migration moves local media to object storage. | Locked 2026-11-13 |
| RD4 | Who can follow | Only an account that is an actor | Locked 2026-11-13 |
| RD5 | Retention of items | The admin sets the instance value. An account owner can set a lower value. Starred and ingested items stay. Unfollow keeps the items. (Section 10) | Locked 2026-11-13 |
| RD6 | Replies | Automatic collection as in Mastodon: replies are stored, parents are fetched, and the thread is fetched when the owner opens it (Section 6.5) | Locked 2026-11-13 |
| RD7 | Media limits | The admin sets the size limits and the eviction period (Section 10) | Locked 2026-11-13 |
| RD8 | Following limit | Instance setting, default 1000. The admin can increase it. (Section 5.1, Section 10) | Locked 2026-11-13 |
| RD9 | Visibility and precedence | `following` collection gives the total only. Environment variables win over the admin storage settings. | Locked 2026-11-13 |
| RD10 | Federation client | The federation client is in `/syndication`. It does two-way communication and the first collection, separate from ingestion. It takes collected posts into the knowledge base and publishes ingested documents. (Section 1.3) | Locked 2026-11-13 |
| RD11 | Outbound interaction scope | Reply, new public note, quote with FEP-044f consent, favourite, boost, delete of an own note. No media in notes, no poll (Section 5.4). | Locked 2026-11-13 |
| RD12 | Connected external accounts in the client | The owner decides later, after the progress of the external project fedi.blue. Until then FP4 has the native actor only (option A); the prototype keeps option B for reference. | **Deferred** 2026-11-13 |
| RD13 | Feeds in the client | Feed subscriptions belong to the account (first collection). Baseline goal: a Tiny Tiny RSS user moves the subscription list with OPML (Section 1.3 rule 7). The auto-ingest rule is a later proposal. | Locked 2026-11-13 |
| RD14 | Navigation | Stage pipeline N2 (Section 1.3 rule 6) | Locked 2026-11-13 |
| RD15 | Feeds in single mode | Feeds work in single mode and in network mode (Section 1.3 rule 7) | Locked 2026-11-13 |
| RD16 | Non-public messages | No storage; one automatic notice to the sender (Section 6.2 rule 4) | Locked 2026-11-13 |
| RD17 | Order of work and data ownership | Deferred work D1 to D4 (Section 1.2.1); ingested documents are never deleted by the client (Section 10 rule 5) | Locked 2026-11-13 |
| RD18 | Live update of the client | Periodic polling first: about every 30 seconds while the tab is visible, and once when the tab becomes visible again. The timeline routes use cursors, so that Server-Sent Events can later send only a "new items" signal. | Locked 2026-11-13 |
| RD19 | Role of DrFed | A debugging tool of the developer only: not in the repository, not in CI, not in the service. `FEDERATION.md` has a short note on its use. | Locked 2026-11-13 |
| RD20 | Prototype host | The prototype leaves the app path `/prototype/syndication`. It is served on its own host role `prototype`, as a static page with mock data only. The prototype is a tool for the maintainer: it has no setting. The host name is fixed in code: `prototype.` + the registrable domain of `CLAIRE_FQDN` (Public Suffix List), for example `prototype.netspheres.org` for `cb.netspheres.org`. The static prototype of this design replaces the old one. The difference between the prototype and the real server is synchronized later. | Locked 2026-11-13 |
| RD21 | Content of a published document | The `Article` of a published document has the summary and the share link only, not the body (Section 5.5). | Locked 2026-11-13 |
| RD22 | Markdown bodies | Ingestion stops making Markdown bodies; AsciiDoc is the only body format. Old Markdown documents stay readable without an operator action. Design: [`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md). | Locked 2026-11-13 |
| RD23 | Markdown setting | `CLAIRE_RENDER_FORMAT=md` and its code paths are sealed and closed, not maintained, and kept only as a reference for the Markdown export ([`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) Section 3). | Locked 2026-11-13 |
| RD24 | Markdown in Claire | Markdown is used only for the export of an ingested AsciiDoc document ([`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) Section 4). | Locked 2026-11-13 |
| RD25 | Markdown removal | No conversion: the update deletes the remaining Markdown bodies once and all Markdown code. Markdown export comes from the context menu of an item in `/syndication` ([`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) RD25, Section 5). | Locked 2026-11-13 |
| RD26 | Media storage backends | Local storage is the default. S3-compatible object storage is supported with Garage as the reference implementation (Section 9.1.1). | Locked 2026-11-13 |
| RD27 | Database files | Database files by the kind of data, each with its own version: `claire.db` (shared assets), `client.db` (communication data of accounts), `auth.db`, `fedify.db` ([`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md)). Section 7.1 uses it. | Locked 2026-11-13 |
| RD28 | Knowledge data of accounts | One knowledge database now, with triggers for one file for each account ([`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) Section 4). | Locked 2026-11-13 |
| RD29 | Service of themes of accounts | Collaborator ingest only after an invitation; one admin ingest limit; a public theme is visible outside only through its account actor ([`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) Section 5). | Locked 2026-11-13 |

---

## 14. Phases and Work Units

The work unit rules of the parent design §14.4 apply.

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| FP4-0 | **RT1 to RT9 done 2026-11-13; RT10 is part of FP4-1.** Technical items RT1 to RT9 with small test programs. No product code. | Each item has a result. A failed item goes back to the owner. | RD12, RD13 | S |
| FP4-1 | Storage backends (Section 9.1.1): local default, object storage settings (`/admin/storage`, routes, encrypted secret, test), read fallback to local storage, migration command (Section 9.2, 9.3), RT10 | The test route reports success with a local Garage server. A migration run moves all files with equal hashes and can run again. | FP4-0 | M |
| FP4-2 | **Done 2026-11-13.** With DS-1 (RD27): `client.db` version 1 with the feed tables (moved from `claire.db`, which becomes version 22), `federation_following`, `federation_account_settings`, `federation_client_outbox` with the kinds of Section 5.2 and `target_iri`, store functions, reading settings with the admin and account routes (Section 10) | The migration is idempotent and keeps all outbox rows. An account value above the instance value is refused. | FP4-0 | M |
| FP4-3 | **Done 2026-11-14 (contract 1.3).** Follow lifecycle: routes (Section 5.3 first three), tasks `follow`, `undo_follow`, `reject_follow`, sidecar resolution and delivery, `Accept` and `Reject` events, `following` collection, domain block effect | A follow of a test actor becomes `accepted` after its `Accept`. A follower removal sends `Reject(Follow)`. | FP4-2 | L |
| FP4-4 | `federation_timeline_items`, inbound `Create`, `Update`, `Delete`, `Announce`, `Undo(Announce)`, addressing and block rules, fan-out, replies with the timeline rule and the parent chain (Section 6.5 rules 1 to 3), contract 1.3 fixtures | A public `Create` from a followed actor makes one item for each following account. A followers-only object makes none. A group `Announce` makes one item with the announcer. A reply to an actor that is not followed has `in_timeline` = 0. | FP4-3 | L |
| FP4-5 | Timeline routes, federation client view in `/syndication` (list and thread), the context menu (right click) of an item with "Markdown export" for a knowledge base document (`GET /document/export`, MD-3), `usercontent` render route with the `remote` token, ingest of one item, the FP3-5 publish dialog reached from the client, console fields of the reading settings | The body renders on the `usercontent` origin only. A script in the content does not run. The client and the knowledge base are two views of one page. | FP4-4 | M |
| FP4-6 | Thread fetch: task `fetch_context`, sidecar fetch with `@fedify/backfill`, internal endpoint `context-objects` (Section 6.5 rules 4 to 6) | A thread fetch stores the replies within `context_max_items`. A second request within `context_refetch_minutes` makes no task. | FP4-5 | M |
| FP4-7 | `federation_media_cache`, media proxy route, fetch rules, eviction | An image arrives through the proxy. A private address and an SVG are refused. A file above the admin limit is `too_large`. | FP4-1, FP4-5 | M |
| FP4-8 | Inbound interactions with the account: mentions and replies of actors that are not followed, `Like` and `Announce` of own objects, `federation_object_reactions`, mention rate limit (Section 6.6), automatic notice for non-public messages with `federation_auto_replies` (Section 6.2 rule 4) | A public reply of an actor that is not followed to a published document makes one item with `mention` = 1. A `Like` and its `Undo` leave the counter at 0. | FP4-4 | M |
| FP4-9 | Outbound interactions: `federation_notes`, task kinds `note`, `update_note`, `delete_note`, `like`, `undo_like`, `announce`, `undo_announce`, composition fields (edit, content warning, language, hashtags), routes and client controls (Section 5.4) | A reply arrives at a local test server with `inReplyTo` and a `Mention`. A deleted note gives 410. | FP4-5, FP4-8 | L |
| FP4-9Q | Quotes (FEP-044f): outbound `QuoteRequest` and quote states, inbound requests with `quote_policy`, `federation_quote_authorizations`, the authorization path, revoke (Section 5.4 rules 5 and 6, Section 6.6 rule 5) | A quote of a test object becomes `approved` after the authorization. A request for a `nobody` object gets `Reject`. A revoked authorization gives 410. | FP4-9 | M |
| FP4-10 | End-to-end test (follow, receive, reply and quote in both directions, favourite, boost, render, media through the proxy, take an item into the knowledge base, publish a document), retention run and `claire federation prune` (Section 10), implementation documents, full CI | The end-to-end test passes in development mode. `scripts/ci.sh` passes. | FP4-6, FP4-7, FP4-9Q | M |

Feed units (RD13, RD15). They need no actor and no federation unit, and they work in single mode:

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| FP4-F1 | **Done 2026-11-13.** Account feed tables (`claire.db` version 21), poller with conditional `GET`, WebSub where the feed has a hub, backoff, private address check, item normal form for RSS 2.0, Atom 1.0, and JSON Feed | A feed of a local test server gives items; a second poll with the same `ETag` stores nothing. Works in single mode. | — (first unit, 2026-11-13) | M |
| FP4-F2 | **Done 2026-11-13.** OPML import (feeds, folders, skip list, report) and OPML export | A Tiny Tiny RSS style OPML file with categories gives the same folders and feeds; a second import adds nothing. | FP4-F1 | S |
| FP4-F3 | Feed view in the client (tree, list, reader, link share, star, ingest of one item), item merge by URL with the timeline | An end-to-end test subscribes to a local feed, reads an item, and ingests it into a theme. | FP4-F1, FP4-5 (network mode parts only) | M |

Publication unit (RD21). It changes FP3 behavior and needs no FP4 table:

| Unit | Scope | Completion criteria | After | Size |
| :--- | :--- | :--- | :--- | :--- |
| FP4-P1 | **Done 2026-11-13 except rule 5 of Section 5.5** (the `update` task on a change of the summary or the share link). Object content of a published document: summary and share link (Section 5.5), `update` task on a change of the summary or the share link, tests and fixtures of the federation contract (`object.json`), `FEDERATION.md` | The object of a published document has the summary paragraph and the share link, and no text of the body. A body with a script or Markdown marks does not change the object. The end-to-end publish test reads the summary at a test inbox. | — | S |

The Markdown units MD-1 to MD-5 (RD22 to RD24) are in [`MARKDOWN_RETIREMENT_DESIGN.md`](MARKDOWN_RETIREMENT_DESIGN.md) Section 7.

Order (owner, 2026-11-13): the feed units start first (version 21), parallel to FP4-0. FP4-2 follows as version 22.

Order (owner, 2026-11-14, HD9): the home units HW-1 to HW-7 of [`HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md`](HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md) come before the units of this design that are not done. Unit HW-8 (the stage strip of the home page) comes after FP4-5 and uses the counts of its stage bar.

Units that can be done in any order after their "After" units: FP4-1 and FP4-2; FP4-6, FP4-7, and FP4-8.

### 14.1 Documentation Updates

| Unit | Documents |
| :--- | :--- |
| FP4-1 | `OPERATIONS.md`, `ENVIRONMENT_VARIABLES.md`, `COMMANDS.md` |
| FP4-2 to FP4-9 | `FEDERATION.md`, `SCHEMA_VERSIONING.md` (`claire.db` v22), `API_HTTP_CONTRACT.md`, `openapi.yaml`, `EXTERNAL_ACCESS.md` (media proxy) |
| FP4-10 | `FEDERATION.md` (known limits, differences from this design), `IMPLEMENTATION_STATUS.md` |

### 14.2 Tests

The tests follow the repository test policy and the parent design §14.2. All servers are local (PRINCIPLES A2). The media proxy tests use a local HTTP server, and the object storage tests use a local S3-compatible server.
