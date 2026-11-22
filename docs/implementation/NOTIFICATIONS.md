# Notifications and Web Push: Implementation Reference

Basis: 2026-11-22 (units PN1 to PN6). Readers: agents that change the notifications, and operators.

Design principles: [`PWA_NOTIFICATION_DESIGN.md`](../design/PWA_NOTIFICATION_DESIGN.md). Terms: [`GLOSSARY.md`](GLOSSARY.md) (알림, 연계 요청, 웹 푸시 구독, 진입 주소).

---

## 1. Scope and State

A notification tells one account that a linkage request waits, and gives the entry address of the page where the account sees or decides it. A browser that the account subscribed gets the notification as a Web Push message, also when no Claire page is open.

| Function | State |
| :--- | :--- |
| Notification records, grouping, retention | Implemented (Section 3) |
| Kinds and their events | Implemented: 19 kinds and the test kind (Section 4) |
| Web Push: encryption, VAPID, sender, session binding | Implemented (Section 5). No test sends to a real push service; the owner makes that check on a server with a public HTTPS address. |
| Bell of the application bar, `/settings/notifications`, service worker | Implemented (Section 6) |
| Events that the design does not cover | Not implemented (design Section 5, last table) |

Not in this function: the Telegram messages of `src/claire/ops/notify.py` and `src/claire/api/oauth.py` (they stay as they are), and the notifications of an outside Mastodon account in the syndication client.

## 2. Files

| File | Content |
| :--- | :--- |
| `src/claire/notifications/__init__.py` | `KINDS`, `emit`, `render`, `kb_people_managers`, `kb_reviewers` |
| `src/claire/notifications/events.py` | One function for each kind, `after_commit`, `scan_pending_contributions` |
| `src/claire/notifications/webpush.py` | RFC 8291 encryption and RFC 8292 VAPID header; no network call |
| `src/claire/notifications/sender.py` | `send_due`, `run_scheduler` (task of the `api` process), `wake`, `TaskRecorder` and `read_task_state` (the state file of the task) |
| `src/claire/ops/support_bundle_notifications.py` | The notification members of the two Support Bundles (Section 8) |
| `src/claire/store/notifications.py` | The tables of `client.db` and the VAPID key pair of `auth.db` |
| `src/claire/api/accounts_routes/notifications.py` | The routes and the body of `/settings/notifications` |
| `src/bible/static/js/sw.js` | The service worker (sent at `/sw.js`) |
| `src/bible/static/js/appbar.js`, `partials/common_appbar.html`, `css/appbar.css` | The bell and its list |
| `src/bible/static/js/console_notifications.js` | The controller of `/settings/notifications` |
| `src/claire/i18n/locales/{en,ko}.json` | `notify.<kind>.*` (server only), `nav.notify.*`, `console.notify.*`, `console.js.notify.*` |

## 3. Data

`client.db` version 9 (`store/client_db.py`, `_migrate_v9`):

| Table | Columns | Rules |
| :--- | :--- | :--- |
| `notifications` | `id`, `account_id`, `kind`, `params_json`, `target_path`, `group_key`, `count`, `created_at`, `updated_at`, `read_at`, `pushed_at` | A partial unique index on `(account_id, kind, group_key)` for rows that are not read: an event of the same group changes the row (`count`, parameters, `updated_at`). After the row is read, the next event makes a new row. |
| `push_subscriptions` | `id`, `account_id`, `endpoint` (unique), `p256dh`, `auth`, `label`, `session_ref`, `created_at`, `last_success_at`, `failure_count`, `last_error` | 10 for each account. An endpoint that another account had moves to the account that subscribes now. |
| `push_queue` | `notification_id`, `subscription_id`, `attempts`, `next_attempt_at` | One message that waits. `mark_read` removes the rows of the notification. |
| `notification_push_off` | `account_id`, `kind` | A row stops Web Push for the kind. The notification is recorded in each case. |
| `notification_scan_seen` | `scope`, `item_id` | The contributions that the scan told about (scope `contribution`). `client_meta` key `notification_scan:contribution` says that the first scan ran. |

- The text is not stored. `render(notification, locale)` reads `notify.<kind>.title` and `notify.<kind>.body` (with `.<variant>` for a kind with variants, and `_many` when the count is more than 1). A kind that the code does not know gives `notify.unknown.*`.
- A parameter is text or a whole number. Text is cut to 120 characters and has no control characters.
- Retention: 90 days after the last change (`sender.cleanup`, one time each hour).
- `client_db.purge_account` deletes the rows of an account in the four account tables.
- `auth.db` version 7: `server_key_pairs` (`name`, `public_key`, `private_ciphertext`, `private_nonce`, `key_version`, `created_at`). The row `webpush_vapid` is the VAPID pair. `ensure_vapid_private_key` makes it at the first use; the additional data of the ciphertext is `server_key|webpush_vapid`. If the row is lost, a new pair is made and each browser must subscribe again (`/settings/notifications` does this when the person turns notifications on).

## 4. Kinds and Events

`emit(db_file, kind=, account_ids=, target=, params=, group=, actor_account_id=, events=)`:

1. It never raises. An error is logged with the kind, and the result is an empty list.
2. Recipients: the given accounts, without `actor_account_id`, and only accounts with the status `active`.
3. `target` must be a path of this server (it starts with one `/`).
4. For each recipient it records the notification, and it puts one queue row for each subscription unless the kind is off for the account. A notification that was sent less than 60 s ago waits until 60 s are over.
5. When `client.db` does not exist (a test database without it), it does nothing.

| Kind | Class | Where the event is called | Recipients | Entry address | Group |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `kb_join_request` | request | `kb_join_route` (join policy `approval`) | `kb_people_managers` | `/moderation/kb/{n}?tab=people` | knowledge base |
| `kb_join_approved` | answer | `kb_member_change_route`, action `approve` | The requester | `/settings/kb?tab=mem` | — |
| `kb_contribution_pending` | request | `scan_pending_contributions` (contributor account present) | `kb_reviewers` | `/moderation/kb/{n}?tab=queue` | knowledge base |
| `kb_contribution_decided` | answer | `kb_contribution_review_route` (approve, reject), `kb_contribution_remove_route` | The contributor account | `/settings/kb?tab=subs` | knowledge base and decision |
| `kb_role_given` | notice | `kb_member_change_route` (role `collaborator`), `admin_kb_moderator_route` (give), `_after_kb_founded` (mutual collaborators) | The account with the new role | `/settings/kb?tab=mem` | — |
| `mutual_requested` | request | `mutual_collaborations_route` | The other account | `/settings/kb?tab=mem` | — |
| `mutual_accepted` | answer | `mutual_collaboration_route` (`PUT`) | The requester | `/settings/kb?tab=mem` | — |
| `agent_source_staged` | request | `ingest.pipeline.ingest` when the result is a staged source | The owner of the personal knowledge base | `/settings/mcp` | account |
| `agent_extraction_done` | answer | `submit_client_extraction`, after the commit and the hooks | The owner of the personal knowledge base | The home of the account with `?doc=<id>` | account |
| `agent_write_grant` | notice | `oauth._maybe_notify_write_grant` (redirect flow and device flow) | The account | `/settings/mcp` | — |
| `ap_follower` | notice | Inbox event `Follow`, when the follower row is new | The followed account | `/settings/federation` | account |
| `ap_follow_answered` | answer | `_follow_response`, when the state changed | The account of the follow | `/syndication?stage=collect` | — |
| `ap_mention` | request | `_decide_timeline`, for each new item with a mention | The accounts of the items | `/syndication?stage=collect` | account |
| `ap_quote_request` | request | Quote event `QuoteRequest`, new and `pending` | The owner of the object | `/syndication?stage=collect` | account |
| `ap_quote_answered` | answer | Quote event `Accept` or `Reject` that changed the quote | The account of the note | `/syndication?stage=collect` | — |
| `ap_private_message` | notice | `_private_notices`, for each account that sent an automatic notice | Those accounts | `/syndication?stage=collect` | account |
| `kf_contribution_pending` | request | `scan_pending_contributions` (no contributor account) | `kb_reviewers` | `/moderation/kb/{n}?tab=queue` | knowledge base |
| `kf_offer_answered` | answer | Handler `accept_offer`, `reject_offer`, when the activity made the decision | The account of the offer | `/settings/kb?tab=subs` | remote knowledge base and decision |
| `kf_grant` | notice | Handler `grant` (stored), `revoke` (first time) | The account of the grant | `/settings/kb?tab=mem` | — |
| `test` | notice | `POST /api/me/push/test` | The account | `/settings/notifications` | — |

Rules of the events:

- **After the commit.** A route that owns the commit calls the event after it. The inbox, timeline, quote, and knowledge activity routes put the decision and the commit in `events.after_commit()`: the event calls of the thread run when the block ends, and an exception in the block drops them. A handler of the knowledge journal runs in a savepoint; it calls the event as its last step (`handlers._notify`).
- **Scan.** `scan_pending_contributions` reads `kb_contributions` with the state `pending` and tells about the rows that `notification_scan_seen` does not have. It makes one emit for each knowledge base and source, with the count of the new rows and the name of the last contributor. The first scan records the rows that exist and tells about none. The task of the `api` process runs the scan every 5 seconds.
- **Names.** An account of this server shows its display name, else `@username`. An actor or a knowledge base of another server shows `name@host` from its IRI, or the host when the last path part is a number (`events.actor_handle`). The text is a label; no code uses it as an address.
- `agent_source_staged` comes only from `pipeline.ingest` (a person, a feed, or the Telegram bot started the ingest). `stage_raw`, with which an agent stages its own source, makes no notification.

## 5. Web Push

- **Request** (`webpush.build_request`): `POST` to the endpoint with `Content-Encoding: aes128gcm`, `TTL: 86400`, `Urgency` (`high` for the class `request`, else `normal`), `Topic: claire-<id>`, and `Authorization: vapid t=<JWT>, k=<public key>`. The JWT is ES256 with `aud` (origin of the endpoint), `exp` (12 hours), and `sub` (the canonical origin of the server, `contributions.canonical_origin`).
- **Payload**: JSON `{"id", "kind", "title", "body", "url", "tag", "unread"}` in the locale of the account (`ops.notify.push_locale`). When the payload is more than 3,000 bytes, the body text is left out.
- **Sender** (`sender.send_due`): reads 100 due queue rows at most. Before a send it deletes a subscription whose account is not `active` or whose `session_ref` is not a session of the account that is in effect (`auth_sessions`, approved and not expired). It does not send a notification that is read.

| Result | Action |
| :--- | :--- |
| 2xx | Queue row deleted; `last_success_at` and `pushed_at` set; failure count 0 |
| 404, 410 | Subscription deleted |
| 429, 5xx, network error, host that does not resolve | Retry after 30 s, 2 min, 10 min; then the queue row is deleted and one failure is counted |
| Other 4xx | Queue row deleted and one failure counted |
| Address that is not public, or subscription values that cannot make a message | Subscription deleted |
| 5 failures in sequence | Subscription deleted |
| No canonical origin | Due queue rows deleted; nothing is sent (`no_public_origin`) |

- **Outbound safety**: `guarded_client(allow_private_networks=False, private_network_allowlist=[], follow_redirects=False)`. The private network settings of the fetchers (`CLAIRE_ALLOW_PRIVATE_NETWORKS`, `CLAIRE_PRIVATE_NETWORK_ALLOWLIST`) do not apply. The subscribe route accepts only an `https` URL with a host name and without credentials; an IP address is refused.
- **Task**: `run_scheduler` starts in the lifespan of the `api` process (`server.app_lifespan`). An emit in the same process calls `sender.wake`. The interval is 5 seconds. The log line `web push: sent=… retried=… dropped=… removed_subscriptions=… errors=[…]` has no endpoint and no key.
- **State file**: the task writes `data/run/task_web_push.json` after a cycle with work, after a failed cycle, and one time each 60 seconds at least: start, last cycle, last cycle with work, totals since the start, and the last reasons of failed sends.
- There is no environment variable and no admin setting for this function.

## 6. Client

- **Service worker** (`/sw.js`, file `static/js/sw.js`, scope `/`): network passthrough for requests as before. `push`: shows the notification (`tag` and `renotify`), sets the application badge to `unread`, and sends `{type: "claire-notification"}` to open pages. `notificationclick`: `POST /api/me/notifications/read`, then focus on a page that shows the entry address already, else `clients.openWindow`. An address that is not a path of this server becomes `/settings/notifications`. `pushsubscriptionchange`: subscribes again and tells the server.
- **Bell** (`#notifybtn`, `#pop-notify`): `appbar.js` starts it on a page that shows the sign-out control (`#menu-logout-form`), and when a page calls `ClaireAppBar.setLogoutVisible(true)`. The list request gives 404 for a caller that is not an owner; the bell then stays hidden. Refresh: page load, the page becomes visible, a message of the service worker, every 60 s while visible, and `ClaireAppBar.refreshNotifications()`. Each value goes into the page as a text node.
- **`/settings/notifications`** (console section `notifications`, group `workspace`): device state (`unsupported`, `denied`, `off`, `on`, `other_account`), "Turn on" (permission, `PushManager.subscribe` with the server key, `POST /api/me/push/subscriptions`), "Turn off", "Send a test notification", the device list, and the kind switches. The browser finds its own row by the digest `ref` (the first 16 hexadecimal characters of the SHA-256 of the endpoint); no API answer has an endpoint.
- **Tab query**: `/settings/kb?tab=personal|subs|rule|rep|mem` and `/moderation/kb/{n}?tab=queue|content|curate|people|settings|log` (`console_kb.js`, `tabFromQuery`). A value that is not a tab of the page gives the first tab.
- The sign-out answer has `Clear-Site-Data: "storage"`: the browser removes the service worker and its subscription. The sender deletes the row of the session at its next send.

## 7. Routes

All routes have the rule `_any("owner", "none")`; `/settings/notifications` has `_system("owner", "none")`. A caller that is not an owner gets 404.

| Method | Path | Request | Answer |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/me/notifications` | `limit` (1 to 50, default 20), `before` (notification number) | 200 `{"items": [{"id", "kind", "channel", "class", "title", "body", "url", "count", "created_at", "updated_at", "read"}], "unread"}`; 400 `invalid_request` |
| `POST` | `/api/me/notifications/read` | `{"ids": [1 to 50 numbers]}` or `{"all": true}` | 200 `{"ok", "unread"}`; 400 `invalid_request` |
| `GET` | `/api/me/push` | — | 200 `{"ready", "reason", "public_key", "max_subscriptions", "subscriptions": [{"id", "ref", "label", "created_at", "last_success_at", "failure_count", "this_session"}], "kinds": [{"kind", "channel", "class", "push"}]}` |
| `POST` | `/api/me/push/subscriptions` | `{"endpoint", "keys": {"p256dh", "auth"}}` (the form of `PushSubscription.toJSON()`) | 201 `{"subscription": {"id", "ref", "label", "created_at"}}`; 400 `invalid_subscription`; 403 `session_required` (the credential is not a cookie session); 409 `subscription_limit` |
| `DELETE` | `/api/me/push/subscriptions` | `{"endpoint"}` or `{"id"}` | 200 `{"ok", "deleted"}` |
| `PUT` | `/api/me/push/kinds/{kind}` | `{"push": true \| false}` | 200 `{"kind", "push"}`; 404 `kind_not_found` |
| `POST` | `/api/me/push/test` | `{}` | 201 `{"ok", "id"}` |

## 8. Diagnosis with a Support Bundle

When a person gets no notification, make a Support Bundle ([`TELEMETRY.md`](TELEMETRY.md) §3.7 and §4) and read `notifications/summary.json`. Its `findings` name the cause; the manifest of the server-wide bundle has the same codes in `notification_findings`.

| Question | Where |
| :--- | :--- |
| Can the server send? | `readiness` (`NO_PUBLIC_ORIGIN`, `ORIGIN_NOT_HTTPS`, `VAPID_PAIR_ABSENT`) |
| Does the task run? | `sender_task`, and `diagnostics/services.json` `api_tasks.web_push` (`SENDER_TASK_NOT_RUNNING`, `SENDER_TASK_CYCLE_FAILED`) |
| Do messages wait? | `push_queue` (`QUEUE_OVERDUE`) |
| Do the devices fail, and with which result of which push service? | `push_subscriptions` (`DEVICES_FAILING`, `DEVICES_WITHOUT_SESSION`) |
| Was the notification made, and was it sent? | `notifications_by_kind` (`unread_never_sent`), `push_off`, and `scan` (`SCAN_NEVER_RAN`, `SCAN_BEHIND`) |
| Which accounts wait? | `accounts_with_most_unread` |

The account makes its own bundle at `/settings/bundles`. Its member has the devices of the account with the state of each sign-in session, the kinds without push, and the last 50 notifications with the time of each send.

## 9. Known Limits

- A real send (a browser of a person, the push service of its vendor) is not in the tests. It needs a server with a public HTTPS address.
- The headless browser of the E2E tests has no system notification and no application badge. `e2e/notifications.spec.js` gives the worker the events and records the calls.
- A pending contribution gets its notification at the next scan, 5 seconds later at most.
- An item whose mention flag changes from 0 to 1 after its first store makes no `ap_mention` notification (`upsert_timeline_item` reports only a new row).
- A journal row that an administrator replays (`/api/admin/federation/journal/{id}/replay`) calls the event of its handler without an `after_commit` block: the notification is made before the commit of the replay.
- The name of an actor of another server comes from its IRI, not from its profile.
- A notification that is read stays in the list for 90 days; the list has no delete control.

## 10. Tests

| File | Content |
| :--- | :--- |
| `tests/test_notifications.py` | The example of RFC 8291 Section 5 (body start, key, and nonce); the VAPID signature and claims; the store (grouping, queue, purge, migration from version 8, VAPID key); `emit` rules; the text of each kind in `en` and `ko`; the events of each kind; `after_commit`; the scan; the sender results (2xx, retry, 410, network error, session end, address that is not public, no public origin, kind that is off); the task of the `api` process; the routes; the page; the notification member of the account-scope Support Bundle |
| `tests/test_e2e_federation_interactions.py`, `tests/test_e2e_federation_quotes.py`, `tests/test_knowledge_federation_kf4_e2e.py` | `ap_mention`, `ap_private_message`, `ap_quote_request`, `kf_offer_answered`, and `kf_grant` from the internal federation API |
| `tests/test_support_bundle_services.py` | The notification member of the server-wide Support Bundle and the task state file |
| `e2e/notifications.spec.js` | The bell and `/settings/notifications` in the console shell; a kind switch; an item opens its entry address and becomes read; the tab query; the service worker (`push`, an address of another site, `notificationclick`) |
