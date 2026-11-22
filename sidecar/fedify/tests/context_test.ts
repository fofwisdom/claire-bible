// Thread fetch of the sidecar (contract 1.5, unit FP4-6): the context collection, the reply tree, the limits, the rules for an object in a collection, and the calls to Claire.
// All servers are in this process, as in the other test files: the test task has no network permission. The remote servers are a table of documents that the fetch of this file gives; another address gives an actor document.

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { Collection, CollectionPage, Note, type Object as ASObject, OrderedCollection, Person, PUBLIC_COLLECTION } from "@fedify/fedify/vocab";
import { fetchThread, objectBatches, OBJECTS_BATCH, requestLimit } from "../src/context.ts";
import { InternalApi, type TimelineObject } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const PREFIX = "/internal/federation/v1";
const AS2 = "application/activity+json";
const DAN = "https://remote.example/users/dan";
const ERIN = "https://remote.example/users/erin";
const ROOT = "https://remote.example/notes/40";
const CONTEXT = "https://remote.example/contexts/40";

async function remoteActor(iri: string): Promise<unknown> {
  const name = iri.split("/").pop() ?? "";
  return await new Person({ id: new URL(iri), preferredUsername: name, name: name.charAt(0).toUpperCase() + name.slice(1), inbox: new URL(`${iri}/inbox`) }).toJsonLd();
}

function note(id: string, author: string, values: Record<string, unknown> = {}): Note {
  return new Note({ id: new URL(id), attribution: new URL(author), content: "<p>text</p>", to: PUBLIC_COLLECTION, ...values });
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const documents = new Map<string, unknown>();
  const fetched: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new Request(input, init).url.split("#")[0];
    fetched.push(url);
    if (url.endsWith("/missing")) return new Response("Not Found", { status: 404 });
    return new Response(JSON.stringify(documents.get(url) ?? await remoteActor(url)), { headers: { "content-type": AS2 } });
  };
  const add = async (object: ASObject, at?: string) => documents.set(at ?? object.id!.href, await object.toJsonLd());
  // A thread fetch with the signed loader of the sidecar, as a task does it.
  const fetchWith = async (maxItems: number, maxDepth = 5, blocked: string[] = []) => {
    const ctx = await sidecar.createContext();
    return await fetchThread(ROOT, { documentLoader: await ctx.getDocumentLoader({ identifier: "116412345678901234" }), contextLoader: ctx.contextLoader, origin: ORIGIN, isBlocked: (host) => Promise.resolve(blocked.includes(host)), maxItems, maxDepth });
  };
  const iris = (result: TimelineObject[] | string) => (result as TimelineObject[]).map((object) => object.object_iri);
  return { fake, sidecar, documents, fetched, add, fetchWith, iris, restore: () => (globalThis.fetch = realFetch) };
}

// The thread of the contract fixtures: the root of dan with a context collection that has the root and one public reply of erin.
async function fixtureThread(add: (object: ASObject, at?: string) => Promise<unknown>) {
  await add(note(ROOT, DAN, { contexts: [new URL(CONTEXT)] }));
  await add(note("https://remote.example/notes/41", ERIN, { content: "<p>A reply from Erin</p>", published: Temporal.Instant.fromEpochMilliseconds(1_800_000_395_000), contexts: [new URL(CONTEXT)], replyTarget: new URL(ROOT) }));
  await add(new OrderedCollection({ id: new URL(CONTEXT), items: [new URL(ROOT), new URL("https://remote.example/notes/41")] }));
}

Deno.test("a thread fetch task reads the context collection and gives the objects to Claire as the contract fixtures", async () => {
  const { fake, sidecar, add, restore } = setup();
  try {
    await fixtureThread(add);
    fake.overrides.set(`POST ${PREFIX}/client-tasks/4/ack`, { status: 200, response: { id: 4, status: "handed_off" } });
    assert.equal(await sidecar.processContextTasks(), 1);
    // The body of the request is the body of the fixture: the fake answers 404 for another body, and then there is no acknowledgement.
    assert.deepEqual(fake.calls.filter((call) => call.includes("/context-") || call.includes("/client-tasks")), [`GET ${fixture("context_tasks").request.path}`, `POST ${fixture("context_objects").request.path}`, `POST ${PREFIX}/client-tasks/4/ack`]);
    assert.deepEqual(fake.clientAcks, [{ id: 4, result: "handed_off", error_code: null, actor: null }]);
    assert.equal(sidecar.outboxCounters.handed_off, 1);
  } finally {
    restore();
  }
});

Deno.test("a start object that is not available rejects the task", async () => {
  const { fake, sidecar, documents, restore } = setup();
  try {
    documents.set(ROOT, await remoteActor(ROOT));
    fake.overrides.set(`POST ${PREFIX}/client-tasks/4/ack`, { status: 200, response: { id: 4, status: "rejected" } });
    assert.equal(await sidecar.processContextTasks(), 1);
    assert.deepEqual(fake.clientAcks, [{ id: 4, result: "rejected", error_code: "object_not_found", actor: null }]);
    assert.ok(!fake.calls.some((call) => call.includes("/context-objects")));
  } finally {
    restore();
  }
});

Deno.test("a paged context collection stops at the item limit, and the fetch makes no request to this server or to a blocked domain", async () => {
  const { add, fetchWith, fetched, iris, restore } = setup();
  try {
    await add(note(ROOT, DAN, { contexts: [new URL(CONTEXT)] }));
    const ids = (from: number, to: number) => Array.from({ length: to - from + 1 }, (_, i) => `https://remote.example/notes/${from + i}`);
    for (const id of ids(41, 46)) await add(note(id, ERIN, { replyTarget: new URL(ROOT) }));
    await add(note("https://blocked.example/notes/1", "https://blocked.example/users/x", { replyTarget: new URL(ROOT) }));
    await add(new OrderedCollection({ id: new URL(CONTEXT), first: new URL(`${CONTEXT}?page=1`) }));
    await add(new CollectionPage({ id: new URL(`${CONTEXT}?page=1`), partOf: new URL(CONTEXT), next: new URL(`${CONTEXT}?page=2`), items: [new URL(ROOT), new URL(`${ORIGIN}/ap/objects/816412345678901234`), new URL("https://blocked.example/notes/1"), ...ids(41, 43).map((id) => new URL(id))] }));
    await add(new CollectionPage({ id: new URL(`${CONTEXT}?page=2`), partOf: new URL(CONTEXT), items: ids(44, 46).map((id) => new URL(id)) }));

    assert.deepEqual(iris(await fetchWith(100, 5, ["blocked.example"])), ids(41, 46));
    assert.ok(!fetched.some((url) => url.startsWith(ORIGIN) || url.includes("blocked.example")));

    assert.deepEqual(iris(await fetchWith(4, 5, ["blocked.example"])), ids(41, 44));
    assert.deepEqual(await fetchWith(0), []);
  } finally {
    restore();
  }
});

Deno.test("an object without a context collection gives the reply tree: the parents and the replies collections, within the depth", async () => {
  const { add, fetchWith, iris, restore } = setup();
  try {
    const replies = (id: string, items: string[]) => new Collection({ id: new URL(`${id}/replies`), items: items.map((item) => new URL(item)) });
    // The context of Mastodon before 4.5 is an address without a collection.
    await add(note("https://remote.example/notes/39", DAN));
    await add(note(ROOT, DAN, { contexts: [new URL("https://remote.example/contexts/missing")], replyTarget: new URL("https://remote.example/notes/39"), replies: replies(ROOT, ["https://remote.example/notes/41", "https://remote.example/notes/42"]) }));
    await add(note("https://remote.example/notes/41", ERIN, { replyTarget: new URL(ROOT), replies: replies("https://remote.example/notes/41", ["https://remote.example/notes/43"]) }));
    await add(note("https://remote.example/notes/42", ERIN, { replyTarget: new URL(ROOT) }));
    await add(note("https://remote.example/notes/43", DAN, { replyTarget: new URL("https://remote.example/notes/41") }));

    assert.deepEqual(iris(await fetchWith(100)).sort(), [39, 41, 42, 43].map((n) => `https://remote.example/notes/${n}`));
    assert.deepEqual(iris(await fetchWith(100, 1)).sort(), [39, 41, 42].map((n) => `https://remote.example/notes/${n}`));
    assert.deepEqual(await fetchWith(100, 0), []);
  } finally {
    restore();
  }
});

Deno.test("only a public post from its own server with an author on that server goes to Claire", async () => {
  const { add, documents, fetchWith, iris, restore } = setup();
  try {
    await add(note(ROOT, DAN, { contexts: [new URL(CONTEXT)] }));
    const real = note("https://other.example/notes/1", "https://other.example/users/zoe", { content: "<p>real text</p>", replyTarget: new URL(ROOT) });
    await add(real);
    await add(new Note({ id: new URL("https://remote.example/notes/51"), attribution: new URL(ERIN), content: "<p>private</p>", to: new URL(`${ERIN}/followers`), replyTarget: new URL(ROOT) }));
    await add(note("https://remote.example/notes/52", "https://other.example/users/zoe", { replyTarget: new URL(ROOT) }));
    await add(note("https://remote.example/notes/53", ERIN, { content: `<p>${"x".repeat(70 * 1024)}</p>`, replyTarget: new URL(ROOT) }));
    // The collection of remote.example has a changed copy of the object of other.example, and an object whose ID is on a server that gives another document.
    const forged = note("https://other.example/notes/1", "https://other.example/users/zoe", { content: "<p>forged text</p>", replyTarget: new URL(ROOT) });
    const absent = note("https://other.example/notes/missing", "https://other.example/users/zoe", { content: "<p>forged text</p>", replyTarget: new URL(ROOT) });
    await add(new OrderedCollection({ id: new URL(CONTEXT), items: [forged, absent, new URL("https://remote.example/notes/51"), new URL("https://remote.example/notes/52"), new URL("https://remote.example/notes/53")] }));

    const result = await fetchWith(100) as TimelineObject[];
    assert.deepEqual(iris(result), ["https://other.example/notes/1"]);
    assert.equal(result[0].content_html, "<p>real text</p>");
    assert.deepEqual(result[0].author, { iri: "https://other.example/users/zoe", name: "Zoe", handle: "@zoe@other.example" });
    assert.ok(documents.size > 0);
  } finally {
    restore();
  }
});

Deno.test("the request limit stops a fetch, and the objects go to Claire in parts", async () => {
  const { add, fetchWith, fetched, restore } = setup();
  try {
    await add(note(ROOT, DAN, { contexts: [new URL(CONTEXT)] }));
    const ids = Array.from({ length: 60 }, (_, i) => `https://remote.example/notes/${100 + i}`);
    for (const id of ids) await add(note(id, `https://remote.example/users/u${id.slice(-3)}`, { replyTarget: new URL(ROOT) }));
    await add(new OrderedCollection({ id: new URL(CONTEXT), items: ids.map((id) => new URL(id)) }));
    fetched.length = 0;
    const result = await fetchWith(2) as TimelineObject[];
    assert.equal(result.length, 2);
    // The requests of Fedify for JSON-LD contexts are not in the table of this file: each address here is a document of a remote server.
    assert.ok(fetched.length <= requestLimit(2), `${fetched.length} requests`);

    const many = Array.from({ length: OBJECTS_BATCH + 5 }, () => result[0]);
    assert.deepEqual(objectBatches(many).map((part) => part.length), [OBJECTS_BATCH, 5]);
    const large = { ...result[0], content_html: "x".repeat(60 * 1024) };
    assert.ok(objectBatches(Array.from({ length: 20 }, () => large)).every((part) => new TextEncoder().encode(JSON.stringify(part)).byteLength < 1024 * 1024));
    assert.deepEqual(objectBatches([]), []);
  } finally {
    restore();
  }
});
