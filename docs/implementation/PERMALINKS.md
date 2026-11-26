# Permalinks and Entity Addresses

Basis: the commit that adds this document (2026-11-23) · Readers: agents that change document, note, or entity URLs, share links, or the workspace entry · Design: [`PERMALINK_UNIFICATION_DESIGN.md`](../design/PERMALINK_UNIFICATION_DESIGN.md)

This document tells what the code does now. Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1.

## 1. State of the design phases

| Phase | State |
| :--- | :--- |
| B1 bundle paths | Done |
| A1 registry, route, redirects, share actions | Done for documents and notes. `GET /silvercord/{token}` serves the unified Silvercord document view (`SQ1:B`, `SQ2:B+A1`) with `본문`/`의견` tabs, pull-out local graph, and `/silvercord/{token}/workspace` (`U8`) / `/silvercord/{token}/graph` (`SD2`). The item view of a note has the author, the time, and the text; it does not have the reactions and the thread |
| Entity address (design §6.2) | Done: the graph for a caller that can read the knowledge base, the page for link access alone, the share actions, and the panel actions (§3.1, §5) |
| Terms (design §4: entity, node, graph, relation) | Done in the UI text (`ko.json`, `en.json`), two bot messages, and `GLOSSARY.md` §1.5, §1.6, §1.11. API names (`GET /node`) and code identifiers are not changed |
| A2 client (address bar, timeline actions, link builders of the console and the notifications, representation paths, terms) | Not done, except the items of §5 and `/silvercord/{token}/workspace` (`U8`) |
| A3 federation | Done: `Article.url`, the link in `Article.content`, and `Note.url` (contract 1.17) are the permalink; an object ID gives 303 to the permalink for a request that does not accept ActivityPub; a permalink gives 303 to the object ID for a request that accepts only ActivityPub; the item views have the alternate link. Not done: the measurement with Mastodon (does a stored copy get the new `url`), and an `Update` for objects that are delivered |
| A4 public site | `robots.txt`, sitemap, and canonical link use the permalink. `GLOSSARY.md` is not changed |

## 2. Registry

- **[code]** Table `permalinks` in `claire.db` (schema v43, [`SCHEMA_VERSIONING.md`](SCHEMA_VERSIONING.md)). Module `src/claire/store/permalinks.py`.
- **[code]** The address token has 16 characters of `23456789abcdefghjkmnpqrstuvwxyz`. The path words are constants of `store/permalinks.py`: `PERMALINK_PATH_PREFIX` (`/silvercord/`) and `ENTITY_PATH_PREFIX` (`/entity/`). `silvercord` and `entity` are reserved USERNAMEs (`accounts/username_policy.py`).
- **[decision]** `/silvercord/` is only for the kinds that can be ActivityPub objects: a document (`Article`) and a note (`Note`) (`ACTIVITYPUB_KINDS`). A kind that is not an ActivityPub object gets its own path word, as the entity does. A later kind follows this rule (owner, 2026-11-23).
- **[code]** A row is made by the migration (documents and entities that exist when a process prepares the DB with `init_db`, once in each process) and by `ensure` at the first use of an item without a row: the share action, the sitemap, an old link (`?doc=`, `?node=`). The insert paths of documents and entities have no hook. A note gets its row when the account makes it (`notes_post`), and a note without a row gets one when the internal federation API or the timeline reads it (`ensure_note_token`, a short write connection to `claire.db`).
- **[code]** `merge_documents` (near-duplicate merge) makes the tokens of the losers aliases of the token of the keeper. The keeper gets link access 1 when one document of the group had it.
- **[code]** A purge sets `gone_at` and link access 0 (`stats["gone_permalinks"]`). The deletion or the reset of a knowledge base and the purge of an account do the same for each document and entity of the knowledge base (`mark_kb_gone`). The route also sets `gone_at` when it does not find the document.
- **[code]** `ensure_entity` gives an entity the row of the same knowledge base and identity (type and normalized name) when exactly one such row has no live entity. Else it makes a new row.

## 3. Route `GET /silvercord/{token}`

**[code]** `permalink_route` in `src/claire/api/server.py`. Route rule: `_system("public", "none")`. It resolves an optional session principal via `resolve_optional_session_principal` in `src/claire/api/security.py` (with S11 cookie-tossing protection). The first rule that applies gives the answer:

1. The token does not have the format, or the registry does not have it: 404.
2. The token is an alias: 308 to the permalink of its target.
3. The item is gone: 410.
4. The item is a note: a request that accepts only ActivityPub gets 303 to `/ap/objects/{public ID}`; each other request gets the item view of the note (`_note_item_page`, template `api/templates/note_item.html`): the author with a link to the account home, the time, the text (`nh3.clean`), the content warning, the reply target, the canonical link, and `<link rel="alternate" type="application/activity+json">` with the object ID. A deleted note gives 410. The item is an entity: 404 (an entity is not at a Silvercord path).
5. The document is hidden or not extracted (`pending`), and the caller is not a member of the knowledge base: 404.
6. The caller is not a member, cannot read the knowledge base (`_kb_decide`, capability `read`), and the document does not have link access 1: the answer of the workspace path for this caller (`_workspace_view(..., readable=False)`: sign-in redirect, private profile page, or 404). This answer has no mark and no ID of the document.
7. A document with a published object, in a public knowledge base (`is_member or readable_kb`), for a request that accepts only ActivityPub: 303 to `/ap/objects/{public ID}`.
8. Every readable caller (anonymous visitor, signed-in non-member, and KB member per `SQ1:B` and `SQ2:B+A1`) gets the unified Silvercord document view (`shared_html`, `src/bible/templates/share.html`).
   - Initial `#docdata` strips `nodes` for all callers and adds `silvercord` metadata (`token`, `kb_label`, `kb_public`, `can_open_workspace`, `workspace_url` set to `/silvercord/{token}/workspace` when `is_member or readable_kb`, `graph_url` set to `/silvercord/{token}/graph`, `object_id`, `entity_count`, `link_only`, and the renditions of [`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) phase E3c: `renditions` (template, version, `updated_at`, `primary`, `url`, in the template order; empty for a document without a rendition row), `rendition` (the template of the page), `add_rendition_url` (the route of API v1 for 정리본 추가, only for the session of the owner of a personal knowledge base), `missing_renditions`, `rendition_account`, and since phase E4b `same_source_renditions`: the renditions of the published documents of other accounts of this server with the same source key, at most 30 documents, in the template order and the newest first, each with its account; the picker shows them in the group "같은 출처 · 이 서버"; and since phase E5c `remote_renditions`: the renditions of other Claire servers that the cache can show (`federation.remote_renditions.renditions_of`), each with `key`, `template`, `version`, `updated`, `account`, `domain`, `origin_url`, and `frame_url`; the picker shows them in the group "같은 출처 · 다른 서버"). A choice of that group does not navigate: the page script puts an `iframe` with `sandbox="allow-popups allow-popups-to-escape-sandbox"` and the `frame_url` in the body area (`element.startViewTransition()` on the body area where the browser has it, else the change at once), writes `?rendition={key}` to the address with `history.replaceState`, and shows a link to the origin in the info line. An address with `?rendition={key}` opens the frame at once. Since phase E9b each entry of `renditions` and `remote_renditions` has `plus` (`score`, `mine`, `own`; `null` for a rendition without an object), and `silvercord.plus_one` has `url` (the route of the mark, for a reader that is a federation actor) and `sign_in` (the sign-in address, for a visitor without a session). The page script shows the count in each option ("+1 N") and the control after the picker: a `button` with `aria-pressed` for a reader, the count as text for the author, and the count with a sign-in link for a visitor. The control follows the rendition that the body area shows. The page script (`share.html`) shows the section "정리본" when `renditions` is not empty: the label, a customizable select (`appearance: base-select`, options whose text makes sense alone), the info line (template, version, account, date), and the 정리본 추가 popover; a choice navigates to the `url` of the rendition. `silvercord.css` has `@view-transition { navigation: auto; }` inside `prefers-reduced-motion: no-preference`. A page that does not opt in (the workspace) makes Chromium abort the transition with a page error, so the `pageswap` handler of `share.html` skips each transition whose destination is not a document or rendition page of Silvercord.
   - Headers: `Referrer-Policy: strict-origin-when-cross-origin`, `Vary: Accept, Cookie`, and `Cache-Control: private, no-store` when the caller is signed in (`viewer_principal` or `is_member`) or when `not readable_kb`. When `not readable_kb` (only link access or private membership makes the document readable), the response has `X-Robots-Tag: noindex, nofollow` and the `robots` meta element `noindex, nofollow`. The item view never has the raw file link (`can_access_raw=False`).

**[code] Membership.** The route is public, thus the authentication layer validates no credential for it. `_caller_is_member` gives the request to the full application again (`_dispatch_workspace`) as `GET {workspace path}whoami` with the headers of the caller. In this internal request only, `whoami` has the key `member`: true for the account of a personal knowledge base and for an active member of a shared one. The scope mark `claire.permalink_dispatch` tells the workspace route that the query of the internal request is not an old link.

**[code] Workspace path.** `/kb/{number}/` for a shared knowledge base, `/@{username}/` for a personal one. Each active account has a USERNAME: the code makes an account without one only as the administrator row in the status `setup_required`, before the setup, and a trigger stops the removal of a USERNAME. For a knowledge base whose account is not active or has no USERNAME, the route has no workspace path and no member.

### 3.1 Route `GET /silvercord/{token}/workspace`

**[code]** `permalink_workspace_route` in `src/claire/api/server.py` (design `U8`). Route rule: `_system("public", "none")` for `GET` and `HEAD`.

1. Invalid token, missing row, or non-document kind: 404.
2. Alias token: 308 to `/silvercord/{target}/workspace`.
3. Gone document: 410.
4. If `is_member`, or `readable_kb` and the document is neither `pending` nor `hidden`: returns `_workspace_view(..., kind="document", target=found.target, readable=True)` with `<meta name="claire-open-item" content="document:{document ID}">` and `<link rel="canonical" href="/silvercord/{token}">`. The client (`app.js`, `OPEN_ITEM`) opens the document inside the Workspace.
5. Else: returns `_workspace_view(..., readable=False)` without the document mark or ID.

### 3.1a Route `GET /silvercord/{token}/renditions/{template}`

**[code]** `permalink_rendition_route` in `src/claire/api/server.py` ([`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) Section 5.8, phase E3c). Route rule: `_system("public", "none")` for `GET` and `HEAD`. The page is the document page (`_document_permalink_page`) with the body of the rendition.

1. A template outside the set (`standard`, `concise`, `academic`): 404. `standard`: 308 to `/silvercord/{token}` (the 대표 정리본 is the document).
2. The rules of the document token: alias 308 to the same sub-path of the target, gone 410, a note 404, hidden or not extracted 404 for a caller who is not a member, and the workspace answer for a caller who cannot read.
3. A template that the document does not have: 404. A request that accepts only ActivityPub: 406 until the rendition objects of phase E4.
4. `#docdata` has the body of the rendition (`detail`, `detail_html`) and `silvercord.rendition`; `cite-as` and `citation_public_url` have the address of the rendition.

Test: `tests/test_silvercord_rendering.py` (`test_silvercord_rendition_sub_paths_and_the_section_data`), E2E `e2e/renditions.spec.js`.

### 3.1b Route `GET /silvercord/{token}/renditions/remote/{key}`

**[code]** `permalink_remote_rendition_route` in `src/claire/api/server.py` ([`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) Sections 5.5 and 5.8 rule 4, phase E5c). Route rule: `_system("public", "none")` for `GET` and `HEAD`. `{key}` is the first 16 hexadecimal characters of the SHA-256 of the address of the rendition.

1. The rules of the document token, as for the graph route: alias 308, gone 410, hidden or not extracted 404 for a caller who is not a member, and 404 for a caller who cannot read the document.
2. A key that is not a copy of the source key of the document that the cache can show now: 404.
3. The answer is `usercontent.remote_item_response`: the body through `sanitize_remote_html` (no script, no resource element), `Content-Security-Policy` with `default-src 'none'`, `frame-ancestors 'self'`, and `sandbox allow-popups allow-popups-to-escape-sandbox`, `X-Frame-Options: SAMEORIGIN`, and `X-Robots-Tag: noindex, nofollow`. The page policy has `frame-src 'self'` with a usercontent host (`security.py`); without that host `default-src 'self'` permits the frame.

Test: `tests/test_silvercord_rendering.py` (`test_the_page_lists_the_renditions_of_other_servers_and_the_frame_route_gives_a_sandbox_page`), E2E `e2e/renditions.spec.js`.

### 3.1c Route `GET /silvercord/{token}/opinions`

**[code]** `permalink_opinions_route` in `src/claire/api/server.py` ([`DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md`](../design/DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md) Section 5.11 B and D, phase E7a). Route rule: `_system("public", "none")` for `GET` and `HEAD`. The same route for a rendition: `GET /silvercord/{token}/renditions/{template}/opinions`.

1. The rules of the document token, as for the graph route: alias 308 to the same sub-path of the target, gone 410, a note 404, hidden or not extracted 404 for a caller who is not a member, and 404 for a caller who cannot read the document.
2. The answer is the JSON of the opinions of the object of the page ([`FEDERATION.md`](FEDERATION.md) Section 4.7). A document or a rendition without an object gives `{"published": false}`.
3. The request starts a fetch of the opinions when the conditions of Section 5.11 B2 hold, also for an anonymous caller.
4. Headers: `X-Robots-Tag: noindex, nofollow`, `Cache-Control: private, no-store`, `Vary: Cookie`.

Test: `tests/test_silvercord_rendering.py`.

### 3.2 Route `GET /silvercord/{token}/graph`

**[code]** `permalink_graph_route` in `src/claire/api/server.py` (design `SD2`, `SQ4`, `A1`, `A2`). Route rule: `_system("public", "none")` for `GET` and `HEAD`.

1. Invalid token, missing row, or non-document kind: 404.
2. Alias token: 308 to `/silvercord/{target}/graph`.
3. Gone document: 410.
4. Hidden or `pending` document when `not is_member`: 404.
5. Unreadable document (`not is_member and not readable_kb and not found.link_access`): 404.
6. Returns JSON with `scope` (`"full"` or `"link_only"`), `document` (`token`, `title`), `ring1` (up to 24 document entities sorted by degree descending then name), `ring2` (up to 12 direct KB neighbor entities connected to `ring1`, sorted by degree descending then name), deduplicated `edges` (`from`, `to`, `rel`, `provisional`) between rendered `ring1` and `ring2` nodes, `unshown_r1`, `unshown_r2`, `shared_docs`, and `hidden_shared_docs_count`.
   - **Full scope (`is_member or readable_kb`)**: uses real entity IDs, provides `entity_path` (`/entity/{token}`), up to 5 readable `other_docs` per entity (`title`, `path`), `hidden_other_docs_count: 0`, and up to 5 `shared_docs` (`title`, `path`, `shared_count`, `source_type`) with `hidden_shared_docs_count: 0`.
   - **Link-Access-Only scope (`not is_member and not readable_kb and found.link_access`)**: includes `ring1`, `ring2`, and `edges` with opaque IDs (`e0`, `e1`, ...) instead of internal `ent_*` IDs; includes `entity_path` only when that entity itself has `link_access == 1`; blocks other documents by returning `other_docs: []` with `hidden_other_docs_count` and `shared_docs: []` with `hidden_shared_docs_count`.
   - **Headers**: `Cache-Control: private, no-store`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Robots-Tag: noindex, nofollow`, `Vary: Cookie`.

### 3.3 Route `GET /entity/{token}`

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
- **[code]** Timeline of the account home (`user_home_ap.js`): each post, a note or a document, has the same two actions with its permalink (`share_url`): "열기" (`[data-uh-open-post]`) and "링크 복사" (`[data-uh-copy-link]`). The two old buttons of a document ("open in workspace", "read shared page") do not exist now.
- **[code]** These builders give the permalink: the reader share button (it uses `path`), the Telegram bot, `claire share`, the sitemap, the canonical link and `og:url` of the item view, the timeline field `share_url`, `Article.url` and `Article.content` of the internal federation API.
- **[code]** The notification `agent_extraction_done` has the permalink of the document as its entry address.
- **[code]** These places still give old forms: `?doc=` links of the console and the public KB page (the server answers them with 308); `/p/presentation?s=` and `?id=`; `/document/raw?id=` and `/document/export?id=`.
