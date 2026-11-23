# One Permalink for Documents, Notes, and Entities

- **Document Identifier**: `DESIGN-PERMALINK-20261123`
- **Status**: Proposal. Not implemented. Open items O1 to O4 wait for the owner.
- **Date**: 2026-11-23 (base commit `80e3fb14`). Revision 2: notes in the scope, one path form. Revision 3: entities in the scope, prefix proposal from the criteria of §9.1, term review (§4), O5 accepted (now U6), O2 and O3 explained.
- **Related**: [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (§4.3 item 3 and the share link row of §6.2: this design replaces them when it is implemented), [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD21 and RT7: not changed), [`GLOSSARY.md`](../implementation/GLOSSARY.md) §1.2 and §1.5, [`PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md), [`FEDERATION.md`](../implementation/FEDERATION.md)

## 1. Owner requests (2026-11-23)

> * 현재 적재 본문은 doc, p, /ap/objects 세 가지로 혼재하는 것으로 보인다.
> * 이용자는 서버 내에서 사용하는 오브젝트의 용도와 무관하게 하나의 URL만 인식하여 본문에 접근하거나 공유할 수 있어야 한다.
> * 물음표 쓰고 이퀄에 코드 적는 형식의 URL은 폐기해야한다.

> * 노트와 아티클 모두 타임라인에서 함께 표현되어야 합니다.
> * 이용자가 노트와 아티클(적재 본문)의 URL을 분리하여 인식해야되면 혼란을 줍니다.

> * 엔티티도 공유하여 접근할 수 있도록 설계에 포함합시다.
> * O1: 접두어는 기존의 문자를 답습하지 말고 오너의 마음으로 장기유지보수자로서 문자를 제안하세요.
> * O5: 제안 수용.

Requirements:

- **R1.** Each document, note, and entity has one URL. A user opens, copies, and shares the same URL. The use of the object in the server (workspace, share, federation) does not change the URL.
- **R2.** User-visible URLs of these items have no query form `?name=code`. The scope of R2 outside these items is O2 and O3.
- **R3.** Notes and published documents stay together in the timeline.
- **R4.** The URLs of the three kinds have the same form. A user does not need to know the kind to read, copy, or share a URL.
- **R5.** A user can share an entity and open a shared entity.

## 2. Facts at the base commit

**[code]** Identifiers of the three kinds:

| Kind | Identifier | Store | Secret? |
| :--- | :--- | :--- | :--- |
| Document | `doc_` + 12 hexadecimal digits (48 random bits, `new_id`, `ontology/base.py:23`) | `documents` of `claire.db`, one KB for each row | No: the API, MCP results, logs, and Google Analytics get it |
| Note | Public ID (one generator with published documents, RT7) | `federation_notes` of `client.db` (`store/client_db.py:340`) | No: it is the object ID |
| Entity | `ent_` + 12 hexadecimal digits (`ontology/base.py:37`) | `entities` of `claire.db`, one KB for each row | No |

**[code]** URL forms now:

| Form | Kind | Where it comes from | Who can read |
| :--- | :--- | :--- | :--- |
| `{workspace}?doc={document ID}` (`{workspace}` is `/`, `/@{username}/`, `/kb/{number}/`, or a theme path) | Document | Notifications (`notifications/events.py:238`), console (`console_kb.js:230`, `:692`, `:705`), public KB page (`public_kb.js:341`), timeline (`user_home_ap.js:199`) | The read policy of the KB |
| `{workspace}?node={entity ID}` | Entity | Read by the client at page load (`app.js:97`, `:122`), named in `server.py:4039` | The read policy of the KB |
| `/p?s={share token}` | Document | `POST /share` (`server.py:6259`), sitemap (`ensure_shares=True`), publish route (`accounts_routes/federation.py:584`), `claire share`, Telegram bot, timeline (`accounts/user_home.py:162`) | Each person who has the token. The page checks only `hidden`, not the visibility of the KB (`server.py:6292`) |
| `/ap/objects/{public ID}` | Published document (`Article`), note (`Note`) | Fedify sidecar (`sidecar/fedify/src/sidecar.ts:1235`), edge `location ^~ /ap/` | ActivityPub clients. A browser gets 406 (`sidecar.ts:292`) |

Share token: 16 characters of `_TOKEN_ALPHABET` (31 characters, about 79 bits, `store/db.py:5003`, `:4101`) in `doc_shares` (`store/db.py:261`). One document can have many tokens. No caller gives `ttl`. No route revokes a token. An entity has no share mechanism.

**[code]** Federation and timeline:

- `Article.url` is the share page (`sidecar.ts:772`). `Article.content` is the summary and a link to the share page, never the body (RD21).
- `Note.url` is the object ID (`notes.ts:60`): Claire has no web page for one note. "Copy link" in Mastodon gives `/ap/objects/{id}`, and a browser gets 406 there (owner observation 2026-11-23, Mastodon 4.7.6).
- The timeline of the account home (`user_home_ap.js:188`) shows notes and published documents together. A published document has two links ("open in workspace", "read shared page"). A note has no link. A reply or a quote shows the raw object IRI (`user_home_ap.js:227`, `:231`).
- Entities are not federation objects. The knowledge federation sends the extraction with a document (`/ap/kb/{number}/documents/{contribution}`, signed requests only).

**[code]** Lifecycle facts that a permalink must survive:

- `merge_documents` (`store/db.py:6497`) deletes the loser documents of a near-duplicate cluster and keeps one document.
- An entity row is deleted when its last source document goes (orphan prune, `store/db.py:6912`, `:7253`) or when a rollback removes a `CREATE_NEW`. A later extraction can make an entity with the same type and name again, with a new entity ID. No code merges two existing entities into one.
- A contribution copy gets a new document ID and new entity IDs in the target KB (`collaboration/contributions.py:93`, `:1504`).

**[code]** Other forms of the same items: `/p/presentation?s=` and `?id=…&kb=` (`reader.js:1613`, `cli.py:3391`); `/document/raw?id=` and `/document/export?id=` (`app.js:2054`, `reader.js:480`, `share.html:114`); `/p?id=` (`syndication_live.js:857`), which no route reads (defect: always 404); analytics virtual paths `/doc/{document ID}`, `/node/{entity ID}`, and `/p/{document ID}`; `/render/doc/{id}` on the usercontent host (source of the reader frame). The reader does not write the document into the address bar (`pushState(next, '')`, `app.js:501`).

## 3. Cause

Each need got its own identifier and its own path: the position in the workspace (document ID, entity ID: not secret), the access for persons outside the KB (share token: documents only, many for one document, a different page), and the protocol identity (public ID: published documents and notes only, cannot change). Notes have no web page, thus their protocol identity is also their link. Entities have no share mechanism at all.

## 4. Term review

Request: "아티클을 기존에는 적재 본문이라고 써 왔었는데, 혹시 범위가 달라 혼용인지 검토."

**[code] Result: the three words have different scopes. They are not variants of one concept.**

| Word | Where | Scope |
| :--- | :--- | :--- |
| 문서 (document) | `GLOSSARY.md` §1.2, document list, reader | The unit: ID, source, metadata, collected source text, summary, body, extraction. All documents, published or not. |
| 적재 본문 | Reader tab `synd.kb.reader.tab_ingested` "적재 본문 (Claire 정제본)", switch "수집 원문 ⇄ 적재 본문", `CLAIRE_RENDER_FORMAT` | **One part** of a document: the body that Claire made (`detail`). Its pair is 수집 원문 (collected source text). |
| 아티클 / `Article` | ActivityPub type; UI 발행 문서 (filter `home.timeline.filter_article`) and KB 발행 (chip `home.timeline.kind_article`) | **The federation form** of a **published** document (a subset: documents of public KBs that the owner published). It has the summary and a link, never the 적재 본문 (RD21). |

Thus "아티클(적재 본문)" joins a subset (published documents), a part (the body), and a protocol form. The permalink names the **document**: the whole unit, published or not. The reader opens on the 적재 본문 by default.

Defect found in this review: `synd.kb.publish.public_note` says that publishing sends "이 문서의 적재 본문" to the servers of the followers. The code sends the summary and a link only (RD21, `FEDERATION.md` §4.4). The text must change (phase A2).

Proposed terms (contract review of `GLOSSARY.md` before A1):

| Concept | English | Korean | Note |
| :--- | :--- | :--- | :--- |
| The unit with a permalink | document | 문서 | No change (§1.2). |
| The body that Claire made | ingested body | 적재 본문 | No change. Not a name for a timeline kind. |
| A published document as a timeline kind | document | 문서 | Replaces 발행 문서 and KB 발행 in the timeline. The timeline shows only published documents, thus "발행" adds nothing. 발행 stays the name of the action and the state (publish, withdraw). |
| `Article` | Article | (none) | Protocol type name in technical documents only. Not a user term. |
| A short public post that an account writes | note | 노트 | No change (22 catalog strings). Not 글: a document is also 글. |
| A note or a document in a timeline | post | 게시물 | No change (UI and Mastodon). |
| A knowledge item from extraction | entity | 엔티티 | No change (§1.5). The permalink names the entity, not the graph node. |
| The one URL of a document, a note, or an entity | permalink | 고유 주소 | New. |
| The last path segment of a permalink | address token | 주소 토큰 | New. |
| The flag that lets each person with the permalink read a document or an entity of a private KB | link access | 링크 공개 | New. A note is always public. |

## 5. Decisions in this proposal

| ID | Decision | Reason |
| :--- | :--- | :--- |
| U1 | The permalink of a document, a note, or an entity is `{origin}/perma/{address token}` (prefix: O1, §9.1). One form for the three kinds. The form does not show the kind. It has no namespace segment. | R1, R4. The URL must not change when the username, the server mode, or the KB changes. The server finds the kind and the owner from the token. |
| U2 | The address token has 16 random characters of `_TOKEN_ALPHABET`. One registry gives it (§7). It never changes and is never given to a second item. | One registry gives one uniqueness rule over the three kinds and the two databases. An existing share token can become the address token without a change. About 79 random bits stop guessing. A counter would let a person find items with link access. |
| U3 | Access is a property of the item, not of the URL. See §6. | The URL of the owner and the URL that the owner sends are the same. |
| U4 | Link access is a flag of a document or an entity. The share action sets 1 and returns the permalink. The stop-sharing action sets 0. A caller with the current authorization of `POST /share` can set 1; the same rule applies to entities. A caller with write access to the KB can set 0. No expiry. No second token. | The URL stays, the access changes. A new token would make a second URL (R1). If a permalink leaks, the owner sets 0. |
| U5 | Old forms stop. Server and client code never make them again. Old web links redirect with 308: `{workspace}?doc=`, `{workspace}?node=`, `/p?s=` (each valid token, also the older tokens of the document), `/p/presentation?s=` and `?id=`. A value that the server does not find, or that the caller cannot read, gives 404. No fallback to the workspace. | R2. Links in mail, chats, bookmarks, and search indexes stay usable. |
| U6 | (O5, accepted 2026-11-23.) A caller that is not a member of a public KB gets the item view (§6), with a switch to the workspace view. The switch does not change the URL. | The same view as for a note, with metadata for link previews. |
| U7 | When an item opens (reader or graph focus in the workspace, post in a timeline), the client writes the permalink into the address bar (`pushState` with the URL). When it closes, the address bar shows the URL of the screen again. The permalink names the item, not the screen around it: in the app the client keeps the current screen; a new page load selects the screen with §6. | The URL that the user sees is the URL that the user shares (R1). |
| U8 | Each representation of a document is under its permalink: `/perma/{t}/presentation`, `/perma/{t}/raw`, `/perma/{t}/export/{format}`. The access policies of the raw file and the export do not change. | One root for one item (R2). |
| U9 | Page metadata. Each view writes the canonical link `/perma/{t}`, Open Graph tags, and JSON-LD. `robots` is `index, follow` for a note and for a document or an entity of a public KB. An item that only link access makes readable gets `noindex`. The sitemap lists `/perma/{t}` for documents of public KBs and makes no tokens; entities are not in the sitemap (entity pages in the sitemap stay an idea of `PUBLIC_SITE_INDEXING_IDEAS.md`). Responses under `/perma/` send `Referrer-Policy: strict-origin-when-cross-origin` or a stricter value. Google Analytics keeps virtual paths with internal IDs and never gets an address token. | A link-only permalink is a capability: search engines and other sites must not get it. **This changes the current behavior**: all share pages have `index, follow` now. |
| U10 | Timeline. A note and a document have the same actions: "open" and "copy link", both the permalink. The two links of a published document go away. A post never shows an object ID: a reply or a quote of an own item shows its permalink, a remote one shows its remote `url`. The node detail panel of an entity has "copy link" and, in a private KB, the link access switch. | R3, R4, R5. |
| U11 | ActivityPub. The object ID stays `/ap/objects/{public ID}`. `Note.url`, `Article.url`, and the link in `Article.content` become the permalink. A request to an object ID that does not accept ActivityPub gets 303 to the permalink (`onNotAcceptable` of the sidecar). A request to a permalink that accepts only ActivityPub gets 303 to the object ID when the item is federated. The page of a federated item has `<link rel="alternate" type="application/activity+json" href="{object ID}">`. Entities are not federation objects and get none of this. | Delivered IDs cannot change. A remote copy can keep its old `url`; the 303 and the 308 send old links to the permalink. 303, not 308: the object ID did not move; the server only selects a representation. |
| U12 | Input. `extract_own_share_token` and `resolve_document_targets` (CLI, Telegram bot, MCP targets) read the permalink and the old forms of U5, with the same mapping as the redirects. | Old links stay in messages. Reading an old link is not making one. |

## 6. Views and access for `GET /perma/{address token}`

**Item view.** One frame for the three kinds, the opened form of a timeline post:

- Note: author, time, text, reactions, thread (글타래).
- Document: author or KB, time, the reader (적재 본문 first), and for a published document the reactions and the thread.
- Entity: name, type, aliases, observations, relations, source documents.

**Workspace view.** The workspace of the KB with the reader open (document, as `?doc=` now) or with the graph focused on the entity (as `?node=` now).

The first rule that applies gives the answer:

1. The registry does not have the token: 404. The item is gone (§7): 410. The token is an alias: 308 to the permalink of the target.
2. A note: item view.
3. A hidden document, and the caller cannot see hidden documents of the KB (`_sees_hidden`): 404.
4. The caller is a member of the KB (personal KB: the owner; shared KB: a member role): workspace view.
5. The KB is public: item view with the switch to the workspace view (U6).
6. The item has link access 1: item view without the switch. An entity view shows, as a link, only an item that the caller can open with its own permalink. Another related entity shows its name without a link. Another source document shows only in the count.
7. Else: the answer that the workspace path of the KB gives to this caller now (sign-in redirect or 404).

## 7. Registry and lifecycle

The migration completes itself at the start. The operator does nothing.

**Registry.** New table `permalinks` in `claire.db`: `token` (primary key), `kind` (`document`, `note`, `entity`), `kb_id` (document, entity), `account_id` (note), `target` (document ID, note public ID, entity ID), `identity` (entity only: type and normalized name), `link_access` (document, entity), `alias_of` (a token), `gone_at`, `created_at`. One live row for each item (`UNIQUE` on `kind`, `target` for rows without `alias_of`). A note is in `client.db`; the route that makes a note writes the registry row first (a row without a note answers 404 and does no harm).

**Migration.**

1. Each document with a valid row in `doc_shares`: the newest valid token becomes its address token (the selection of `create_doc_share(reuse_existing=True)`), `link_access` 1. The older valid tokens of the document become alias rows. Each existing share link keeps its access.
2. Each other document, each note (also a deleted note, with `gone_at`), and each entity: a new token, `link_access` 0.
3. `doc_shares` is then not used: remove it after the migration copied it.

**Lifecycle.**

| Event | Registry change |
| :--- | :--- |
| A document, a note, or an entity is made (ingestion, contribution copy, graph bundle import, note creation, extraction) | New row |
| A row is replaced (`INSERT OR REPLACE`, `store/db.py:2730`, `ops/graph_bundle.py:1143`; in-place re-ingestion of `f0b3efe8`; note edit) | No change: the token stays |
| Near-duplicate merge (`merge_documents`) | The rows of the losers get `alias_of` = the token of the keeper. The keeper gets the highest `link_access` of the group, so that each old shared link still opens. |
| A document is purged, a note is deleted | `gone_at` (410) |
| An entity row is deleted (orphan prune, rollback) | `gone_at`. When an entity with the same KB and `identity` is made later and exactly one gone row matches, that row becomes live again with the new entity ID: the permalink survives a re-extraction. With no match or more than one match: a new row. |
| Account backup and restore | The rows go with the account |

## 8. Phases

| Phase | Work |
| :--- | :--- |
| A1 | Registry and migration (§7), resolver, `GET /perma/{t}` with the views and the metadata (§6, U9), the 308 redirects (U5), share and stop-sharing for documents and entities. |
| A2 | Client: address bar (U7), timeline and node panel actions (U10), each link builder uses the permalink (console, public KB page, notifications, syndication with the defect `/p?id=`, account home, Telegram bot, CLI, MCP results), representation paths (U8), the text `synd.kb.publish.public_note` (§4), the terms of §4 after the review. |
| A3 | Federation (U11). Measure with Mastodon if an `Update` changes the stored `url` without the "edited" mark; send `Update` for existing objects only if the measurement shows this. |
| A4 | Public site (U9). Update `docs/implementation/` (PUBLIC_SITE, FEDERATION, WEBUI, API_HTTP_CONTRACT, COMMANDS, openapi) and `GLOSSARY.md`. |

Tests: E2E "open a document in the workspace, copy the permalink from the address bar, open it as an anonymous caller" for a public KB and for a private KB with link access 1 and 0; the same for an entity from the graph; E2E "open a note and a document from the timeline, copy each link, both have the same form"; E2E for the 308 of an old `/p?s=` link and of an old `?node=` link, and for the 303 of an object ID. Unit tests: migration (I/O), registry lifecycle (merge alias, entity re-creation), resolver over the two databases, sidecar redirect (interface between modules).

## 9. Open items for the owner

### 9.1 O1: prefix

Lessons from this project:

- `/doc/` (revision 1) became wrong in one hour, when notes joined; entities joined next. A prefix that names a kind ages when a kind joins.
- `/p` has no clear meaning (public, page, presentation?), and it has two different things under it (`/p/presentation`, `/p/images/`). Code checks it with `startswith("/p/presentation")` (`security.py:2614`), and `/purge` and `/providers` also start with `p`.
- Paths with a namespace needed 308 redirects after the mode change (namespace design §4.3).

Criteria:

| ID | Criterion |
| :--- | :--- |
| C1 | It names the mechanism (a permanent address), not a kind, a feature (share), a protocol (`ap`, `objects`), or a screen (timeline). |
| C2 | No current top-level path starts with it. A prefix match in the edge (`location ^~`), in `robots.txt`, or in `startswith` cannot catch a different path by accident. Checked against the 102 top-level segments of the routes at the base commit. |
| C3 | The code does not use the word for a different concept (`item`: timeline items; `link`: graph links; `id`: internal IDs; `ref`: `/reference`; `node`, `object`, `share`, `doc`, `p`: current meanings). |
| C4 | A person can read it in logs, route policies, `robots.txt`, edge rules, and support talks without a table. |
| C5 | Short lowercase ASCII. |
| C6 | It can be a reserved USERNAME (rule D1 of the console design), so that no account name looks like it. |

| Candidate | C1 | C2 | C3 | C4 | C5 | Result |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`/perma/`** | Yes | Yes (0 paths) | Yes (the only use is the RSS attribute `isPermaLink`, the same meaning) | Yes | 5 letters | **Proposal** |
| `/permalink/` | Yes | Yes | Yes | Yes | 9 letters | Good, but long for sharing |
| `/x/` | Yes | Yes | Yes | No: no meaning, a reader needs a table; also the brand X | 1 letter | Second choice if length is first |
| `/go/` | Partly | Yes | No: "go links" are names that a person selects, not random tokens | Partly | 2 letters | No |
| `/item/` | Yes | Yes | No | Yes | 4 letters | No |
| `/a/`, `/i/`, `/k/`, `/r/`, `/s/` | Partly | No (`api`, `account`, `admin`; `ingest`, `images`; `kb`, `knowledge-…`; `reference`, `regenerate`; `search`, `settings`) | — | — | 1 letter | No |

Proposal: **`/perma/`**, reserved as a USERNAME. "Perma" is the common short form of "permalink" (the Perma.cc service of libraries uses it for permanent citation links), thus it names what the URL promises: it does not change. Example: `https://claire.bible/perma/k9x2m7q1p4v8nd3a`.

### 9.2 O2: JSON API

**What it is.** The paths that programs call and that give JSON: the web client (`fetch`), the CLI, tests. MCP tools call functions in the process, not these paths. The agent plugins call only `/api/client-extraction/...`, which has path forms now.

**The query forms.** 9 API handlers read an item identifier from the query: `GET /document?id=` (`server.py:5234`), `/document/export?id=` (`:5195`), `/document/raw?id=` (`:5270`), `/document/presentation?id=` (`:6515`), `GET` and `PUT /document/presentation/adoc?id=` (`:6760`), `/node?id=` (`:5160`), and `POST /document/presentation/generate`, `/compose`, `/compile` with `?id=` (`:6546`, `:6645`, `:6709`). The value is the internal document ID or entity ID, not an address token. The two presentation pages (`server.py:6431`, `usercontent.py:579`) are pages, not API: U5 and U8 move them.

**Who sees them.** A user does not see them in the address bar, with two exceptions: `/document/raw` and `/document/export` open in a browser tab. U8 moves these two to the permalink.

**Why not in this design.**

1. A program needs the internal ID: it also acts on items that it may change but not share, and an address token is a capability for link access. If the API took tokens, the capability would go into each program call and log.
2. The change is a rename of 9 routes, their client calls, and `openapi.yaml`. There are no external API consumers, thus old names can close at once. But this does not help R1.
3. Together with this design, the change and its tests become larger, and a failure in one part stops the other.

**Options.** (a) No change. (b) A separate small design after this one: path forms under `/api/` with internal IDs, for example `GET /api/documents/{document ID}`, `/api/documents/{document ID}/presentation`, `/api/entities/{entity ID}`. Old names close at once.

**Proposal.** (b), after A4, if the owner wants R2 for all paths. Else (a).

### 9.3 O3: other user-visible query forms

Revision 3 moves `?node=` into this design (U5), because entities are in the scope now. The other query forms that a user can see:

| Class | Examples | Proposal |
| :--- | :--- | :--- |
| One-time download token (a code after `=`) | `/graph/bundle?token=` (`console_kb.js:1045`), `/support/bundle?token=` (`console_admin.js:536`) | Path form (`/graph/bundle/{token}`, `/support/bundle/{token}`) in a separate small change. R2 applies: the token is a code. Not in this design: these are not items. |
| Filter of a list | `/themes?knowledge_type=` (`console_kb.js:259`, `common_ingest_modals.html:11`) | No change. The path names the list, and the query only narrows it. No item has this URL. |
| External standards | `/oauth/authorize?client_id=…` (OAuth 2.1), `/device?user_code=` (RFC 8628 `verification_uri_complete`), `/.well-known/webfinger?resource=acct:…` (RFC 7033), ActivityPub collection pages `?page=true&max_id=` | No change. Other servers and clients send these forms; Claire cannot change them. |
| Technical markers | `/?pwa=1` (start URL of the web app manifest), `/icon?p=` (icon file) | No change now. Nobody shares them. A later cleanup can give them paths. |

## 10. Rejected alternatives

- **One path for each kind** (`/notes/{id}`, `/doc/{t}`, `/entity/{id}`): the user must know the kind (R4).
- **Namespace in the path** (`/@{username}/{id}`, as Mastodon does; `/kb/{number}/…`): the URL changes with the username, the server mode, or the KB.
- **Public ID or internal ID in the path**: documents that are not published and entities have no public ID; a counter lets a person find items with link access; internal IDs are not secret.
- **A token column in each table**: three tables in two databases need three uniqueness checks, and merges, aliases, gone items, and entity re-creation need their own rules in each table. One registry has one rule set.
- **Object ID as the permalink**: delivered IDs cannot change, entities have none, and the edge would need to divide a Claire path by `Accept`.
- **Keep `/p?s=` and add redirects only**: the query form stays (R2), and a document keeps many share URLs (R1).
