// End-to-end follow (phase FP2 completion criterion): a remote server follows an actor of a real Claire process through the sidecar code.
//
// The Python test tests/test_fedify_sidecar.py starts Claire and runs this file. It is not in tests/, because it needs a Claire process.
// The second instance is in this process: a key pair, actor documents, and a capture of deliveries. Claire has no function that sends a Follow, and Fedify refuses the loopback address of a second development instance (design Section 13, FS1). Thus the remote server has the name remote.example and answers through a replacement of fetch.
//
// Environment: CLAIRE_FEDIFY_SOCKET, CLAIRE_FEDIFY_SERVICE_KEY_FILE, E2E_ACTOR (public ID of the actor to follow).

import assert from "node:assert/strict";
import { generateCryptoKeyPair, getDocumentLoader, MemoryKvStore, signRequest, verifyRequest } from "@fedify/fedify";
import { type Activity, CryptographicKey, Endpoints, Follow, Like, Person, Undo } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";

const AS2 = "application/activity+json";

Deno.test("a remote server follows and unfollows an actor of a real Claire", async () => {
  const socketPath = Deno.env.get("CLAIRE_FEDIFY_SOCKET")!;
  const actorId = Deno.env.get("E2E_ACTOR")!;
  const key = Deno.readFileSync(Deno.env.get("CLAIRE_FEDIFY_SERVICE_KEY_FILE")!);
  const realFetch = globalThis.fetch;
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: socketPath } });
  // The internal API uses the real fetch on the Unix socket, the same as src/main.ts.
  const api = new InternalApi({ key, transport: (request) => realFetch(request, { client }) });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore() });
  const origin = (await sidecar.config()).canonical_origin;
  const actorUrl = `${origin}/ap/actors/${actorId}`;

  const remoteKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");
  const remoteActor = async (iri: string) =>
    await new Person({
      id: new URL(iri),
      preferredUsername: iri.split("/").pop(),
      inbox: new URL(`${iri}/inbox`),
      endpoints: new Endpoints({ sharedInbox: new URL(`${new URL(iri).origin}/inbox`) }),
      publicKey: new CryptographicKey({ id: new URL(`${iri}#main-key`), owner: new URL(iri), publicKey: remoteKeys.publicKey }),
    }).toJsonLd();
  const deliveries: { url: string; request: Request; body: Record<string, unknown> }[] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    assert.ok(new URL(request.url).hostname.endsWith(".example"), `unexpected outbound request to ${request.url}`);
    if (request.method === "GET") return new Response(JSON.stringify(await remoteActor(request.url.split("#")[0])), { headers: { "content-type": AS2 } });
    deliveries.push({ url: request.url, request: request.clone(), body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  const post = async (path: string, activity: Activity, sign = true) => {
    const document = await activity.toJsonLd() as Record<string, unknown>;
    let request = new Request(`${origin}${path}`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify(document) });
    if (sign) request = await signRequest(request, remoteKeys.privateKey, new URL(`${document.actor}#main-key`));
    const response = await sidecar.handle(request);
    await response.body?.cancel();
    return response.status;
  };
  const total = async () => (await (await sidecar.handle(new Request(`${actorUrl}/followers`, { headers: { accept: AS2 } }))).json()).totalItems;
  const follow = (user: string, host = "remote.example") => new Follow({ id: new URL(`https://${host}/activities/follow-${user}`), actor: new URL(`https://${host}/users/${user}`), object: new URL(actorUrl) });

  try {
    assert.equal(await total(), 0);

    // An activity without a signature changes nothing.
    assert.equal(await post(`/ap/actors/${actorId}/inbox`, follow("bob"), false), 401);
    assert.equal(await total(), 0);

    // A signed Follow: Claire stores the follower and the sidecar sends an Accept with the key that Claire keeps.
    assert.equal(await post(`/ap/actors/${actorId}/inbox`, follow("bob")), 202);
    assert.equal(await total(), 1);
    assert.equal(deliveries.length, 1);
    const accept = deliveries[0];
    assert.equal(accept.url, "https://remote.example/users/bob/inbox");
    assert.equal(accept.body.type, "Accept");
    assert.equal(accept.body.actor, actorUrl);
    assert.equal((accept.body.object as Record<string, unknown>).id, "https://remote.example/activities/follow-bob");
    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${origin}/`)) return await standard(url);
      const answer = await sidecar.handle(new Request(url, { headers: { accept: AS2 } }));
      return { contextUrl: null, documentUrl: url, document: await answer.json() };
    };
    assert.equal((await verifyRequest(accept.request, { documentLoader: remoteLoader, contextLoader: remoteLoader }))?.ownerId?.href, actorUrl);

    // A second follower through the shared inbox, and a type outside the v1 set.
    assert.equal(await post("/ap/inbox", follow("dan")), 202);
    assert.equal(await total(), 2);
    assert.equal(await post(`/ap/actors/${actorId}/inbox`, new Like({ id: new URL("https://remote.example/activities/like-1"), actor: new URL("https://remote.example/users/bob"), object: new URL(actorUrl) })), 202);
    assert.equal(sidecar.counters.ignored, 1);

    // A domain that the admin blocked gets 403 and no reply.
    assert.equal(await post(`/ap/actors/${actorId}/inbox`, follow("eve", "sub.blocked.example")), 403);
    assert.equal(await total(), 2);

    // Undo(Follow) removes the follower.
    const bob = new URL("https://remote.example/users/bob");
    assert.equal(await post(`/ap/actors/${actorId}/inbox`, new Undo({ id: new URL("https://remote.example/activities/undo-bob"), actor: bob, object: follow("bob") })), 202);
    assert.equal(await total(), 1);

    assert.deepEqual(deliveries.map((d) => `${d.body.type} ${d.url}`), ["Accept https://remote.example/users/bob/inbox", "Accept https://remote.example/users/dan/inbox"]);
    assert.deepEqual(sidecar.counters, { accepted: 3, rejected: 0, ignored: 1, blocked: 1, dropped: 0 });
  } finally {
    globalThis.fetch = realFetch;
    client.close();
  }
});
