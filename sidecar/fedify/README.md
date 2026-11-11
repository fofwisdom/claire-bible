# Fedify Sidecar

The sidecar is the ActivityPub protocol adapter of Claire Bible. Claire is canonical: the sidecar reads all account data through the internal federation API and stores only a remote-document cache and a message queue. Design: [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](../../docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md).

## Layout

| Path | Content |
| :--- | :--- |
| `src/internal_api.ts` | Signed client for the internal federation API of Claire, with sealed response shapes |
| `src/sidecar.ts` | WebFinger, NodeInfo, actor documents, the instance actor, and the public self-check |
| `src/main.ts` | Entry point: database, Unix socket client, heartbeat, HTTP listener |
| `tests/` | Tests against the contract fixtures in `tests/fixtures/federation_contract/` of the repository |

## Commands

Run the commands from this directory. Runtime: Deno 2.9 with the frozen lock file `deno.lock`.

```bash
deno task test
```

```bash
deno task check
```

The Python test `tests/test_fedify_sidecar.py` runs the same tests and starts the sidecar with a real Claire process.

## Public Paths in Phase FP1

`/.well-known/webfinger`, `/.well-known/nodeinfo`, `/nodeinfo/2.1`, `/ap/actors/{public_id}`, and `/ap/actors/instance`. Inbox paths, the followers collection, and the outbox come in phases FP2 and FP3. Until then a `GET` of those paths returns 404, and a method that is not `GET` or `HEAD` returns 405 on each path. All paths return 404 when federation is `off` or the instance mode is `single`.

## Dependencies

A dependency must have the MIT, Apache-2.0, BSD, or ISC license. Other licenses need owner approval. Do not set the Fedify option that permits private network addresses: a test fails if the source contains it.
