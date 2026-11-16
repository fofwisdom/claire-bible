# Federation of Shared Knowledge Between Claire Bible Servers (Idea I2)

- **Document Identifier**: `DESIGN-KNOWLEDGE-FEDERATION-20261116` (Rev. 1 to Rev. 3: `DESIGN-SERVER-PARTNERSHIP-20261116`, file `SERVER_PARTNERSHIP_DESIGN.md`)
- **Status**: Planned, not implemented (draft, Rev. 4). The owner direction of Section 2 is locked (CT31, CT33, CT35, CT36 of the shared knowledge base design, 2026-11-16). All other content is a **proposal**. Section 16 has the open questions; the owner answers them later.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Revisions**:
  - Rev. 1 (2026-11-16): a partnership between servers, with BookWyrm as the reference.
  - Rev. 2 (2026-11-16): review of NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, ForgeFed and Forgejo, and Lemmy; subscription and partnership as two levels with scopes.
  - Rev. 3 (2026-11-16): interoperation review for each kind of software; Fedify baseline; review of the need for a partnership.
  - Rev. 4 (2026-11-16): the terms follow Mastodon (Section 3): **federation** is the open exchange without an agreement, and **relay** is the server-level subscription. The term "partnership" is retired, with its bilateral agreement and its scopes. Themes share ingested documents between servers through federation (owner). The file is renamed.
- **Related Documents**:
  - [`SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md`](SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md) (shared knowledge bases, themes, contributions; this design extends them across servers)
  - [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md) (§4.2 ideas I1 and I2)
  - [`FEDERATION.md`](../implementation/FEDERATION.md) (server actor §4.2, inbox event contract §5.7, domain blocks, switch and readiness checks §7, Fedify version §9.3)
  - [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) (server modes §4, first admin setup §8.4, S9)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[unverified]**.

---

## 1. Goal

1. Themes share ingested documents between Claire servers through federation, with no agreement between admins.
2. The root page in network mode shows themes across servers and the servers that take part.
3. Reviewers curate from other servers. Accounts contribute to shared knowledge bases of other servers. Automatic contributions go across servers when both servers allow them.
4. Each server keeps its own decisions: merge of entities, subject judgement, review, reputation. No server is an authority over another server.

---

## 2. Owner Direction (2026-11-16)

| Item | Direction |
| :--- | :--- |
| Aim | Shared knowledge bases and themes share ingested documents between servers. Themes do this without a partnership (owner, Rev. 4). |
| Terms | The open exchange is **federation**; the earlier meaning of "partnership" is **relay**, as in Mastodon. One set of terms in all documents (owner, Rev. 4; Section 3). |
| Root page | In network mode, the root page shows this information. |
| Reference | BookWyrm and the projects of the owner's research list (NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, Gancio, Forgejo with ForgeFed, Lemmy). A reference needs a clean-room implementation because of the licenses. Software of other domains (events, video, audio, code) needs no communication design. |
| Default | When the ActivityPub functions are complete, the federation of shared knowledge is on by default, also in single mode. |
| Single mode | The admin sets the USERNAME (the handle) at the first setup. Single mode shows no root page of network mode: `/` sends the request to `/@{username}/` of the admin. This is an architecture change, and the rules change with it. |
| Automatic contribution | The admin turns it on or off for the server in the ActivityPub federation menu. |
| Fedify | Work uses the latest Fedify release (Section 4.4). |
| Later work | Merge decisions differ between servers; federation of merge decisions waits for a later design. Links between duplicate knowledge wait until there is a real need. |

Production state (owner, 2026-11-16): the production server is in network mode, and its admin has a USERNAME.

---

## 3. Terms (CT36)

Checked on 2026-11-16 against the Mastodon documentation and the READMEs of relay software (Section 4.3).

### 3.1 Federation

| Fact **[external]** | Source |
| :--- | :--- |
| Servers exchange content with no agreement between admins. Content reaches a server mostly because its accounts follow accounts of other servers; boosts, replies, and mentions also bring content. | Mastodon docs: user/network, spec/activitypub |
| Moderation is always local. Admin controls are one-sided: domain blocks with the levels "reject media", "limit", and "suspend"; import and export of block lists; secure mode (signed GET); limited federation mode (an allow list of one admin, "for private use"). | Mastodon docs: admin/moderation, admin/config |
| Mastodon has no bilateral agreement between two servers. | Not in the Mastodon documentation that was read |

**In Claire:** the federation of shared knowledge is this model. Any Claire server with `federation` on exchanges shared knowledge with the Claire servers that it knows (Section 6), unless the admin blocks the domain. The reviewers of each shared knowledge base decide each contribution and curation.

### 3.2 Relay

| Fact **[external]** | Source |
| :--- | :--- |
| A relay is a separate service actor, a hub: servers subscribe to it with `Follow`, and it sends the public, signed activities that it receives to all its subscribers again. | READMEs of pub-relay, Activity-Relay, Pleroma relay |
| The Mastodon documentation has no page about relays (the expected page gave 404). | Mastodon docs |
| Mobilizon calls the internal actor of its subscriptions between two servers `relay`; the other admin approves. | Mobilizon docs (Rev. 2 review) |

**In Claire:** a relay is a channel that the admin opts into. It brings announcements of shared knowledge bases from servers that no account of the server knows yet, so that themes across servers grow beyond the follows of persons. It carries only public announcements, no documents and no contributions (Section 7).

### 3.3 Retired Term "Partnership"

Rev. 1 to Rev. 3 used "partnership" for a bilateral agreement of two admins with negotiated scopes. Mastodon has no such concept. Its parts go to these places:

| Part of the old partnership | New place |
| :--- | :--- |
| Reading the shared knowledge bases of another server | Federation (Section 6) |
| One-way subscription by the admin to receive announcements | Relay (Section 7) |
| Curation and contributions across servers | Federation; the reviewers of each knowledge base decide (Sections 11, 12) |
| Trust for one remote account | A grant of the role `collaborator` by a moderator (Section 12.2) |
| Automatic contributions across servers | Federation, with the switch `kb_auto_contribution` on both servers (Section 12.3) |
| Acceptance of the approvals of another server, higher limits | Removed; nobody asked for them |
| "Partnership" between two accounts (shared knowledge base design §9.10) | Renamed "mutual collaboration" |

---

## 4. Clean-Room Rules

### 4.1 License Facts

Read on 2026-11-16 from license files, READMEs, and documentation footers only.

| Project | Code license | Specification or data | Effect for Claire (MIT) |
| :--- | :--- | :--- | :--- |
| Claire Bible | MIT **[code]** `LICENSE.md` | — | — |
| Mastodon | AGPL-3.0 | — | No code or text |
| BookWyrm | Anti-Capitalist Software License v1.4 (limits on kinds of users) | — | No code or text |
| NeoDB | AGPL-3.0 | — | No code or text |
| Inventaire | AGPL-3.0 | Data: CC0 (Wikidata-derived parts keep the Wikidata terms) | No code or text; data identifiers can be used |
| PeerTube | AGPL-3.0 | — | No code or text |
| Funkwhale | Not confirmed **[unverified]** | — | Treated as copyleft: no code or text |
| Mobilizon | AGPL-3.0 or later | Documentation: CC BY-SA 4.0 | No code or text |
| Forgejo | GPL-3.0 or later (v9 and later; MIT up to v8) | ForgeFed specification: CC0, and OWFA 1.0 from 2025-06-18 | No Forgejo code. The ForgeFed specification can be followed directly. |
| Lemmy | AGPL-3.0 | — | No code or text |
| Relay software (pub-relay, Activity-Relay, Pleroma relay) | Not checked | — | Treated as copyleft: no code or text |
| FEPs (Fediverse Enhancement Proposals) | — | CC0 (repository-wide statement) | Can be followed directly |

### 4.2 Rules

1. No source code, template, schema, migration, test, or documentation text of these projects enters Claire Bible.
2. This design takes only ideas and protocol facts from public documentation. Section 5 lists each idea, its project, and the Claire design in our own words.
3. Specifications with a public-domain or patent-promise license (ForgeFed: CC0 and OWFA 1.0; FEPs: CC0) can be followed directly. Claire still writes its own text and claims no conformance.
4. Persons who write Claire code for this design do not read the source code of these projects. They work only from this design and from the specifications.
5. Section 5.4 says which software Claire exchanges with. Standard base types (Section 9.1) let other software show Claire objects.
6. When a later revision takes a new idea from a project, it adds a row to Section 5 with the page and the date.

### 4.3 Pages Read (2026-11-16, documentation and license only)

| Project | Pages |
| :--- | :--- |
| Mastodon | `docs.joinmastodon.org`: spec/activitypub, admin/moderation, admin/config, user/network (admin/relays: 404) |
| Relay software | READMEs of `noellabo/pub-relay`, `yukimochi/Activity-Relay`, `pleroma/relay` |
| BookWyrm | `FEDERATION.md`, `LICENSE.md`; `docs.joinbookwyrm.com`: federation, activitypub, admin-settings, system |
| NeoDB | `FEDERATION.md` (pointer), `neodb.net/internals/activitypub`, `neodb.net/servers/`, repository README |
| Inventaire | `data.inventaire.io`, `api.inventaire.io`, `wiki.inventaire.io` (Entities data, Technic), repository READMEs |
| PeerTube | `docs.joinpeertube.org/api/activitypub`, `docs.joinpeertube.org/admin/following-instances`, `LICENSE` |
| Funkwhale | `docs.funkwhale.audio/developer/federation/index.html`, documentation index |
| Mobilizon | `docs.mobilizon.org` (ActivityPub, instance federation), repository README |
| ForgeFed, Forgejo | `forgefed.org/spec` (parts of Sections 6 to 8 not reached), `forgefed.org`, Forgejo FAQ, Forgejo federation post of 2023-01-10 |
| Lemmy, Fedify | `join-lemmy.org/docs/contributors/05-federation.html`, `fedify.dev/tutorial/threadiverse`, repository README; FEP repository README. FEP-1b12 itself could not be read **[unverified]**. |

### 4.4 Fedify Baseline

The sidecar uses Fedify 2.4.5 (`deno.json` pins `~2.4.5`), the latest release on JSR on 2026-11-16 **[code]** FEDERATION.md §9.3. Work for this design uses the latest Fedify release at the time of the work. A fact about an older Fedify version in a tutorial or a report does not apply.

---

## 5. Reference Review

### 5.1 Check of the Research List

| Claim of the research list | Result |
| :--- | :--- |
| NeoDB uses a dedicated relay for catalog sharing between instances. | Partly confirmed. A default relay of the NeoDB developers sends public activities and catalog information between compatible NeoDB servers. It is optional. It is not only for catalog data. |
| Inventaire has an ActivityPub shared inbox at `/activitypub/shared-inbox`. | Not confirmed. Its fediverse part is an experiment; users and groups do not federate yet. Inventaire keeps book data on one "entities host" so that the data is not copied. |
| Funkwhale stores received payloads before it processes them. | Confirmed. |
| Lemmy wraps received activities of a community in `Announce` for its followers. | Confirmed (Lemmy documentation). |
| The other rows (PeerTube, Mobilizon, Gancio, ForgeFed) | Confirmed in their main points, except Gancio, which this review did not read. |

### 5.2 Ideas and Their Use

| No. | Project | Idea (own words) | Claire design |
| :--- | :--- | :--- | :--- |
| B1 | BookWyrm | Servers share book metadata to make a networked database. | Servers read the `/kb` collections of each other (Section 8). |
| B2 | BookWyrm | Objects have custom types and origin URLs; a receiver keeps a local copy and matches it to existing objects when it can. | Origin IRIs on all objects; the receiver matches entities by its own rules (Section 10.4). |
| B3 | BookWyrm | Data sources with an admin-set priority for search; to block incoming search gives more duplicates. | The page "Make a shared knowledge base" shows similar knowledge bases of other servers first (Section 8.3). |
| B4 | BookWyrm | A book page shows reviews of the same book from connected servers. | A theme page shows knowledge bases of the same subject from other servers (Section 8.2). |
| B5 | BookWyrm | A server actor signs GET requests; a server can require signed GET. | The server actor signs all requests of this design; collections of shared knowledge need a signed GET (Section 14). |
| B6 | BookWyrm | Admin list of federated servers with block. | The list of known servers (Section 6.2). |
| B7 | BookWyrm | Custom types reach other software as `Article` or `Note`. | Standard base types (Section 9.1). |
| B8 | BookWyrm | The receiving server decides what it shows. | Each server decides what it shows and keeps (Sections 10, 11). |
| B9 | BookWyrm | Custom types without a published JSON-LD context. | Claire publishes its context from the first version (Section 9.1). |
| P1 | PeerTube | The platform actor (`Application`) follows other platforms; the followed admin can require manual approval. | The subscription to a relay (Section 7). |
| P2 | PeerTube | An admin can follow the platforms of an index list automatically. | An index list gives suggestions of known servers only (Section 6.2). |
| P3 | PeerTube | A following platform receives metadata with links to the files, not the files. | `/kb` collections carry metadata and signatures, not documents (Section 8.1). |
| P4 | PeerTube | A mirror server tells the origin with a `CacheFile` object that it holds a copy; the origin controls who may mirror. | The alternative to knowledge base actors (Section 9.2, rule 6). |
| P5 | PeerTube | A channel is a `Group` actor that several accounts manage. | A shared knowledge base as an actor (Section 9.2). |
| M1 | Mobilizon | Servers subscribe to each other through an internal `relay` actor; the other admin approves; only public content created after the subscription is sent. | Relay (Section 7); a full read at first contact (Section 8.1). |
| M2 | Mobilizon | Group members have roles from "invited" to "administrator". | Same idea as the roles of a shared knowledge base (shared knowledge base design §6.2). |
| L1 | Lemmy | A community is a `Group`; it wraps each received activity in `Announce` and sends it to its followers. | A knowledge base actor sends revisions and decisions to its followers (Section 9.2). |
| L2 | Lemmy | Moderators of other servers can be added to a community; an admin removal of content in a remote community has effect only on the local server. | Remote collaborators with grants; no remote moderators in v1; a local removal never changes the origin (Sections 12.2, 10.3). |
| F1 | ForgeFed | To open an issue on a remote repository, the author sends `Offer` to the tracker actor; the tracker answers `Accept` with the new IRI in `result`, or `Reject`; the answer can wait for human review. | The contribution across servers (Section 12.1). |
| F2 | ForgeFed | When the target accepts, it hosts its own copy and controls it; the sender is only the author. Forks are new actors with copies whose `origin` points to the original. | Rules for copies (Section 10.1). |
| F3 | ForgeFed | Roles are given with `Grant` (context = resource, target = person, object = role, end time) and taken back with `Revoke`; the grant IRI is the capability that later activities carry. | Remote collaborators (Section 12.2). |
| W1 | Funkwhale | Each pod stores received payloads before it processes or delivers them. | The inbox journal (Section 13). |
| W2 | Funkwhale | A library is an actor and a collection; the service actor of a pod is the authority for the objects of its domain. | Knowledge base actors; the origin is the authority (Sections 9.2, 6.4). |
| N1 | NeoDB | A remote review lands on an existing item that shares an external ID (ISBN, Open Library, Goodreads). | Authority identifiers of entities (Section 10.4). |
| N2 | NeoDB | Follow the origin: lists keep possibly stale mirrors and do not serve them again as ActivityPub. | A holder never serves a remote object as its own (Section 10.1). |
| N3 | NeoDB | An optional relay sends public activities between servers. | Relay (Section 7). |
| I1 | Inventaire | Entities have `wd:` (Wikidata) and `inv:` (local) identifiers; data is CC0. | Authority identifiers of entities (Section 10.4). |

### 5.3 Common Patterns

1. **Exchange without an agreement; one-sided admin controls** (Mastodon): Section 6.
2. **A relay as an opt-in hub for discovery** (relay software, NeoDB, Mobilizon): Section 7.
3. **A container is an actor with followers** (Lemmy, PeerTube, Mobilizon, Funkwhale, ForgeFed): Section 9.2.
4. **A submission to a remote container is an offer that the container accepts or rejects** (ForgeFed): Section 12.1.
5. **The origin is the authority; a receiver keeps its own copy and marks the origin** (ForgeFed, Funkwhale, NeoDB, PeerTube): Section 10.1.
6. **Shared external identifiers make matching across servers possible** (Inventaire, NeoDB): Section 10.4.
7. **Received payloads are kept before processing** (Funkwhale): Section 13.

The parser of one project (Lemmy) is stricter than the others about the form of the public address. This is not a rule for Claire; Claire follows the forms that the specifications allow.

### 5.4 Interoperation by Kind of Software

The patterns of Section 5.3 are design references only. Communication is designed only where an exchange with a knowledge platform makes sense.

| Software | Domain | Exchange with Claire |
| :--- | :--- | :--- |
| Claire Bible | Knowledge | This design (idea I2) |
| Mastodon, Misskey, and other microblog software | Posts | Existing general federation: the account actor publishes documents as `Article` (FP3); remote reading receives `Note` and `Article` (FP4); content enters a knowledge base only by an ingest of the owner. No new protocol. |
| Blog software with ActivityPub (for example WriteFreely, the WordPress plugin, Ghost) | Articles | Same as the line above. **[unverified]** the federation state of each product. |
| BookWyrm, NeoDB | Book and media catalogs, reviews | Their reviews reach other software as `Article` or `Note`, so remote reading covers them. Claire does not read their catalog types. Later: their book identifiers (ISBN) as authority identifiers (Section 10.4). |
| Inventaire | Book data (Wikidata-centered, CC0) | No ActivityPub exchange. Later: its CC0 open data interface as an authority source for book entities, outside ActivityPub. |
| Lemmy and other forum software | Forums | Remote reading covers their posts. No special handling. A follow of a knowledge base actor from forum software is idea I1, outside this design. |
| Forgejo (ForgeFed) | Code forges | No exchange. Only the specification patterns (`Offer`, `Grant`) are used. |
| Funkwhale | Audio | No exchange. Pattern only. |
| PeerTube | Video | No exchange (owner, 2026-11-16). Pattern only. |
| Mobilizon, Gancio | Events | No exchange (owner, 2026-11-16). Pattern only. |

---

## 6. Federation of Shared Knowledge (CT35)

### 6.1 Rule

No agreement between admins is necessary. A Claire server with `federation` on exchanges shared knowledge with each Claire server that it knows (Section 6.2), unless a domain block stops it. The reviewers of each shared knowledge base decide each contribution and curation.

### 6.2 Known Servers

A Claire server becomes known when:

1. an account of this server follows, reads, joins, curates from, or contributes to something on it;
2. an account of that server contributes to or curates from a shared knowledge base of this server;
3. the admin adds its domain in `/admin/federation`;
4. a relay brings an announcement from it (Section 7).

The server reads NodeInfo and the server actor of a known server and keeps its software, version, counts, last read, and errors. An index list of Claire servers (P2) and the known servers of known servers are suggestions in the console; the admin adds them by hand.

### 6.3 Admin Controls

All controls are one-sided, as in Mastodon (Section 3.1).

| Control | Effect |
| :--- | :--- |
| `federation` (existing switch) | Off: no exchange of shared knowledge. |
| Domain block (existing) | No exchange with that domain. Copies from it stay (CT7, CT19); their revisions stop. |
| `kb_auto_contribution` | Off: the accounts of this server send no automatic contributions, and this server accepts none (Section 12.3). |
| Signed GET | Collections of shared knowledge answer only signed requests of server actors, so that domain blocks apply to reads (Section 14). |

A level "limit" for domain blocks (offers from that domain always wait for approval; its knowledge bases do not join local themes) is **[proposal, PQ13]**.

### 6.4 Authority

1. The origin server is the authority for its objects: its knowledge bases, its documents, and the decisions of its reviewers (W2, F2).
2. A server never changes an object of another server. It sends offers and notices; the origin decides.
3. A local action on a copy (hide, remove, stop updates) has effect only on the local copy (L2).

### 6.5 Admin Console `/admin/federation`

| Area | Content |
| :--- | :--- |
| Switches | `federation`, `kb_auto_contribution` (Section 14) |
| Known servers | Domain, software, version, how it became known, counts of shared knowledge bases, last read, errors; add a domain |
| Domain blocks | Existing list |
| Relays | Subscriptions and their state (Section 7) |
| Suggestions | Known servers of known servers, index lists |
| Journal | The inbox journal of Section 13, with replay of a failed activity |

---

## 7. Relay **[proposal, PQ12]**

1. A relay is a service actor that sends the public announcements that it receives to all its subscribers again (Section 3.2).
2. The admin subscribes the server to a relay with `Follow` from the server actor. The relay operator can approve. `Undo` ends the subscription. Each action needs a recent sign-in and writes `admin_audit`.
3. A subscribed server sends the public announcements of its shared knowledge bases (`Create`, `Update`, `Delete` of `claire:KnowledgeBase`) to the relay and receives those of the other subscribers. Each sender becomes a known server (Section 6.2).
4. A relay carries no documents, no contributions, and no decisions. The exchange of content stays in federation between the two servers.
5. **[unverified]** Generic relay software sends again only payloads with a linked-data signature. Unit KF6 checks which proof the relays accept (for example FEP-8b32 object integrity proofs) before Claire depends on one.
6. A Claire server that acts as a relay (as the internal `relay` actor of Mobilizon) is later work.
7. Default: no relay.

---

## 8. `/kb` Collections and Themes Across Servers

### 8.1 Reading the `/kb` Collection of Another Server

1. The `/kb` collection of a server (shared knowledge base design §7) lists its shared knowledge bases: label, description, knowledge type, counts, the signature of §8.2 of that design, and the IRI. No documents (P3).
2. At the first contact with a known server, this server reads its `/kb` collection once in full with a signed GET. Later changes come from the knowledge base actors (Section 9.2) or from a new read each day.
3. This server keeps the records in a store of remote knowledge bases. A remote knowledge base is not a partition of the local `claire.db` and has no documents until a curation or a contribution.
4. Public personal knowledge bases are not in the `/kb` collection **[proposal, PQ5]**.
5. Themes are not exchanged: each server computes its own themes and links to the knowledge bases of other servers.

### 8.2 Themes Across Servers (CT35)

1. The subject judgement of each server includes the signatures of remote shared knowledge bases. A remote knowledge base can be a member of a local theme.
2. The same subject can have different theme numbers on different servers. A theme number never leaves its server as an identifier.
3. The theme page shows local and remote knowledge bases. A remote knowledge base links to its origin.

### 8.3 Root Page (Network Mode)

1. Themes first, with the counts of local and remote shared knowledge bases.
2. A section "Federated servers": the known servers that have shared knowledge bases, with name, domain, and counts.
3. Recent activity across servers: new shared knowledge bases of other servers, contributions that other servers approved from accounts of this server, curations.
4. The page "Make a shared knowledge base" shows similar local and remote shared knowledge bases before the account makes a new one (B3).

Single mode shows no root page (Section 15).

---

## 9. Objects and Actors

### 9.1 Vocabulary

1. Claire publishes one JSON-LD context for its types from the first version (B9). The context IRI is the same for all Claire servers **[proposal, PQ2]**. Each server also serves a copy at a local path. Claire servers read the compact names; they do not need a JSON-LD processor.
2. The vocabulary document is written for Claire only, in our own words (Section 4.2).

| Type | Base type | Content |
| :--- | :--- | :--- |
| `claire:KnowledgeBase` | `Group` with a collection of documents **[proposal, PQ7]** | A shared knowledge base: label, description, knowledge type, counts, signature, documents collection, `followers` |
| Document | `Article` | Title, content, canonical URL, content hash, `attributedTo` (the actor with the credit), `origin` (the object it was copied from), `claire:root` (the root document) |
| `claire:Extraction` | `Object` | Entities (keys `type` and `norm_name`, aliases, `sameAs` with authority identifiers) and relations of one document, so that the receiver builds its graph without an LLM call |
| `claire:Contribution` | `Object` | A document with its extraction, offered to one shared knowledge base |

### 9.2 Knowledge Bases as Actors **[proposal, PQ7]**

1. Each shared knowledge base has a `Group` actor with an inbox, an outbox, and `followers`. Its keys are in the sidecar like the other keys.
2. A server that holds copies of the documents of a knowledge base, or that has it in a local theme, follows that actor with its server actor.
3. The knowledge base actor sends `Update` of its own record, `Update` of a document (revision), and `Delete` (removal at the origin) to its followers.
4. A contribution `Offer` goes to the inbox of the knowledge base actor (F1).
5. Followers are only server actors of Claire servers in this design. A follow of a person of the open fediverse is idea I1 and stays outside this design; the same actor can serve it later.
6. Alternative if PQ7 is rejected: no knowledge base actors; the holder sends `Add` (document IRI, target = its knowledge base) to the server actor of the origin, and the origin keeps the list of holders (as `CacheFile` in P4); changes of records come from the daily read.

### 9.3 Activities

| Activity | From → to | Object | Effect |
| :--- | :--- | :--- | :--- |
| `Follow`, `Accept`, `Undo` | Server actor → knowledge base actor | — | Holder or theme member (Section 9.2) |
| `Update`, `Delete` | Knowledge base actor → followers | `claire:KnowledgeBase` | Change of the record |
| `Update` | Knowledge base actor → followers | `Article` with `claire:Extraction` | Revision (Section 10.2) |
| `Delete` | Knowledge base actor → followers | `Article` | Removal at the origin (Section 10.3) |
| `Offer` | Account actor → knowledge base actor | `claire:Contribution` | Pending contribution (Section 12.1) |
| `Accept` with `result`, `Reject` | Knowledge base actor → account actor | The `Offer` | Decision of the reviewers; `result` is the IRI of the contribution on the target |
| `Grant`, `Revoke` | Knowledge base actor → account actor | Role `collaborator` | Remote collaborator (Section 12.2) |
| `Follow`, `Undo` | Server actor → relay actor | — | Relay subscription (Section 7) |
| `Create`, `Update`, `Delete` | Server → relay | `claire:KnowledgeBase` (public) | Announcement through the relay (Section 7) |

Curation and the first read use signed GET requests of the collections of the other server.

---

## 10. Copies, Revisions, and Entities

### 10.1 Copies

1. A copy (curation or accepted contribution) is a new object on the holder with a new IRI. It keeps `origin`, the IRI of the actor with the credit, and `claire:root` (F2).
2. The holder hosts and controls its copy (F2). It never sends the content of a remote object as its own object: its collections show the copy with `origin` (N2).
3. When a domain block starts, copies from that domain stay (CT7, CT19). Revisions stop because the channel stops.

### 10.2 Revisions

The knowledge base actor of the origin sends `Update` for each revision of a document to its followers. The holders apply it automatically, as in CT20 and CT26 of the shared knowledge base design.

### 10.3 Removal at the Origin **[proposal, PQ3]**

The holder hides the copy at once and puts it in its review queue with the reason code of the origin. Its reviewers decide to remove it or to show it again. A copy is not removed automatically, because a removal at the origin can have a local reason; the hide at once protects the holder when the reason is legal or spam. A removal on the holder never changes the origin (L2).

### 10.4 Entities Across Servers

1. Each server resolves entities in its own merge job (CT24). No server changes the graph of another server. Different results on different servers are normal.
2. **Authority identifiers** **[proposal, PQ9]**. An entity can carry identifiers of an authority in `sameAs`: Wikidata (`wd:Q…`), ISBN, ORCID, and similar (I1, N1). The merge job of the receiver matches an entity with the same authority identifier first, then by keys. v1 sends an identifier only when the entity already has one; a function that links entities to an authority is later work.
3. Federation of merge decisions and links between duplicates are later work (owner direction).

---

## 11. Curation Across Servers

1. A reviewer of a local shared knowledge base K opens a remote shared knowledge base S that is in a theme of K and selects documents.
2. This server reads the documents and their extractions with a signed GET and makes copies as in §9.7 of the shared knowledge base design, with the remote credit and `origin`.
3. This server follows the knowledge base actor of S (Section 9.2) to receive revisions and removals.

---

## 12. Contributions and Roles Across Servers

### 12.1 Contribution to a Shared Knowledge Base of Another Server (F1)

1. An account of server A selects a shared knowledge base of server B, or a theme with knowledge bases of B. The flow of §9.1 and §9.2 of the shared knowledge base design applies, with these changes.
2. The account actor of A sends `Offer` with the document and its extraction to the knowledge base actor of B. The account needs a handle (CT33).
3. B makes a pending contribution with the remote actor as contributor. The answer can wait for its reviewers. B sends `Accept` with `result` (the IRI of the contribution on B) or `Reject` with the reason code. The submission page on A shows the decision.
4. The credit on B is the remote actor. When B receives `Delete(Person)` for that actor, the credit becomes "deleted account" (CT7).
5. B computes the reputation of the remote account only from the decisions of B.

### 12.2 Remote Collaborators (F3, L2) **[proposal, PQ8]**

1. A moderator of a shared knowledge base on B can give the role `collaborator` to an account of another server. B sends `Grant` (context = the knowledge base, target = the remote actor, object = `collaborator`, end time 6 months) from the knowledge base actor.
2. Each `Offer` of that account carries the grant IRI as its capability. B checks the grant (its own actor, its context, its target, the time, not revoked) and then applies the rules of a collaborator: no approval (shared knowledge base design §6.2), unless the local reputation of the account is `low`.
3. `Revoke` ends the grant at once. The moderator can renew a grant before its end time.
4. A remote collaborator has only the capabilities `contribute_direct` and `contribute_theme` across servers. Review, curation, and maintenance stay with local accounts in v1, because they need a session on B.
5. No remote moderators in v1. The admin of B is responsible for its moderators.
6. Mutual collaboration between accounts of different servers (shared knowledge base design §9.10) uses the same grants.

### 12.3 Automatic Contribution Across Servers

1. Needs `kb_auto_contribution` `on` on both servers: on the sender for its accounts, on the receiver to accept.
2. The rule of the account (shared knowledge base design §9.9) can include remote targets. The candidates use the signature of the remote knowledge base from the store of Section 8.1.
3. All limits and safety rules of §9.9 apply on the sender. The receiver applies its own pending limits for each remote account and each domain.

---

## 13. Inbox Journal (W1)

1. The sidecar stores each received activity of this design, with the result of its signature check, before Claire processes it.
2. Processing is idempotent: the activity IRI is the key; the same activity processed twice changes nothing.
3. A failed activity stays in the journal with its error. The admin can process it again from `/admin/federation`.
4. Retention: 30 days for processed activities, 90 days for failed activities **[proposal]**. The journal is in `fedify.db` or a new table of `client.db` **[unverified]**: unit KF1 decides after a check of the inbox event contract (FEDERATION.md §5.7).

---

## 14. Security and Switches

| ID | Risk | Control |
| :--- | :--- | :--- |
| KS1 | A blocked server reads or writes shared knowledge | Collections answer only signed requests of server actors; domain blocks apply to reads and writes. |
| KS2 | Another server sends large or bad objects | Size limits for each object, each domain, and each day; schema validation; HTML sanitization; the merge job makes no LLM call, so received text is not a prompt in the merge. |
| KS3 | Another server floods the review queue | Pending limits for each remote account and each domain; domain block. |
| KS4 | Another server tells a false reputation or merge decision | Neither is received; each server computes its own (Sections 10.4, 12.1). |
| KS5 | Private data leaves the server | Only shared knowledge bases (always public) and their visible documents leave the server. Personal knowledge bases are not in `/kb` (PQ5). No IdP data goes into federation (existing rule). |
| KS6 | A relay brings spam announcements | Announcements only add known servers and records of remote knowledge bases, never content; the admin ends the subscription or blocks the domain. |
| KS7 | A relay subscription or a domain change by mistake or with a stolen token | Recent sign-in for each admin action; `admin_audit`. |
| KS8 | A grant is used after it should end | End time of 6 months; `Revoke`; each use checks the grant (Section 12.2). |
| KS9 | An activity is processed twice or lost | Inbox journal with idempotent processing (Section 13). |

| Switch (`server_settings`, all in `/admin/federation`) | Values | Default |
| :--- | :--- | :--- |
| `federation` (exists) | `off`, `on` | `off` today; `on` when the ActivityPub functions are complete (owner direction). It also turns on the federation of shared knowledge. |
| `kb_auto_contribution` | `off`, `on` | `off` until the admin turns it on **[proposal, PQ6]** |

The switch `partnerships` of Rev. 1 to Rev. 3 is removed.

---

## 15. Single Mode (CT33, confirmed)

1. **USERNAME.** The first setup sets the USERNAME of the admin (namespace design §8.4 already requires it). The production server already has one.
2. **Root.** `/` sends a temporary redirect (302) to `/@{username}/`. It is temporary because the meaning of `/` depends on the server mode, which the admin can change. Other root paths of the personal knowledge base of single mode get 308 to `/@{username}/…`; these targets stay valid in network mode.
3. **Setup not complete.** While the admin account has the state `setup_required`, `/` with the admin credential goes to `/setup` (as today); other requests get a page "setup not complete". There is no other target.
4. **USERNAME exposure.** Rule S9 of the namespace design (no USERNAME in single mode) is withdrawn. The test `tests/test_single_mode_no_username_leak.py` is replaced by a test of the redirects. The setup confirmation says that the USERNAME is public in both modes.
5. **Federation in single mode.** The server actor and the federation of shared knowledge work in single mode. The readiness check GF1 (network mode) changes for them. The account actor of the admin in single mode: **[proposal, PQ4]**.
6. **Mode change.** The refusal of `network` → `single` while `federation` is `on` (FEDERATION.md §8.3) changes, because federation can be on in single mode. A change to single mode still needs no members.

---

## 16. Open Questions

Resolved in Rev. 4: PQ1 (two levels of agreement) and PQ10 (need of a partnership) by the owner direction of Section 2: federation without an agreement, relay for the server-level subscription. PQ11 (the term "directory") is applied with the unification of terms: the text uses "the `/kb` collection of another server"; the owner can reverse it.

| ID | Question | Proposal |
| :--- | :--- | :--- |
| PQ2 | The IRI of the Claire JSON-LD context (Section 9.1) | One IRI under a domain of the project that the owner selects; each server also serves a copy. |
| PQ3 | Removal at the origin (Section 10.3) | The holder hides the copy at once and its reviewers decide. |
| PQ4 | Does the account actor of the admin federate in single mode? It is necessary for contributions to other servers. | Yes, with the same rules as network mode (`profile_public`, step-up). |
| PQ5 | Public personal knowledge bases in the `/kb` collection? | No in v1. Only shared knowledge bases. |
| PQ6 | Default of `kb_auto_contribution` | `off`. |
| PQ7 | Each shared knowledge base as a `Group` actor, with holders and theme members as its followers (Section 9.2)? | Yes. |
| PQ8 | Remote collaborators with `Grant` and `Revoke` of the ForgeFed pattern, only for contributions, no remote moderators (Section 12.2)? | Yes. |
| PQ9 | Authority identifiers (Wikidata and others) in `sameAs` of entities, matched first by the receiver (Section 10.4)? | Yes, when the entity already has one; linking to an authority is later work. |
| PQ12 | Relay as in Section 7: admin opt-in, announcements of shared knowledge bases only, no relay by default; a Claire server as a relay later? | Yes. |
| PQ13 | A domain block level "limit" (offers always wait for approval; its knowledge bases do not join local themes)? | Yes, as a later step after the existing block. |

---

## 17. Phases

Precondition: units CT-0 to CT-9 of the shared knowledge base design, and the federation phases of the sidecar.

| Phase | Scope | Completion criteria |
| :--- | :--- | :--- |
| KF0 | Vocabulary and JSON-LD context in our own words; contract fixtures for each object and activity | Fixtures validate on both sides of a test with two Claire servers |
| KF1 | Known servers, admin controls, inbox journal, single mode root and USERNAME (Section 15) | Two test servers know each other after a contribution or an admin entry; a blocked third server gets 404 on shared-knowledge collections; a failed activity can be processed again; single mode `/` gives 302 to `/@{username}/` |
| KF2 | Reading `/kb` collections, store of remote knowledge bases, themes across servers, root page | A shared knowledge base on server B appears in a theme of server A and on the root page of A, without any agreement |
| KF3 | Knowledge base actors, curation across servers, revisions, removal at the origin | A revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue |
| KF4 | Contributions across servers (`Offer`, `Accept` with `result`, `Reject`), remote collaborators (`Grant`, `Revoke`) | E2E test: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after `Revoke`, it needs approval again |
| KF5 | Automatic contribution across servers | With `kb_auto_contribution` on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run |
| KF6 | Relay (after PQ12) | Two servers subscribed to a test relay know each other's shared knowledge bases without any account interaction |

Later work: federation of merge decisions; links between duplicate knowledge; a function that links entities to an authority; a Claire server as a relay; idea I1 (follows of persons of the open fediverse to a knowledge base actor).
