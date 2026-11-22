// Public federation surface against the contract fixtures: WebFinger, NodeInfo, actor documents, and the server actor (Fedify dispatcher wiring).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { ACTIVITY_ACCEPT, CONFIG_CACHE_SECONDS, KEY_CACHE_SECONDS, KNOWLEDGE_SELF_CHECK_PATHS, SELF_CHECK_PATH, SELF_CHECK_TTL_SECONDS, Sidecar, SIDECAR_HEADER } from "../src/sidecar.ts";
import { CLAIRE_HEADER, SELF_CHECK_HEADER } from "../src/probes.ts";
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
  assert.equal(doc.discoverable, true);
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
  for (const path of [webfinger("suspended_carol@claire.example.com"), webfinger(`${ALICE}@claire.example.com`), webfinger("alice@other.example")]) {
    const response = await get(path, JRD);
    assert.equal(response.status, 404, path);
    assert.equal(response.text, reference.text, path);
  }
  const actorReference = await get("/ap/actors/999", AS2);
  assert.equal(actorReference.status, 404);
  for (const path of ["/ap/actors/316412345678901234", "/ap/actors/alice", "/ap/actors/0116412345678901234"]) {
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
  assert.equal((info.usage as { users: { total: number } }).users.total, 3);
});

Deno.test("the server actor and NodeInfo work in single mode", async () => {
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

// A fetch function that sends each request to `handler`, as the public origin does.
function through(handler: (request: Request) => Response | Promise<Response>): typeof fetch {
  return (async (input: string | URL | Request, init?: RequestInit) => await handler(input instanceof Request ? input : new Request(input, init))) as typeof fetch;
}

Deno.test("the public self-check accepts the answer of this sidecar process only, in each mode, and tells who answered", async () => {
  for (const state of [{}, { mode: "single" }]) {
    const { sidecar, fake } = setup();
    fake.config = state;
    const config = await sidecar.config();
    const requested: Request[] = [];
    const passed = await sidecar.publicSelfCheck(config, through((request) => {
      requested.push(request.clone());
      return sidecar.handle(request);
    }));
    assert.deepEqual({ ok: passed.ok, answer: passed.answer }, { ok: true, answer: null }, JSON.stringify(state));
    assert.equal(requested.length, 1);
    assert.equal(requested[0].url, `${ORIGIN}/.well-known/webfinger?resource=acct%3Aclaire%40claire.example.com`);
    // The request has the headers of a request of another server: an agent of Fedify and the JRD media type.
    assert.match(requested[0].headers.get("user-agent") ?? "", /^Fedify\//);
    assert.equal(requested[0].headers.get("accept"), JRD);

    // A wrong proxy rule sends the request to Claire, which answers 404 with its own header.
    const claire = await sidecar.publicSelfCheck(config, through(() => new Response("Not Found", { status: 404, headers: { [CLAIRE_HEADER]: "api" } })));
    assert.deepEqual([claire.ok, claire.answer?.problem, claire.answer?.party, claire.answer?.status], [false, "not_routed", "claire", 404]);
    // A different sidecar process has a different marker.
    const other = setup().sidecar;
    const otherProcess = await sidecar.publicSelfCheck(config, through((request) => other.handle(request)));
    assert.deepEqual([otherProcess.ok, otherProcess.answer?.party], [false, "sidecar_other"]);
    const network = await sidecar.publicSelfCheck(config, (() => Promise.reject(new Error("connection refused"))) as typeof fetch);
    assert.deepEqual([network.ok, network.answer?.party, network.answer?.error], [false, "none", "connect"]);
  }
});

Deno.test("a self-check tells a CDN challenge, a CDN block, a CDN error, and an intermediary", async () => {
  const { sidecar } = setup();
  const config = await sidecar.config();
  const cases: Array<[Response, string, Record<string, unknown>]> = [
    [new Response("<html>", { status: 403, headers: { "cf-mitigated": "challenge", "cf-ray": "1-ICN", "content-type": "text/html; charset=UTF-8", server: "cloudflare" } }), "cdn_challenge", { via_cdn: "cloudflare", media_type: "text/html", mitigation: "challenge", server: "cloudflare" }],
    [new Response("", { status: 403, headers: { "cf-mitigated": "block", "cf-ray": "1-ICN" } }), "cdn_block", { mitigation: "block" }],
    [new Response("", { status: 522, headers: { "cf-ray": "1-ICN" } }), "cdn_error", { status: 522 }],
    [new Response("blocked", { status: 403, headers: { server: "waf name with a very long text that is longer than the limit of sixty-four characters" } }), "intermediary", { via_cdn: null, server: "waf name with a very long text that is longer than the limit of " }],
  ];
  for (const [response, party, fields] of cases) {
    const result = await sidecar.publicSelfCheck(config, through(() => response));
    assert.equal(result.answer?.party, party);
    assert.equal(result.answer?.problem, "not_routed");
    for (const [key, value] of Object.entries(fields)) assert.equal((result.answer as unknown as Record<string, unknown>)[key], value, `${party} ${key}`);
  }
});

Deno.test("a self-check tells a changed path, a changed Host, and an answer that this process did not make", async () => {
  const { sidecar } = setup();
  const config = await sidecar.config();
  // The proxy removes the prefix /.well-known: the request still gets to this process, on another path.
  const stripped = await sidecar.publicSelfCheck(config, through((request) => sidecar.handle(new Request(request.url.replace("/.well-known/webfinger", "/webfinger"), request))));
  assert.deepEqual([stripped.answer?.problem, stripped.answer?.received], ["path_changed", "/webfinger?resource=acct%3Aclaire%40claire.example.com"]);
  // The proxy sets the Host of the upstream.
  const host = await sidecar.publicSelfCheck(config, through((request) => {
    const headers = new Headers(request.headers);
    headers.set("host", "10.0.0.5:8790");
    return sidecar.handle(new Request(request.url, { headers }));
  }));
  assert.deepEqual([host.answer?.problem, host.answer?.received], ["host_changed", "10.0.0.5:8790"]);
  // A cache gives an old answer with the marker of this process, and the request does not come to this process.
  const cached = await sidecar.handle(new Request(`${ORIGIN}/.well-known/webfinger?resource=acct%3Aclaire%40claire.example.com`));
  const stale = await sidecar.publicSelfCheck(config, through(() => new Response("Not Found", { status: 404, headers: cached.headers })));
  assert.deepEqual([stale.ok, stale.answer?.problem, stale.answer?.party], [false, "answer_not_live", "sidecar"]);
  // An intermediary that removes the self-check header gives the same result.
  const noHeader = await sidecar.publicSelfCheck(config, through((request) => {
    const headers = new Headers(request.headers);
    headers.delete(SELF_CHECK_HEADER);
    return sidecar.handle(new Request(request.url, { headers }));
  }));
  assert.equal(noHeader.answer?.problem, "answer_not_live");
});

Deno.test("each answer has the marker header, and it shows no server data", async () => {
  const { sidecar, fake } = setup();
  // A path of Claire, an unknown federation path, and an actor path: each answer has the marker.
  for (const [path, status] of [["/", 404], ["/ap/unknown", 404], ["/ap/actors/server", 200]] as const) {
    const response = await sidecar.handle(new Request(`${LISTEN}${path}`, { headers: { accept: AS2 } }));
    assert.equal(response.status, status, path);
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
  assert.ok(sidecar.includes(`createFederation<void>({ kv: this.#kv, queue: this.#queue, origin, ${option}, meterProvider, onOutboxError:`));
});

Deno.test("knowledgeSelfCheck passes through sidecar, sends the two signature forms of a delivery, and rejects nonce replay", async () => {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const config = await sidecar.config();
  const signed: Request[] = [];
  const reads: Request[] = [];

  const result = await sidecar.knowledgeSelfCheck(config, through((request) => {
    if (new URL(request.url).pathname === SELF_CHECK_PATH && request.method === "POST") signed.push(request.clone());
    else reads.push(request.clone());
    return sidecar.handle(request);
  }));

  assert.equal(result.checked_at, fake.now);
  assert.deepEqual(result.failed_paths, []);
  assert.deepEqual(result.answers, []);
  assert.deepEqual(result.signed, { draft_cavage: { result: "ok", answer: null }, rfc9421: { result: "ok", answer: null } });

  // The signed requests have the media type of a delivery. One has the draft form (Signature only) and one has the RFC 9421 form (Signature-Input).
  assert.equal(signed.length, 2);
  for (const request of signed) {
    assert.equal(request.headers.get("content-type"), "application/activity+json");
    assert.match(request.headers.get("user-agent") ?? "", /^Fedify\//);
  }
  assert.deepEqual(signed.map((request) => request.headers.has("signature-input")).sort(), [false, true]);
  // The reads have the Accept header of a read of another server.
  assert.equal(reads.length, KNOWLEDGE_SELF_CHECK_PATHS.length);
  assert.equal(reads.find((request) => request.url.endsWith("/ap/kb"))?.headers.get("accept"), ACTIVITY_ACCEPT);
  assert.equal(reads.find((request) => request.url.endsWith("/nodeinfo/2.1"))?.headers.get("accept"), "application/json");

  // Replay of the same signed self-check request gets 404.
  const replay = await sidecar.handle(signed[0]);
  assert.equal(replay.status, 404);

  // GET and unknown nonces also get 404.
  assert.equal((await sidecar.handle(new Request(`${config.canonical_origin}${SELF_CHECK_PATH}`, { method: "GET" }))).status, 404);
  assert.equal((await sidecar.handle(new Request(`${config.canonical_origin}${SELF_CHECK_PATH}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ nonce: "unknown" }) }))).status, 404);
});

Deno.test("knowledgeSelfCheck detects missing routes, a removed path prefix, host changes, signature changes, a block of one media type, TLS errors, and DNS errors", async () => {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const config = await sidecar.config();

  // The reverse proxy sends /ap/themes and /ap/self-check to Claire.
  const missingProxy = await sidecar.knowledgeSelfCheck(config, through((request) => {
    const path = new URL(request.url).pathname;
    if (path === "/ap/themes" || path === SELF_CHECK_PATH) return new Response("not here", { status: 404, headers: { [CLAIRE_HEADER]: "api" } });
    return sidecar.handle(request);
  }));
  assert.deepEqual(missingProxy.failed_paths, ["/ap/themes"]);
  assert.deepEqual(missingProxy.answers.map((answer) => [answer.path, answer.problem, answer.party]), [["/ap/themes", "not_routed", "claire"]]);
  assert.deepEqual([missingProxy.signed.draft_cavage.result, missingProxy.signed.draft_cavage.answer?.party, missingProxy.signed.rfc9421.answer?.problem], ["failed", "claire", "not_routed"]);

  // The reverse proxy removes the prefix /ap. Each request gets to this process, so the marker alone shows no fault.
  const stripped = await sidecar.knowledgeSelfCheck(config, through((request) => sidecar.handle(new Request(request.url.replace("/ap/", "/"), request))));
  assert.deepEqual(stripped.failed_paths, KNOWLEDGE_SELF_CHECK_PATHS.filter((path) => path.startsWith("/ap/")));
  assert.deepEqual([stripped.answers[0].problem, stripped.answers[0].received], ["path_changed", "/actors/server"]);
  assert.equal(stripped.signed.draft_cavage.answer?.problem, "path_changed");

  // The reverse proxy alters the Host header.
  const hostChanged = await sidecar.knowledgeSelfCheck(config, through(async (request) => {
    if (new URL(request.url).pathname !== SELF_CHECK_PATH) return await sidecar.handle(request);
    const headers = new Headers(request.headers);
    headers.set("host", "127.0.0.1:8000");
    return await sidecar.handle(new Request(request.url, { method: request.method, headers, body: await request.arrayBuffer() }));
  }));
  assert.deepEqual(hostChanged.failed_paths, []);
  assert.deepEqual([hostChanged.signed.draft_cavage.answer?.problem, hostChanged.signed.rfc9421.answer?.problem], ["host_changed", "host_changed"]);

  // The reverse proxy alters the signed body.
  const bodyChanged = await sidecar.knowledgeSelfCheck(config, through(async (request) => {
    if (new URL(request.url).pathname !== SELF_CHECK_PATH) return await sidecar.handle(request);
    const raw = JSON.parse(await request.text()) as Record<string, unknown>;
    return await sidecar.handle(new Request(request.url, { method: "POST", headers: request.headers, body: JSON.stringify({ ...raw, extra: true }) }));
  }));
  assert.deepEqual([bodyChanged.signed.draft_cavage.answer?.problem, bodyChanged.signed.rfc9421.answer?.problem], ["signature_invalid", "signature_invalid"]);

  // An intermediary removes the Signature-Input header: only the RFC 9421 form fails.
  const oneForm = await sidecar.knowledgeSelfCheck(config, through(async (request) => {
    if (!request.headers.has("signature-input")) return await sidecar.handle(request);
    const headers = new Headers(request.headers);
    headers.delete("signature-input");
    return await sidecar.handle(new Request(request.url, { method: request.method, headers, body: await request.arrayBuffer() }));
  }));
  assert.deepEqual([oneForm.signed.draft_cavage.result, oneForm.signed.rfc9421.result, oneForm.signed.rfc9421.answer?.problem], ["ok", "failed", "signature_invalid"]);

  // A WAF refuses the media type of a delivery.
  const waf = await sidecar.knowledgeSelfCheck(config, through((request) => (request.headers.get("content-type") === "application/activity+json" ? new Response("blocked", { status: 403, headers: { server: "waf" } }) : sidecar.handle(request))));
  assert.deepEqual(waf.failed_paths, []);
  assert.deepEqual([waf.signed.draft_cavage.answer?.party, waf.signed.draft_cavage.answer?.status, waf.signed.draft_cavage.answer?.server], ["intermediary", 403, "waf"]);

  // TLS and DNS failures have their class.
  const tlsFail = await sidecar.knowledgeSelfCheck(config, () => Promise.reject(new Error("certificate verify failed")));
  assert.deepEqual(tlsFail.failed_paths, [...KNOWLEDGE_SELF_CHECK_PATHS]);
  assert.equal(tlsFail.answers.length, KNOWLEDGE_SELF_CHECK_PATHS.length);
  assert.deepEqual([tlsFail.signed.draft_cavage.answer?.party, tlsFail.signed.draft_cavage.answer?.error], ["none", "tls"]);

  const dnsFail = await sidecar.knowledgeSelfCheck(config, () => Promise.reject(new Error("getaddrinfo ENOTFOUND")));
  assert.equal(dnsFail.signed.rfc9421.answer?.error, "dns");

  // Expired nonce cannot be reused after SELF_CHECK_TTL_SECONDS.
  let savedExpired: Request | null = null;
  await sidecar.knowledgeSelfCheck(config, through((request) => {
    if (new URL(request.url).pathname === SELF_CHECK_PATH) savedExpired = request.clone();
    return new Response(null, { status: 503 });
  }));
  fake.now += SELF_CHECK_TTL_SECONDS + 1;
  assert.ok(savedExpired !== null);
  assert.equal((await sidecar.handle(savedExpired)).status, 404);
});

Deno.test("knowledgeSelfCheck passes RFC 9421 when TLS terminates before the HTTP edge without X-Forwarded-Proto", async () => {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const config = await sidecar.config();
  assert.ok(config.canonical_origin.startsWith("https://"));

  const ok = await sidecar.knowledgeSelfCheck(config, through(async (request) => {
    const headers = new Headers(request.headers);
    const body = request.method === "POST" ? await request.arrayBuffer() : undefined;
    return await sidecar.handle(new Request(request.url.replace(/^https:/, "http:"), { method: request.method, headers, body }));
  }));
  assert.deepEqual(ok.failed_paths, []);
  assert.deepEqual(ok.signed, { draft_cavage: { result: "ok", answer: null }, rfc9421: { result: "ok", answer: null } });

  const badHost = await sidecar.knowledgeSelfCheck(config, through(async (request) => {
    const headers = new Headers(request.headers);
    headers.set("host", "other.example.com");
    const body = request.method === "POST" ? await request.arrayBuffer() : undefined;
    return await sidecar.handle(new Request(request.url.replace(/^https:/, "http:"), { method: request.method, headers, body }));
  }));
  assert.equal(badHost.signed.rfc9421.answer?.problem, "host_changed");
});

Deno.test("runKnowledgeReads and runKnowledgeExchange update heartbeat loop state and clear errors on recovery", async () => {
  const fake = fakeClaire();
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
    discoverable: false,
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
    assert.equal(person.discoverable, false);
  } finally {
    globalThis.fetch = realFetch;
  }
});

Deno.test("actor summary renders escaped HTML paragraph on Person when non-empty", async () => {
  const { fake, get } = setup();
  const baseActor = fixture("actor").response as Record<string, unknown>;
  fake.overrides.set(`GET /internal/federation/v1/actors/${ALICE}`, {
    status: 200,
    response: { ...baseActor, summary: "Line 1 <script>\nLine 2" },
  });
  const actor = await get(`/ap/actors/${ALICE}`, AS2);
  assert.equal(actor.status, 200);
  assert.equal(actor.json?.summary, "<p>Line 1 &lt;script&gt;<br/>Line 2</p>");
});
