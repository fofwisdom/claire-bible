# Knowledge Federation Implementation Reference

Basis: units KF0 to KF3 (2026-11-18). Readers: agents who do the units KF4 and KF5.

Design: [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md). This document tells what the code has now. The general federation (actors of accounts, publishing, remote reading) is in [`FEDERATION.md`](FEDERATION.md). The local shared knowledge bases are in [`THEMES.md`](THEMES.md) Sections 4.7 to 4.12.

## 1. Scope and State

Units KF0 to KF3 are implemented. KF0: the vocabulary, the JSON-LD context, the builders, the validators, the contract fixtures, and the routes `/ns/v1` and `/ns`. KF1: the known servers, the admin controls, the inbox journal, and the single mode root (Section 10). KF2: the `/kb` collection for other servers, the store of remote knowledge bases, the themes across servers, and the root page (Section 11). KF3: the knowledge base actors, the curation across servers, the revisions, and the removal at the origin (Section 12). The sidecar does its part of KF1 to KF3 (Sections 10.6 and 12.7). The activity kinds of unit KF4 (`offer_contribution`, `accept_offer`, `reject_offer`, `grant`, `revoke`) have no handler, and no server sends them.

| Function | State |
| :--- | :--- |
| Vocabulary, context document, vocabulary page (files) | Implemented |
| Builders and validators of each object and activity | Implemented |
| Contract fixtures | Implemented |
| Routes `/ns/v1` and `/ns` | Implemented (`server.py`: `ns_context_route`, `ns_vocabulary_route`) |
| Vocabulary in the sidecar | The context document, the handoff of inbound activities (Section 10.6), and the outbound activities as the JSON of the builders (Section 12.7) are implemented |
| Known servers, admin controls, inbox journal, single mode root (KF1) | Implemented (Section 10; the sidecar part in Section 10.6) |
| `/kb` collections of other servers, store of remote knowledge bases, themes across servers, root page (KF2) | Implemented (Section 11) |
| Knowledge base actors, curation, revisions, removal at the origin (KF3) | Implemented (Section 12) |
| Contributions and grants across servers (KF4) | Not implemented |
| Automatic contribution across servers (KF5) | Not implemented |
| Registration on `w3id.org` | Owner task, after `claire.bible` serves the two paths |

## 2. Files

| File | Content |
| :--- | :--- |
| `src/claire/knowledge_federation/vocabulary.py` | Identifiers, the table `TERMS`, `context_document`, `vocabulary_page`, builders, validators. No database access and no network access. |
| `src/claire/static/ns/v1.jsonld` | The context document of version 1. It is the text of `context_json()`. |
| `src/claire/static/ns/index.html` | The page of the vocabulary terms. It is the text of `vocabulary_page()`. Each term has an anchor. |
| `tests/fixtures/knowledge_federation/` | The contract fixtures: `objects/`, `activities/`, `invalid/`. |
| `tests/test_knowledge_federation_vocabulary.py` | The tests, and `write_fixtures()` that writes the fixture files from the builders. |

A test fails when one of the two static files is different from the output of the module. The context document of `v1` must not change after the first server serves it; a new term goes to a new version (design Section 9.1 rule 2).

## 3. Identifiers

| Item | Value |
| :--- | :--- |
| Context IRI (`CLAIRE_CONTEXT`) | `https://w3id.org/claire-bible/ns/v1` |
| Namespace (`CLAIRE_NS`), prefix `claire` | `https://w3id.org/claire-bible/ns#` |
| ActivityStreams context (`AS2_CONTEXT`) | `https://www.w3.org/ns/activitystreams` |
| ForgeFed namespace (`FORGEFED_NS`) | `https://forgefed.org/ns#` |
| Local paths | `/ns/v1` (context), `/ns` (page) |

Each wire document has `"@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/claire-bible/ns/v1"]`. Claire reads the compact names and uses no JSON-LD processor. Claire never gets the context from the network.

### 3.1 Terms

| Term | Kind | IRI | Value |
| :--- | :--- | :--- | :--- |
| `claire:KnowledgeBase` | Type (base `Group`) | `…/ns#KnowledgeBase` | — |
| `claire:Extraction` | Type (base `Object`) | `…/ns#Extraction` | — |
| `claire:Contribution` | Type (base `Object`) | `…/ns#Contribution` | — |
| `Grant` | Type (activity) | `https://forgefed.org/ns#Grant` | — |
| `Revoke` | Type (activity) | `https://forgefed.org/ns#Revoke` | — |
| `claire:knowledgeType` | Property | `…/ns#knowledgeType` | `real` or `fictional` |
| `claire:documentCount`, `claire:entityCount`, `claire:relationCount` | Property | `…/ns#documentCount`, … | Integer, 0 or more |
| `claire:signature` | Property | `…/ns#signature` | JSON value (Section 4.1) |
| `claire:documents` | Property | `…/ns#documents` | IRI of the documents collection |
| `claire:contentHash` | Property | `…/ns#contentHash` | 64 lowercase hexadecimal characters |
| `claire:root` | Property | `…/ns#root` | IRI of the root document |
| `claire:extraction` | Property | `…/ns#extraction` | A `claire:Extraction` |
| `claire:keyClaims` | Property | `…/ns#keyClaims` | List of texts |
| `claire:entities`, `claire:relations` | Property | `…/ns#entities`, `…/ns#relations` | JSON value (Section 4.3) |
| `claire:document` | Property | `…/ns#document` | An `Article` |
| `claire:reasonCode` | Property | `…/ns#reasonCode` | `off_subject`, `wrong_knowledge_type`, `duplicate`, `low_quality`, `spam`, `other` |
| `capability` | Property | `https://forgefed.org/ns#capability` | IRI of a grant |
| `claire:collaborator` | Value (role) | `…/ns#collaborator` | — |

The standard terms come from ActivityStreams: `Group`, `Article`, `Object`, `Tombstone`, `Follow`, `Accept`, `Reject`, `Undo`, `Update`, `Delete`, `Offer`, and the properties in `AS2_TERMS`.

## 4. Objects

A type of the vocabulary is an array with the base type first, for example `["Group", "claire:KnowledgeBase"]`. Thus software that does not know the Claire terms can show the object.

### 4.1 `claire:KnowledgeBase` (kind `knowledge_base`)

Builder: `build_knowledge_base(kb, iris)`.

| Field | Rule |
| :--- | :--- |
| `id`, `inbox`, `outbox`, `followers`, `claire:documents` | Necessary IRIs. All are on the server of `id`. |
| `url` | Necessary IRI: the page of the knowledge base. |
| `name` | Necessary: the label. |
| `summary` | Necessary: the description. It can be empty. |
| `claire:knowledgeType` | Necessary. |
| `claire:documentCount`, `claire:entityCount`, `claire:relationCount` | Necessary. |
| `claire:signature` | Optional: absent for a knowledge base that has no signature. Keys: `entities` (at most 256 rows `[type, norm_name, weight]`), `visible_documents`, `computed_at`. |

### 4.2 Document: `Article` (kind `document`)

Builder: `build_document(doc, iris, extraction)`.

| Field | Rule |
| :--- | :--- |
| `id` | Necessary IRI. |
| `name`, `content` | Necessary: the title, and the HTML of the document. |
| `url` | Optional: the canonical URL. Absent for a document that has no URL. |
| `attributedTo` | Optional: the actor with the credit. Absent when the account was deleted. |
| `origin` | Optional: the object that this document was copied from. Absent for a document that is not a copy. It must be different from `id`. |
| `claire:root` | Necessary: the root document. A root document has its own `id`. |
| `claire:contentHash` | Necessary. |
| `published` | Necessary time. `updated` is optional. |
| `claire:extraction` | Optional in a document that goes alone. Necessary in an `Update` and in a contribution. |

### 4.3 `claire:Extraction` (kind `extraction`)

Builder: `build_extraction(result, authority_ids)`. The input is the stored extraction of the local merge job (`extractions.raw_response`). The builder adds `norm_name`.

| Field | Rule |
| :--- | :--- |
| `summary` | Necessary plain text. It can be empty. |
| `claire:keyClaims` | Necessary list of texts. |
| `claire:entities` | Necessary JSON array. Each row: `name`, `type`, `norm_name`, `aliases`, `observations` (all necessary); `sameAs` (optional, a list of authority identifiers such as `wd:Q7259`, not empty); `proposed_type` (optional). |
| `claire:relations` | Necessary JSON array. Each row: `source`, `target`, `type` (necessary); `proposed_type` (optional). `source` and `target` are names of entities of the same extraction. |

The rows of a JSON value are sealed: a key outside this list gives `unknown_field`. The wire form converts to the model `ExtractionResult` of the merge job without a change of key names.

### 4.4 `claire:Contribution` (kind `contribution`)

Builder: `build_contribution(document=, attributed_to=, knowledge_base=, contribution_id=)`.

| Field | Rule |
| :--- | :--- |
| `id` | Optional. Absent in an offer. The target gives the IRI in `result` of its `Accept`. |
| `attributedTo` | Necessary: the account actor that contributes. |
| `context` | Necessary: the IRI of the knowledge base actor. |
| `claire:document` | Necessary: the `Article` with its extraction. Its `attributedTo` is the same actor. |

## 5. Activities

Each activity has `@context`, `id`, `type`, `actor`, and `to` (an array of IRIs, not empty). The `id` is on the server of the `actor`.

| Kind | Type | Actor → recipient | Object and other fields | Builder |
| :--- | :--- | :--- | :--- | :--- |
| `follow` | `Follow` | Server actor → knowledge base actor | `object`: IRI of the knowledge base actor | `build_follow` |
| `accept_follow` | `Accept` | Knowledge base actor → server actor | `object`: the `Follow` with `id`, `type`, `actor`, `object` | `build_accept_follow` |
| `undo_follow` | `Undo` | Server actor → knowledge base actor | `object`: the `Follow`, same form | `build_undo_follow` |
| `update_knowledge_base` | `Update` | Knowledge base actor → followers | `object`: the `claire:KnowledgeBase`; its `id` is the actor | `build_update_knowledge_base` |
| `delete_knowledge_base` | `Delete` | Knowledge base actor → followers | `object`: `Tombstone` with `id` (the actor) and `formerType` `claire:KnowledgeBase` | `build_delete_knowledge_base` |
| `update_document` | `Update` | Knowledge base actor → followers | `object`: the `Article` with `claire:extraction`, on the server of the actor | `build_update_document` |
| `delete_document` | `Delete` | Knowledge base actor → followers | `object`: `Tombstone` with `id` and `formerType` `Article`; `claire:reasonCode` | `build_delete_document` |
| `offer_contribution` | `Offer` | Account actor → knowledge base actor | `object`: the `claire:Contribution`; `target`: the knowledge base actor; `capability` (optional): IRI of a grant on the server of the target | `build_offer` |
| `accept_offer` | `Accept` | Knowledge base actor → account actor | `object`: IRI of the `Offer`; `result`: IRI of the contribution on the server of the actor | `build_accept_offer` |
| `reject_offer` | `Reject` | Knowledge base actor → account actor | `object`: IRI of the `Offer`; `claire:reasonCode` | `build_reject_offer` |
| `grant` | `Grant` | Knowledge base actor → account actor | `context`: the actor; `target`: the account actor; `object`: `claire:collaborator`; `published`; `endTime` after `published` | `build_grant` |
| `revoke` | `Revoke` | Knowledge base actor → account actor | `object`: IRI of the grant, on the server of the actor | `build_revoke` |

An `Accept` whose `object` is a JSON object is `accept_follow`; an `Accept` whose `object` is an IRI is `accept_offer` and must have `result`.

## 6. Validators

- `validate_object(value, top_level=)` gives one of `OBJECT_KINDS`. `validate_activity(value)` gives one of `ACTIVITY_KINDS`.
- A value outside the contract raises `ContractError` with `code` (one of `ERROR_CODES`) and `path` (the field).
- No validator fills a default. A necessary field that is absent gives `missing_field`.
- A key of the object that is not in the contract has no effect, because other contexts can add terms (for example the keys of an actor).
- Each builder validates its result. Thus a builder with wrong input raises the same error.

| Code | Cause |
| :--- | :--- |
| `not_an_object` | The value is not a JSON object. |
| `missing_context` | A top-level value has no `@context` array with the two context IRIs. |
| `missing_field` | A necessary field is absent. |
| `invalid_field` | A field has a wrong JSON type or a wrong form. |
| `unknown_field` | A row of a JSON value has a key outside the contract. |
| `invalid_iri` | A field is not an absolute `http` or `https` IRI. |
| `invalid_time` | A time is not an ISO 8601 text with a time zone, or an end time is not after its start. |
| `unsupported_type` | The type is not in the vocabulary. |
| `unsupported_object` | The object is not one that the activity type can have. |
| `actor_mismatch` | Two fields that must name the same actor are different. |
| `origin_mismatch` | An IRI is not on the server of the actor that is the authority for it. |
| `target_mismatch` | The target of an offer is not the knowledge base of its contribution. |
| `missing_recipient` | `to` does not have the actor that must get the activity. |
| `invalid_knowledge_type`, `invalid_reason_code`, `invalid_role`, `invalid_content_hash` | The value is not in its list or its form. |
| `signature_too_large` | A signature has more than 256 entities. |
| `unknown_relation_entity` | A relation refers to a name that no entity of the extraction has. |

Checks that are not in the validators, because they need data of the server: the signature of the sender, domain blocks, the scheme rule (`http` only in development, [`FEDERATION.md`](FEDERATION.md) Section 5.7), size limits, HTML sanitization, if the knowledge base exists, and if a grant is in effect.

## 7. Work That Is Not Implemented

### 7.1 Routes

The routes `/ns/v1` and `/ns` are implemented (design Section 9.1 rule 3): `GET /ns/v1` gives the text of `context_json()` with `Content-Type: application/ld+json` and `Access-Control-Allow-Origin: *`; `GET /ns` gives `vocabulary_page()`. No authentication, in each mode, also while the switch `federation` is off. No redirect on the server: the redirect (302) from `https://w3id.org/claire-bible/…` to `https://claire.bible/…` is on `w3id.org`.

### 7.2 Sidecar

The sidecar (`sidecar/fedify/`) has the context document as a local copy and gives each inbound activity of the wire contract to Claire without a change (Section 10.6). Unit KF3 implemented the two items below (Section 12.7). The first item is a rule for each outbound activity:

- The wire JSON of Claire without a change in the outbound direction. Claire reads the compact names, so a document that Fedify compacts again with other contexts is not valid for the validators. Fedify 2.4.5 does not keep the JSON of each type as it came (a `Delete` comes back with other contexts), and its vocabulary has no class for `Grant` and `Revoke` (`Invalid type`). Thus an outbound activity must go out as the JSON of the builders, signed with the key of its actor, and not as an object of the Fedify vocabulary.
- The `Group` actor of each shared knowledge base with its keys, inbox, outbox, and followers (design Section 9.2). The inbox path `/ap/kb/{number}/inbox` takes knowledge activities (Section 10.6); the actor document and the keys are in Section 12.2.

## 8. Choices Made in the Code

The design does not give these details. The owner can change them before the first server serves `v1`.

1. The entities, the relations, and the signature are JSON values (`@type: @json`). The design gives the keys `type` and `norm_name`; the key `type` in a JSON-LD node is the type of the node, so a node cannot have it as data.
2. `sameAs` is a key of an entity row in the JSON value. It has no IRI of its own.
3. `Grant`, `Revoke`, and `capability` have the IRIs of the ForgeFed namespace. The IRIs are from memory of the ForgeFed specification and were not read from the network in this unit.
4. A `Delete` has a `Tombstone` with `formerType`, as the `Delete` of [`FEDERATION.md`](FEDERATION.md) Section 4.4.
5. The reason code is the property `claire:reasonCode` of the `Delete` and of the `Reject`.
6. The activities of a knowledge base actor go to its followers collection only. They do not go to the public collection.
7. The signature does not have the centroid of the embeddings.
8. A contribution has the knowledge base in `context`; the offer has it in `target`. The two must be the same.
9. No builder makes an IRI. The paths of the fixtures (`/ap/kb/{number}`, `/ap/documents/{number}`, `/ap/contributions/{number}`) are test values.
10. The grant has `published` and `endTime`. The length of 6 months is a rule of the server that gives the grant, not of the vocabulary.

## 9. Tests

`tests/test_knowledge_federation_vocabulary.py`:

- Each object kind and each activity kind has a fixture; each fixture is valid and is the output of its builder.
- Each file of `invalid/` gives its error code at its field.
- The exchange between server A and server B: each answer refers to the activity that came.
- The context file is JSON, has the namespace of decision KD9, and defines each Claire term that a builder uses.
- The code lists are the ones of `src/claire/contributions.py` and `src/claire/store/kb_collab.py`. The wire extraction is a valid `ExtractionResult`.

Not tested: the context document with a JSON-LD processor (the repository has none in Python). The tests of KF1 are in Section 10.7.

## 10. Unit KF1: Known Servers, Admin Controls, Inbox Journal, Single Mode Root

Design: Sections 6.2, 6.3, 6.5, 13, 14, and 15.

### 10.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/knowledge_federation/known_servers.py` | The table `kf_known_servers`: how a server becomes known, the leases of the reads, the result of a read. No function commits. |
| `src/claire/knowledge_federation/access.py` | The one-sided controls: the switch `federation`, the domain blocks, the signed read of a collection. No function changes data. |
| `src/claire/knowledge_federation/journal.py` | The table `kf_inbox_journal`: receive, process, replay, retention, and the dispatch table `HANDLERS`. `receive` and `process` commit. |
| `src/claire/api/knowledge_federation_internal.py` | The internal routes of the sidecar (Section 10.4). |
| `src/claire/api/accounts_routes/knowledge_federation.py` | The admin routes (Section 10.5). |
| `src/claire/store/db.py` | `_migrate_v29_knowledge_federation`, schema version 29. |
| `src/claire/api/namespace.py`, `src/claire/api/server.py` | The single mode root (Section 10.8). |

### 10.2 Tables (schema version 29)

The migration step runs at each start and only makes a table that is not there. A table with the name but without a column that the code reads becomes `<name>_retired_v29` (or `_retired_v29_<n>`) with its rows; its indexes are dropped, the log gets a warning, and the start continues.

`kf_known_servers` (one row for each known server; the key is the normalized domain):

| Column | Meaning |
| :--- | :--- |
| `domain` | The host name, lowercase, punycode, no port. |
| `source`, `added_by`, `created_at` | How the server became known the first time. `source` is `admin` (with `added_by`, the admin account), `local_follow`, `local_read`, `local_join`, `local_curation`, `local_contribution`, `remote_contribution`, or `remote_curation`. A later contact does not change these columns. |
| `last_contact_at` | The time of the last activity of that server. An admin entry has none. |
| `server_actor_iri`, `software_name`, `software_version`, `shared_kb_count`, `last_read_at` | The result of the last good read of NodeInfo and of the server actor. |
| `read_lease_until` | A read that the sidecar took. A lease ends after 300 seconds. |
| `error_count`, `last_error`, `last_error_at` | Failed reads in sequence. A good read sets them to zero. |

The state of a known server is not a column: it is `blocked` when a domain block (`federation_domain_blocks`) applies to the domain or to a parent domain, else `active`. A blocked server can be a known server; it has no exchange and no read.

`kf_inbox_journal` (one row for each activity of the knowledge federation; the key is the activity IRI):

| Column | Meaning |
| :--- | :--- |
| `activity_iri`, `activity_type`, `kind` | The `id` and `type` of the activity, and the activity kind of `vocabulary.ACTIVITY_KINDS` after the validation. |
| `actor`, `actor_domain`, `recipient`, `verified_by` | The `actor` (the signer when the activity has no text `actor`), its domain, the inbox owner (`null` for the shared inbox), and the signature mechanism (`http-signature`, `ld-signature`, `object-integrity-proof`). |
| `payload` | The activity JSON as it came. |
| `status`, `error_code`, `error_detail`, `attempts`, `received_at`, `processed_at` | The state (Section 10.3), the error, the number of processings, and the times. |

The journal is in `claire.db`, not in `fedify.db` (design Section 13 rule 4): the admin starts a replay in Claire, and the state of the row and the changes of a handler are in one transaction.

### 10.3 Rules

Known servers (design Section 6.2):

- An admin entry (`POST /api/admin/federation/known-servers`) makes a known server with the source `admin`.
- An activity of the kind `offer_contribution` makes its server known with the source `remote_contribution`; `follow` with `remote_curation`. Each other activity changes `last_contact_at` of a known server only.
- The sources `local_*` are for the outbound units: no code sets them in KF1. The sender of a contribution records `local_contribution` in unit KF4.
- The sidecar reads each known server that is not blocked one time each day.

Access (design Section 6.3, KS1): a collection of shared knowledge answers only a request that the sidecar verified and that the server actor (`/ap/actors/server`) of another server signed. Reasons of a refusal, in this sequence: `federation_off`, `unsigned`, `invalid_actor` (not `https`, `http` only when the origin of this server is `http`, credentials in the address, or this server), `not_a_server_actor`, `blocked_domain`. Each refusal gets the same 404. The server mode is not a condition (design Section 15 rule 5).

Inbox journal (design Section 13, KS9):

| State | Meaning |
| :--- | :--- |
| `received` | The row is stored and the processing did not end. The next delivery of the same activity, or a replay, processes it. |
| `processed` | A handler applied the activity. The row does not change again. |
| `failed` | `no_handler` (no handler for the kind; all kinds in KF1) or `handler_error` (the handler raised an error; its changes are rolled back). The admin can process the row again. |
| `refused` | `error_code` is the code of `vocabulary.ContractError` (`error_detail` is the field), the code of a `HandlerRefusal`, or the control that stopped a replay (`blocked_domain`). No replay. |

- A delivery gets no row (`rejected`) for: `federation_off`, `invalid_actor`, `blocked_domain` (controls for the signer), `invalid_activity` (no JSON object, or no `id` that is an address of another server), `signer_mismatch` (the `id` or the `actor` is not on the server of the signer).
- The row is committed before the processing. A second delivery of a row that is not `received` is `duplicate` and changes nothing; two deliveries at the same time make one row.
- A handler runs in a savepoint and must not commit. `journal.register_handler(kind, handler)` registers it; a kind has one handler.
- A replay needs the state `failed` or `received`. While the switch `federation` is off, a replay is refused and the row does not change.
- Retention: 30 days for `processed` and `refused`, 90 days for `failed` and `received`. Each new row starts the deletion of old rows.

### 10.4 Internal Routes (the sidecar is the caller)

Under `/internal/federation/v1`, with the signature of the service key (`FEDERATION.md`). The models are sealed: an unknown key gets 400 `invalid_request`. The responses have `Cache-Control: no-store`.

| Method and path | Request | Response |
| :--- | :--- | :--- |
| `POST /knowledge-activities` | `{"activity": {...}, "signer": "<IRI>", "verified_by": "http-signature" \| "ld-signature" \| "object-integrity-proof", "recipient": "<IRI>" \| null}`; each key is necessary | 200 `{"decision": "processed" \| "failed" \| "refused" \| "duplicate" \| "rejected", "reason": <code or state> \| null, "journal_id": <int> \| null}` |
| `GET /knowledge-collections/{name}?signer=<IRI>` | `name` is `kb` or `themes`. No `signer`: the request had no valid signature. `kb` only: `&page=true` for a page, and `&max_id=<number>` for the page after the knowledge base with that number | 200 `{"collection", "server": "<domain of the signer>", "document": {...}}`: `document` is the ActivityStreams document of Section 11.2, which the sidecar gives without a change; 404 `not_found` for each refusal and each other name; 400 for another query key or a wrong value |
| `GET /knowledge-server-reads?limit=<1..20>` | Default 5 | 200 `{"servers": [{"domain"}]}`: the known servers due for a read; each one gets a lease. Empty while the switch is off |
| `POST /knowledge-server-reads` | `{"domain", "ok", "server_actor", "software_name", "software_version", "shared_kb_count", "error"}`; `ok` true: the server actor is on the domain and `error` is null; `ok` false: `error` is a code `[a-z0-9_]{1,64}` and the other fields are null | 200 `{"domain", "recorded": true}`; 400 for a body against these rules; 404 for a domain that is not known |

The `kb` collection has the shared knowledge bases only (no owner, public, with a knowledge type). A personal knowledge base is never in it (KD3). Unit KF2 added `GET` and `POST /knowledge-kb-reads` (Section 11.4) and changed the answer of `knowledge-collections` (`document` in place of `items`); the contract version of the internal API was 1.9. Unit KF3 added the routes of Section 12.5; the version is 1.10. The contract fixtures of these routes are in `tests/fixtures/knowledge_federation/internal/`.

### 10.5 Admin Routes

`ROUTE_POLICY`: `_system("admin", "none")`. Each other principal gets 404. A change needs the same origin (403) and a session with a recent sign-in (401 `reauth_required`, 403 `step_up_requires_session`), and writes `admin_audit`. Details: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document.

| Route | Result |
| :--- | :--- |
| `GET /api/admin/federation/known-servers` | `{"servers": [...], "switches": {"federation", "kb_auto_contribution", "signed_get": "required"}}` |
| `POST /api/admin/federation/known-servers` | Body `{"domain"}`. 201 `{"server", "created": true}` (audit `federation.known_server_add`), 200 with `created: false` for a known server; 400 `invalid_request`, `invalid_domain`, `own_domain` |
| `GET /api/admin/federation/journal` | `?status=`, `?limit=` (1 to 200, default 50), `?max_id=`. `{"entries": [...], "next_max_id"}`, newest first; 400 `invalid_status`, `invalid_limit`, `invalid_max_id` |
| `GET /api/admin/federation/journal/{id}` | `{"entry": {..., "payload"}}`; 404 `journal_entry_not_found` |
| `POST /api/admin/federation/journal/{id}/replay` | Body `{}`. 200 `{"entry"}` (audit `federation.journal_replay`); 404 `journal_entry_not_found`; 409 `journal_entry_not_replayable` (with `states`), 409 `federation_off` |

The switches have their own routes: `POST /api/admin/federation` (`federation`), `PUT /api/admin/auto-contribution` (`kb_auto_contribution`), and `/api/admin/federation/domain-blocks`. The console section `/admin/federation` (`console_admin.js`) shows the known servers, with the records and the read state of Section 11, and the journal.

### 10.6 Sidecar (units KF1 and KF2)

Code: `sidecar/fedify/src/knowledge.ts`, `sidecar.ts`, `internal_api.ts`, `main.ts`. The sidecar does this work only while the switch `federation` is on and the mode is `network`, as for its other paths (the single mode part of design Section 15 rule 5 is not implemented in the sidecar).

1. **Inbound activities.** A `POST` to an inbox (`/ap/inbox`, `/ap/actors/{id}/inbox`, `/ap/kb/{number}/inbox`) whose JSON body has the Claire context IRI in its `@context` array is an activity of the knowledge federation. It does not go to the inbox listener of Fedify. The sidecar verifies the HTTP signature of the request (`verifyRequest` of Fedify, with the digest of the body in the signature) and calls `POST /knowledge-activities` with the parsed body (no key changes), `signer` = the owner of the key, `verified_by` = `http-signature`, and `recipient` = the actor of the inbox path, or `null` for the shared inbox. The key document must be on the server of its owner. Answers: 401 without a valid signature; 413 for a body within 8192 bytes of the inbox body limit (the request to Claire has the same limit); 403 for a blocked domain; 503 with `Retry-After` when Claire gave no answer, so that the sender tries again (Claire gives `duplicate` for a second delivery); else 202, for each decision of Claire. An activity with a Linked Data signature or an integrity proof but no valid HTTP signature is not accepted on this path.
2. **Collections.** `GET /ap/kb` and `GET /ap/themes` (with `?page=true` and `&max_id=` for `kb`): the sidecar verifies the HTTP signature and calls `GET /knowledge-collections/{kb|themes}` with the owner of the key as `signer` (no `signer` without a valid signature). It gives the `document` of Claire with `Content-Type: application/activity+json`, and the same `404 Not Found` for each refusal. The paths are below `/ap/`, so the reverse proxy rules of [`FEDERATION.md`](FEDERATION.md) Section 10 send them to the sidecar without a change.
3. **Reads.** One loop of `main.ts` (each 60 seconds) calls `Sidecar.processKnowledgeReads`: `GET /knowledge-server-reads`, then for each domain NodeInfo (`/.well-known/nodeinfo`, then the document of the link) and the server actor (`/ap/actors/server`), then `POST /knowledge-server-reads`; then `GET /knowledge-kb-reads` and the read of each `/kb` collection (Section 11.4). Each request is a GET that the server actor of this server signs (the document loader of Fedify, which refuses a private network address). The server actor and each page must be on the server that was asked. `shared_kb_count` is `null`: NodeInfo of a Claire server does not give the number. Error codes of a server read: `blocked_domain`, `nodeinfo_unavailable`, `nodeinfo_invalid`, `server_actor_unavailable`, `server_actor_invalid`.
4. **Context document.** `sidecar/fedify/src/claire_context_v1.json` is a copy of `src/claire/static/ns/v1.jsonld` (a Python test fails when the two are different). `knowledge.ts` puts it in the table of preloaded contexts of `@fedify/vocab-runtime` (the import `jsr:@fedify/vocab-runtime@^2.4.5`, which is in `deno.lock` as a dependency of Fedify), so that the context loader and the document loaders of Fedify give it without a request.

Unit KF3 added the knowledge base actors, the outbound activities, and the read of documents for a curation (Section 12.7). Not implemented in the sidecar: the work in single mode (item above), and the activities of unit KF4, which need no new code path in the sidecar (Section 12.9).

### 10.7 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_stores.py` | The migration step (a table of another form is kept), the known servers (sources, admin entry, block state, leases, reads), the access rule, the journal (receive, duplicate, contract error with its code, no handler, handler error and refusal, replay, switch off, block, retention). |
| `tests/test_knowledge_federation_routes.py` | The internal routes through the signed internal app; the admin routes (404 for others, origin, recent sign-in, audit, pages, replay). |
| `tests/test_knowledge_federation_e2e.py` | The completion criteria with two app objects and two databases (A and B). The sidecars and the network are simulated: the test calls the internal routes of B with the values that a sidecar gives after it verified a signature. |
| `tests/test_knowledge_federation_internal_contract.py` | Each internal route of the knowledge federation against the fixtures of `tests/fixtures/knowledge_federation/internal/`, and the copy of the context document in the sidecar. |
| `sidecar/fedify/tests/knowledge_test.ts` | The sidecar part (Section 10.6) with the same fixtures: the client calls, each activity kind through an inbox with its JSON unchanged, the signature rules, the collections, and the reads with signed requests. All servers are in the test process. |
| `tests/test_single_mode_redirects.py` | The single mode root (Section 10.8). It replaces the old test of rule S9, which is withdrawn. |

### 10.8 Single Mode Root (design Section 15)

- `/` gives 302 (`Cache-Control: no-store`) to `/@{username}/` of the admin, with the same query. Each other root path of the personal knowledge base gives 308 to `/@{username}/…` for `GET` and `HEAD`; a write at a root path is not redirected and acts on the namespace of the admin. System routes (for example `/health`, `/settings/*`, `/kb/{number}/…`) keep their paths.
- In single mode `/@{username}/…` of the admin is the namespace of the admin, for each reader (single mode has no profile rule). Each other name gets 404.
- The redirects need an `active` admin with a USERNAME. Before the first setup there is no redirect: `/` with the admin session gives 302 to `/setup`; each other request to `/` gets the page "setup not complete" (503, `Cache-Control: no-store`, `Retry-After: 3600`, `data-setup-state="setup_required"`). On a server without anonymous read, an anonymous request gets the 404 of the route policy first.
- Rule S9 of the namespace design is withdrawn. The server setting `admin_username_published` (O15) has no effect; a stored row stays. `POST /api/admin/server/mode` to `single` does not write it and its answer is `{"mode": "single", "changed": true}`.
- Not implemented: the setup confirmation text still says that the USERNAME is public in network mode (design Section 15 rule 4 asks for "both modes"; the text is in the locale catalogs); design Section 15 rules 5 and 6 (the account actor of the admin in single mode, GF1, and the mode change while `federation` is on).

## 11. Unit KF2: `/kb` Collections, Remote Knowledge Bases, Themes Across Servers, Root Page

Design: Sections 8.1, 8.2, 8.3, 9.1, 9.2, and 14.

### 11.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/knowledge_federation/collections.py` | The ActivityStreams form of the `/kb` collection and of the themes collection of this server, and the IRIs of a knowledge base actor (`knowledge_base_iris`). No function changes data. |
| `src/claire/knowledge_federation/remote_kbs.py` | The store of remote knowledge bases: the tables `kf_remote_knowledge_bases` and `kf_remote_kb_reads`, the leases, the batches, the end and the error of a read, `purge_blocked`. No function commits. |
| `src/claire/store/themes.py` | The table `theme_remote_knowledge_bases` and the functions of the remote members. |
| `src/claire/theme_judgement.py` | Step 4b of `run_judgement`, `remote_signature`, `wire_signature`, `subject_score`. |
| `src/claire/api/knowledge_federation_internal.py` | The internal routes (Section 11.4). |
| `src/claire/api/server.py` | The theme view, `GET /explore/federation`, `GET /api/kb/similar`, `PUT /api/admin/themes/{number}/remote-knowledge-bases/{remote_id}`, the root page. |
| `src/claire/store/db.py` | `_migrate_v30_remote_knowledge_bases`, schema version 30. |

### 11.2 The `/kb` Collection for Another Server

- Address: `{origin}/ap/kb`. The root is an `OrderedCollection` with `id`, `totalItems`, and `first` (`{origin}/ap/kb?page=true`). A page is an `OrderedCollectionPage` with `id`, `partOf`, `orderedItems` (at most 20, the highest number first), and `next` (`…&max_id=<number of the last item>`) when the page is full. Each document has the two context IRIs.
- An item is the `claire:KnowledgeBase` of Section 4.1 without `@context`. The actor IRIs are `{origin}/ap/kb/{number}` with `/inbox`, `/outbox`, `/followers`, and `/documents`; `url` is `{origin}/kb/{number}/`. The counts are those of the public page (a hidden document does not count). `claire:signature` comes from `kb_signatures` (the last judgement run): the 256 keys with the highest weight as rows `[type, norm_name, weight]`; a knowledge base without a signature has no `claire:signature`. No document is in the collection.
- The collection has each shared knowledge base, also an archived one. It has no personal knowledge base (KD3).
- The themes collection (`{origin}/ap/themes`) is one `OrderedCollection` of the active themes; an item is `{"type": "Collection", "name", "summary", "url", "claire:knowledgeType"}` without an `id` (design Section 8.1 rule 5 and Section 8.2 rule 2). No server reads it.

### 11.3 Store of Remote Knowledge Bases (schema version 30)

The migration step v30 follows the rule of v29: it makes a table that is not there, and a table of another form becomes `<name>_retired_v30`.

`kf_remote_knowledge_bases` (one row for each shared knowledge base of another server; the key is the IRI of its actor):

| Column | Meaning |
| :--- | :--- |
| `id` | The number of the record on this server. It never leaves the server. |
| `iri`, `domain`, `url` | The actor IRI, its server, and the page on that server (the origin link). |
| `label`, `description`, `knowledge_type`, `document_count`, `entity_count`, `relation_count` | The values of the object. |
| `signature` | The JSON of `claire:signature` as it came, or `NULL`. |
| `payload` | The JSON of the object as it came (it has the inbox and the other IRIs for unit KF3). |
| `first_seen_at`, `seen_at`, `changed_at` | The first read with the record, the last read with it, and the last change of its content. |

`kf_remote_kb_reads` (one row for each server that was leased for a read): `read_started_at` and `lease_until` (a read that did not end; a lease is 600 seconds), `last_read_at` (the last complete read), `stored_count`, `refused_count`, `refused_code` (the last read), `error_count`, `last_error`, `last_error_at`.

`theme_remote_knowledge_bases` (`theme_id`, `remote_kb_id`, `source` `computed` or `admin`, `score`, `pinned`, `excluded`): the membership of a remote knowledge base in a local theme.

Rules:

- A server is due for a read when it is a known server with a good read of its server actor (Section 10.3), no domain block applies, no lease is in effect, and it has no read (the first contact) or its last read or error is one day old.
- Each item of a batch is checked: `vocabulary.validate_object` gives `knowledge_base`; `id` and `url` are on the server that was read and follow the scheme rule; the label has at most 200 characters and the description at most 2000; a read stores at most 1000 knowledge bases of one server. An item outside these rules is refused and counted, with the first code in `refused_code` (the code of `ContractError`, or `not_a_knowledge_base`, `other_server`, `label_too_long`, `description_too_long`, `too_many_knowledge_bases`); the other items are stored.
- The end of a complete read deletes each record of the server that the read did not contain. A failed read deletes nothing.
- When a domain block starts (`POST /api/admin/federation/domain-blocks`), the records and the read state of that domain are deleted in the same transaction (`purge_blocked`); each lease and each judgement run do it again. A record has no document, so no copy goes (design Section 6.3). The lists do not give a record of a blocked domain.
- A record that goes loses its theme memberships, the admin adjustments included. A record whose knowledge type changes leaves its themes.
- Unit KF3 installed the handlers of `update_knowledge_base` and `delete_knowledge_base` (Section 12.4): a change of the label, the description, or the knowledge type comes with an `Update` of the actor that this server follows. The counts and the signature come with the read of the next day.

### 11.4 Internal Routes (the sidecar is the caller)

| Method and path | Request | Response |
| :--- | :--- | :--- |
| `GET /knowledge-kb-reads?limit=<1..20>` | Default 5 | 200 `{"reads": [{"domain", "url"}]}`: the reads that are due; each one gets a lease. `url` is `https://<domain>/ap/kb` (`http` only when the origin of this server is `http`). Empty while the switch is off |
| `POST /knowledge-kb-reads` | `{"domain", "items": [...], "complete", "error"}`; each key is necessary. `error` null: `items` are the objects as they came (at most 100), and `complete` true ends the read. `error` a code `[a-z0-9_]{1,64}`: `items` is empty and `complete` is false | 200 `{"domain", "stored", "refused", "refused_code", "removed"}` (`removed` is null when the read does not end); 400 for a body against these rules; 404 when the domain has no read that a lease started, when a block applies, or when the switch is off |

The sidecar reads the root, then each page (`first`, then `next`): at most 60 pages and 240 seconds for one read, batches of at most 20 items and 512 KiB. Error codes: `blocked_domain`, `collection_unavailable`, `collection_invalid`, `page_on_other_server`, `too_many_pages`, `read_timeout`, `item_too_large`.

### 11.5 Themes Across Servers

- `run_judgement` step 4b: each remote knowledge base with a signature attaches to one local theme of its knowledge type, the one with the highest average similarity with the local shared members that have a signature, when that value is at least `theme_match_threshold`. The row has the source `computed` and the score. Each row that is not pinned and not excluded is computed again in each run.
- A remote knowledge base never makes a theme and does not change the clusters of the local knowledge bases: the idf of the clustering comes from the local signatures; the idf of the attachment comes from the local and the remote signatures of the type. A remote signature has no names and no centroid, so the similarity is the entity similarity, and a remote member does not change the label, the top entities, or the signature of a theme.
- `run_is_due` is also true when a record is new or changed after the last run. The read route does not start a run.
- Admin adjustments for a remote member: `PUT /api/admin/themes/{number}/remote-knowledge-bases/{remote_id}` with `pinned`, `excluded`, or `computed`, with the rules of a local member (a pinned or excluded row stays after a run; one knowledge type for a theme). A merge moves the remote members to the other theme as pinned members. An adjustment changes only this server. A theme with only pinned remote members is `active`.
- The theme view (`GET /themes/{number}/`, `GET /api/admin/themes`): a remote member has `remote` true, `server`, `url` (the origin link), `remote_id`, and `number` null; the view has `shared_knowledge_bases` (this server) and `remote_knowledge_bases`. `GET /themes` has `remote_knowledge_bases` for each theme. A theme number is in no document for another server.

### 11.6 Root Page and the Dialog for a New Shared Knowledge Base

- `GET /explore/federation` (public, network mode only) gives the data of the root page about other servers: `themes` (with the two counts), `servers` (the known servers that have records, with the domain, the software, and the count), and `recent` (the 20 newest records, `kind` `remote_knowledge_base`). A Claire server has no name setting; the domain is its name.
- The root page (`explore_accounts_route`) has the themes first, each with the two counts and with the remote members (the origin link and the domain), then the element `#public-home-federation` that `public_kb.js` fills with the sections "Federated servers" and "Recent activity across servers".
- `GET /api/kb/similar` gives the shared knowledge bases of this server and of other servers near the label and the seed entities that the account typed (`theme_judgement.subject_score`: the part of the terms that are a name in the signature or the label of the candidate, or the Jaccard of the words of the two labels). The dialog of `kb_dialogs.js` shows them before the account makes a new one.
- Not implemented: the other kinds of recent activity of design Section 8.3 rule 3 (contributions that other servers approved, curations), because they come with the units KF3 and KF4.

### 11.7 Choices Made in the Code

1. The collection is at `/ap/kb`, not at `/kb` with content negotiation: the reverse proxy sends `/ap/` to the sidecar, which must verify the signature; `/kb` stays a path of Claire.
2. The collection has pages, so that a server with many knowledge bases stays below the body limit of the internal API (1 MiB).
3. A remote knowledge base attaches to a local theme and never makes one (the design says "a member of a local theme"). Thus a server with no shared knowledge base of a subject shows no theme for it; its known servers are on the root page.
4. A remote knowledge base attaches to one theme (the best one), as a local shared knowledge base is in one computed theme.
5. A domain block deletes the records of the domain (design Section 6.3: no exchange). After the block ends, the next read stores them again.
6. A knowledge activity does not go through the inbox listener of Fedify (Section 10.6 item 1). Thus only an HTTP signature is accepted for it.
7. The similar knowledge bases of the dialog use only the text of the form; the design gives no method.

### 11.8 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_remote.py` | The migration step v30, the leases and the day interval, a batch with refused items, a failed read, the end of a read, a domain block, the admin adjustments for a remote member, and the rule that a remote knowledge base makes no theme. |
| `tests/test_knowledge_federation_kf2_e2e.py` | The completion criterion with two app objects and two databases: a shared knowledge base of B is read by A, is in a theme of A after a judgement run, and is in the data and the HTML of the root page of A; B has no entry for A. The sidecars and the network are simulated by direct calls to the internal routes. |

Not verified: an exchange between two real sidecars over a network (signatures in the two directions, the reverse proxy, NodeInfo of a real server).

## 12. Unit KF3: Knowledge Base Actors, Curation Across Servers, Revisions, Removal at the Origin

Design: Sections 9.2, 9.3, 10, and 11, with Sections 6, 13, and 14 for the controls.

### 12.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/knowledge_federation/actors.py` | The knowledge base actors of this server: the followers, the actor document, the collections, the `Article` of a document with its extraction. Tables `kf_kb_followers`, `kf_kb_actor_state`, `kf_document_announcements`. |
| `src/claire/knowledge_federation/outbox.py` | The outbound activities (`kf_outbox`), the follows of this server (`kf_follows`), and `sync`, which adds the tasks that the state of `claire.db` needs. |
| `src/claire/knowledge_federation/remote_documents.py` | The reads of documents of another server for a curation (`kf_remote_reads`) and the list of the documents of a remote knowledge base (`kf_remote_documents`). |
| `src/claire/knowledge_federation/handlers.py` | The handlers of the inbox journal for the activity kinds of this unit. The import installs them. |
| `src/claire/contributions.py` | `copy_remote_document`, `curate_remote`, `start_remote_revisions`, `origin_removed`, `in_origin_review`, and the authority identifiers in the merge job. |
| `src/claire/store/kb_collab.py` | The columns of a remote source in `kb_contributions` (`ensure_remote_columns`) and their reads. |
| `src/claire/store/actor_keys.py` | `ensure_kb_actor_keys`, `load_kb_actor_keys`, `public_key_pem`. |
| `src/claire/api/knowledge_federation_internal.py` | The internal routes (Section 12.5). |
| `src/claire/api/server.py` | `GET /kb/{number}/remote-sources`, the remote source of `POST /kb/{number}/curations`, and `remote` and `origin_removed` in the view of a contribution. |
| `src/claire/store/db.py` | `_migrate_v31_contribution_remote_columns`, `_migrate_v31_knowledge_base_actors`, schema version 31. |
| `sidecar/fedify/src/knowledge.ts`, `sidecar.ts`, `internal_api.ts`, `main.ts` | The sidecar part (Section 12.7). |

### 12.2 Knowledge Base Actors (design Section 9.2, KD5)

- Each shared knowledge base of this server is an actor. A personal knowledge base is never one (KD3). The IRIs (`collections.knowledge_base_iris`): `{origin}/ap/kb/{number}` with `/inbox`, `/outbox`, `/followers`, and `/documents`. A document: `{origin}/ap/kb/{number}/documents/{contribution}`; `{contribution}` is the public ID of the contribution, which stays when a revision replaces the copy. The public key: `{actor}#main-key`.
- The actor document is the `claire:KnowledgeBase` of Section 4.1 (type `["Group", "claire:KnowledgeBase"]`) with `@context` (ActivityStreams, the Claire context, `https://w3id.org/security/v1`), `manuallyApprovesFollowers` false, and `publicKey` (`id`, `owner`, `publicKeyPem`). It is public while the switch `federation` is on: another server reads it to verify a signature.
- Keys: the table `actor_keys` of `auth.db` ([`FEDERATION.md`](FEDERATION.md) Section 6), with `account_id` = minus the number of the knowledge base. Claire makes the two key pairs at the first request for the actor document or the keys. A number is never used again, so a new knowledge base never gets the keys of a deleted one. The keys of a deleted knowledge base stay in the table.
- The collections answer only a signed request of a server actor (`access.collection_access`, KS1; each refusal is the same 404). `followers` and `outbox` give `totalItems` only. `documents` is an `OrderedCollection` with `first`; a page (`?page=true`, `&max_id=<contribution>`) has at most 20 items, the newest contribution first. An item has `id`, `type` `Article`, `name`, `url` (optional), `claire:contentHash`, `published`, and `updated` (optional), and no content (P3). The document path gives the `Article` of Section 4.2 with its `claire:extraction`.
- A document leaves the server only when its contribution is `visible`, its document row is not hidden, and it has a stored extraction (KS5). `content` is the compiled HTML (or the render of the source text) after the `nh3` sanitizer; a reference that starts at the root of this server gets the origin. A relation whose source or target is not an entity of the extraction is not sent.
- `attributedTo`: for a copy of a remote document, the credit that came; for a local document, the actor of the contributor only when that account is a federation actor ([`FEDERATION.md`](FEDERATION.md) Section 3); else absent. `origin`: only for a copy of a remote document (the document that was copied). `claire:root`: the root that came for such a copy; for each other document its own `id`.
- `sameAs` of an entity: the authority identifiers of the entity of the knowledge base with that name or alias (`entities.props.same_as`). Only a merge of a received extraction sets them (Section 12.4).

### 12.3 Tables (schema version 31)

The step `_migrate_v31_contribution_remote_columns` adds columns to `kb_contributions` that are not there (no row changes). The step `_migrate_v31_knowledge_base_actors` follows the rule of v29: it makes a table that is not there, and a table of another form becomes `<name>_retired_v31`.

`kb_contributions`, new columns (all NULL for a contribution that is not a copy of a remote document):

| Column | Meaning |
| :--- | :--- |
| `remote_source_kb_iri`, `remote_source_iri`, `remote_root_iri`, `remote_credit_iri` | The knowledge base actor of the source, the document that was copied (`origin`), its `claire:root`, and its `attributedTo` (can be NULL). Such a row has no `contributor_account_id`, `source_kb_id`, `source_document_id`, `root_kb_id`, and `root_document_id`. |
| `origin_removed_at`, `origin_removed_reason` | The origin removed the document, with its reason code. |
| `origin_decided_at` | A reviewer decided to show the copy again. |

| Table | Rows |
| :--- | :--- |
| `kf_kb_followers` | `kb_number`, `actor_iri` (the server actor that follows), `domain`, `follow_iri`, `created_at`; one row for each pair of actor and follower. |
| `kf_kb_actor_state` | `kb_number`, `record_digest` (the label, the description, and the knowledge type that the followers have), `updated_at`. |
| `kf_document_announcements` | `contribution_public_id`, `revised_at` (the revision that the followers have), `removed` (the `Delete` is in the outbox). |
| `kf_outbox` | One row for each activity and recipient: `activity_iri`, `kind`, `signer` (`server`, or the number of a knowledge base actor), `recipient` (an actor IRI), `recipient_domain`, `payload` (the activity JSON), `status` (`pending`, `delivered`, `failed`, `refused`), `attempts`, `error_code`, `lease_until`, `next_attempt_at`, `created_at`, `finished_at`. |
| `kf_follows` | The knowledge base actors that the server actor of this server follows: `kb_iri`, `domain`, `follow_iri`, `state` (`pending`, `accepted`), `created_at`, `accepted_at`. |
| `kf_remote_reads` | One read for the sidecar: `kind` (`documents` or `document`), `kb_iri`, `domain`, `url`, `target_iri`, `kb_id` and `account_id` (the knowledge base K and the curator of a `document` read), `status` (`pending`, `done`, `failed`), `attempts`, `pages`, `items`, `error_code`, `contribution_public_id`, `lease_until`, `started_at`, `finished_at`. |
| `kf_remote_documents` | The list of the documents of a remote knowledge base: `kb_iri`, `iri`, `title`, `url`, `content_hash`, `published`, `updated`, `seen_at`. |

### 12.4 Rules

Follows (design Section 9.2 rule 2):

- `outbox.sync` runs when the sidecar takes outbound tasks. The server actor of this server follows a knowledge base actor when the remote knowledge base is a member (not excluded) of an active local theme, or when a contribution that is not closed is a copy from it (and its origin did not remove the document). A new follow is a row of `kf_follows` (`pending`), a `Follow` in the outbox, and a known server (`local_follow` or `local_curation`). When neither condition is true, an `Undo` goes out and the row is deleted. A follow of a blocked domain is deleted without an `Undo`.
- The handler of `follow` accepts each server actor (path `/ap/actors/server`) that follows a shared knowledge base of this server, stores it, and puts the `Accept` in the outbox. Refusals: `not_a_server_actor`, `knowledge_base_not_found`. `undo_follow` deletes the follower. `accept_follow` sets the follow of this server to `accepted` (refusal `follow_not_found`).
- The handlers of `update_knowledge_base`, `delete_knowledge_base`, `update_document`, and `delete_document` refuse an actor that this server does not follow (`not_following`). `delete_knowledge_base` deletes the record and its theme memberships and the follow; the copies stay.

Outbound activities (design Section 9.2 rule 3; the builders of Section 5):

| Activity | Activity IRI | When `sync` adds it |
| :--- | :--- | :--- |
| `Follow` | `{server actor}#follows/{random}` | Above |
| `Undo` | `{Follow IRI}/undo` | Above |
| `Accept` | `{actor}#accepts/{digest of the Follow IRI}` | The handler of `follow` |
| `Update` of the record | `{actor}#updates/{milliseconds}` | The digest of label, description, and knowledge type is different from `kf_kb_actor_state` |
| `Delete` of the record | `{actor}#delete` | The shared knowledge base is gone; then the followers and the state of the actor are deleted |
| `Update` of a document | `{document}#updates/{revised_at in milliseconds}` | A `visible` contribution has a `revised_at` after the one in `kf_document_announcements` and not before the first follower |
| `Delete` of a document | `{document}#delete` | A contribution is `removed` (`contributions.remove`, or a purge of its document) with `decided_at` not before the first follower; `claire:reasonCode` is `decision_reason`, or `other` |

- A task goes to each follower that is not on a blocked domain. The pair of activity IRI and recipient is unique. A task for a blocked domain ends as `refused` (`blocked_domain`).
- Results of the sidecar: `delivered`; `retry` (the task comes again after 60 seconds times 2 for each attempt, at most 6 hours; after 8 attempts `failed`); `rejected` (`failed`). A task without a result comes again after its lease (300 seconds). A task that ended stays 30 days.
- A hide at the origin sends nothing: only a removal does (design Section 10.3).

Curation across servers (design Section 11):

- The sources of K are the remote knowledge bases that are members of an active theme that K is a member of (`remote_documents.sources_of`). A reviewer (capability `curate`) reads the list of documents of a source (`kind` `documents`: the root of `claire:documents`, then each page, at most 50 pages and 1000 documents; an item of another server is not stored; a complete read deletes the rows that it did not contain). The list is read again when it is older than 600 seconds (60 seconds after a failed read).
- A curation request makes one read (`kind` `document`) for each selected document that is in the list. Checks before the read: K has a contribution of that document (`contribution_duplicate`) or removed it (`contribution_tombstoned`).
- When the document comes: `vocabulary.validate_object` gives `document` with an extraction; its `id` is the one that was asked; limits (KS2): content 512 KiB, title 500 characters, 500 entities, 2000 relations. Then `contributions.curate_remote`: the duplicate and tombstone checks of a local curation (`canonical_url`, `content_hash`), a hidden document row from the `Article` (`source_type` `federation`, the HTML after `nh3`, no source text and no files), an extraction row in the form of `ExtractionResult` (the authority identifiers in the key `same_as`), and a contribution with the origin `curation`, the state `merging`, `curator_account_id` and `approved_by_account_id` = the reviewer, and the remote columns. The moderation log gets `curate`. The merge job of Section 4.8 of [`THEMES.md`](THEMES.md) builds the graph without an LLM call.
- The route that takes the answer of the sidecar runs the merge jobs that wait.

Revisions (design Section 10.2):

- The handler of `update_document` calls `contributions.start_remote_revisions` for each copy whose `remote_source_iri` is the `id` of the object and whose `remote_source_kb_iri` is the actor. The rules are those of local revisions: no approval; a contribution with stopped updates and a closed one keep their version; the state stays (a hidden copy stays hidden); the same `claire:contentHash` makes no revision; the newest version wins.
- The handler makes rows only (a new hidden copy, a row of `kb_contribution_revisions`, a merge job). The route `POST /knowledge-activities` and the replay route run the merge jobs after the journal committed. A revision that a newer one closed keeps its copy until its job runs; the job then removes the copy (`_run_revision`).
- Differences from local revisions: a contribution in `failed` or `pending` keeps its version (a remote copy is never `pending`).
- A domain block stops the revisions (the journal refuses the actor); the copies stay (design Section 10.1 rule 3).

Removal at the origin (design Section 10.3, KD1):

- The handler of `delete_document` calls `contributions.origin_removed`: each copy that is not closed gets `origin_removed_at` and the reason code; a `visible` copy becomes `hidden` at once (`documents.hidden = 1`). A copy in `merging` gets the mark, and its merge job leaves it `hidden`. No copy is removed. The moderation log gets `origin_removed` (no actor account).
- The review queue has a contribution with `origin_removed_at`, without `origin_decided_at`, that is not closed (`contributions.in_origin_review`; `origin_removed.waiting` in the view). Decisions: `PATCH` with `unhide` shows it again and sets `origin_decided_at`; `POST .../remove` removes it with a tombstone. A removal here sends a `Delete` only to the followers of the knowledge base of this server; it never changes the origin.

Entities across servers (design Section 10.4):

- Each server resolves entities in its own merge job; no merge decision is received.
- KD7: the merge job reads the authority identifiers of the received extraction. An entity of K with one of the identifiers (`entities.props.same_as`) is matched first: its name goes to the resolver, and the name of the extraction becomes an alias; else the keys decide. After the resolution the entity gets the identifiers. An entity gets identifiers only in this way: no function links an entity to an authority (later work).

### 12.5 Internal Routes (the sidecar is the caller)

Under `/internal/federation/v1`. Contract version 1.10.

| Method and path | Request | Response |
| :--- | :--- | :--- |
| `GET /knowledge-kb-actors/{number}` | No query | 200 `{"document": {...}}`: the actor document; 404 while the switch is off and for each number that is not a shared knowledge base |
| `GET /knowledge-kb-actors/{number}/keys` | — | 200 `{"public_id", "keys": [{"alg", "public_jwk", "private_jwk"}]}` (the form of `/actors/{public_id}/keys`); 404 as above, but the keys of a deleted knowledge base are given while a task of that actor waits |
| `GET /knowledge-kb-actors/{number}/{followers\|outbox\|documents}?signer=<IRI>` | `documents` only: `&page=true`, `&max_id=<contribution>` | 200 `{"document": {...}}`; 404 for each refusal; 400 for another query key or a wrong value |
| `GET /knowledge-kb-actors/{number}/documents/{contribution}?signer=<IRI>` | — | 200 `{"document": {...}}`: the `Article` with `@context` and its extraction; 404 for each refusal and for a document that does not leave the server |
| `GET /knowledge-outbox-tasks?limit=<1..50>` | Default 10 | 200 `{"tasks": [{"id", "kind", "signer", "recipient", "activity"}]}`; `sync` runs first; each task gets a lease. Empty while the switch is off |
| `POST /knowledge-outbox-tasks/{id}/ack` | `{"result": "delivered" \| "retry" \| "rejected", "error_code"}`; `error_code` is null only for `delivered` | 200 `{"id", "status"}`; 404 for no task; 400 for a body against these rules |
| `GET /knowledge-remote-reads?limit=<1..20>` | Default 5 | 200 `{"reads": [{"id", "url"}]}`; each read gets a lease (120 seconds; after 5 leases without an answer the read ends as `read_timeout`). Empty while the switch is off |
| `POST /knowledge-remote-reads` | `{"id", "document", "error"}`: the JSON as it came and null, or null and a code | 200 `{"id", "status": "pending" \| "done" \| "failed", "error_code"}` (`pending`: the next page of a collection follows); 404 for no read that waits, and while the switch is off |

`POST /knowledge-activities` did not change its form; after a `processed` decision it runs the merge jobs that wait. The contract fixtures of the routes with a fixed answer are in `tests/fixtures/knowledge_federation/internal/`; the actor document and the keys have no fixture, because each server makes its own keys.

### 12.6 Public Routes and WebUI

- `GET /kb/{number}/remote-sources` and the remote source of `POST /kb/{number}/curations`: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document. `ROUTE_POLICY`: `_namespaced("owner", "curate")`.
- A contribution has `remote` and (full view) `origin_removed` in each route that gives a contribution.
- `console_kb.js`, tab "Curation": the list of sources has the remote knowledge bases with their server and the follow state; the documents of a remote source come from `?source=`; the requests to copy are in a table. Tab "Review queue": a copy whose origin removed the document is in the list with the tag "Removed at the origin" and the decisions "Show again" and "Remove…" (a moderator or the admin).
- Not implemented: the follow state on the public theme page (`public_kb.js`); the kinds "curation" of the recent activity on the root page (design Section 8.3 rule 3).

### 12.7 Sidecar

Code: `knowledge.ts` (`deliverKnowledgeTask`, `processKnowledgeOutbox`, `readRemoteDocument`, `processRemoteReads`, `KB_ACTOR_PATH`), `sidecar.ts`, `internal_api.ts`, `main.ts`. The work runs only while the switch `federation` is on and the mode is `network`, as in Section 10.6.

1. **Actor paths.** `GET /ap/kb/{number}` gives the actor document of Claire without a change (`application/activity+json`), without a signature check. `GET /ap/kb/{number}/followers`, `/outbox`, `/documents` (with `?page=true` and `&max_id=`), and `/documents/{contribution}`: the sidecar verifies the HTTP signature and gives Claire the owner of the key as `signer` (none without a valid signature); each refusal is the same 404. Fedify does not serve these paths: its actor dispatcher has the accounts and the server actor only.
2. **Outbound activities.** One loop of `main.ts` (each 5 seconds) calls `processKnowledgeRemoteReads` and `processKnowledgeOutbox`. For a task the sidecar reads the actor document of the recipient with a GET that the server actor signs, takes its `inbox` (it must be on the server of the recipient), and sends one POST with the activity JSON of Claire as the body (`JSON.stringify`, no object of the Fedify vocabulary) and an HTTP Signature (`signRequest` of Fedify) of the key `{actor}#main-key` of the signer: the server actor, or the knowledge base actor (`#kbKeyPairs`, the 60 second key cache). Results: 2xx `delivered`; 400, 401, 403, 404, 405, 410, 413, 422 `rejected`; each other answer, no answer, and an actor document that is not available `retry`; `blocked_domain`, `recipient_invalid`, `no_actor_key` `rejected`. A redirect is not followed. The Fedify message queue is not used: Claire gives a task again.
3. **Reads for a curation.** For a read the sidecar gets the address with the document loader that the server actor signs with (it refuses a private address), checks that the answer is on the server that was asked and is a JSON object of at most 900 KiB, and gives it to Claire. Error codes: `document_invalid`, `blocked_domain`, `document_on_other_server`, `document_unavailable`, `document_too_large`.
4. **Inbound activities.** No change: Section 10.6 item 1. An activity that a knowledge base actor signed is verified with the `publicKey` of its actor document.

`deno.json` and `deno.lock` did not change.

### 12.8 Choices Made in the Code

1. The IRI of a document has the public ID of its contribution, so that it stays the same over revisions. The design gives no form.
2. The root on the wire of a document whose root is on this server is the document itself, and `origin` is absent: the root is in a personal knowledge base, which has no address for other servers (KD3, KS5). A curation from another shared knowledge base of the same server also has no `origin` on the wire.
3. `attributedTo` is absent for a contributor that is not a federation actor: an account without a public profile gets no address for other servers. The design says "the actor with the credit".
4. The actor document is public (no signed GET): a server needs it to verify a signature before it can know the signer. The collections and the documents need the signed GET.
5. The curation is asynchronous (202): Claire does not call the sidecar ([`FEDERATION.md`](FEDERATION.md) Section 2), so a read is a row that the sidecar takes.
6. The followers get an `Update` of the record only for the label, the description, and the knowledge type. The counts and the signature change with each document.
7. `Follow` is accepted for each server actor of a server that is not blocked (design Section 6.1: no agreement).
8. A copy whose origin removed the document is no reason to follow the actor. When the remote knowledge base is also in no theme, the follow ends.
9. The keys of the knowledge base actors are in the table `actor_keys` with a negative `account_id`, without a change of the schema of `auth.db`.
10. The `Delete` of a knowledge base does not hide its copies: the design (Section 9.3) gives "change of the record" as its effect, and Section 10.3 is about a document.
11. A request to curate a document that the sidecar cannot read ends as `failed`; the reviewer asks again. The account of the curator is stored in the read (`kf_remote_reads.account_id`) until the read is deleted (30 days).

### 12.9 Interfaces for the Activities of Units KF4 and KF5

- Outbound: `outbox.enqueue(conn, activity, signer=, recipients=, origin=, now=)` takes each activity of `vocabulary`. `Accept` with `result`, `Reject`, `Grant`, and `Revoke` have a knowledge base actor as the signer and need no change in the sidecar. An `Offer` has an account actor as the signer: `signingKey` of the sidecar knows only `server` and the number of a knowledge base, and `signer` has no value for an account actor (the sidecar has the keys of an account actor in `#keyPairs`).
- Inbound: `journal.register_handler` for `offer_contribution`, `accept_offer`, `reject_offer`, `grant`, `revoke`. `contributions.copy_remote_document` makes the document and extraction rows of a received `claire:Contribution`; `kb_collab.add_contribution(remote=...)` keeps the remote credit.
- The read of one remote object: a row of `kf_remote_reads` (a new `kind`).
- Not implemented: a store of grants, the pending limits for each remote account and each domain (KS3), the credit "deleted account" after `Delete(Person)` (design Section 12.1 rule 4), and the submission page for a remote target.

### 12.10 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_kf3_e2e.py` | The completion criteria with two app objects and two databases (the mock provider): A curates a document of a knowledge base of B; `Follow` and `Accept`; a revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue. The sidecars and the network are simulated by direct calls to the internal routes. |
| `tests/test_knowledge_federation_kf3.py` | The migration step v31; the handlers with the outbox (follow, undo, accept, the changes of a record, the retry rule, a blocked domain, a deleted knowledge base); the follows of this server; the read of a documents collection; the rules of revisions for a remote copy, the authority identifiers, and the removal at the origin. |
| `tests/test_knowledge_federation_internal_contract.py` | The internal routes of Section 12.5 against their fixtures. |
| `sidecar/fedify/tests/knowledge_test.ts` | The client calls against the fixtures; the actor paths; a delivery with the body unchanged and a signature that verifies with the actor document of the sidecar; the results of a delivery; a read for a curation. |

Not verified: an exchange between two real sidecars over a network; the WebUI in a browser (no Playwright spec uses the new parts); a delivery to software that is not Claire.
