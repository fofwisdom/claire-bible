# One Permalink for Documents, Notes, and Entities

- **Document Identifier**: `DESIGN-PERMALINK-20261123`
- **Status**: Decided (owner, 2026-11-23). Partly implemented: B1; A1 for documents (registry, route, redirects, share actions); the entity address with its share actions and its page for link access alone (§6.2); the terms of §4. Also implemented: the item view of a note (without the reactions and the thread), the timeline actions (U10), and A3 without the measurement with Mastodon. Not implemented: the rest of A2 (address bar, console and notification links, representation paths) and the rest of A4. Current behavior and the state of each phase: [`PERMALINKS.md`](../implementation/PERMALINKS.md). No open items.
- **Date**: 2026-11-23 (base commit `80e3fb14`). Revision 2: notes in the scope, one path form. Revision 3: entities in the scope, term review (§4). Revision 4: the owner decisions of §9 (prefix `/silvercord/`, API, other query forms, bundle paths). Revision 5: the entity has its own address `/entity/{address token}`; `/silvercord/` is only for ActivityPub object kinds; usage rules for entity, node, graph, relation (§4, §6.2, §9).
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

> * O1: /silvercord/
> * O2: 잘 모르겠네요. Claude는 이 프로젝트의 오너이고, 장기 유지보수자니까 올바른 결정해주세요.
> * O3: 제가 너무 광범위하게 정의했네요. GET 방식이 필요한 곳도 있겠죠. 필요에 따라 유지할 수 있습니다.
>    * 그래프 번들과 지원 번들 모두 지원을 위한 번들이니 GET을 유지하더라도 호출 경로 변경이 필요해보입니다.
> * O4: 알겠습니다.

> * 엔티티 공유에 필요한 것은 엔티티를 가리키는 주소와 접근 규칙(링크 공개)입니다. 그 주소가 /silvercord/여야 할 이유는 없습니다.
> * 예를 들어, vSAN에 대해 공유하고자 한다면 바로 vSAN을 그래프에서 볼 수 있도록 함이 목표입니다.
> * (엔티티 주소 `/entity/{주소 토큰}`) 수용. (용어) 전부 바로잡습니다. /silvercord/ 경로는 제안에 동의합니다. 다만 ActivityPub 전용으로 기록하여 추후 확장 시 참고를 편하도록 합시다.

Requirements:

- **R1.** Each document, note, and entity has one URL. A user opens, copies, and shares the same URL. The use of the object in the server (workspace, share, federation) does not change the URL.
- **R2.** The URLs of these items that a person sees (address bar, copied link, download link) have no query form `?name=code`. R2 is not a rule for all paths of the server: a query stays where a path needs it (owner, §9.3).
- **R3.** Notes and published documents stay together in the timeline.
- **R4.** The URLs of a note and a document have the same form (Silvercord). A user does not need to know which of the two it is. An entity has its own form (revision 5).
- **R5.** A user can share an entity and open a shared entity: the address of the entity opens the graph of its KB with the entity selected.

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

**Entity, node, graph, relation (revision 5).** `GLOSSARY.md` §1.5 and §1.6 had the definitions, but the UI and the API did not follow them: the list tab said 엔티티 and the button of the same item said 노드 관리; one text had "엔티티와 지식 노드/관계"; the relations of an entity were 연결 in the list and 관계 in the filter; the entity detail API is `GET /node?id=`. Rules (owner, 2026-11-23):

1. The knowledge item is always an **entity** (엔티티): manage, change the primary label, synthesis, research, share, not found, the items of a document.
2. A **node** (노드) is only the point of an entity in the graph view: click a node, the path between two nodes.
3. The **graph** (그래프) is all entities and relations of one KB, and the name of the view that draws them.
4. A statement between two entities is always a **relation** (관계). A **link** (링크) is only the line in the graph view. 연결 is not a term for this.
5. 지식 노드 and 개념 노드 are not used.

Defect found in this review: `synd.kb.publish.public_note` says that publishing sends "이 문서의 적재 본문" to the servers of the followers. The code sends the summary and a link only (RD21, `FEDERATION.md` §4.4). The text must change (phase A2).

Terms (accepted by the owner 2026-11-23; `GLOSSARY.md` gets them in phase A4):

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
| U1 | The permalink of a document or a note is `{origin}/silvercord/{address token}` (prefix: owner, §9.1). **`/silvercord/` is only for the kinds that can be ActivityPub objects** (document: `Article`; note: `Note`). A kind that is not an ActivityPub object gets its own path word: the entity has `{origin}/entity/{address token}` (§6.2). A later kind follows this rule. One form for the two Silvercord kinds. The form does not show the kind. It has no namespace segment. The path word `silvercord` is in one constant of the code, in the edge rule, and in `robots.txt`; each other identifier uses `permalink`. `silvercord` is a reserved USERNAME. | R1, R4. The URL must not change when the username, the server mode, or the KB changes. The server finds the kind and the owner from the token. |
| U2 | The address token has 16 random characters of `_TOKEN_ALPHABET`. One registry gives it (§7). It never changes and is never given to a second item. | One registry gives one uniqueness rule over the three kinds and the two databases. An existing share token can become the address token without a change. About 79 random bits stop guessing. A counter would let a person find items with link access. |
| U3 | Access is a property of the item, not of the URL. See §6. | The URL of the owner and the URL that the owner sends are the same. |
| U4 | Link access is a flag of a document or an entity. The share action sets 1 and returns the permalink. The stop-sharing action sets 0. A caller with the current authorization of `POST /share` can set 1; the same rule applies to entities. A caller with write access to the KB can set 0. No expiry. No second token. | The URL stays, the access changes. A new token would make a second URL (R1). If a permalink leaks, the owner sets 0. |
| U5 | Old forms stop. Server and client code never make them again. Old web links redirect with 308: `{workspace}?doc=`, `{workspace}?node=` (to the entity address), `/p?s=` (each valid token, also the older tokens of the document), `/p/presentation?s=` and `?id=`. A value that the server does not find, or that the caller cannot read, gives 404. No fallback to the workspace. | R2. Links in mail, chats, bookmarks, and search indexes stay usable. |
| U6 | (O5, accepted 2026-11-23.) A caller that is not a member of a public KB gets the item view (§6), with a switch to the workspace view. The switch does not change the URL. | The same view as for a note, with metadata for link previews. |
| U7 | When an item opens (reader or graph focus in the workspace, post in a timeline), the client writes the permalink into the address bar (`pushState` with the URL). When it closes, the address bar shows the URL of the screen again. The permalink names the item, not the screen around it: in the app the client keeps the current screen; a new page load selects the screen with §6. | The URL that the user sees is the URL that the user shares (R1). |
| U8 | Each representation of a document is under its permalink: `/silvercord/{t}/presentation`, `/silvercord/{t}/raw`, `/silvercord/{t}/export/{format}`. The access policies of the raw file and the export do not change. | One root for one item (R2). |
| U9 | Page metadata. Each view writes the canonical link `/silvercord/{t}`, Open Graph tags, and JSON-LD. `robots` is `index, follow` for a note and for a document or an entity of a public KB. An item that only link access makes readable gets `noindex`. The sitemap lists `/silvercord/{t}` for documents of public KBs and makes no tokens; entities are not in the sitemap (entity pages in the sitemap stay an idea of `PUBLIC_SITE_INDEXING_IDEAS.md`). Responses under `/silvercord/` send `Referrer-Policy: strict-origin-when-cross-origin` or a stricter value. Google Analytics keeps virtual paths with internal IDs and never gets an address token. | A link-only permalink is a capability: search engines and other sites must not get it. **This changes the current behavior**: all share pages have `index, follow` now. |
| U10 | Timeline. A note and a document have the same actions: "open" and "copy link", both the permalink. The two links of a published document go away. A post never shows an object ID: a reply or a quote of an own item shows its permalink, a remote one shows its remote `url`. The node detail panel of an entity has "copy link" and, in a private KB, the link access switch. | R3, R4, R5. |
| U11 | ActivityPub. The object ID stays `/ap/objects/{public ID}`. `Note.url`, `Article.url`, and the link in `Article.content` become the permalink. A request to an object ID that does not accept ActivityPub gets 303 to the permalink (`onNotAcceptable` of the sidecar). A request to a permalink that accepts only ActivityPub gets 303 to the object ID when the item is federated. The page of a federated item has `<link rel="alternate" type="application/activity+json" href="{object ID}">`. Entities are not federation objects and get none of this. | Delivered IDs cannot change. A remote copy can keep its old `url`; the 303 and the 308 send old links to the permalink. 303, not 308: the object ID did not move; the server only selects a representation. |
| U12 | Input. `extract_own_share_token` and `resolve_document_targets` (CLI, Telegram bot, MCP targets) read the permalink and the old forms of U5, with the same mapping as the redirects. | Old links stay in messages. Reading an old link is not making one. |
| U13 | (O2, decided by the maintainer on the request of the owner.) The JSON API does not change. An API call keeps the internal ID where it has it now, also in a query. A new API route puts an item identifier in the path. | R2 is a rule for URLs that a person sees, and U8 moves the two API paths that a person sees. A rename of the other 7 handlers gives a user nothing and can break the client. New routes have the path form now (`/api/client-extraction/sources/{document_id}`, `/api/admin/themes/{number}`), thus the API converges without a rename project. |

## 6. Views and access for `GET /silvercord/{address token}`

**Item view.** One frame for the three kinds, the opened form of a timeline post:

- Note: author, time, text, reactions, thread (글타래).
- Document: author or KB, time, the reader (적재 본문 first), and for a published document the reactions and the thread.
- Entity: not an item view. See §6.2.

**Workspace view.** The workspace of the KB with the reader open (document, as `?doc=` now) or with the graph focused on the entity (as `?node=` now).

The first rule that applies gives the answer:

1. The registry does not have the token: 404. The item is gone (§7): 410. The token is an alias: 308 to the permalink of the target.
2. A note: item view.
3. A hidden document, and the caller cannot see hidden documents of the KB (`_sees_hidden`): 404.
4. The caller is a member of the KB (personal KB: the owner; shared KB: a member role): workspace view.
5. The KB is public: item view with the switch to the workspace view (U6).
6. The item has link access 1: item view without the switch. 
7. Else: the answer that the workspace path of the KB gives to this caller now (sign-in redirect or 404).

### 6.2 Entity address `GET /entity/{address token}` (revision 5)

Goal of the owner: a person who gets the address of the entity vSAN sees vSAN in the graph at once.

1. The token is not in the registry, or it is the token of a document or a note: 404. A Silvercord path with the token of an entity: 404. The entity is gone: 410.
2. The caller can read the KB (a member, or a reader of a public KB): the workspace of the KB in the graph view, with the entity selected and the context panel open.
3. The entity has link access 1, and the caller cannot read the KB: the graph view with only this entity and the entities that have a direct relation with it (the scope of "neighbors only"). A related entity shows its name. A source document shows as a link only when the caller can open it; else it is only in the count.
4. Else: the answer that the workspace path of the KB gives to this caller now. The answer has no ID of the entity.

The registry, the address token, link access, the alias and gone rules, and the re-creation rule (§7) are the same as for a document. An entity is not a federation object: U11 does not apply.

## 7. Registry and lifecycle

The migration completes itself at the start. The operator does nothing.

**Registry.** New table `permalinks` in `claire.db`: `token` (primary key), `kind` (`document`, `note`, `entity`), `kb_id` (document, entity), `account_id` (note), `target` (document ID, note public ID, entity ID), `identity` (entity only: type and normalized name), `link_access` (document, entity), `alias_of` (a token), `gone_at`, `created_at`. One live row for each item (`UNIQUE` on `kind`, `target` for rows without `alias_of`). A note is in `client.db`; the route that makes a note writes the registry row first (a row without a note answers 404 and does no harm).

**Migration.**

1. Each document with a valid row in `doc_shares`: the newest valid token becomes its address token (the selection of `create_doc_share(reuse_existing=True)`), `link_access` 1. The older valid tokens of the document become alias rows. Each existing share link keeps its access.
2. Each other document, each note (also a deleted note, with `gone_at`), and each entity: a new token, `link_access` 0.
3. `doc_shares` is then not used: the migration removes the table after it copied the rows.

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
| B1 | Bundle paths (§9.4). **Done 2026-11-23.** Current behavior: [`TELEMETRY.md`](../implementation/TELEMETRY.md), [`GRAPH_BUNDLE_REVIEW.md`](../implementation/GRAPH_BUNDLE_REVIEW.md). |
| A1 | The path constant and the reserved USERNAME `silvercord`, registry and migration (§7), resolver, `GET /silvercord/{t}` with the views and the metadata (§6, U9), the 308 redirects (U5), share and stop-sharing for documents and entities. |
| A2 | Client: address bar (U7), timeline and node panel actions (U10), each link builder uses the permalink (console, public KB page, notifications, syndication with the defect `/p?id=`, account home, Telegram bot, CLI, MCP results), representation paths (U8), the text `synd.kb.publish.public_note` (§4), the terms of §4 after the review. |
| A3 | Federation (U11). Measure with Mastodon if an `Update` changes the stored `url` without the "edited" mark; send `Update` for existing objects only if the measurement shows this. |
| A4 | Public site (U9). Update `docs/implementation/` (PUBLIC_SITE, FEDERATION, WEBUI, API_HTTP_CONTRACT, COMMANDS, openapi) and `GLOSSARY.md`. |

Tests: E2E "open a document in the workspace, copy the permalink from the address bar, open it as an anonymous caller" for a public KB and for a private KB with link access 1 and 0; the same for an entity from the graph; E2E "open a note and a document from the timeline, copy each link, both have the same form"; E2E for the 308 of an old `/p?s=` link and of an old `?node=` link, and for the 303 of an object ID. Unit tests: migration (I/O), registry lifecycle (merge alias, entity re-creation), resolver over the two databases, sidecar redirect (interface between modules).

## 9. Owner decisions (2026-11-23)

| ID | Question | Decision |
| :--- | :--- | :--- |
| O1 | Path prefix of the permalink | `/silvercord/` (owner). §9.1 |
| O2 | R2 for the JSON API | No change to the API (maintainer, U13). §9.2 |
| O3 | R2 for other query forms | R2 is for item URLs only; a query stays where a path needs it. The two bundle paths change (owner). §9.3, §9.4 |
| O4 | Terms | Accepted. §4 |
| O5 | View for a caller that is not a member of a public KB | Item view with the switch (U6) |
| O6 | Address of an entity (revision 5) | `/entity/{address token}`, not Silvercord (owner). `/silvercord/` is only for ActivityPub object kinds, recorded in U1, in `GLOSSARY.md` §1.11, and at the path constants of `store/permalinks.py` |
| O7 | Entity, node, graph, relation (revision 5) | Usage rules of §4 (owner: correct all). The UI text follows them; API names and code identifiers change when a route or a module changes for another reason (U13) |

### 9.1 O1: prefix `/silvercord/`

The owner selected the name. The proposal `/perma/` of revision 3 was not selected: "permalink" is a short form already, and a second short form has no convention.

Lessons that the name must satisfy, and the check of `/silvercord/`:

| ID | Criterion | `/silvercord/` |
| :--- | :--- | :--- |
| C1 | It names no kind (`/doc/` became wrong when notes and entities joined), no feature, no protocol, no screen. | Yes: a proper name |
| C2 | No current top-level path starts with it, so that a prefix match in the edge (`location ^~`), in `robots.txt`, or in `startswith` cannot catch a different path. Checked against the 102 top-level segments of the routes at the base commit. | Yes: 0 paths |
| C3 | The code does not use the word for a different concept. | Yes: no use in `src/`, `sidecar/`, `ops/`, `docs/` |
| C4 | It is one unique string in logs, route policies, and edge rules. | Yes |
| C5 | It can be a reserved USERNAME (rule D1 of the console design). The word matches the USERNAME syntax, thus the reservation is necessary. | Add to `RESERVED_USERNAMES` |

Example: `https://claire.bible/silvercord/k9x2m7q1p4v8nd3a`.

### 9.2 O2: JSON API (decision U13)

**What it is.** The paths that programs call and that give JSON: the web client (`fetch`), the CLI, tests. MCP tools call functions in the process, not these paths. The agent plugins call only `/api/client-extraction/...`, which has path forms now.

**The query forms.** 9 API handlers read an item identifier from the query: `GET /document?id=` (`server.py:5234`), `/document/export?id=` (`:5195`), `/document/raw?id=` (`:5270`), `/document/presentation?id=` (`:6515`), `GET` and `PUT /document/presentation/adoc?id=` (`:6760`), `/node?id=` (`:5160`), and `POST /document/presentation/generate`, `/compose`, `/compile` with `?id=` (`:6546`, `:6645`, `:6709`). The value is the internal document ID or entity ID, not an address token.

**Decision and reasons.**

1. A person does not see these URLs, with two exceptions: `/document/raw` and `/document/export` open in a browser tab. U8 moves these two under the permalink, and the old two names close.
2. A program needs the internal ID. An address token is a capability for link access; it must not go into each program call and log.
3. A rename of the other 7 handlers, their client calls, the tests, and `openapi.yaml` gives a user nothing and can break the client. Other sessions change the same files at the same time.
4. The API converges without a project: each new route has the identifier in the path.

### 9.3 O3: other query forms

The owner narrowed R2: a query stays where a path needs it.

| Class | Examples | Result |
| :--- | :--- | :--- |
| Item identifier | `?doc=`, `?node=`, `/p?s=`, `/p/presentation?s=`, `/document/raw?id=`, `/document/export?id=` | This design (U5, U8) |
| Bundle download token | `/graph/bundle?token=`, `/support/bundle?token=` | New paths (§9.4) |
| Filter of a list | `/themes?knowledge_type=` | No change: the path names the list, the query narrows it |
| External standards | `/oauth/authorize?client_id=…` (OAuth 2.1), `/device?user_code=` (RFC 8628), `/.well-known/webfinger?resource=acct:…` (RFC 7033), ActivityPub collection pages `?page=true&max_id=` | No change: other servers and clients send these forms |
| Technical markers | `/?pwa=1` (start URL of the web app manifest), `/icon?p=` (icon file) | No change |

### 9.4 Bundle paths (work item B1)

Owner: the graph bundle and the support bundle are both bundles for support, thus their paths must change, also if the method stays `GET`.

**[code]** Now: the support bundle has server diagnostics for a root cause analysis, the administrator makes it, and it is deleted after 6 hours (`ops/support_bundle.py`). The graph bundle has the graph of one KB without vectors and secrets, the owner of the namespace makes it, and it expires after 24 hours (`ops/graph_bundle.py`). The two have different path roots (`/support/`, `/graph/`), and each download link has the token in a query.

| Now | New | Policy |
| :--- | :--- | :--- |
| `POST /support/bundle` | No change | `_system("admin", "none")` |
| `GET /support/bundle?token={token}` | `GET /support/bundle/{token}` | `_system("public", "none")`; the token is the capability |
| `POST {namespace}/graph/bundle` | `POST {namespace}/support/graph-bundle` | `_namespaced("owner", "settings")` |
| `GET /graph/bundle?token={token}` | `GET /support/graph-bundle/{token}` | `_system("public", "none")`; the token finds the KB |

1. The two bundles are under one root, `/support/`. The names of the two bundles (support bundle, graph bundle) do not change.
2. The method stays `GET` for a download; the token is a path segment.
3. The old paths close at once, with no redirect: they are API names, and a link lives 6 or 24 hours at most.
4. The change is in `server.py`, `security.py` (policy and the `robots.txt` lines), the builders of the download links (`ops/graph_bundle.py:357`, `ops/support_bundle.py:1152`, `telegram/bot.py`), the console scripts and hints, 10 test files, and 10 documents of `docs/implementation/`.

## 10. Rejected alternatives

- **One path for each kind** (`/notes/{id}`, `/doc/{t}`, `/entity/{id}`): the user must know the kind (R4).
- **Namespace in the path** (`/@{username}/{id}`, as Mastodon does; `/kb/{number}/…`): the URL changes with the username, the server mode, or the KB.
- **Public ID or internal ID in the path**: documents that are not published and entities have no public ID; a counter lets a person find items with link access; internal IDs are not secret.
- **A token column in each table**: three tables in two databases need three uniqueness checks, and merges, aliases, gone items, and entity re-creation need their own rules in each table. One registry has one rule set.
- **Object ID as the permalink**: delivered IDs cannot change, entities have none, and the edge would need to divide a Claire path by `Accept`.
- **Keep `/p?s=` and add redirects only**: the query form stays (R2), and a document keeps many share URLs (R1).
