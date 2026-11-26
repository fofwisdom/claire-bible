// Loopback of the requests of the sidecar to its own server (FEDERATION_REMOTE_READING_DESIGN.md Section 6.7, owner decision RD30).
//
// An account of this server is an actor as an actor of another server. Thus a read of a document of this server (an actor, an object, a collection), a delivery to an inbox of this server, and a WebFinger lookup of an account of this server go through the same code as with another server: the request is signed in the same way, and the request handler of the sidecar verifies and answers it. Only the transport changes: the request does not go to the network, it goes to the handler in this process.
//
// Fedify 2.4.6 on Deno makes each request with the global fetch after its check of the address (the runtime pinner of Fedify is null on Deno), so one wrapper of the global fetch catches the reads, the deliveries, and the lookups of Fedify. The self-checks of the public path and the transport of the internal API keep the real fetch: they must use the network.

export type RequestHandler = (request: Request) => Promise<Response>;

function requestUrl(input: RequestInfo | URL): string {
  if (input instanceof Request) return input.url;
  return input instanceof URL ? input.href : String(input);
}

// A fetch that gives each request to the canonical origin of the server to `handle`, and each other request to `realFetch`. `origin` gives the canonical origin, or null while it is not known (then each request goes to the network).
export function loopbackFetch(realFetch: typeof fetch, origin: () => Promise<string | null>, handle: RequestHandler): typeof fetch {
  return async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    let own: string | null = null;
    try {
      own = await origin();
    } catch {
      own = null;
    }
    let target: URL | null = null;
    try {
      target = new URL(requestUrl(input));
    } catch {
      target = null;
    }
    if (own !== null && target !== null && target.origin === new URL(own).origin) {
      return await handle(new Request(input, init));
    }
    return await realFetch(input, init);
  };
}
