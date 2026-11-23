# Permalinks

Basis: the commit that adds this document (2026-11-23) · Readers: agents that change document, note, or entity URLs, share links, or the workspace entry · Design: [`PERMALINK_UNIFICATION_DESIGN.md`](../design/PERMALINK_UNIFICATION_DESIGN.md)

This document tells what the code does now. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1.

## 1. State of the design phases

| Phase | State |
| :--- | :--- |
| B1 bundle paths | Done |
| A1 registry, route, redirects, share actions | Done for documents. The registry has rows for entities. The route gives 404 for a note or an entity: their views are not made |
| A2 client (address bar, timeline actions, link builders of the console and the notifications, representation paths, terms) | Not done, except the items of §5 |
| A3 federation | `Article.url` and the link in `Article.content` are the permalink. Not done: `Note.url`, the 303 redirects, the alternate link |
| A4 public site | `robots.txt`, sitemap, and canonical link use the permalink. `GLOSSARY.md` is not changed |

## 2. Registry

- **[code]** Table `permalinks` in `claire.db` (schema v43, [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). Module `src/claire/store/permalinks.py`.
- **[code]** The address token has 16 characters of `23456789abcdefghjkmnpqrstuvwxyz`. The path word is in one constant, `PERMALINK_PATH_PREFIX` (`/silvercord/`). `silvercord` is a reserved USERNAME (`accounts/username_policy.py`).
- **[code]** A row is made by the migration (documents and entities that exist) and by `ensure` at the first use of an item without a row: the share action, the sitemap, an old link (`?doc=`, `?node=`). The insert paths of documents and entities have no hook. A note has no row until code calls `ensure_note`; no code calls it now.
- **[code]** `merge_documents` (near-duplicate merge) makes the tokens of the losers aliases of the token of the keeper. The keeper gets link access 1 when one document of the group had it.
- **[code]** A purge sets `gone_at` and link access 0. The route also sets `gone_at` when it does not find the document.
- **[code]** `ensure_entity` gives an entity the row of the same knowledge base and identity (type and normalized name) when exactly one such row has no live entity. Else it makes a new row.

## 3. Route `GET /silvercord/{token}`

**[code]** `permalink_route` in `src/claire/api/server.py`. Route rule: `_system("public", "none")`. The first rule that applies gives the answer:

1. The token does not have the format, or the registry does not have it: 404.
2. The token is an alias: 308 to the permalink of its target.
3. The item is gone: 410.
4. The item is a note or an entity: 404 (no view now).
5. The caller is a member of the knowledge base of the document: the workspace page of the knowledge base, with `<meta name="claire-open-item" content="document:{document ID}">` and a canonical link. The client (`app.js`, `OPEN_ITEM`) opens the document.
6. The document is hidden or not extracted: 404.
7. The caller can read the knowledge base (`_kb_decide`, capability `read`), or the document has link access 1: the item view (`shared_html`, the page of the old share link). When only link access makes the document readable, the response has `X-Robots-Tag: noindex, nofollow` and the `robots` meta element `noindex, nofollow`. The item view never has the raw file link.
8. Else: the answer of the workspace path for this caller (sign-in redirect, private profile page, or 404).

**[code] Membership.** The route is public, thus the authentication layer validates no credential for it. `_caller_is_member` gives the request to the full application again (`_dispatch_workspace`) as `GET {workspace path}whoami` with the headers of the caller. In this internal request only, `whoami` has the key `member`: true for the account of a personal knowledge base and for an active member of a shared one. The workspace page of rule 5 comes from a second internal request. The scope mark `claire.permalink_dispatch` tells the workspace route that the query of the internal request is not an old link.

**[code] Workspace path.** `/kb/{number}/` for a shared knowledge base, `/@{username}/` for a personal one. An account without a USERNAME has no workspace path: each caller gets rule 6 to 8.

## 4. Old links

| Old form | Answer |
| :--- | :--- |
| `GET /p?s={token}` | 308 to `/silvercord/{address token}` when the registry has the token (also an alias) for a document; else 404. No page is at `/p` now |
| `GET {workspace}?doc={document ID}` | 308 to the permalink when the document is in the knowledge base of the path; else 404 |
| `GET {workspace}?node={entity ID}` | 308 to the permalink of the entity when it is in the knowledge base of the path (the permalink then gives 404, §3 rule 4); else 404 |
| `/p/presentation?s=` and `?id=` | Not changed |

`extract_own_share_token` and `resolve_document_targets` read a permalink and the old share link as input (CLI, Telegram bot).

## 5. Share actions and link builders

- **[code]** `POST {namespace}/share` (`_namespaced("read", "share")`): sets link access 1. Response: `{"token", "path": "/silvercord/{token}", "link_access": true, "kb_id"}`. A second call gives the same token.
- **[code]** `DELETE {namespace}/share` (`_namespaced("write", "share")`) with `{"doc_id"}`: sets link access 0. The token stays.
- **[code]** These builders give the permalink: the reader share button (it uses `path`), the Telegram bot, `claire share`, the sitemap, the canonical link and `og:url` of the item view, the timeline field `share_url`, `Article.url` and `Article.content` of the internal federation API.
- **[code]** These places still give old forms: `?doc=` links of the console, the public KB page, and the notifications (the server answers them with 308); `/p/presentation?s=` and `?id=`; `/document/raw?id=` and `/document/export?id=`.
