# Knowledge Federation Implementation Reference

Basis: unit KF0 (2026-11-17). Readers: agents who do the units KF1 to KF5.

Design: [`KNOWLEDGE_FEDERATION_DESIGN.md`](../design/KNOWLEDGE_FEDERATION_DESIGN.md). This document tells what the code has now. The general federation (actors of accounts, publishing, remote reading) is in [`FEDERATION.md`](FEDERATION.md). The local shared knowledge bases are in [`THEMES.md`](THEMES.md) Sections 4.7 to 4.12.

## 1. Scope and State

Only unit KF0 is implemented: the vocabulary, the JSON-LD context, the builders, the validators, and the contract fixtures. No server sends or receives shared knowledge.

| Function | State |
| :--- | :--- |
| Vocabulary, context document, vocabulary page (files) | Implemented |
| Builders and validators of each object and activity | Implemented |
| Contract fixtures | Implemented |
| Routes `/ns/v1` and `/ns` | Not implemented (Section 7.1) |
| Vocabulary in the sidecar | Not implemented (Section 7.2) |
| Known servers, inbox journal, single mode root (KF1) | Not implemented |
| `/kb` collections of other servers, themes across servers (KF2) | Not implemented |
| Knowledge base actors, curation, revisions, removal at the origin (KF3) | Not implemented |
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

No route serves the two files at `/ns/v1` and `/ns`. The route must do this (design Section 9.1 rule 3):

- `GET /ns/v1`: status 200, the bytes of `src/claire/static/ns/v1.jsonld`, `Content-Type: application/ld+json`, `Access-Control-Allow-Origin: *`. No authentication. The same answer in single mode and in network mode, and when the switch `federation` is off.
- `GET /ns`: status 200, the bytes of `src/claire/static/ns/index.html`, `Content-Type: text/html; charset=utf-8`. No authentication.
- No redirect on the server. The redirect (302) from `https://w3id.org/claire-bible/…` to `https://claire.bible/…` is on `w3id.org`.

### 7.2 Sidecar

The sidecar (`sidecar/fedify/`) does not know the vocabulary. The units KF1 to KF4 must add this:

- The context document as a local copy for the document loader of Fedify, so that Fedify does not get `https://w3id.org/claire-bible/ns/v1` from the network.
- The wire JSON of Claire without a change in the two directions. Claire reads the compact names, so a document that Fedify compacts again with other contexts is not valid for the validators.
- Handling of the types `Grant` and `Revoke`, and of the `Offer` to a knowledge base actor. It is not known if the vocabulary of Fedify 2.4.5 has classes for `Grant` and `Revoke`.
- The `Group` actor of each shared knowledge base with its keys, inbox, outbox, and followers (design Section 9.2).

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

Not tested: the context document with a JSON-LD processor (the repository has none in Python), and two servers that run. The test with two servers that run is for the units that add the inbox (KF1 and later).
