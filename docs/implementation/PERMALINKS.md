# Permalinks and Entity Addresses

Basis: the commit that adds this document (2026-11-23) · Readers: agents that change document, note, or entity URLs, share links, or the workspace entry · Design: [`PERMALINK_UNIFICATION_DESIGN.md`](../design/PERMALINK_UNIFICATION_DESIGN.md)

This document tells what the code does now. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1.

## 1. State of the design phases

| Phase | State |
| :--- | :--- |
| B1 bundle paths | Done |
| A1 registry, route, redirects, share actions | Done for documents. The Silvercord route gives 404 for a note: its view is not made |
| Entity address (design §6.2) | Done: the graph for a caller that can read the knowledge base, the page for link access alone, the share actions, and the panel actions (§3.1, §5) |
| Terms (design §4: entity, node, graph, relation) | Done in the UI text (`ko.json`, `en.json`), two bot messages, and `GLOSSARY.md` §1.5, §1.6, §1.11. API names (`GET /node`) and code identifiers are not changed |
| A2 client (address bar, timeline actions, link builders of the console and the notifications, representation paths, terms) | Not done, except the items of §5 |
| A3 federation | `Article.url` and the link in `Article.content` are the permalink. Not done: `Note.url`, the 303 redirects, the alternate link |
| A4 public site | `robots.txt`, sitemap, and canonical link use the permalink. `GLOSSARY.md` is not changed |

## 2. Registry

- **[code]** Table `permalinks` in `claire.db` (schema v43, [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). Module `src/claire/store/permalinks.py`.
- **[code]** The address token has 16 characters of `23456789abcdefghjkmnpqrstuvwxyz`. The path words are constants of `store/permalinks.py`: `PERMALINK_PATH_PREFIX` (`/silvercord/`) and `ENTITY_PATH_PREFIX` (`/entity/`). `silvercord` and `entity` are reserved USERNAMEs (`accounts/username_policy.py`).
- **[decision]** `/silvercord/` is only for the kinds that can be ActivityPub objects: a document (`Article`) and a note (`Note`) (`ACTIVITYPUB_KINDS`). A kind that is not an ActivityPub object gets its own path word, as the entity does. A later kind follows this rule (owner, 2026-11-23).
- **[code]** A row is made by the migration (documents and entities that exist) and by `ensure` at the first use of an item without a row: the share action, the sitemap, an old link (`?doc=`, `?node=`). The insert paths of documents and entities have no hook. A note has no row until code calls `ensure_note`; no code calls it now.
- **[code]** `merge_documents` (near-duplicate merge) makes the tokens of the losers aliases of the token of the keeper. The keeper gets link access 1 when one document of the group had it.
- **[code]** A purge sets `gone_at` and link access 0 (`stats["gone_permalinks"]`). The deletion or the reset of a knowledge base and the purge of an account do the same for each document and entity of the knowledge base (`mark_kb_gone`). The route also sets `gone_at` when it does not find the document.
- **[code]** `ensure_entity` gives an entity the row of the same knowledge base and identity (type and normalized name) when exactly one such row has no live entity. Else it makes a new row.

## 3. Route `GET /silvercord/{token}`

**[code]** `permalink_route` in `src/claire/api/server.py`. Route rule: `_system("public", "none")`. The first rule that applies gives the answer:

1. The token does not have the format, or the registry does not have it: 404.
2. The token is an alias: 308 to the permalink of its target.
3. The item is gone: 410.
4. The item is a note: 404 (no view now). The item is an entity: 404 (an entity is not at a Silvercord path).
5. The caller is a member of the knowledge base of the document: the workspace page of the knowledge base, with `<meta name="claire-open-item" content="document:{document ID}">` and a canonical link. The client (`app.js`, `OPEN_ITEM`) opens the document.
6. The document is hidden or not extracted: 404.
7. The caller can read the knowledge base (`_kb_decide`, capability `read`), or the document has link access 1: the item view (`shared_html`, the page of the old share link). When only link access makes the document readable, the response has `X-Robots-Tag: noindex, nofollow` and the `robots` meta element `noindex, nofollow`. The item view never has the raw file link.
8. Else: the answer of the workspace path for this caller (sign-in redirect, private profile page, or 404). This answer has no mark and no ID of the document.

**[code] Membership.** The route is public, thus the authentication layer validates no credential for it. `_caller_is_member` gives the request to the full application again (`_dispatch_workspace`) as `GET {workspace path}whoami` with the headers of the caller. In this internal request only, `whoami` has the key `member`: true for the account of a personal knowledge base and for an active member of a shared one. The workspace page of rule 5 comes from a second internal request. The scope mark `claire.permalink_dispatch` tells the workspace route that the query of the internal request is not an old link.

**[code] Workspace path.** `/kb/{number}/` for a shared knowledge base, `/@{username}/` for a personal one. Each active account has a USERNAME: the code makes an account without one only as the administrator row in the status `setup_required`, before the setup, and a trigger stops the removal of a USERNAME. For a knowledge base whose account is not active or has no USERNAME, the route has no workspace path and no member.

### 3.1 Route `GET /entity/{token}`

**[code]** `entity_address_route`. Route rule: `_system("public", "none")`.

1. The token does not have the format, the registry does not have it, or it is the token of a document or a note: 404.
2. The entity row does not exist: the route sets `gone_at`, and the answer is 410.
3. The caller is a member of the knowledge base, or can read it (`_kb_decide`, capability `read`): the workspace page of the knowledge base with `<meta name="claire-open-item" content="entity:{entity ID}">` and the canonical link `/entity/{token}`. The client starts in the graph view and, after the graph data came, calls `focusNode` one time: the entity is selected, the camera goes to its node, and the context panel shows it. An entity that the graph data of the caller does not have is not selected.
4. The entity has link access 1: the page of `_entity_link_page` (template `api/templates/entity_link.html`). It has the graph of the entity and the entities that have a direct relation with it (the graph engine of `static/js/graph/`, the entity selected), and a panel with the name, the type, the aliases, the observations, the relations, and the source documents. A source document is a link (its Silvercord address) only when it has link access itself; the other source documents are only in a count. The page has no internal ID: the graph data uses `e0`, `e1`, …. Headers: `X-Robots-Tag: noindex, nofollow`, `Cache-Control: no-store`.
5. Else: the answer of the workspace path for this caller, without a mark and without the ID of the entity.

## 4. Old links

| Old form | Answer |
| :--- | :--- |
| `GET /p?s={token}` | 308 to `/silvercord/{address token}` when the registry has the token (also an alias) for a document; else 404. No page is at `/p` now |
| `GET {workspace}?doc={document ID}` | 308 to the permalink when the document is in the knowledge base of the path; else 404 |
| `GET {workspace}?node={entity ID}` | 308 to `/entity/{address token}` when the entity is in the knowledge base of the path; else 404 |
| `/p/presentation?s=` and `?id=` | Not changed |

`extract_own_share_token` and `resolve_document_targets` read a permalink and the old share link as input (CLI, Telegram bot).

## 5. Share actions and link builders

- **[code]** `POST {namespace}/share` (`_namespaced("read", "share")`): sets link access 1. Response: `{"token", "path": "/silvercord/{token}", "link_access": true, "kb_id"}`. A second call gives the same token.
- **[code]** `DELETE {namespace}/share` (`_namespaced("write", "share")`) with `{"doc_id"}`: sets link access 0. The token stays.
- **[code]** `POST {namespace}/entities/{entity_id}/share` (`_namespaced("read", "share")`) sets link access of an entity to 1, and `DELETE` on the same path (`_namespaced("write", "share")`) sets 0. Response: `{"token", "path": "/entity/{token}", "link_access", "kb_id"}`. An entity that the caller does not see gives 404.
- **[code]** `GET {namespace}/node?id=` has the key `address`: `{"path": "/entity/{token}", "link_access", "kb_public"}`, or null when the database is read-only. The context panel of an entity (`app.js`) has the button "링크 복사" (`#node-copy-link-btn`) and, when the knowledge base is not public and the scope of the caller is `owner` or `write`, the button of link access (`#node-link-access-btn`).
- **[code]** These builders give the permalink: the reader share button (it uses `path`), the Telegram bot, `claire share`, the sitemap, the canonical link and `og:url` of the item view, the timeline field `share_url`, `Article.url` and `Article.content` of the internal federation API.
- **[code]** These places still give old forms: `?doc=` links of the console, the public KB page, and the notifications (the server answers them with 308); `/p/presentation?s=` and `?id=`; `/document/raw?id=` and `/document/export?id=`.
