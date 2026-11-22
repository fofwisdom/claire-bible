# Federation of Shared Knowledge Between Claire Bible Servers (Idea I2)

- **Document Identifier**: `DESIGN-KNOWLEDGE-FEDERATION-20261116` (Rev. 1 to Rev. 3: `DESIGN-SERVER-PARTNERSHIP-20261116`, file `SERVER_PARTNERSHIP_DESIGN.md`)
- **Status**: Units KF0 to KF6 are implemented; the current behavior is in [`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md). Pending verification and execution: the acceptance path (Section 14.2, steps KA1 to KA5). Rev. 10. The owner direction of Section 2 and the decisions KD1 to KD14 of Section 2.1 are locked (KD1 to KD9: 2026-11-16; KD10 to KD12: 2026-11-18; KD13 to KD14: 2026-11-19). No open questions remain. Work starts from the visual prototype [`prototypes/console_modern_prototype.html`](prototypes/console_modern_prototype.html) (mock data; owner, 2026-11-16). Decision GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (owner, 2026-11-22) removed the switch `federation`: federation is a function of each server that cannot be turned off. Decision KD13 and rule 8 of Section 14.1.1 (the gate) have no object, each statement "while the switch `federation` is on" or "off" in this document is void, and GF14 to GF16 apply on each server. Decisions GD5 to GD8 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (owner, 2026-11-22) also apply: every `active` account with a USERNAME is an actor regardless of `profile_public` (GD5), toggling `profile_public` needs no step-up authentication (GD6), and under GD8 ("Model 1", revising GD7) `profile_public` controls discovery (`/directory`, `Person.discoverable`) and search indexing only—outbound KB offers, automatic contribution remote targets, public document contributor credit (`attributedTo`), and recent activity account names require only an active actor with a USERNAME regardless of `profile_public`.
- **Date**: 2026-11-16
- **Base commit**: `e679bf90`
- **Revisions**:
  - Rev. 1 (2026-11-16): a partnership between servers, with BookWyrm as the reference.
  - Rev. 2 (2026-11-16): review of NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, ForgeFed and Forgejo, and Lemmy; subscription and partnership as two levels with scopes.
  - Rev. 3 (2026-11-16): interoperation review for each kind of software; Fedify baseline; review of the need for a partnership.
  - Rev. 4 (2026-11-16): the terms follow Mastodon (Section 3): **federation** is the open exchange without an agreement, and **relay** is the server-level subscription. The term "partnership" is retired, with its bilateral agreement and its scopes. Themes share ingested documents between servers through federation (owner). The file is renamed.
  - Rev. 5 (2026-11-16): the relay and the history of the server-level partnership of Rev. 1 to Rev. 3 moved to [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md), because the plan grew too wide (owner). This design keeps federation only. The mutual collaboration between accounts is not a part of that partnership and stays in the shared knowledge base design §9.10.
  - Rev. 6 (2026-11-16): answers to PQ3 to PQ9 and PQ13 (KD1 to KD8, Section 2.1). Review of the owner proposal for PQ2 (the domain `claire.bible`) with the policy of the `.bible` registry (Section 16).
  - Rev. 7 (2026-11-16): answer to PQ2: the identifiers are on `w3id.org` (KD9). The registration is a pull request of the owner when the target is live (Section 9.1).
  - Rev. 8 (2026-11-16): the visual prototype is the base for the work (owner). The areas of the admin console (Section 6.5) are tabs of the section `/admin/federation` in [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md).
  - Rev. 9 (2026-11-18, base commit `33748825`): owner decisions KD10 to KD12. The readiness checks of the knowledge federation GF8 to GF17 and the contract change with the sidecar (Section 14.1), the acceptance path KA1 to KA5 (Section 14.2), unit KF6 (Section 17), and the open questions PQ14 and PQ15 (Section 16).
  - Rev. 10 (2026-11-19): owner decisions KD13 (`PQ14: A`) and KD14 (`PQ15: C`), and unit KF6 implementation complete.
  - Rev. 11 (2026-11-19): owner decision KD15. The root page has no list of the federated servers; the public account directory is `/directory` in both modes; the form of the recent activity waits for an evaluation of prototypes (Section 8.3). The account routes of the federation are in both modes (Section 15 rule 5).
- **Related Documents**:
  - [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) (the development network of two servers for the acceptance path, KD12)
  - [`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) (layout and tabs of the console)
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
| Scope | The server-level partnership of Rev. 1 to Rev. 3 and the relay are out of this design and kept in [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) (owner, Rev. 5). The mutual collaboration between accounts is an exchange inside a server and stays in the shared knowledge base design §9.10. |
| Root page | In network mode, the root page shows this information. |
| Reference | BookWyrm and the projects of the owner's research list (NeoDB, Inventaire, PeerTube, Funkwhale, Mobilizon, Gancio, Forgejo with ForgeFed, Lemmy). A reference needs a clean-room implementation because of the licenses. Software of other domains (events, video, audio, code) needs no communication design. |
| Default | When the ActivityPub functions are complete, the federation of shared knowledge is on by default, also in single mode. **Replaced** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md): federation is always on and has no switch. |
| Single mode | The admin sets the USERNAME (the handle) at the first setup. Single mode shows no root page of network mode: `/` sends the request to `/@{username}/` of the admin. This is an architecture change, and the rules change with it. |
| Automatic contribution | The admin turns it on or off for the server in the ActivityPub federation menu. |
| Fedify | Work uses the latest Fedify release (Section 4.4). |
| Later work | Merge decisions differ between servers; federation of merge decisions waits for a later design. Links between duplicate knowledge wait until there is a real need. |

Production state (owner, 2026-11-16): the production server is in network mode, and its admin has a USERNAME.

### 2.1 Decisions (2026-11-16 to 2026-11-19)

| ID | Question | Decision |
| :--- | :--- | :--- |
| KD1 | PQ3: removal at the origin | The holder hides the copy at once and puts it in its review queue; its reviewers decide (Section 10.3). |
| KD2 | PQ4: the account actor of the admin in single mode | It federates with the same rules as network mode: every `active` account with a USERNAME is an actor without a `profile_public` actor gate or step-up on `profile_public` (GD5, GD6); under GD8 (revising GD7), KB offers, automatic remote targets, and public contributor credit require only active actor status regardless of `profile_public` (Section 15). |
| KD3 | PQ5: public personal knowledge bases in `/kb` | No in v1. Only shared knowledge bases (Section 8.1). |
| KD4 | PQ6: default of `kb_auto_contribution` | `off` (Section 14). |
| KD5 | PQ7: knowledge bases as actors | Each shared knowledge base has a `Group` actor; holders and theme members follow it (Section 9.2). |
| KD6 | PQ8: remote collaborators | `Grant` and `Revoke` of the ForgeFed pattern, only for contributions, no remote moderators (Section 12.2). |
| KD7 | PQ9: authority identifiers | Entities carry authority identifiers in `sameAs` when they have them; the receiver matches them first; linking to an authority is later work (Section 10.4). |
| KD8 | PQ13: domain block level "limit" | Yes, as a later step after the existing block (Section 6.3). |
| KD9 | PQ2: the IRI of the JSON-LD context | Option B: the identifiers are under `https://w3id.org/claire-bible/` and redirect (302) to `https://claire.bible/`. The context is `https://w3id.org/claire-bible/ns/v1`; the vocabulary terms are `https://w3id.org/claire-bible/ns#…` (Sections 9.1, 16). |
| KD10 | Order of the remaining checks (2026-11-18) | Before the remaining checks of the knowledge federation (a real exchange between two servers with real sidecars, the check of the JSON-LD context before it is hosted, the calibration of thresholds and limits), the readiness checks GF1 to GF7 must pass. The order of all steps is the acceptance path (Section 14.2). |
| KD11 | Readiness of the knowledge federation (2026-11-18) | GF1 to GF7 examine only that one server is ready for the federation of accounts. The paths of the knowledge federation need new readiness checks, GF8 and later (Section 14.1). |
| KD12 | Development stack (2026-11-18) | The Docker development stack can be used when it is necessary. It must become a common environment, so that the community can contribute code ([`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md), decision CD1). |
| KD13 | PQ14: gate of `federation` switch (2026-11-19) | Option A: changing `federation` to `on` (`POST /api/admin/federation`) requires every required check that applies at that time (GF1 to GF7 and GF8 to GF13). GF14 to GF16 apply after the change; GF17 is a warning only (Section 14.1.1 rule 8). **Withdrawn** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (2026-11-22): there is no switch, so there is no gate. |
| KD14 | PQ15: scope of KA3 (2026-11-19) | Option C: KA3 runs between the two servers of the development network; the first exchange of a server of the owner with another server on the internet is part of KA5 (Section 14.2). |
| KD15 | Root page and directory (owner review, 2026-11-19) | The root page has no list of the federated servers: there is no reason to make it public, and the admin sees the known servers in the console. A public list of the blocked domains can be more useful; it has its own proposal ([`PUBLIC_DOMAIN_BLOCK_LIST_DESIGN.md`](PUBLIC_DOMAIN_BLOCK_LIST_DESIGN.md)). The public account directory is useful at its own path: `/directory`, in both modes. The path `/explore` is retired (the namespace design §4.4 is retired). The form of the recent activity across servers waits for an evaluation of prototypes ([`prototypes/recent_activity_prototypes.html`](prototypes/recent_activity_prototypes.html)). |

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

**In Claire:** the relay is an idea outside this design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3).

### 3.3 Retired Term "Partnership"

Rev. 1 to Rev. 3 used "partnership" for a bilateral agreement of two admins. The term is retired (CT36). [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) keeps where its parts went and the ideas that came from it.

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

The sidecar uses Fedify 2.4.6 (`deno.json` pins `~2.4.6`), the latest release on JSR on 2026-11-22 **[code]** FEDERATION.md §9.3. Work for this design uses the latest Fedify release at the time of the work. A fact about an older Fedify version in a tutorial or a report does not apply.

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
| P1 | PeerTube | The platform actor (`Application`) follows other platforms; the followed admin can require manual approval. | The relay idea ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3). |
| P2 | PeerTube | An admin can follow the platforms of an index list automatically. | An index list gives suggestions of known servers only (Section 6.2). |
| P3 | PeerTube | A following platform receives metadata with links to the files, not the files. | `/kb` collections carry metadata and signatures, not documents (Section 8.1). |
| P4 | PeerTube | A mirror server tells the origin with a `CacheFile` object that it holds a copy; the origin controls who may mirror. | The alternative to knowledge base actors (Section 9.2, rule 6). |
| P5 | PeerTube | A channel is a `Group` actor that several accounts manage. | A shared knowledge base as an actor (Section 9.2). |
| M1 | Mobilizon | Servers subscribe to each other through an internal `relay` actor; the other admin approves; only public content created after the subscription is sent. | A full read at first contact (Section 8.1); the relay idea ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3). |
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
| N3 | NeoDB | An optional relay sends public activities between servers. | The relay idea ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3). |
| I1 | Inventaire | Entities have `wd:` (Wikidata) and `inv:` (local) identifiers; data is CC0. | Authority identifiers of entities (Section 10.4). |

### 5.3 Common Patterns

1. **Exchange without an agreement; one-sided admin controls** (Mastodon): Section 6.
2. **A relay as an opt-in hub for discovery** (relay software, NeoDB, Mobilizon): an idea outside this design ([`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3).
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
3. the admin adds its domain in `/admin/federation`.

The server reads NodeInfo and the server actor of a known server and keeps its software, version, counts, last read, and errors. An index list of Claire servers (P2) and the known servers of known servers are suggestions in the console; the admin adds them by hand.

### 6.3 Admin Controls

All controls are one-sided, as in Mastodon (Section 3.1).

| Control | Effect |
| :--- | :--- |
| `federation` (existing switch) | Off: no exchange of shared knowledge. |
| Domain block (existing) | No exchange with that domain. Copies from it stay (CT7, CT19); their revisions stop. |
| `kb_auto_contribution` | Off: the accounts of this server send no automatic contributions, and this server accepts none (Section 12.3). |
| Signed GET | Collections of shared knowledge answer only signed requests of server actors, so that domain blocks apply to reads (Section 14). |

A level "limit" for domain blocks (offers from that domain always wait for approval; its knowledge bases do not join local themes) is decided as a later step after the existing block (KD8).

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
| Suggestions | Known servers of known servers, index lists |
| Journal | The inbox journal of Section 13, with replay of a failed activity |

Layout ([`CONSOLE_LAYOUT_DESIGN.md`](CONSOLE_LAYOUT_DESIGN.md) §5): the section `/admin/federation` has four tabs. "Switches and state" has the switches, the existing readiness checks, and the existing sidecar state. "Known servers" is a list with the detail of the selected server and the field to add a domain. "Blocks and suggestions" has the domain blocks and the suggestions. "Inbox journal" has the journal. The relay has no area (Section 7).

---

## 7. Relay (Idea)

The relay is not part of this design. [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) §3 keeps it as an idea (owner, Rev. 5).

---

## 8. `/kb` Collections and Themes Across Servers

### 8.1 Reading the `/kb` Collection of Another Server

1. The `/kb` collection of a server (shared knowledge base design §7) lists its shared knowledge bases: label, description, knowledge type, counts, the signature of §8.2 of that design, and the IRI. No documents (P3).
2. At the first contact with a known server, this server reads its `/kb` collection once in full with a signed GET. Later changes come from the knowledge base actors (Section 9.2) or from a new read each day.
3. This server keeps the records in a store of remote knowledge bases. A remote knowledge base is not a partition of the local `claire.db` and has no documents until a curation or a contribution.
4. Public personal knowledge bases are not in the `/kb` collection (KD3).
5. Themes are not exchanged: each server computes its own themes and links to the knowledge bases of other servers.

### 8.2 Themes Across Servers (CT35)

1. The subject judgement of each server includes the signatures of remote shared knowledge bases. A remote knowledge base can be a member of a local theme.
2. The same subject can have different theme numbers on different servers. A theme number never leaves its server as an identifier.
3. The theme page shows local and remote knowledge bases. A remote knowledge base links to its origin.

### 8.3 Root Page (Network Mode) and Public Account Directory

1. Themes first, with the counts of local and remote shared knowledge bases.
2. **Public account directory (KD15).** The accounts with a public profile are at `/directory`, in both server modes (HTML for a browser, the collection contract of the namespace design §7.4 for an API client). The root page shows the first accounts and links to `/directory`. The page has no list of the federated servers (KD15): the known servers are in the admin console only.
3. Recent activity across servers: new shared knowledge bases of other servers, contributions that other servers approved from accounts of this server, curations. Because account identity is always open for active accounts with a USERNAME (GD8, withdrawing N1), recent activity includes any `active` account with a USERNAME regardless of `profile_public`. The current form is a plain list. The final form waits for an evaluation of prototypes (KD15, [`prototypes/recent_activity_prototypes.html`](prototypes/recent_activity_prototypes.html)).
4. The page "Make a shared knowledge base" shows similar local and remote shared knowledge bases before the account makes a new one (B3).

Single mode shows no root page (Section 15). The public account directory (rule 2) and the collections `/themes` and `/kb` are in both modes.

---

## 9. Objects and Actors

### 9.1 Vocabulary

1. Claire publishes one JSON-LD context for its types from the first version (B9). The context IRI is the same for all Claire servers: `https://w3id.org/claire-bible/ns/v1`; the vocabulary terms are `https://w3id.org/claire-bible/ns#…` (KD9). Each server also serves a copy at a local path. Claire servers read the compact names; they do not need a JSON-LD processor.
2. **[proposal]** Claire never fetches the context over the network: the context document is part of the code. The document of `v1` never changes; new terms go to a new version.
3. **The target of the redirect.** `w3id.org` sends everything under `/claire-bible/` with 302 to the same path on `https://claire.bible/`. Thus the server on `claire.bible` serves the context document at `/ns/v1` (media type `application/ld+json`, header `Access-Control-Allow-Origin: *`) and a page of the vocabulary terms at `/ns`. Other Claire servers serve the same documents at the same local paths.
4. **Registration (owner).** The owner adds the directory `ids/claire-bible/` to the repository `perma-id/w3id.org` with a pull request. Its guide asks that the target is live over HTTPS before the pull request. When `claire.bible` cannot be used (Section 16, review of PQ2), a new pull request changes the target; the identifiers stay.
5. The vocabulary document is written for Claire only, in our own words (Section 4.2).

| Type | Base type | Content |
| :--- | :--- | :--- |
| `claire:KnowledgeBase` | `Group` with a collection of documents (KD5) | A shared knowledge base: label, description, knowledge type, counts, signature, documents collection, `followers` |
| Document | `Article` | Title, content, canonical URL, content hash, `attributedTo` (the actor with the credit), `origin` (the object it was copied from), `claire:root` (the root document) |
| `claire:Extraction` | `Object` | Entities (keys `type` and `norm_name`, aliases, `sameAs` with authority identifiers) and relations of one document, so that the receiver builds its graph without an LLM call |
| `claire:Contribution` | `Object` | A document with its extraction, offered to one shared knowledge base |

### 9.2 Knowledge Bases as Actors (KD5)

1. Each shared knowledge base has a `Group` actor with an inbox, an outbox, and `followers`. Its keys are in the sidecar like the other keys.
2. A server that holds copies of the documents of a knowledge base, or that has it in a local theme, follows that actor with its server actor.
3. The knowledge base actor sends `Update` of its own record, `Update` of a document (revision), and `Delete` (removal at the origin) to its followers.
4. A contribution `Offer` goes to the inbox of the knowledge base actor (F1).
5. Followers are only server actors of Claire servers in this design. A follow of a person of the open fediverse is idea I1 and stays outside this design; the same actor can serve it later.

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

Curation and the first read use signed GET requests of the collections of the other server.

---

## 10. Copies, Revisions, and Entities

### 10.1 Copies

1. A copy (curation or accepted contribution) is a new object on the holder with a new IRI. It keeps `origin`, the IRI of the actor with the credit, and `claire:root` (F2).
2. The holder hosts and controls its copy (F2). It never sends the content of a remote object as its own object: its collections show the copy with `origin` (N2).
3. When a domain block starts, copies from that domain stay (CT7, CT19). Revisions stop because the channel stops.

### 10.2 Revisions

The knowledge base actor of the origin sends `Update` for each revision of a document to its followers. The holders apply it automatically, as in CT20 and CT26 of the shared knowledge base design.

### 10.3 Removal at the Origin (KD1)

The holder hides the copy at once and puts it in its review queue with the reason code of the origin. Its reviewers decide to remove it or to show it again. A copy is not removed automatically, because a removal at the origin can have a local reason; the hide at once protects the holder when the reason is legal or spam. A removal on the holder never changes the origin (L2).

### 10.4 Entities Across Servers

1. Each server resolves entities in its own merge job (CT24). No server changes the graph of another server. Different results on different servers are normal.
2. **Authority identifiers** (KD7). An entity can carry identifiers of an authority in `sameAs`: Wikidata (`wd:Q…`), ISBN, ORCID, and similar (I1, N1). The merge job of the receiver matches an entity with the same authority identifier first, then by keys. v1 sends an identifier only when the entity already has one; a function that links entities to an authority is later work.
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

### 12.2 Remote Collaborators (F3, L2) (KD6)

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
| KS5 | Private data leaves the server | Only shared knowledge bases (always public) and their visible documents leave the server. Personal knowledge bases are not in `/kb` (KD3). No IdP data goes into federation (existing rule). |
| KS7 | A domain block or a switch change by mistake or with a stolen token | Recent sign-in for each admin action; `admin_audit`. |
| KS8 | A grant is used after it should end | End time of 6 months; `Revoke`; each use checks the grant (Section 12.2). |
| KS9 | An activity is processed twice or lost | Inbox journal with idempotent processing (Section 13). |

| Switch (`server_settings`, all in `/admin/federation`) | Values | Default |
| :--- | :--- | :--- |
| `federation` (exists) | `off`, `on` | `off` today; `on` when the ActivityPub functions are complete (owner direction). It also turns on the federation of shared knowledge. **Removed** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (2026-11-22): federation has no switch. |
| `kb_auto_contribution` | `off`, `on` | `off` until the admin turns it on (KD4) |

The switch `partnerships` of Rev. 1 to Rev. 3 is removed.

### 14.1 Readiness Checks of the Knowledge Federation (KD11)

The readiness checks GF1 to GF7 ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 7.2) examine the federation of accounts: the server mode, the sidecar heartbeat with the contract version, the service key, the master key, https in production, the WebFinger path through the public origin (GF6), and the OAuth metadata through the public origin (GF7) **[code]**. They do not examine the paths, the keys, the loops, and the settings that the knowledge federation uses (KD11). The checks GF8 to GF17 of this section examine them; unit KF6 implements them ([`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) Section 17). Decisions: KD10, KD11, and KD13.

#### 14.1.1 Rules for All Checks

1. **Small and decidable.** Each check examines one thing. It gives "passed" or "failed" and a detail text that tells what to examine.
2. **No request to another server.** A check reads the database, a file, or the heartbeat record, or it sends requests to the public origin of this server only, as GF6 and GF7 do. The public origin is the configured public URL (`WebRuntimeConfig.public_origin` in Claire, `canonical_origin` of the config in the sidecar).
3. **No change of data by Claire.** A check of Claire writes nothing. GF14 and GF15 read an actor document through the public origin; as each first read by another server, this read can make the keys of that actor ([`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) Section 12.2). The self-check of the sidecar stores nothing (Section 14.1.4).
4. **Two sources.** A check of Claire runs in the process that makes the report: the `api` process for `GET /api/admin/federation`, the CLI process for `claire server check --federation`. A check of the sidecar runs in the sidecar at each heartbeat, and Claire reads its result in the heartbeat record, as GF6 does.
5. **Client of the requests of Claire.** The rules of GF7 apply: no redirects, no proxy settings of the environment, a timeout of 5 seconds for each request, and a limit for the size of each answer. The requests trust the certificate authorities of the system trust store of the process (in the image: the public authorities and the files of `/extra-certs`, which the entrypoint adds **[code]** `scripts/docker-entrypoint.sh`). Today GF7 uses `httpx` with `trust_env=False`; `httpx` 0.28.1 then trusts only the bundle of `certifi` **[code]**. Thus a server behind a private certificate authority fails GF7, for example a server of the development network ([`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) Section 6.4). This rule applies to GF7 too. The checks with requests run at the same time, with a limit of 20 seconds for all of them; a check that does not end in that time fails with the detail "The check did not end in 20 seconds."
6. **Group, level, and scope.** Each item has `group` (`account` for GF1 to GF7, `knowledge` for GF8 to GF17), `level` (`required` or `warning`), and `applies` (true or false). An item that does not apply has `passed` true and a detail that tells why. Each check sets the three values itself; no value has a default. `report.passed` is true when each `required` item passes. A `warning` item never changes `report.passed`.
7. **Modes and switches.** Each check applies in network mode and in single mode, because the mode is not a condition of the knowledge federation (Section 15 rule 5). GF14, GF15, and GF16 apply only while the switch `federation` is `on`, because the sidecar gives 404 for each federation path while the switch is `off` **[code]**. The other checks apply in each state of the switch, so that they can pass before the admin sets it to `on`. No check needs `kb_auto_contribution` `on` (KD4): GF13 examines its value, and GF15 compares it with the actor document.
8. **Gate.** **[decision, KD13]** The change of `federation` to `on` (`POST /api/admin/federation`) needs each required item that applies at that time: GF1 to GF7 and GF8 to GF13. GF14 to GF16 apply after the change, and the acceptance path needs them (Section 14.2). A required item that fails while the switch is `on` starts no automatic action; the report shows it, as for GF1 to GF7 today. **Withdrawn** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (2026-11-22): the checks are a report of the state.
9. **Text.** The API and the CLI give the detail in English (ASD-STE100), as GF1 to GF7 do. Each item also has a `code` (for example `paths_not_routed`) and the values of its detail. The console shows the text of the catalog key of that code in English or Korean ([`I18N_DESIGN.md`](I18N_DESIGN.md) ID5). Unit KF6 gives codes to GF1 to GF7 too, so that the console has one method to show an item. The CLI is not localized (ID6).

#### 14.1.2 Summary

| ID | Name | Source | Requests to the public origin | Applies | Level |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GF8 | Knowledge federation paths reach this sidecar | Sidecar self-check | Sidecar: 7 GET | Always | Required |
| GF9 | Signed request through the public origin | Sidecar self-check | Sidecar: 1 POST | Always | Required |
| GF10 | Inbox body of the largest size through the public origin | Claire | 1 POST | Always | Required |
| GF11 | Knowledge federation loops of the sidecar make progress | Heartbeat | None | Always | Required |
| GF12 | Actor keys can be decrypted | Claire | None | Always | Required |
| GF13 | Settings of the knowledge federation and the public name | Claire | None | Always | Required |
| GF14 | First-contact documents through the public origin | Claire | 3 GET | Switch `on` | Required |
| GF15 | Knowledge base actor document through the public origin | Claire | 1 GET | Switch `on` and a shared knowledge base exists | Required |
| GF16 | Collections refuse an unsigned read | Claire | 2 or 3 GET | Switch `on` | Required |
| GF17 | JSON-LD context and vocabulary page through the public origin | Claire | 2 GET | Always | Warning |

Not a check: the clock of the server. Other servers refuse an HTTP signature with a time that is more than one hour wrong **[code]** (KNOWLEDGE_FEDERATION.md Section 14.2), but a check of the clock needs a request to another time source, and rule 2 does not permit that.

#### 14.1.3 The Checks

**GF8: Knowledge federation paths reach this sidecar.**

- Shows: the reverse proxy sends each path of the knowledge federation to this sidecar process. GF6 shows this for `/.well-known/webfinger` only; a proxy can have a rule for that path and no rule for `/nodeinfo/` or `/ap/`.
- How (sidecar, at each heartbeat): one `GET {origin}{path}` for each path of this list: `/.well-known/nodeinfo`, `/nodeinfo/2.1`, `/ap/actors/server`, `/ap/kb`, `/ap/themes`, `/ap/kb/0` (a path below `/ap/kb/`; 0 is never the number of a knowledge base), and `/ap/inbox`. No redirects, a timeout of 10 seconds, all requests at the same time. A path passes when the answer has the header `X-Claire-Fedify-Server` with the value of this sidecar process **[code]** (FEDERATION.md Section 7.2). The status of the answer is not important, so that the check passes also while the switch is `off` (each answer is then 404).
- Passed: the heartbeat is recent (the limit of GF2), and its `knowledge_self_check.failed_paths` is an empty list.
- Details on failure: "There is no recent sidecar heartbeat. The result of the knowledge self-check is not known." / "The sidecar did not do the knowledge self-check yet." / "The public origin does not send these paths to this sidecar: {paths}. Examine the reverse proxy rules: exact /.well-known/nodeinfo, prefix /nodeinfo/, prefix /ap/."
- Requests: from the sidecar to the public origin of this server only.

**GF9: Signed request through the public origin.**

- Shows: a signed `POST` goes through the reverse proxy to this sidecar without a change of the parts that a signature covers: the `Host` header, the `Date` header, the digest headers, the signature headers, and the body. Other servers verify each delivery and each signed read with these parts; FEDERATION.md Section 10 tells the proxy to keep them **[code]**.
- How (sidecar, at each heartbeat): at its start the sidecar makes one key pair in memory, the self-check key. It is not an actor key, it is not in `auth.db`, and it never leaves the process. The sidecar sends one `POST {origin}/ap/self-check` with a small JSON body that contains a random value (the nonce). It signs the request with the self-check key and with the same function as a delivery (`signRequest` of Fedify). The handler of `/ap/self-check` in the same process (Section 14.1.4) verifies the request with the same function as an inbox (`verifyRequest` of Fedify) and only the self-check key, compares `Host` with the authority of the public origin, and records the result for the nonce in memory. The check reads that result. While the switch `federation` is `off`, the sidecar cannot get the actor keys **[code]** (FEDERATION.md Section 5.3); the self-check key makes the check possible before the admin sets the switch to `on`. The keys of the actors are examined by GF12, GF14, and GF15.
- Passed: the result of the last self-check is `ok`.
- Details on failure, for each result code: `not_routed`: "The signed self-check request did not reach this sidecar. Examine the reverse proxy rules for the prefix /ap/." / `host_changed`: "The reverse proxy changed the Host header. It must send the Host of the request without a change, because the signature includes it." / `signature_invalid`: "The signature of the self-check request did not verify after the reverse proxy. The proxy must send the body and the headers Date, Digest, Content-Digest, Signature, and Signature-Input without a change." / `tls_error`: "The sidecar cannot verify the TLS certificate of the public origin. Give the sidecar the certificate authority of the origin." / `dns_error`: "The sidecar cannot resolve the name of the public origin." / `request_failed`: "The self-check request to the public origin failed ({error})." The two texts of GF8 for a heartbeat that is not recent and for a self-check that was not done also apply.
- Requests: from the sidecar to the public origin of this server only.

**GF10: Inbox body of the largest size through the public origin.**

- Shows: the reverse proxy accepts a request body of the largest size that the sidecar accepts on an inbox. A proxy with a lower limit refuses a large contribution (an `Offer` with a document and its extraction) with 413 before the sidecar gets it.
- How (Claire): one `POST {origin}/ap/inbox` without a signature, with `Content-Type: application/activity+json` and a body of `inbox_body_bytes` minus 8192 bytes (1040384 bytes now) **[code]** (FEDERATION.md Section 5.3, KNOWLEDGE_FEDERATION.md Section 10.6 item 1). The sidecar refuses the request (401 without a signature, or 404 while the switch is `off`) and does not give it to Claire.
- Passed: the answer has the header `X-Claire-Fedify-Server` (any value), and its status is not 413.
- Details on failure: "The public origin refused an inbox body of {n} bytes (HTTP {status}). The reverse proxy must accept a request body of {n} bytes or more on the paths below /ap/." / "The answer did not come from a sidecar (no header X-Claire-Fedify-Server). Examine the reverse proxy rules for the prefix /ap/." / "The request to the public origin failed ({error})."
- Requests: from Claire to the public origin of this server only. The sidecar can write a log line for the refused request **[unverified]**.

**GF11: Knowledge federation loops of the sidecar make progress.**

- Shows: the two loops of the knowledge federation run and reach Claire: `knowledge_reads` (the reads of known servers and of `/kb` collections, each 60 seconds) and `knowledge_exchange` (the reads for curations and the outbound activities, each 5 seconds) **[code]** (`sidecar/fedify/src/main.ts`). A loop asks Claire for work also when there is no work, so the check works on a server that knows no other server. The age of the last lease is not used, because a lease exists only when there is work.
- How (heartbeat): for each loop the heartbeat has `running`, `progress_at` (the end of the last step or pass that ended without an error), the last error code, and its time (Section 14.1.4).
- Passed: for each loop, `progress_at` is at most 300 seconds old. The limit is more than the longest step, the read of a `/kb` collection, which has a limit of 240 seconds **[code]** (KNOWLEDGE_FEDERATION.md Section 11.4).
- Details on failure: "The sidecar heartbeat has no state of the knowledge federation loops." / "The loop {loop} of the sidecar made no progress in the last 300 seconds. Last error: {code, or none}."
- Requests: none.

**GF12: Actor keys can be decrypted.**

- Shows: the master key of this server decrypts each private key in `actor_keys` (`auth.db`): the server actor (0), the account actors, and the knowledge base actors (negative numbers) **[code]** (FEDERATION.md Section 6, KNOWLEDGE_FEDERATION.md Section 12.2). GF4 shows only that the master key loads. After a restore with a different master key, no actor key decrypts, and the sidecar cannot sign a delivery or a signed read.
- How (Claire): it reads each row and decrypts the private key with the additional authenticated data of its row. It makes no key.
- Passed: each row decrypts. A server with no row passes, with the detail "There is no actor key yet."
- Details on failure: "{n} actor keys cannot be decrypted with the master key (actors: {at most five numbers}). The master key is not the key that encrypted them." / "The actor key store cannot be read ({error})."
- Requests: none.

**GF13: Settings of the knowledge federation and the public name.**

- Shows: the stored values of the knowledge federation are valid, so that no loop and no handler stops with an error of the data, and other servers can resolve the public name.
- How (Claire, read only): (1) `federation` and `kb_auto_contribution` are `off` or `on`. (2) Each key of `contributions.LIMITS` that has a row in `server_settings` has an integer of its lowest value or more (the limits of KNOWLEDGE_FEDERATION.md Sections 13.4 and 14.1); today `read_limits` raises an error at run time for such a row **[code]**. (3) `theme_match_threshold`, `auto_match_threshold`, and `auto_run_documents` read without an error with their rules. (4) In production, the host of the public origin is not below a special-use name: `test`, `example`, `invalid`, `localhost` (RFC 6761), or `local` (RFC 6762). In development, a name below `test` is the rule of the development network.
- Passed: all four parts.
- Details on failure: "The server setting {key} has a value that is not valid: {value}." / "The host {host} of the public origin is below the special-use name {suffix}. Other servers cannot resolve it."
- Requests: none.

**GF14: First-contact documents through the public origin.**

- Shows: another server gets what it reads at its first contact with this server **[code]** (KNOWLEDGE_FEDERATION.md Section 10.6 item 3): NodeInfo, the document of its link, and the server actor with its key.
- How (Claire): `GET {origin}/.well-known/nodeinfo` gives 200 and JSON with a link whose `href` is `{origin}/nodeinfo/2.1`. A `GET` of that link gives 200 and JSON with `software.name` `claire-bible`, the version of this Claire, and `activitypub` in `protocols`. `GET {origin}/ap/actors/server` with `Accept: application/activity+json` gives 200, the type `Application`, the `id` `{origin}/ap/actors/server`, an `inbox` on the origin, `publicKey.owner` equal to `id`, and a `publicKey.publicKeyPem` that is the stored public key of the server actor.
- Passed: all steps.
- Details: not applicable while the switch is `off`: "The switch federation is off. The sidecar gives 404 for each federation path while it is off." / on failure: "NodeInfo through the public origin: {problem}." / "The server actor through the public origin: {problem}." / "The public key of the server actor through the public origin is not the stored key. Examine caches between the reverse proxy and the sidecar."
- Requests: from Claire to the public origin of this server only.

**GF15: Knowledge base actor document through the public origin.**

- Shows: another server can read the actor of a shared knowledge base, verify its signatures, and find the addresses for a follow and an offer.
- How (Claire): the shared knowledge base with the lowest number. `GET {origin}/ap/kb/{n}` gives 200 and `application/activity+json`; the type has `Group` and `claire:KnowledgeBase`; `id` is `{origin}/ap/kb/{n}`; `inbox` is `{id}/inbox`; `followers`, `outbox`, and `claire:documents` are on the origin; `publicKey.owner` is `id`; `publicKey.publicKeyPem` is the stored public key of that actor; `claire:acceptsAutomatic` is true only when `federation` and `kb_auto_contribution` are both `on` now **[code]** (KNOWLEDGE_FEDERATION.md Sections 4.1, 12.2, and 15.5).
- Passed: all parts.
- Details: not applicable: the text of GF14 for the switch, or "There is no shared knowledge base." / on failure: "The actor document of the knowledge base {n} through the public origin: {problem}." / "claire:acceptsAutomatic is {value} in the document, but the switches give {expected}. A cache between the reverse proxy and the sidecar can give old documents."
- Requests: from Claire to the public origin of this server only.

**GF16: Collections refuse an unsigned read.**

- Shows: control KS1 works through the reverse proxy. A cache in front of the sidecar that keeps a signed answer can give it to each client, and thus go around domain blocks.
- How (Claire): `GET {origin}/ap/kb`, `GET {origin}/ap/themes`, and, when a shared knowledge base exists, `GET {origin}/ap/kb/{n}/documents` for the lowest number, each without a signature and with `Accept: application/activity+json`.
- Passed: each answer is 404.
- Details: not applicable: the text of GF14 for the switch / on failure: "The public origin gave HTTP {status} for an unsigned read of {path}. A collection of shared knowledge must give 404 to a request without a signature (KS1). Examine caches in front of the sidecar."
- Requests: from Claire to the public origin of this server only.

**GF17: JSON-LD context and vocabulary page through the public origin.**

- Shows: the reverse proxy gives `/ns/v1` and `/ns` of Claire without a change (Section 9.1 rule 3).
- How (Claire): `GET {origin}/ns/v1` gives 200 without a redirect, `Content-Type: application/ld+json`, `Access-Control-Allow-Origin: *`, and a body that is the context document of this version (`static/ns/v1.jsonld`). `GET {origin}/ns` gives 200 and `text/html`. The routes work in each state of the switch **[code]** (KNOWLEDGE_FEDERATION.md Section 7.1).
- Passed: both requests.
- Details on failure: "GET /ns/v1 through the public origin: {problem}." / "GET /ns through the public origin: {problem}." The problems are: the status, a redirect, the media type, no CORS header, or a body that is not the context document of this version.
- Requests: from Claire to the public origin of this server only.
- Level: warning, because Claire servers never get the context from the network (Section 9.1 rule 2); software of other kinds can. Step KA4 needs it on the server that hosts the context (Section 14.2).

#### 14.1.4 Contract Change with the Sidecar

1. **Heartbeat.** `POST /internal/federation/v1/heartbeat` gets two necessary fields. Times are Unix seconds; a code is 1 to 64 characters of `a-z`, `0-9`, and `_`.

   ```json
   {
     "knowledge_self_check": {
       "checked_at": 1800000300.0,
       "failed_paths": [],
       "signed": {"result": "ok"}
     },
     "loops": {
       "knowledge_reads": {"running": false, "progress_at": 1800000290.0, "error": null, "error_at": null},
       "knowledge_exchange": {"running": true, "progress_at": 1800000299.0, "error": null, "error_at": null}
     }
   }
   ```

   Before the first self-check, `checked_at`, `failed_paths`, and `signed.result` are null. `signed.result` is `ok` or one of the codes of GF9. The other fields of the heartbeat do not change.
2. **Self-check path.** The sidecar gets the public path `POST /ap/self-check`. It answers 202 only for a request that this process signed with its self-check key in the last 60 seconds, with a nonce that it did not see before; each other request gets the same 404 as an unknown path. It answers in each state of the switch `federation`, because GF9 must pass before the switch is `on`. It never calls Claire and stores nothing; the result of a nonce stays in memory for 60 seconds. The prefix `/ap/` of the reverse proxy rules (FEDERATION.md Section 10) already sends the path to the sidecar.
3. **Cadence.** The knowledge self-check runs at each heartbeat (each 30 seconds), as the self-check of GF6, also while the switch is `off`. Its requests run at the same time, with a limit of 20 seconds, so that a heartbeat is not late. Thus the age of the result is the age of the heartbeat.
4. **Version.** The contract version becomes 1.12. The rules of FEDERATION.md Section 5.4 apply: Claire and the sidecar come from the same commit, and the shapes are sealed. A sidecar of version 1.11 sends a heartbeat without the new fields; Claire refuses it (400 `invalid_request`), records nothing, and GF2 fails. An absent field gets no default value.
5. **Fixtures.** The contract fixtures of the heartbeat get the new fields; the Python and Deno tests read the same files, as today.

#### 14.1.5 Report and Console

- `GET /api/admin/federation`: each item of `readiness.items` has `group`, `level`, `applies`, and `code`; `readiness.passed` follows rule 6 of Section 14.1.1; `heartbeat` has the new fields.
- `POST /api/admin/federation` with `on`: the gate of rule 8; `failures` has each required item that applies and fails.
- CLI: `claire server check --federation [--json]` gives all items in the two groups. The exit code is 0 only when `passed` is true. The help text names GF1 to GF17.
- Console `/admin/federation`, tab "Switches and state" (Section 6.5): the readiness table has two groups, each with a heading row and its own summary: "Federation of accounts (GF1 to GF7)" and "Federation of shared knowledge (GF8 to GF17)". A column shows the level (required, warning). An item that does not apply shows "Does not apply" and its reason. The sidecar card shows each loop of GF11 (the age of its progress and its last error). The new text has catalog keys in `en` and `ko` (ID5).

### 14.2 Acceptance Path of the Knowledge Federation (KD10)

The order of the steps is the direction of the owner (2026-11-18): GF1 to GF7 pass; then GF8 and later pass; then a real exchange between two servers with real sidecars; then the check of the JSON-LD context before it is hosted; then the calibration of thresholds and limits with real data. The content of each step is a **[proposal]**.

| Step | Content | Completion criteria |
| :--- | :--- | :--- |
| KA1 | GF1 to GF7 on each server of the exchange | `claire server check --federation --json` gives "passed" for GF1 to GF7 on server A and on server B of the development network ([`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) phase DE4) |
| KA2 | GF8 to GF17 | Unit KF6 is complete. With the switch `off`, GF8 to GF13 pass on A and B; the admin of each server sets the switch to `on`; then GF14 to GF16 pass on A and B; GF17 passes, or its warning has a known reason |
| KA3 | Real exchange between two servers | Each completion criterion of KF1 to KF5 (Section 17) passes between A and B with real sidecars, real TLS, real DNS, and the reverse proxy rules of FEDERATION.md Section 10. The tests act only through the public routes and the federation paths: no direct call of an internal route and no direct row in a database. One test with the marker `devnet` for each criterion. The tests of today simulate the sidecars and the network **[code]** (KNOWLEDGE_FEDERATION.md Sections 10.7, 11.8, 12.10, 13.9, 15.8) |
| KA4 | Check of the JSON-LD context before it is hosted | A JSON-LD processor expands each fixture of `tests/fixtures/knowledge_federation/objects/` and `activities/` with the context `v1` and compacts it again to the same compact names, and no term is lost. The IRIs of `Grant`, `Revoke`, and `capability` agree with the ForgeFed specification (today they come from memory, KNOWLEDGE_FEDERATION.md Section 8 item 3). The owner reads the vocabulary page. Then `v1` is frozen (Section 9.1 rule 2), `claire.bible` serves `/ns/v1` and `/ns` and GF17 passes there, and the owner opens the pull request on `w3id.org` (Section 9.1 rule 4, KF0) |
| KA5 | Calibration of thresholds and limits with real data | `theme_match_threshold` (the attachment of a remote knowledge base to a local theme), `auto_match_threshold`, `auto_run_documents`, the limits of KNOWLEDGE_FEDERATION.md Sections 13.4 and 14.1, and the time limits of the sidecar (Section 14.5 there) are compared with the data of servers that federate. The owner collects the numbers on servers of the owner. An agent works only with the numbers that the owner gives, because agents do not operate production. Each new default is a decision of the owner with its data |

Rules:

1. A step starts only after the step before it is complete.
2. A change of a path, a key, a loop, a setting, or the reverse proxy rules in a later step repeats KA1 and KA2.
3. KA1 to KA3 use the development network of two servers (KD12; scope: KD14). KA4 and KA5 need servers of the owner on the internet; the owner does the steps on production.

---

## 15. Single Mode (CT33, confirmed)

1. **USERNAME.** The first setup sets the USERNAME of the admin (namespace design §8.4 already requires it). The production server already has one.
2. **Root.** `/` sends a temporary redirect (302) to `/@{username}/`. It is temporary because the meaning of `/` depends on the server mode, which the admin can change. Other root paths of the personal knowledge base of single mode get 308 to `/@{username}/…`; these targets stay valid in network mode.
3. **Setup not complete.** While the admin account has the state `setup_required`, `/` with the admin credential goes to `/setup` (as today); other requests get a page "setup not complete". There is no other target.
4. **USERNAME exposure.** Rule S9 of the namespace design (no USERNAME in single mode) is withdrawn. The test `tests/test_single_mode_no_username_leak.py` is replaced by a test of the redirects. The setup confirmation says that the USERNAME is public in both modes.
5. **Federation in single mode.** The server actor and the federation of shared knowledge work in single mode (there is no federation switch, GD4). The account actor of the admin federates in single mode with the same rules as network mode (KD2, GD5 to GD8): `profile_public` controls only discovery and search indexing (not actor status, KB offers, published KB `Article`s, or public contributor credit), changing `profile_public` needs no step-up authentication, and publishing KB `Article`s follows KB `is_public` plus active actor status. The account routes of the federation (`/api/federation/*`, the timeline, notes, and publishing), `/account/profile`, `/account/federation`, and `/syndication` are the same in both modes; only the routes for more than one account (invites, member keys) are in network mode only (2026-11-19).
6. **Mode change.** The refusal of `network` → `single` while `federation` is `on` (FEDERATION.md §8.3) changes, because federation can be on in single mode. A change to single mode still needs no members.

---

## 16. Open Questions

Resolved in Rev. 4: PQ1 (two levels of agreement) and PQ10 (need of a partnership) by the owner direction of Section 2: federation without an agreement, relay for the server-level subscription. PQ11 (the term "directory") is applied with the unification of terms: the text uses "the `/kb` collection of another server"; the owner can reverse it. PQ12 (relay) moved to [`RETIRED_PARTNERSHIP_IDEAS.md`](../ideas/RETIRED_PARTNERSHIP_IDEAS.md) in Rev. 5.

Answered on 2026-11-16: PQ3 (KD1), PQ4 (KD2), PQ5 (KD3), PQ6 (KD4), PQ7 (KD5), PQ8 (KD6), PQ9 (KD7), PQ13 (KD8), PQ2 (KD9).

Answered on 2026-11-19: PQ14 (KD13), PQ15 (KD14). No open questions remain.

### PQ2: The IRI of the Claire JSON-LD Context (Section 9.1), Answered (KD9)

**Owner proposal (2026-11-16):** the domain `claire.bible`. It is the first server, and the maintainer of the project manages it. When this server stops and the domain is not renewed, the maintenance of the project has ended.

**Review (2026-11-16):**

1. **Effect on Claire servers.** Small. Claire servers do not fetch the context (Section 9.1, rule 2), so a loss of the domain does not stop the exchange between Claire servers. The IRI stays an identifier.
2. **Registry policy.** The `.bible` registry is the American Bible Society **[external]** (`nic.bible`). Its Acceptable Use Policy (version of 2018-02-22, read 2026-11-16) says, in summary:
   - Its content rules apply to all content published with a `.bible` domain and to links from it to external content. Prohibited content includes content that advocates a faith tradition other than orthodox Christianity or Judaism, content that is disrespectful to the Bible, and content that can harm the reputation of the registry or the `.bible` name space.
   - The registry can, at its own discretion and without notice, suspend, cancel, or end a registration, and can redirect or divert the traffic of a domain.
   - Third-level domains need the permission of the registry; a list of common labels (for example `www`, `kb`, `help`) needs none.
3. **Consequence.** The proposal makes "the domain stops" mean "the maintenance ended". The policy adds a second cause that the maintainer does not control: a suspension or a redirect by the registry while the project is alive. A Claire server shows content that its accounts ingest (any subject, fiction, other faiths) and links to it, so this cause is real for a server on `claire.bible`. When the registry redirects the domain, a JSON-LD processor of other software that fetches the context can get a different document under the Claire IRI.
4. **Alternative.** `w3id.org` is a permanent URL redirection service of the W3C Permanent Identifier Community Group, operated by a group of organizations **[external]**. It separates the identifier from the host: a project adds redirect rules with a pull request and can change the target later. Fediverse vocabularies use it (for example `https://w3id.org/security/v1`). The paths `w3id.org/claire` and `w3id.org/claire-bible` were free on 2026-11-16.

| Option | Identifier | When `claire.bible` stops or is suspended |
| :--- | :--- | :--- |
| A (owner proposal) | `https://claire.bible/ns/v1` | The identifier stays valid for Claire servers; other software can get a wrong document. |
| B (proposal) | `https://w3id.org/claire-bible/ns/v1`, redirect to `https://claire.bible/ns/v1` | While the project is alive, a pull request moves the redirect to another host. When the maintenance ends, the redirect can point to an archive. |

**Proposal:** B. The rule of the owner stays true for the project home and the first server; the identifier does not depend on the policy of one registry. The registration on `w3id.org` is a step of the owner (a pull request).

**Answer (owner, 2026-11-16):** B (KD9).

### PQ14: Do GF8 to GF13 Gate the Switch `federation`? (Section 14.1.1 rule 8), Answered (KD13)

The one switch `federation` turns on the federation of accounts and the federation of shared knowledge (Section 14).

| Option | Rule | Effect |
| :--- | :--- | :--- |
| A | The change to `on` needs each required item that applies: GF1 to GF7 and GF8 to GF13. GF14 to GF16 show after the change. | A server whose proxy has no rule for `/ap/` or `/nodeinfo/`, or whose actor keys do not decrypt, cannot turn on the federation by mistake. An admin who wants only the federation of accounts must also make the knowledge paths work, because one switch turns on both. |
| B | Only GF1 to GF7 gate. GF8 to GF17 show in the report only. | No new condition for admins. A server can federate accounts while the knowledge paths do not work, and other servers then record read errors for it. |
| C | B for one release with a note in the console, then A. | Admins of existing servers get time to change the proxy before the gate starts. |

**Answer (owner, 2026-11-19):** A (KD13).

### PQ15: Is the Development Network Enough for Step KA3? (Section 14.2), Answered (KD14)

| Option | Scope of KA3 | Effect |
| :--- | :--- | :--- |
| A | Only the two servers of the development network | Repeatable as a test; each contributor can run it. It does not show the effects of a proxy or a network of a real operator. |
| B | The development network and two servers on the internet before KA4 | Shows the real network before the context is hosted, but needs a second server of the owner on the internet at this step. |
| C | A for KA3; the first exchange of a server of the owner with another server on the internet is a part of KA5 | KA3 stays a test. The real network gives its data in KA5, where the owner works on production anyway. |

**Answer (owner, 2026-11-19):** C (KD14).

---

## 17. Phases

Precondition: units CT-0 to CT-9 of the shared knowledge base design, and the federation phases of the sidecar.

| Phase | Scope | Completion criteria |
| :--- | :--- | :--- |
| KF0 | Vocabulary and JSON-LD context in our own words; contract fixtures for each object and activity | `claire.bible` serves `/ns/v1` and `/ns` (Section 9.1, rule 3), and then the owner opens the `w3id.org` pull request. Fixtures validate on both sides of a test with two Claire servers |
| KF1 | Known servers, admin controls, inbox journal, single mode root and USERNAME (Section 15) | Two test servers know each other after a contribution or an admin entry; a blocked third server gets 404 on shared-knowledge collections; a failed activity can be processed again; single mode `/` gives 302 to `/@{username}/` |
| KF2 | Reading `/kb` collections, store of remote knowledge bases, themes across servers, root page | A shared knowledge base on server B appears in a theme of server A and on the root page of A, without any agreement |
| KF3 | Knowledge base actors, curation across servers, revisions, removal at the origin | A revision on B reaches the copy on A without approval; a removal on B hides the copy on A and puts it in the review queue |
| KF4 | Contributions across servers (`Offer`, `Accept` with `result`, `Reject`), remote collaborators (`Grant`, `Revoke`) | E2E test: an account of A contributes to a shared knowledge base of B; a reviewer of B approves; the submission page on A shows the decision; after a grant, the next contribution needs no approval; after `Revoke`, it needs approval again |
| KF5 | Automatic contribution across servers | With `kb_auto_contribution` on both servers, a new shared knowledge base on B gets the matching documents of an account of A after a run |
| KF6 | Readiness checks GF8 to GF17 (Section 14.1): the checks of Claire, the knowledge self-check and the loop state of the sidecar in the heartbeat, the path `/ap/self-check`, contract version 1.12, the trust store of the readiness requests (also for GF7), the gate (PQ14), and the two groups of the report in `/admin/federation` and in the CLI | On the development network ([`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) phase DE5) each check passes on both servers. For each check, one fault makes it fail with its detail: a proxy without the rule for `/ap/` (GF8), a proxy that changes `Host` (GF9), a body limit of 64 KiB (GF10), a stopped loop (GF11), a different master key (GF12), a stored limit of 0 (GF13), a proxy that sends `/nodeinfo/` to Claire (GF14), a cache that gives an old actor document (GF15), a cache that gives `/ap/kb` without a signature (GF16), and `/ns/v1` without the CORS header (GF17: a warning, and `report.passed` stays true). With PQ14 A, the change to `on` is refused while GF8 to GF13 fail. E2E test: the console shows the two groups, the level, and "Does not apply" |

After KF6, the acceptance path of Section 14.2 (KA1 to KA5) applies.

Later work: federation of merge decisions; links between duplicate knowledge; a function that links entities to an authority; idea I1 (follows of persons of the open fediverse to a knowledge base actor).
