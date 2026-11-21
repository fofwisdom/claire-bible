# Knowledge Federation Implementation Reference

Basis: units KF0 to KF6 (2026-11-19). Readers: agents who work on the federation of shared knowledge.

Design: [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md). This document tells what the code has now. The general federation (actors of accounts, publishing, remote reading) is in [`FEDERATION.md`](FEDERATION.md). The local shared knowledge bases are in [`THEMES.md`](THEMES.md) Sections 4.7 to 4.12.

## 1. Scope and State

Units KF0 to KF6 are implemented. KF0: the vocabulary, the JSON-LD context, the builders, the validators, the contract fixtures, and the routes `/ns/v1` and `/ns`. KF1: the known servers, the admin controls, the inbox journal, and the single mode root (Section 10). KF2: the `/kb` collection for other servers, the store of remote knowledge bases, the themes across servers, and the root page (Section 11). KF3: the knowledge base actors, the curation across servers, the revisions, and the removal at the origin (Section 12). KF4: the contributions across servers, the remote collaborators, and the single mode part of design Section 15 rules 5 and 6 (Section 13). KF5: the automatic contribution across servers (Section 15). KF6: the readiness checks GF8 to GF17, the knowledge self-check and loop state in the sidecar heartbeat (contract 1.12), `/ap/self-check`, the system trust store for readiness HTTP requests, the gate in `POST /api/admin/federation`, and the console and CLI displays (Section 17). The sidecar does its part of KF1 to KF4 and KF6 (Sections 10.6, 12.7, 13.6, and 17.3). The limits and the checks of a security review of KF1 to KF4 (schema version 33) are in Section 14.

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
| Contributions and grants across servers (KF4) | Implemented (Section 13) |
| Automatic contribution across servers (KF5) | Implemented (Section 15) |
| Knowledge readiness checks GF8 to GF17 and contract 1.12 (KF6) | Implemented (Section 17). Acceptance path steps KA1 to KA5 are pending execution against two running servers. |
| Registration on `w3id.org` | Owner task, after `claire.bible` serves the two paths |

## 2. Files

| File | Content |
| :--- | :--- |
| `src/claire/federation/knowledge/vocabulary.py` | Identifiers, the table `TERMS`, `context_document`, `vocabulary_page`, builders, validators. No database access and no network access. |
| `src/bible/static/ns/v1.jsonld` | The context document of version 1. It is the text of `context_json()`. |
| `src/bible/static/ns/index.html` | The page of the vocabulary terms. It is the text of `vocabulary_page()`. Each term has an anchor. |
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
| `claire:reasonCode` | Property | `…/ns#reasonCode` | `off_subject`, `wrong_knowledge_type`, `duplicate`, `low_quality`, `spam`, `other`; in a `Reject` also `automatic_not_accepted` (unit KF5) |
| `claire:automatic` | Property | `…/ns#automatic` | `true` in a `claire:Contribution` that the automatic rule of the account selected (unit KF5) |
| `claire:acceptsAutomatic` | Property | `…/ns#acceptsAutomatic` | Boolean in a `claire:KnowledgeBase`: its server accepts automatic contributions now (unit KF5) |
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
| `claire:acceptsAutomatic` | Optional boolean (unit KF5): `true` when the switches `federation` and `kb_auto_contribution` of its server are on now. The builder always writes it (`kb["accepts_automatic"]`); an object without it is valid, and a reader then does not know the state. |

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
| `claire:automatic` | Optional boolean (unit KF5). The builder writes `true` only for an automatic contribution (`automatic=True`); without it, the account selected the document itself. |

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
| `reject_offer` | `Reject` | Knowledge base actor → account actor | `object`: IRI of the `Offer`; `claire:reasonCode` (one of `REJECT_REASON_CODES`: the reason codes and `automatic_not_accepted`) | `build_reject_offer` |
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
- The code lists are the ones of `src/claire/collaboration/contributions.py` and `src/claire/store/kb_collab.py`. The wire extraction is a valid `ExtractionResult`.

Not tested: the context document with a JSON-LD processor (the repository has none in Python). The tests of KF1 are in Section 10.7.

## 10. Unit KF1: Known Servers, Admin Controls, Inbox Journal, Single Mode Root

Design: Sections 6.2, 6.3, 6.5, 13, 14, and 15.

### 10.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/federation/knowledge/known_servers.py` | The table `kf_known_servers`: how a server becomes known, the leases of the reads, the result of a read. No function commits. |
| `src/claire/federation/knowledge/access.py` | The one-sided controls: the switch `federation`, the domain blocks, the signed read of a collection. No function changes data. |
| `src/claire/federation/knowledge/journal.py` | The table `kf_inbox_journal`: receive, process, replay, retention, and the dispatch table `HANDLERS`. `receive` and `process` commit. |
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
| `payload` | The activity JSON as it came; `{}` for a `refused` row (Section 14). |
| `quota_domain`, `payload_bytes` | The registrable domain of the signer and the size of the activity: the quota of 24 hours (schema version 33, Section 14). |
| `status`, `error_code`, `error_detail`, `attempts`, `received_at`, `processed_at` | The state (Section 10.3), the error, the number of processings, and the times. |

The journal is in `claire.db`, not in `fedify.db` (design Section 13 rule 4): the admin starts a replay in Claire, and the state of the row and the changes of a handler are in one transaction.

### 10.3 Rules

Known servers (design Section 6.2):

- An admin entry (`POST /api/admin/federation/known-servers`) makes a known server with the source `admin`.
- A server becomes known only when a handler accepts its activity: `offer_contribution` with the source `remote_contribution` when the offer makes a contribution, `follow` with `remote_curation` when the follower is stored. Each other activity changes `last_contact_at` of a known server only (Section 14).
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

- A delivery gets no row (`rejected`) for: `federation_off`, `invalid_actor`, `blocked_domain` (controls for the signer), `invalid_activity` (no JSON object, or no `id` that is an address of another server), `signer_mismatch` (the `id` is not on the origin of the signer, or the `actor` is not the signer and the signer is not the server actor of the origin of the actor; Section 14), `domain_quota` (Section 14).
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

Code: `sidecar/fedify/src/knowledge.ts`, `sidecar.ts`, `internal_api.ts`, `main.ts`. The sidecar does this work only while the switch `federation` is on, as for its other paths. Since unit KF4 the mode is not a condition (design Section 15 rule 5, Section 13.7).

1. **Inbound activities.** A `POST` to an inbox (`/ap/inbox`, `/ap/actors/{id}/inbox`, `/ap/kb/{number}/inbox`) whose JSON body has the Claire context IRI in its `@context` array is an activity of the knowledge federation. It does not go to the inbox listener of Fedify. The sidecar verifies the HTTP signature of the request (`verifyRequest` of Fedify, with the digest of the body in the signature) and calls `POST /knowledge-activities` with the parsed body (no key changes), `signer` = the owner of the key, `verified_by` = `http-signature`, and `recipient` = the actor of the inbox path, or `null` for the shared inbox. The key document must be on the server of its owner. Answers: 401 without a valid signature; 413 for a body within 8192 bytes of the inbox body limit (the request to Claire has the same limit); 403 for a blocked domain; 503 with `Retry-After` when Claire gave no answer, so that the sender tries again (Claire gives `duplicate` for a second delivery); else 202, for each decision of Claire. An activity with a Linked Data signature or an integrity proof but no valid HTTP signature is not accepted on this path.
2. **Collections.** `GET /ap/kb` and `GET /ap/themes` (with `?page=true` and `&max_id=` for `kb`): the sidecar verifies the HTTP signature and calls `GET /knowledge-collections/{kb|themes}` with the owner of the key as `signer` (no `signer` without a valid signature). It gives the `document` of Claire with `Content-Type: application/activity+json`, and the same `404 Not Found` for each refusal. The paths are below `/ap/`, so the reverse proxy rules of [`FEDERATION.md`](FEDERATION.md) Section 10 send them to the sidecar without a change.
3. **Reads.** One loop of `main.ts` (each 60 seconds) calls `Sidecar.processKnowledgeReads`: `GET /knowledge-server-reads`, then for each domain NodeInfo (`/.well-known/nodeinfo`, then the document of the link) and the server actor (`/ap/actors/server`), then `POST /knowledge-server-reads`; then `GET /knowledge-kb-reads` and the read of each `/kb` collection (Section 11.4). Each request is a GET that the server actor of this server signs (the document loader of Fedify, which refuses a private network address). The server actor and each page must be on the server that was asked. `shared_kb_count` is `null`: NodeInfo of a Claire server does not give the number. Error codes of a server read: `blocked_domain`, `nodeinfo_unavailable`, `nodeinfo_invalid`, `server_actor_unavailable`, `server_actor_invalid`.
4. **Context document.** `sidecar/fedify/src/claire_context_v1.json` is a copy of `src/bible/static/ns/v1.jsonld` (a Python test fails when the two are different). `knowledge.ts` puts it in the table of preloaded contexts of `@fedify/vocab-runtime` (the import `jsr:@fedify/vocab-runtime@^2.4.5`, which is in `deno.lock` as a dependency of Fedify), so that the context loader and the document loaders of Fedify give it without a request.

Unit KF3 added the knowledge base actors, the outbound activities, and the read of documents for a curation (Section 12.7). Unit KF4 added the account actor as a signer and the work in single mode (Section 13.6).

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

- `/` gives 302 (`Cache-Control: no-store`) to `/@{username}/` of the admin, with the same query. Each other root path of the personal knowledge base gives 308 to `/@{username}/…` for `GET` and `HEAD`; a write at a root path is not redirected and acts on the namespace of the admin. System routes (for example `/health`, `/settings/*`, `/moderation/*`, `/kb/{number}/…`) keep their paths.
- In single mode `/@{username}/…` of the admin is the namespace of the admin, for each reader (single mode has no profile rule). Each other name gets 404.
- The redirects need an `active` admin with a USERNAME. Before the first setup there is no redirect: `/` with the admin session gives 302 to `/setup`; each other request to `/` gets the page "setup not complete" (503, `Cache-Control: no-store`, `Retry-After: 3600`, `data-setup-state="setup_required"`). On a server without anonymous read, an anonymous request gets the 404 of the route policy first.
- Rule S9 of the namespace design is withdrawn. The server setting `admin_username_published` (O15) has no effect; a stored row stays. `POST /api/admin/server/mode` to `single` does not write it and its answer is `{"mode": "single", "changed": true}`.
- Not implemented: the setup confirmation text still says that the USERNAME is public in network mode (design Section 15 rule 4 asks for "both modes"; the text is in the locale catalogs). Design Section 15 rules 5 and 6 (the account actor of the admin in single mode, GF1, and the mode change while `federation` is on) are implemented in unit KF4 (Section 13.7).

## 11. Unit KF2: `/kb` Collections, Remote Knowledge Bases, Themes Across Servers, Root Page

Design: Sections 8.1, 8.2, 8.3, 9.1, 9.2, and 14.

### 11.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/federation/knowledge/collections.py` | The ActivityStreams form of the `/kb` collection and of the themes collection of this server, and the IRIs of a knowledge base actor (`knowledge_base_iris`). No function changes data. |
| `src/claire/federation/knowledge/remote_kbs.py` | The store of remote knowledge bases: the tables `kf_remote_knowledge_bases` and `kf_remote_kb_reads`, the leases, the batches, the end and the error of a read, `purge_blocked`. No function commits. |
| `src/claire/store/themes.py` | The table `theme_remote_knowledge_bases` and the functions of the remote members. |
| `src/claire/collaboration/theme_judgement.py` | Step 4b of `run_judgement`, `remote_signature`, `wire_signature`, `subject_score`. |
| `src/claire/api/knowledge_federation_internal.py` | The internal routes (Section 11.4). |
| `src/claire/api/server.py` | The theme view, the recent activity of the root page (`_recent_activity`), `GET /api/kb/similar`, `PUT /api/admin/themes/{number}/remote-knowledge-bases/{remote_id}`, the root page. |
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
| `payload` | The JSON of the keys of the contract of the object (`remote_kbs.RECORD_KEYS`: it has the inbox and the other IRIs for unit KF3); no other key is stored (Section 14). `list_all` does not read it. |
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

- The root page of network mode (`public_directory_route` with `root_page`) has the themes first, each with the two counts and with the remote members (the origin link and the domain). Then it has the recent activity across servers (Section 16.1) in the JSON element `#public-home-recent-data`; `public_kb.js` renders it in `#public-home-recent`. The JSON text has `<`, `>`, and `&` as `\u003c`, `\u003e`, and `\u0026`, so that a label of another server cannot end the element. Then it has the first accounts of the public account directory with a link to `/accounts` (ACCOUNTS.md Section 5.11).
- The list of the federated servers is not public (owner decision, 2026-11-19): no public page and no public route has it. The admin sees the known servers in `/admin/federation`. A Claire server has no name setting; the domain is its name.
- The data route `GET /explore/federation` is removed (404 in both modes). The counts of the themes are in `GET /themes`.
- `GET /api/kb/similar` gives the shared knowledge bases of this server and of other servers near the label and the seed entities that the account typed (`theme_judgement.subject_score`: the part of the terms that are a name in the signature or the label of the candidate, or the Jaccard of the words of the two labels). The dialog of `kb_dialogs.js` shows them before the account makes a new one.
- The other kinds of recent activity of design Section 8.3 rule 3 (contributions that other servers approved, curations) are in Section 16.1.

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
| `src/claire/federation/knowledge/actors.py` | The knowledge base actors of this server: the followers, the actor document, the collections, the `Article` of a document with its extraction. Tables `kf_kb_followers`, `kf_kb_actor_state`, `kf_document_announcements`. |
| `src/claire/federation/knowledge/outbox.py` | The outbound activities (`kf_outbox`), the follows of this server (`kf_follows`), and `sync`, which adds the tasks that the state of `claire.db` needs. |
| `src/claire/federation/knowledge/remote_documents.py` | The reads of documents of another server for a curation (`kf_remote_reads`) and the list of the documents of a remote knowledge base (`kf_remote_documents`). |
| `src/claire/federation/knowledge/handlers.py` | The handlers of the inbox journal for the activity kinds of this unit. The import installs them. |
| `src/claire/collaboration/contributions.py` | `copy_remote_document`, `curate_remote`, `start_remote_revisions`, `origin_removed`, `in_origin_review`, and the authority identifiers in the merge job. |
| `src/claire/store/kb_collab.py` | The columns of a remote source in `kb_contributions` (`ensure_remote_columns`) and their reads. |
| `src/claire/store/actor_keys.py` | `ensure_kb_actor_keys`, `load_kb_actor_keys`, `public_key_pem`. |
| `src/claire/api/knowledge_federation_internal.py` | The internal routes (Section 12.5). |
| `src/claire/api/server.py` | `GET /kb/{number}/remote-sources`, the remote source of `POST /kb/{number}/curations`, and `remote` and `origin_removed` in the view of a contribution. |
| `src/claire/store/db.py` | `_migrate_v31_contribution_remote_columns`, `_migrate_v31_knowledge_base_actors`, schema version 31. |
| `sidecar/fedify/src/knowledge.ts`, `sidecar.ts`, `internal_api.ts`, `main.ts` | The sidecar part (Section 12.7). |

### 12.2 Knowledge Base Actors (design Section 9.2, KD5)

- Each shared knowledge base of this server is an actor. A personal knowledge base is never one (KD3). The IRIs (`collections.knowledge_base_iris`): `{origin}/ap/kb/{number}` with `/inbox`, `/outbox`, `/followers`, and `/documents`. A document: `{origin}/ap/kb/{number}/documents/{contribution}`; `{contribution}` is the public ID of the contribution, which stays when a revision replaces the copy. The public key: `{actor}#main-key`.
- The actor document is the `claire:KnowledgeBase` of Section 4.1 (type `["Group", "claire:KnowledgeBase"]`) with `@context` (ActivityStreams, the Claire context, `https://w3id.org/security/v1`), `manuallyApprovesFollowers` false, and `publicKey` (`id`, `owner`, `publicKeyPem`). It is public while the switch `federation` is on: another server reads it to verify a signature.
- Keys: the table `actor_keys` of `auth.db` ([`FEDERATION.md`](FEDERATION.md) Section 6), with `account_id` = minus the number of the knowledge base. Claire makes the two key pairs at the first request for the actor document or the keys. A number is never used again, so a new knowledge base never gets the keys of a deleted one. The keys of a deleted knowledge base are deleted when no task of its actor waits (Section 14).
- The collections answer only a signed request of a server actor (`access.collection_access`, KS1; each refusal is the same 404). `followers` and `outbox` give `totalItems` only. `documents` is an `OrderedCollection` with `first`; a page (`?page=true`, `&max_id=<contribution>`) has at most 20 items, the newest contribution first. An item has `id`, `type` `Article`, `name`, `url` (optional), `claire:contentHash`, `published`, and `updated` (optional), and no content (P3). The document path gives the `Article` of Section 4.2 with its `claire:extraction`.
- A document leaves the server only when its contribution is `visible`, its document row is not hidden, and it has a stored extraction (KS5). `content` is the compiled HTML (or the render of the source text) after the `nh3` sanitizer; a reference that starts at the root of this server gets the origin. A relation whose source or target is not an entity of the extraction is not sent.
- `attributedTo`: for a copy of a remote document, the credit that came; for a local document, the actor of the contributor only when that account is a federation actor ([`FEDERATION.md`](FEDERATION.md) Section 3); else absent. `origin`: only for a copy of a remote document (the document that was copied). `claire:root`: the root that came for such a copy; for each other document its own `id`.
- `sameAs` of an entity: none. Each authority identifier of this server came from another server, and this server does not send it onward (Section 14, KS4).

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
| `kf_kb_actor_state` | `kb_number`, `record_digest` (the label, the description, the knowledge type, and, since unit KF5, `claire:acceptsAutomatic` that the followers have), `updated_at`. |
| `kf_document_announcements` | `contribution_public_id`, `revised_at` (the revision that the followers have), `removed` (the `Delete` is in the outbox). |
| `kf_outbox` | One row for each activity and recipient: `activity_iri`, `kind`, `signer` (`server`, or the number of a knowledge base actor), `recipient` (an actor IRI), `recipient_domain`, `status` (`pending`, `delivered`, `failed`, `refused`), `attempts`, `error_code`, `lease_until`, `next_attempt_at`, `created_at`, `finished_at`. The activity JSON is in `kf_outbox_activities` (`activity_iri`, `payload`), one time for each activity (schema version 33). |
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
| `Update` of the record | `{actor}#updates/{milliseconds}` | The digest of label, description, knowledge type, and `claire:acceptsAutomatic` (unit KF5) is different from `kf_kb_actor_state` |
| `Delete` of the record | `{actor}#delete` | The shared knowledge base is gone; then the followers and the state of the actor are deleted |
| `Update` of a document | `{document}#updates/{revised_at in milliseconds}` | A `visible` contribution has a `revised_at` after the one in `kf_document_announcements` and not before the first follower |
| `Delete` of a document | `{document}#delete` | A contribution is `removed` (`contributions.remove`, or a purge of its document) with `decided_at` not before the first follower; `claire:reasonCode` is `decision_reason`, or `other` |

- A task goes to each follower that is not on a blocked domain. The pair of activity IRI and recipient is unique. A task for a blocked domain ends as `refused` (`blocked_domain`).
- Results of the sidecar: `delivered`; `retry` (the task comes again after 60 seconds times 2 for each attempt, at most 6 hours; after 8 attempts `failed`); `rejected` (`failed`). A task without a result comes again after its lease (300 seconds). A task that ended stays 30 days.
- A hide at the origin sends nothing: only a removal does (design Section 10.3).

Curation across servers (design Section 11):

- The sources of K are the remote knowledge bases that are members of an active theme that K is a member of (`remote_documents.sources_of`). A reviewer (capability `curate`) reads the list of documents of a source (`kind` `documents`: the root of `claire:documents`, then each page, at most 50 pages and 1000 documents; an item of another server is not stored; a complete read deletes the rows that it did not contain). The list is read again when it is older than 600 seconds (60 seconds after a failed read).
- A curation request makes one read (`kind` `document`) for each selected document that is in the list. Checks before the read: K has a contribution of that document (`contribution_duplicate`) or removed it (`contribution_tombstoned`).
- When the document comes: `vocabulary.validate_object` gives `document` with an extraction; its `id` is the one that was asked; limits (KS2): content 512 KiB, title 500 characters, 500 entities, 2000 relations. Then `contributions.curate_remote`: the duplicate and tombstone checks of a local curation (`canonical_url`, and the content hash that this server computes, Section 14), a hidden document row from the `Article` (`source_type` `federation`, the HTML after `sanitize_remote_html`, no source text and no files), an extraction row in the form of `ExtractionResult` (the authority identifiers in the key `same_as`), and a contribution with the origin `curation`, the state `merging`, `curator_account_id` and `approved_by_account_id` = the reviewer, and the remote columns. The moderation log gets `curate`. The merge job of Section 4.8 of [`THEMES.md`](THEMES.md) builds the graph without an LLM call.
- The route that takes the answer of the sidecar runs the merge jobs that wait, after its answer (Section 14).

Revisions (design Section 10.2):

- The handler of `update_document` calls `contributions.start_remote_revisions` for each copy whose `remote_source_iri` is the `id` of the object and whose `remote_source_kb_iri` is the actor. The rules are those of local revisions: no approval; a contribution with stopped updates and a closed one keep their version; the state stays (a hidden copy stays hidden); the same content makes no revision; the newest version wins. The checks and the limits of Section 14 apply first.
- The handler makes rows only (a new hidden copy, a row of `kb_contribution_revisions`, a merge job). The route `POST /knowledge-activities` and the replay route run the merge jobs after the journal committed. A revision that a newer one closed keeps its copy until its job runs; the job then removes the copy (`_run_revision`).
- Differences from local revisions: a contribution in `failed` or `pending` keeps its version (a remote copy is never `pending`).
- A domain block stops the revisions (the journal refuses the actor); the copies stay (design Section 10.1 rule 3).

Removal at the origin (design Section 10.3, KD1):

- The handler of `delete_document` calls `contributions.origin_removed`: each copy that is not closed gets `origin_removed_at` and the reason code; a `visible` copy becomes `hidden` at once (`documents.hidden = 1`). A copy in `merging` gets the mark, and its merge job leaves it `hidden`. No copy is removed. The moderation log gets `origin_removed` (no actor account).
- The review queue has a contribution with `origin_removed_at`, without `origin_decided_at`, that is not closed (`contributions.in_origin_review`; `origin_removed.waiting` in the view). Decisions: `PATCH` with `unhide` shows it again and sets `origin_decided_at`; `POST .../remove` removes it with a tombstone. A removal here sends a `Delete` only to the followers of the knowledge base of this server; it never changes the origin.

Entities across servers (design Section 10.4):

- Each server resolves entities in its own merge job; no merge decision is received.
- KD7: the merge job reads the authority identifiers of the received extraction (only the forms of Section 14). An entity of K with one of the identifiers, the same type, and a name or an alias that agrees (`entities.props.same_as`) is matched first: its name goes to the resolver, and the name of the extraction becomes an alias; else the keys decide. After the resolution the entity gets the identifiers only when it matched by them or is new. An entity gets identifiers only in this way: no function links an entity to an authority (later work).

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

`POST /knowledge-activities` did not change its form; after a `processed` decision the merge jobs that wait run after the answer (a background task of the request, Section 14). The contract fixtures of the routes with a fixed answer are in `tests/fixtures/knowledge_federation/internal/`; the actor document and the keys have no fixture, because each server makes its own keys.

### 12.6 Public Routes and WebUI

- `GET /kb/{number}/remote-sources` and the remote source of `POST /kb/{number}/curations`: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document. `ROUTE_POLICY`: `_namespaced("owner", "curate")`.
- A contribution has `remote` and (full view) `origin_removed` in each route that gives a contribution.
- `console_kb.js`, tab "Curation": the list of sources has the remote knowledge bases with their server and the follow state; the documents of a remote source come from `?source=`; the requests to copy are in a table. Tab "Review queue": a copy whose origin removed the document is in the list with the tag "Removed at the origin" and the decisions "Show again" and "Remove…" (a moderator or the admin).
- The follow state on the public theme page and the kind "curation" of the recent activity on the root page are in Section 16.

### 12.7 Sidecar

Code: `knowledge.ts` (`deliverKnowledgeTask`, `processKnowledgeOutbox`, `readRemoteDocument`, `processRemoteReads`, `KB_ACTOR_PATH`), `sidecar.ts`, `internal_api.ts`, `main.ts`. The work runs only while the switch `federation` is on, as in Section 10.6 (since unit KF4 in each mode).

1. **Actor paths.** `GET /ap/kb/{number}` gives the actor document of Claire without a change (`application/activity+json`), without a signature check. `GET /ap/kb/{number}/followers`, `/outbox`, `/documents` (with `?page=true` and `&max_id=`), and `/documents/{contribution}`: the sidecar verifies the HTTP signature and gives Claire the owner of the key as `signer` (none without a valid signature); each refusal is the same 404. Fedify does not serve these paths: its actor dispatcher has the accounts and the server actor only.
2. **Outbound activities.** One loop of `main.ts` (each 5 seconds) calls `processKnowledgeRemoteReads` and `processKnowledgeOutbox`. For a task the sidecar reads the actor document of the recipient with a GET that the server actor signs, takes its `inbox` (it must be on the server of the recipient), and sends one POST with the activity JSON of Claire as the body (`JSON.stringify`, no object of the Fedify vocabulary) and an HTTP Signature (`signRequest` of Fedify) of the key `{actor}#main-key` of the signer: the server actor, or the knowledge base actor (`#kbKeyPairs`, the 60 second key cache). Results: 2xx `delivered`; 400, 401, 403, 404, 405, 410, 413, 422 `rejected`; each other answer, no answer, and an actor document that is not available `retry`; `blocked_domain`, `recipient_invalid`, `no_actor_key` `rejected`. A redirect is not followed. The Fedify message queue is not used: Claire gives a task again.
3. **Reads for a curation.** For a read the sidecar gets the address with the document loader that the server actor signs with (it refuses a private address), checks that the answer is on the server that was asked and is a JSON object of at most 900 KiB, and gives it to Claire. Error codes: `document_invalid`, `blocked_domain`, `document_on_other_server`, `document_unavailable`, `document_too_large`, `origin_removed` (a 404 or 410 of the origin), `read_timeout` (Section 14).
4. **Inbound activities.** No change: Section 10.6 item 1. An activity that a knowledge base actor signed is verified with the `publicKey` of its actor document.

`deno.json` and `deno.lock` did not change.

### 12.8 Choices Made in the Code

1. The IRI of a document has the public ID of its contribution, so that it stays the same over revisions. The design gives no form.
2. The root on the wire of a document whose root is on this server is the document itself, and `origin` is absent: the root is in a personal knowledge base, which has no address for other servers (KD3, KS5). A curation from another shared knowledge base of the same server also has no `origin` on the wire.
3. `attributedTo` is absent for a contributor that is not a federation actor: an account without a public profile gets no address for other servers. The design says "the actor with the credit".
4. The actor document is public (no signed GET): a server needs it to verify a signature before it can know the signer. The collections and the documents need the signed GET.
5. The curation is asynchronous (202): Claire does not call the sidecar ([`FEDERATION.md`](FEDERATION.md) Section 2), so a read is a row that the sidecar takes.
6. The followers get an `Update` of the record only for the label, the description, and the knowledge type (since unit KF5 also for `claire:acceptsAutomatic`). The counts and the signature change with each document.
7. `Follow` is accepted for each server actor of a server that is not blocked (design Section 6.1: no agreement).
8. A copy whose origin removed the document is no reason to follow the actor. When the remote knowledge base is also in no theme, the follow ends.
9. The keys of the knowledge base actors are in the table `actor_keys` with a negative `account_id`, without a change of the schema of `auth.db`.
10. The `Delete` of a knowledge base does not hide its copies: the design (Section 9.3) gives "change of the record" as its effect, and Section 10.3 is about a document.
11. A request to curate a document that the sidecar cannot read ends as `failed`; the reviewer asks again. The account of the curator is stored in the read (`kf_remote_reads.account_id`) until the read is deleted (30 days).

### 12.9 Interfaces for the Activities of Units KF4 and KF5

- Outbound: `outbox.enqueue(conn, activity, signer=, recipients=, origin=, now=)` takes each activity of `vocabulary`. `Accept` with `result`, `Reject`, `Grant`, and `Revoke` have a knowledge base actor as the signer and need no change in the sidecar. An `Offer` has an account actor as the signer: `signingKey` of the sidecar knows only `server` and the number of a knowledge base, and `signer` has no value for an account actor (the sidecar has the keys of an account actor in `#keyPairs`).
- Inbound: `journal.register_handler` for `offer_contribution`, `accept_offer`, `reject_offer`, `grant`, `revoke`. `contributions.copy_remote_document` makes the document and extraction rows of a received `claire:Contribution`; `kb_collab.add_contribution(remote=...)` keeps the remote credit.
- The read of one remote object: a row of `kf_remote_reads` (a new `kind`).
- Unit KF4 implemented these interfaces (Section 13): the signer `account:{public ID}`, the handlers, the stores of offers and grants, the pending limits for each remote account and each domain (KS3), the credit "deleted account" after `Delete(Person)`, and the submission page for a remote target.

### 12.10 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_kf3_e2e.py` | The completion criteria with two app objects and two databases (the mock provider): A curates a document of a knowledge base of B; `Follow` and `Accept`; a revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue. The sidecars and the network are simulated by direct calls to the internal routes. |
| `tests/test_knowledge_federation_kf3.py` | The migration step v31; the handlers with the outbox (follow, undo, accept, the changes of a record, the retry rule, a blocked domain, a deleted knowledge base); the follows of this server; the read of a documents collection; the rules of revisions for a remote copy, the authority identifiers, and the removal at the origin. |
| `tests/test_knowledge_federation_internal_contract.py` | The internal routes of Section 12.5 against their fixtures. |
| `sidecar/fedify/tests/knowledge_test.ts` | The client calls against the fixtures; the actor paths; a delivery with the body unchanged and a signature that verifies with the actor document of the sidecar; the results of a delivery; a read for a curation. |

Not verified: an exchange between two real sidecars over a network; the WebUI in a browser (no Playwright spec uses the new parts); a delivery to software that is not Claire.

## 13. Unit KF4: Contributions and Remote Collaborators Across Servers

Design: Sections 9.3, 12.1, 12.2, 13, 14, and 15 rule 5; [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) Sections 6, 8.6, 9.1 to 9.6, and 10.

### 13.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/federation/knowledge/offers.py` | The offers that go out (`kf_outbound_offers`: `send`, `skip`, `record_decision`, `result_removed`, `outbound_view`) and the offers that come in (`kf_inbound_offers`: `record_inbound`, `answer_refusal`, `sync`, `actor_deleted`). No function commits. |
| `src/claire/federation/knowledge/grants.py` | The grants that this server gave (`kf_grants`: `give`, `revoke`, `valid`) and the grants that its accounts got (`kf_received_grants`: `receive`, `receive_revoke`, `capability_for`). No function commits. |
| `src/claire/federation/knowledge/handlers.py` | The handlers of `offer_contribution`, `accept_offer`, `reject_offer`, `grant`, and `revoke`. |
| `src/claire/federation/knowledge/actors.py` | `account_actor_iri`, `local_account_of_actor`, `personal_article` (the `Article` of a document of a personal knowledge base for an `Offer`). |
| `src/claire/federation/knowledge/outbox.py` | `sync` calls `offers.sync`, which puts the answers in the outbox. |
| `src/claire/collaboration/contributions.py` | `submit_to_themes` (the remote targets), `canonical_origin`, `contribute_remote`, `remote_actor_deleted`, and the new keys of `LIMITS`. |
| `src/claire/store/kb_collab.py` | `pending_count_remote`, `count_pending_among`, `decisions_of_remote_actor`, `remote_actor_deleted`; `add_contribution` takes a remote source without a knowledge base actor. |
| `src/claire/collaboration/reputation.py` | `compute_remote`. |
| `src/claire/api/server.py` | `/kb/{number}/remote-collaborators`, `/api/kb/remote-grants`, the remote targets of `/submissions`, and `offer` in the view of a contribution. |
| `src/claire/api/federation_routes.py` | `Delete` of a remote actor through `POST /inbox-events`; the mode is not a condition (Section 13.7). |
| `src/claire/store/db.py` | `_migrate_v32_offers_and_grants`, schema version 32. |
| `sidecar/fedify/src/sidecar.ts`, `knowledge.ts`, `internal_api.ts` | The account actor as a signer; no mode condition (Section 13.6). |
| `src/bible/static/js/console_kb.js`, `kb_dialogs.js`, `console_admin.js` | The WebUI (Section 13.5). |

### 13.2 Flow

1. An account of A contributes documents of its personal knowledge base to a theme of A (`POST /submissions`). While the switch `federation` is on and A has a canonical origin, each shared knowledge base of another server that is a member (not excluded) of the theme of an item is a target. For each one, `offers.send` puts an `Offer` in `kf_outbox` (signer: the account actor) and a row `pending` in `kf_outbound_offers`, or records the target as `skipped` with the reason. The server of the target becomes a known server (`local_contribution`).
2. The sidecar of A delivers the `Offer` to the inbox of the knowledge base actor of B. The sidecar of B gives it to `POST /knowledge-activities`; the journal makes A a known server of B (`remote_contribution`), and the handler `offer_contribution` makes the contribution (Section 13.4) and a row `pending` in `kf_inbound_offers`.
3. A reviewer of B decides with the routes of a local contribution (`PATCH /kb/{number}/contributions/{contribution}`). The next `outbox.sync` (the sidecar asks for tasks) puts the answer in the outbox: `Accept` with `result` (the IRI of the document of the contribution on B, `{actor}/documents/{contribution}`) after an approval, or `Reject` with the reason code after a rejection or a removal before the approval. The knowledge base actor signs it.
4. The handler `accept_offer` or `reject_offer` of A sets the decision on the row of the offer. `GET /submissions` and `GET /submissions/{number}` show it. A later `Delete` of that document by the knowledge base actor sets the state `removed` (only when A follows that actor, Section 12.4).
5. A moderator of B gives the role collaborator to the account actor of A (`POST /kb/{number}/remote-collaborators`): a row of `kf_grants` and a `Grant`. A stores it in `kf_received_grants`. Each later `Offer` of the account to that knowledge base has the grant IRI as `capability`. B checks it (`grants.valid`); with a grant in effect and a reputation on B that is not `low`, the contribution needs no approval. `DELETE /kb/{number}/remote-collaborators/{grant}` ends it at once and sends `Revoke`; the next `Offer` needs approval again, also when it still carries the old capability.

### 13.3 Tables (schema version 32)

The step `_migrate_v32_offers_and_grants` follows the rule of v29: it makes a table that is not there, and a table of another form becomes `<name>_retired_v32`.

| Table | Rows |
| :--- | :--- |
| `kf_outbound_offers` | One row for each remote target of a contribution of an account of this server: `offer_iri` (null for a skipped target), `account_id` (null after a purge with records kept), `actor_iri`, `kb_iri`, `domain`, `source_kb_id`, `source_document_id`, `submission_item_id`, `capability_iri`, `automatic` (schema version 34, Section 15.3), `state` (`pending`, `accepted`, `rejected`, `removed`, `skipped`), `result_iri`, `reason_code`, `created_at`, `decided_at`. |
| `kf_inbound_offers` | One row for each `Offer` that a knowledge base actor of this server got and that the handler did not refuse: `offer_iri`, `actor_iri`, `domain`, `kb_number`, `contribution_public_id` (null for a `Reject` at once), `capability_iri`, `grant_valid` (the grant was in effect when the offer came), `state` (`pending`, `accepted`, `rejected`, `closed`), `reason_code` (the reason code on the wire), `detail` (the code of this server for a `Reject` at once), `received_at`, `answered_at`. |
| `kf_grants` | The grants that this server gave: `grant_iri`, `kb_number`, `actor_iri`, `domain`, `granted_by_account_id`, `published_at`, `end_time` (`published_at` + 183 days), `revoked_at`, `revoked_by_account_id`. |
| `kf_received_grants` | The grants that accounts of this server got: `grant_iri`, `kb_iri`, `domain`, `account_id`, `published_at`, `end_time` (as the other server sent them), `revoked_at`, `received_at`. |

### 13.4 Rules

The sender (`offers.send`), in this sequence; each failed check records the target as `skipped` with the reason, and no offer goes out:

| Check | Reason |
| :--- | :--- |
| The domain of the knowledge base is blocked, or the IRI is not one of another server | `blocked_domain` |
| The account is not a federation actor (the switch `federation`, `active`, a public profile, a USERNAME; FEDERATION.md Section 3) | `not_a_federation_actor` |
| An offer of the same document to the same knowledge base waits or was accepted | `contribution_duplicate` |
| No grant in its term, and the offers of the account to that knowledge base that wait are at the pending limit of its reputation on this server | `pending_limit` |
| The document has no extraction that the wire contract can have | `document_not_extracted` |
| The limits of one document (KS2: content 512 KiB, title 500 characters, 500 entities, 2000 relations) | `document_too_large`, `title_too_long`, `extraction_too_large` |

The receiver (handler `offer_contribution`). The journal checked before: the switch, the domain block, the wire contract, and that the activity and its actor are on the server of the signer.

| Check | Result |
| :--- | :--- |
| `target` is not a shared knowledge base of this server | Refused `knowledge_base_not_found` (no row, no answer) |
| The inbox is the one of another actor | Refused `recipient_mismatch` |
| The document is a copy (`origin`), is not its own root, or is not on the server of the actor | Refused `not_own_document` |
| The limits of one document (KS2) | Refused `document_too_large`, `title_too_long`, `extraction_too_large` |
| The offers of the registrable domain in the last 24 hours are at `remote_domain_daily_offers` (KS2) | Refused `daily_limit` |
| The knowledge base is archived | `Reject` at once, reason `other` (`detail` `kb_archived`) |
| No grant in effect (or the reputation is `low`), and the pending contributions of the actor in the knowledge base are at the lower value of `remote_account_pending_limit` and the limit of its reputation (KS3) | `Reject` at once, reason `other` (`pending_limit`) |
| The same, and the pending contributions of all actors of the registrable domain are at `remote_domain_pending_limit` (KS3) | `Reject` at once, reason `other` (`domain_pending_limit`) |
| The knowledge base has a contribution of the same document (`origin` IRI, `canonical_url`, or `content_hash`) or removed it | `Reject` at once, reason `duplicate` (`contribution_duplicate`, `contribution_tombstoned`) |
| Else, with a grant in effect and a reputation that is not `low` | Contribution `merging` with the origin `direct`, `approved_at` set, no approving account; the merge job runs after the journal committed; then `Accept` |
| Else | Contribution `pending` with the origin `bulk` and a hidden copy; no graph until a reviewer approves |

- A contribution of an offer has no contributor account, no local source, and no local root. `remote_credit_iri` is the account actor; `remote_source_iri` and `remote_root_iri` are the document on the other server; `remote_source_kb_iri` is null, because a personal knowledge base is not an actor (KD3).
- The HTML of the document goes through `sanitize_remote_html` (`api/usercontent.py`) in `copy_remote_document`, as each copy of another server (KS2, Section 14).
- A refused activity makes no row and gets no answer: the sender sees `pending` until it sends again. A `Reject` at once is `processed` in the journal.
- The answer goes out only once: a row that is not `pending` does not change. The activity IRIs are `{kb actor}#accepts/{digest of the offer IRI}` and `{kb actor}#rejects/{digest}`; the pair of activity IRI and recipient is unique in the outbox.
- A contribution that is gone without a decision (a pending contribution of a deleted actor), or whose knowledge base is gone, closes its row (`closed`) without an answer.
- The reputation of a remote account (`reputation.compute_remote`) uses only the decisions of the reviewers of this server on its offers, with the rules of a local account (180 days, the counted reason codes, one third for each knowledge base, at least 10 decisions); its fiction share is 0. An approval without a reviewer (a grant) is no decision. The review queue orders the offers by this level.

Grants:

- Activity IRI and capability: `{kb actor}#grants/{random}`. `Revoke`: `{grant IRI}/revoke`. The `Grant` has `published` and `endTime` (183 days later).
- `grants.valid` is the check of each use (KS8): a grant of this server, for this knowledge base and this actor, `published_at <= now < end_time`, not revoked. A capability that is not in effect is no reason for a refusal: the offer waits for approval.
- A new grant for an actor that has one in effect revokes the old one and sends its `Revoke` (a renewal).
- A grant gives no membership row and no other capability (design Section 12.2 rules 4 and 5).
- On the receiving side, `grant` needs a target that is a federation actor of this server now (refusal `account_not_found`) and the inbox of that actor or the shared inbox (`recipient_mismatch`). `revoke` needs the actor that gave the grant (`grant_not_found`). The other server decides each use; the stored state only selects the capability of an `Offer` and the direct targets of the WebUI.
- Limits of `kf_received_grants` (a remote server fills it): at most 50 rows of one account from one registrable domain (refusal `grant_limit`); a grant that ended or was revoked more than 30 days ago is deleted at the next `Grant`, and so is a grant of the same knowledge base actor for the same account that is not in effect.
- Limits of `kf_inbound_offers`: the daily limit above; a row without a contribution (a `Reject` at once, or `closed`) is deleted 30 days after its answer, at the next row.

`Delete` of a remote account actor (design Section 12.1 rule 4, CT7, CT19): the sidecar gives it to `POST /inbox-events` (kind `delete_actor`) as before. Claire then deletes the pending contributions of the actor with their hidden copies, keeps each other contribution without the credit (`remote_credit_iri = NULL`: the views show "deleted account"), closes the rows of its offers that wait, and ends its grants without a `Revoke`.

Purge of a local account: its received grants are deleted, and its outbound offers lose the account (or are deleted with the option of the purge confirmation that deletes the records).

Limits (admin settings, `server_settings`, `PUT /api/admin/themes/settings`, the fields in `/moderation/themes`):

| Key | Default | Meaning |
| :--- | :--- | :--- |
| `remote_account_pending_limit` | 20 | Pending contributions of one remote account in one knowledge base (the lower value of this and the limit of its reputation applies) |
| `remote_domain_pending_limit` | 200 | Pending contributions of all accounts of one registrable domain on this server |
| `remote_domain_daily_offers` | 500 | Offers of one registrable domain in 24 hours |

The keys of Section 14 are in the same table of settings and the same route.

### 13.5 Public Routes and WebUI

Routes ([`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document):

| Route | `ROUTE_POLICY` | Result |
| :--- | :--- | :--- |
| `POST /submissions` | `_any("write", "none")` | New key `remote_knowledge_base` (the record ID of a remote knowledge base; not with `theme`): 403 `grant_required` without a grant in its term, 404 `knowledge_base_not_found`, 400 `remote_knowledge_base_invalid` |
| `GET /submissions`, `GET /submissions/{number}` | `_any("read", "none")` | A remote target: `{"knowledge_base": null, "label", "remote": true, "remote_id", "server", "url", "actor", "state", "approved", "reason", "result", "with_grant", "delivery", "decided_at"}` |
| `GET`, `HEAD`, `POST /kb/{number}/remote-collaborators` | `_namespaced("owner", "people")` | The grants of the knowledge base; `POST {"actor"}` gives one (a recent sign-in in a session) |
| `DELETE /kb/{number}/remote-collaborators/{grant}` | `_namespaced("owner", "people")` | Revokes one grant (a recent sign-in in a session) |
| `GET`, `HEAD /api/kb/remote-grants` | `_any("read", "none")` | The grants that the requester got |

A contribution view has `offer` (full view): `{"actor", "server", "with_grant", "grant_valid"}` or null; a reviewer gets `contributor_reputation` of a remote account.

WebUI:

- `/settings/kb`, tab "Submissions": a remote target shows its label with a link to its page on the other server, the tag of the server, "As a collaborator" for an offer with a grant, the state (`kb.state.accepted` is new), the reason, and the delivery state when the `Offer` is not delivered. A remote target has no withdrawal. Tab "Memberships": the card "Collaborator on other servers" lists the received grants (only when there is one).
- Dialog "Contribute directly" (`kb_dialogs.js`, `openNoThemePicker`): below the local list, the shared knowledge bases of other servers with a grant in effect and a record on this server; a pick sends `POST /submissions` with `remote_knowledge_base` and shows the result table in the dialog. The dialog "Contribute to a theme" needs no change: the result table shows the remote targets with their server.
- `/moderation/kb/{number}`, tab "Review queue": a remote contributor is the actor IRI, with the tag of its server; the detail has "Server" and "Grant of the role collaborator" (in effect when the offer came, not in effect, or none), and for a moderator the button "Give the role collaborator". Tab "Members and roles" (a moderator or the admin): the card "Collaborators of other servers" with the field for the actor address, the list of grants with state and end time, and the actions "Renew" (or "Give the role collaborator" for a grant that ended) and "Revoke". The moderation log has the actions `remote_collaborator_grant` and `remote_collaborator_revoke`.
- `/moderation/themes`: a number field for each key of `contributions.LIMITS`, the limits of Sections 13.4 and 14 included.

### 13.6 Internal Contract and Sidecar

- Contract version 1.11. `signer` of a task of `GET /knowledge-outbox-tasks` can be `account:{public ID}`: the account actor signs (the `Offer`). The other shapes did not change. The internal routes of Section 12.5 serve the new activities without a change: `Accept`, `Reject`, `Grant`, and `Revoke` have the knowledge base actor as signer.
- The sidecar (`Sidecar.#knowledgeDeps`, `signingKey`) takes the keys of an account actor from `#keyPairs` (Claire gives keys only for an account that is an actor now; else the task ends `rejected` with `no_actor_key`) and the key ID `{actor}#main-key`. Inbound `Offer`, `Accept`, `Reject`, `Grant`, and `Revoke` go to the journal without a change, as each activity with the Claire context (Section 10.6); `Delete` of an account actor has no Claire context and goes to the inbox listener of Fedify, as before.
- `deno.json` and `deno.lock` did not change.

### 13.7 Single Mode (design Section 15 rules 5 and 6)

- The mode is not a condition of the internal routes (`federation_routes._federation_active`), of `actors.account_actor_iri`, of the name change `Update(Person)`, of the purge `Delete`, and of the sidecar (each path and each loop): while the switch `federation` is on, the server actor, the account actor of the admin (with `profile_public` and a USERNAME), and the knowledge federation work in single mode. Since 2026-11-19 the account routes `/api/federation/*` and `/render/remote/{item_id}` are in both modes too ([`FEDERATION.md`](FEDERATION.md) Section 7.5), and `/settings/account` has the USERNAME and the switch `profile_public` in both modes.
- Readiness check GF1 passes in network mode, and in single mode when the admin account is `active` and has a USERNAME.
- `POST /api/admin/server/mode` to `single` no longer refuses while `federation` is on (FEDERATION.md Section 8.3).
- `/admin/federation` is the same in both modes. The note of single mode is removed (2026-11-19).

### 13.8 Choices Made in the Code

1. An account that is not a federation actor sends no `Offer`; its target is `skipped` with `not_a_federation_actor` (design Section 12.1 rule 2: "the account needs a handle"; CT33). The submission and its local targets are made as before.
2. The `id` of the document in an `Offer` is `{account actor}/documents/{document ID}`. It is an identifier only: no route serves it, because a personal knowledge base has no address for other servers (KD3, KS5). The same document gives the same IRI, so the receiver finds a second offer of it. The copy on the receiver has this IRI as `origin` and `claire:root`.
3. A remote target is a theme member of the theme of the item (the same rule as a local target), or the direct choice with a grant. A direct choice without a grant is refused (403 `grant_required`): only a collaborator has `contribute_direct` without a membership, and a remote account cannot be a member (design Section 12.2 rule 4).
4. The pending limit of the sender (shared knowledge base design Section 9.2 rule 5) counts the offers to the knowledge base that wait for an answer; an offer with a grant does not check it.
5. A decision about an offer that needs no reviewer (an archived knowledge base, a duplicate, a tombstone, a pending limit) gets a `Reject` at once, so that the submission page of the sender shows it. The wire has only the reason codes of the vocabulary: an archived knowledge base is `other`, and a pending limit is `limit_reached` (Section 16.4; before, `other`); the local code is in `detail`. A problem of the activity itself (not the own document, a size limit, the daily limit, an unknown knowledge base) is a refusal of the journal without an answer.
6. An approval by a grant has `approved_at` and no `approved_by_account_id`; it is no decision for the reputation (no reviewer decided).
7. The answer is not sent by the review route: `offers.sync` compares the rows with the contributions when the sidecar takes tasks, as `outbox.sync` does for the other activities of a knowledge base actor.
8. There is no automatic check of the knowledge type: the reviewers decide with the reason code `wrong_knowledge_type` (shared knowledge base design Section 9.3 rule 4).
9. A remote contributor cannot withdraw a pending contribution: the vocabulary has no `Undo` of an `Offer`. The submission page of the sender has no withdrawal for a remote target.
10. The grant is given to an actor IRI that the moderator enters (or picks in the review queue). The server does not read the actor document of the target before the `Grant`; the delivery reads it.
11. `GET /api/kb/remote-grants` uses the surface `any`, as `/submissions`, because the WebUI of the contribution dialogs calls it from each namespace.
12. The state `removed` of an outbound offer needs a follow of that knowledge base actor (Section 12.4): an origin removal reaches only its followers.

### 13.9 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_kf4_e2e.py` | The completion criterion with two app objects and two databases (A and B, the mock provider): ben of A contributes to a theme of A that has Cities of B; `Offer` with the account actor as signer; a pending contribution on B with the remote credit; a reviewer of B approves; `Accept` with `result`; the submission page on A shows `accepted`; a direct choice without a grant gets 403; a grant (step-up) and `Grant`; the next contribution is visible without approval; `Revoke` (step-up); an offer that still carries the capability waits for approval; a rejection with a reason code reaches the submission page; the reputation of the remote account on B. The sidecars and the network are simulated by direct calls to the internal routes (the docstring of the file says what is simulated). |
| `tests/test_knowledge_federation_kf4.py` | The migration step v32; the checks of an inbound `Offer` (refusals, `Reject` at once, duplicate); the limits for each remote account, each domain, and each day; answers and grants only from the actor that owns them; the end of a grant; the checks of the sender (no federation actor, duplicate, pending limit, blocked domain); `Delete` of a remote actor; the sanitizer of the HTML of an offer; the limits and the retention of received grants and inbound rows. |
| `sidecar/fedify/tests/knowledge_test.ts` | An `Offer` task with the signer `account:{public ID}` goes to the inbox of the knowledge base actor with the body unchanged and a signature that verifies with the actor document of the account, in single mode; a task of an account without keys ends `no_actor_key`. |
| `sidecar/fedify/tests/sidecar_test.ts`, `outbox_test.ts`, `inbox_test.ts`, `tests/test_federation_routes.py`, `tests/test_federation_readiness.py`, `tests/test_e2e_network_mode_transition.py` | Single mode with the switch on (Section 13.7). |

Not verified: an exchange between two real sidecars over a network; the WebUI parts in a browser (no Playwright spec for them); a delivery to software that is not Claire; the `Delete` of an account of A on B when no account of B follows that account (Fedify sends the `Delete` of an actor to its followers only).

### 13.10 Interfaces for Unit KF5

- `offers.send(conn, account_id=, source_kb_id=, source_document_id=, remote_kb=, submission_item_id=, pending_limit=, origin=, now=)` sends one `Offer` with all checks of the sender; a run of the automatic rule can call it with `submission_item_id=None`. Unit KF5 implemented the rest (Section 15): the parameter `automatic`, the field `claire:automatic`, the check of `access.auto_contribution_accepted` on the receiver, and `contribute_remote(automatic=True)` with the origin `auto`.
- The limits of the receiver (Section 13.4) apply to each offer; design Section 12.3 rule 3 needs no other limit.
- The candidates of a remote target use `remote_kbs` records with a signature (`theme_judgement.remote_signature`).

## 14. Limits and Checks of the Security Review

Design: Section 14 (KS1 to KS5 and the limits for each object, each domain, and each day), Sections 6, 9 to 13. Schema version 33. Tests: `tests/test_knowledge_federation_security.py`, and in `sidecar/fedify/tests/knowledge_test.ts` the tests of the time limits, the address check of a delivery, and `origin_removed`.

### 14.1 Admin Settings

`server_settings`, in `contributions.LIMITS` with the limits of Section 13.4; `GET /api/admin/themes` gives them and `PUT /api/admin/themes/settings` changes them (an integer of 1 or more; `remote_revision_min_seconds` 0 or more). A "registrable domain" is the public suffix and one label of the host (`access.quota_domain`; the public suffix list of the package `publicsuffixlist`, without a download). A host without one (an IP address, a top-level domain that the list does not have) is its own key. Thus the subdomains of one domain share each limit for each domain.

| Key | Default | Meaning | Result over the limit |
| :--- | :--- | :--- | :--- |
| `remote_domain_daily_activities` | 10000 | Activities of one registrable domain in 24 hours (rows of the inbox journal) | No row: `rejected` `domain_quota` |
| `remote_domain_daily_bytes` | 104857600 (100 MiB) | Bytes of those activities in 24 hours | No row: `rejected` `domain_quota` |
| `kb_followers_limit` | 1000 | Followers of one knowledge base actor | `follow` refused `follower_limit` |
| `remote_domain_followers_limit` | 1000 | Follows by the server actors of one registrable domain, over all knowledge base actors | `follow` refused `domain_follower_limit` |
| `remote_revision_min_seconds` | 300 | Minimum time between two revisions of one copy from its origin | `update_document` refused `revision_too_soon` |
| `remote_domain_daily_revisions` | 200 | Revisions of copies from the knowledge base actors of one registrable domain in 24 hours | `update_document` refused `revision_budget` |

The limits of Section 13.4 (`remote_domain_pending_limit`, `remote_domain_daily_offers`) and the limit of received grants (50) are also for each registrable domain.

### 14.2 Rules

Inbox journal and known servers:

- The `id` of an activity must be on the origin (scheme, host, and port) of the signer. The `actor` must be the signer, or the signer must be the server actor (`/ap/actors/server`) of the origin of the actor. The handlers use the `actor` of the row (`access.iri_origin`, `access.server_actor_of`).
- A `refused` row keeps no payload (`{}`). A row does not make its server known: the handler of `follow` does that when it stores the follower, and the handler of `offer_contribution` when the offer makes a contribution.
- `journal.process` takes the write lock (`BEGIN IMMEDIATE`) before it reads the state of the row: two deliveries of one activity at the same time do not both run the handler.

Records and lists of other servers (KS2):

- A record of a remote knowledge base keeps only the keys of the contract (`remote_kbs.RECORD_KEYS`). Refusals of an item, with the codes of Section 11.3: `iri_too_long` (an IRI over 2048 characters), `signature_too_large` (a type of a row of the signature over 64 characters or a name over 200), `record_too_large` (the stored JSON over 128 KiB). `remote_kbs.list_all` (the root page `GET /explore/federation`, the judgement, the sources of a curation) does not read `payload`.
- The list of the documents of a remote knowledge base (`kf_remote_documents`) has at most 1000 rows for each knowledge base, also after reads that failed: a new row takes the place of the oldest row that the current read did not see, or is not stored. An item needs a `claire:contentHash` of 64 lowercase hexadecimal characters, a `published` (and an `updated`) that is an ISO 8601 time with a time zone, and IRIs of at most 2048 characters.
- A count of the wire form is at most 2^53 (`vocabulary.MAX_COUNT`); an IRI is at most 2048 characters (`vocabulary.MAX_IRI_LENGTH`); `sameAs` has at most 16 identifiers of at most 100 characters. A value over a limit is `invalid_field` or `invalid_iri`. Two IRIs are on the same server only when their scheme and authority are equal (`claire:documents` of `http` for an actor of `https` is `origin_mismatch`).

Copies, revisions, and entities:

- Each copy of another server (a curation, an offer, a revision) stores the HTML after `sanitize_remote_html` without the media proxy: no element loads a resource of the other server, and a link can be `http`, `https`, or `mailto` only.
- The content hash of a copy is the one that this server computes: the SHA-256 of the stored HTML and of the extraction that came (`contributions.remote_content_hash`). The duplicate and tombstone checks of a first copy use it; the hash that the other server declares is not used.
- The credit (`attributedTo`) and the root (`claire:root`) of a curation are kept only when they are on the origin of the knowledge base actor of the source; else the copy has no credit, and its root is the document itself. The credit and the root of an offer are those of the account actor (Section 13.4).
- The limits of one document (Section 12.4) also have limits for each field: `url` 2048 characters (`document_too_large`); summary 10000 characters, at most 200 key claims of 2000 characters, a name, a normal name, an alias, a relation end 300 characters, a type 100 characters, at most 50 aliases and 50 observations of 2000 characters for each entity (`extraction_too_large`).
- A revision (`update_document`, and a read again of Section 14.3): refused `invalid_time` when its time (`updated`, else `published`) is more than one day after now; then the limits of Section 14.1. Each copy keeps its first `canonical_url`, credit, and root. The time of the version of the origin is stored for each copy (`kb_contributions.remote_updated_at`): a copy with a later time keeps its version, and the handler refuses an `Update` that changes no copy for that reason (`stale_revision`). The wire time has whole seconds, so a version of the same second is applied, and the content hash decides if it is new. A new version that has the address or the content hash of another document of the knowledge base, or a tombstone, closes the revision of that copy (no new copy).
- The journal forgets a processed row after 30 days, so the same activity can come again. A revision cannot apply an old state again (`remote_updated_at`). The other kinds: a `Delete` of a document changes nothing at a copy with a mark of the origin; an answer to an offer changes only a `pending` offer; a `Revoke` and an `Undo` change nothing a second time; a `Follow` makes a follower again (a server can follow again); an `Update` of a record that is not newer than the stored record is refused (`stale_update`, Section 16.3). A third server cannot send an old activity again: the sidecar verifies the time of the HTTP signature (one hour, the default of Fedify).
- Authority identifiers (design Section 10.4 rule 2, KS4): only `wd:Q…` (Wikidata), `isbn:` with 10 or 13 digits, and `orcid:` with the form of ORCID, at most 10 for each entity, are stored. An identifier makes a match only with an entity of the same type whose name or alias agrees with the name or an alias of the received entity. An entity gets the identifiers only when it matched by them or is new, not after a match by the name alone. No identifier goes to another server.
- The merge jobs of `POST /knowledge-activities` and `POST /knowledge-remote-reads` run after the answer to the sidecar (a background task of the request).

Followers, keys, and the outbox:

- A domain block (`POST /api/admin/federation/domain-blocks`) deletes, in its transaction, the records of the domain (Section 11.3), the lists of their documents, and the followers on that domain; `outbox.sync` deletes the followers of a blocked domain too. After the block ends, that server gets activities only after a new `Follow`.
- The keys of the actor of a deleted knowledge base are deleted from `auth.db` when no task of that actor waits (`GET /knowledge-outbox-tasks` checks it after the lease).
- The entity and relation counts of a record (`claire:entityCount`, `claire:relationCount`) do not count the entities that only hidden documents have, and the relations of such entities (`collections.visible_graph_counts`).

### 14.3 Removal at the Origin Before the Follow

After the `Accept` of a follow of this server (`accept_follow`), each document of that knowledge base actor that this server holds an open copy of gets a read (`kf_remote_reads`, `kind` `recheck`). The sidecar reads it as a read for a curation. Its answer:

| Answer | Effect |
| :--- | :--- |
| `origin_removed` (a 404 or 410 of the origin) | `contributions.origin_removed` with the reason code `other`: the copy is hidden and waits in the review queue |
| The document | The rules of a revision (Section 14.2); an older version changes nothing |
| Another error | The read is `failed`; nothing changes |

A read for a curation that gets `origin_removed` is `failed` with that code. A hide at the origin sends nothing (Section 12.4); the design says only a removal sends `Delete`.

### 14.4 Admin Route to Remove a Known Server

`DELETE /api/admin/federation/known-servers` with `{"domain"}` (`ROUTE_POLICY` `_system("admin", "none")`; the same origin, a session with a recent sign-in, `admin_audit` `federation.known_server_remove` with the counts). Answer 200 `{"removed": true, "domain", "deleted": {"knowledge_bases", "listed_documents", "followers", "outbox_tasks", "journal_payloads"}}`; 404 `known_server_not_found`; 400 `invalid_request`, `invalid_domain`. Details: [`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document.

| Deleted | Stays |
| :--- | :--- |
| The known server; the records of its shared knowledge bases with their theme memberships, the lists of their documents, the read state, and the reads that ended | The copies of its documents, with their origin (design Section 10.1) |
| Its server actors as followers of the knowledge base actors of this server | The follows of this server: `outbox.sync` ends the follow of a record that is gone and keeps the follow of an actor that this server holds copies from |
| The tasks for that server that wait: `refused` with `server_removed` | The rows of the inbox journal until their retention ends; a `failed` row keeps its payload for a replay |
| The payload of each `processed` and `refused` row of the inbox journal of that server | A domain block |

The server becomes known again at its next contact. The tab "Known servers" of `/admin/federation` has the action "Remove" with a confirmation that lists what is deleted and what stays (`console_admin.js`).

### 14.5 Sidecar

- Each request to another server of the knowledge federation has a timeout of 15 seconds (`REQUEST_TIMEOUT_MS`: the GET requests of the document loader get a signal, and the POST of a delivery gets one too).
- Each task has a time limit of 60 seconds (`TASK_SECONDS`, `withDeadline`): the read of one server (NodeInfo and the server actor; result `read_timeout`), one delivery (result `retry` with `delivery_timeout`), one read for a curation (`read_timeout`). The read of a `/kb` collection has its limit of 240 seconds also for a request that waits (`read_timeout`). One other server cannot hold the loop of `main.ts`.
- The POST of a delivery is refused for an inbox at an address that is not public (`rejected` with `inbox_private_address`; `validatePublicUrl` of `@fedify/vocab-runtime`, not while the development switch `allow_private_addresses` is on). The document loader has this check for each GET. The POST pins the address that it checked (Section 16.5).
- The read of a `/kb` collection needs the scheme of this server (`collection_invalid`), as a read for a curation does.
- A 404 or 410 of the origin in a read for a curation gives `origin_removed` (Section 14.3).
- `deno.json` and `deno.lock` did not change.

## 15. Unit KF5: Automatic Contribution Across Servers

Design: Section 12.3, with Sections 6.3, 12.1, 12.2, and 14; [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) Sections 9.3, 9.9, and 9.10. The local automatic contribution is in [`THEMES.md`](THEMES.md) Section 4.12.

### 15.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/collaboration/auto_contribution.py` | `remote_state`, `remote_targets`, `remote_candidates`; `plan`, `run_account`, and `run_all` with the parameter `origin` and the remote targets. |
| `src/claire/federation/knowledge/vocabulary.py` | `AUTOMATIC`, `ACCEPTS_AUTOMATIC`, `AUTOMATIC_NOT_ACCEPTED`, `REJECT_REASON_CODES`; `build_contribution(automatic=)`; the field `accepts_automatic` of `build_knowledge_base`. |
| `src/claire/federation/knowledge/offers.py` | The column `automatic`, `send(automatic=)`, `automatic_of_account`, `automatic_offered`, `RETRY_REASONS`, `ensure_automatic_column`; `record_decision` gives the row. |
| `src/claire/federation/knowledge/remote_kbs.py` | `claire:acceptsAutomatic` in `RECORD_KEYS`, `accepts_automatic`, `note_automatic_not_accepted`. |
| `src/claire/federation/knowledge/handlers.py` | `offer_contribution` and `reject_offer` (Sections 15.4 and 15.5). |
| `src/claire/federation/knowledge/collections.py`, `actors.py` | `claire:acceptsAutomatic` in the record of each shared knowledge base, and in `record_digest`. |
| `src/claire/federation/knowledge/grants.py` | `kb_iris_in_effect`. |
| `src/claire/collaboration/contributions.py` | `contribute_remote(automatic=)`. |
| `src/claire/api/server.py`, `cli.py`, `ingest/service.py`, `ingest/pipeline.py` | The canonical origin for each run; the remote targets of the dry run; the automatic offers in `GET /api/contributions`. |
| `src/claire/store/db.py` | `_migrate_v34_automatic_offers`, schema version 34. |
| `src/bible/static/js/kb_dialogs.js`, `console_kb.js` | The WebUI (Section 15.6). |

### 15.2 Vocabulary

The context `v1` was not served yet, so the new terms are in `v1` (design Section 9.1 rule 2 applies after the first server serves it). `src/bible/static/ns/v1.jsonld`, `src/bible/static/ns/index.html`, and the copy of the sidecar were written again from the module.

| Term or value | Where | Rule |
| :--- | :--- | :--- |
| `claire:automatic` | `claire:Contribution` | Optional boolean. `true`: the automatic rule of the account selected the document. |
| `claire:acceptsAutomatic` | `claire:KnowledgeBase` (the `/kb` collection, the actor document, `Update` of the record) | Optional boolean. The state of `access.auto_contribution_accepted` of the server now. |
| `automatic_not_accepted` | `claire:reasonCode` of a `Reject` only | The server accepts no automatic contributions now. It is not a reason code of the reviewers (`vocabulary.REASON_CODES` stays the list of `contributions.REASON_CODES`); a `Delete` with it is `invalid_reason_code`. |
| `limit_reached` | `claire:reasonCode` of a `Reject` only | Added after unit KF5 (Section 16.4): a pending limit of the receiver. The same rules as `automatic_not_accepted`. |

The fixtures of `objects/`, `activities/`, `invalid/`, and `internal/` were written again; `invalid/` has three new files (`knowledge_base_accepts_automatic_not_a_boolean`, `offer_automatic_not_a_boolean`, `delete_document_with_the_reason_of_a_reject`).

### 15.3 Table (schema version 34)

`kf_outbound_offers` has the new column `automatic` (`INTEGER NOT NULL DEFAULT 0`): 1 for an offer that the automatic rule sent (it has no `submission_item_id`). The step `_migrate_v34_automatic_offers` adds the column to a table of version 32 or 33 before the step v32 compares the columns, so the table is not retired; an old row gets 0. A new table has the column.

### 15.4 Sender

Conditions (`auto_contribution.remote_state` and `run_account`): the switch `kb_auto_contribution` of this server (design Section 12.3 rule 1), the switch `federation`, the canonical origin of this server (`contributions.canonical_origin`; the same value for a request and for a run in the background), and an account that is a federation actor. Else the rule has only local targets.

| Item | Rule |
| :--- | :--- |
| Targets (`remote_targets`) | `memberships`: the remote knowledge bases that gave the account a grant in its term (`grants.kb_iris_in_effect`) and that have a record here. `themes`: the remote members (not excluded) of the themes of the rule (a merged theme is its target). `mutual`: none. A record of a blocked domain is no target. |
| Score (`remote_candidates`) | The signature of the record (`remote_kbs.signature_rows`, design Section 12.3 rule 2); else the signature of a local theme that has the record as a member. No seed entities: they do not come to this server. The threshold is `auto_match_threshold`. |
| Not candidates | The exclusions of a local target (hidden, "never contribute", pasted text, upload without `include_uploads`, no extraction), and each document with a row of `kf_outbound_offers` of the account for that knowledge base in any state, also from a submission (`offers.automatic_offered`). A row that was skipped or rejected with a reason of `offers.RETRY_REASONS` (`not_a_federation_actor`, `blocked_domain`, `pending_limit`, `knowledge_base_not_found`, `automatic_not_accepted`) does not count. The duplicate and tombstone checks are on the receiver. |
| Mode | `approved` with a grant in its term (the receiver decides at the end: the reputation of the account there); `pending` without one; `closed` when the record says that the server accepts no automatic contributions (`remote_kbs.accepts_automatic` is False). A record without the key (a read before KF5) is not `closed`: the receiver decides. |
| Limits | `auto_run_documents` for each run and target; the reputation level of the account on this server (`limited` fewer documents, `low` no run); for `pending`, the pending limit of the level minus the offers to that knowledge base that wait (`offers.pending_outbound`). |
| Run | One `Offer` with `claire:automatic` for each candidate: `offers.send(automatic=True, submission_item_id=None)` with all checks of Section 13.4, and first `automatic_not_accepted` for a `closed` record. Each offer is its own transaction. A `closed` target gets no row. |
| When | The events of the local rule (THEMES.md Section 4.12). A new remote knowledge base: the read of its `/kb` collection makes the judgement due (`run_is_due`, Section 11.5); the judgement run puts it in a theme, and the rules run after the judgement run. |

`Reject` with `automatic_not_accepted` of an automatic offer (handler `reject_offer`): the row is `rejected` with that reason, and each record of the domain of the actor gets `claire:acceptsAutomatic` false (`remote_kbs.note_automatic_not_accepted`; `changed_at` does not change, so no judgement run is due). The next read of the `/kb` collection, or an `Update` of a record from a followed actor, gives the state of that server again. The same reason on an offer that was not automatic changes no record.

### 15.5 Receiver

The handler `offer_contribution` (Section 13.4), with one new check after the daily limit and before the archived state: an offer whose `claire:Contribution` has `claire:automatic` `true` while `access.auto_contribution_accepted` is false (the switch `federation` or `kb_auto_contribution` is off) gets a `Reject` at once with `automatic_not_accepted` (`detail` `kb_auto_contribution_off`); the journal row is `processed`, and no contribution is made. Else the rules of Section 13.4 apply without a change (the pending limits for each remote account and each domain, design Section 12.3 rule 3; the grant and the reputation), and the contribution has the origin `auto` (`contribute_remote(automatic=True)`): `pending` with a hidden copy, or `merging` without approval with a grant in effect and a reputation that is not `low`. The review queue shows the origin `auto` (shared knowledge base design Section 9.3 rule 2).

The record of each shared knowledge base of this server has `claire:acceptsAutomatic`. When the admin changes a switch, the digest of the record changes: `outbox.sync` sends `Update` of each record to its followers. After the change to version 34 each record sends one `Update`, because the digest has a new part.

### 15.6 Routes and WebUI

No new route. Changes ([`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md) and the OpenAPI document):

| Route | Change |
| :--- | :--- |
| `POST /api/auto-rule/dry-run` | New key `remote_state` (`on`, `no_origin`, `federation_off`, `not_a_federation_actor`). Each target has `remote`. A remote target: `{"knowledge_base": null, "label", "remote": true, "remote_id", "server", "url", "actor", "mode", "documents"}`; `mode` can be `closed` (then `documents` is empty). |
| `GET /api/contributions` | Without `origin` and with `origin=auto`: also the automatic offers of the account, newest first with the other items: `{"knowledge_base": null, "label", "number": null, "document", "title", "origin": "auto", "state", "approved", "reason", "contributed_at", "remote_target": {"remote_id", "server", "url", "actor", "result", "with_grant", "delivery", "decided_at"}}`. `state` is the state of the outbound row (`pending`, `accepted`, `rejected`, `removed`, `skipped`). |
| `GET /submissions`, `GET /submissions/{number}` | A remote target has `automatic` (false: an offer of a submission). An automatic offer has no submission item, so it is not in these views. |
| `PUT /api/auto-rule`, the admin routes | No change of the form; a run has remote targets. |

WebUI: the dry run dialog (`kb_dialogs.js`) shows a remote target with its label, its server, and a link to its page; the mode `closed` with a note; and a note when `remote_state` is not `on`. `/settings/kb`, tab "Automatic rule": the list of automatic contributions shows a remote target with its server, its state, the reason (`automatic_not_accepted` has its catalog text), and the delivery state; a remote target has no withdrawal. The same rows are in the list "My contributions" of the tab "Submissions". New catalog keys: `kb.dlg.mode_closed`, `kb.dlg.mode_closed_hint`, `kb.dlg.dry_run_remote_no_origin`, `kb.dlg.dry_run_remote_federation_off`, `kb.dlg.dry_run_remote_not_a_federation_actor`, `kb.reason.automatic_not_accepted`, `console.js.kb.auto_remote_hint`.

The sidecar needs no change: an automatic `Offer` is an `Offer` of the account actor (Section 13.6). `deno.json` and `deno.lock` did not change.

### 15.7 Choices Made in the Code

1. The receiver must know that an offer is automatic (design Section 12.3 rule 1: the receiver accepts only with its switch on): the field `claire:automatic` of `claire:Contribution`.
2. The sender learns the switch of the receiver from the record (`claire:acceptsAutomatic`), so that the dry run shows a `closed` target and no offer goes there. The receiver still decides each offer: the record can be one day old.
3. A refusal for the switch has its own reason on the wire (`automatic_not_accepted`), so that the sender shows it and can send the document again later. The reason codes of the reviewers do not change.
4. Remote targets of `memberships`: a remote account is never a member (design Section 12.2 rule 4); a grant in its term is its role collaborator, and the shared knowledge base design Section 9.9 rule 1 counts a collaborator in `memberships`.
5. `mutual` has no remote target. Design Section 12.2 rule 6 says that a mutual collaboration between accounts of different servers uses the same grants; the request and the acceptance of such a collaboration between two servers have no activity in design Section 9.3. Thus the effect that exists across servers is the grant (`memberships`).
6. A remote target has no withdrawal (the vocabulary has no `Undo` of an `Offer`, Section 13.8 item 9). The account can pause the rule and set "never contribute" (shared knowledge base design Section 9.9 rule 6).
7. The duplicate and tombstone checks of a remote target are on the receiver (`Reject` with `duplicate`); the sender does not have the documents of the other server. A rejected offer (any reason but `automatic_not_accepted`) keeps the document from that knowledge base (design Section 9.9 rule 2). A `Reject` at once for a pending limit of the receiver has the reason `limit_reached` (Section 16.4): as `automatic_not_accepted`, it does not keep the document out.
8. The candidates of a remote target have no seed entities: the record has none. A remote knowledge base without a signature has a score only through a local theme.
9. A grant that comes (`Grant`) starts no run; the next event of the local rule (a new document, a judgement run) sends the documents.
10. The MCP tools `kb_candidates` and `kb_submit` have the targets of other servers (Section 16.2).

### 15.8 Tests

| File | What it examines |
| :--- | :--- |
| `tests/test_knowledge_federation_kf5_e2e.py` | The completion criterion with two app objects and two databases (A and B, the mock provider): the switch on both servers; on A a theme and the rule of ben; a new shared knowledge base Cities on B; the read of A; the dry run shows Cities with its server and the mode `pending`; a run on A sends one automatic `Offer` for each matching document (not for the document with "never contribute" or of another subject); B makes pending contributions with the origin `auto`; a second run sends nothing; an approval on B reaches the list of A. The case with the switch off on B: the target is `closed` and no offer goes; after the switch is on and a new read, the offers go; an offer that comes while the switch is off again gets `Reject` with `automatic_not_accepted`, which A shows; after the switch is on again, the same documents go. The sidecars and the network are simulated by direct calls to the internal routes (the docstring says what is simulated). |
| `tests/test_knowledge_federation_kf5.py` | The migration step v34; the receiver with the switch off and on and with a grant; the switch in the record and its `Update` to the followers; the `Reject` for the switch and the records of the sender; the sender skips a `closed` record; the remote targets of a rule. |
| `tests/test_knowledge_federation_vocabulary.py` | The new terms in the context and the page, the fixtures, an automatic offer and its `Reject`. |

Not verified: an exchange between two real sidecars over a network; the new WebUI parts with a remote target in a browser (the Playwright specs cover the pages without a remote target).

## 16. Remaining Items After Unit KF5

Design: Sections 8.3 rule 3, 9.2 rule 2, 9.3, 12.3, and 14; [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](../design/SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) Section 9.9 rule 7. No new route and no schema version. Tests: `tests/test_knowledge_federation_remaining.py`, `sidecar/fedify/tests/pinned_fetch_test.ts`.

### 16.1 Recent Activity of the Root Page (design Section 8.3 rule 3)

`src/claire/federation/knowledge/recent.py` (`recent_activity`) makes the recent activity of the root page (`_recent_activity` in `api/server.py`) from three stores, the newest first, at most 20 items:

| `kind` | Source | Keys |
| :--- | :--- | :--- |
| `remote_knowledge_base` | `remote_kbs.list_all` (`first_seen_at`) | `label`, `url`, `knowledge_type` |
| `accepted_contribution` | `offers.recent_accepted`: the rows `accepted` of `kf_outbound_offers`, one for each pair of account and knowledge base (the newest `decided_at`) | `label`, `url` (the record; null without one), `account` |
| `curation` | `kb_collab.recent_remote_curations`: the `visible` copies with the origin `curation` and a remote source whose origin did not remove them, one for each pair of knowledge base and source (the newest approval) | `label`, `url` (the source), `knowledge_base` (`number`, `label`, `url` of the shared knowledge base of this server) |

Privacy: `account` is `{"username", "display_name"}` only for an account that is `active` with a public profile and a USERNAME now, else null; no item has a document or a title (a document of an offer is in a personal knowledge base, KD3, KS5); a domain that a block applies to is not in the list. `public_kb.js` (`recentItemHtml`) shows each kind with the catalog keys `kb.pub.recent_accepted`, `kb.pub.recent_by`, `kb.pub.recent_curation`.

### 16.2 Follow State on the Public Theme Page and MCP Targets of Other Servers

- `GET /themes/{number}/` (`theme_page_route`): `follows` true for the admin and for a reviewer (capability `review`) of a shared knowledge base of this server in the theme; then each remote member has `follow` (`outbox.follow_states`: `pending`, `accepted`, or null) and the answer has `Cache-Control: private, no-store`. Each other reader, also an anonymous one, gets `follows` false: the design gives the follow state to no public page (Section 9.2 rule 2 tells why a server follows, not who sees it). `renderThemeDetail` shows the column "Followed by this server" (`kb.pub.col_follow`, `kb.pub.follow.*`) only with `follows`.
- MCP (`api/mcp_tools.py`): `kb_candidates` takes `theme` (also the remote members of the theme), `kb`, or `remote_knowledge_base` (the record ID). A remote target has `remote` true, `remote_knowledge_base`, `server`, `url`, `mode` (`approved` with a grant in its term, else `pending`; a manual offer is not automatic, so `closed` does not apply) and the documents of `auto_contribution.remote_candidates`. `remote_state` (`auto_contribution.remote_state`) tells why no remote target is in the result. `kb_submit` takes `remote_knowledge_base` (not with `theme`) and calls `contributions.submit_to_themes` as `POST /submissions` does (`grant_required` without a grant); the remote targets of the result come from `offers.outbound_view`. Reading: shared knowledge base design Section 9.9 rule 7 gives the account's agent the candidates "for a knowledge base or a theme", and federation design Section 12.3 rule 2 lets the rule have remote targets; thus the agent sees them.

### 16.3 Time of a Record (`updated`)

- The `claire:KnowledgeBase` of this server has `updated` (whole seconds) when a follower got the record: the time of `kf_kb_actor_state` (`actors.record_updated_at`). `outbox.sync` makes each new time at least one second after the time before, and the `Update` of a record has it. The validator needs `updated` in the object of an `Update` of a record (`missing_field`); in another record it is optional.
- The receiver keeps `updated` in the payload of the record (`remote_kbs.RECORD_KEYS`; no new column). `apply_update` refuses an `Update` whose `updated` is not after the stored one (`stale_update`). A read whose item is older than the stored record keeps the record and marks it seen; another read stores the item. A change of `updated` alone does not change `changed_at`.

### 16.4 Reason `limit_reached`

The pending limits of the receiver for a remote account and for its domain (Section 13.4) give a `Reject` at once with `limit_reached` (`vocabulary.LIMIT_REACHED`, in `REJECT_REASON_CODES`; `detail` keeps `pending_limit` or `domain_pending_limit`). The sender has it in `offers.RETRY_REASONS`: the automatic rule can offer the document again later; the submission page shows `kb.reason.limit_reached`. A `Delete` with it is `invalid_reason_code`. The context `v1` did not change (a reason is a value, not a term); the vocabulary page has the value. The daily limit of a domain stays a refusal of the journal without an answer (KS2: an answer to each offer of a flood is more work for this server). An archived knowledge base stays `other`.

### 16.5 Pinned Address of a Delivery (sidecar)

`sidecar/fedify/src/pinned_fetch.ts` (`pinnedFetch`) is the request function of a delivery while `allow_private_addresses` is off. It resolves the name one time (`node:dns` lookup, all addresses), refuses the request when one address is not public (`isValidPublicIPv4Address`, `isValidPublicIPv6Address` of `@fedify/vocab-runtime`; result `rejected` with `inbox_private_address`), connects to the first address (`Deno.connect`), starts TLS with the name of the URL (`Deno.startTls`: SNI and the check of the certificate use the name), and sends one HTTP/1.1 request with the signed headers, the `Host` of the URL, and `Connection: close`. It reads only the status line (a 1xx answer is skipped). Why not `fetch`: Fedify 2.4.5 pins on Node.js and Bun only, and `Deno.createHttpClient` can send to a fixed TCP address only without TLS. Without the permission `net` for each host (the tests) the global `fetch` sends the request, as `openPublicFetch` of Fedify does; the image has `--allow-net` without a list ([`FEDERATION.md`](FEDERATION.md) Section 9.3). `deno.json` and `deno.lock` did not change.

### 16.6 Public Origin of the IRIs That Leave the Server

`_public_origin` of `api/namespace.py` falls back to the address of the request; the page links keep it. The routes that build IRIs for other servers use `_federation_origin` (`contributions.canonical_origin`: the configured public URL only): `POST`, `DELETE /kb/{number}/remote-collaborators` and `POST /submissions` with `remote_knowledge_base` give 409 `public_origin_missing` without it; a submission to a theme then has the targets of this server only (`submit_to_themes` with `origin` None). The internal routes of the sidecar, the automatic rule, and the MCP tools use the same value. `create_app` stops when the settings give no valid public URL (`WebRuntimeConfig.from_settings`), so a running server always has it; readiness check GF5 fails for the same settings, and GF7 needs the public origin.

## 17. Unit KF6: Readiness Checks GF8 to GF17

Design: Section 14.1, decisions KD10, KD11, KD13 (`PQ14: A`), KD14 (`PQ15: C`), and [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](../design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) decision CD4 (`DQ1: A`). No new database table or schema version.

### 17.1 Files

| File | Content |
| :--- | :--- |
| `src/claire/accounts/federation_readiness.py` | Readiness checks GF1 to GF17, `FederationReadinessItem` (`group`, `level`, `applies`, `code`, `values`), `FederationReadinessReport`, `_system_ssl_context()`, `request_public_origin`, 20-second deadline across request checks |
| `src/claire/store/actor_keys.py` | `load_kb_public_jwks` |
| `src/claire/api/federation_routes.py` | Contract `1.12`, sealed heartbeat models `KnowledgeSelfCheck`, `SignedSelfCheck`, `LoopState`, `HeartbeatLoops` |
| `src/claire/api/accounts_routes/federation.py` | Gate `POST /api/admin/federation` (`failures` filters on `level == "required" and applies and not passed`), heartbeat `knowledge_self_check` and `loops` |
| `src/claire/api/accounts_routes/console_federation_panels.py`, `src/bible/static/js/console_admin.js`, `src/claire/i18n/locales/en.json`, `src/claire/i18n/locales/ko.json` | Two-group readiness table, level column, "Does not apply", loop states |
| `src/claire/cli.py` | `--federation` help (`GF1 to GF17`) and output |
| `src/claire/api/security.py`, `src/claire/api/host_router.py` | Decision CD4 (`DQ1: A`): `development` accepts `https://<name>.test/` and applies `.test` DNS role rules |
| `sidecar/fedify/src/internal_api.ts`, `sidecar/fedify/src/sidecar.ts`, `sidecar/fedify/src/main.ts` | Contract `1.12`, `POST /ap/self-check`, `knowledgeSelfCheck`, `runKnowledgeReads`, `runKnowledgeExchange`, `loops()` |

### 17.2 Checks GF8 to GF17

| ID | Level | Applies | What Claire examines |
| :--- | :--- | :--- | :--- |
| GF8 | Required | Always | `heartbeat.knowledge_self_check.failed_paths` is empty for the 7 paths `/.well-known/nodeinfo`, `/nodeinfo/2.1`, `/ap/actors/server`, `/ap/kb`, `/ap/themes`, `/ap/kb/0`, and `/ap/inbox` |
| GF9 | Required | Always | `heartbeat.knowledge_self_check.signed.result == "ok"` for signed `POST /ap/self-check` through the public origin |
| GF10 | Required | Always | `POST {origin}/ap/inbox` with `GF10_INBOX_BODY_BYTES` (`1040384`) does not return HTTP `413` and has header `X-Claire-Fedify-Server` |
| GF11 | Required | Always | `heartbeat.loops.knowledge_reads` and `knowledge_exchange` have `progress_at` within `300` seconds (`LOOP_MAX_AGE_SECONDS`) |
| GF12 | Required | Always | Every ciphertext row in `actor_keys` (account actors and negative KB IDs) decrypts with `SecretService`; fails show up to 5 actor IDs |
| GF13 | Required | Always | `federation` and `kb_auto_contribution` switches, `contributions.LIMITS`, `theme_match_threshold`, `auto_match_threshold`, `auto_run_documents` are valid; in `production`, the public host is not under `.test`, `.example`, `.invalid`, `.localhost`, or `.local` |
| GF14 | Required | `federation == "on"` | `/.well-known/nodeinfo`, `/nodeinfo/2.1`, and `/ap/actors/server` (`Application`, matching RSA `publicKeyPem` in `auth.db`) |
| GF15 | Required | `federation == "on"` and at least one shared KB | Lowest-number shared KB `/ap/kb/{number}` (`Group` + `claire:KnowledgeBase`, `claire:acceptsAutomatic` vs `kb_auto_contribution`, matching RSA `publicKeyPem` in `auth.db`) |
| GF16 | Required | `federation == "on"` | Unsigned `GET` to `/ap/kb`, `/ap/themes`, and `/ap/kb/{number}/documents` returns HTTP `404` |
| GF17 | Warning | Always | `GET /ns/v1` (200, `application/ld+json`, `Access-Control-Allow-Origin: *`, current context document) and `GET /ns` (200, `text/html`); never sets `report.passed` to false |

HTTP checks (`fetch_metadata`, `request_public_origin`) use `_system_ssl_context()` (default system CA paths plus `SSL_CERT_FILE`), `trust_env=False`, no redirects, and a 5-second per-request timeout; all active request checks (`GF7`, `GF10`, `GF14` to `GF17`) run concurrently under a shared 20-second deadline.

### 17.3 Sidecar Changes (Contract 1.12)

- `POST /ap/self-check` uses an in-memory 2048-bit RSA key pair (`#getSelfCheckKey`), single-use 60-second nonces (`SELF_CHECK_TTL_SECONDS`), exact `Host` validation (`host_changed`), and Fedify HTTP signature verification against `preloadedContexts` (`signature_invalid`). Replays, expired nonces, GET requests, and invalid JSON return `404`.
- `runKnowledgeReads()` and `runKnowledgeExchange()` track `running`, `progress_at`, `error`, and `error_at` in memory; `main.ts` runs an initial pass (waiting up to 5 seconds) before sending the first heartbeat.

### 17.4 Tests

| File | Scope |
| :--- | :--- |
| `tests/test_federation_readiness.py` | GF1 to GF17 pass and failure cases, `SSL_CERT_FILE` loopback TLS, 20-second deadline, `POST /api/admin/federation` gate (`PQ14: A`), CLI `--federation` help and warning exit code |
| `tests/test_federation_routes.py` | Contract 1.12 heartbeat fixture and v1.11 payload rejection |
| `tests/test_api_security.py`, `tests/test_host_roles.py` | Decision CD4 (`.test` HTTPS development origin and DNS role rules) |
| `sidecar/fedify/tests/sidecar_test.ts` | `POST /ap/self-check`, `knowledgeSelfCheck`, `runKnowledgeReads`, `runKnowledgeExchange` |
| `e2e/federation_admin.spec.js` | `/admin/federation` readiness groups, level badges, "Does not apply", warning GF17, and sidecar loop state |

Verified: `pytest` readiness, contract, console, container-mount, and sidecar E2E suites; Deno sidecar suite (`deno test` in `sidecar/fedify/`); Playwright console E2E (`scripts/e2e.sh run e2e/federation_admin.spec.js`); and a two-server Docker Compose devnet (`a.claire.test`, `b.claire.test` with private CA TLS, `dnsmasq`, and Nginx gateway) confirming KA1, KA2, KF6 fault profiles (`GF8`, `GF9`, `GF10`, `GF13`, `GF14`, `GF16`, `GF17`) with recovery, and live server-to-server discovery and `/ap/kb` sync (`KA3` baseline). Remaining acceptance steps: permanent `DE1` to `DE5` devnet commands/tests, full `KA3` KF3 to KF5 real-sidecar scenarios, `KA4`, and `KA5`.
