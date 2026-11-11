// Public federation surface against the contract fixtures: WebFinger, NodeInfo, actor documents, and the instance actor (Fedify dispatcher wiring).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { CONFIG_CACHE_SECONDS, KEY_CACHE_SECONDS, publicSelfCheck, Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const INSTANCE_URL = `${ORIGIN}/ap/actors/instance`;
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
  // Collections of later phases and fields that the account does not have are not in the document.
  for (const field of ["outbox", "followers", "following", "summary", "icon"]) assert.ok(!(field in doc), field);
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

Deno.test("the instance actor is an Application with its own keys", async () => {
  const { get } = setup();
  const finger = await get(webfinger("claire@claire.example.com"), JRD);
  assert.equal(link(finger.json, "self")?.href, INSTANCE_URL);
  const actor = await get("/ap/actors/instance", AS2);
  assert.equal(actor.status, 200);
  assert.equal(actor.json?.type, "Application");
  assert.equal(actor.json?.id, INSTANCE_URL);
  assert.equal(actor.json?.preferredUsername, "claire");
  assert.equal(actor.json?.inbox, `${INSTANCE_URL}/inbox`);
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

Deno.test("all paths are 404 when federation is off or the mode is single", async () => {
  for (const state of [{ federation: "off" }, { mode: "single" }]) {
    const { get, fake, bodies } = setup();
    fake.config = { ...state, federated_actors: 0 };
    for (const [path, accept] of [[webfinger("alice@claire.example.com"), JRD], [webfinger("claire@claire.example.com"), JRD], [`/ap/actors/${ALICE}`, AS2], ["/ap/actors/instance", AS2], ["/.well-known/nodeinfo", "application/json"], ["/nodeinfo/2.1", "application/json"]]) {
      const response = await get(path, accept);
      assert.equal(response.status, 404, `${JSON.stringify(state)} ${path}`);
      assert.equal(response.text, "Not Found");
    }
    // Single mode must show no USERNAME (S9). The sidecar asked Claire for the config only.
    assert.ok(!bodies.join("").includes("alice"));
    assert.ok(fake.calls.every((call) => call.endsWith("/config")));
  }
});

Deno.test("paths of Claire and paths of later phases are 404 without a call to Claire", async () => {
  const { sidecar, fake } = setup();
  for (const path of ["/", "/api/me", "/@alice/", "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource", "/.well-known/", "/nodeinfo/2.0", "/ap", "/internal/federation/v1/config"]) {
    assert.equal((await sidecar.handle(new Request(`${LISTEN}${path}`))).status, 404, path);
  }
  assert.deepEqual(fake.calls, []);
  for (const [method, path] of [["POST", `/ap/actors/${ALICE}/inbox`], ["POST", "/ap/inbox"], ["GET", `/ap/actors/${ALICE}/followers`], ["GET", `/ap/actors/${ALICE}/outbox`]]) {
    const response = await sidecar.handle(new Request(`${LISTEN}${path}`, { method, headers: { accept: AS2, "content-type": AS2 }, body: method === "POST" ? "{}" : undefined }));
    assert.ok(response.status === 404 || response.status === 405, `${method} ${path} -> ${response.status}`);
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
  await get("/ap/actors/instance", AS2);
  await get("/nodeinfo/2.1", "application/json");
  const all = bodies.join("");
  for (const name of ["actor_keys", "instance_actor_keys"]) {
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

Deno.test("the public self-check accepts the answer of this sidecar only", async () => {
  const { sidecar } = setup();
  const config = await sidecar.config();
  const requested: string[] = [];
  const throughProxy = ((input: string | URL | Request) => {
    requested.push(String(input));
    return sidecar.handle(new Request(String(input), { headers: { accept: JRD } }));
  }) as typeof fetch;
  assert.equal(await publicSelfCheck(config, throughProxy), true);
  assert.deepEqual(requested, [`${ORIGIN}/.well-known/webfinger?resource=acct%3Aclaire%40claire.example.com`]);
  // A wrong proxy rule sends the request to Claire, which answers 404.
  assert.equal(await publicSelfCheck(config, (() => Promise.resolve(new Response("Not Found", { status: 404 }))) as typeof fetch), false);
  assert.equal(await publicSelfCheck(config, (() => Promise.resolve(Response.json({ links: [{ rel: "self", href: "https://other.example/ap/actors/instance" }] }))) as typeof fetch), false);
  assert.equal(await publicSelfCheck(config, (() => Promise.reject(new Error("network"))) as typeof fetch), false);
});

Deno.test("the source never permits private network addresses (FS1)", () => {
  const forbidden = "allow" + "PrivateAddress";
  for (const entry of Deno.readDirSync(new URL("../src/", import.meta.url))) {
    const text = Deno.readTextFileSync(new URL(`../src/${entry.name}`, import.meta.url));
    assert.ok(!text.includes(forbidden), entry.name);
  }
});
