// Federation of shared knowledge in the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md, units KF1 and KF2): the local context document, the calls of the internal API against the contract fixtures, inbound activities, the signed read of a collection, and the reads of another server.
// All servers are in this process: the test task has no network permission. The other server (b.example) is a key pair, a table of documents that the fetch of this file gives, and a capture of the requests that it gets.

import assert from "node:assert/strict";
import { exportJwk, exportSpki, generateCryptoKeyPair, getDocumentLoader, MemoryKvStore, signRequest, verifyRequest } from "@fedify/fedify";
import { Application, CryptographicKey, Endpoints } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { CLAIRE_CONTEXT_DOCUMENT, CLAIRE_CONTEXT_IRI, kbBatches, KB_BATCH_ITEMS, readKbCollection, readRemoteDocument, readServer, REMOTE_READ_BYTES } from "../src/knowledge.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY, knowledgeFixture, knowledgeFixtureNames, wireFixture } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const REMOTE = "https://b.example";
const REMOTE_SERVER = `${REMOTE}/ap/actors/server`;
const AS2 = "application/activity+json";
// The shared knowledge base of this server in the contract fixtures of unit KF3 (its server is https://a.example there).
const KB = "1846000000000000200";
const PREFIX = "/internal/federation/v1";
// Each activity kind of the wire contract (vocabulary.ACTIVITY_KINDS of Claire). Grant and Revoke are ForgeFed types that the vocabulary of Fedify does not have; the sidecar does not give a knowledge activity to that vocabulary.
const ACTIVITY_KINDS = ["accept_follow", "accept_offer", "delete_document", "delete_knowledge_base", "follow", "grant", "offer_contribution", "reject_offer", "revoke", "undo_follow", "update_document", "update_knowledge_base"];

const remoteKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");

// The actor document of an address of another server. Each address has the same key pair.
async function remoteActor(iri: string): Promise<Record<string, unknown>> {
  const actor = new Application({
    id: new URL(iri),
    preferredUsername: "claire",
    inbox: new URL(`${iri}/inbox`),
    endpoints: new Endpoints({ sharedInbox: new URL(`${new URL(iri).origin}/ap/inbox`) }),
    publicKey: new CryptographicKey({ id: new URL(`${iri}#main-key`), owner: new URL(iri), publicKey: remoteKeys.publicKey }),
  });
  return await actor.toJsonLd() as Record<string, unknown>;
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  // The documents of the other server. The key is the address; an address that is not in the table gives an actor document.
  const documents = new Map<string, unknown>();
  const requests: Request[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    requests.push(request.clone());
    const url = request.url.split("#")[0];
    if (documents.has(url)) {
      const document = documents.get(url);
      return document === null ? new Response("Not Found", { status: 404 }) : new Response(JSON.stringify(document), { headers: { "content-type": "application/json" } });
    }
    return new Response(JSON.stringify(await remoteActor(url)), { headers: { "content-type": AS2 } });
  };
  const currentTime = Temporal.Instant.fromEpochMilliseconds(fake.now * 1000);
  const signed = async (method: string, path: string, options: { actor?: string; key?: CryptoKey; body?: unknown } = {}) => {
    const request = new Request(`${ORIGIN}${path}`, { method, headers: options.body === undefined ? { accept: AS2 } : { "content-type": AS2 }, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
    // The sidecar checks the time of the signature with its own clock, which is the clock of the fake Claire here.
    return await sidecar.handle(await signRequest(request, options.key ?? remoteKeys.privateKey, new URL(`${options.actor ?? REMOTE_SERVER}#main-key`), { currentTime }));
  };
  const knowledgeCalls = () => fake.calls.filter((call) => call.includes("/knowledge-"));
  return { fake, api, sidecar, documents, requests, signed, knowledgeCalls, restore: () => (globalThis.fetch = realFetch) };
}

// --- Context document ---

Deno.test("the Claire context is a local document for the loaders of Fedify, without a request", async () => {
  const { sidecar, requests, restore } = setup();
  try {
    assert.equal((CLAIRE_CONTEXT_DOCUMENT as { "@context": Record<string, unknown> })["@context"].claire, "https://w3id.org/claire-bible/ns#");
    const ctx = await sidecar.createContext();
    // The context loader reads each @context; the document loaders have the same table. (The loader that signs requests reads objects of other servers, never a context.)
    for (const loader of [getDocumentLoader(), ctx.contextLoader, ctx.documentLoader]) {
      const remote = await loader(CLAIRE_CONTEXT_IRI);
      assert.deepEqual(remote.document, CLAIRE_CONTEXT_DOCUMENT);
    }
    assert.deepEqual(requests.map((request) => request.url), []);
  } finally {
    restore();
  }
});

// --- Internal API client against the contract fixtures ---

Deno.test("each knowledge fixture has a test in this file", () => {
  assert.deepEqual(knowledgeFixtureNames(), ["knowledge_activity", "knowledge_activity_duplicate", "knowledge_activity_follow", "knowledge_collection_kb", "knowledge_collection_kb_page", "knowledge_collection_themes", "knowledge_collection_unsigned", "knowledge_kb_actor_documents", "knowledge_kb_actor_documents_page", "knowledge_kb_actor_followers", "knowledge_kb_actor_unsigned", "knowledge_kb_read_batch", "knowledge_kb_read_complete", "knowledge_kb_read_not_due", "knowledge_kb_reads", "knowledge_outbox_ack", "knowledge_outbox_ack_unknown", "knowledge_outbox_tasks", "knowledge_remote_read", "knowledge_remote_read_unknown", "knowledge_remote_reads", "knowledge_server_read", "knowledge_server_read_error", "knowledge_server_read_not_known", "knowledge_server_reads"]);
});

Deno.test("the knowledge calls of the client send the requests of the fixtures and accept their answers", async () => {
  const { fake, api, restore } = setup();
  try {
    const body = <T>(name: string) => knowledgeFixture(name).request.body as T;
    assert.deepEqual(await api.knowledgeServerReads(5), ["b.example"]);
    assert.equal(await api.recordServerRead(body("knowledge_server_read")), true);
    assert.equal(await api.recordServerRead(body("knowledge_server_read_error")), true);
    assert.equal(await api.recordServerRead(body("knowledge_server_read_not_known")), false);
    assert.deepEqual(await api.knowledgeKbReads(5), knowledgeFixture("knowledge_kb_reads").response.reads);
    assert.deepEqual(await api.recordKbRead(body("knowledge_kb_read_batch")), knowledgeFixture("knowledge_kb_read_batch").response);
    assert.deepEqual(await api.recordKbRead(body("knowledge_kb_read_complete")), knowledgeFixture("knowledge_kb_read_complete").response);
    assert.equal(await api.recordKbRead(body("knowledge_kb_read_not_due")), null);
    assert.deepEqual(await api.knowledgeCollection("kb", { signer: REMOTE_SERVER }), knowledgeFixture("knowledge_collection_kb").response);
    assert.deepEqual(await api.knowledgeCollection("kb", { signer: REMOTE_SERVER, page: "true" }), knowledgeFixture("knowledge_collection_kb_page").response);
    assert.deepEqual(await api.knowledgeCollection("themes", { signer: REMOTE_SERVER }), knowledgeFixture("knowledge_collection_themes").response);
    assert.equal(await api.knowledgeCollection("kb", { signer: null }), null);
    // The first fixture with the same request answers: the duplicate has the same shape.
    assert.deepEqual(await api.knowledgeActivity(body("knowledge_activity")), knowledgeFixture("knowledge_activity").response);
    assert.deepEqual(Object.keys(knowledgeFixture("knowledge_activity_duplicate").response), Object.keys(knowledgeFixture("knowledge_activity").response));
    // Unit KF3: the parts of a knowledge base actor, the outbound activities, and the reads for a curation.
    assert.deepEqual(await api.knowledgeActivity(body("knowledge_activity_follow")), knowledgeFixture("knowledge_activity_follow").response);
    const document = (name: string) => knowledgeFixture(name).response.document;
    assert.deepEqual(await api.kbActorPart(KB, { collection: "followers" }, { signer: REMOTE_SERVER }), document("knowledge_kb_actor_followers"));
    assert.deepEqual(await api.kbActorPart(KB, { collection: "documents" }, { signer: REMOTE_SERVER }), document("knowledge_kb_actor_documents"));
    assert.deepEqual(await api.kbActorPart(KB, { collection: "documents" }, { signer: REMOTE_SERVER, page: "true" }), document("knowledge_kb_actor_documents_page"));
    assert.equal(await api.kbActorPart(KB, { collection: "documents" }, { signer: null }), null);
    assert.deepEqual(await api.knowledgeOutboxTasks(10), knowledgeFixture("knowledge_outbox_tasks").response.tasks);
    assert.equal(await api.ackKnowledgeOutboxTask(1, "delivered", null), "delivered");
    assert.equal(await api.ackKnowledgeOutboxTask(99, "retry", "inbox_unreachable"), null);
    assert.deepEqual(await api.knowledgeRemoteReads(5), knowledgeFixture("knowledge_remote_reads").response.reads);
    assert.deepEqual(await api.recordRemoteRead(body("knowledge_remote_read")), knowledgeFixture("knowledge_remote_read").response);
    assert.equal(await api.recordRemoteRead(body("knowledge_remote_read_unknown")), null);
    // Each request was the request of a fixture: the path with its query, and the body.
    const paths = new Set(knowledgeFixtureNames().map((name) => `${knowledgeFixture(name).request.method} ${knowledgeFixture(name).request.path}`));
    for (const call of fake.calls) assert.ok(paths.has(call), call);
    assert.deepEqual(fake.knowledgePosts.map((post) => post.body), ["knowledge_server_read", "knowledge_server_read_error", "knowledge_server_read_not_known", "knowledge_kb_read_batch", "knowledge_kb_read_complete", "knowledge_kb_read_not_due", "knowledge_activity", "knowledge_activity_follow", "knowledge_remote_read", "knowledge_remote_read_unknown"].map((name) => body(name)));
    assert.deepEqual(fake.knowledgeAcks, [{ id: 1, ...body<object>("knowledge_outbox_ack") }, { id: 99, ...body<object>("knowledge_outbox_ack_unknown") }]);
  } finally {
    restore();
  }
});

// --- Inbound activities ---

for (const kind of ACTIVITY_KINDS) {
  Deno.test(`a signed ${kind} activity goes to Claire as a knowledge activity with its JSON unchanged`, async () => {
    const { fake, sidecar, signed, restore } = setup();
    try {
      const activity = wireFixture("activities", kind);
      fake.overrides.set("POST /internal/federation/v1/knowledge-activities", { status: 200, response: { decision: "processed", reason: null, journal_id: 7 } });
      const response = await signed("POST", "/ap/inbox", { actor: String(activity.actor), body: activity });
      assert.equal(response.status, 202, await response.text());
      // The body is the fixture, key for key: no new compaction and no change of a key. The signer is the actor of the activity; the shared inbox has no recipient.
      assert.deepEqual(fake.knowledgePosts, [{ path: "/knowledge-activities", body: { activity, signer: activity.actor, verified_by: "http-signature", recipient: null } }]);
      // It is not an inbox event and not a timeline event.
      assert.deepEqual([fake.events, fake.timelineEvents, fake.quoteEvents], [[], [], []]);
      assert.deepEqual(sidecar.knowledgeCounters, { processed: 1, failed: 0, refused: 0, duplicate: 0, rejected: 0 });
    } finally {
      restore();
    }
  });
}

Deno.test("a knowledge activity in the inbox of an actor has that actor as the recipient, and an unsigned one does not go to Claire", async () => {
  const { fake, sidecar, signed, knowledgeCalls, restore } = setup();
  try {
    const activity = wireFixture("activities", "update_knowledge_base");
    // The shared inbox: the request of the contract fixture. Claire has no handler, the row is `failed`, and the answer is 202: the sender must not send the activity again.
    assert.equal((await signed("POST", "/ap/inbox", { actor: String(activity.actor), body: activity })).status, 202);
    assert.deepEqual(fake.knowledgePosts[0].body, knowledgeFixture("knowledge_activity").request.body);
    assert.deepEqual(sidecar.knowledgeCounters, { processed: 0, failed: 1, refused: 0, duplicate: 0, rejected: 0 });
    // The inbox of an account actor and the inbox of a knowledge base actor: the recipient is the actor of the path.
    fake.overrides.set("POST /internal/federation/v1/knowledge-activities", { status: 200, response: { decision: "duplicate", reason: "failed", journal_id: 1 } });
    for (const owner of [`/ap/actors/${ALICE}`, "/ap/kb/1846000000000000100"]) {
      assert.equal((await signed("POST", `${owner}/inbox`, { actor: String(activity.actor), body: activity })).status, 202);
      assert.equal(fake.knowledgePosts.at(-1)?.body.recipient, `${ORIGIN}${owner}`);
    }
    // The signer is the actor of the key, also when the activity names another actor of that server: Claire compares the two servers.
    await signed("POST", "/ap/inbox", { actor: REMOTE_SERVER, body: activity });
    assert.equal(fake.knowledgePosts.at(-1)?.body.signer, REMOTE_SERVER);
    // A signature of RFC 9421 with the digest of the body is valid too.
    const rfc = await signRequest(new Request(`${ORIGIN}/ap/inbox`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify(activity) }), remoteKeys.privateKey, new URL(`${activity.actor}#main-key`), { spec: "rfc9421", currentTime: Temporal.Instant.fromEpochMilliseconds(fake.now * 1000) });
    assert.match(rfc.headers.get("signature-input") ?? "", /"content-digest"/);
    assert.equal((await sidecar.handle(rfc)).status, 202);
    const calls = knowledgeCalls().length;

    const unsigned = await sidecar.handle(new Request(`${ORIGIN}/ap/inbox`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify({ ...activity, id: `${activity.id}/2` }) }));
    assert.equal(unsigned.status, 401);
    // A signature of another key pair, and a body that changed after the signature, are not valid.
    const otherKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");
    assert.equal((await signed("POST", "/ap/inbox", { actor: String(activity.actor), key: otherKeys.privateKey, body: activity })).status, 401);
    const request = await signRequest(new Request(`${ORIGIN}/ap/inbox`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify(activity) }), remoteKeys.privateKey, new URL(`${activity.actor}#main-key`), { currentTime: Temporal.Instant.fromEpochMilliseconds(fake.now * 1000) });
    const changed = await sidecar.handle(new Request(request.url, { method: "POST", headers: request.headers, body: JSON.stringify({ ...activity, id: `${activity.id}/9` }) }));
    assert.equal(changed.status, 401);
    assert.equal(knowledgeCalls().length, calls);
  } finally {
    restore();
  }
});

Deno.test("an activity of a blocked domain and a failure of Claire give no knowledge activity that counts as done", async () => {
  const { fake, sidecar, signed, restore } = setup();
  try {
    const activity = wireFixture("activities", "update_knowledge_base");
    // Claire gave no answer: the sidecar answers 503, so the sender tries again. Claire gives the same answer for a second delivery.
    fake.overrides.set("POST /internal/federation/v1/knowledge-activities", { status: 500, response: { error: "internal_error" } });
    const failed = await signed("POST", "/ap/inbox", { actor: String(activity.actor), body: activity });
    assert.deepEqual([failed.status, failed.headers.get("retry-after")], [503, "5"]);
    // An activity near the body limit cannot go to Claire: 413, and no call.
    const before = fake.knowledgePosts.length;
    const large = await signed("POST", "/ap/inbox", { actor: String(activity.actor), body: { ...activity, summary: "x".repeat(1048576 - 4000) } });
    assert.equal(large.status, 413);
    assert.equal(fake.knowledgePosts.length, before);
    assert.deepEqual(sidecar.knowledgeCounters, { processed: 0, failed: 0, refused: 0, duplicate: 0, rejected: 0 });
    // A blocked domain: 403 after the signature check, and no call for the activity.
    fake.overrides.set("GET /internal/federation/v1/domain-blocks", { status: 200, response: { etag: "blocked", domains: ["b.example"] } });
    fake.config = { domain_blocks_etag: "blocked" };
    fake.now += 10;
    const calls = fake.knowledgePosts.length;
    const blocked = await signed("POST", "/ap/inbox", { actor: String(activity.actor), body: { ...activity, id: `${activity.id}/3` } });
    assert.equal(blocked.status, 403);
    assert.equal(fake.knowledgePosts.length, calls);
    assert.equal(sidecar.counters.blocked, 1);
  } finally {
    restore();
  }
});

// --- Collections of shared knowledge ---

Deno.test("a GET that a server actor signed gets the collection document of Claire, and each refusal is the same 404", async () => {
  const { fake, sidecar, signed, knowledgeCalls, restore } = setup();
  try {
    const signer = `signer=${encodeURIComponent(REMOTE_SERVER)}`;
    const root = await signed("GET", "/ap/kb");
    assert.equal(root.status, 200);
    assert.match(root.headers.get("content-type") ?? "", /application\/activity\+json/);
    assert.deepEqual(await root.json(), knowledgeFixture("knowledge_collection_kb").response.document);
    const page = await signed("GET", "/ap/kb?page=true");
    assert.deepEqual(await page.json(), knowledgeFixture("knowledge_collection_kb_page").response.document);
    const themes = await signed("GET", "/ap/themes");
    assert.deepEqual(await themes.json(), knowledgeFixture("knowledge_collection_themes").response.document);
    assert.deepEqual(knowledgeCalls(), [`GET /internal/federation/v1/knowledge-collections/kb?${signer}`, `GET /internal/federation/v1/knowledge-collections/kb?${signer}&page=true`, `GET /internal/federation/v1/knowledge-collections/themes?${signer}`]);

    // No signature: Claire gets no signer and refuses.
    const unsigned = await sidecar.handle(new Request(`${ORIGIN}/ap/kb`, { headers: { accept: AS2 } }));
    assert.deepEqual([unsigned.status, await unsigned.text()], [404, "Not Found"]);
    assert.equal(knowledgeCalls().at(-1), "GET /internal/federation/v1/knowledge-collections/kb");
    // A signature that is not valid (the key of another pair) is no signature.
    const otherKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");
    const forged = await signed("GET", "/ap/kb", { key: otherKeys.privateKey });
    assert.deepEqual([forged.status, await forged.text()], [404, "Not Found"]);
    assert.equal(knowledgeCalls().at(-1), "GET /internal/federation/v1/knowledge-collections/kb");
    // A refusal of Claire for a valid signature (for example a blocked domain) is the same answer.
    fake.overrides.set(`GET /internal/federation/v1/knowledge-collections/kb?${signer}`, { status: 404, response: { error: "not_found" } });
    const refused = await signed("GET", "/ap/kb");
    assert.deepEqual([refused.status, await refused.text()], [404, "Not Found"]);
    // A query key outside the contract has no answer and makes no call; another method gets 405.
    const calls = knowledgeCalls().length;
    assert.equal((await signed("GET", "/ap/kb?limit=5")).status, 404);
    assert.equal(knowledgeCalls().length, calls);
    assert.equal((await sidecar.handle(new Request(`${ORIGIN}/ap/kb`, { method: "POST", body: "{}" }))).status, 405);
    // While federation is off, the path is not there.
    fake.config = { federation: "off" };
    fake.now += 10;
    assert.equal((await sidecar.handle(new Request(`${ORIGIN}/ap/kb`, { headers: { accept: AS2 } }))).status, 404);
    assert.equal(knowledgeCalls().length, calls);
  } finally {
    restore();
  }
});

// --- Reads of another server ---

function remoteServer(documents: Map<string, unknown>, items: unknown[], pageSize = 1) {
  documents.set(`${REMOTE}/.well-known/nodeinfo`, { links: [{ rel: "http://nodeinfo.diaspora.software/ns/schema/2.1", href: `${REMOTE}/nodeinfo/2.1` }] });
  documents.set(`${REMOTE}/nodeinfo/2.1`, { version: "2.1", software: { name: "claire-bible", version: "1.0.0" }, protocols: ["activitypub"] });
  documents.set(`${REMOTE}/ap/kb`, { id: `${REMOTE}/ap/kb`, type: "OrderedCollection", totalItems: items.length, first: `${REMOTE}/ap/kb?page=true` });
  for (let start = 0, page = 0; start < items.length || page === 0; start += pageSize, page += 1) {
    const url = page === 0 ? `${REMOTE}/ap/kb?page=true` : `${REMOTE}/ap/kb?page=true&max_id=${page}`;
    const more = start + pageSize < items.length;
    documents.set(url, { id: url, type: "OrderedCollectionPage", partOf: `${REMOTE}/ap/kb`, orderedItems: items.slice(start, start + pageSize), ...(more ? { next: `${REMOTE}/ap/kb?page=true&max_id=${page + 1}` } : {}) });
  }
}

Deno.test("the read job reads NodeInfo, the server actor, and the /kb collection with signed requests and gives the results to Claire", async () => {
  const { fake, sidecar, documents, requests, restore } = setup();
  try {
    const kb = (knowledgeFixture("knowledge_kb_read_batch").request.body as { items: Record<string, unknown>[] }).items[0];
    remoteServer(documents, [kb]);
    assert.equal(await sidecar.processKnowledgeReads(), 2);
    // The bodies are the requests of the contract fixtures: the result of the server read, one batch with the knowledge base, and the end of the read.
    assert.deepEqual(fake.knowledgePosts, [
      { path: "/knowledge-server-reads", body: knowledgeFixture("knowledge_server_read").request.body },
      { path: "/knowledge-kb-reads", body: knowledgeFixture("knowledge_kb_read_batch").request.body },
      { path: "/knowledge-kb-reads", body: knowledgeFixture("knowledge_kb_read_complete").request.body },
    ]);
    assert.deepEqual(sidecar.knowledgeReadCounters, { servers_ok: 1, servers_failed: 0, collections_ok: 1, collections_failed: 0 });
    // Each request to the other server is a GET that the server actor of this server signed. The other server gets the key from the actor document of the sidecar.
    const remoteGets = requests.filter((request) => request.url.startsWith(`${REMOTE}/`));
    assert.deepEqual(remoteGets.map((request) => request.url), [`${REMOTE}/.well-known/nodeinfo`, `${REMOTE}/nodeinfo/2.1`, REMOTE_SERVER, `${REMOTE}/ap/kb`, `${REMOTE}/ap/kb?page=true`]);
    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${ORIGIN}/`)) return await standard(url);
      const answer = await sidecar.handle(new Request(url, { headers: { accept: AS2 } }));
      return { contextUrl: null, documentUrl: url, document: await answer.json() };
    };
    for (const request of remoteGets) {
      assert.equal(request.method, "GET");
      const key = await verifyRequest(request, { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime: Temporal.Now.instant() });
      assert.equal(key?.ownerId?.href, `${ORIGIN}/ap/actors/server`, request.url);
    }
    // While federation is off, the job asks Claire for nothing.
    fake.config = { federation: "off" };
    fake.now += 10;
    const calls = fake.calls.length;
    assert.equal(await sidecar.processKnowledgeReads(), 0);
    assert.deepEqual(fake.calls.slice(calls), ["GET /internal/federation/v1/config"]);
  } finally {
    restore();
  }
});

Deno.test("a read that fails gives Claire an error code and no item of another server", async () => {
  const { fake, api, sidecar, documents, restore } = setup();
  try {
    const ctx = await sidecar.createContext();
    const blocked: string[] = [];
    const deps = { api, documentLoader: await ctx.getDocumentLoader({ identifier: "server" }), origin: ORIGIN, isBlocked: (host: string) => Promise.resolve(blocked.includes(host)) };
    const kb = wireFixture("objects", "knowledge_base");
    remoteServer(documents, [kb, kb, kb]);

    // Server read: no NodeInfo, a NodeInfo document on another server, and a server actor with another ID.
    documents.set(`${REMOTE}/.well-known/nodeinfo`, null);
    assert.equal((await readServer(deps, "b.example")).error, "nodeinfo_unavailable");
    documents.set(`${REMOTE}/.well-known/nodeinfo`, { links: [{ rel: "http://nodeinfo.diaspora.software/ns/schema/2.1", href: "https://c.example/nodeinfo/2.1" }] });
    assert.equal((await readServer(deps, "b.example")).error, "nodeinfo_invalid");
    remoteServer(documents, [kb, kb, kb]);
    documents.set(REMOTE_SERVER, { ...(await remoteActor(REMOTE_SERVER)), id: "https://c.example/ap/actors/server" });
    assert.deepEqual(await readServer(deps, "b.example"), { domain: "b.example", ok: false, server_actor: null, software_name: null, software_version: null, shared_kb_count: null, error: "server_actor_invalid" });
    blocked.push("b.example");
    assert.equal((await readServer(deps, "b.example")).error, "blocked_domain");
    blocked.pop();

    // Collection read. Claire answers each batch (the fixtures answer only their own bodies).
    const read = { domain: "b.example", url: `${REMOTE}/ap/kb` };
    fake.overrides.set("POST /internal/federation/v1/knowledge-kb-reads", { status: 200, response: { domain: "b.example", stored: 1, refused: 0, refused_code: null, removed: null } });
    assert.equal(await readKbCollection(deps, read), "complete");
    // Three pages with one item each, then the end of the read.
    assert.deepEqual(fake.knowledgePosts.map((post) => [(post.body.items as unknown[]).length, post.body.complete, post.body.error]), [[1, false, null], [1, false, null], [1, false, null], [0, true, null]]);
    fake.knowledgePosts.length = 0;
    // A page on another server stops the read with an error: the items of the pages before it stay with Claire, and no record is deleted, because the read did not end.
    documents.set(`${REMOTE}/ap/kb?page=true`, { type: "OrderedCollectionPage", orderedItems: [kb], next: "https://c.example/ap/kb?page=true" });
    assert.equal(await readKbCollection(deps, read), "page_on_other_server");
    assert.deepEqual(fake.knowledgePosts.map((post) => [(post.body.items as unknown[]).length, post.body.complete, post.body.error]), [[1, false, null], [0, false, "page_on_other_server"]]);
    fake.knowledgePosts.length = 0;
    documents.set(`${REMOTE}/ap/kb`, null);
    assert.equal(await readKbCollection(deps, read), "collection_unavailable");
    documents.set(`${REMOTE}/ap/kb`, { type: "Note" });
    assert.equal(await readKbCollection(deps, read), "collection_invalid");
    assert.deepEqual(fake.knowledgePosts.map((post) => post.body.error), ["collection_unavailable", "collection_invalid"]);
    // Claire has no read for the domain (the lease ended, or a block started): the sidecar stops.
    fake.overrides.set("POST /internal/federation/v1/knowledge-kb-reads", { status: 404, response: { error: "not_found" } });
    remoteServer(documents, [kb]);
    assert.equal(await readKbCollection(deps, read), "no_read");
  } finally {
    restore();
  }
});

Deno.test("the items of a page go to Claire in batches inside the limits", () => {
  const item = { id: "https://b.example/ap/kb/1", name: "x".repeat(1000) };
  const batches = kbBatches(Array.from({ length: KB_BATCH_ITEMS * 2 + 1 }, () => item));
  assert.deepEqual(batches?.map((batch) => batch.length), [KB_BATCH_ITEMS, KB_BATCH_ITEMS, 1]);
  assert.equal(kbBatches([{ name: "x".repeat(600 * 1024) }]), null);
  assert.deepEqual(kbBatches([]), []);
});

// --- Unit KF3: knowledge base actors, outbound activities, and reads for a curation ---

const KB_ACTOR = `${ORIGIN}/ap/kb/${KB}`;
const kbKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");

// The answers of Claire for the knowledge base actor of this server: its actor document with the public key (the form of knowledge_federation.actors.actor_document) and its keys.
async function kbActor(fake: ReturnType<typeof setup>["fake"]): Promise<Record<string, unknown>> {
  const actor = {
    "@context": ["https://www.w3.org/ns/activitystreams", CLAIRE_CONTEXT_IRI, "https://w3id.org/security/v1"],
    id: KB_ACTOR,
    type: ["Group", "claire:KnowledgeBase"],
    name: "Early computing",
    summary: "About Early computing.",
    url: `${ORIGIN}/kb/${KB}/`,
    inbox: `${KB_ACTOR}/inbox`,
    outbox: `${KB_ACTOR}/outbox`,
    followers: `${KB_ACTOR}/followers`,
    "claire:documents": `${KB_ACTOR}/documents`,
    "claire:knowledgeType": "real",
    "claire:documentCount": 0,
    "claire:entityCount": 0,
    "claire:relationCount": 0,
    manuallyApprovesFollowers: false,
    publicKey: { id: `${KB_ACTOR}#main-key`, owner: KB_ACTOR, publicKeyPem: await exportSpki(kbKeys.publicKey) },
  };
  fake.overrides.set(`GET ${PREFIX}/knowledge-kb-actors/${KB}`, { status: 200, response: { document: actor } });
  const jwk = async (key: CryptoKey) => Object.fromEntries(Object.entries(await exportJwk(key)).filter(([, value]) => typeof value === "string"));
  fake.overrides.set(`GET ${PREFIX}/knowledge-kb-actors/${KB}/keys`, { status: 200, response: { public_id: KB, keys: [{ alg: "rsa2048", public_jwk: await jwk(kbKeys.publicKey), private_jwk: await jwk(kbKeys.privateKey) }] } });
  return actor;
}

function acceptTask(id: number, recipient = REMOTE_SERVER) {
  const follow = { id: `${REMOTE_SERVER}#follows/1`, type: "Follow", actor: REMOTE_SERVER, object: KB_ACTOR };
  const activity = { "@context": ["https://www.w3.org/ns/activitystreams", CLAIRE_CONTEXT_IRI], id: `${KB_ACTOR}#accepts/1`, type: "Accept", actor: KB_ACTOR, object: follow, to: [REMOTE_SERVER] };
  return { id, kind: "accept_follow", signer: KB, recipient, activity };
}

Deno.test("the paths of a knowledge base actor give the documents of Claire: the actor document without a signature, each other part only with one", async () => {
  const { fake, sidecar, signed, restore } = setup();
  try {
    const actor = await kbActor(fake);
    // The actor document is public: another server reads it to verify a signature.
    const plain = await sidecar.handle(new Request(KB_ACTOR, { headers: { accept: AS2 } }));
    assert.equal(plain.status, 200);
    assert.equal(plain.headers.get("content-type"), AS2);
    assert.deepEqual(await plain.json(), actor);
    assert.equal((await sidecar.handle(new Request(`${ORIGIN}/ap/kb/1846000000000000999`, { headers: { accept: AS2 } }))).status, 404);
    // A collection and a document: Claire gets the actor of the key and decides. The fixtures have the answers for the server actor of b.example.
    for (const [path, name] of [["followers", "knowledge_kb_actor_followers"], ["documents", "knowledge_kb_actor_documents"], ["documents?page=true", "knowledge_kb_actor_documents_page"]]) {
      const answer = await signed("GET", `/ap/kb/${KB}/${path}`);
      assert.equal(answer.status, 200, path);
      assert.deepEqual(await answer.json(), knowledgeFixture(name).response.document);
    }
    const query = new URLSearchParams({ signer: REMOTE_SERVER });
    fake.overrides.set(`GET ${PREFIX}/knowledge-kb-actors/${KB}/documents/1846000000000000300?${query}`, { status: 200, response: { document: { id: `${KB_ACTOR}/documents/1846000000000000300`, type: "Article" } } });
    assert.deepEqual(await (await signed("GET", `/ap/kb/${KB}/documents/1846000000000000300`)).json(), { id: `${KB_ACTOR}/documents/1846000000000000300`, type: "Article" });
    // Without a valid signature Claire gets no signer and refuses: the same 404 as for a path that has no answer.
    for (const path of [`/ap/kb/${KB}/documents`, `/ap/kb/${KB}/followers`, `/ap/kb/${KB}/documents/1846000000000000300`, `/ap/kb/${KB}/followers?page=true`, `/ap/kb/${KB}/keys`, `/ap/kb/${KB}?x=1`]) {
      const answer = await sidecar.handle(new Request(`${ORIGIN}${path}`, { headers: { accept: AS2 } }));
      assert.equal(answer.status, 404, path);
      assert.equal(await answer.text(), "Not Found");
    }
    // The private key of the actor is in no answer of a public path.
    assert.ok(!fake.calls.some((call) => call.includes("/keys")));
  } finally {
    restore();
  }
});

Deno.test("an outbound activity goes to the inbox of its recipient as the JSON of Claire, signed with the key of the knowledge base actor", async () => {
  const { fake, sidecar, documents, requests, restore } = setup();
  try {
    await kbActor(fake);
    const task = acceptTask(7);
    fake.overrides.set(`GET ${PREFIX}/knowledge-outbox-tasks?limit=10`, { status: 200, response: { tasks: [task] } });
    fake.overrides.set(`POST ${PREFIX}/knowledge-outbox-tasks/7/ack`, { status: 200, response: { id: 7, status: "delivered" } });
    // The inbox of the other server answers 202.
    documents.set(`${REMOTE_SERVER}/inbox`, {});
    assert.equal(await sidecar.processKnowledgeOutbox(), 1);
    assert.deepEqual(fake.knowledgeAcks, [{ id: 7, result: "delivered", error_code: null }]);
    assert.deepEqual(sidecar.knowledgeDeliveryCounters, { delivered: 1, retry: 0, rejected: 0, reads_ok: 0, reads_failed: 0 });
    // The actor document of the recipient was read with a GET that the server actor signed; then one POST went to its inbox.
    const remote = requests.filter((request) => request.url.startsWith(`${REMOTE}/`));
    assert.deepEqual(remote.map((request) => `${request.method} ${request.url}`), [`GET ${REMOTE_SERVER}`, `POST ${REMOTE_SERVER}/inbox`]);
    const post = remote[1];
    assert.equal(post.headers.get("content-type"), AS2);
    // The body is the activity of the task, key for key: the sidecar does not make an object of the Fedify vocabulary from it.
    assert.equal(await post.clone().text(), JSON.stringify(task.activity));
    // The other server verifies the signature with the public key of the actor document that this sidecar gives at /ap/kb/{number}.
    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${ORIGIN}/`)) return await standard(url);
      const answer = await sidecar.handle(new Request(url, { headers: { accept: AS2 } }));
      return { contextUrl: null, documentUrl: url, document: await answer.json() };
    };
    const key = await verifyRequest(post, { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime: Temporal.Instant.fromEpochMilliseconds(fake.now * 1000) });
    assert.equal(key?.ownerId?.href, KB_ACTOR);
    assert.equal(key?.id?.href, `${KB_ACTOR}#main-key`);
    // The GET of the actor document has the signature of the server actor.
    const serverKey = await verifyRequest(remote[0], { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime: Temporal.Now.instant() });
    assert.equal(serverKey?.ownerId?.href, `${ORIGIN}/ap/actors/server`);
  } finally {
    restore();
  }
});

Deno.test("the result of a delivery tells Claire if the task can come again", async () => {
  const { fake, sidecar, documents, requests, restore } = setup();
  try {
    await kbActor(fake);
    const inboxAnswer = { status: 500 };
    const realFetch = globalThis.fetch;
    globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = new Request(input, init);
      if (request.method !== "POST") return await realFetch(input, init);
      requests.push(request.clone());
      if (inboxAnswer.status === 0) throw new TypeError("connection refused");
      return new Response(null, { status: inboxAnswer.status });
    };
    const run = async (task: Record<string, unknown>) => {
      fake.knowledgeAcks.length = 0;
      fake.overrides.set(`GET ${PREFIX}/knowledge-outbox-tasks?limit=10`, { status: 200, response: { tasks: [task] } });
      fake.overrides.set(`POST ${PREFIX}/knowledge-outbox-tasks/${task.id}/ack`, { status: 200, response: { id: task.id, status: "pending" } });
      await sidecar.processKnowledgeOutbox();
      return `${fake.knowledgeAcks[0].result} ${fake.knowledgeAcks[0].error_code}`;
    };
    // An error of the inbox can go away: the task comes again. A refusal is final.
    assert.equal(await run(acceptTask(1)), "retry inbox_status_500");
    inboxAnswer.status = 0;
    assert.equal(await run(acceptTask(2)), "retry inbox_unreachable");
    inboxAnswer.status = 404;
    assert.equal(await run(acceptTask(3)), "rejected inbox_status_404");
    inboxAnswer.status = 202;
    // The actor document of the recipient is not there: the server can come back.
    documents.set(`${REMOTE}/ap/kb/1`, null);
    assert.equal(await run(acceptTask(4, `${REMOTE}/ap/kb/1`)), "retry recipient_unavailable");
    // An actor document whose inbox is on another server gets no delivery.
    documents.set(`${REMOTE}/ap/kb/2`, { id: `${REMOTE}/ap/kb/2`, type: "Group", inbox: "https://c.example/inbox" });
    assert.equal(await run(acceptTask(5, `${REMOTE}/ap/kb/2`)), "rejected recipient_invalid");
    // A signer that has no key (the knowledge base is not an actor now).
    assert.equal(await run({ ...acceptTask(6), signer: "1846000000000000999" }), "rejected no_actor_key");
    // A blocked domain gets no request.
    const before = requests.length;
    const blocks = fixture("domain_blocks").response as { domains: string[] };
    assert.equal(await run(acceptTask(7, `https://${blocks.domains[0]}/ap/actors/server`)), "rejected blocked_domain");
    assert.equal(requests.length, before);
    // While federation is off, the job asks Claire for nothing.
    fake.config = { federation: "off" };
    fake.now += 10;
    const calls = fake.calls.length;
    assert.equal(await sidecar.processKnowledgeOutbox(), 0);
    assert.equal(await sidecar.processKnowledgeRemoteReads(), 0);
    assert.deepEqual(fake.calls.slice(calls), ["GET /internal/federation/v1/config"]);
  } finally {
    restore();
  }
});

Deno.test("a read for a curation is a GET that the server actor signs, and its JSON goes to Claire without a change", async () => {
  const { fake, api, sidecar, documents, requests, restore } = setup();
  try {
    // The read of the contract fixture: the documents collection of the knowledge base of b.example. Its answer is the page of the fixture.
    const read = (knowledgeFixture("knowledge_remote_reads").response.reads as { id: number; url: string }[])[0];
    const answer = knowledgeFixture("knowledge_remote_read").request.body as { document: Record<string, unknown> };
    documents.set(read.url, answer.document);
    assert.equal(await sidecar.processKnowledgeRemoteReads(), 1);
    assert.deepEqual(fake.knowledgePosts, [{ path: "/knowledge-remote-reads", body: knowledgeFixture("knowledge_remote_read").request.body }]);
    assert.equal(sidecar.knowledgeDeliveryCounters.reads_ok, 1);
    const gets = requests.filter((request) => request.url.startsWith(`${REMOTE}/`));
    assert.deepEqual(gets.map((request) => `${request.method} ${request.url}`), [`GET ${read.url}`]);
    assert.ok(gets[0].headers.has("signature"));

    // A read that fails gives Claire an error code and no document.
    const ctx = await sidecar.createContext();
    const blocked: string[] = [];
    const deps = { api, documentLoader: await ctx.getDocumentLoader({ identifier: "server" }), origin: ORIGIN, isBlocked: (host: string) => Promise.resolve(blocked.includes(host)) };
    documents.set(`${REMOTE}/gone`, null);
    assert.deepEqual(await readRemoteDocument(deps, { id: 2, url: `${REMOTE}/gone` }), { document: null, error: "document_unavailable" });
    documents.set(`${REMOTE}/list`, [1, 2]);
    assert.deepEqual(await readRemoteDocument(deps, { id: 3, url: `${REMOTE}/list` }), { document: null, error: "document_invalid" });
    documents.set(`${REMOTE}/large`, { content: "x".repeat(REMOTE_READ_BYTES) });
    assert.deepEqual(await readRemoteDocument(deps, { id: 4, url: `${REMOTE}/large` }), { document: null, error: "document_too_large" });
    assert.deepEqual(await readRemoteDocument(deps, { id: 5, url: "http://b.example/ap/kb/1/documents" }), { document: null, error: "document_invalid" });
    assert.deepEqual(await readRemoteDocument(deps, { id: 6, url: "not an address" }), { document: null, error: "document_invalid" });
    blocked.push("b.example");
    const before = requests.length;
    assert.deepEqual(await readRemoteDocument(deps, { id: 7, url: read.url }), { document: null, error: "blocked_domain" });
    assert.equal(requests.length, before);
  } finally {
    restore();
  }
});
