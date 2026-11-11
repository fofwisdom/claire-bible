// Publishing in the sidecar (unit FP3-3): handoff of outbox tasks as Create, Update, and Delete of an Article, delivery to followers, the object path, and the outbox collection.
// All servers are in this process. The remote server is a key-less capture of outbound requests: the followers come from Claire, so no remote document is fetched.

import assert from "node:assert/strict";
import { getDocumentLoader, MemoryKvStore, verifyRequest } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const OBJECT = "816412345678901234";
const OBJECT_URL = `${ORIGIN}/ap/objects/${OBJECT}`;
const GONE = "816412345678901239";
const GONE_URL = `${ORIGIN}/ap/objects/${GONE}`;
const PREFIX = "/internal/federation/v1";
const AS2 = "application/activity+json";
const PUBLIC = "https://www.w3.org/ns/activitystreams#Public";

interface Delivery {
  url: string;
  request: Request;
  body: Record<string, unknown>;
}

function task(id: number, kind: string, objectId: string | null = OBJECT) {
  return { id, kind, actor_public_id: ALICE, object_public_id: objectId, created_at: 1800000310.0, lease_until: 1800000700.0 };
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const deliveries: Delivery[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    assert.equal(request.method, "POST", `unexpected outbound ${request.method} ${request.url}`);
    deliveries.push({ url: request.url, request: request.clone(), body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  // The followers of the fixtures as one page: u3 and u1 have the shared inbox of remote.example, u2 has a personal inbox only.
  const followers = [...(fixture("followers").response.items as unknown[]), ...(fixture("followers_next").response.items as unknown[])];
  const setFollowers = (items: unknown[]) => fake.overrides.set(`GET ${PREFIX}/actors/${ALICE}/followers?limit=80`, { status: 200, response: { public_id: ALICE, total: items.length, items, next_max_id: null } });
  const setTasks = (tasks: unknown[]) => fake.overrides.set(`GET ${PREFIX}/outbox-tasks?limit=10`, { status: 200, response: { tasks } });
  const setAck = (id: number, status: string) => fake.overrides.set(`POST ${PREFIX}/outbox-tasks/${id}/ack`, { status: 200, response: { id, status } });
  setFollowers(followers);
  const get = (url: string, accept = AS2) => sidecar.handle(new Request(url, { headers: { accept } }));
  const sorted = () => [...deliveries].sort((a, b) => a.url.localeCompare(b.url));
  return { fake, sidecar, deliveries, sorted, setFollowers, setTasks, setAck, get, restore: () => (globalThis.fetch = realFetch) };
}

Deno.test("a create task and a delete task become signed activities for the inboxes of the followers", async () => {
  const { fake, sidecar, deliveries, sorted, setAck, get, restore } = setup();
  try {
    // The tasks of the contract fixture: Create of the object fixture, and Delete of the deleted object.
    setAck(2, "handed_off");
    assert.equal(await sidecar.processOutboxTasks(), 2);
    assert.deepEqual(fake.acks, ["1 handed_off null", "2 handed_off null"]);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 2, rejected: 0, failed: 0, skipped: 0 });

    // The shared inbox gets one delivery for the two followers that have it. The follower without a shared inbox gets a delivery to its inbox.
    assert.equal(deliveries.length, 4);
    const creates = sorted().filter((d) => d.body.type === "Create");
    assert.deepEqual(creates.map((d) => d.url), ["https://remote.example/inbox", "https://remote.example/users/u2/inbox"]);
    const create = creates[0].body;
    assert.equal(create.id, `${OBJECT_URL}#create`);
    assert.equal(create.actor, ALICE_URL);
    assert.equal(create.to, PUBLIC);
    assert.equal(create.cc, `${ALICE_URL}/followers`);
    const record = fixture("object").response;
    assert.deepEqual(create.object, {
      id: OBJECT_URL,
      type: "Article",
      attributedTo: ALICE_URL,
      name: record.name,
      content: record.content,
      url: record.url,
      published: "2027-01-15T08:05:10Z",
      updated: "2027-01-15T08:05:20Z",
      to: PUBLIC,
      cc: `${ALICE_URL}/followers`,
    });
    // v1 sends no tags and no attachments (design Section 9.1).
    assert.ok(!("tag" in (create.object as object)) && !("attachment" in (create.object as object)));

    const deletes = sorted().filter((d) => d.body.type === "Delete");
    assert.equal(deletes.length, 2);
    assert.equal(deletes[0].body.id, `${GONE_URL}#delete`);
    assert.equal(deletes[0].body.actor, ALICE_URL);
    assert.deepEqual(deletes[0].body.object, { id: GONE_URL, type: "Tombstone" });
    // A Delete needs no read of the object.
    assert.ok(!fake.calls.includes(`GET ${PREFIX}/objects/${GONE}`));

    // The remote server verifies each delivery with the public key in the actor document of the sidecar.
    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${ORIGIN}/`)) return await standard(url);
      return { contextUrl: null, documentUrl: url, document: await (await get(url)).json() };
    };
    for (const delivery of deliveries) {
      const key = await verifyRequest(delivery.request, { documentLoader: remoteLoader, contextLoader: remoteLoader });
      assert.equal(key?.ownerId?.href, ALICE_URL, delivery.url);
      assert.ok(!JSON.stringify(delivery.body).includes('"d"'));
    }
  } finally {
    restore();
  }
});

Deno.test("a second handoff of the same task makes the same activity and the same object", async () => {
  const { fake, sidecar, deliveries, setTasks, setFollowers, restore } = setup();
  try {
    setFollowers([(fixture("followers").response.items as unknown[])[1]]);
    setTasks([task(1, "create")]);
    // Claire gives the task again: for example, the acknowledgement was lost and the lease ended.
    await sidecar.processOutboxTasks();
    await sidecar.processOutboxTasks();
    assert.deepEqual(fake.acks, ["1 handed_off null", "1 handed_off null"]);
    assert.equal(deliveries.length, 2);
    // The signatures have the time of the handoff. All other fields are equal.
    const unsigned = (body: Record<string, unknown>) => ({ ...body, proof: null, signature: null });
    assert.deepEqual(unsigned(deliveries[0].body), unsigned(deliveries[1].body));
    assert.equal(deliveries[0].body.id, `${OBJECT_URL}#create`);
    assert.equal((deliveries[0].body.object as Record<string, unknown>).id, OBJECT_URL);
  } finally {
    restore();
  }
});

Deno.test("an update task becomes an Update whose ID has the time of the change", async () => {
  const { fake, sidecar, deliveries, setTasks, setFollowers, setAck, restore } = setup();
  try {
    setFollowers([(fixture("followers").response.items as unknown[])[1]]);
    setTasks([task(7, "update")]);
    setAck(7, "handed_off");
    await sidecar.processOutboxTasks();
    await sidecar.processOutboxTasks();
    // A later change of the document is a different activity.
    fake.overrides.set(`GET ${PREFIX}/objects/${OBJECT}`, { status: 200, response: { ...fixture("object").response, name: "New Title", updated_at: 1800000999.5 } });
    await sidecar.processOutboxTasks();
    assert.deepEqual(deliveries.map((d) => `${d.body.type} ${d.body.id}`), [`Update ${OBJECT_URL}#updates/1800000320000`, `Update ${OBJECT_URL}#updates/1800000320000`, `Update ${OBJECT_URL}#updates/1800000999500`]);
    const article = deliveries[2].body.object as Record<string, unknown>;
    assert.deepEqual([article.id, article.type, article.name, article.published], [OBJECT_URL, "Article", "New Title", "2027-01-15T08:05:10Z"]);
  } finally {
    restore();
  }
});

Deno.test("a task whose object is not available is rejected and sends nothing", async () => {
  const { fake, sidecar, deliveries, setTasks, setAck, restore } = setup();
  try {
    // 2: the object was deleted after the task was made (the contract fixture). 3: Claire does not show the object, for example the theme is private now. 4: the object is of a different actor.
    setTasks([task(2, "create", GONE), task(3, "update", "816412345678901230"), task(4, "create", "816412345678901231")]);
    setAck(3, "rejected");
    setAck(4, "rejected");
    fake.overrides.set(`GET ${PREFIX}/objects/816412345678901231`, { status: 200, response: { ...fixture("object").response, public_id: "816412345678901231", actor_public_id: "316412345678901234" } });
    assert.equal(await sidecar.processOutboxTasks(), 3);
    assert.deepEqual(fake.acks, ["2 rejected object_gone", "3 rejected object_not_found", "4 rejected actor_mismatch"]);
    assert.deepEqual(deliveries, []);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 0, rejected: 3, failed: 0, skipped: 0 });
  } finally {
    restore();
  }
});

Deno.test("an error in one task gives no acknowledgement and does not stop the other tasks", async () => {
  const { fake, sidecar, deliveries, setTasks, setFollowers, setAck, restore } = setup();
  try {
    setFollowers([(fixture("followers").response.items as unknown[])[1]]);
    // 5: Claire gives an error for the object. 6: an actor task of unit FP3-4. 1: a good task.
    setTasks([task(5, "update", "816412345678901232"), task(6, "update_actor", null), task(1, "create")]);
    fake.overrides.set(`GET ${PREFIX}/objects/816412345678901232`, { status: 500, response: { error: "internal_error" } });
    setAck(5, "handed_off");
    setAck(6, "handed_off");
    assert.equal(await sidecar.processOutboxTasks(), 3);
    assert.deepEqual(fake.acks, ["1 handed_off null"]);
    assert.deepEqual(deliveries.map((d) => d.body.id), [`${OBJECT_URL}#create`]);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 1, rejected: 0, failed: 1, skipped: 1 });

    // The followers are not available: the task stays with Claire.
    fake.overrides.set(`GET ${PREFIX}/actors/${ALICE}/followers?limit=80`, { status: 500, response: { error: "internal_error" } });
    setTasks([task(1, "create")]);
    await sidecar.processOutboxTasks();
    assert.deepEqual(fake.acks, ["1 handed_off null"]);
    assert.equal(deliveries.length, 1);
    assert.equal(sidecar.outboxCounters.failed, 2);
  } finally {
    restore();
  }
});

Deno.test("a follower on a blocked domain gets no delivery, and an actor without followers is handed off", async () => {
  const { fake, sidecar, deliveries, setTasks, setFollowers, restore } = setup();
  try {
    const follower = (host: string, name: string, shared: string | null = null) => ({ id: "716412345678901200", actor_iri: `https://${host}/users/${name}`, inbox_iri: `https://${host}/users/${name}/inbox`, shared_inbox_iri: shared });
    // The domain-blocks fixture has blocked.example. A block applies to a subdomain and to the host of an inbox.
    setFollowers([follower("blocked.example", "a"), follower("sub.blocked.example", "b"), follower("ok.example", "c", "https://blocked.example/inbox"), follower("notblocked.example", "d")]);
    setTasks([task(1, "create")]);
    await sidecar.processOutboxTasks();
    assert.deepEqual(deliveries.map((d) => d.url), ["https://notblocked.example/users/d/inbox"]);

    setFollowers([]);
    await sidecar.processOutboxTasks();
    assert.equal(deliveries.length, 1);
    assert.deepEqual(fake.acks, ["1 handed_off null", "1 handed_off null"]);
  } finally {
    restore();
  }
});

Deno.test("no task is taken while federation is off or the mode is single", async () => {
  const { fake, sidecar, deliveries, restore } = setup();
  try {
    for (const state of [{ federation: "off" }, { mode: "single" }]) {
      fake.config = state;
      fake.now += 600;
      assert.equal(await sidecar.processOutboxTasks(), 0);
    }
    assert.ok(!fake.calls.some((call) => call.includes("/outbox-tasks")));
    assert.deepEqual(deliveries, []);
  } finally {
    restore();
  }
});

Deno.test("the object path gives the Article, 410 with a Tombstone for a deleted object, and 404 for all other IDs", async () => {
  const { fake, sidecar, get, restore } = setup();
  try {
    const response = await get(OBJECT_URL);
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type") ?? "", /^application\/activity\+json/);
    const article = await response.json();
    const record = fixture("object").response;
    assert.deepEqual([article.id, article.type, article.attributedTo, article.name, article.content, article.url], [OBJECT_URL, "Article", ALICE_URL, record.name, record.content, record.url]);
    assert.deepEqual([article.published, article.updated, article.to, article.cc], ["2027-01-15T08:05:10Z", "2027-01-15T08:05:20Z", "as:Public", `${ALICE_URL}/followers`]);

    const gone = await get(GONE_URL);
    assert.equal(gone.status, 410);
    assert.equal(gone.headers.get("content-type"), AS2);
    assert.deepEqual(await gone.json(), { "@context": "https://www.w3.org/ns/activitystreams", id: GONE_URL, type: "Tombstone", formerType: "Article", deleted: "2027-01-15T08:05:30Z" });

    // The object-not-found fixture, and an object that Claire does not show.
    const missing = await get(`${ORIGIN}/ap/objects/816412345678901230`);
    assert.equal(missing.status, 404);
    assert.equal(await missing.text(), "Not Found");
    // A page request gets no activity document, and the object path accepts GET and HEAD only.
    assert.equal((await get(OBJECT_URL, "text/html")).status, 406);
    assert.equal((await sidecar.handle(new Request(OBJECT_URL, { method: "DELETE" }))).status, 405);
    // A read of a deleted object leaves no state: the next read of a different answer is correct.
    fake.overrides.set(`GET ${PREFIX}/objects/${GONE}`, { status: 404, response: { error: "not_found" } });
    assert.equal((await get(GONE_URL)).status, 404);
  } finally {
    restore();
  }
});

Deno.test("the outbox collection has one Create for each public object, in pages", async () => {
  const { fake, sidecar, get, restore } = setup();
  try {
    const item = (id: string, at: number) => ({ id, published_at: at, updated_at: at });
    const outbox = `${PREFIX}/actors/${ALICE}/outbox`;
    fake.overrides.set(`GET ${outbox}?limit=20`, { status: 200, response: { public_id: ALICE, total: 2, items: [item("816412345678901235", 1800000340.0)], next_max_id: "816412345678901235" } });
    fake.overrides.set(`GET ${outbox}?limit=20&max_id=816412345678901235`, { status: 200, response: { public_id: ALICE, total: 2, items: [item(OBJECT, 1800000310.0)], next_max_id: null } });

    const root = await (await get(`${ALICE_URL}/outbox`)).json();
    assert.deepEqual([root.id, root.type, root.totalItems], [`${ALICE_URL}/outbox`, "OrderedCollection", 2]);
    assert.equal(root.orderedItems, undefined);
    const first = await (await get(root.first)).json();
    assert.equal(first.type, "OrderedCollectionPage");
    assert.deepEqual(first.orderedItems.map((i: Record<string, unknown>) => ({ ...i, "@context": undefined })), [{ "@context": undefined, id: `${ORIGIN}/ap/objects/816412345678901235#create`, type: "Create", actor: ALICE_URL, object: `${ORIGIN}/ap/objects/816412345678901235`, published: "2027-01-15T08:05:40Z", to: "as:Public", cc: `${ALICE_URL}/followers` }]);
    const second = await (await get(first.next)).json();
    assert.deepEqual(second.orderedItems.map((i: { object: string }) => i.object), [OBJECT_URL]);
    assert.equal(second.next, undefined);

    // An account that is not an actor, the instance actor, and a cursor that is not an ID have no collection.
    for (const url of [`${ORIGIN}/ap/actors/216412345678901234/outbox`, `${ORIGIN}/ap/actors/instance/outbox`, `${ALICE_URL}/outbox?cursor=x`]) {
      assert.equal((await get(url)).status, 404, url);
    }
    // The outbox accepts no activity from a client.
    assert.equal((await sidecar.handle(new Request(`${ALICE_URL}/outbox`, { method: "POST", body: "{}" }))).status, 405);
  } finally {
    restore();
  }
});
