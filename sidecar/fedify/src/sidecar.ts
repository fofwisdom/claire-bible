// Public federation surface of the sidecar for phase FP1: WebFinger, NodeInfo, actor documents, and the instance actor (design Section 5).
//
// Rules of this module:
// - Claire is canonical. Each request reads account data through the internal API. The sidecar stores no account data.
// - All URLs come from the canonical origin that Claire supplies. The request Host header and the X-Forwarded-* headers are not read.
// - Cookie and Authorization headers are removed before a request gets to Fedify. A response never sets a cookie.
// - When federation is off or the mode is single, each path returns the same 404.
// - The option that permits private network addresses is never set (design Section 13, FS1). A test reads this directory to make sure.
// - Inbox paths, the followers collection, and the outbox come in phases FP2 and FP3. Until then those paths return 404.

import { createFederation, type Federation, importJwk, type KvStore, type MessageQueue } from "@fedify/fedify";
import { Application, Endpoints, Person } from "@fedify/fedify/vocab";
import { type ActorRecord, INSTANCE_ACTOR_ID, type InstanceConfig, type InternalApi } from "./internal_api.ts";

// The USERNAME of the instance actor. Claire reserves this word, so no account can have it.
export const INSTANCE_ACTOR_USERNAME = "claire";
export const KEY_CACHE_SECONDS = 60;
export const CONFIG_CACHE_SECONDS = 5;

const FEDERATION_PATHS = /^\/(?:\.well-known\/(?:webfinger|nodeinfo)|nodeinfo\/2\.1|ap\/.+)$/;
const PUBLIC_ID = /^[1-9][0-9]{0,18}$/;
const REMOVED_REQUEST_HEADERS = ["cookie", "authorization", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port"];

export interface SidecarOptions {
  api: InternalApi;
  kv: KvStore;
  queue?: MessageQueue;
  // Unix time in seconds.
  now?: () => number;
}

function notFound(): Response {
  return new Response("Not Found", { status: 404, headers: { "content-type": "text/plain; charset=utf-8" } });
}

export class Sidecar {
  readonly #api: InternalApi;
  readonly #kv: KvStore;
  readonly #queue: MessageQueue | undefined;
  readonly #now: () => number;
  #config: { value: InstanceConfig; expires: number } | null = null;
  #federation: { origin: string; value: Federation<void> } | null = null;
  readonly #keys = new Map<string, { pairs: CryptoKeyPair[]; expires: number }>();

  constructor(options: SidecarOptions) {
    this.#api = options.api;
    this.#kv = options.kv;
    this.#queue = options.queue;
    this.#now = options.now ?? (() => Date.now() / 1000);
  }

  async config(): Promise<InstanceConfig> {
    const now = this.#now();
    if (this.#config === null || this.#config.expires <= now) {
      this.#config = { value: await this.#api.config(), expires: now + CONFIG_CACHE_SECONDS };
    }
    return this.#config.value;
  }

  instanceActorUrl(config: InstanceConfig): string {
    return `${config.canonical_origin}/ap/actors/${INSTANCE_ACTOR_ID}`;
  }

  async handle(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (!FEDERATION_PATHS.test(path)) return notFound();
    let config: InstanceConfig;
    try {
      config = await this.config();
    } catch (error) {
      console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return new Response("Service Unavailable", { status: 503, headers: { "content-type": "text/plain; charset=utf-8", "retry-after": "5" } });
    }
    if (config.federation !== "on" || config.mode !== "network") return notFound();

    const headers = new Headers(request.headers);
    for (const name of REMOVED_REQUEST_HEADERS) headers.delete(name);
    const clean = new Request(request.url, { method: request.method, headers, body: request.body, redirect: "manual" });
    const response = await this.#federationFor(config).fetch(clean, {
      contextData: undefined,
      onNotFound: notFound,
      onNotAcceptable: () => new Response("Not Acceptable", { status: 406, headers: { "content-type": "text/plain; charset=utf-8", vary: "Accept" } }),
    });
    if (!response.headers.has("set-cookie")) return response;
    const safe = new Headers(response.headers);
    safe.delete("set-cookie");
    return new Response(response.body, { status: response.status, headers: safe });
  }

  #federationFor(config: InstanceConfig): Federation<void> {
    if (this.#federation === null || this.#federation.origin !== config.canonical_origin) {
      this.#federation = { origin: config.canonical_origin, value: this.#build(config.canonical_origin) };
    }
    return this.#federation.value;
  }

  async #keyPairs(identifier: string): Promise<CryptoKeyPair[]> {
    const now = this.#now();
    const cached = this.#keys.get(identifier);
    if (cached !== undefined && cached.expires > now) return cached.pairs;
    this.#keys.delete(identifier);
    const keys = await this.#api.actorKeys(identifier);
    if (keys === null) return [];
    const pairs: CryptoKeyPair[] = [];
    // The RSA key is first: Fedify uses the first key for HTTP Signatures.
    for (const alg of ["rsa2048", "ed25519"]) {
      const key = keys.keys.find((k) => k.alg === alg);
      if (key === undefined) continue;
      pairs.push({ privateKey: await importJwk(key.private_jwk as JsonWebKey, "private"), publicKey: await importJwk(key.public_jwk as JsonWebKey, "public") });
    }
    this.#keys.set(identifier, { pairs, expires: now + KEY_CACHE_SECONDS });
    return pairs;
  }

  #build(origin: string): Federation<void> {
    const federation = createFederation<void>({ kv: this.#kv, queue: this.#queue, origin });
    const inbox = (identifier: string) => new URL(`/ap/actors/${identifier}/inbox`, origin);
    const sharedInbox = new URL("/ap/inbox", origin);

    federation
      .setActorDispatcher("/ap/actors/{identifier}", async (ctx, identifier) => {
        if (identifier === INSTANCE_ACTOR_ID) {
          const config = await this.config();
          const keys = await ctx.getActorKeyPairs(identifier);
          return new Application({
            id: ctx.getActorUri(identifier),
            preferredUsername: INSTANCE_ACTOR_USERNAME,
            name: config.handle_host,
            inbox: inbox(identifier),
            endpoints: new Endpoints({ sharedInbox }),
            publicKey: keys[0]?.cryptographicKey,
            assertionMethods: keys.map((k) => k.multikey),
            manuallyApprovesFollowers: true,
          });
        }
        if (!PUBLIC_ID.test(identifier)) return null;
        const record: ActorRecord | null = await this.#api.actor(identifier);
        if (record === null) return null;
        const keys = await ctx.getActorKeyPairs(identifier);
        return new Person({
          id: ctx.getActorUri(identifier),
          preferredUsername: record.username,
          name: record.display_name,
          url: new URL(record.profile_url),
          inbox: inbox(identifier),
          endpoints: new Endpoints({ sharedInbox }),
          publicKey: keys[0]?.cryptographicKey,
          assertionMethods: keys.map((k) => k.multikey),
          published: record.activated_at === null ? null : Temporal.Instant.fromEpochMilliseconds(Math.round(record.activated_at * 1000)),
          manuallyApprovesFollowers: false,
        });
      })
      .mapHandle(async (_ctx, username) => {
        if (username === INSTANCE_ACTOR_USERNAME) return INSTANCE_ACTOR_ID;
        return (await this.#api.actorByUsername(username))?.public_id ?? null;
      })
      .setKeyPairsDispatcher((_ctx, identifier) => this.#keyPairs(identifier));

    federation.setNodeInfoDispatcher("/nodeinfo/2.1", async () => {
      const config = await this.config();
      return {
        software: { name: config.software.name, version: config.software.version },
        protocols: ["activitypub"],
        openRegistrations: false,
        // Federated actors only. The number of private accounts is not shown (design Section 5.1 rule 6).
        usage: { users: { total: config.federated_actors }, localPosts: 0, localComments: 0 },
      };
    });
    return federation;
  }
}

// Public self-check (design Section 12, GF6): a request through the public origin must get the WebFinger answer of this sidecar for the instance actor.
export async function publicSelfCheck(config: InstanceConfig, fetcher: typeof fetch = fetch): Promise<boolean> {
  const url = `${config.canonical_origin}/.well-known/webfinger?resource=${encodeURIComponent(`acct:${INSTANCE_ACTOR_USERNAME}@${config.handle_host}`)}`;
  try {
    const response = await fetcher(url, { headers: { accept: "application/jrd+json" }, redirect: "manual", signal: AbortSignal.timeout(10_000) });
    if (response.status !== 200) return false;
    const body = await response.json();
    const links = Array.isArray(body?.links) ? body.links as { rel?: string; href?: string }[] : [];
    return links.some((link) => link.rel === "self" && link.href === `${config.canonical_origin}/ap/actors/${INSTANCE_ACTOR_ID}`);
  } catch {
    return false;
  }
}
