// Public federation surface against the contract fixtures: WebFinger, NodeInfo, actor documents, and the server actor (Fedify dispatcher wiring).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { CONFIG_CACHE_SECONDS, KEY_CACHE_SECONDS, KNOWLEDGE_SELF_CHECK_PATHS, publicSelfCheck, SELF_CHECK_PATH, SELF_CHECK_TTL_SECONDS, Sidecar, SIDECAR_HEADER } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const SERVER_URL = `${ORIGIN}/ap/actors/server`;
const AS2 = "application/activity+json";
const JRD = "application/jrd+json";
// The reverse proxy sends requests to this address. It must not be in an answer.
const LISTEN = "http://127.0.0.1:8790";

function setup() {
  const fake = fakeClaire();
  // The sidecar and Claire have the same clock.
  const clock = fake;
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => clock.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => clock.now });
  const bodies: string[] = [];
  const get = async (path: string, accept: string, headers: Record<string, string> = {}, base = LISTEN) => {
    const response = await sidecar.handle(new Request(`${base}${path}`, { headers: { accept, ...headers } }));
    const text = await response.text();
    bodies.push(text + JSON.stringify([...response.headers]));
    let json: Record<string, unknown> | null = null;
    try {
      json = JSON.parse(text);
    } catch {
      json = null;
    }
    return { status: response.status, json, text, headers: response.headers };
  };
  return { fake, clock, sidecar, get, bodies };
}

const webfinger = (handle: string) => `/.well-known/webfinger?resource=${encodeURIComponent(`acct:${handle}`)}`;
type Link = { rel?: string; type?: string; href?: string };
const link = (jrd: Record<string, unknown> | null, rel: string) => ((jrd?.links ?? []) as Link[]).find((l) => l.rel === rel);

Deno.test("a handle resolves to an actor document with the canonical origin", async () => {
  const { get, bodies } = setup();
  const finger = await get(webfinger("alice@claire.example.com"), JRD);
  assert.equal(finger.status, 200);
  assert.equal(finger.json?.subject, "acct:alice@claire.example.com");
  assert.equal(link(finger.json, "self")?.href, ALICE_URL);
  assert.equal(link(finger.json, "self")?.type, AS2);
  assert.equal(link(finger.json, "http://webfinger.net/rel/profile-page")?.href, `${ORIGIN}/@alice/`);

  const actor = await get(new URL(ALICE_URL).pathname, AS2);
  assert.equal(actor.status, 200);
  const doc = actor.json!;
  assert.equal(doc.id, ALICE_URL);
  assert.equal(doc.type, "Person");
  assert.equal(doc.preferredUsername, "alice");
  assert.equal(doc.name, "Alice Kim");
  assert.equal(doc.url, `${ORIGIN}/@alice/`);
  assert.equal(doc.inbox, `${ALICE_URL}/inbox`);
  assert.equal((doc.endpoints as Record<string, unknown>).sharedInbox, `${ORIGIN}/ap/inbox`);
  assert.equal(doc.manuallyApprovesFollowers, false);
  assert.equal(doc.published, "2027-01-15T08:01:40.5Z");
  const publicKey = doc.publicKey as Record<string, unknown>;
  assert.equal(publicKey.owner, ALICE_URL);
  assert.match(String(publicKey.publicKeyPem), /^-----BEGIN PUBLIC KEY-----/);
  const assertion = ([doc.assertionMethod].flat() as Record<string, unknown>[]);
  assert.ok(assertion.some((m) => m.controller === ALICE_URL && String(m.publicKeyMultibase).startsWith("z6Mk")), "Ed25519 multikey");
  assert.equal(doc.followers, `${ALICE_URL}/followers`);
  assert.equal(doc.outbox, `${ALICE_URL}/outbox`);
  // The following collection (contract 1.3, unit FP4-3) shows its total only.
  assert.equal(doc.following, `${ALICE_URL}/following`);
  // Fields that the account does not have are not in the document.
  for (const field of ["summary", "icon"]) assert.ok(!(field in doc), field);
  assert.ok(!bodies.join("").includes("127.0.0.1"), "the request host is not in an answer");
});

Deno.test("the request host and the forwarded headers do not change the URLs", async () => {
  const { get } = setup();
  const forged = { host: "evil.example", "x-forwarded-host": "evil.example", "x-forwarded-proto": "http", forwarded: "host=evil.example" };
  const actor = await get(`/ap/actors/${ALICE}`, AS2, forged, "http://evil.example");
  assert.equal(actor.json?.id, ALICE_URL);
  assert.ok(!actor.text.includes("evil.example"));
  const finger = await get(webfinger("alice@claire.example.com"), JRD, forged, "http://evil.example");
  assert.equal(link(finger.json, "self")?.href, ALICE_URL);
});

Deno.test("accounts that are not actors get the same 404", async () => {
  const { get } = setup();
  const reference = await get(webfinger("nobody@claire.example.com"), JRD);
  assert.equal(reference.status, 404);
  for (const path of [webfinger("private_bob@claire.example.com"), webfinger(`${ALICE}@claire.example.com`), webfinger("alice@other.example")]) {
    const response = await get(path, JRD);
    assert.equal(response.status, 404, path);
    assert.equal(response.text, reference.text, path);
  }
  const actorReference = await get("/ap/actors/999", AS2);
  assert.equal(actorReference.status, 404);
  for (const path of ["/ap/actors/216412345678901234", "/ap/actors/alice", "/ap/actors/0116412345678901234"]) {
    const response = await get(path, AS2);
    assert.equal(response.status, 404, path);
    assert.equal(response.text, actorReference.text, path);
  }
});

Deno.test("the server actor is an Application with its own keys", async () => {
  const { get } = setup();
  const finger = await get(webfinger("claire@claire.example.com"), JRD);
  assert.equal(link(finger.json, "self")?.href, SERVER_URL);
  const actor = await get("/ap/actors/server", AS2);
  assert.equal(actor.status, 200);
  assert.equal(actor.json?.type, "Application");
  assert.equal(actor.json?.id, SERVER_URL);
  assert.equal(actor.json?.preferredUsername, "claire");
  assert.equal(actor.json?.inbox, `${SERVER_URL}/inbox`);
  const alice = await get(`/ap/actors/${ALICE}`, AS2);
  assert.notEqual((actor.json?.publicKey as Record<string, unknown>).publicKeyPem, (alice.json?.publicKey as Record<string, unknown>).publicKeyPem);
});

Deno.test("NodeInfo shows the software and federated actors only", async () => {
  const { get } = setup();
  const discovery = await get("/.well-known/nodeinfo", "application/json");
  assert.equal(discovery.status, 200);
  assert.equal(link(discovery.json, "http://nodeinfo.diaspora.software/ns/schema/2.1")?.href, `${ORIGIN}/nodeinfo/2.1`);
  const info = (await get("/nodeinfo/2.1", "application/json")).json!;
  assert.deepEqual(info.software, { name: "claire-bible", version: "0.0.0+fixture" });
  assert.deepEqual(info.protocols, ["activitypub"]);
  assert.equal(info.openRegistrations, false);
  assert.equal((info.usage as { users: { total: number } }).users.total, 1);
});

Deno.test("all paths are 404 when federation is off, in each mode", async () => {
  for (const state of [{ federation: "off" }, { federation: "off", mode: "single" }]) {
    const { get, fake, bodies } = setup();
    fake.config = { ...state, federated_actors: 0 };
    for (const [path, accept] of [[webfinger("alice@claire.example.com"), JRD], [webfinger("claire@claire.example.com"), JRD], [`/ap/actors/${ALICE}`, AS2], ["/ap/actors/server", AS2], ["/.well-known/nodeinfo", "application/json"], ["/nodeinfo/2.1", "application/json"]]) {
      const response = await get(path, accept);
      assert.equal(response.status, 404, `${JSON.stringify(state)} ${path}`);
      assert.equal(response.text, "Not Found");
    }
    // The sidecar asked Claire for the config only.
    assert.ok(!bodies.join("").includes("alice"));
    assert.ok(fake.calls.every((call) => call.endsWith("/config")));
  }
});

Deno.test("the server actor and NodeInfo work in single mode while federation is on", async () => {
  // KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5: the server actor and the federation of shared knowledge work in single mode. Claire decides which account is an actor (KD2).
  const { get, fake } = setup();
  fake.config = { mode: "single" };
  for (const [path, accept] of [["/ap/actors/server", AS2], ["/.well-known/nodeinfo", "application/json"]]) {
    assert.equal((await get(path, accept)).status, 200, path);
  }
});

Deno.test("paths of Claire are 404 without a call to Claire, and paths that v1 does not have are 404", async () => {
  const { sidecar, fake } = setup();
  for (const path of ["/", "/api/me", "/@alice/", "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource", "/.well-known/", "/nodeinfo/2.0", "/ap", "/internal/federation/v1/config"]) {
    assert.equal((await sidecar.handle(new Request(`${LISTEN}${path}`))).status, 404, path);
  }
  assert.deepEqual(fake.calls, []);
  for (const path of ["/ap/objects/1", "/ap/objects/x", "/ap/objects/1/replies"]) {
    const response = await sidecar.handle(new Request(`${LISTEN}${path}`, { headers: { accept: AS2 } }));
    assert.equal(response.status, 404, path);
  }
});

Deno.test("credentials are ignored and no cookie is set", async () => {
  const { get } = setup();
  const plain = await get(`/ap/actors/${ALICE}`, AS2);
  const withCredentials = await get(`/ap/actors/${ALICE}`, AS2, { cookie: "claire_session=secret-session", authorization: "Bearer secret-token" });
  assert.equal(withCredentials.status, 200);
  assert.equal(withCredentials.text, plain.text);
  assert.equal(withCredentials.headers.get("set-cookie"), null);
});

Deno.test("an actor document has no HTML form", async () => {
  const { get } = setup();
  assert.equal((await get(`/ap/actors/${ALICE}`, "text/html")).status, 406);
});

Deno.test("no private key and no account data outside the actor record is in a public answer", async () => {
  const { get, bodies } = setup();
  await get(webfinger("alice@claire.example.com"), JRD);
  await get(`/ap/actors/${ALICE}`, AS2);
  await get("/ap/actors/server", AS2);
  await get("/nodeinfo/2.1", "application/json");
  const all = bodies.join("");
  for (const name of ["actor_keys", "server_actor_keys"]) {
    for (const key of fixture(name).response.keys as { private_jwk: Record<string, string> }[]) {
      for (const field of ["d", "p", "q", "dp", "dq", "qi"]) {
        if (field in key.private_jwk) assert.ok(!all.includes(key.private_jwk[field]), `${name} ${field}`);
      }
    }
  }
  assert.ok(!all.includes("updated_at") && !all.includes("1800000200.25"));
});

Deno.test("keys stay in memory for 60 seconds at most, and the config for 5 seconds", async () => {
  const { get, fake, clock } = setup();
  const keyCalls = () => fake.calls.filter((c) => c.endsWith(`/actors/${ALICE}/keys`)).length;
  const configCalls = () => fake.calls.filter((c) => c.endsWith("/config")).length;
  await get(`/ap/actors/${ALICE}`, AS2);
  await get(`/ap/actors/${ALICE}`, AS2);
  assert.equal(keyCalls(), 1);
  assert.equal(configCalls(), 1);
  clock.now += CONFIG_CACHE_SECONDS;
  await get(`/ap/actors/${ALICE}`, AS2);
  assert.equal(configCalls(), 2);
  assert.equal(keyCalls(), 1);
  clock.now += KEY_CACHE_SECONDS;
  await get(`/ap/actors/${ALICE}`, AS2);
  assert.equal(keyCalls(), 2);
});

Deno.test("an internal API failure is 503, not 404 and not a cached answer", async () => {
  const { get, fake } = setup();
  fake.failStatus = 500;
  const response = await get(`/ap/actors/${ALICE}`, AS2);
  assert.equal(response.status, 503);
});

Deno.test("the public self-check accepts the answer of this sidecar process only, also while federation is off", async () => {
  for (const state of [{}, { federation: "off", federated_actors: 0 }]) {
    const { sidecar, fake } = setup();
    fake.config = state;
    const config = await sidecar.config();
    const requested: string[] = [];
    const throughProxy = ((input: string | URL | Request) => {
      requested.push(String(input));
      return sidecar.handle(new Request(String(input), { headers: { accept: JRD } }));
    }) as typeof fetch;
    assert.equal(await publicSelfCheck(config, sidecar.marker, throughProxy), true, JSON.stringify(state));
    assert.deepEqual(requested, [`${ORIGIN}/.well-known/webfinger?resource=acct%3Aclaire%40claire.example.com`]);
    // A wrong proxy rule sends the request to Claire, which answers 404 without the marker.
    assert.equal(await publicSelfCheck(config, sidecar.marker, (() => Promise.resolve(new Response("Not Found", { status: 404 }))) as typeof fetch), false);
    // A different sidecar process has a different marker.
    const other = setup().sidecar;
    assert.equal(await publicSelfCheck(config, sidecar.marker, ((input: string | URL | Request) => other.handle(new Request(String(input)))) as typeof fetch), false);
    assert.equal(await publicSelfCheck(config, sidecar.marker, (() => Promise.reject(new Error("network"))) as typeof fetch), false);
  }
});

Deno.test("each answer has the marker header, and it shows no server data", async () => {
  const { sidecar, fake } = setup();
  fake.config = { mode: "single", federation: "off", federated_actors: 0 };
  for (const path of ["/", "/ap/actors/server", "/.well-known/webfinger?resource=acct:alice@claire.example.com"]) {
    const response = await sidecar.handle(new Request(`${LISTEN}${path}`));
    assert.equal(response.status, 404);
    assert.equal(response.headers.get(SIDECAR_HEADER), sidecar.marker);
  }
  assert.match(sidecar.marker, /^[0-9a-f-]{36}$/);
});

Deno.test("a method that is not GET or HEAD gets 405 on each federation path that is not an inbox", async () => {
  const { sidecar, fake } = setup();
  for (const [method, path] of [["POST", `/ap/actors/${ALICE}`], ["PUT", `/ap/actors/${ALICE}`], ["DELETE", "/ap/actors/server"], ["POST", `/ap/actors/${ALICE}/followers`], ["POST", `/ap/actors/${ALICE}/outbox`], ["POST", "/.well-known/webfinger"]]) {
    const response = await sidecar.handle(new Request(`${LISTEN}${path}`, { method, headers: { accept: AS2, "content-type": AS2 }, body: method === "DELETE" ? undefined : "{}" }));
    assert.equal(response.status, 405, `${method} ${path}`);
    assert.equal(response.headers.get("allow"), "GET, HEAD");
  }
  assert.deepEqual(fake.calls, []);
  const head = await sidecar.handle(new Request(`${LISTEN}/ap/actors/${ALICE}`, { method: "HEAD", headers: { accept: AS2 } }));
  assert.equal(head.status, 200);
});

Deno.test("the source permits private network addresses only through the development switch of the config (FS1)", () => {
  // The option of Fedify is in one place only, and its value is the config field of Claire (contract 1.8), never a constant. Claire gives true only in the development environment with CLAIRE_ALLOW_PRIVATE_NETWORKS on.
  const option = "allow" + "PrivateAddress";
  const found: string[] = [];
  for (const entry of Deno.readDirSync(new URL("../src/", import.meta.url))) {
    const text = Deno.readTextFileSync(new URL(`../src/${entry.name}`, import.meta.url));
    assert.ok(!new RegExp(`${option}\\s*:\\s*true`).test(text), entry.name);
    if (text.includes(option)) found.push(entry.name);
  }
  assert.deepEqual(found, ["sidecar.ts"]);
  const sidecar = Deno.readTextFileSync(new URL("../src/sidecar.ts", import.meta.url));
  assert.ok(sidecar.includes(`this.#build(config.canonical_origin, config.allow_private_addresses)`));
  assert.ok(sidecar.includes(`createFederation<void>({ kv: this.#kv, queue: this.#queue, origin, ${option} })`));
});

Deno.test("knowledgeSelfCheck passes through sidecar while federation is off and rejects nonce replay", async () => {
  const fake = fakeClaire();
  fake.config = { federation: "off" };
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const config = await sidecar.config();
  let capturedSigned: Request | null = null;

  const result = await sidecar.knowledgeSelfCheck(config, async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    if (new URL(req.url).pathname === SELF_CHECK_PATH && req.method === "POST") {
      capturedSigned = req.clone();
    }
    return await sidecar.handle(req);
  });

  assert.equal(result.checked_at, fake.now);
  assert.deepEqual(result.failed_paths, []);
  assert.equal(result.signed.result, "ok");
  assert.ok(capturedSigned !== null);

  // Replay of the same signed self-check request gets 404.
  const replay = await sidecar.handle(capturedSigned);
  assert.equal(replay.status, 404);

  // GET and unknown nonces also get 404.
  assert.equal((await sidecar.handle(new Request(`${config.canonical_origin}${SELF_CHECK_PATH}`, { method: "GET" }))).status, 404);
  assert.equal((await sidecar.handle(new Request(`${config.canonical_origin}${SELF_CHECK_PATH}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ nonce: "unknown" }) }))).status, 404);
});

Deno.test("knowledgeSelfCheck detects missing routes, host changes, signature changes, TLS errors, and DNS errors", async () => {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const config = await sidecar.config();

  // Reverse proxy drops /ap/themes and /ap/self-check.
  const missingProxy = await sidecar.knowledgeSelfCheck(config, async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    const path = new URL(req.url).pathname;
    if (path === "/ap/themes" || path === SELF_CHECK_PATH) return new Response("not here", { status: 404 });
    return await sidecar.handle(req);
  });
  assert.deepEqual(missingProxy.failed_paths, ["/ap/themes"]);
  assert.equal(missingProxy.signed.result, "not_routed");

  // Reverse proxy alters Host header.
  const hostChanged = await sidecar.knowledgeSelfCheck(config, async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    if (new URL(req.url).pathname === SELF_CHECK_PATH) {
      const headers = new Headers(req.headers);
      headers.set("host", "127.0.0.1:8000");
      return await sidecar.handle(new Request(req.url, { method: req.method, headers, body: await req.arrayBuffer() }));
    }
    return await sidecar.handle(req);
  });
  assert.deepEqual(hostChanged.failed_paths, []);
  assert.equal(hostChanged.signed.result, "host_changed");

  // Reverse proxy alters the signed body.
  const bodyChanged = await sidecar.knowledgeSelfCheck(config, async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    if (new URL(req.url).pathname === SELF_CHECK_PATH) {
      const raw = JSON.parse(await req.text()) as { nonce: string };
      return await sidecar.handle(new Request(req.url, { method: "POST", headers: req.headers, body: JSON.stringify({ nonce: raw.nonce, extra: true }) }));
    }
    return await sidecar.handle(req);
  });
  assert.equal(bodyChanged.signed.result, "signature_invalid");

  // TLS and DNS fetch failures map to signed codes.
  const tlsFail = await sidecar.knowledgeSelfCheck(config, () => Promise.reject(new Error("certificate verify failed")));
  assert.deepEqual(tlsFail.failed_paths, [...KNOWLEDGE_SELF_CHECK_PATHS]);
  assert.equal(tlsFail.signed.result, "tls_error");

  const dnsFail = await sidecar.knowledgeSelfCheck(config, () => Promise.reject(new Error("getaddrinfo ENOTFOUND")));
  assert.equal(dnsFail.signed.result, "dns_error");

  // Expired nonce cannot be reused after SELF_CHECK_TTL_SECONDS.
  let savedExpired: Request | null = null;
  await sidecar.knowledgeSelfCheck(config, (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    if (new URL(req.url).pathname === SELF_CHECK_PATH) savedExpired = req.clone();
    return Promise.resolve(new Response(null, { status: 503 }));
  });
  fake.now += SELF_CHECK_TTL_SECONDS + 1;
  assert.ok(savedExpired !== null);
  assert.equal((await sidecar.handle(savedExpired)).status, 404);
});

Deno.test("runKnowledgeReads and runKnowledgeExchange update heartbeat loop state and clear errors on recovery", async () => {
  const fake = fakeClaire();
  fake.config = { federation: "off" };
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });

  await sidecar.runKnowledgeReads();
  await sidecar.runKnowledgeExchange();
  assert.deepEqual(sidecar.loops(), {
    knowledge_reads: { running: false, progress_at: fake.now, error: null, error_at: null },
    knowledge_exchange: { running: false, progress_at: fake.now, error: null, error_at: null },
  });

  // Failing API call records error and leaves progress_at unchanged.
  const lastProgress = fake.now;
  fake.now += 30;
  fake.failStatus = 500;
  await assert.rejects(() => sidecar.runKnowledgeReads());
  const failedState = sidecar.loops();
  assert.equal(failedState.knowledge_reads.running, false);
  assert.equal(failedState.knowledge_reads.progress_at, lastProgress);
  assert.ok(typeof failedState.knowledge_reads.error === "string" && failedState.knowledge_reads.error.length > 0);
  assert.equal(failedState.knowledge_reads.error_at, fake.now);

  // Recovery clears error and updates progress_at.
  fake.failStatus = null;
  await api.config();
  fake.now += 15;
  await sidecar.runKnowledgeReads();
  assert.deepEqual(sidecar.loops().knowledge_reads, {
    running: false,
    progress_at: fake.now,
    error: null,
    error_at: null,
  });
});

Deno.test("webfinger resolves handles case-insensitively and preserves stored preferredUsername display case", async () => {
  const { fake, get } = setup();
  const baseActor = fixture("actor").response;
  const aliceRecord = { ...baseActor, username: "Alice", profile_url: `${ORIGIN}/@Alice/` };
  fake.overrides.set("GET /internal/federation/v1/actors/by-username/AlIcE", {
    status: 200,
    response: aliceRecord,
  });
  fake.overrides.set(`GET /internal/federation/v1/actors/${ALICE}`, {
    status: 200,
    response: aliceRecord,
  });

  const mixed = await get(webfinger("AlIcE@claire.example.com"), JRD);
  assert.equal(mixed.status, 200);
  assert.equal(mixed.json?.subject, "acct:AlIcE@claire.example.com");
  assert.equal(link(mixed.json, "self")?.href, ALICE_URL);
  assert.equal(link(mixed.json, "http://webfinger.net/rel/profile-page")?.href, `${ORIGIN}/@Alice/`);

  const byActor = await get(`/.well-known/webfinger?resource=${encodeURIComponent(ALICE_URL)}`, JRD);
  assert.equal(byActor.status, 200);
  assert.ok(((byActor.json?.aliases ?? []) as string[]).includes("acct:Alice@claire.example.com"));

  const serverMixed = await get(webfinger("CLAIRE@claire.example.com"), JRD);
  assert.equal(serverMixed.status, 200);
  assert.equal(link(serverMixed.json, "self")?.href, SERVER_URL);
});

Deno.test("update_actor after username rename keeps immutable actor id and delivers new preferredUsername and url", async () => {
  const { fake, sidecar } = setup();
  const baseActor = fixture("actor").response as Record<string, unknown>;
  const renamedActor = {
    ...baseActor,
    username: "AliceDev",
    updated_at: 1800000500.0,
    profile_url: `${ORIGIN}/@AliceDev/`,
  };
  const follower = (fixture("followers").response.items as unknown[])[1];
  const deliveries: Record<string, unknown>[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const req = new Request(input, init);
    deliveries.push(JSON.parse(await req.text()));
    return new Response(null, { status: 202 });
  };
  try {
    fake.overrides.set(`GET /internal/federation/v1/actors/${ALICE}`, { status: 200, response: renamedActor });
    fake.overrides.set(`GET /internal/federation/v1/actors/${ALICE}/followers?limit=80`, {
      status: 200,
      response: { public_id: ALICE, total: 1, items: [follower], next_max_id: null },
    });
    fake.overrides.set("GET /internal/federation/v1/outbox-tasks?limit=10", {
      status: 200,
      response: {
        tasks: [{ id: 88, kind: "update_actor", actor_public_id: ALICE, object_public_id: null, created_at: 1800000500.0, lease_until: 1800000900.0 }],
      },
    });
    fake.overrides.set("POST /internal/federation/v1/outbox-tasks/88/ack", {
      status: 200,
      response: { id: 88, status: "handed_off" },
    });

    await sidecar.processOutboxTasks();
    assert.deepEqual(fake.acks, ["88 handed_off null"]);
    assert.equal(deliveries.length, 1);
    const activity = deliveries[0];
    assert.equal(activity.type, "Update");
    assert.equal(activity.actor, ALICE_URL);
    const person = activity.object as Record<string, unknown>;
    assert.equal(person.id, ALICE_URL);
    assert.equal(person.preferredUsername, "AliceDev");
    assert.equal(person.url, `${ORIGIN}/@AliceDev/`);
  } finally {
    globalThis.fetch = realFetch;
  }
});
