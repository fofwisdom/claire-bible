// End-to-end publish (phase FP3 completion criterion of unit FP3-3): a document that was published on a real Claire process arrives at a test inbox with a valid signature.
//
// The Python test tests/test_fedify_sidecar.py starts Claire, publishes two documents through the public API, retracts one of them, and runs this file. It is not in tests/, because it needs a Claire process.
// The remote server is in this process: it is a capture of deliveries, with the name remote.example (design Section 13, FS1 forbids a loopback address).
//
// Environment: CLAIRE_FEDIFY_SOCKET, CLAIRE_FEDIFY_SERVICE_KEY_FILE, E2E_ACTOR (public ID of the actor), E2E_OBJECT (public ID of the published object), E2E_GONE (public ID of the retracted object).

import assert from "node:assert/strict";
import { getDocumentLoader, MemoryKvStore, verifyRequest } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";

const AS2 = "application/activity+json";

Deno.test("a published document of a real Claire arrives at a test inbox with a valid signature", async () => {
  const socketPath = Deno.env.get("CLAIRE_FEDIFY_SOCKET")!;
  const actorId = Deno.env.get("E2E_ACTOR")!;
  const objectId = Deno.env.get("E2E_OBJECT")!;
  const goneId = Deno.env.get("E2E_GONE")!;
  const key = Deno.readFileSync(Deno.env.get("CLAIRE_FEDIFY_SERVICE_KEY_FILE")!);
  const realFetch = globalThis.fetch;
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: socketPath } });
  // The internal API uses the real fetch on the Unix socket, the same as src/main.ts.
  const api = new InternalApi({ key, transport: (request) => realFetch(request, { client }) });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore() });
  const origin = (await sidecar.config()).canonical_origin;
  const actorUrl = `${origin}/ap/actors/${actorId}`;
  const objectUrl = `${origin}/ap/objects/${objectId}`;
  const goneUrl = `${origin}/ap/objects/${goneId}`;

  // The test inbox.
  const deliveries: { url: string; request: Request; body: Record<string, unknown> }[] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    assert.equal(request.method, "POST", `unexpected outbound ${request.method} ${request.url}`);
    deliveries.push({ url: request.url, request: request.clone(), body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  const get = (url: string) => sidecar.handle(new Request(url, { headers: { accept: AS2 } }));

  try {
    // Three tasks: Create of the first document, Create of the second document (retracted, thus rejected), and Delete of the second document.
    assert.equal(await sidecar.processOutboxTasks(), 3);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 2, rejected: 1, failed: 0 });

    // The follower on the blocked domain gets nothing. The other follower gets the two activities at its shared inbox.
    assert.deepEqual(deliveries.map((d) => `${d.body.type} ${d.url}`), ["Create https://remote.example/inbox", "Delete https://remote.example/inbox"]);
    const [create, del] = deliveries;
    assert.equal(create.body.id, `${objectUrl}#create`);
    assert.equal(create.body.actor, actorUrl);
    assert.equal(create.body.to, "https://www.w3.org/ns/activitystreams#Public");
    assert.equal(create.body.cc, `${actorUrl}/followers`);
    const article = create.body.object as Record<string, string>;
    assert.deepEqual([article.id, article.type, article.attributedTo, article.name], [objectUrl, "Article", actorUrl, "First Post"]);
    // RD21: the object has the summary (the title here: no extraction) and the share link, never the body.
    // The url is the permalink of the document (PERMALINK_UNIFICATION_DESIGN.md U11).
    assert.ok(article.url.startsWith(`${origin}/silvercord/`), article.url);
    assert.ok(article.content.startsWith("<p>First Post</p>") && article.content.includes(`href="${article.url}"`) && !article.content.includes("world") && !article.content.includes("<script"), article.content);
    assert.deepEqual(del.body.object, { id: goneUrl, type: "Tombstone" });
    assert.equal(del.body.id, `${goneUrl}#delete`);

    // The signature is valid for the key in the actor document, which is the key that Claire keeps.
    const standard = getDocumentLoader();
    const remoteLoader = async (url: string) => {
      if (!url.startsWith(`${origin}/`)) return await standard(url);
      return { contextUrl: null, documentUrl: url, document: await (await get(url)).json() };
    };
    for (const delivery of deliveries) {
      assert.equal((await verifyRequest(delivery.request, { documentLoader: remoteLoader, contextLoader: remoteLoader }))?.ownerId?.href, actorUrl);
    }

    // Claire has the acknowledgements: no task is ready.
    assert.equal(await sidecar.processOutboxTasks(), 0);
    assert.equal(deliveries.length, 2);

    // The object path: the Article that was delivered, and a Tombstone for the retracted object.
    const fetched = await get(objectUrl);
    assert.equal(fetched.status, 200);
    const document = await fetched.json();
    assert.deepEqual([document.id, document.type, document.name, document.content, document.url], [objectUrl, "Article", article.name, article.content, article.url]);
    // The collections of the object (phase E6, contract 1.19): the object names them, and the real Claire gives each document through the sidecar.
    assert.equal(document.replies, `${objectUrl}/replies`);
    assert.deepEqual([document.likes, document.shares], [{ id: `${objectUrl}/likes`, type: "Collection", totalItems: 0 }, { id: `${objectUrl}/shares`, type: "Collection", totalItems: 0 }]);
    const replies = await get(`${objectUrl}/replies`);
    assert.equal(replies.status, 200);
    assert.deepEqual(await replies.json(), { "@context": "https://www.w3.org/ns/activitystreams", id: `${objectUrl}/replies`, type: "Collection", totalItems: 0, first: { type: "CollectionPage", partOf: `${objectUrl}/replies`, items: [] } });
    assert.deepEqual(await (await get(`${objectUrl}/likes`)).json(), { "@context": "https://www.w3.org/ns/activitystreams", id: `${objectUrl}/likes`, type: "Collection", totalItems: 0 });
    assert.equal((await get(`${goneUrl}/replies`)).status, 404);
    // The renditions (phase E4b, contract 1.20): the object at its ID names the collection of its renditions, the collection has the document object first and the rendition object of "concise", and the rendition object names its document. A delivery has no claire: field.
    assert.equal(document["claire:renditions"], `${objectUrl}/renditions`);
    assert.ok(!("claire:renditions" in (create.body.object as Record<string, unknown>)));
    const renditions = await (await get(`${objectUrl}/renditions`)).json();
    assert.equal(renditions.type, "OrderedCollection");
    assert.equal(renditions.orderedItems.length, 2);
    assert.equal(renditions.orderedItems[0], objectUrl);
    const rendition = await (await get(renditions.orderedItems[1])).json();
    assert.deepEqual([rendition.type, rendition["claire:renditionOf"], rendition["claire:template"], rendition["claire:templateVersion"]], ["Article", objectUrl, "concise", "concise-v1"]);
    assert.ok(String(rendition.url).endsWith("/renditions/concise"));
    // The body needs the signature of the server actor of another Claire server.
    assert.equal((await get(`${renditions.orderedItems[1]}/body`)).status, 404);
    const gone = await get(goneUrl);
    assert.equal(gone.status, 410);
    assert.deepEqual([(await gone.json()).type], ["Tombstone"]);

    // The outbox collection has the public object only.
    const actor = await (await get(actorUrl)).json();
    assert.equal(actor.outbox, `${actorUrl}/outbox`);
    const outbox = await (await get(actor.outbox)).json();
    assert.equal(outbox.totalItems, 1);
    const page = await (await get(outbox.first)).json();
    assert.deepEqual(page.orderedItems.map((item: { type: string; object: string }) => `${item.type} ${item.object}`), [`Create ${objectUrl}`]);
  } finally {
    globalThis.fetch = realFetch;
    client.close();
  }
});
