# One Address for Each Ingested Document

- **Document Identifier**: `DESIGN-DOCUMENT-ADDRESS-20261123`
- **Status**: Proposal. Not implemented. Open items O1 to O5 wait for the owner.
- **Date**: 2026-11-23 (base commit `80e3fb14`)
- **Related**: [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (§4.3 item 3 and the share link row of §6.2: this design replaces them when it is implemented), [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (RD21, RT7: not changed), [`PUBLIC_SITE.md`](../implementation/PUBLIC_SITE.md), [`FEDERATION.md`](../implementation/FEDERATION.md)

## 1. Owner request (2026-11-23)

> * 현재 적재 본문은 doc, p, /ap/objects 세 가지로 혼재하는 것으로 보인다.
> * 이용자는 서버 내에서 사용하는 오브젝트의 용도와 무관하게 하나의 URL만 인식하여 본문에 접근하거나 공유할 수 있어야 한다.
> * 물음표 쓰고 이퀄에 코드 적는 형식의 URL은 폐기해야한다.

Requirements:

- **R1.** An ingested document has one URL. A user opens, copies, and shares the same URL. The use of the object in the server (workspace, share, federation) does not change the URL.
- **R2.** User-visible URLs of a document have no query form `?name=code`.

## 2. Facts at the base commit

**[code]** Three identifiers give three URL forms.

| Form | Identifier | Where it comes from | Who can read |
| :--- | :--- | :--- | :--- |
| `{workspace}?doc={document ID}` (`{workspace}` is `/`, `/@{username}/`, `/kb/{number}/`, or a theme path) | Document ID: `doc_` + 12 hexadecimal digits (48 random bits, `new_id`, `ontology/base.py:23`) | Notifications (`notifications/events.py:238`), console links (`console_kb.js:230`, `:692`, `:705`), public KB page (`public_kb.js:341`) | The read policy of the knowledge base (KB) |
| `/p?s={share token}` | Share token: 16 characters of `_TOKEN_ALPHABET` (31 characters, about 79 bits, `store/db.py:5003`, `:4101`) in table `doc_shares` (`store/db.py:261`) | `POST /share` (`server.py:6259`), sitemap (`ensure_shares=True`), publish route, `claire share`, Telegram bot, user home (`accounts/user_home.py:162`) | Each person who has the token. The page checks only `hidden`, not the visibility of the KB (`server.py:6292`) |
| `/ap/objects/{public ID}` | Public ID of `federation_objects` (`store/db.py:1224`). Notes use the same number space (RT7) | Fedify sidecar (`sidecar/fedify/src/sidecar.ts:1235`), edge `location ^~ /ap/` | ActivityPub clients. A browser gets 406 (`sidecar.ts:292`) |

**[code]** Related forms of the same body:

1. Presentation: `/p/presentation?s={token}` and `/p/presentation?id={document ID}&kb=…` (`reader.js:1613`, `cli.py:3391`).
2. Downloads: `/document/raw?id=` and `/document/export?id=` (`app.js:2054`, `:5302`).
3. `/p?id={document ID}` (`syndication_live.js:857`). No route reads `id`. This link always gives 404 (defect).
4. Analytics virtual paths `/doc/{document ID}` (`app.js:506`, `reader.js:811`) and `/p/{document ID}`. No route serves them.
5. `/ap/kb/{number}/documents/{contribution}`: knowledge federation object, signed server-to-server requests only.
6. `/render/doc/{id}` on the usercontent host: the source of the reader frame. A user does not open it.

**[code]** More facts that limit the design:

- The reader does not write the document into the address bar. `pushAppHistory` calls `history.pushState(next, '')` without a URL (`app.js:501`). The client reads `?doc=` only when the page loads (`app.js:363`). Thus a user cannot copy the URL of an open document from the address bar.
- One document can have many share tokens. `create_doc_share(reuse_existing=True)` returns the newest valid one. No caller gives `ttl`, thus no token expires now. No route revokes a token.
- The document ID is not secret: the API, MCP results, logs, and Google Analytics (`page_location`) get it.
- A document row belongs to one KB. A contribution copy gets a new document ID (`collaboration/contributions.py:93`). All KBs are in one database file (`KnowledgeBaseManager.resolve_share_token`).
- The workspace path of a document changes when the username, the server mode, or the KB changes. The namespace design needed 308 redirects for the mode change for this reason.
- An ActivityPub object ID cannot change after delivery. Remote servers keep it for replies, favourites, boosts, and quotes.

## 3. Cause

Each of the three needs got its own identifier:

1. **Position in the workspace** uses the document ID. It is not secret, thus it cannot give access to a document of a private KB.
2. **Access for a person outside the KB** uses a share token. A document can have many tokens, and the share page is a different view.
3. **Protocol identity** uses the public ID. It exists only for published documents and cannot change.

R1 needs one identifier that can do 1 and 2, and a fixed relation from 3 to it.

## 4. Decisions in this proposal

| ID | Decision | Reason |
| :--- | :--- | :--- |
| U1 | The URL of a document is `{origin}/doc/{address token}`. We call it the **document address**. It has no namespace segment. | The address must not change when the username, the server mode, or the KB changes. A document row belongs to one KB, thus the server finds the KB from the token. |
| U2 | The **address token** has 16 random characters of `_TOKEN_ALPHABET` (the format of the share token). The server gives it when it makes the document row. It never changes. It is unique in the server (`UNIQUE` index). | An existing share token can become the address token without a change. About 79 random bits stop guessing. |
| U3 | Access is a property of the document, not of the URL. See §5. | The address of the owner and the address that the owner sends are the same, thus the access must not depend on the URL. |
| U4 | **Link access** is a flag of the document (`documents.link_access`, 0 or 1). The share action sets 1 and returns the document address. The stop-sharing action sets 0. A caller with the current authorization of `POST /share` can set 1. A caller with write access to the KB can set 0. No expiry. No new token for a document. | This is the one-URL share model: the URL stays, the access changes. A new token would make a second URL (R1). If an address leaks, the owner sets 0. |
| U5 | The old query forms stop. Server and client code never make them again. Old web links redirect with 308: `{workspace}?doc={document ID}`, `/p?s={token}` (each valid token of the document, also the older tokens), `/p/presentation?s=` and `?id=`. A value that the server does not find, or that the caller cannot read, gives 404. No fallback to the workspace. | R2. Links in mail, chats, bookmarks, and search indexes stay usable. The rule "web links keep 308" applies. |
| U6 | When the reader opens a document, the client writes the document address into the address bar (`pushState` with the URL). When the reader closes, the address bar shows the workspace URL again. The share button copies the same address. | The address that the user sees is the address that the user shares (R1). |
| U7 | Each representation of the body is under the document address: `/doc/{t}/presentation`, `/doc/{t}/raw`, `/doc/{t}/export/{format}`. The access policies of the raw file and the export do not change. | One root for one body. No `?id=` in a link that a user sees (R2). |
| U8 | Page metadata. Both views (§5) write the canonical link `/doc/{t}`, Open Graph tags, and JSON-LD. `robots` is `index, follow` only when the KB is public. A document that only link access makes readable gets `noindex` and is not in the sitemap. The sitemap lists `/doc/{t}` for documents of public KBs and makes no tokens. `robots.txt` allows `/doc/` and drops `/p?`. Responses under `/doc/` send `Referrer-Policy: strict-origin-when-cross-origin` or a stricter value. Google Analytics keeps the virtual path with the document ID and never gets the address token. | Link previews must work for each address. A link-only address is a capability: search engines and other sites must not get it. **This changes the current behavior**: all share pages have `index, follow` now. |
| U9 | ActivityPub. The object ID stays `/ap/objects/{public ID}`. `Article.url` and the link in `content` become the document address, never null. A request to the object ID that does not accept ActivityPub gets 303 to `Article.url` (`onNotAcceptable` of the sidecar), not 406. A request to the document address that accepts only ActivityPub gets 303 to the object ID. The page of a published document has `<link rel="alternate" type="application/activity+json" href="{object ID}">`. The UI does not show the object ID as a link to open or copy. | The delivered IDs cannot change, and notes share the number space. With `url`, the two redirects, and the alternate link, a person sees and pastes only the document address, also in the search box of a fediverse client. 303, not 308: the object ID did not move; the server only selects a representation. |
| U10 | Input. `extract_own_share_token` and `resolve_document_targets` (CLI, Telegram bot, MCP targets) read the document address. They also read the old forms of U5, with the same mapping as the 308 redirects. | Old links stay in messages. Reading an old link is not making one. |

## 5. Access rules for `GET /doc/{address token}`

The server finds the document and its KB from the token. Then the first rule that applies gives the answer:

1. No document has the token: 404.
2. The document is hidden, and the caller cannot see hidden documents of the KB (`_sees_hidden`): 404.
3. The caller can read the KB (the current read policy of the KB for this caller): the **workspace view** of that KB with the reader open, as `?doc=` does now.
4. The document has link access 1: the **reader view** (the current share page, `shared_html`). It shows no other part of the KB.
5. Else: the same answer that the workspace path of the KB gives to this caller now (sign-in redirect or 404).

## 6. Data and migration

The migration completes itself at the start. The operator does nothing.

1. Add `documents.address_token TEXT` with a `UNIQUE` index and `documents.link_access INTEGER NOT NULL DEFAULT 0`.
2. For each document: if it has a valid row in `doc_shares`, the newest valid token becomes the address token (the same selection as `create_doc_share(reuse_existing=True)`) and `link_access` becomes 1. Else the server makes a new token and `link_access` stays 0. Thus each existing share link keeps its access.
3. `doc_shares` stays as the read-only alias table for the 308 redirects of U5. The server writes no new rows. Column `expires_at` has no use; remove it.
4. Each path that makes a document row gives a new address token: ingestion, contribution copy (`collaboration/contributions.py:93`, `:1260`), graph bundle import (`ops/graph_bundle.py:824`). A path that replaces a row keeps its token: `INSERT OR REPLACE` (`store/db.py:2730`, `ops/graph_bundle.py:1143`) and the in-place re-ingestion of `f0b3efe8`. Account backup and restore keep the token, so that the address stays.
5. If a new token collides with the `UNIQUE` index, the server makes another one.

## 7. Phases

| Phase | Work |
| :--- | :--- |
| A1 | Schema and migration (§6), resolver, `GET /doc/{t}` with the two views and the metadata (§5, U8), the 308 redirects (U5), `POST /share` returns the document address and sets link access, stop-sharing route. |
| A2 | Client: address bar (U6), each link builder uses the document address: console, public KB page, notifications, syndication (also the defect `/p?id=`), user home, Telegram bot, CLI, MCP results. Presentation, raw, and export paths (U7). |
| A3 | Federation (U9): Claire internal object record (`url`, `content`), sidecar `onNotAcceptable`, 303 on `/doc/{t}`, alternate link. |
| A4 | Public site (U8): sitemap, `robots.txt`, canonical. Update `docs/implementation/` (PUBLIC_SITE, FEDERATION, WEBUI, API_HTTP_CONTRACT, COMMANDS, openapi). |

Tests: one E2E test for the flow "open a document, copy the address from the address bar, open it as an anonymous caller", for a public KB and for a private KB with link access 1 and 0; one E2E test for the 308 of an old `/p?s=` link. Unit tests: migration (I/O), resolver, the sidecar redirect (interface between modules).

## 8. Open items for the owner

| ID | Question | Proposal |
| :--- | :--- | :--- |
| O1 | Path prefix of the document address. | `/doc/`. It is the word that the owner used. Alternatives: `/d/` (shorter), `/p/{t}` (same prefix as the old share link, but `/p/images/` and `/p/presentation` are also there). |
| O2 | Does R2 also apply to the JSON API (`GET /document?id=`, `/node?id=`, `/document/presentation?id=`, `/documents?id=`)? | Not in this design. The API uses the document ID, not the address token, and a user does not see these URLs. A separate design can give them path forms. |
| O3 | Does R2 apply to other user-visible query forms that are not about a body (`?node=` of the graph, `app.js:97`, `:122`)? | Not in this design. A separate item. |
| O4 | A browser request to the object ID of a **note** (`/ap/objects/{id}` of a `Note`). | No change now (406). A note is not an ingested document. |
| O5 | New terms for the glossary: 문서 주소 (document address), 주소 토큰 (address token), 링크 공개 (link access). | A contract review of `GLOSSARY.md` before A1. |

## 9. Rejected alternatives

- **Document ID in the path** (`/doc/doc_…`): the document ID is not secret, thus it cannot give access to a document of a private KB. Sharing would need a second URL.
- **Object ID as the page URL** (the document address as the ActivityPub ID): delivered IDs cannot change, thus two ID forms stay. The edge would need to divide a Claire path by `Accept`. `url`, the redirects, and the alternate link give the same result to the user.
- **Namespace in the path** (`/@{username}/doc/{t}`, `/kb/{number}/doc/{t}`): the address would change with the username, the server mode, or the KB.
- **Public ID or a counter in the path** (`/doc/123`): a person can count up and find documents that have link access.
- **Keep `/p?s=` and add redirects only**: the query form stays (R2), and a document keeps many share URLs (R1).
