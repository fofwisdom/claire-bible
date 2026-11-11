// End-to-end lifecycle (phase FP3 completion criterion of unit FP3-4): a change of a theme to private on a real Claire process sends Delete for the published object, and a change of the display name sends Update(Person).
//
// The Python test tests/test_fedify_sidecar.py runs this file after e2e/publish_e2e.ts, on the same Claire process: it makes the theme private and changes the display name through the public API first.
//
// Environment: CLAIRE_FEDIFY_SOCKET, CLAIRE_FEDIFY_SERVICE_KEY_FILE, E2E_ACTOR (public ID of the actor), E2E_OBJECT (public ID of the object of the theme), E2E_NAME (the new display name).

import assert from "node:assert/strict";
import { getDocumentLoader, MemoryKvStore, verifyRequest } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";

const AS2 = "application/activity+json";

Deno.test("a theme change to private sends Delete, and a new display name sends Update(Person)", async () => {
  const socketPath = Deno.env.get("CLAIRE_FEDIFY_SOCKET")!;
  const actorId = Deno.env.get("E2E_ACTOR")!;
  const objectId = Deno.env.get("E2E_OBJECT")!;
  const key = Deno.readFileSync(Deno.env.get("CLAIRE_FEDIFY_SERVICE_KEY_FILE")!);
  const realFetch = globalThis.fetch;
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: socketPath } });
  const api = new InternalApi({ key, transport: (request) => realFetch(request, { client }) });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore() });
  const origin = (await sidecar.config()).canonical_origin;
  const actorUrl = `${origin}/ap/actors/${actorId}`;
  const objectUrl = `${origin}/ap/objects/${objectId}`;

  const deliveries: { url: string; request: Request; body: Record<string, unknown> }[] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    assert.equal(request.method, "POST", `unexpected outbound ${request.method} ${request.url}`);
    deliveries.push({ url: request.url, request: request.clone(), body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  const get = (url: string) => sidecar.handle(new Request(url, { headers: { accept: AS2 } }));

  try {
    assert.equal(await sidecar.processOutboxTasks(), 2);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 2, rejected: 0, failed: 0 });
    assert.deepEqual(deliveries.map((d) => `${d.body.type} ${d.url}`), ["Delete https://remote.example/inbox", "Update https://remote.example/inbox"]);
    const [del, update] = deliveries.map((d) => d.body);
    assert.deepEqual([del.id, del.actor, del.object], [`${objectUrl}#delete`, actorUrl, { id: objectUrl, type: "Tombstone" }]);
    const person = update.object as Record<string, unknown>;
    assert.deepEqual([update.actor, person.id, person.type, person.name], [actorUrl, actorUrl, "Person", Deno.env.get("E2E_NAME")]);

    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${origin}/`)) return await standard(url);
      return { contextUrl: null, documentUrl: url, document: await (await get(url)).json() };
    };
    for (const delivery of deliveries) {
      assert.equal((await verifyRequest(delivery.request, { documentLoader: remoteLoader, contextLoader: remoteLoader }))?.ownerId?.href, actorUrl);
    }

    // The object is gone, and the outbox collection is empty.
    assert.equal((await get(objectUrl)).status, 410);
    assert.equal((await (await get(`${actorUrl}/outbox`)).json()).totalItems, 0);
    assert.equal((await (await get(actorUrl)).json()).name, Deno.env.get("E2E_NAME"));
  } finally {
    globalThis.fetch = realFetch;
    client.close();
  }
});
