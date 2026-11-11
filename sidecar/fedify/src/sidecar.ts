// Public federation surface of the sidecar: WebFinger, NodeInfo, actor documents, the instance actor (phase FP1), inboxes, and the followers collection (unit FP2-3) (design Section 5).
//
// Rules of this module:
// - Claire is canonical. Each request reads account data through the internal API. The sidecar stores no account data.
// - All URLs come from the canonical origin that Claire supplies. The request Host header and the X-Forwarded-* headers do not change a URL in an answer.
// - The inbox paths accept POST. On all other paths a method that is not GET or HEAD gets 405.
// - Fedify verifies each inbox activity. Claire makes the decision for each verified activity (design Section 6.3). The sidecar does not accept an activity on behalf of Claire: if the internal API is not available, the listener fails and the Fedify inbox queue tries again.
// - The sidecar keeps the list of blocked domains in memory and reads it again when the entity tag in the config changes. An activity from a blocked domain gets 403 after the signature check, goes to no listener work, and gets no reply.
// - The followers collection shows the total to the public and no follower.
// - Each answer has the header SIDECAR_HEADER with a random value of this process. The public self-check uses it to see that a request through the public origin gets to this sidecar. The value shows no instance data.
// - Cookie and Authorization headers are removed before a request gets to Fedify. A response never sets a cookie.
// - When federation is off or the mode is single, each path returns the same 404.
// - The option that permits private network addresses is never set (design Section 13, FS1). A test reads this directory to make sure.
// - The outbox and the object paths come in phase FP3. Until then those paths return 404.

import { type Context, createFederation, type Federation, importJwk, type InboxContext, type KvStore, type MessageQueue } from "@fedify/fedify";
import { Accept, Activity, Application, Endpoints, Follow, getTypeId, Person, type Recipient, Reject, Undo } from "@fedify/fedify/vocab";
import { type ActorRecord, type InboxDecision, type InboxEvent, INSTANCE_ACTOR_ID, type InstanceConfig, type InternalApi } from "./internal_api.ts";

// The USERNAME of the instance actor. Claire reserves this word, so no account can have it.
export const INSTANCE_ACTOR_USERNAME = "claire";
export const KEY_CACHE_SECONDS = 60;
export const CONFIG_CACHE_SECONDS = 5;
export const SIDECAR_HEADER = "x-claire-fedify-instance";

const FEDERATION_PATHS = /^\/(?:\.well-known\/(?:webfinger|nodeinfo)|nodeinfo\/2\.1|ap\/.+)$/;
const INBOX_PATHS = /^\/ap\/(?:inbox|actors\/[^/]+\/inbox)$/;
const PUBLIC_ID = /^[1-9][0-9]{0,18}$/;
const IPV4 = /^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$/;
// The largest page of the internal followers endpoint.
const FOLLOWERS_PAGE_SIZE = 80;
const REMOVED_REQUEST_HEADERS = ["cookie", "authorization", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port"];

export interface SidecarOptions {
  api: InternalApi;
  kv: KvStore;
  queue?: MessageQueue;
  // Unix time in seconds.
  now?: () => number;
}

// Numbers of inbox activities since the start of this process, by result.
export interface InboxCounters {
  accepted: number;
  rejected: number;
  ignored: number;
  blocked: number;
  dropped: number;
}

function text(status: number, body: string, headers: Record<string, string> = {}): Response {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8", ...headers } });
}

// The host of the actor of an activity document, or null. This reads the body before the signature check, only to select the 403 answer after the check.
function actorHost(body: Uint8Array): string | null {
  try {
    const document = JSON.parse(new TextDecoder().decode(body));
    let actor = document?.actor;
    if (Array.isArray(actor)) actor = actor[0];
    if (typeof actor === "object" && actor !== null) actor = actor.id;
    return typeof actor === "string" ? new URL(actor).hostname : null;
  } catch {
    return null;
  }
}

// Reads a request body of `limit` bytes at most. Returns null for a larger body.
async function readLimited(request: Request, limit: number): Promise<Uint8Array<ArrayBuffer> | null> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(declared) && declared > limit) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  if (request.body !== null) {
    for await (const chunk of request.body) {
      size += chunk.byteLength;
      if (size > limit) return null;
      chunks.push(chunk);
    }
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
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
  #blocks: { etag: string; domains: Set<string> } | null = null;
  readonly counters: InboxCounters = { accepted: 0, rejected: 0, ignored: 0, blocked: 0, dropped: 0 };

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

  // A random value of this process. It is in the header SIDECAR_HEADER of each answer.
  readonly marker: string = crypto.randomUUID();

  async handle(request: Request): Promise<Response> {
    const response = await this.#respond(request);
    const headers = new Headers(response.headers);
    headers.set(SIDECAR_HEADER, this.marker);
    headers.delete("set-cookie");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  }

  async #respond(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (!FEDERATION_PATHS.test(path)) return notFound();
    const inbox = INBOX_PATHS.test(path);
    if (inbox ? request.method !== "POST" : request.method !== "GET" && request.method !== "HEAD") {
      return text(405, "Method Not Allowed", { allow: inbox ? "POST" : "GET, HEAD" });
    }
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
    let body: Uint8Array<ArrayBuffer> | null = null;
    let blocked = false;
    if (inbox) {
      body = await readLimited(request, config.limits.inbox_body_bytes);
      if (body === null) return text(413, "Content Too Large");
      const host = actorHost(body);
      try {
        blocked = host !== null && await this.isBlocked(host);
      } catch (error) {
        console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
        return text(503, "Service Unavailable", { "retry-after": "5" });
      }
    }
    const clean = new Request(request.url, { method: request.method, headers, body: inbox ? body : request.body, redirect: "manual" });
    let response: Response;
    try {
      response = await this.#federationFor(config).fetch(clean, {
        contextData: undefined,
        onNotFound: notFound,
        onNotAcceptable: () => text(406, "Not Acceptable", { vary: "Accept" }),
      });
    } catch (error) {
      // For example: Claire stopped after the config was read. The sender tries again.
      console.error(`request failed: ${(error as Error).name}: ${(error as Error).message}`);
      return text(503, "Service Unavailable", { "retry-after": "5" });
    }
    // The 403 comes after the signature check of Fedify: a request without a valid signature gets 401 and learns nothing about the blocks (design Section 5.3).
    if (blocked && response.ok) {
      await response.body?.cancel();
      return text(403, "Forbidden");
    }
    return response;
  }

  // True when a blocked domain applies to the host. A block applies to the domain and to its subdomains. An IP address has an exact comparison only.
  // The list comes from Claire and stays in memory until the entity tag in the config changes.
  async isBlocked(host: string): Promise<boolean> {
    const config = await this.config();
    if (this.#blocks === null || this.#blocks.etag !== config.domain_blocks_etag) {
      const blocks = await this.#api.domainBlocks();
      this.#blocks = { etag: blocks.etag, domains: new Set(blocks.domains) };
    }
    const name = host.toLowerCase().replace(/\.$/, "");
    if (this.#blocks.domains.has(name)) return true;
    if (IPV4.test(name) || name.includes(":")) return false;
    const labels = name.split(".");
    return labels.some((_label, index) => index > 0 && this.#blocks!.domains.has(labels.slice(index).join(".")));
  }

  // All followers of an actor as delivery recipients, newest first. The public collection does not use this list.
  async followerRecipients(identifier: string, pageSize = FOLLOWERS_PAGE_SIZE): Promise<Recipient[]> {
    const recipients: Recipient[] = [];
    let maxId: string | undefined = undefined;
    for (;;) {
      const page = await this.#api.followers(identifier, { limit: pageSize, maxId });
      if (page === null) break;
      for (const item of page.items) {
        recipients.push({ id: new URL(item.actor_iri), inboxId: new URL(item.inbox_iri), endpoints: item.shared_inbox_iri === null ? null : { sharedInbox: new URL(item.shared_inbox_iri) } });
      }
      if (page.next_max_id === null) break;
      maxId = page.next_max_id;
    }
    return recipients;
  }

  // One verified inbound activity: makes the event, gets the decision of Claire, and sends the reply (design Section 6.3).
  // An error from the internal API or from the fetch of the remote actor goes to Fedify, and the inbox queue tries again.
  async #onActivity(ctx: InboxContext<void>, activity: Activity): Promise<void> {
    const type = getTypeId(activity).hash.slice(1) || getTypeId(activity).href;
    const drop = (why: string) => {
      this.counters.dropped += 1;
      console.warn(`inbox activity ${type} dropped: ${why}`);
    };
    if (activity.id === null || activity.actorId === null) return drop("no activity ID or no actor");
    if (await this.isBlocked(activity.actorId.hostname)) {
      this.counters.blocked += 1;
      return;
    }

    let objectType: string | null = null;
    let objectIri = activity.objectId?.href ?? null;
    if (activity instanceof Undo) {
      // The object of an Undo is the activity that is undone. Claire needs its type and its object.
      const undone = await activity.getObject({ ...ctx, suppressError: true });
      if (undone instanceof Activity) {
        const undoneType = getTypeId(undone);
        objectType = undoneType.hash.slice(1) || undoneType.href;
        objectIri = undone.objectId?.href ?? null;
      }
    }

    const actor = await activity.getActor({ ...ctx, suppressError: true });
    if (actor === null || actor.id === null || actor.id.href !== activity.actorId.href || actor.inboxId === null) return drop("the actor document has no inbox");
    const document = await activity.toJsonLd({ contextLoader: ctx.contextLoader }) as Record<string, unknown>;
    const event: InboxEvent = {
      event_id: activity.id.href,
      type,
      object_type: objectType,
      recipient_public_id: ctx.recipient !== null && PUBLIC_ID.test(ctx.recipient) ? ctx.recipient : null,
      actor: { iri: actor.id.href, inbox: actor.inboxId.href, shared_inbox: actor.endpoints?.sharedInbox?.href ?? null },
      object_iri: objectIri,
      // Fedify verified the activity with one of the mechanisms that the activity has. The value names the strongest one that is present.
      verified_by: "proof" in document ? "object-integrity-proof" : "signature" in document ? "ld-signature" : "http-signature",
    };
    const decision: InboxDecision = await this.#api.inboxEvent(event);
    this.counters[decision.decision] += 1;
    if (decision.decision !== "accepted") console.warn(`inbox activity ${type} ${decision.decision}: ${decision.reason}`);
    if (!(activity instanceof Follow)) return;

    const followed = ctx.parseUri(activity.objectId);
    if (followed?.type !== "actor" || !PUBLIC_ID.test(followed.identifier)) return;
    const sender = { identifier: followed.identifier };
    // The reply has the Follow with IDs only. The activity that came in can have the full actor document in it.
    const follow = new Follow({ id: activity.id, actor: activity.actorId, object: activity.objectId });
    const replyId = async (kind: string) => {
      const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(event.event_id)));
      return new URL(`#${kind}/${[...digest.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("")}`, ctx.getActorUri(followed.identifier));
    };
    if (decision.decision === "accepted" && decision.reply === "accept") {
      await ctx.sendActivity(sender, actor, new Accept({ id: await replyId("accepts"), actor: ctx.getActorUri(followed.identifier), object: follow }));
      return;
    }
    // A Reject needs the key of the actor that was followed. An account that is not an actor has no key, so it sends no reply. A blocked domain and an address that is not permitted get no delivery.
    if (decision.decision === "rejected" && decision.reason === "recipient_mismatch" && (await ctx.getActorKeyPairs(followed.identifier)).length > 0) {
      await ctx.sendActivity(sender, actor, new Reject({ id: await replyId("rejects"), actor: ctx.getActorUri(followed.identifier), object: follow }));
    }
  }

  // A Fedify context for code that has no HTTP request, for example a signed fetch with the key of the instance actor.
  async createContext(): Promise<Context<void>> {
    const config = await this.config();
    return this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
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
          followers: ctx.getFollowersUri(identifier),
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

    federation
      .setInboxListeners("/ap/actors/{identifier}/inbox", "/ap/inbox")
      // One listener for all types: Claire decides which types apply (the v1 set) and which are ignored.
      .on(Activity, (ctx, activity) => this.#onActivity(ctx, activity))
      // Fetches of remote documents for the shared inbox are signed with the key of the instance actor.
      .setSharedKeyDispatcher(() => ({ identifier: INSTANCE_ACTOR_ID }))
      .onError((_ctx, error) => console.error(`inbox listener failed: ${error.name}: ${error.message}`));

    federation
      .setFollowersDispatcher("/ap/actors/{identifier}/followers", async (ctx, identifier) => {
        if (!PUBLIC_ID.test(identifier)) return null;
        // A context with a request is a public read of the collection: it gets no follower (design Section 5.1). The delivery code has no request and gets the list.
        if ("request" in ctx) return (await this.#api.actor(identifier)) === null ? null : { items: [] };
        return { items: await this.followerRecipients(identifier) };
      })
      .setCounter(async (_ctx, identifier) => {
        if (!PUBLIC_ID.test(identifier)) return null;
        return (await this.#api.followers(identifier, { limit: 1 }))?.total ?? null;
      });

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

// Public self-check (design Section 12, GF6): a request through the public origin must get to this sidecar process.
// The check does not depend on the federation switch: the admin can set the switch to `on` only after this check passes. Thus the status of the answer is not important (it is 404 while federation is off). The marker header shows which process answered.
export async function publicSelfCheck(config: InstanceConfig, marker: string, fetcher: typeof fetch = fetch): Promise<boolean> {
  const url = `${config.canonical_origin}/.well-known/webfinger?resource=${encodeURIComponent(`acct:${INSTANCE_ACTOR_USERNAME}@${config.handle_host}`)}`;
  try {
    const response = await fetcher(url, { headers: { accept: "application/jrd+json" }, redirect: "manual", signal: AbortSignal.timeout(10_000) });
    await response.body?.cancel();
    return response.headers.get(SIDECAR_HEADER) === marker;
  } catch {
    return false;
  }
}
