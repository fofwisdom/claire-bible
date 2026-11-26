// End-to-end exchange of renditions between two real Claire servers (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md phases E4, E5, and E9).
//
// tests/test_renditions_exchange.py starts two Claire processes, A (https://a.claire.test) and B (https://b.claire.test), prepares their databases, and runs this file one time for each phase. Each run makes the two sidecars of the two servers with the real code. The only part that is not real is the network: a request of a sidecar to the origin of the other server goes to the handle function of the other sidecar. A request to another host is an error of the test.
//
// Phases (E2E_PHASE):
// - read:   B reads the source index of A, its rendition objects, their bodies, and the account of the author, with requests that the server actor of B signs (the read queue of B).
// - plus:   B delivers the +1 of an account of B to the inbox of the author on A (the knowledge outbox of B); A records the mark and shows the count on the object.
// - opinions: A fetches the opinions of its object from B (the replies of accounts of B), with requests that its server actor signs.
// - fill:   alice of A follows carol of B; the sidecar of A reads the outbox of carol with the key of alice and gives her posts to A (phase E10a).
// - unplus: B purges the account that gave the +1; B sends the Undo, signed by its server actor, and A removes the mark (Section 5.12 rule 14).
// - delete: A delivers the Delete of each gone rendition object to the server actor of B, the reader of its body (the knowledge outbox of A).
//
// Environment: E2E_PHASE, CLAIRE_A_SOCKET, CLAIRE_A_KEY_FILE, CLAIRE_B_SOCKET, CLAIRE_B_KEY_FILE, E2E_RENDITION (the number of the rendition object "concise" on A, phase plus).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";

const AS2 = "application/activity+json";
const A_ORIGIN = "https://a.claire.test";

function sidecarFor(socketEnv: string, keyEnv: string, realFetch: typeof fetch): Sidecar {
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: Deno.env.get(socketEnv)! } });
  const api = new InternalApi({ key: Deno.readFileSync(Deno.env.get(keyEnv)!), transport: (request) => realFetch(request, { client }) });
  return new Sidecar({ api, kv: new MemoryKvStore() });
}

// Runs one queue of a sidecar until it gives no more work: a read can make the next read (an index gives objects, an object gives a body and an account).
async function drain(work: () => Promise<number>, rounds = 20): Promise<number> {
  let total = 0;
  for (let round = 0; round < rounds; round += 1) {
    const done = await work();
    total += done;
    if (done === 0) return total;
  }
  throw new Error("the queue did not end");
}

Deno.test(`renditions between two Claire servers: ${Deno.env.get("E2E_PHASE")}`, async () => {
  const phase = Deno.env.get("E2E_PHASE");
  const realFetch = globalThis.fetch;
  const a = sidecarFor("CLAIRE_A_SOCKET", "CLAIRE_A_KEY_FILE", realFetch);
  const b = sidecarFor("CLAIRE_B_SOCKET", "CLAIRE_B_KEY_FILE", realFetch);
  const traffic: string[] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    traffic.push(`${request.method} ${url.host}${url.pathname}`);
    if (url.host === "a.claire.test") return await a.handle(request);
    if (url.host === "b.claire.test") return await b.handle(request);
    throw new Error(`a request to a host outside the test: ${request.url}`);
  };
  try {
    assert.equal((await a.config()).canonical_origin, A_ORIGIN);
    if (phase === "read") {
      const reads = await drain(() => b.processKnowledgeRemoteReads());
      // One index, two objects (the document object and the rendition object), two bodies, one account.
      assert.equal(reads, 6, traffic.join("\n"));
      assert.deepEqual(b.knowledgeDeliveryCounters.reads_failed, 0);
      // Each read of B to A was signed; an unsigned read of the same body gets 404 from A.
      assert.ok(traffic.some((line) => line.startsWith("GET a.claire.test/ap/sources/")));
      const body = traffic.find((line) => line.startsWith("GET a.claire.test/ap/objects/") && line.endsWith("/body"))!;
      const unsigned = await a.handle(new Request(`https://${body.slice(4)}`, { headers: { accept: AS2 } }));
      assert.equal(unsigned.status, 404);
    } else if (phase === "plus") {
      const delivered = await drain(() => b.processKnowledgeOutbox());
      assert.equal(delivered, 1, traffic.join("\n"));
      assert.equal(b.knowledgeDeliveryCounters.delivered, 1);
      assert.ok(traffic.some((line) => line.startsWith("POST a.claire.test/") && line.includes("inbox")), traffic.join("\n"));
      // The object of A shows the mark of B.
      const rendition = Deno.env.get("E2E_RENDITION")!;
      const object = await (await a.handle(new Request(`${A_ORIGIN}/ap/objects/${rendition}`, { headers: { accept: AS2 } }))).json();
      assert.deepEqual([object["claire:plusOnes"].totalItems, object["claire:plusOnes"]["claire:servers"], object["claire:plusOnes"]["claire:score"]], [1, 1, 1]);
    } else if (phase === "opinions") {
      // A fetches the opinions of its object: it reads the reply of carol from B and its replies collection, and the reply of dan in it.
      const tasks = await drain(() => a.processOpinionTasks());
      assert.equal(tasks, 1, traffic.join("\n"));
      assert.equal(a.outboxCounters.handed_off, 1, traffic.join("\n"));
      assert.ok(traffic.some((line) => line.startsWith("GET b.claire.test/ap/objects/") && line.endsWith("/replies")), traffic.join("\n"));
      assert.ok(traffic.every((line) => !line.startsWith("GET a.claire.test/ap/objects/")), traffic.join("\n"));
    } else if (phase === "fill") {
      // alice of A follows carol of B: A reads the first page of the outbox of carol with the key of alice (phase E10a).
      const tasks = await drain(() => a.processFillTasks());
      assert.equal(tasks, 1, traffic.join("\n"));
      assert.ok(traffic.some((line) => line.startsWith("GET b.claire.test/ap/actors/") && line.includes("outbox")), traffic.join("\n"));
      assert.ok(traffic.every((line) => !line.startsWith("GET a.claire.test/")), traffic.join("\n"));
    } else if (phase === "unplus") {
      // B purged carol: the knowledge outbox of B sends the Undo of her +1, signed by the server actor of B (Section 5.12 rule 14); A removes the mark.
      const delivered = await drain(() => b.processKnowledgeOutbox());
      assert.equal(delivered, 1, traffic.join("\n"));
      const rendition = Deno.env.get("E2E_RENDITION")!;
      const object = await (await a.handle(new Request(`${A_ORIGIN}/ap/objects/${rendition}`, { headers: { accept: AS2 } }))).json();
      assert.deepEqual([object["claire:plusOnes"].totalItems, object["claire:plusOnes"]["claire:score"]], [0, 0]);
    } else if (phase === "delete") {
      const delivered = await drain(() => a.processKnowledgeOutbox());
      // One Delete for each gone object that B read: the document object and the rendition object.
      assert.equal(delivered, 2, traffic.join("\n"));
      assert.equal(a.knowledgeDeliveryCounters.delivered, 2);
      assert.ok(traffic.every((line) => !line.startsWith("POST a.claire.test")), traffic.join("\n"));
    } else {
      throw new Error(`unknown phase ${phase}`);
    }
  } finally {
    globalThis.fetch = realFetch;
  }
});
