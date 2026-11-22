# PWA Notifications for Linkage Requests

- **Document Identifier**: `DESIGN-PWA-NOTIFICATION-20261122`
- **Status**: Units PN1 to PN6 are implemented (2026-11-22). The current state is in [`NOTIFICATIONS.md`](../implementation/NOTIFICATIONS.md).
- **Date**: 2026-11-22
- **Base commit**: `58cb6d0d`
- **Related Documents**:
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (join requests, contributions, mutual collaboration)
  - [`AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md`](AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md) (staged sources, `agent_client`)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) Section 6.6 and [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (inbound activities)
  - [`DATA_STORE_LAYOUT_DESIGN.md`](DATA_STORE_LAYOUT_DESIGN.md) Section 3 (`client.db`), [`I18N_DESIGN.md`](I18N_DESIGN.md) Section 7.2 (the `notify.` key domain)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[measured]**.

---

## 1. Problem

The owner instruction (2026-11-22): implement PWA notifications, so that a person sees a linkage request between the platform, the agents, and ActivityPub at once and can go directly to it. **[decision]**

At the base commit a request of another party waits until the person opens the correct page by chance:

1. No record tells an account that something waits for it. Each page shows only its own items. **[code]**
2. The web client has no push channel. `/stats` is polled every 25 s and the federation timeline every 30 s, only while the page is open. The console pages load one time. **[code]**
3. The service worker at `/sw.js` only passes requests through. It has no `push` handler. **[code]**
4. The only message that leaves the server for a person is a Telegram message, and only for three events (a `write` OAuth grant, permanent ingest failures, link expansion). **[code]**

## 2. Terms

| Term | Korean | Definition |
| :--- | :--- | :--- |
| Linkage request | 연계 요청 | The term of the owner. In this design: an item that another party made and that waits for a decision of the account, or the answer of another party to a request of the account. The other party is an account of this server (platform), an `agent_client` (agent), or an actor of another server (ActivityPub). **[proposal]** for the exact limits. |
| Notification | 알림 | One record for one account. It says which linkage request waits and gives the entry address. |
| Entry address | 진입 주소 | The path of the page, with the tab, where the account sees or decides the item. |
| Web Push subscription | 웹 푸시 구독 | One browser of one account that receives notifications through the push service of the browser vendor (RFC 8030). The interface calls it a device (기기). |

Term collisions that this design keeps apart:

- "Push" in [`I18N_DESIGN.md`](I18N_DESIGN.md) Section 7.2 and `src/claire/ops/notify.py` (`push_locale`) is a Telegram message. This design says "Web Push" for the browser channel.
- "Notifications" in the function list of `/settings/connections` are the notifications of an outside Mastodon account. They are not the notifications of this design.
- "Connection" is the word of `/settings/connections`, and "mutual collaboration" is one item of the shared knowledge base design. Thus the English word for 연계 is "linkage".

## 3. Goal and Non-Goals

Goals:

1. **G1**: Each linkage request of Section 5 makes a notification for each account that must see it.
2. **G2**: A browser or an installed PWA that the account subscribed shows the notification in some seconds, also when no Claire page is open.
3. **G3**: One activation of the notification (on the device or in the application bar) opens the entry address.
4. **G4**: The push service of the browser vendor cannot read a notification.

Non-goals:

- No new function to decide a request. The pages that exist stay the place of the decision.
- No notification for an OAuth consent that waits (redirect flow and device flow): the request has no account until the person signs in. **[code]** `create_oauth_pending_request`, `create_oauth_device_code`
- No notification for a reaction (`Like`, `Announce`), for a changed setting, or for an event of the server operation. These are not linkage requests.
- No change to the Telegram messages that exist.
- No e-mail channel.

## 4. Design

### PN1. Records in `client.db` (version 9)

`client.db` holds the communication data of accounts (RD27). The notifications are communication data, and all request paths and all daemons can open the file. **[proposal]**

| Table | Content |
| :--- | :--- |
| `notifications` | `id`, `account_id`, `kind`, `params_json`, `target_path`, `group_key`, `count`, `created_at`, `updated_at`, `read_at`, `pushed_at` |
| `push_subscriptions` | `id`, `account_id`, `endpoint` (unique), `p256dh`, `auth`, `label`, `session_ref`, `created_at`, `last_success_at`, `failure_count`, `last_error` |
| `push_queue` | `notification_id`, `subscription_id`, `attempts`, `next_attempt_at`; key `(notification_id, subscription_id)` |
| `notification_push_off` | `account_id`, `kind`: the kinds for which the account turned Web Push off |

Rules:

1. A notification stores the kind and its parameters, not text. The text comes from the catalog (`notify.<kind>.title`, `notify.<kind>.body`) in the locale of the account at the time of the read or of the send.
2. **Grouping.** While a notification of the same account, kind, and `group_key` is not read, a new event of that group does not make a second row. It adds 1 to `count` and replaces the parameters. Example: twenty contributions to one knowledge base are one notification with the count 20.
3. A notification is always recorded. `notification_push_off` stops only the Web Push message of a kind. A kind without a row sends Web Push.
4. Retention: a notification goes 90 days after its last change. An account has 10 subscriptions at most.
5. The account purge deletes the rows of the account in the four tables.
6. `auth.db` (version 7) gets `server_key_pairs`: the VAPID key pair of the server (name `webpush_vapid`). `SecretService` encrypts the private key. The first use makes the pair; an operator does nothing.

### PN2. Emit

`notifications.emit(db_file, kind=..., account_ids=..., params=..., target=..., group=...)` records one event for each account and puts one queue row for each subscription of the account.

1. The emit never stops its caller. An error is logged and the caller continues: a request must not fail because a notification failed.
2. The caller emits after the commit of its own change. `client.db` is a different file, and no transaction crosses files (RD27). A handler that changes the database in a transaction of its caller gives its event to a block of the caller; the block runs the events after the commit and drops them when the transaction fails.
3. The emit removes the account that did the action from the recipients. An account gets no notification for its own action.
4. An account that is not `active` gets no notification.
5. **Scan for contributions that wait for review.** Such a contribution comes from many paths: the pages, the MCP tools, the CLI, the Telegram bot, the automatic rules, and the offers of other servers. No emit is in these paths. The task of PN3 reads the committed contributions with the state `pending` and tells about each one time. The first scan after an update records the contributions that wait and tells about none of them.

### PN3. Web Push

1. **Protocol.** The message body is encrypted for the subscription with `aes128gcm` (RFC 8291). The request has a VAPID token (RFC 8292, ES256) whose `sub` is the canonical origin of the server. The module uses the `cryptography` package that the project has; there is no new dependency. **[external]** RFC 8030, RFC 8188, RFC 8291, RFC 8292. **[measured]** the module gives the content-encryption key, the nonce, and the body of the example in RFC 8291 Section 5.
2. **Payload.** JSON with `id`, `title`, `body`, `url`, `tag`, `kind`, and `unread`. 3,000 bytes at most.
3. **Sender.** A task of the `api` process, like the feed poller. An emit in the same process wakes it at once; it also runs every 5 seconds for the scan of PN2 rule 5 and for the emits of other processes.
4. **Send rules.** `TTL` 86,400 s. `Urgency` is `high` for a kind of the class `request` and `normal` for the others. `Topic` is the notification number, thus a waiting message of the same notification is replaced. A notification is sent again 60 s after its last send at the earliest (grouping rule 2).
5. **Results.** 2xx: done. 404 or 410: the subscription is deleted. 429, 5xx, and a network error: a retry after 30 s, 2 min, and 10 min, then the queue row goes. Other 4xx: the queue row goes and the failure is counted. A subscription with 5 failures in sequence is deleted.
6. **Outbound safety.** An endpoint must be an `https` URL. Before each request the sender resolves the host and refuses an address that is not public, with the check of the fetchers (PRINCIPLES B2). The private network settings of the fetchers do not apply: a push service is always a public host. The sender follows no redirect.
7. **Session binding.** A subscription stores the identifier of the session that made it. The sender deletes a subscription whose session is gone (sign-out, expiry, "sign out the other sessions", account suspension). A browser that is not signed in gets no notification.
8. **No public origin.** When the settings give no canonical origin, the sender sends nothing and `/settings/notifications` says so. There is no substitute subject.

### PN4. Client

1. **Service worker** (`/sw.js`): `push` shows the notification (`tag` = the notification, thus an update replaces the old one), sets the application badge, and tells open pages. `notificationclick` marks the notification read, then focuses an open Claire window and sends it to the entry address, or opens a window.
2. **Application bar**: a bell with the count of notifications that are not read, and a list of the last 20. An item opens its entry address and becomes read. "Mark all as read" is in the list. The bell shows only for a signed-in account. The count refreshes at page load, when the page becomes visible, on a message of the service worker, and every 60 s while the page is visible.
3. **`/settings/notifications`** (console shell): the state of this device with "Turn on" and "Turn off", "Send a test notification", the list of devices with "Remove", and one switch for each kind in three groups (platform, agent, ActivityPub).
4. iOS and iPadOS give Web Push only to a web application on the Home Screen. The page says this when the browser has no `PushManager`. **[external]** WebKit, Web Push for Web Apps on iOS and iPadOS (16.4)
5. **Entry addresses.** The console pages of the knowledge bases take the tab in the query: `/settings/kb?tab=<id>` and `/moderation/kb/{number}?tab=<id>`.

### PN5. Routes

| Method | Path | Function |
| :--- | :--- | :--- |
| `GET` | `/api/me/notifications` | The last notifications (`limit` 50 at most, `before`) with rendered text, and `unread` |
| `POST` | `/api/me/notifications/read` | `{"ids": [...]}` or `{"all": true}` |
| `GET` | `/api/me/push` | The public key, the readiness, the devices, the kinds with their switches |
| `POST` | `/api/me/push/subscriptions` | Add or renew the subscription of this browser (cookie session only) |
| `DELETE` | `/api/me/push/subscriptions` | `{"endpoint": ...}` or `{"id": ...}` |
| `PUT` | `/api/me/push/kinds/{kind}` | `{"push": true \| false}` |
| `POST` | `/api/me/push/test` | Make a notification of the kind `test` for the account |

All routes are for the account of the credential (`/api/me/*` rules). A state change needs the same origin.

## 5. Kinds

Class: `request` waits for a decision of the recipient; `answer` is the answer to a request of the recipient; `notice` is a linkage that began without a decision. Reviewers of K are the accounts with the capability `review` in the knowledge base K; people managers of K have `people`.

| Kind | Class | Event | Recipients | Entry address |
| :--- | :--- | :--- | :--- | :--- |
| **Platform** | | | | |
| `kb_join_request` | request | An account asks to join K (join policy `approval`) | People managers of K | `/moderation/kb/{n}?tab=people` |
| `kb_join_approved` | answer | A join request is approved | The requester | `/settings/kb?tab=mem` |
| `kb_contribution_pending` | request | A contribution of an account of this server waits for review in K | Reviewers of K | `/moderation/kb/{n}?tab=queue` |
| `kb_contribution_decided` | answer | A contribution is approved, rejected, or removed | The contributor | `/settings/kb?tab=subs` |
| `kb_role_given` | notice | The role `collaborator` or `moderator` is given | The account that got the role | `/settings/kb?tab=mem` |
| `mutual_requested` | request | An account asks for a mutual collaboration | The other account | `/settings/kb?tab=mem` |
| `mutual_accepted` | answer | The mutual collaboration is accepted | The requester | `/settings/kb?tab=mem` |
| **Agent** | | | | |
| `agent_source_staged` | request | A source is staged and waits for an `agent_client` to extract it | The owner of the knowledge base | `/settings/mcp` |
| `agent_extraction_done` | answer | An `agent_client` committed the extraction of a staged source | The owner of the knowledge base | The document |
| `agent_write_grant` | notice | An agent got a grant with the scope `write` | The account | `/settings/mcp` |
| **ActivityPub** | | | | |
| `ap_follower` | notice | An actor of another server follows the account | The account | `/settings/federation` |
| `ap_follow_answered` | answer | The other server accepted or rejected a follow of the account | The account | `/syndication?stage=collect` |
| `ap_mention` | request | A post of another server mentions the account or answers its object | The account | `/syndication?stage=collect` |
| `ap_quote_request` | request | A quote request waits for the manual approval of the account | The owner of the object | `/syndication?stage=collect` |
| `ap_quote_answered` | answer | The author approved or refused a quote of the account | The account | `/syndication?stage=collect` |
| `ap_private_message` | notice | A message that is not public came; Claire does not store it | The account | `/syndication?stage=collect` |
| `kf_contribution_pending` | request | An `Offer` of an account of another server waits for review in K | Reviewers of K | `/moderation/kb/{n}?tab=queue` |
| `kf_offer_answered` | answer | A knowledge base of another server accepted or rejected an offer of the account | The account | `/settings/kb?tab=subs` |
| `kf_grant` | notice | A knowledge base of another server gave the role `collaborator` to the account, or revoked it | The account | `/settings/kb?tab=mem` |
| **Other** | | | | |
| `test` | notice | The account sent a test notification | The account | `/settings/notifications` |

Rules for the content:

1. The text of a notification has the label of the knowledge base, the display name of the account, the address of the actor, or the name of the agent. It has no content of a document or of a post.
2. A value of another server (an actor address, a server name) is data: the list shows it as text, and the length is limited to 120 characters.
3. `ap_mention` and `ap_private_message` have the group "the account"; the kinds of a knowledge base have the group "the knowledge base". Thus a burst gives one notification with a count.

Events that this design does not cover, with the reason:

| Event | Reason |
| :--- | :--- |
| A copy whose origin was removed and that waits in the review queue | The item is in the same queue; a later unit can add the kind. |
| A new known server; a failed journal row; a delivery that failed for good | Operation of the server, not a linkage request of an account. |
| A refresh token that was used two times (the grant family is revoked) | A security notice; it needs its own decision. |
| A member that is banned or removed; a join request that is declined | The decline stores no state, and the page shows nothing to open. |

## 6. Security

| Risk | Control |
| :--- | :--- |
| The push service reads a notification | End-to-end encryption to the browser (PN3 rule 1) |
| A subscription endpoint points to an internal host (SSRF) | PN3 rule 6 |
| A browser of a session that ended shows notifications | PN3 rule 7; the sign-out also clears the site data of the browser |
| A value of another server is shown as markup | The list writes text nodes; a system notification shows text only |
| A burst of events makes a burst of messages | Grouping (PN1 rule 2), the 60 s rule and `Topic` (PN3 rule 4) |
| The VAPID private key leaks | Encrypted in `auth.db` with additional data `server_key|webpush_vapid`; never in a log or an API answer |
| A notification link leaves the server | `target_path` is a path of this server that the code makes; the service worker opens only same-origin paths |

## 7. Work Units

| Unit | Content |
| :--- | :--- |
| PN1 | `client.db` version 9, `auth.db` version 7, the store module, the account purge |
| PN2 | The kinds, the catalog text (`en`, `ko`), the emit, the rendering |
| PN3 | The Web Push module, the sender, the task of the `api` process |
| PN4 | The service worker, the bell of the application bar, `/settings/notifications`, the tab query of the knowledge base console pages |
| PN5 | The routes and the route policy |
| PN6 | The emit calls at the events of Section 5; tests; documents |

## 8. Verification

- Unit tests: the example of RFC 8291 Section 5; the VAPID token (signature, claims); the store (grouping, queue, purge, migration); the sender results (2xx, 410, 429, session gone, address that is not public); the routes.
- One test for each kind: the event makes a notification for the correct accounts with the correct entry address.
- E2E (Playwright): the bell shows a notification and opens the entry address; `/settings/notifications` renders in the console shell; the service worker shows a notification for a push message that the browser tool delivers, and the activation opens the entry address.
- A send to a real push service needs a public HTTPS origin. The owner does this check on the production server (PRINCIPLES E4).

## 9. Decisions

The owner gave the goal (Section 1). Each other choice in this document is a choice of the agent under the delegated authority; the owner can change it:

| No. | Choice | Alternative |
| :--- | :--- | :--- |
| PD1 | The limits of "linkage request" (Section 2) and the kinds of Section 5 | More kinds (reactions, operation events) or fewer |
| PD2 | Each kind sends Web Push until the account turns it off | Only the class `request` sends at the start |
| PD3 | Own Web Push code with `cryptography` | The package `pywebpush` (new dependencies: `http-ece`, `py-vapid`, `requests`, `aiohttp`) |
| PD4 | The subject of the VAPID token is the canonical origin | A contact address of the server administrator (a new setting) |
| PD5 | Telegram stays as it is | The same notifications also to the Telegram bot of the account |

## 10. Rejected Alternatives

- **Server-sent events or WebSocket only.** They work only while a page is open. The goal is a request that the person sees without an open page.
- **Text stored in the notification.** A change of the locale of the account would not change old notifications, and the catalog would not be the single source of text.
- **A detector that reads the state tables for all kinds.** An answer or a notice has no state to read. Only the review queue, which has many writers, uses a scan (PN2 rule 5).
- **Subscription keys encrypted at rest.** A subscription is of use only with the VAPID private key, which is encrypted.
