// The loopback of the sidecar (RD30, FEDERATION_REMOTE_READING_DESIGN.md Section 6.7): a request to the origin of this server goes to the request handler of the process; each other request goes to the network.

import assert from "node:assert/strict";
import { loopbackFetch } from "../src/loopback.ts";

Deno.test("a request to the own origin goes to the handler, and each other request to the network", async () => {
  const network: string[] = [];
  const handled: string[] = [];
  const realFetch = ((input: RequestInfo | URL) => {
    network.push(input instanceof Request ? input.url : String(input));
    return Promise.resolve(new Response("network"));
  }) as typeof fetch;
  const handle = async (request: Request) => {
    handled.push(`${request.method} ${request.url} ${request.headers.get("signature") ?? ""} ${await request.text()}`);
    return new Response("handler", { status: 202 });
  };
  const fetcher = loopbackFetch(realFetch, () => Promise.resolve("https://claire.example.com"), handle);
  const posted = await fetcher("https://claire.example.com/ap/actors/1/inbox", { method: "POST", headers: { signature: "sig" }, body: "{}" });
  assert.equal(posted.status, 202);
  assert.equal(await (await fetcher(new Request("https://claire.example.com/.well-known/webfinger?resource=acct:a@claire.example.com"))).text(), "handler");
  assert.equal(await (await fetcher(new URL("https://remote.example/users/dan"))).text(), "network");
  assert.deepEqual(handled, ["POST https://claire.example.com/ap/actors/1/inbox sig {}", "GET https://claire.example.com/.well-known/webfinger?resource=acct:a@claire.example.com  "]);
  assert.deepEqual(network, ["https://remote.example/users/dan"]);
});

Deno.test("without a known origin each request goes to the network", async () => {
  const network: string[] = [];
  const realFetch = ((input: RequestInfo | URL) => {
    network.push(String(input));
    return Promise.resolve(new Response("network"));
  }) as typeof fetch;
  const fetcher = loopbackFetch(realFetch, () => Promise.reject(new Error("no config")), () => Promise.resolve(new Response("handler")));
  assert.equal(await (await fetcher("https://claire.example.com/ap/actors/1")).text(), "network");
  assert.deepEqual(network, ["https://claire.example.com/ap/actors/1"]);
});
