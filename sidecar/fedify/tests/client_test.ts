// Client tasks of the sidecar (contract 1.3, unit FP4-3): Follow after WebFinger resolution, Undo(Follow), Reject(Follow) of a removed follower, the block of a domain, and the following collection.
// All servers are in this process. The remote server answers WebFinger and actor documents to a GET and captures each POST (a delivery).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { Endpoints, Person } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const PREFIX = "/internal/federation/v1";
const AS2 = "application/activity+json";

interface Delivery {
  url: string;
  body: Record<string, unknown>;
}

async function remoteActor(iri: string): Promise<unknown> {
  const url = new URL(iri);
  const name = iri.split("/").pop() ?? "";
  return await new Person({
    id: url,
    preferredUsername: name,
    name: name.charAt(0).toUpperCase() + name.slice(1),
    inbox: new URL(`${iri}/inbox`),
    endpoints: new Endpoints({ sharedInbox: new URL(`${url.origin}/inbox`) }),
  }).toJsonLd();
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const deliveries: Delivery[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/.well-known/webfinger") {
      const [user, host] = (url.searchParams.get("resource") ?? "").replace(/^acct:/, "").split("@");
      const jrd = { subject: `acct:${user}@${host}`, links: [{ rel: "self", type: AS2, href: `https://${host}/users/${user}` }] };
      return new Response(JSON.stringify(jrd), { headers: { "content-type": "application/jrd+json" } });
    }
    if (request.method === "GET") return new Response(JSON.stringify(await remoteActor(request.url.split("#")[0])), { headers: { "content-type": AS2 } });
    deliveries.push({ url: request.url, body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  const setTasks = (tasks: unknown[]) => fake.overrides.set(`GET ${PREFIX}/client-tasks?limit=10`, { status: 200, response: { tasks } });
  const setAck = (id: number, status = "handed_off") => fake.overrides.set(`POST ${PREFIX}/client-tasks/${id}/ack`, { status: 200, response: { id, status } });
  return { fake, sidecar, deliveries, setTasks, setAck, restore: () => (globalThis.fetch = realFetch) };
}

function task(id: number, kind: string, target: string, followingId: string | null) {
  return { id, kind, actor_public_id: ALICE, target, following_id: followingId, created_at: 1800000360.0, lease_until: 1800000700.0 };
}

Deno.test("a follow task resolves the handle, sends Follow with the follow ID, and gives the actor to Claire; a removed follower gets Reject", async () => {
  const { fake, sidecar, deliveries, setAck, restore } = setup();
  try {
    // The tasks of the contract fixture: a follow of @carol@remote.example and a reject_follow of erin.
    setAck(2);
    assert.equal(await sidecar.processClientTasks(), 2);
    assert.deepEqual(fake.clientAcks[0], { id: 1, ...(fixture("client_task_ack").request.body as object) });
    assert.deepEqual(fake.clientAcks[1], { id: 2, result: "handed_off", error_code: null, actor: null });

    const follow = deliveries.find((d) => d.body.type === "Follow");
    assert.ok(follow !== undefined);
    assert.equal(follow.url, "https://remote.example/users/carol/inbox");
    assert.equal(follow.body.id, `${ALICE_URL}#follows/1`);
    assert.equal(follow.body.actor, ALICE_URL);
    assert.equal(follow.body.object, "https://remote.example/users/carol");

    const reject = deliveries.find((d) => d.body.type === "Reject");
    assert.ok(reject !== undefined);
    assert.equal(reject.url, "https://remote.example/users/erin/inbox");
    assert.equal(reject.body.actor, ALICE_URL);
    const object = reject.body.object as Record<string, unknown>;
    assert.deepEqual([object.type, object.actor, object.object], ["Follow", "https://remote.example/users/erin", ALICE_URL]);
  } finally {
    restore();
  }
});

Deno.test("an undo task sends Undo of the same Follow ID", async () => {
  const { fake, sidecar, deliveries, setTasks, setAck, restore } = setup();
  try {
    setTasks([task(7, "undo_follow", "https://remote.example/users/dan", "3")]);
    setAck(7);
    assert.equal(await sidecar.processClientTasks(), 1);
    assert.deepEqual(fake.clientAcks, [{ id: 7, result: "handed_off", error_code: null, actor: null }]);
    assert.equal(deliveries.length, 1);
    const undo = deliveries[0].body;
    assert.equal(undo.type, "Undo");
    assert.equal(undo.id, `${ALICE_URL}#follows/3/undo`);
    assert.equal((undo.object as Record<string, unknown>).id, `${ALICE_URL}#follows/3`);
  } finally {
    restore();
  }
});

Deno.test("a follow of an actor on a blocked domain is rejected and sends nothing", async () => {
  const { fake, sidecar, deliveries, setTasks, setAck, restore } = setup();
  try {
    setTasks([task(8, "follow", "@mallory@blocked.example", "9")]);
    setAck(8, "rejected");
    assert.equal(await sidecar.processClientTasks(), 1);
    assert.deepEqual(fake.clientAcks, [{ id: 8, result: "rejected", error_code: "blocked_domain", actor: null }]);
    assert.equal(deliveries.length, 0);
  } finally {
    restore();
  }
});

Deno.test("the following collection shows the total only", async () => {
  const { sidecar, restore } = setup();
  try {
    const response = await sidecar.handle(new Request(`${ALICE_URL}/following`, { headers: { accept: AS2 } }));
    assert.equal(response.status, 200);
    const collection = await response.json();
    assert.equal(collection.totalItems, 1);
    assert.ok(!JSON.stringify(collection).includes("remote.example"));
  } finally {
    restore();
  }
});
