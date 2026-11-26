// A runner for the tests of two real Claire servers (A: https://a.claire.test, B: https://b.claire.test) and their real sidecars.
//
// The Python test starts the two Claire processes, makes the steps of its accounts with the routes, and runs this file with E2E_WORK: a list of `{server}:{queue}` items, for example "a:reads,a:outbox". The runner makes the two sidecars with the real code and runs the queues in that order, again and again, until no queue gives work. The only part that is not real is the network: a request of a sidecar to the origin of a server goes to the handle function of the sidecar of that server. A request to another host is an error of the test.
//
// Queues: reads (the reads of known servers and of their /kb collections), remote (the read queue of the knowledge federation), outbox (the knowledge outbox), client (the client tasks), interaction (the interaction tasks), objects (the publish outbox).
//
// Environment: E2E_WORK, CLAIRE_A_SOCKET, CLAIRE_A_KEY_FILE, CLAIRE_B_SOCKET, CLAIRE_B_KEY_FILE.

import assert from "node:assert/strict";
import { InProcessMessageQueue, MemoryKvStore } from "@fedify/fedify";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";

function sidecarFor(socketEnv: string, keyEnv: string, realFetch: typeof fetch): Sidecar {
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: Deno.env.get(socketEnv)! } });
  const api = new InternalApi({ key: Deno.readFileSync(Deno.env.get(keyEnv)!), transport: (request) => realFetch(request, { client }) });
  // A queue as in production: a delivery comes after the acknowledgement of its task.
  return new Sidecar({ api, kv: new MemoryKvStore(), queue: new InProcessMessageQueue() });
}

Deno.test({ name: `two Claire servers: ${Deno.env.get("E2E_WORK")}`, sanitizeOps: false, sanitizeResources: false }, async () => {
  const realFetch = globalThis.fetch;
  const servers: Record<string, Sidecar> = { a: sidecarFor("CLAIRE_A_SOCKET", "CLAIRE_A_KEY_FILE", realFetch), b: sidecarFor("CLAIRE_B_SOCKET", "CLAIRE_B_KEY_FILE", realFetch) };
  const traffic: string[] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    traffic.push(`${request.method} ${url.host}${url.pathname}`);
    if (url.host === "a.claire.test") return await servers.a.handle(request);
    if (url.host === "b.claire.test") return await servers.b.handle(request);
    throw new Error(`a request to a host outside the test: ${request.url}`);
  };
  const queues: Record<string, (s: Sidecar) => Promise<number>> = {
    reads: (s) => s.processKnowledgeReads(),
    remote: (s) => s.processKnowledgeRemoteReads(),
    outbox: (s) => s.processKnowledgeOutbox(),
    client: (s) => s.processClientTasks(),
    interaction: (s) => s.processInteractionTasks(),
    objects: (s) => s.processOutboxTasks(),
  };
  const work = (Deno.env.get("E2E_WORK") ?? "").split(",").filter(Boolean).map((item) => item.split(":"));
  const settle = async () => {
    let seen = -1;
    for (let wait = 0; wait < 100 && seen !== traffic.length; wait += 1) {
      seen = traffic.length;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  };
  try {
    for (let round = 0; round < 20; round += 1) {
      const before = traffic.length;
      let done = 0;
      for (const [server, queue] of work) done += await queues[queue](servers[server]);
      await settle();
      if (done === 0 && traffic.length === before) break;
      assert.ok(round < 19, "the queues did not end");
    }
    console.log(`TRAFFIC ${JSON.stringify(traffic)}`);
    for (const [name, s] of Object.entries(servers)) {
      console.log(`COUNTERS ${name} ${JSON.stringify(s.knowledgeDeliveryCounters)} ${JSON.stringify(s.outboxCounters)}`);
      assert.equal(s.knowledgeDeliveryCounters.rejected + s.knowledgeDeliveryCounters.reads_failed + s.outboxCounters.failed, 0, `${name}: ${traffic.join("\n")}`);
    }
  } finally {
    globalThis.fetch = realFetch;
  }
});
