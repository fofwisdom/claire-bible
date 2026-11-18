// The POST of a delivery with a pinned address (src/pinned_fetch.ts, KNOWLEDGE_FEDERATION.md Section 14.5). The transport is a fake: no network.

import { strict as assert } from "node:assert";
import { type ByteConn, pinnedFetch, type PinnedTransport, PrivateAddressError } from "../src/pinned_fetch.ts";
import { deliverKnowledgeTask, type KnowledgeDeliveryDeps } from "../src/knowledge.ts";
import type { InternalApi } from "../src/internal_api.ts";

// A connection that records what the client writes and gives the answer in parts.
class FakeConn implements ByteConn {
  written: Uint8Array[] = [];
  closed = false;
  #parts: Uint8Array[];
  constructor(answer: string[]) {
    this.#parts = answer.map((part) => new TextEncoder().encode(part));
  }
  read(buffer: Uint8Array): Promise<number | null> {
    const part = this.#parts.shift();
    if (part === undefined) return Promise.resolve(null);
    buffer.set(part);
    return Promise.resolve(part.length);
  }
  write(data: Uint8Array): Promise<number> {
    // A short write: the client must write the rest.
    const count = Math.min(data.length, 7);
    this.written.push(data.slice(0, count));
    return Promise.resolve(count);
  }
  close(): void {
    this.closed = true;
  }
  text(): string {
    return new TextDecoder().decode(new Uint8Array(this.written.flatMap((part) => [...part])));
  }
}

function transport(addresses: { address: string; family: number }[], answer: string[] = ["HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n"]) {
  const calls: string[] = [];
  // The answer comes on the TLS connection for https, and on the TCP connection for http.
  const tcp = new FakeConn(answer);
  const tls = new FakeConn(answer);
  const fake: PinnedTransport = {
    canResolve: () => true,
    lookup: (hostname) => {
      calls.push(`lookup ${hostname}`);
      return Promise.resolve(addresses);
    },
    connect: (address, port) => {
      calls.push(`connect ${address}:${port}`);
      return Promise.resolve(tcp);
    },
    startTls: (conn, hostname) => {
      assert.equal(conn, tcp);
      calls.push(`tls ${hostname}`);
      return Promise.resolve(tls);
    },
  };
  return { fake, calls, tcp, tls };
}

Deno.test("the POST connects to the address that was checked, with the TLS name and the Host header of the URL, and gives the status", async () => {
  const { fake, calls, tls } = transport([{ address: "93.184.216.34", family: 4 }, { address: "2606:2800:220:1:248:1893:25c8:1946", family: 6 }], ["HTTP/1.1 103 Early Hints\r\nLink: </a>\r\n\r\nHTTP/1.1 2", "02 Accepted\r\nContent-Length: 0\r\n\r\n"]);
  const body = JSON.stringify({ id: "https://a.example/ap/kb/1#accepts/1" });
  const request = new Request("https://inbox.example:8443/ap/actors/server/inbox?x=1", { method: "POST", headers: { "content-type": "application/activity+json", signature: "keyId=\"k\"", host: "inbox.example:8443" }, body });
  const response = await pinnedFetch(fake)(request);
  assert.equal(response.status, 202);
  // One lookup; the connection goes to the first address that was checked, not to a second lookup.
  assert.deepEqual(calls, ["lookup inbox.example", "connect 93.184.216.34:8443", "tls inbox.example"]);
  const text = tls.text();
  const [head, sent] = text.split("\r\n\r\n");
  const lines = head.split("\r\n");
  assert.equal(lines[0], "POST /ap/actors/server/inbox?x=1 HTTP/1.1");
  assert.ok(lines.includes("Host: inbox.example:8443"));
  assert.ok(lines.includes('signature: keyId="k"'));
  assert.ok(lines.includes("content-type: application/activity+json"));
  assert.ok(lines.includes(`Content-Length: ${new TextEncoder().encode(body).length}`));
  assert.ok(lines.includes("Connection: close"));
  assert.equal(lines.filter((line) => line.toLowerCase().startsWith("host:")).length, 1);
  // The body is the activity without a change.
  assert.equal(sent, body);
  assert.ok(tls.closed);
});

Deno.test("an answer of a name with one address that is not public is refused before a connection", async () => {
  for (const addresses of [[{ address: "93.184.216.34", family: 4 }, { address: "10.0.0.5", family: 4 }], [{ address: "::1", family: 6 }], [{ address: "169.254.169.254", family: 4 }], []]) {
    const { fake, calls } = transport(addresses);
    await assert.rejects(pinnedFetch(fake)(new Request("https://rebind.example/inbox", { method: "POST", body: "{}" })), PrivateAddressError);
    assert.deepEqual(calls, ["lookup rebind.example"]);
  }
  // An address in the URL is checked without a lookup.
  const { fake, calls } = transport([]);
  await assert.rejects(pinnedFetch(fake)(new Request("http://127.0.0.1:8080/inbox", { method: "POST", body: "{}" })), PrivateAddressError);
  assert.deepEqual(calls, []);
});

Deno.test("http has no TLS, and an answer without a status line is an error of the request", async () => {
  const plain = transport([{ address: "93.184.216.34", family: 4 }]);
  assert.equal((await pinnedFetch(plain.fake)(new Request("http://inbox.example/inbox", { method: "POST", body: "{}" }))).status, 202);
  assert.deepEqual(plain.calls, ["lookup inbox.example", "connect 93.184.216.34:80"]);
  assert.match(plain.tcp.text(), /^POST \/inbox HTTP\/1\.1\r\nHost: inbox\.example\r\n/);
  const broken = transport([{ address: "93.184.216.34", family: 4 }], ["SSH-2.0-OpenSSH\r\n\r\n"]);
  await assert.rejects(pinnedFetch(broken.fake)(new Request("https://inbox.example/inbox", { method: "POST", body: "{}" })), TypeError);
  const closed = transport([{ address: "93.184.216.34", family: 4 }], []);
  await assert.rejects(pinnedFetch(closed.fake)(new Request("https://inbox.example/inbox", { method: "POST", body: "{}" })), TypeError);
});

Deno.test("a delivery whose address is not public at the connection is rejected as inbox_private_address", async () => {
  const { fake } = transport([{ address: "192.168.1.10", family: 4 }]);
  const deps = {
    api: {} as InternalApi,
    origin: "https://b.example",
    isBlocked: () => Promise.resolve(false),
    documentLoader: (url: string) => Promise.resolve({ contextUrl: null, documentUrl: url, document: { id: url, inbox: `${url}/inbox` } }),
    signingKey: () => Promise.resolve({ privateKey: {} as CryptoKey, keyId: new URL("https://b.example/ap/kb/1#main-key") }),
    sign: (request: Request) => Promise.resolve(request),
    // The check before the POST passed (the first DNS answer was public); the answer at the connection is private.
    checkAddress: () => Promise.resolve(),
    fetch: pinnedFetch(fake),
  } as KnowledgeDeliveryDeps;
  const task = { id: 1, kind: "accept_follow", signer: "1", recipient: "https://rebind.example/ap/actors/server", activity: { id: "https://b.example/ap/kb/1#accepts/1" } };
  assert.deepEqual(await deliverKnowledgeTask(deps, task), { result: "rejected", error: "inbox_private_address" });
});

Deno.test("without the permission net for each host the global fetch sends the request", async () => {
  const realFetch = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = (input: RequestInfo | URL) => {
    seen.push(new Request(input).url);
    return Promise.resolve(new Response(null, { status: 202 }));
  };
  try {
    const { fake, calls } = transport([{ address: "93.184.216.34", family: 4 }]);
    const status = (await pinnedFetch({ ...fake, canResolve: () => false })(new Request("https://inbox.example/inbox", { method: "POST", body: "{}" }))).status;
    assert.equal(status, 202);
    assert.deepEqual(calls, []);
    assert.deepEqual(seen, ["https://inbox.example/inbox"]);
  } finally {
    globalThis.fetch = realFetch;
  }
});
