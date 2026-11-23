# One Permalink for Notes and Ingested Documents

- **Document Identifier**: `DESIGN-PERMALINK-20261123`
- **Status**: Proposal. Not implemented. Open items O1 to O5 wait for the owner.
- **Date**: 2026-11-23 (base commit `80e3fb14`). Revision 2 on the same day: notes are in the scope, one path form for notes and documents.
- **Related**: [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (§4.3 item 3 and the share link row of §6.2: this design replaces them when it is implemented), [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD21 and RT7: not changed), [`PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md), [`FEDERATION.md`](../implementation/FEDERATION.md)

## 1. Owner requests (2026-11-23)

> * 현재 적재 본문은 doc, p, /ap/objects 세 가지로 혼재하는 것으로 보인다.
> * 이용자는 서버 내에서 사용하는 오브젝트의 용도와 무관하게 하나의 URL만 인식하여 본문에 접근하거나 공유할 수 있어야 한다.
> * 물음표 쓰고 이퀄에 코드 적는 형식의 URL은 폐기해야한다.

> * 노트와 아티클 모두 타임라인에서 함께 표현되어야 합니다.
> * 이용자가 노트와 아티클(적재 본문)의 URL을 분리하여 인식해야되면 혼란을 줍니다.

Requirements:

- **R1.** Each note and each ingested document has one URL. A user opens, copies, and shares the same URL. The use of the object in the server (workspace, share, federation) does not change the URL.
- **R2.** User-visible URLs have no query form `?name=code`.
- **R3.** Notes and articles stay together in the timeline.
- **R4.** The URL of a note and the URL of an article have the same form. A user does not need to know the kind to read, copy, or share a URL.

## 2. Facts at the base commit

**[code]** URL forms of an ingested document:

| Form | Identifier | Where it comes from | Who can read |
| :--- | :--- | :--- | :--- |
| `{workspace}?doc={document ID}` (`{workspace}` is `/`, `/@{username}/`, `/kb/{number}/`, or a theme path) | Document ID: `doc_` + 12 hexadecimal digits (48 random bits, `new_id`, `ontology/base.py:23`) | Notifications (`notifications/events.py:238`), console (`console_kb.js:230`, `:692`, `:705`), public KB page (`public_kb.js:341`), timeline (`user_home_ap.js:199`) | The read policy of the knowledge base (KB) |
| `/p?s={share token}` | Share token: 16 characters of `_TOKEN_ALPHABET` (31 characters, about 79 bits, `store/db.py:5003`, `:4101`), table `doc_shares` (`store/db.py:261`) | `POST /share` (`server.py:6259`), sitemap (`ensure_shares=True`), publish route (`accounts_routes/federation.py:584`), `claire share`, Telegram bot, timeline (`accounts/user_home.py:162`) | Each person who has the token. The page checks only `hidden`, not the visibility of the KB (`server.py:6292`) |
| `/ap/objects/{public ID}` | Public ID of `federation_objects` (`store/db.py:1224`) | Fedify sidecar (`sidecar/fedify/src/sidecar.ts:1235`), edge `location ^~ /ap/` | ActivityPub clients. A browser gets 406 (`sidecar.ts:292`) |

**[code]** Notes:

- A note is always public (`to` is the public collection, `notes.ts`; UI text `synd.compose.note`). It is in `federation_notes` of `client.db` (`store/client_db.py:340`).
- Its object ID is `/ap/objects/{public ID}`. Notes and published documents use one number generator (RT7, `FEDERATION.md` §25): the note route refuses a number that `claire.db` has.
- `Note.url` is the object ID (`sidecar/fedify/src/notes.ts:60`), because Claire has no web page for one note. Thus "copy link" in Mastodon gives `/ap/objects/{id}`, and a browser gets 406 there (owner observation 2026-11-23, Mastodon 4.7.6).
- `Article.url` is the share page (`sidecar.ts:772`). Thus "copy link" of an article gives `/p?s={token}`.

**[code]** The timeline of the account home (`user_home_ap.js:188`) shows notes and articles together, with the filters all, articles, notes. An article item has two links: "open in workspace" (document ID) and "read shared page" (`/p?s=`). A note item has no link. A reply or a quote shows the raw object IRI (`user_home_ap.js:227`, `:231`).

**[code]** Other forms of the same body: `/p/presentation?s=` and `?id=…&kb=` (`reader.js:1613`, `cli.py:3391`); `/document/raw?id=` and `/document/export?id=` (`app.js:2054`, `:5302`); `/p?id=` (`syndication_live.js:857`), which no route reads (defect: always 404); analytics virtual paths `/doc/{document ID}` and `/p/{document ID}`; `/ap/kb/{number}/documents/{contribution}` (signed server-to-server requests only); `/render/doc/{id}` on the usercontent host (source of the reader frame).

**[code]** More facts that limit the design:

- The reader does not write the document into the address bar (`pushState(next, '')`, `app.js:501`). A user cannot copy the URL of an open document from the address bar.
- One document can have many share tokens. No caller gives `ttl`. No route revokes a token.
- The document ID is not secret: the API, MCP results, logs, and Google Analytics get it.
- A document row belongs to one KB. A contribution copy gets a new document ID (`collaboration/contributions.py:93`).
- The workspace path changes when the username, the server mode, or the KB changes.
- An ActivityPub object ID cannot change after delivery. A remote server keeps its copy of an object, with the `url` of the time of the delivery.

## 3. Cause

Each need got its own identifier and its own path:

1. **Position in the workspace**: document ID. Not secret, thus no access for persons outside a private KB.
2. **Access for persons outside the KB**: share token. Many tokens for one document, and a different page.
3. **Protocol identity**: public ID. Only published documents and notes have it, and it cannot change.
4. **Notes**: no web page, thus the protocol identity is also the link.

R1 and R4 need one identifier and one path for 1, 2, and 4, and a fixed relation from 3 to them.

## 4. Terms (proposal, contract review of `GLOSSARY.md` before A1)

| Concept | English | Korean (proposal) | Note |
| :--- | :--- | :--- | :--- |
| A short public post that an account writes in Claire (`Note`) | note | 노트 | The UI uses 노트 now (22 catalog strings). Not 글: an ingested document is also 글. Not 게시물: see the next rows. |
| An ingested document as a timeline item (`Article`) | article | 아티클 | The owner says 아티클 (적재 본문). The UI says 발행 문서 (filter) and KB 발행 (chip). One concept must have one word (O4). |
| A note or an article in a timeline | post | 게시물 | The UI uses it now (`home.flow.timeline`, `home.timeline.empty`), and Mastodon uses it. |
| The one URL of a note or a document | permalink | 고유 주소 | Neutral for the kind and for the publication state. |
| The last path segment of a permalink | address token | 주소 토큰 | |
| The flag that lets each person with the permalink read a document of a private KB | link access | 링크 공개 | Documents only. A note is always public. |

## 5. Decisions in this proposal

| ID | Decision | Reason |
| :--- | :--- | :--- |
| U1 | The permalink of a note or a document is `{origin}/p/{address token}`. One form for the two kinds. The form does not show the kind. It has no namespace segment. | R1, R4. The address must not change when the username, the server mode, or the KB changes. The server finds the kind and the owner from the token. |
| U2 | The address token has 16 random characters of `_TOKEN_ALPHABET`. The server gives it when it makes the note or the document row. It never changes. It is unique over the documents of `claire.db` and the notes of `client.db`: the generator refuses a token that one of the two has and makes another one (the method of RT7). | An existing share token can become the address token without a change, thus `/p?s=X` becomes `/p/X`. About 79 random bits stop guessing. A number (public ID) would let a person count up to documents with link access. |
| U3 | Access is a property of the note or the document, not of the URL. See §6. | The URL of the owner and the URL that the owner sends are the same. |
| U4 | Link access is a flag of the document (`documents.link_access`, 0 or 1). The share action sets 1 and returns the permalink. The stop-sharing action sets 0. A caller with the current authorization of `POST /share` can set 1. A caller with write access to the KB can set 0. No expiry. No second token. | The URL stays, the access changes. A new token would make a second URL (R1). If a permalink leaks, the owner sets 0. |
| U5 | Old forms stop. Server and client code never make them again. Old web links redirect with 308: `{workspace}?doc={document ID}`, `/p?s={token}` (each valid token, also the older tokens of the document), `/p/presentation?s=` and `?id=`. A value that the server does not find, or that the caller cannot read, gives 404. No fallback to the workspace. | R2. Links in mail, chats, bookmarks, and search indexes stay usable. |
| U6 | When a document or a note opens (reader of the workspace, post of a timeline), the client writes the permalink into the address bar (`pushState` with the URL). When it closes, the address bar shows the URL of the screen again. The permalink names the note or the document, not the screen around it: in the app, the client keeps the current screen; a new page load selects the screen with §6. | The URL that the user sees is the URL that the user shares (R1). |
| U7 | Each representation of a document is under its permalink: `/p/{t}/presentation`, `/p/{t}/raw`, `/p/{t}/export/{format}`. The access policies of the raw file and the export do not change. `/p/images/{filename}` stays: `images` is not a valid token (length, and `i` is not in the alphabet). | One root for one body (R2). |
| U8 | Page metadata. Each view writes the canonical link `/p/{t}`, Open Graph tags, and JSON-LD. `robots` is `index, follow` only for a note and for a document of a public KB. A document that only link access makes readable gets `noindex` and is not in the sitemap. The sitemap lists `/p/{t}` for documents of public KBs and makes no tokens. Responses under `/p/` send `Referrer-Policy: strict-origin-when-cross-origin` or a stricter value. Google Analytics keeps the virtual path with the document ID and never gets the address token. | Link previews must work for each permalink. A link-only permalink is a capability: search engines and other sites must not get it. **This changes the current behavior**: all share pages have `index, follow` now. |
| U9 | Timeline. A note and an article have the same actions: "open" (the permalink) and "copy link" (the permalink). The two links of an article ("open in workspace", "read shared page") go away. A post never shows an object ID: a reply or a quote of an own note or document shows its permalink, a remote one shows its remote `url`. | R3, R4. One action gives one URL for each kind of post. |
| U10 | ActivityPub. The object ID stays `/ap/objects/{public ID}` for notes and articles. `Note.url`, `Article.url`, and the link in `Article.content` become the permalink. A request to an object ID that does not accept ActivityPub gets 303 to the permalink (`onNotAcceptable` of the sidecar, both kinds), not 406. A request to a permalink that accepts only ActivityPub gets 303 to the object ID when the note or the document is federated. The page of a federated note or document has `<link rel="alternate" type="application/activity+json" href="{object ID}">`. | Delivered IDs cannot change. A remote copy can keep its old `url` (the object ID for notes, `/p?s=` for articles): the 303 and the 308 send these old links to the permalink. 303, not 308: the object ID did not move; the server only selects a representation. |
| U11 | Input. `extract_own_share_token` and `resolve_document_targets` (CLI, Telegram bot, MCP targets) read the permalink and the old forms of U5, with the same mapping as the redirects. | Old links stay in messages. Reading an old link is not making one. |

## 6. Views and access for `GET /p/{address token}`

**Post view.** One view for the two kinds: the author, the time, the kind, the body (the text of a note, or the reader of a document), and, for a federated item, the reactions and the thread (글타래). It is the opened form of a timeline post (R3). The author sees the actions of the owner.

**Workspace view.** The workspace of the KB of the document with the reader open, as `?doc=` gives now.

The first rule that applies gives the answer:

1. No note and no document has the token: 404. A deleted note (tombstone row): 410.
2. A note: post view.
3. A hidden document, and the caller cannot see hidden documents of the KB (`_sees_hidden`): 404.
4. The caller is a member of the KB (personal KB: the owner; shared KB: a member role): workspace view.
5. The KB is public, or the document has link access 1: post view. For a public KB, the post view has a switch to the workspace view. The switch does not change the URL.
6. Else: the answer that the workspace path of the KB gives to this caller now (sign-in redirect or 404).

## 7. Data and migration

The migration completes itself at the start. The operator does nothing.

1. `claire.db`: add `documents.address_token TEXT` with a `UNIQUE` index and `documents.link_access INTEGER NOT NULL DEFAULT 0`. `client.db`: add `federation_notes.address_token TEXT` with a `UNIQUE` index.
2. For each document: if it has a valid row in `doc_shares`, the newest valid token becomes the address token (the selection of `create_doc_share(reuse_existing=True)`) and `link_access` becomes 1. Else the server makes a new token and `link_access` stays 0. Each existing share link keeps its access.
3. For each note, also a deleted note: a new token.
4. `doc_shares` stays as the read-only alias table for the 308 redirects. The server writes no new rows. Column `expires_at` has no use; remove it.
5. Each path that makes a row gives a new token: ingestion, contribution copy (`collaboration/contributions.py:93`, `:1260`), graph bundle import (`ops/graph_bundle.py:824`), note creation. A path that replaces a row keeps the token: `INSERT OR REPLACE` (`store/db.py:2730`, `ops/graph_bundle.py:1143`), the in-place re-ingestion of `f0b3efe8`, note edits. Account backup and restore keep the tokens.

## 8. Phases

| Phase | Work |
| :--- | :--- |
| A1 | Schema and migration (§7), resolver over the two databases, `GET /p/{t}` with the two views and the metadata (§6, U8), the 308 redirects (U5), `POST /share` returns the permalink and sets link access, stop-sharing route. |
| A2 | Client: address bar (U6), timeline actions (U9), each link builder uses the permalink: console, public KB page, notifications, syndication (also the defect `/p?id=`), account home, Telegram bot, CLI, MCP results. Representation paths (U7). |
| A3 | Federation (U10): `url` of notes and articles, `Article.content`, sidecar `onNotAcceptable` with 303 for the two kinds, 303 on `/p/{t}`, alternate link. Measure with Mastodon if an `Update` changes the stored `url` without the "edited" mark. Send `Update` for existing objects only if the measurement shows this. The 303 works also for copies that do not change. |
| A4 | Public site (U8): sitemap, `robots.txt`, canonical. Update `docs/implementation/` (PUBLIC_SITE, FEDERATION, WEBUI, API_HTTP_CONTRACT, COMMANDS, openapi) and `GLOSSARY.md` (§4 after the review). |

Tests: E2E "open a document in the workspace, copy the permalink from the address bar, open it as an anonymous caller" for a public KB and for a private KB with link access 1 and 0; E2E "open a note and an article from the timeline, copy each link, both have the form `/p/{token}`"; E2E for the 308 of an old `/p?s=` link and the 303 of an object ID. Unit tests: migration (I/O), resolver over the two databases, the sidecar redirect (interface between modules).

## 9. Open items for the owner

| ID | Question | Proposal |
| :--- | :--- | :--- |
| O1 | Path prefix of the permalink. | `/p/`. It is one of the three forms that the owner named, it is neutral for notes and documents, and the old share links map one to one (`/p?s=X` to `/p/X`). `/doc/` (revision 1) is a word for documents only (R4). |
| O2 | Does R2 also apply to the JSON API (`GET /document?id=`, `/node?id=`, `/document/presentation?id=`, `/documents?id=`)? | Not in this design. The API uses the document ID, and a user does not see these URLs. A separate design. |
| O3 | Does R2 apply to other user-visible query forms (`?node=` of the graph, `app.js:97`, `:122`)? | Not in this design. A separate item. |
| O4 | Terms of §4: 노트 for a note; one word for an article (아티클, 발행 문서, or KB 발행); 게시물, 고유 주소, 주소 토큰, 링크 공개. | 노트, 아티클, 게시물, 고유 주소, 주소 토큰, 링크 공개. |
| O5 | The view for a caller that is not a member of a public KB (anonymous or another account). | Post view with the switch (§6 rule 5): the same view as for a note, with metadata for previews. Now the same caller gets the workspace view with `?doc=` and the share page with `/p?s=`. |

## 10. Rejected alternatives

- **Two paths, one for each kind** (for example `/notes/{id}` and `/doc/{t}`): the user must know the kind (R4).
- **Namespace in the path** (`/@{username}/{id}`, as Mastodon does; `/kb/{number}/…`): the URL changes with the username, the server mode, or the KB. A document of a shared KB has no username.
- **Public ID in the path** (`/p/117660457792953794`): documents that are not published have no public ID, and a person can count up to documents with link access.
- **Document ID in the path**: the document ID is not secret, thus it cannot give access to a document of a private KB.
- **Object ID as the permalink**: delivered IDs cannot change, thus two ID forms stay. The edge would need to divide a Claire path by `Accept`. `url`, the redirects, and the alternate link give the same result to the user.
- **Keep `/p?s=` and add redirects only**: the query form stays (R2), and a document keeps many share URLs (R1).
