// End-to-end discovery (phase FP1 completion criterion): a remote-style client resolves a handle to an actor document and verifies a signed fetch.
// The remote side uses only public answers of the sidecar. All servers are in this process: no test calls a public server.

import assert from "node:assert/strict";
import { getDocumentLoader, MemoryKvStore, signRequest, verifyRequest } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const AS2 = "application/activity+json";

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const fetched: string[] = [];
  // The document loader of the remote server. A URL on the canonical origin goes to the sidecar, as through the reverse proxy.
  const standard = getDocumentLoader();
  const remoteLoader = async (url: string) => {
    if (!url.startsWith(`${ORIGIN}/`)) return await standard(url);
    fetched.push(url);
    const response = await sidecar.handle(new Request(url, { headers: { accept: AS2 } }));
    if (response.status !== 200) throw new Error(`remote fetch of ${url} failed: ${response.status}`);
    return { contextUrl: null, documentUrl: url, document: await response.json() };
  };
  return { fake, sidecar, remoteLoader, fetched };
}

Deno.test("a remote-style client resolves a handle and reads the actor document", async () => {
  const { sidecar } = setup();
  // Step 1: WebFinger for the handle.
  const finger = await sidecar.handle(new Request(`${ORIGIN}/.well-known/webfinger?resource=${encodeURIComponent("acct:alice@claire.example.com")}`, { headers: { accept: "application/jrd+json" } }));
  assert.equal(finger.status, 200);
  const self = ((await finger.json()).links as { rel: string; type?: string; href?: string }[]).find((l) => l.rel === "self" && l.type === AS2);
  assert.ok(self?.href);
  // Step 2: the actor document at the self link.
  const actor = await sidecar.handle(new Request(self.href, { headers: { accept: AS2 } }));
  assert.equal(actor.status, 200);
  assert.match(actor.headers.get("content-type") ?? "", /application\/activity\+json/);
  const doc = await actor.json();
  assert.equal(doc.id, self.href);
  assert.equal(doc.preferredUsername, "alice");
  assert.ok(String(doc.inbox).startsWith(`${ORIGIN}/`));
});

for (const identifier of ["instance", "116412345678901234"]) {
  Deno.test(`a remote server verifies a fetch that is signed with the key of actor ${identifier}`, async () => {
    const { sidecar, remoteLoader, fetched, fake } = setup();
    const ctx = await sidecar.createContext();
    const [rsa] = await ctx.getActorKeyPairs(identifier);
    assert.ok(rsa, "the actor has an RSA key pair");
    assert.equal(rsa.keyId.href, `${ORIGIN}/ap/actors/${identifier}#main-key`);

    // The sidecar signs a request to a remote server. The private key came from Claire through the internal API.
    const currentTime = Temporal.Instant.fromEpochMilliseconds(fake.now * 1000);
    const signed = await signRequest(new Request("https://remote.example/users/bob", { headers: { accept: AS2 } }), rsa.privateKey, rsa.keyId, { currentTime });

    // The remote server knows only the request. It gets the public key from the actor document that the sidecar serves.
    const key = await verifyRequest(signed, { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime });
    assert.ok(key, "the signature is valid");
    assert.equal(key.ownerId?.href, `${ORIGIN}/ap/actors/${identifier}`);
    assert.ok(fetched.some((url) => url.startsWith(`${ORIGIN}/ap/actors/${identifier}`)), "the key came from the sidecar");

    // A changed request does not verify.
    const changed = new Request("https://remote.example/users/carol", { headers: signed.headers });
    assert.equal(await verifyRequest(changed, { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime }), null);
  });
}

Deno.test("a signature of one actor is not valid for a different actor", async () => {
  const { sidecar, remoteLoader, fake } = setup();
  const ctx = await sidecar.createContext();
  const [alice] = await ctx.getActorKeyPairs("116412345678901234");
  const [instance] = await ctx.getActorKeyPairs("instance");
  const currentTime = Temporal.Instant.fromEpochMilliseconds(fake.now * 1000);
  // The request names the key of the instance actor but uses the private key of alice.
  const forged = await signRequest(new Request("https://remote.example/users/bob", { headers: { accept: AS2 } }), alice.privateKey, instance.keyId, { currentTime });
  assert.equal(await verifyRequest(forged, { documentLoader: remoteLoader, contextLoader: remoteLoader, currentTime }), null);
});
