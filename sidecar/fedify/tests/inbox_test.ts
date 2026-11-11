// Inbox of the sidecar (unit FP2-3): signature check by Fedify, decision by Claire, Accept and Reject, blocked domains, body limit, and the followers collection.
// All servers are in this process. The remote server is a key pair, an actor document in the document loader, and a capture of outbound requests.

import assert from "node:assert/strict";
import { generateCryptoKeyPair, getDocumentLoader, MemoryKvStore, signRequest, verifyRequest } from "@fedify/fedify";
import { type Activity, Announce, CryptographicKey, Delete, Endpoints, Follow, Person, Undo } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const AS2 = "application/activity+json";
const ACCEPTED = { decision: "accepted", reply: "accept", reason: null };

const remoteKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");

interface Delivery {
  url: string;
  request: Request;
  body: Record<string, unknown>;
}

async function remoteActor(iri: string): Promise<unknown> {
  const host = new URL(iri).origin;
  const actor = new Person({
    id: new URL(iri),
    preferredUsername: iri.split("/").pop(),
    inbox: new URL(`${iri}/inbox`),
    endpoints: new Endpoints({ sharedInbox: new URL(`${host}/inbox`) }),
    publicKey: new CryptographicKey({ id: new URL(`${iri}#main-key`), owner: new URL(iri), publicKey: remoteKeys.publicKey }),
  });
  return await actor.toJsonLd();
}

async function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const standard = getDocumentLoader();
  const fetched: string[] = [];
  // The loader of the remote server in the checks of this file. Fedify has the standard contexts in its code, so the standard loader needs no network for them.
  const loader = async (url: string) => {
    const target = new URL(url);
    if (target.hostname.endsWith(".example") && target.origin !== ORIGIN) return { contextUrl: null, documentUrl: url, document: await remoteActor(url.split("#")[0]) };
    return await standard(url);
  };
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const deliveries: Delivery[] = [];
  const realFetch = globalThis.fetch;
  // The remote server: a GET gives an actor document, a POST is a delivery to an inbox.
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    if (request.method === "GET") {
      fetched.push(request.url);
      return new Response(JSON.stringify(await remoteActor(request.url.split("#")[0])), { headers: { "content-type": AS2 } });
    }
    deliveries.push({ url: request.url, request: request.clone(), body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  const post = async (path: string, activity: Activity | Record<string, unknown>, options: { key?: CryptoKey; keyId?: string; sign?: boolean; headers?: Record<string, string> } = {}) => {
    const document = "toJsonLd" in activity ? await (activity as Activity).toJsonLd() as Record<string, unknown> : activity;
    let request = new Request(`${ORIGIN}${path}`, { method: "POST", headers: { "content-type": AS2, ...options.headers }, body: JSON.stringify(document) });
    if (options.sign !== false) {
      const actor = typeof document.actor === "string" ? document.actor : "https://remote.example/users/bob";
      request = await signRequest(request, options.key ?? remoteKeys.privateKey, new URL(options.keyId ?? `${actor}#main-key`));
    }
    const response = await sidecar.handle(request);
    return { status: response.status, text: await response.text(), headers: response.headers };
  };
  return { fake, sidecar, post, deliveries, fetched, loader, restore: () => (globalThis.fetch = realFetch) };
}

// The Follow of the contract fixture: the event that the sidecar makes from it must be equal to the request body of the fixture.
function fixtureFollow(): Follow {
  const body = fixture("inbox_event_follow").request.body as { event_id: string; actor: { iri: string }; object_iri: string };
  return new Follow({ id: new URL(body.event_id), actor: new URL(body.actor.iri), object: new URL(body.object_iri) });
}

Deno.test("a signed Follow gives an Accept that is signed with the key of the followed actor", async () => {
  const { fake, post, deliveries, loader, sidecar, restore } = await setup();
  try {
    const response = await post(`/ap/actors/${ALICE}/inbox`, fixtureFollow());
    assert.ok(response.status === 202 || response.status === 200, `status ${response.status}: ${response.text}`);
    // The event is the request of the contract fixture, and the decision came from Claire.
    assert.deepEqual(fake.events, [fixture("inbox_event_follow").request.body]);
    assert.deepEqual(sidecar.counters, { accepted: 1, rejected: 0, ignored: 0, blocked: 0, dropped: 0 });

    assert.equal(deliveries.length, 1);
    const [accept] = deliveries;
    assert.equal(accept.url, "https://remote.example/users/bob/inbox");
    assert.equal(accept.body.type, "Accept");
    assert.equal(accept.body.actor, ALICE_URL);
    assert.ok(String(accept.body.id).startsWith(`${ALICE_URL}#accepts/`));
    const object = accept.body.object as Record<string, unknown>;
    assert.equal(object.type, "Follow");
    assert.equal(object.id, "https://remote.example/activities/123");
    assert.equal(object.actor, "https://remote.example/users/bob");
    assert.equal(object.object, ALICE_URL);

    // The remote server verifies the delivery with the public key in the actor document of the sidecar.
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${ORIGIN}/`)) return await loader(url);
      const answer = await sidecar.handle(new Request(url, { headers: { accept: AS2 } }));
      return { contextUrl: null, documentUrl: url, document: await answer.json() };
    };
    const key = await verifyRequest(accept.request, { documentLoader: remoteLoader, contextLoader: remoteLoader });
    assert.equal(key?.ownerId?.href, ALICE_URL);
    // No private key and no service key is in the delivery.
    assert.ok(!JSON.stringify(accept.body).includes('"d"'));
  } finally {
    restore();
  }
});

Deno.test("a repeated Follow with a new activity ID gets the same decision and a second Accept", async () => {
  const { fake, post, deliveries, restore } = await setup();
  try {
    fake.decision = ACCEPTED;
    for (const id of ["a1", "a2"]) {
      await post(`/ap/actors/${ALICE}/inbox`, new Follow({ id: new URL(`https://remote.example/activities/${id}`), actor: new URL("https://remote.example/users/bob"), object: new URL(ALICE_URL) }));
    }
    assert.equal(fake.events.length, 2);
    assert.deepEqual(deliveries.map((d) => d.body.type), ["Accept", "Accept"]);
    assert.notEqual(deliveries[0].body.id, deliveries[1].body.id);
  } finally {
    restore();
  }
});

Deno.test("an invalid signature gets 401 and Claire gets no event", async () => {
  const { fake, post, deliveries, restore } = await setup();
  try {
    const other = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");
    const forged = await post(`/ap/actors/${ALICE}/inbox`, fixtureFollow(), { key: other.privateKey });
    assert.equal(forged.status, 401);
    const unsigned = await post(`/ap/actors/${ALICE}/inbox`, fixtureFollow(), { sign: false });
    assert.equal(unsigned.status, 401);
    // A valid signature of a different actor is not valid for this activity.
    const stolen = await post("/ap/inbox", fixtureFollow(), { keyId: "https://other.example/users/mallory#main-key" });
    assert.equal(stolen.status, 401);
    assert.deepEqual(fake.events, []);
    assert.deepEqual(deliveries, []);
  } finally {
    restore();
  }
});

Deno.test("a blocked domain gets 403 after the signature check, no event, and no reply", async () => {
  const { fake, post, deliveries, sidecar, restore } = await setup();
  try {
    const follow = new Follow({ id: new URL("https://sub.blocked.example/activities/9"), actor: new URL("https://sub.blocked.example/users/eve"), object: new URL(ALICE_URL) });
    const signed = await post(`/ap/actors/${ALICE}/inbox`, follow);
    assert.equal(signed.status, 403);
    assert.equal((await post("/ap/inbox", new Follow({ id: new URL("https://blocked.example/activities/10"), actor: new URL("https://blocked.example/users/eve"), object: new URL(ALICE_URL) }))).status, 403);
    // Without a valid signature the answer is 401: the request learns nothing about the blocks.
    assert.equal((await post(`/ap/actors/${ALICE}/inbox`, follow, { sign: false })).status, 401);
    assert.deepEqual(fake.events, []);
    assert.deepEqual(deliveries, []);
    assert.equal(sidecar.counters.blocked, 2);
    assert.equal(await sidecar.isBlocked("notblocked.example"), false);
    assert.equal(await sidecar.isBlocked("example"), false);
  } finally {
    restore();
  }
});

Deno.test("the list of blocked domains stays in memory until the entity tag in the config changes", async () => {
  const { fake, sidecar, restore } = await setup();
  try {
    const blockCalls = () => fake.calls.filter((c) => c.endsWith("/domain-blocks")).length;
    assert.equal(await sidecar.isBlocked("blocked.example"), true);
    assert.equal(await sidecar.isBlocked("remote.example"), false);
    fake.now += 600;
    assert.equal(await sidecar.isBlocked("BLOCKED.example."), true);
    assert.equal(blockCalls(), 1);

    fake.config = { domain_blocks_etag: '"changed"' };
    fake.overrides.set("GET /internal/federation/v1/domain-blocks", { status: 200, response: { etag: '"changed"', domains: ["remote.example", "192.0.2.7"] } });
    fake.now += 600;
    assert.equal(await sidecar.isBlocked("remote.example"), true);
    assert.equal(await sidecar.isBlocked("blocked.example"), false);
    assert.equal(await sidecar.isBlocked("192.0.2.7"), true);
    assert.equal(await sidecar.isBlocked("2.7"), false);
    assert.equal(blockCalls(), 2);
  } finally {
    restore();
  }
});

Deno.test("Undo(Follow) and Delete of the actor go to Claire with the fields of the contract, and get no reply", async () => {
  const { fake, post, deliveries, restore } = await setup();
  try {
    const bob = new URL("https://remote.example/users/bob");
    const undo = new Undo({ id: new URL("https://remote.example/activities/124"), actor: bob, object: new Follow({ id: new URL("https://remote.example/activities/123"), actor: bob, object: new URL(ALICE_URL) }) });
    await post(`/ap/actors/${ALICE}/inbox`, undo);
    assert.deepEqual(fake.events[0], fixture("inbox_event_undo_follow").request.body);

    // The shared inbox has no recipient.
    await post("/ap/inbox", new Delete({ id: new URL("https://remote.example/activities/125"), actor: bob, object: bob }));
    assert.deepEqual(fake.events[1], fixture("inbox_event_delete_actor").request.body);
    assert.deepEqual(deliveries, []);
  } finally {
    restore();
  }
});

Deno.test("a type outside the v1 set goes to Claire, is counted as ignored, and gets no reply", async () => {
  const { fake, post, deliveries, sidecar, restore } = await setup();
  try {
    fake.decision = { decision: "ignored", reply: null, reason: "unsupported_type" };
    const response = await post(`/ap/actors/${ALICE}/inbox`, new Announce({ id: new URL("https://remote.example/activities/130"), actor: new URL("https://remote.example/users/bob"), object: new URL(`${ORIGIN}/ap/objects/1`) }));
    assert.ok(response.status < 300);
    assert.equal(fake.events[0].type, "Announce");
    assert.equal(sidecar.counters.ignored, 1);
    assert.deepEqual(deliveries, []);
  } finally {
    restore();
  }
});

Deno.test("a rejected Follow gets a Reject only when the followed actor has a key", async () => {
  const { fake, post, deliveries, sidecar, restore } = await setup();
  try {
    const bob = new URL("https://remote.example/users/bob");
    // The Follow of alice came to the shared inbox, and Claire refused it for a reason that permits a reply.
    fake.decision = { decision: "rejected", reply: null, reason: "recipient_mismatch" };
    await post("/ap/inbox", new Follow({ id: new URL("https://remote.example/activities/140"), actor: bob, object: new URL(ALICE_URL) }));
    assert.equal(deliveries.length, 1);
    assert.equal(deliveries[0].body.type, "Reject");
    assert.equal(deliveries[0].body.actor, ALICE_URL);
    assert.equal((deliveries[0].body.object as Record<string, unknown>).id, "https://remote.example/activities/140");

    // An account that is not an actor has no key: no reply. A blocked domain gets no delivery.
    for (const reason of ["not_an_actor", "blocked_domain", "invalid_actor", "federation_off"]) {
      fake.decision = { decision: "rejected", reply: null, reason };
      await post("/ap/inbox", new Follow({ id: new URL(`https://remote.example/activities/${reason}`), actor: bob, object: new URL(ALICE_URL) }));
    }
    assert.equal(deliveries.length, 1);
    assert.equal(sidecar.counters.rejected, 5);
  } finally {
    restore();
  }
});

Deno.test("when the internal API is not available the inbox gives no success answer and sends no reply", async () => {
  const { fake, post, deliveries, sidecar, restore } = await setup();
  try {
    // The config and the blocks are in memory. Then Claire stops.
    await sidecar.isBlocked("remote.example");
    fake.failStatus = 503;
    const response = await post(`/ap/actors/${ALICE}/inbox`, fixtureFollow());
    assert.ok(response.status >= 500, `status ${response.status}`);
    assert.deepEqual(deliveries, []);
    assert.equal(sidecar.counters.accepted, 0);
  } finally {
    restore();
  }
});

Deno.test("a body larger than the inbox limit gets 413 before the signature check", async () => {
  const { fake, post, restore } = await setup();
  try {
    fake.config = { limits: { inbox_body_bytes: 600 } };
    const large = { "@context": "https://www.w3.org/ns/activitystreams", id: "https://remote.example/activities/big", type: "Follow", actor: "https://remote.example/users/bob", object: ALICE_URL, summary: "x".repeat(700) };
    assert.equal((await post(`/ap/actors/${ALICE}/inbox`, large, { sign: false })).status, 413);
    assert.equal((await post("/ap/inbox", large)).status, 413);
    assert.deepEqual(fake.events, []);
  } finally {
    restore();
  }
});

Deno.test("inbox paths accept POST only, and are 404 when federation is not active or the account is not an actor", async () => {
  const { fake, post, sidecar, restore } = await setup();
  try {
    for (const path of [`/ap/actors/${ALICE}/inbox`, "/ap/inbox"]) {
      const get = await sidecar.handle(new Request(`${ORIGIN}${path}`, { headers: { accept: AS2 } }));
      assert.equal(get.status, 405);
      assert.equal(get.headers.get("allow"), "POST");
    }
    assert.equal((await post("/ap/actors/216412345678901234/inbox", fixtureFollow())).status, 404);
    assert.equal((await post(`/ap/actors/${ALICE}`, fixtureFollow())).status, 405);
    for (const state of [{ federation: "off" }, { mode: "single" }]) {
      fake.config = state;
      fake.now += 600;
      assert.equal((await post(`/ap/actors/${ALICE}/inbox`, fixtureFollow())).status, 404);
      assert.equal((await post("/ap/inbox", fixtureFollow())).status, 404);
    }
    assert.deepEqual(fake.events, []);
  } finally {
    restore();
  }
});

Deno.test("the followers collection shows the total and no follower", async () => {
  const { fake, sidecar, restore } = await setup();
  try {
    fake.overrides.set(`GET /internal/federation/v1/actors/${ALICE}/followers?limit=1`, { status: 200, response: { ...fixture("followers").response, items: [(fixture("followers").response.items as unknown[])[0]] } });
    const actor = await (await sidecar.handle(new Request(ALICE_URL, { headers: { accept: AS2 } }))).json();
    assert.equal(actor.followers, `${ALICE_URL}/followers`);

    const response = await sidecar.handle(new Request(`${ALICE_URL}/followers`, { headers: { accept: AS2 } }));
    assert.equal(response.status, 200);
    const text = await response.text();
    const collection = JSON.parse(text);
    assert.equal(collection.id, `${ALICE_URL}/followers`);
    assert.equal(collection.type, "OrderedCollection");
    assert.equal(collection.totalItems, 3);
    assert.ok(collection.orderedItems === undefined || collection.orderedItems.length === 0);
    assert.ok(!text.includes("remote.example"), "no follower is in the public collection");

    // An account that is not an actor, and the instance actor, have no collection.
    for (const id of ["216412345678901234", "instance"]) {
      assert.equal((await sidecar.handle(new Request(`${ORIGIN}/ap/actors/${id}/followers`, { headers: { accept: AS2 } }))).status, 404);
    }
  } finally {
    restore();
  }
});

Deno.test("the delivery code reads all follower pages from Claire", async () => {
  const { sidecar, restore } = await setup();
  try {
    const recipients = await sidecar.followerRecipients(ALICE, 2);
    const expected = [...(fixture("followers").response.items as { actor_iri: string }[]), ...(fixture("followers_next").response.items as { actor_iri: string }[])];
    assert.deepEqual(recipients.map((r) => r.id?.href), expected.map((item) => item.actor_iri));
    assert.equal(recipients[0].inboxId?.href, "https://remote.example/users/u3/inbox");
    assert.equal(recipients[0].endpoints?.sharedInbox?.href, "https://remote.example/inbox");
    assert.equal(recipients[1].endpoints, null);
    assert.deepEqual(await sidecar.followerRecipients("216412345678901234"), []);
  } finally {
    restore();
  }
});
