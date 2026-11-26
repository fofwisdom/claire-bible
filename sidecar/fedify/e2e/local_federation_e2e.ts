// Federation between two accounts of one Claire server (owner decision RD30, FEDERATION_REMOTE_READING_DESIGN.md Section 6.7).
//
// tests/test_local_federation.py starts one Claire process (https://a.claire.test), makes the actions of its two accounts with the routes of the client, and runs this file after each step. The sidecar is the real code, with the loopback of main.ts: a request of Fedify to the origin of the server goes to the request handler of the same sidecar. A request to another host is an error of the test.
//
// Environment: CLAIRE_A_SOCKET, CLAIRE_A_KEY_FILE.

import assert from "node:assert/strict";
import { InProcessMessageQueue, MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { loopbackFetch } from "../src/loopback.ts";
import { Sidecar } from "../src/sidecar.ts";

Deno.test({ name: "one Claire server: the tasks of its accounts reach each other through the loopback", sanitizeOps: false, sanitizeResources: false }, async () => {
  const realFetch = globalThis.fetch;
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: Deno.env.get("CLAIRE_A_SOCKET")! } });
  const api = new InternalApi({ key: Deno.readFileSync(Deno.env.get("CLAIRE_A_KEY_FILE")!), transport: (request) => realFetch(request, { client }) });
  // A queue as in production (main.ts gives a SQLite queue): a delivery comes after the acknowledgement of its task, not inside it.
  const a = new Sidecar({ api, kv: new MemoryKvStore(), queue: new InProcessMessageQueue() });
  const traffic: string[] = [];
  const loopback = loopbackFetch(realFetch, async () => (await a.config()).canonical_origin, (request) => {
    const url = new URL(request.url);
    traffic.push(`${request.method} ${url.host}${url.pathname}`);
    return a.handle(request);
  });
  // Each request that does not go to the loopback is an error of the test: the server has no other server to reach.
  globalThis.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    if (url.host !== "a.claire.test") return Promise.reject(new Error(`a request to a host outside the test: ${url.href}`));
    return loopback(input, init);
  };
  try {
    // The queue delivers in the background: wait until no request comes for 300 ms.
    const settle = async () => {
      let seen = -1;
      for (let wait = 0; wait < 100 && seen !== traffic.length; wait += 1) {
        seen = traffic.length;
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    };
    // The tasks of one step can make more tasks (a Follow gives an Accept; an Accept gives a fill).
    for (let round = 0; round < 20; round += 1) {
      const before = traffic.length;
      const done = await a.processClientTasks() + await a.processInteractionTasks() + await a.processOutboxTasks() + await a.processFillTasks() + await a.processContextTasks() + await a.processOpinionTasks();
      await settle();
      if (done === 0 && traffic.length === before) break;
      assert.ok(round < 19, "the tasks did not end");
    }
    console.log(`TRAFFIC ${JSON.stringify(traffic)}`);
    assert.equal(a.outboxCounters.failed, 0, traffic.join("\n"));
  } finally {
    globalThis.fetch = realFetch;
  }
});
