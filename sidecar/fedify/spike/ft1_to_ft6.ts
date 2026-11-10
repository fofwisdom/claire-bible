// FP0 technical checks FT1 to FT6 for the Fedify sidecar design.
// Design: docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md, Section 15.1.
// This program is a check, not product code. It calls no public server.
//
// Run from this directory:
//   FP0_SOCKET=/tmp/fp0.sock deno run --frozen --allow-net=127.0.0.1,claire.internal:80,unix:/tmp/fp0.sock --allow-read --allow-write --allow-env=FP0_SOCKET ft1_to_ft6.ts

import { DatabaseSync } from "node:sqlite";
import { createFederation, type Federation, generateCryptoKeyPair } from "@fedify/fedify";
import { Create, Endpoints, Note, Person, PUBLIC_COLLECTION } from "@fedify/fedify/vocab";
import { SqliteKvStore, SqliteMessageQueue } from "@fedify/sqlite";

const ORIGIN = "https://claire.example.com";
const ALICE_ID = "116412345678901234"; // accounts.public_id. The USERNAME is "alice".
const AS2 = "application/activity+json";

const results: { id: string; pass: boolean; detail: string }[] = [];
function record(id: string, pass: boolean, detail: string): void {
  results.push({ id, pass, detail });
  console.log(`${id} ${pass ? "PASS" : "FAIL"} ${detail}`);
}

const tmp = await Deno.makeTempDir({ prefix: "fp0-" });

// A local HTTP server that stands for a remote ActivityPub server at a loopback address.
let remoteHits = 0;
const remote = Deno.serve({ hostname: "127.0.0.1", port: 0, onListen: () => {} }, (req) => {
  remoteHits += 1;
  const self = new URL(req.url);
  if (req.method === "GET") {
    return Response.json({ "@context": "https://www.w3.org/ns/activitystreams", id: `${self.origin}/actor`, type: "Person", inbox: `${self.origin}/inbox` }, { headers: { "content-type": AS2 } });
  }
  return new Response(null, { status: 202 });
});
const remoteOrigin = `http://127.0.0.1:${remote.addr.port}`;

const keyPairs = [await generateCryptoKeyPair("RSASSA-PKCS1-v1_5"), await generateCryptoKeyPair("Ed25519")];

interface Built {
  federation: Federation<void>;
  db: DatabaseSync;
  errors: string[];
}

function build(name: string, allowPrivateAddress: boolean | undefined): Built {
  const db = new DatabaseSync(`${tmp}/${name}.db`);
  const errors: string[] = [];
  const federation = createFederation<void>({
    kv: new SqliteKvStore(db),
    queue: new SqliteMessageQueue(db),
    origin: ORIGIN,
    manuallyStartQueue: true,
    ...(allowPrivateAddress === undefined ? {} : { allowPrivateAddress }),
    onOutboxError: (error) => {
      errors.push(String(error));
    },
  });
  federation
    .setActorDispatcher("/ap/actors/{identifier}", async (ctx, identifier) => {
      if (identifier !== ALICE_ID) return null;
      const keys = await ctx.getActorKeyPairs(identifier);
      return new Person({
        id: ctx.getActorUri(identifier),
        preferredUsername: "alice",
        name: "Alice",
        url: new URL("/@alice/", ctx.canonicalOrigin),
        inbox: ctx.getInboxUri(identifier),
        followers: ctx.getFollowersUri(identifier),
        endpoints: new Endpoints({ sharedInbox: ctx.getInboxUri() }),
        publicKey: keys[0]?.cryptographicKey,
        assertionMethods: keys.map((k) => k.multikey),
      });
    })
    .mapHandle((_ctx, username) => (username === "alice" ? ALICE_ID : null))
    .setKeyPairsDispatcher((_ctx, identifier) => (identifier === ALICE_ID ? keyPairs : []));
  federation.setInboxListeners("/ap/actors/{identifier}/inbox", "/ap/inbox");
  federation
    .setFollowersDispatcher("/ap/actors/{identifier}/followers", (ctx, identifier, _cursor) => {
      if (identifier !== ALICE_ID) return null;
      // A public HTTP request has a request context. The delivery code has a plain context.
      if ("request" in ctx) return { items: [] };
      return { items: [{ id: new URL(`${remoteOrigin}/actor`), inboxId: new URL(`${remoteOrigin}/inbox`) }] };
    })
    .setCounter((_ctx, identifier) => (identifier === ALICE_ID ? 1 : null));
  return { federation, db, errors };
}

function queuedMessages(db: DatabaseSync): Record<string, unknown>[] {
  return db.prepare('SELECT message FROM "fedify_message"').all().map((row) => JSON.parse(String(row.message)));
}

async function getJson(federation: Federation<void>, url: string, accept: string): Promise<{ status: number; body: Record<string, unknown> | null }> {
  const response = await federation.fetch(new Request(url, { headers: { accept } }), { contextData: undefined });
  const text = await response.text();
  let body: Record<string, unknown> | null = null;
  try {
    body = JSON.parse(text);
  } catch {
    body = null;
  }
  return { status: response.status, body };
}

function selfLink(jrd: Record<string, unknown> | null): string | undefined {
  const links = (jrd?.links ?? []) as { rel?: string; type?: string; href?: string }[];
  return links.find((l) => l.rel === "self")?.href;
}

const strict = build("strict", undefined);
const actorUrl = `${ORIGIN}/ap/actors/${ALICE_ID}`;

// FT1: a numeric actor identifier with a WebFinger USERNAME mapping.
{
  const finger = await getJson(strict.federation, `${ORIGIN}/.well-known/webfinger?resource=acct:alice@claire.example.com`, "application/jrd+json");
  const unknown = await getJson(strict.federation, `${ORIGIN}/.well-known/webfinger?resource=acct:bob@claire.example.com`, "application/jrd+json");
  const byNumber = await getJson(strict.federation, `${ORIGIN}/.well-known/webfinger?resource=acct:${ALICE_ID}@claire.example.com`, "application/jrd+json");
  const actor = await getJson(strict.federation, actorUrl, AS2);
  const pass = finger.status === 200 && selfLink(finger.body) === actorUrl && finger.body?.subject === "acct:alice@claire.example.com" && unknown.status === 404 && actor.status === 200 && actor.body?.id === actorUrl && actor.body?.preferredUsername === "alice";
  record("FT1", pass, `self=${selfLink(finger.body)} subject=${finger.body?.subject} unknown_user=${unknown.status} number_as_username=${byNumber.status} actor.id=${actor.body?.id} preferredUsername=${actor.body?.preferredUsername}`);
}

// FT2: the canonical origin is fixed. The request host does not change the URLs.
{
  const other = "http://127.0.0.1:8769";
  const finger = await getJson(strict.federation, `${other}/.well-known/webfinger?resource=acct:alice@claire.example.com`, "application/jrd+json");
  const actor = await getJson(strict.federation, `${other}/ap/actors/${ALICE_ID}`, AS2);
  const text = JSON.stringify([finger.body, actor.body]);
  const pass = selfLink(finger.body) === actorUrl && actor.body?.id === actorUrl && actor.body?.inbox === `${actorUrl}/inbox` && !text.includes("127.0.0.1");
  record("FT2", pass, `request_host=127.0.0.1:8769 self=${selfLink(finger.body)} actor.id=${actor.body?.id} inbox=${actor.body?.inbox} leaks_request_host=${text.includes("127.0.0.1")}`);
}

// FT5: the public followers collection gives the total only. The delivery code gets the full list.
{
  const collection = await getJson(strict.federation, `${actorUrl}/followers`, AS2);
  const items = (collection.body?.orderedItems ?? collection.body?.items ?? []) as unknown[];
  const publicText = JSON.stringify(collection.body);
  const ctx = strict.federation.createContext(new URL(ORIGIN), undefined);
  await ctx.sendActivity({ identifier: ALICE_ID }, "followers", new Create({ id: new URL(`${ORIGIN}/ap/activities/1`), actor: ctx.getActorUri(ALICE_ID), to: PUBLIC_COLLECTION, object: new Note({ id: new URL(`${ORIGIN}/ap/objects/1`), attribution: ctx.getActorUri(ALICE_ID), content: "FP0 check", to: PUBLIC_COLLECTION }) }));
  const queued = queuedMessages(strict.db);
  const deliveryText = JSON.stringify(queued);
  const pass = collection.status === 200 && collection.body?.totalItems === 1 && items.length === 0 && !publicText.includes(remoteOrigin) && deliveryText.includes(`${remoteOrigin}/inbox`);
  record("FT5", pass, `status=${collection.status} totalItems=${collection.body?.totalItems} public_items=${items.length} public_shows_follower=${publicText.includes(remoteOrigin)} delivery_has_follower_inbox=${deliveryText.includes(`${remoteOrigin}/inbox`)}`);
}

// FT3: does a queued message contain the sender private key?
{
  const queued = queuedMessages(strict.db);
  const types = queued.map((m) => String(m.type));
  const privateFields = new Set<string>();
  for (const message of queued) {
    for (const pair of (message.keys ?? []) as { privateKey?: Record<string, unknown> }[]) {
      for (const field of ["d", "p", "q", "dp", "dq", "qi"]) {
        if (pair.privateKey && field in pair.privateKey) privateFields.add(field);
      }
    }
  }
  const containsPrivateKey = privateFields.size > 0;
  // This check records a fact. It passes when the result is clear.
  record("FT3", queued.length > 0, `queued_types=${types.join(",")} contains_private_key=${containsPrivateKey} jwk_private_fields=${[...privateFields].join(",")}`);
}

// FT4: private and loopback addresses are refused by default, for fetch and for delivery.
{
  const ctx = strict.federation.createContext(new URL(ORIGIN), undefined);
  remoteHits = 0;
  let fetchResult = "null";
  try {
    const object = await ctx.lookupObject(`${remoteOrigin}/actor`);
    fetchResult = object == null ? "null" : "object";
  } catch (error) {
    fetchResult = `error:${(error as Error).name}`;
  }
  const fetchHits = remoteHits;

  // Process each queued message in this process until no new message appears. This is the same code path as the queue worker.
  remoteHits = 0;
  const done = new Set<string>();
  for (let round = 0; round < 5; round += 1) {
    const pending = queuedMessages(strict.db).filter((m) => !done.has(String(m.id)));
    if (pending.length === 0) break;
    for (const message of pending) {
      done.add(String(message.id));
      try {
        // deno-lint-ignore no-explicit-any
        await strict.federation.processQueuedTask(undefined, message as any);
      } catch (error) {
        strict.errors.push(String(error));
      }
    }
  }
  const deliveryHits = remoteHits;

  // Control: the same steps with the check turned off must reach the server. This shows that the test can fail.
  const open = build("open", true);
  const openCtx = open.federation.createContext(new URL(ORIGIN), undefined);
  remoteHits = 0;
  await openCtx.lookupObject(`${remoteOrigin}/actor`).catch(() => null);
  const controlFetchHits = remoteHits;
  remoteHits = 0;
  await openCtx.sendActivity({ identifier: ALICE_ID }, { id: new URL(`${remoteOrigin}/actor`), inboxId: new URL(`${remoteOrigin}/inbox`) }, new Create({ id: new URL(`${ORIGIN}/ap/activities/2`), actor: openCtx.getActorUri(ALICE_ID), to: PUBLIC_COLLECTION, object: new Note({ id: new URL(`${ORIGIN}/ap/objects/2`), content: "FP0 control" }) }), { immediate: true }).catch((error) => open.errors.push(String(error)));
  const controlDeliveryHits = remoteHits;
  open.db.close();

  const pass = fetchHits === 0 && fetchResult !== "object" && deliveryHits === 0 && strict.errors.length > 0 && controlFetchHits > 0 && controlDeliveryHits > 0;
  record("FT4", pass, `default_fetch=${fetchResult} default_fetch_hits=${fetchHits} default_delivery_hits=${deliveryHits} default_delivery_errors=${strict.errors.length} control_fetch_hits=${controlFetchHits} control_delivery_hits=${controlDeliveryHits}`);
  if (strict.errors.length > 0) console.log(`     first delivery error: ${strict.errors[0].slice(0, 160)}`);
}

// FT6: the runtime serves HTTP on a TCP port and calls an HTTP API on a Unix socket.
{
  // FP0_SOCKET lets the caller name the socket in the permission flags.
  const socketPath = Deno.env.get("FP0_SOCKET") ?? `${tmp}/federation.sock`;
  // This server stands for the Claire internal federation API.
  const internal = Deno.serve({ path: socketPath, onListen: () => {} }, (req) => Response.json({ path: new URL(req.url).pathname, signature: req.headers.get("x-claire-fed-signature") }));
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: socketPath } });
  // The public listener calls the internal API for each request, the same as the sidecar will.
  const publicServer = Deno.serve({ hostname: "127.0.0.1", port: 0, onListen: () => {} }, async () => {
    const upstream = await fetch("http://claire.internal/internal/federation/v1/config", { client, headers: { "x-claire-fed-signature": "test" } });
    return Response.json({ upstream: await upstream.json() });
  });
  let detail = "";
  let pass = false;
  try {
    const response = await fetch(`http://127.0.0.1:${publicServer.addr.port}/`);
    const body = await response.json();
    pass = response.status === 200 && body.upstream?.path === "/internal/federation/v1/config" && body.upstream?.signature === "test";
    detail = `status=${response.status} upstream=${JSON.stringify(body.upstream)}`;
  } catch (error) {
    detail = `error=${String(error).slice(0, 160)}`;
  }
  client.close();
  await publicServer.shutdown();
  await internal.shutdown();
  record("FT6", pass, detail);
}

strict.db.close();
await remote.shutdown();
await Deno.remove(tmp, { recursive: true });

const failed = results.filter((r) => !r.pass);
console.log(failed.length === 0 ? "FP0 FT1-FT6: all checks completed" : `FP0 FT1-FT6: ${failed.length} check(s) failed: ${failed.map((r) => r.id).join(", ")}`);
Deno.exit(failed.length === 0 ? 0 : 1);
