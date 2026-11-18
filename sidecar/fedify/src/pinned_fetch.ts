// The POST of a delivery of the knowledge federation with a pinned address (KNOWLEDGE_FEDERATION.md Section 14.5).
//
// The check of an address (validatePublicUrl of Fedify) resolves the name, and fetch resolves it again when it connects: between the two lookups a DNS answer can change (DNS rebinding), and the POST can go to a private address. Fedify 2.4.5 pins the connection on Node.js and Bun only; it has no pinner for Deno, and fetch of Deno has no option for the address of the connection (Deno.createHttpClient can send to a fixed TCP address, but without TLS).
// This module does the request itself: it resolves the name one time, refuses the request when one address is not public, connects to the first address (Deno.connect), starts TLS with the name of the URL (Deno.startTls: the server name of TLS and the check of the certificate use the name, not the address), and sends one HTTP/1.1 request with "Connection: close". It reads only the status line: the delivery needs only the status, and the body of the answer is not read.
// No new dependency: Deno and the address checks of @fedify/vocab-runtime (isValidPublicIPv4Address, isValidPublicIPv6Address) only.

import { lookup as dnsLookup } from "node:dns/promises";
import { isIP } from "node:net";
import { isValidPublicIPv4Address, isValidPublicIPv6Address } from "jsr:@fedify/vocab-runtime@^2.4.5";

// A connection: Deno.Conn and Deno.TlsConn have these functions.
export interface ByteConn {
  read(buffer: Uint8Array): Promise<number | null>;
  write(data: Uint8Array): Promise<number>;
  close(): void;
}

export interface PinnedTransport {
  // True when the process has the permission net without a host list (the sidecar in production: FEDERATION.md Section 9.3). Without it the name cannot be resolved, and fetch can connect only to the hosts of the permission; then the request uses the global fetch, as openPublicFetch of Fedify does.
  canResolve(): boolean;
  // All addresses of a name (node:dns lookup with all: true, as the check of Fedify).
  lookup(hostname: string): Promise<readonly { address: string; family: number }[]>;
  connect(address: string, port: number): Promise<ByteConn>;
  startTls(conn: ByteConn, hostname: string): Promise<ByteConn>;
}

// An address of the request is not public: the delivery is refused (inbox_private_address), as the check before it.
export class PrivateAddressError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PrivateAddressError";
  }
}

// The largest status line and header block that the function reads before the status of the answer.
const MAX_HEAD_BYTES = 16 * 1024;

export const denoTransport: PinnedTransport = {
  canResolve: () => Deno.permissions.querySync({ name: "net" }).state === "granted",
  lookup: (hostname) => dnsLookup(hostname, { all: true, verbatim: true }),
  connect: (address, port) => Deno.connect({ hostname: address, port }),
  startTls: (conn, hostname) => Deno.startTls(conn as Deno.TcpConn, { hostname }),
};

function isPublic(address: string, family: number): boolean {
  return family === 4 ? isValidPublicIPv4Address(address) : family === 6 ? isValidPublicIPv6Address(address) : false;
}

// The addresses of the host of a URL, each one public. Throws PrivateAddressError when one address is not public or when the lookup gives no address; another error of the lookup goes to the caller.
export async function publicAddresses(transport: PinnedTransport, hostname: string): Promise<string[]> {
  const literal = hostname.startsWith("[") && hostname.endsWith("]") ? hostname.slice(1, -1) : hostname;
  const literalFamily = isIP(literal);
  const found = literalFamily !== 0 ? [{ address: literal, family: literalFamily }] : await transport.lookup(literal);
  const addresses: string[] = [];
  for (const entry of found) {
    const family = isIP(entry.address);
    if (family === 0) continue;
    if (!isPublic(entry.address, family)) throw new PrivateAddressError(`the address ${entry.address} of ${literal} is not public`);
    addresses.push(entry.address);
  }
  if (addresses.length === 0) throw new PrivateAddressError(`${literal} has no address`);
  return addresses;
}

async function writeAll(conn: ByteConn, data: Uint8Array): Promise<void> {
  let offset = 0;
  while (offset < data.length) offset += await conn.write(data.subarray(offset));
}

// The status of the final answer: a 1xx answer (for example 103) comes before it and is skipped.
async function readStatus(conn: ByteConn): Promise<number> {
  const decoder = new TextDecoder();
  let text = "";
  const buffer = new Uint8Array(4096);
  while (text.length <= MAX_HEAD_BYTES) {
    const end = text.indexOf("\r\n\r\n");
    if (end !== -1) {
      const match = /^HTTP\/1\.[01] (\d{3})[ \r]/.exec(text);
      if (match === null) throw new TypeError("the answer has no HTTP/1.1 status line");
      const status = Number(match[1]);
      if (status >= 200) return status;
      text = text.slice(end + 4);
      continue;
    }
    const count = await conn.read(buffer);
    if (count === null) throw new TypeError("the connection closed before the status of the answer");
    text += decoder.decode(buffer.subarray(0, count), { stream: true });
  }
  throw new TypeError("the head of the answer is too large");
}

// A fetch for the POST of a delivery that connects only to an address that it checked. The result is a Response with the status of the answer and no body. Throws PrivateAddressError for an address that is not public; each other failure is an error of the request (the caller tries again later). The signal closes the connection.
export function pinnedFetch(transport: PinnedTransport = denoTransport): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    if (!transport.canResolve()) return await globalThis.fetch(request, init);
    const signal = init?.signal ?? request.signal;
    signal?.throwIfAborted();
    const url = new URL(request.url);
    if (url.protocol !== "https:" && url.protocol !== "http:") throw new TypeError(`the scheme ${url.protocol} is not supported`);
    const addresses = await publicAddresses(transport, url.hostname);
    const body = new Uint8Array(await request.arrayBuffer());
    const port = url.port !== "" ? Number(url.port) : url.protocol === "https:" ? 443 : 80;
    let conn: ByteConn | null = null;
    const abort = () => conn?.close();
    signal?.addEventListener("abort", abort, { once: true });
    try {
      conn = await transport.connect(addresses[0], port);
      signal?.throwIfAborted();
      if (url.protocol === "https:") conn = await transport.startTls(conn, url.hostname);
      signal?.throwIfAborted();
      // The Host header is the one that the HTTP Signature covers: the host of the URL.
      const lines = [`${request.method} ${url.pathname}${url.search} HTTP/1.1`, `Host: ${request.headers.get("host") ?? url.host}`];
      for (const [name, value] of request.headers) {
        if (!["host", "content-length", "connection", "transfer-encoding"].includes(name)) lines.push(`${name}: ${value}`);
      }
      lines.push(`Content-Length: ${body.length}`, "Connection: close", "", "");
      await writeAll(conn, new TextEncoder().encode(lines.join("\r\n")));
      await writeAll(conn, body);
      return new Response(null, { status: await readStatus(conn) });
    } catch (error) {
      signal?.throwIfAborted();
      throw error;
    } finally {
      signal?.removeEventListener("abort", abort);
      try {
        conn?.close();
      } catch {
        // The connection is closed already (the signal closed it).
      }
    }
  }) as typeof fetch;
}
