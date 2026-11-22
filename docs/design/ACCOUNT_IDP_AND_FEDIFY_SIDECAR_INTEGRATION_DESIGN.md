# Account, IdP, and Fedify MIT Sidecar Integration Design

- **Status**: Implemented (phases FP1 to FP3). Design principles only. Current behavior: [`FEDERATION.md`](../implementation/FEDERATION.md) Sections 1 to 14. Phase FP4 (remote reading and two-way communication) has its own design: [`FEDERATION_REMOTE_READING_DESIGN.md`](FEDERATION_REMOTE_READING_DESIGN.md).
- **Date**: 2026-11-10 · condensed 2026-11-16. The full first version (architecture, contracts, data model, work units FP1-1 to FP3-5, technical items FT1 to FT7) is in git history before commit `d15ae8e0`.
- **Related**: [`PRINCIPLES.md`](../PRINCIPLES.md), [`MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md`](MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md), [`ACCOUNTS.md`](../implementation/ACCOUNTS.md), [`API_AND_MCP_GUIDE.md`](../implementation/API_AND_MCP_GUIDE.md) §2.5.

## Goal

Each Claire account with a public profile can be an ActivityPub actor with the handle `@username@{host}`. A separate process, the Fedify sidecar, does the protocol work. Claire stays the only source of accounts, identity, authorization, and content.

Fedify (`https://fedify.dev`) has the MIT license. The sidecar source is in this repository. Work uses the latest Fedify release.

## Problem

An earlier syndication design withdrew a native actor. The reasons were key custody, signature verification, delivery queues, remote moderation, abuse handling, and a larger public attack surface. This design gives each concern an owner:

| Concern | Owner |
| :--- | :--- |
| Signatures, JSON-LD, WebFinger, NodeInfo, delivery queue, retry, fan-out | The sidecar (Fedify) |
| Key custody | Claire |
| Moderation and abuse | Claire policy, with a small first scope |
| Public attack surface | A fixed set of paths in a separate process |

## Decisions in Force

Owner decisions FD1 to FD12 (locked 2026-11-10):

| ID | Decision | Reason |
| :--- | :--- | :--- |
| FD1 | Claire hosts ActivityPub actors for accounts through a Fedify sidecar. | The sidecar takes the protocol risks that stopped the earlier native design. |
| FD2 | Federation operates in network mode only. **Replaced** by KD2 and Section 15 of [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md): federation works in both modes, and rule S9 is withdrawn. | Single mode must not show the USERNAME of the admin (S9). A handle shows the USERNAME. |
| FD3 | The gate of an account is `profile_public` together with the server switch. **Changed** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (2026-11-22): there is no server switch; the gate of an account is `profile_public` alone. There is no separate switch for each account. | One public profile rule. A remote server cannot find which private accounts exist: all other states give the same 404. |
| FD4 | The actor ID contains the public number of the account, not the USERNAME. | A released USERNAME can go to a new account. The new account must not get the old identity, followers, or trust. A public number is never used again. |
| FD5 | Claire keeps the actor keys, encrypted. The sidecar gets a key only on the internal channel. | Backup, purge, and rotation follow the existing secret rules. |
| FD6 | The reverse proxy sends the federation paths to the sidecar. Claire does not proxy them. | Inbound federation load does not go through the one ASGI worker. A wrong proxy rule fails closed with 404. |
| FD7 | The sidecar runtime is Deno. | Permission flags limit the process. The Python image gets no JavaScript runtime. |
| FD8 | A published document is an `Article`. Published documents send no hashtags. | Entity names are knowledge of the theme, not tags for a remote server. |
| FD9 | Follow requests are accepted automatically. | Small first scope. |
| FD10 | A change of `profile_public` to 0 is a reversible pause. Only a purge sends `Delete(Person)`. | Most remote servers cannot undo a `Delete(Person)`. A user must be able to pause quickly and come back. |
| FD11 | When federation is on, a change of `profile_public` from 0 to 1 needs recent authentication. A change from 1 to 0 never does. | The change makes the account public to other servers. A pause must always be fast. |
| FD12 | Theme actors are out of scope. | A theme is a group function of Claire. Idea I1 in the FP4 design records the theme as a group. |

Architecture principles (FK1 to FK10):

1. **Claire is canonical (FK1).** The sidecar stores only a remote-document cache and a message queue. Account suspension and purge stop federation without a second database to clean. A loss of the sidecar database loses no canonical data.
2. **One identity authority (FK2).** The sidecar has no user database, no login, no session, and no user-facing authentication. A browser never sends a credential to the sidecar. Thus the sidecar cannot become a second authorization path.
3. **One direction of control (FK7).** The sidecar calls a versioned internal API on Claire. Claire does not call the sidecar. The internal API has its own listener, selected by the listener and never by the `Host` header, and it is not reachable from the public port. A local socket has no network exposure.
4. **No account, no action.** The service credential of the sidecar is not a principal and cannot call a public route. Each outbound task has an explicit account. No federation function has a default account. A read-only credential cannot cause an outbound activity. The admin is not a content superuser: the admin controls the switch and domain blocks, but cannot publish for a member.
5. **No external identity data in federation.** Issuer, subject, email, and provider ID never appear in actor documents, WebFinger, NodeInfo, or internal API payloads. A link or unlink of an IdP does not change the actor. An IdP claim cannot turn federation on or set the USERNAME. An IdP logout ends sessions only.
6. **No fallback (FK9, PRINCIPLES C1 and C2).** If the sidecar is not available, outbound tasks stay pending and the interface shows that state. Claire does not send through a connected account as a substitute.
7. **Transactional outbox.** A publish writes the object and its task in one transaction. Activity IDs are stable, so a second handoff makes no second object on a remote server.
8. **Privacy changes remove what was published.** A theme that becomes private, a purged document, and a deleted theme make `Delete` tasks in the transaction of the change. The interface says that removal on remote servers is a request, not a guarantee.
9. **Readiness before the switch.** The admin can turn federation on only when all readiness checks pass. One of the checks shows that the reverse proxy sends the federation paths to the sidecar and the OAuth metadata paths to Claire. **Withdrawn** by GD4 of [`FEDERATION_GO_LIVE_DESIGN.md`](FEDERATION_GO_LIVE_DESIGN.md) (2026-11-22): federation has no switch, and the readiness checks are a report of the state.
10. **Schema grows only when it is read (FK10).** A schema object is added in the phase that first reads it.
11. **Sidecar dependency licenses.** A sidecar dependency must have the MIT, Apache-2.0, BSD, or ISC license. Other licenses need owner approval. The sidecar image keeps the license files of its dependencies.

## Rejected Alternatives

| Alternative | Reason for rejection |
| :--- | :--- |
| Federation code inside Claire | Key custody, queues, and inbound load stay in the one ASGI worker. The public attack surface grows. |
| The sidecar generates and stores the keys | The sidecar database becomes canonical and needs its own purge and backup rules. |
| A TCP port on the Compose network for the internal API | A socket has no network exposure. |
| The USERNAME in the actor ID | A released USERNAME would give the old actor identity to a new account. |
| `Delete(Person)` at a pause | Not reversible on most remote servers. |
| Send through a connected external account when the sidecar is down | A silent substitute (C1). |

## Limits for Later Changes

- The connected-account client model (external server accounts) is not part of this design. Its state is RD12 (deferred) in the FP4 design.
- The sidecar database is a secret store, because queued deliveries contain sender keys. Keep it out of support bundles, graph bundles, and default backups, the same as the master key.
- No test calls a public fediverse server (PRINCIPLES A2). Verification is in development mode only (PRINCIPLES E4).
