// Public federation surface of the sidecar: WebFinger, NodeInfo, actor documents, the instance actor (phase FP1), inboxes, the followers collection (unit FP2-3), published objects, the outbox collection, and the handoff of outbox tasks (unit FP3-3) (design Sections 5 and 9).
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
// - A published object is an Article. Claire supplies its canonical form and decides if it is public. A deleted object gets 410 with a Tombstone.
// - The outbox collection has one Create for each public object. The item refers to the object by its ID.
// - An actor task becomes Update(Person) or Delete(Person) (unit FP3-4). A purged actor gets 410 with a Tombstone.
// - An outbox task becomes one activity with a stable ID: a second handoff of the same task makes the same activity, and a remote server makes no second object (design Section 9.3). The sidecar acknowledges a task only after Fedify took the activity.
// - A delivery does not go to a follower on a blocked domain.
// - A client task (contract 1.3, unit FP4-3) becomes Follow, Undo(Follow), or Reject(Follow). A Follow has the ID {actor}#follows/{following_id}; an Accept or a Reject of it is matched by that ID, and its object is never fetched (RT1). A follow of an actor on a blocked domain is not sent.

import { type Context, createFederation, type Federation, importJwk, type InboxContext, type KvStore, type MessageQueue } from "@fedify/fedify";
import { Accept, Activity, Application, Article, Create, Delete, Endpoints, Follow, getTypeId, isActor, Person, PUBLIC_COLLECTION, type Recipient, Reject, Tombstone, Undo, Update } from "@fedify/fedify/vocab";
import { type ActorRecord, type ClientTask, type InboxDecision, type InboxEvent, INSTANCE_ACTOR_ID, type InstanceConfig, type InternalApi, type ObjectRecord, type OutboxTask, type ResolvedActor } from "./internal_api.ts";

// The USERNAME of the instance actor. Claire reserves this word, so no account can have it.
export const INSTANCE_ACTOR_USERNAME = "claire";
export const KEY_CACHE_SECONDS = 60;
export const CONFIG_CACHE_SECONDS = 5;
export const SIDECAR_HEADER = "x-claire-fedify-instance";

const FEDERATION_PATHS = /^\/(?:\.well-known\/(?:webfinger|nodeinfo)|nodeinfo\/2\.1|ap\/.+)$/;
const INBOX_PATHS = /^\/ap\/(?:inbox|actors\/[^/]+\/inbox)$/;
// The paths that can give 410 with a Tombstone: a deleted object and a purged actor.
const TOMBSTONE_PATH = /^\/ap\/(objects|actors)\/([1-9][0-9]{0,18})$/;
const PUBLIC_ID = /^[1-9][0-9]{0,18}$/;
const IPV4 = /^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$/;
// The largest page of the internal followers endpoint.
const FOLLOWERS_PAGE_SIZE = 80;
// The number of objects in one page of the public outbox collection.
export const OUTBOX_PAGE_SIZE = 20;
// The number of outbox tasks that one poll takes.
export const OUTBOX_TASK_BATCH = 10;
const AS2_CONTEXT = "https://www.w3.org/ns/activitystreams";
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

// Numbers of outbox tasks since the start of this process, by result. failed: an error occurred, there was no acknowledgement, and Claire gives the task again after the lease.
export interface OutboxCounters {
  handed_off: number;
  rejected: number;
  failed: number;
}

function instant(seconds: number): Temporal.Instant {
  return Temporal.Instant.fromEpochMilliseconds(Math.round(seconds * 1000));
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
  readonly outboxCounters: OutboxCounters = { handed_off: 0, rejected: 0, failed: 0 };
  // Deleted objects and purged actors that a dispatcher found, with the time of removal. The key is the path. The answer code reads the entry and removes it.
  readonly #gone = new Map<string, number>();

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
    const tombstonePath = TOMBSTONE_PATH.exec(path);
    if (tombstonePath !== null) {
      const goneAt = this.#gone.get(path);
      this.#gone.delete(path);
      if (response.status === 404 && goneAt !== undefined) {
        await response.body?.cancel();
        const tombstone = { "@context": AS2_CONTEXT, id: `${config.canonical_origin}${path}`, type: "Tombstone", formerType: tombstonePath[1] === "objects" ? "Article" : "Person", deleted: instant(goneAt).toString() };
        return new Response(JSON.stringify(tombstone), { status: 410, headers: { "content-type": "application/activity+json" } });
      }
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

  // The followers of an actor that can get a delivery: a follower with an actor, an inbox, or a shared inbox on a blocked domain is not in the list.
  async deliveryRecipients(identifier: string): Promise<Recipient[]> {
    const recipients: Recipient[] = [];
    for (const recipient of await this.followerRecipients(identifier)) {
      const hosts = [recipient.id, recipient.inboxId, recipient.endpoints?.sharedInbox ?? null].filter((url) => url !== null).map((url) => url.hostname);
      let blocked = false;
      for (const host of hosts) blocked ||= await this.isBlocked(host);
      if (!blocked) recipients.push(recipient);
    }
    return recipients;
  }

  async #person(ctx: Context<void>, record: ActorRecord): Promise<Person> {
    const identifier = record.public_id;
    const keys = await ctx.getActorKeyPairs(identifier);
    return new Person({
      id: ctx.getActorUri(identifier),
      preferredUsername: record.username,
      name: record.display_name,
      url: new URL(record.profile_url),
      inbox: ctx.getInboxUri(identifier),
      outbox: ctx.getOutboxUri(identifier),
      followers: ctx.getFollowersUri(identifier),
      following: ctx.getFollowingUri(identifier),
      endpoints: new Endpoints({ sharedInbox: ctx.getInboxUri() }),
      publicKey: keys[0]?.cryptographicKey,
      assertionMethods: keys.map((k) => k.multikey),
      published: record.activated_at === null ? null : instant(record.activated_at),
      manuallyApprovesFollowers: false,
    });
  }

  #article(ctx: Context<void>, record: ObjectRecord): Article {
    return new Article({
      id: ctx.getObjectUri(Article, { id: record.public_id }),
      attribution: ctx.getActorUri(record.actor_public_id),
      name: record.name,
      content: record.content,
      url: record.url === null ? null : new URL(record.url),
      published: instant(record.published_at),
      updated: instant(record.updated_at),
      to: PUBLIC_COLLECTION,
      cc: ctx.getFollowersUri(record.actor_public_id),
    });
  }

  // Takes the ready outbox tasks from Claire and gives each one to Fedify as an activity (design Section 9.3). Returns the number of tasks that Claire gave.
  // An error in one task does not stop the other tasks. A task without an acknowledgement comes again after its lease.
  async processOutboxTasks(limit = OUTBOX_TASK_BATCH): Promise<number> {
    const config = await this.config();
    if (config.federation !== "on" || config.mode !== "network") return 0;
    const tasks = await this.#api.outboxTasks(limit);
    if (tasks.length === 0) return 0;
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    for (const task of tasks) {
      try {
        this.outboxCounters[await this.#handOff(ctx, task)] += 1;
      } catch (error) {
        this.outboxCounters.failed += 1;
        console.error(`outbox task ${task.id} (${task.kind}) failed: ${(error as Error).name}: ${(error as Error).message}`);
      }
    }
    return tasks.length;
  }

  async #handOff(ctx: Context<void>, task: OutboxTask): Promise<"handed_off" | "rejected"> {
    const reject = async (code: string): Promise<"rejected"> => {
      console.warn(`outbox task ${task.id} (${task.kind}) rejected: ${code}`);
      await this.#api.ackOutboxTask(task.id, "rejected", code);
      return "rejected";
    };
    const actor = ctx.getActorUri(task.actor_public_id);
    const audience = { actor, to: PUBLIC_COLLECTION, cc: ctx.getFollowersUri(task.actor_public_id) };
    if (task.kind === "update_actor" || task.kind === "delete_actor") {
      let activity: Activity;
      if (task.kind === "update_actor") {
        const record = await this.#api.actor(task.actor_public_id);
        // The account is not an actor now (pause or purge). A pause sends nothing (design Section 8.1).
        if (record === null) return await reject("not_an_actor");
        // The ID has the time of the change, the same rule as for the Update of an object.
        activity = new Update({ id: new URL(`#updates/${Math.round(record.updated_at * 1000)}`, actor), object: await this.#person(ctx, record), ...audience });
      } else {
        // Claire gives the keys of a purged actor only for this task. Without a key no remote server has a key to verify with, and nothing can be sent.
        if ((await ctx.getActorKeyPairs(task.actor_public_id)).length === 0) return await reject("no_actor_key");
        activity = new Delete({ id: new URL("#delete", actor), object: actor, ...audience });
      }
      const recipients = await this.deliveryRecipients(task.actor_public_id);
      if (recipients.length > 0) await ctx.sendActivity({ identifier: task.actor_public_id }, recipients, activity, { preferSharedInbox: true, orderingKey: actor.href });
      // After this acknowledgement of a delete_actor task, Claire deletes the followers and the keys of the actor.
      await this.#api.ackOutboxTask(task.id, "handed_off");
      this.#keys.delete(task.actor_public_id);
      return "handed_off";
    }
    if (task.object_public_id === null) return await reject("no_object");
    const objectUri = ctx.getObjectUri(Article, { id: task.object_public_id });
    let activity: Activity;
    if (task.kind === "delete") {
      activity = new Delete({ id: new URL("#delete", objectUri), object: new Tombstone({ id: objectUri }), ...audience });
    } else {
      const result = await this.#api.object(task.object_public_id);
      if (result === null) return await reject("object_not_found");
      if (result.state === "gone") return await reject("object_gone");
      const record = result.record;
      if (record.actor_public_id !== task.actor_public_id) return await reject("actor_mismatch");
      const article = this.#article(ctx, record);
      // The ID of an Update has the time of the change: each change is one activity, and a second handoff of the same change is the same activity.
      activity = task.kind === "create" ? new Create({ id: new URL("#create", objectUri), object: article, published: instant(record.published_at), ...audience }) : new Update({ id: new URL(`#updates/${Math.round(record.updated_at * 1000)}`, objectUri), object: article, published: instant(record.updated_at), ...audience });
    }
    const recipients = await this.deliveryRecipients(task.actor_public_id);
    // The ordering key keeps the Create, the Update, and the Delete of one object in sequence for each remote server.
    if (recipients.length > 0) await ctx.sendActivity({ identifier: task.actor_public_id }, recipients, activity, { preferSharedInbox: true, orderingKey: objectUri.href });
    await this.#api.ackOutboxTask(task.id, "handed_off");
    return "handed_off";
  }

  // Takes the ready client tasks from Claire (contract 1.3) and gives each one to Fedify. Returns the number of tasks that Claire gave.
  async processClientTasks(limit = OUTBOX_TASK_BATCH): Promise<number> {
    const config = await this.config();
    if (config.federation !== "on" || config.mode !== "network") return 0;
    const tasks = await this.#api.clientTasks(limit);
    if (tasks.length === 0) return 0;
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    for (const task of tasks) {
      try {
        this.outboxCounters[await this.#handOffClient(ctx, task)] += 1;
      } catch (error) {
        this.outboxCounters.failed += 1;
        console.error(`client task ${task.id} (${task.kind}) failed: ${(error as Error).name}: ${(error as Error).message}`);
      }
    }
    return tasks.length;
  }

  async #handOffClient(ctx: Context<void>, task: ClientTask): Promise<"handed_off" | "rejected"> {
    const reject = async (code: string): Promise<"rejected"> => {
      console.warn(`client task ${task.id} (${task.kind}) rejected: ${code}`);
      await this.#api.ackClientTask(task.id, "rejected", code);
      return "rejected";
    };
    const sender = { identifier: task.actor_public_id };
    const actorUri = ctx.getActorUri(task.actor_public_id);
    // The remote actor: a handle goes through WebFinger, an IRI is fetched. The fetch is signed with the key of the actor (servers with authorized fetch).
    let remote: unknown = null;
    try {
      remote = await ctx.lookupObject(task.target, { documentLoader: await ctx.getDocumentLoader(sender) });
    } catch {
      remote = null;
    }
    if (!isActor(remote) || remote.id === null || remote.inboxId === null) return await reject("actor_not_found");
    if (task.kind === "follow") {
      if (task.following_id === null) return await reject("no_following_id");
      const hosts = [remote.id, remote.inboxId, remote.endpoints?.sharedInbox ?? null].filter((url) => url !== null).map((url) => url.hostname);
      for (const host of hosts) if (await this.isBlocked(host)) return await reject("blocked_domain");
      const typeId = getTypeId(remote);
      const type = typeId.hash.slice(1) || typeId.href.split("#").pop() || "";
      if (!["Person", "Group", "Service", "Application", "Organization"].includes(type)) return await reject("unsupported_actor_type");
      const follow = new Follow({ id: new URL(`#follows/${task.following_id}`, actorUri), actor: actorUri, object: remote.id });
      await ctx.sendActivity(sender, remote, follow);
      const name = remote.name?.toString() ?? null;
      const resolved: ResolvedActor = { iri: remote.id.href, type: type as ResolvedActor["type"], inbox: remote.inboxId.href, shared_inbox: remote.endpoints?.sharedInbox?.href ?? null, name: name === null ? null : name.slice(0, 512) };
      await this.#api.ackClientTask(task.id, "handed_off", null, resolved);
      return "handed_off";
    }
    let activity: Activity;
    if (task.kind === "undo_follow") {
      // The ID of the Follow is the one that the follow task sent. A follow without a number (none today) gets an ID from the task.
      const followId = new URL(task.following_id === null ? `#client-tasks/${task.id}/follow` : `#follows/${task.following_id}`, actorUri);
      activity = new Undo({ id: new URL(`${followId.hash}/undo`, actorUri), actor: actorUri, object: new Follow({ id: followId, actor: actorUri, object: remote.id }) });
    } else {
      // reject_follow: the owner removed the follower. The Follow of the remote actor is described by its actor and object.
      activity = new Reject({ id: new URL(`#rejects/client-tasks/${task.id}`, actorUri), actor: actorUri, object: new Follow({ actor: remote.id, object: actorUri }) });
    }
    await ctx.sendActivity(sender, remote, activity);
    await this.#api.ackClientTask(task.id, "handed_off");
    return "handed_off";
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
        const result = await this.#api.actorResult(identifier);
        if (result === null) return null;
        if (result.state === "gone") {
          this.#gone.set(`/ap/actors/${identifier}`, result.gone_at);
          return null;
        }
        return await this.#person(ctx, result.record);
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

    federation
      // The following collection shows the total only (design Section 5.1 rule 7, RD9).
      .setFollowingDispatcher("/ap/actors/{identifier}/following", async (_ctx, identifier) => {
        if (!PUBLIC_ID.test(identifier)) return null;
        return (await this.#api.following(identifier)) === null ? null : { items: [] };
      })
      .setCounter(async (_ctx, identifier) => {
        if (!PUBLIC_ID.test(identifier)) return null;
        return (await this.#api.following(identifier))?.total ?? null;
      });

    federation.setObjectDispatcher(Article, "/ap/objects/{id}", async (ctx, values) => {
      if (!PUBLIC_ID.test(values.id)) return null;
      const result = await this.#api.object(values.id);
      if (result === null) return null;
      if (result.state === "gone") {
        this.#gone.set(`/ap/objects/${values.id}`, result.gone_at);
        return null;
      }
      return this.#article(ctx, result.record);
    });

    federation
      .setOutboxDispatcher("/ap/actors/{identifier}/outbox", async (ctx, identifier, cursor) => {
        // The cursor is the empty string for the first page, or the ID of the last object of the page before.
        if (!PUBLIC_ID.test(identifier) || (cursor !== null && cursor !== "" && !PUBLIC_ID.test(cursor))) return null;
        const page = await this.#api.actorOutbox(identifier, { limit: OUTBOX_PAGE_SIZE, maxId: cursor || undefined });
        if (page === null) return null;
        const audience = { actor: ctx.getActorUri(identifier), to: PUBLIC_COLLECTION, cc: ctx.getFollowersUri(identifier) };
        const items = page.items.map((item) => {
          const objectUri = ctx.getObjectUri(Article, { id: item.id });
          return new Create({ id: new URL("#create", objectUri), object: objectUri, published: instant(item.published_at), ...audience });
        });
        return { items, nextCursor: page.next_max_id };
      })
      // An account that is not an actor has no first page. Then Fedify asks the dispatcher for the full collection, and the dispatcher gives 404.
      .setFirstCursor(async (_ctx, identifier) => PUBLIC_ID.test(identifier) && (await this.#api.actorOutbox(identifier, { limit: 1 })) !== null ? "" : null)
      .setCounter(async (_ctx, identifier) => {
        if (!PUBLIC_ID.test(identifier)) return null;
        return (await this.#api.actorOutbox(identifier, { limit: 1 }))?.total ?? null;
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
