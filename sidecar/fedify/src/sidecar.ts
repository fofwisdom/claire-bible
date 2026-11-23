// Public federation surface of the sidecar: WebFinger, NodeInfo, actor documents, the server actor (phase FP1), inboxes, the followers collection (unit FP2-3), published objects, the outbox collection, and the handoff of outbox tasks (unit FP3-3) (design Sections 5 and 9).
//
// Rules of this module:
// - Claire is canonical. Each request reads account data through the internal API. The sidecar stores no account data.
// - All URLs come from the canonical origin that Claire supplies. The request Host header and the X-Forwarded-* headers do not change a URL in an answer.
// - The inbox paths accept POST. On all other paths a method that is not GET or HEAD gets 405.
// - Fedify verifies each inbox activity. Claire makes the decision for each verified activity (design Section 6.3). The sidecar does not accept an activity on behalf of Claire: if the internal API is not available, the listener fails and the Fedify inbox queue tries again.
// - The sidecar keeps the list of blocked domains in memory and reads it again when the entity tag in the config changes. An activity from a blocked domain gets 403 after the signature check, goes to no listener work, and gets no reply.
// - The followers collection shows the total to the public and no follower.
// - Each answer has the header SIDECAR_HEADER with a random value of this process. The public self-check uses it to see that a request through the public origin gets to this sidecar. The value shows no server data.
// - Cookie and Authorization headers are removed before a request gets to Fedify. A response never sets a cookie.
// - Federation has no switch (FEDERATION_GO_LIVE_DESIGN.md decision GD4). The mode is not a condition: the server actor, the knowledge federation, and the account actors work in single mode too (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5, KD2); Claire decides which account is an actor.
// - The option that permits private network addresses is never set (design Section 13, FS1). A test reads this directory to make sure.
// - A published object is an Article. Claire supplies its canonical form and decides if it is public. A deleted object gets 410 with a Tombstone.
// - The outbox collection has one Create for each public object. The item refers to the object by its ID.
// - An actor task becomes Update(Person) or Delete(Person) (unit FP3-4). A purged actor gets 410 with a Tombstone.
// - An outbox task becomes one activity with a stable ID: a second handoff of the same task makes the same activity, and a remote server makes no second object (design Section 9.3). The sidecar acknowledges a task only after Fedify took the activity.
// - A delivery does not go to a follower on a blocked domain.
// - A Create, an Update, or an Announce of a post, a Delete of an object, and an Undo(Announce) go to Claire as a timeline event with the normal form of the object (unit FP4-4). The sidecar fetches the object of an Announce and the parents of a reply only when Claire says that an account follows the actor. All other activities go to Claire as an inbox event.
// - Inbound interactions with an account (contract 1.4, unit FP4-8): a Create from an actor that no account follows is a timeline event only when its object, as it is in the activity, mentions an actor of this server or is a reply to an object of this server; nothing is fetched for it. A Like or an Announce of an object of this server, and the Undo of one, stay inbox events: Claire counts them. For a non-public message, Claire can answer with automatic notices; the sidecar sends each one as a Note to the sender only, with the ID {actor}#auto-replies/{digest of the object ID}.
// - A thread fetch task (contract 1.5, unit FP4-6) has no activity: the sidecar fetches the objects of a thread and gives them to Claire (context.ts).
// - An own note (contract 1.6, unit FP4-9) has the object path of the published documents: one dispatcher for the two (RT7). A deleted note gets 410 with a Tombstone whose formerType is Note. The outbox collection has the notes too. An interaction task becomes Create, Update, or Delete of a Note, or Like, Announce, and their Undo (notes.ts).
// - Quotes (contract 1.7, unit FP4-9Q, quotes.ts): an Article and a Note have canQuote from the quote policy that Claire gives. A QuoteRequest for an object of this server, and an Accept or a Reject of the quote request of an own note, go to Claire as quote events. An approved request is a QuoteAuthorization at /ap/quote-authorizations/{id}; a revoked one gets 410 with a Tombstone.
// - Federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md, units KF1 and KF2, knowledge.ts): an inbound activity whose @context has the Claire context goes to Claire as a knowledge activity, with its JSON as it came; it is not an inbox event and not a timeline event. Such an activity does not go through the inbox listener of Fedify: Fedify reads an activity into its own vocabulary, which has no Grant and no Revoke, and it does not keep the JSON of each type as it came. The sidecar verifies the HTTP signature of the request and gives Claire the body and the actor of the key; Claire keeps the activity in its inbox journal and makes each other check. The collections /ap/kb and /ap/themes answer a signed GET: the sidecar verifies the signature, and Claire decides and gives the document. A job reads the known servers and their /kb collections with the key of the server actor.
// - A client task (contract 1.3, unit FP4-3) becomes Follow, Undo(Follow), or Reject(Follow). A Follow has the ID {actor}#follows/{following_id}; an Accept or a Reject of it is matched by that ID, and its object is never fetched (RT1). A follow of an actor on a blocked domain is not sent.

import { type Context, createFederation, type Federation, generateCryptoKeyPair, importJwk, type InboxContext, type KvStore, type MessageQueue, signRequest, verifyRequest } from "@fedify/fedify";
import { Accept, Activity, type Actor, Announce, Application, Article, Create, CryptographicKey, Delete, Endpoints, Follow, getTypeId, isActor, LanguageString, Mention, Note, Person, PUBLIC_COLLECTION, type Recipient, Reject, Tombstone, Undo, Update } from "@fedify/fedify/vocab";
import { getUserAgent, preloadedContexts } from "jsr:@fedify/vocab-runtime@^2.4.6";
import { type ActorRecord, type ClientTask, type HeartbeatLoops, type InboxDecision, type InboxEvent, InternalApiError, type KnowledgeActivityResult, type KnowledgeSelfCheck, type ProbeAnswer, type PublicSelfCheck, SERVER_ACTOR_ID, type ServerConfig, type InternalApi, type ObjectRecord, type OutboxTask, type ResolvedActor, type Traffic, type TimelineEvent, type TimelineNotice, type TimelineObject } from "./internal_api.ts";
import { CONTEXT_TASK_BATCH, processContextTasks } from "./context.ts";
// Answer attribution and traffic counters of the readiness checks (contract 1.13).
import { classifyAnswer, deliveryFailureClass, deliveryMeterProvider, noAnswer, probeAnswer, SELF_CHECK_HEADER, SIDECAR_HEADER, TrafficCounters } from "./probes.ts";
// Federation of shared knowledge (units KF1 to KF3).
import { pinnedFetch } from "./pinned_fetch.ts";
import { ACCOUNT_SIGNER_PREFIX, KB_ACTOR_PATH, KNOWLEDGE_COLLECTION_PATHS, type KnowledgeDeliveryCounters, type KnowledgeDeliveryDeps, knowledgeDocumentOf, KNOWLEDGE_OUTBOX_BATCH, KNOWLEDGE_READ_BATCH, type KnowledgeReadCounters, processKnowledgeOutbox, processKnowledgeReads, processRemoteReads, publicAddressCheck, SERVER_SIGNER } from "./knowledge.ts";
import { actorProfile, actorType, isPost, isPublic, type LoadOptions, localMentions, noFetch, normalObject, type Post } from "./timeline.ts";
// Outbound interactions (contract 1.6, unit FP4-9).
import { Object as ASObject } from "@fedify/fedify/vocab";
import { buildNote, handOffInteraction, knownMentions } from "./notes.ts";
// Quotes (contract 1.7, unit FP4-9Q).
import { QuoteAuthorization } from "@fedify/fedify/vocab";
import { buildQuoteAuthorization, quoteEventOf, quoteInteractionPolicy } from "./quotes.ts";

// The USERNAME of the server actor. Claire reserves this word, so no account can have it.
export const SERVER_ACTOR_USERNAME = "claire";
export const KEY_CACHE_SECONDS = 60;
export const CONFIG_CACHE_SECONDS = 5;
export { SIDECAR_HEADER };
export const SELF_CHECK_PATH = "/ap/self-check";
export const SELF_CHECK_TTL_SECONDS = 60;
export const SELF_CHECK_REQUEST_TIMEOUT_MS = 10_000;
export const SELF_CHECK_DEADLINE_MS = 20_000;
export const KNOWLEDGE_SELF_CHECK_PATHS = ["/.well-known/nodeinfo", "/nodeinfo/2.1", "/ap/actors/server", "/ap/kb", "/ap/themes", "/ap/kb/0", "/ap/inbox"] as const;
// The Accept header of a read of an ActivityPub document, in the two forms that other servers send.
export const ACTIVITY_ACCEPT = 'application/activity+json, application/ld+json; profile="https://www.w3.org/ns/activitystreams"';
// The two signature forms of a delivery of Fedify (the "double knock").
export const SIGNATURE_FORMS = { draft_cavage: "draft-cavage-http-signatures-12", rfc9421: "rfc9421" } as const;

const FEDERATION_PATHS = /^\/(?:\.well-known\/(?:webfinger|nodeinfo)|nodeinfo\/2\.1|ap\/.+)$/;
// The inbox of a knowledge base actor (/ap/kb/{number}/inbox) gets only activities of the knowledge federation. The actor document, its collections, and its documents are the paths of KB_ACTOR_PATH (unit KF3).
const INBOX_PATHS = /^\/ap\/(?:inbox|actors\/[^/]+\/inbox|kb\/[1-9][0-9]{0,18}\/inbox)$/;
// The paths that can give 410 with a Tombstone: a deleted object, a purged actor, and a revoked quote authorization.
const TOMBSTONE_PATH = /^\/ap\/(objects|actors|quote-authorizations)\/([1-9][0-9]{0,18})$/;
const PUBLIC_ID = /^[1-9][0-9]{0,18}$/;
const OBJECT_PATH = /^\/ap\/objects\/([1-9][0-9]{0,18})$/;
const IPV4 = /^[0-9]{1,3}(?:\.[0-9]{1,3}){3}$/;
// The largest page of the internal followers endpoint.
const FOLLOWERS_PAGE_SIZE = 80;
// The number of objects in one page of the public outbox collection.
export const OUTBOX_PAGE_SIZE = 20;
// The number of outbox tasks that one poll takes.
export const OUTBOX_TASK_BATCH = 10;
const AS2_CONTEXT = "https://www.w3.org/ns/activitystreams";
// The part of the body of a knowledge activity request to Claire that is not the activity: the keys, the signer, and the recipient.
const KNOWLEDGE_ENVELOPE_BYTES = 8192;
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

function escapeSummaryHtml(text: string): string {
  return text.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch] ?? ch)).replace(/\n/g, "<br/>");
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

function loopErrorCode(error: unknown): string {
  const raw = error instanceof InternalApiError ? error.code : (error as { name?: string })?.name ?? "error";
  const normalized = raw.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64);
  return normalized || "error";
}

// One self-check request that this process sent (GF6, GF8, GF9). The nonce of the request is the key of the entry.
type ProbeEntry = {
  origin: string;
  host: string;
  createdAt: number;
  expiresAt: number;
  // The path with the query and the Host that this process got for the nonce. null: the request did not come.
  received: { target: string; host: string } | null;
  // The signed POST of GF9 only.
  signed: boolean;
  signedSeen: boolean;
  signedResult: "ok" | "signature_invalid" | null;
};

// The path and the query parameters of two request targets are the same. The percent-encoding of the query can be different.
function sameTarget(sent: string, received: string): boolean {
  const a = new URL(sent, "http://probe.invalid");
  const b = new URL(received, "http://probe.invalid");
  return a.pathname === b.pathname && JSON.stringify([...a.searchParams]) === JSON.stringify([...b.searchParams]);
}

// TLS terminates at the external reverse proxy and the edge forwards plain HTTP without X-Forwarded-Proto.
// Restore the canonical scheme from Claire's origin so RFC 9421 @target-uri verification sees https:// while keeping the received host, path, and query intact.
function withCanonicalScheme(requestUrl: string, canonicalOrigin: string): string {
  const url = new URL(requestUrl);
  url.protocol = new URL(canonicalOrigin).protocol;
  return url.href;
}

export class Sidecar {
  readonly #api: InternalApi;
  readonly #kv: KvStore;
  readonly #queue: MessageQueue | undefined;
  readonly #now: () => number;
  #config: { value: ServerConfig; expires: number } | null = null;
  #federation: { origin: string; allowPrivate: boolean; value: Federation<void> } | null = null;
  readonly #keys = new Map<string, { pairs: CryptoKeyPair[]; expires: number }>();
  #blocks: { etag: string; domains: Set<string> } | null = null;
  readonly counters: InboxCounters = { accepted: 0, rejected: 0, ignored: 0, blocked: 0, dropped: 0 };
  readonly outboxCounters: OutboxCounters = { handed_off: 0, rejected: 0, failed: 0 };
  // Numbers of inbound activities of the knowledge federation since the start of this process, by the decision of Claire.
  readonly knowledgeCounters: Record<KnowledgeActivityResult["decision"], number> = { processed: 0, failed: 0, refused: 0, duplicate: 0, rejected: 0 };
  readonly knowledgeReadCounters: KnowledgeReadCounters = { servers_ok: 0, servers_failed: 0, collections_ok: 0, collections_failed: 0 };
  // Numbers of outbound activities of the knowledge federation and of reads for a curation (unit KF3).
  readonly knowledgeDeliveryCounters: KnowledgeDeliveryCounters = { delivered: 0, retry: 0, rejected: 0, reads_ok: 0, reads_failed: 0 };
  // Deleted objects and purged actors that a dispatcher found, with the time of removal. The key is the path. The answer code reads the entry and removes it.
  readonly #gone = new Map<string, number>();
  // The paths in #gone that are deleted notes (unit FP4-9): the Tombstone has the former type Note.
  readonly #goneNotes = new Set<string>();
  #selfCheckKey: Promise<CryptoKeyPair> | null = null;
  readonly #probes = new Map<string, ProbeEntry>();
  readonly #traffic = new TrafficCounters(() => this.#now());
  readonly #loops: HeartbeatLoops = {
    knowledge_reads: { running: false, progress_at: null, error: null, error_at: null },
    knowledge_exchange: { running: false, progress_at: null, error: null, error_at: null },
  };

  constructor(options: SidecarOptions) {
    this.#api = options.api;
    this.#kv = options.kv;
    this.#queue = options.queue;
    this.#now = options.now ?? (() => Date.now() / 1000);
  }

  async config(): Promise<ServerConfig> {
    const now = this.#now();
    if (this.#config === null || this.#config.expires <= now) {
      this.#config = { value: await this.#api.config(), expires: now + CONFIG_CACHE_SECONDS };
    }
    return this.#config.value;
  }

  serverActorUrl(config: ServerConfig): string {
    return `${config.canonical_origin}/ap/actors/${SERVER_ACTOR_ID}`;
  }

  // A random value of this process. It is in the header SIDECAR_HEADER of each answer.
  readonly marker: string = crypto.randomUUID();

  async handle(request: Request): Promise<Response> {
    this.#recordProbe(request);
    const response = await this.#respond(request);
    const headers = new Headers(response.headers);
    headers.set(SIDECAR_HEADER, this.marker);
    headers.delete("set-cookie");
    return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
  }

  async #respond(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === SELF_CHECK_PATH) return await this.#handleSelfCheck(request);
    if (!FEDERATION_PATHS.test(path)) return notFound();
    const inbox = INBOX_PATHS.test(path);
    if (inbox ? request.method !== "POST" : request.method !== "GET" && request.method !== "HEAD") {
      return text(405, "Method Not Allowed", { allow: inbox ? "POST" : "GET, HEAD" });
    }
    let config: ServerConfig;
    try {
      config = await this.config();
    } catch (error) {
      console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return new Response("Service Unavailable", { status: 503, headers: { "content-type": "text/plain; charset=utf-8", "retry-after": "5" } });
    }

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
    const clean = new Request(withCanonicalScheme(request.url, config.canonical_origin), { method: request.method, headers, body: inbox ? body : request.body, redirect: "manual" });
    const collection = KNOWLEDGE_COLLECTION_PATHS.get(path);
    if (collection !== undefined) return await this.#knowledgeCollection(clean, config, collection);
    const kbActor = inbox ? null : KB_ACTOR_PATH.exec(path);
    if (kbActor !== null) return await this.#knowledgeBaseActor(clean, config, kbActor);
    if (inbox && body !== null) {
      const activity = knowledgeDocumentOf(body);
      if (activity !== null) return await this.#knowledgeInbox(clean, config, path, activity, blocked, body.byteLength);
    }
    let response: Response;
    try {
      response = await this.#federationFor(config).fetch(clean, {
        contextData: undefined,
        onNotFound: notFound,
        // A request for an object ID that does not accept ActivityPub (a browser) goes to the permalink of the object, with 303: the object ID did not move, the server only selects a representation (PERMALINK_UNIFICATION_DESIGN.md U11).
        onNotAcceptable: async () => {
          const objectPath = OBJECT_PATH.exec(path);
          const permalink = objectPath === null ? null : await this.#permalinkOf(objectPath[1]);
          if (permalink !== null) return new Response(null, { status: 303, headers: { location: permalink, vary: "Accept", "cache-control": "no-store" } });
          return text(406, "Not Acceptable", { vary: "Accept" });
        },
      });
    } catch (error) {
      // For example: Claire stopped after the config was read. The sender tries again.
      console.error(`request failed: ${(error as Error).name}: ${(error as Error).message}`);
      return text(503, "Service Unavailable", { "retry-after": "5" });
    }
    // A signed inbox request that Fedify refused (GF19 counts the requests that verified; this number shows the other ones).
    if (inbox && response.status === 401 && (headers.has("signature") || headers.has("signature-input"))) this.#traffic.inboundRejected();
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
        const tombstone = { "@context": AS2_CONTEXT, id: `${config.canonical_origin}${path}`, type: "Tombstone", formerType: tombstonePath[1] === "objects" ? (this.#goneNotes.delete(path) ? "Note" : "Article") : tombstonePath[1] === "actors" ? "Person" : "QuoteAuthorization", deleted: instant(goneAt).toString() };
        return new Response(JSON.stringify(tombstone), { status: 410, headers: { "content-type": "application/activity+json" } });
      }
    }
    return response;
  }

  async #getSelfCheckKey(): Promise<CryptoKeyPair> {
    if (this.#selfCheckKey === null) {
      this.#selfCheckKey = generateCryptoKeyPair("RSASSA-PKCS1-v1_5");
    }
    return await this.#selfCheckKey;
  }

  #pruneProbes(now: number): void {
    for (const [nonce, entry] of this.#probes) {
      if (entry.expiresAt <= now) this.#probes.delete(nonce);
    }
  }

  // Records what this process got for a self-check request, before the routing. Thus the record exists also for a path that is not a federation path.
  #recordProbe(request: Request): void {
    const nonce = request.headers.get(SELF_CHECK_HEADER);
    if (nonce === null) return;
    const entry = this.#probes.get(nonce);
    const now = this.#now();
    if (entry === undefined || entry.received !== null || now < entry.createdAt || now - entry.createdAt > SELF_CHECK_TTL_SECONDS) return;
    const url = new URL(request.url);
    entry.received = { target: `${url.pathname}${url.search}`, host: (request.headers.get("host") ?? url.host).toLowerCase() };
  }

  // Self-check path POST /ap/self-check (KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1.4 item 2, GF9).
  async #handleSelfCheck(request: Request): Promise<Response> {
    const now = this.#now();
    this.#pruneProbes(now);
    if (request.method !== "POST") return notFound();
    const body = await readLimited(request, 4096);
    if (body === null) return notFound();
    let nonce: unknown = null;
    try {
      const parsed = JSON.parse(new TextDecoder().decode(body)) as { nonce?: unknown } | null;
      nonce = parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed.nonce : null;
    } catch {
      return notFound();
    }
    if (typeof nonce !== "string" || !nonce) return notFound();
    const entry = this.#probes.get(nonce);
    if (entry === undefined || !entry.signed || entry.signedSeen || now < entry.createdAt || now - entry.createdAt > SELF_CHECK_TTL_SECONDS) return notFound();
    entry.signedSeen = true;

    const url = new URL(request.url);
    const requestHost = (request.headers.get("host") ?? url.host).toLowerCase();
    // An intermediary can remove the self-check header. The body has the nonce too.
    entry.received ??= { target: `${url.pathname}${url.search}`, host: requestHost };
    // The sender of the self-check compares the Host and reports host_changed.
    if (requestHost !== entry.host) return notFound();

    const signatureInput = request.headers.get("signature-input");
    if (signatureInput !== null && signatureInput.split("=(").length !== signatureInput.split('"content-digest"').length) {
      entry.signedResult = "signature_invalid";
      return notFound();
    }

    const headers = new Headers(request.headers);
    for (const name of REMOVED_REQUEST_HEADERS) headers.delete(name);
    const clean = new Request(withCanonicalScheme(request.url, entry.origin), { method: "POST", headers, body });

    try {
      const pair = await this.#getSelfCheckKey();
      const actorUri = new URL(`${entry.origin}${SELF_CHECK_PATH}`);
      const keyId = new URL(`${entry.origin}${SELF_CHECK_PATH}#main-key`);
      const contextLoader = async (url: string) => {
        if (url in preloadedContexts) {
          return { contextUrl: null, documentUrl: url, document: (preloadedContexts as Record<string, unknown>)[url] };
        }
        throw new Error(`unexpected context fetch: ${url}`);
      };
      const actorDocument = await new Application({ id: actorUri, preferredUsername: "self-check", publicKey: new CryptographicKey({ id: keyId, owner: actorUri, publicKey: pair.publicKey }) }).toJsonLd({ contextLoader });
      const keyDocument = await new CryptographicKey({ id: keyId, owner: actorUri, publicKey: pair.publicKey }).toJsonLd({ contextLoader });
      const documentLoader = async (url: string) => {
        if (url === keyId.href) return { contextUrl: null, documentUrl: url, document: keyDocument };
        if (url === actorUri.href) return { contextUrl: null, documentUrl: url, document: actorDocument };
        throw new Error(`unexpected document fetch: ${url}`);
      };
      const key = await verifyRequest(clean, { documentLoader, contextLoader, currentTime: instant(now) });
      if (key !== null && key.id?.href === keyId.href && key.ownerId?.href === actorUri.href) {
        entry.signedResult = "ok";
        return new Response(null, { status: 202 });
      }
    } catch {
      // Verification failed; the result stays signature_invalid.
    }
    entry.signedResult = "signature_invalid";
    return notFound();
  }

  // Sends one self-check request to the public origin and tells what went wrong, or null (FEDERATION_GO_LIVE_DESIGN.md Sections 5.1 to 5.4).
  // The request has the headers of a request of another server (Section 6): the agent of the deliveries of this process and the header of the nonce.
  // The permalink (url) of a published document or a note of this server, or null: the object does not exist, it is gone, or it has no permalink.
  async #permalinkOf(publicId: string): Promise<string | null> {
    try {
      const result = await this.#api.object(publicId);
      if (result !== null) return result.state === "found" ? result.record.url : null;
      const note = await this.#api.note(publicId);
      return note !== null && note.state === "found" ? note.record.url : null;
    } catch (error) {
      console.error(`permalink of object ${publicId} is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return null;
    }
  }

  async #sendProbe(config: ServerConfig, fetcher: typeof fetch, signal: () => AbortSignal, path: string, signed: boolean, make: (url: string, nonce: string, headers: Record<string, string>) => Request | Promise<Request>): Promise<ProbeAnswer | null> {
    let host: string;
    try {
      host = new URL(config.canonical_origin).host.toLowerCase();
    } catch (error) {
      return probeAnswer(path, "not_routed", noAnswer(error));
    }
    const nonce = crypto.randomUUID();
    const started = this.#now();
    this.#probes.set(nonce, { origin: config.canonical_origin, host, createdAt: started, expiresAt: started + SELF_CHECK_TTL_SECONDS, received: null, signed, signedSeen: false, signedResult: null });
    let response: Response;
    try {
      const request = await make(`${config.canonical_origin}${path}`, nonce, { "user-agent": getUserAgent(), [SELF_CHECK_HEADER]: nonce });
      response = await fetcher(request, { redirect: "manual", signal: signal() });
      await response.body?.cancel();
    } catch (error) {
      return probeAnswer(path, "not_routed", noAnswer(error));
    }
    const answer = classifyAnswer(response, this.marker);
    if (answer.party !== "sidecar") return probeAnswer(path, "not_routed", answer);
    const entry = this.#probes.get(nonce);
    const received = entry?.received ?? null;
    if (received === null) return probeAnswer(path, "answer_not_live", answer);
    if (!sameTarget(path, received.target)) return probeAnswer(path, "path_changed", answer, received.target);
    if (received.host !== host) return probeAnswer(path, "host_changed", answer, received.host);
    if (signed && !(response.status === 202 && entry?.signedResult === "ok")) return probeAnswer(path, "signature_invalid", answer);
    return null;
  }

  // Public self-check (design Section 12, GF6): a request through the public origin must get to this sidecar process, with its path and its Host.
  // The status of the answer is not important: the server actor has the USERNAME of the request only when Claire gives the actor.
  async publicSelfCheck(config: ServerConfig, fetcher: typeof fetch = fetch): Promise<PublicSelfCheck> {
    this.#pruneProbes(this.#now());
    const path = `/.well-known/webfinger?resource=${encodeURIComponent(`acct:${SERVER_ACTOR_USERNAME}@${config.handle_host}`)}`;
    const answer = await this.#sendProbe(config, fetcher, () => AbortSignal.timeout(SELF_CHECK_REQUEST_TIMEOUT_MS), path, false, (url, _nonce, headers) => new Request(url, { headers: { ...headers, accept: "application/jrd+json" } }));
    return { ok: answer === null, checked_at: this.#now(), answer };
  }

  async knowledgeSelfCheck(config: ServerConfig, fetcher: typeof fetch = fetch): Promise<KnowledgeSelfCheck> {
    const started = this.#now();
    this.#pruneProbes(started);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new DOMException("self-check deadline", "TimeoutError")), SELF_CHECK_DEADLINE_MS);
    const requestSignal = (): AbortSignal => AbortSignal.any([controller.signal, AbortSignal.timeout(SELF_CHECK_REQUEST_TIMEOUT_MS)]);

    const checkPath = (path: string): Promise<ProbeAnswer | null> =>
      this.#sendProbe(config, fetcher, requestSignal, path, false, (url, _nonce, headers) => new Request(url, { headers: { ...headers, accept: path.startsWith("/ap/") ? ACTIVITY_ACCEPT : "application/json" } }));

    // The signed POST has the media type and the form of a delivery. Only the key is the self-check key.
    const checkSigned = (spec: (typeof SIGNATURE_FORMS)[keyof typeof SIGNATURE_FORMS]): Promise<ProbeAnswer | null> =>
      this.#sendProbe(config, fetcher, requestSignal, SELF_CHECK_PATH, true, async (url, nonce, headers) => {
        const pair = await this.#getSelfCheckKey();
        const body = JSON.stringify({ "@context": AS2_CONTEXT, id: `${url}#${nonce}`, type: "Create", actor: url, nonce });
        return await signRequest(new Request(url, { method: "POST", headers: { ...headers, "content-type": "application/activity+json" }, body }), pair.privateKey, new URL(`${url}#main-key`), { currentTime: instant(started), spec });
      });

    try {
      const [pathResults, draftCavage, rfc9421] = await Promise.all([
        Promise.all(KNOWLEDGE_SELF_CHECK_PATHS.map((path) => checkPath(path))),
        checkSigned(SIGNATURE_FORMS.draft_cavage),
        checkSigned(SIGNATURE_FORMS.rfc9421),
      ]);
      const failures = pathResults.filter((answer): answer is ProbeAnswer => answer !== null);
      return {
        checked_at: this.#now(),
        failed_paths: failures.map((answer) => answer.path),
        answers: failures.slice(0, 8),
        signed: {
          draft_cavage: { result: draftCavage === null ? "ok" : "failed", answer: draftCavage },
          rfc9421: { result: rfc9421 === null ? "ok" : "failed", answer: rfc9421 },
        },
      };
    } finally {
      clearTimeout(timer);
    }
  }

  // The requests of other servers and the deliveries to them in the last 24 hours (GF19, GF20).
  traffic(): Traffic {
    return this.#traffic.snapshot();
  }

  loops(): HeartbeatLoops {
    return {
      knowledge_reads: { ...this.#loops.knowledge_reads },
      knowledge_exchange: { ...this.#loops.knowledge_exchange },
    };
  }

  async runKnowledgeReads(): Promise<void> {
    const state = this.#loops.knowledge_reads;
    if (state.running) return;
    state.running = true;
    try {
      while ((await this.processKnowledgeReads()) >= KNOWLEDGE_READ_BATCH) {
        state.progress_at = this.#now();
        state.error = null;
        state.error_at = null;
      }
      state.progress_at = this.#now();
      state.error = null;
      state.error_at = null;
    } catch (error) {
      state.error = loopErrorCode(error);
      state.error_at = this.#now();
      throw error;
    } finally {
      state.running = false;
    }
  }

  async runKnowledgeExchange(): Promise<void> {
    const state = this.#loops.knowledge_exchange;
    if (state.running) return;
    state.running = true;
    try {
      while ((await this.processKnowledgeRemoteReads()) >= KNOWLEDGE_READ_BATCH) {
        state.progress_at = this.#now();
        state.error = null;
        state.error_at = null;
      }
      state.progress_at = this.#now();
      while ((await this.processKnowledgeOutbox()) >= KNOWLEDGE_OUTBOX_BATCH) {
        state.progress_at = this.#now();
        state.error = null;
        state.error_at = null;
      }
      state.progress_at = this.#now();
      state.error = null;
      state.error_at = null;
    } catch (error) {
      state.error = loopErrorCode(error);
      state.error_at = this.#now();
      throw error;
    } finally {
      state.running = false;
    }
  }

  // The actor whose key made the valid HTTP signature of a request, or null when the request has no valid signature.
  // The key document must be on the server of its owner: the actor of a key on another server is not a signer. A request with a body must have its digest in the signature (Fedify requires this for the draft form; this function requires it for RFC 9421).
  async #requestSigner(request: Request, config: ServerConfig): Promise<string | null> {
    const signatureInput = request.headers.get("signature-input");
    // A request without a signature header is not a rejected request: it is an unsigned read.
    const signed = signatureInput !== null || request.headers.has("signature");
    if (request.method !== "GET" && request.method !== "HEAD" && signatureInput !== null && signatureInput.split("=(").length !== signatureInput.split('"content-digest"').length) {
      this.#traffic.inboundRejected();
      return null;
    }
    try {
      const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
      const key = await verifyRequest(request, { documentLoader: ctx.documentLoader, contextLoader: ctx.contextLoader, currentTime: instant(this.#now()) });
      const signer = key !== null && key.id !== null && key.ownerId !== null && key.id.origin === key.ownerId.origin ? key.ownerId : null;
      if (signer !== null) this.#traffic.inboundVerified(signer.hostname, new URL(config.canonical_origin).hostname);
      else if (signed) this.#traffic.inboundRejected();
      return signer === null ? null : signer.href;
    } catch (error) {
      // The key could not be read. The request has no valid signature.
      console.warn(`signature check failed: ${(error as Error).name}`);
      if (signed) this.#traffic.inboundRejected();
      return null;
    }
  }

  // One inbound activity of the knowledge federation (KNOWLEDGE_FEDERATION_DESIGN.md Section 13, unit KF1): the body as it came goes to Claire with the actor of the key. Claire writes it to its inbox journal before it processes it, and gives the same answer for a second delivery.
  // Answers: 401 without a valid signature, 403 for a blocked domain (after the signature check, as for the other inbox activities), 503 when Claire gave no answer (the sender tries again), else 202. The decision of Claire does not change the answer: a refusal is final, so the sender must not try again.
  async #knowledgeInbox(request: Request, config: ServerConfig, path: string, activity: Record<string, unknown>, blocked: boolean, size: number): Promise<Response> {
    const signer = await this.#requestSigner(request, config);
    if (signer === null) return text(401, "Failed to verify the request signature.");
    // The request to Claire has the activity and the two actor addresses. Its body limit is the limit of an inbox body, so an activity near that limit cannot go to Claire, and a second delivery cannot change that.
    if (size > config.limits.inbox_body_bytes - KNOWLEDGE_ENVELOPE_BYTES) return text(413, "Content Too Large");
    try {
      if (blocked || await this.isBlocked(new URL(signer).hostname)) {
        this.counters.blocked += 1;
        return text(403, "Forbidden");
      }
      // The owner of the inbox: an actor path without `/inbox`, or null for the shared inbox.
      const recipient = path === "/ap/inbox" ? null : `${config.canonical_origin}${path.slice(0, -"/inbox".length)}`;
      const result = await this.#api.knowledgeActivity({ activity, signer, verified_by: "http-signature", recipient });
      this.knowledgeCounters[result.decision] += 1;
      if (result.decision !== "processed" && result.decision !== "duplicate") console.warn(`knowledge activity ${result.decision}: ${result.reason}`);
      return new Response(null, { status: 202 });
    } catch (error) {
      console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return text(503, "Service Unavailable", { "retry-after": "5" });
    }
  }

  // A collection of shared knowledge for another server (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.3 and 8.1, KS1). The sidecar verifies the HTTP signature of the request; Claire decides with the actor of the key and gives the ActivityStreams document.
  // A request without a valid signature goes to Claire without a signer and gets the same 404 as each other refusal: a blocked server learns nothing.
  async #knowledgeCollection(request: Request, config: ServerConfig, name: "kb" | "themes"): Promise<Response> {
    const params = new URL(request.url).searchParams;
    // The query of a page: `page=true` and `max_id`. A request with another key has no answer.
    for (const key of params.keys()) if (key !== "page" && key !== "max_id") return notFound();
    const signer = await this.#requestSigner(request, config);
    try {
      const result = await this.#api.knowledgeCollection(name, { signer, page: params.get("page") ?? undefined, maxId: params.get("max_id") ?? undefined });
      if (result === null) return notFound();
      return new Response(request.method === "HEAD" ? null : JSON.stringify(result.document), { status: 200, headers: { "content-type": "application/activity+json", "cache-control": "private, no-store", vary: "Accept, Signature" } });
    } catch (error) {
      // A query that Claire does not accept is a request that has no answer.
      if (error instanceof InternalApiError && error.status === 400) return notFound();
      console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return text(503, "Service Unavailable", { "retry-after": "5" });
    }
  }

  // A knowledge base actor of this server (KNOWLEDGE_FEDERATION_DESIGN.md Section 9.2, unit KF3): the actor document, a collection (followers, outbox, documents), or one document. Claire gives each document, and the sidecar gives it without a change.
  // The actor document needs no signature: another server reads it to verify the signature of an activity. Each other path answers only a request with a valid HTTP signature that Claire accepts (KS1); each refusal is the same 404.
  async #knowledgeBaseActor(request: Request, config: ServerConfig, match: RegExpExecArray): Promise<Response> {
    const [, number, plain, documents, documentId] = match;
    const params = new URL(request.url).searchParams;
    const answer = (document: Record<string, unknown> | null, cache: string): Response => document === null ? notFound() : new Response(request.method === "HEAD" ? null : JSON.stringify(document), { status: 200, headers: { "content-type": "application/activity+json", "cache-control": cache, vary: "Accept, Signature" } });
    try {
      if (plain === undefined && documents === undefined) {
        if (params.size > 0) return notFound();
        return answer(await this.#api.kbActor(number), "public, max-age=180");
      }
      // The query of a page of the documents collection: `page=true` and `max_id`. A request with another key has no answer.
      for (const key of params.keys()) if (documents === undefined || documentId !== undefined || (key !== "page" && key !== "max_id")) return notFound();
      const signer = await this.#requestSigner(request, config);
      const part = { collection: (plain ?? documents) as "followers" | "outbox" | "documents", document: documentId };
      return answer(await this.#api.kbActorPart(number, part, { signer, page: params.get("page") ?? undefined, maxId: params.get("max_id") ?? undefined }), "private, no-store");
    } catch (error) {
      // A query that Claire does not accept is a request that has no answer.
      if (error instanceof InternalApiError && error.status === 400) return notFound();
      console.error(`internal federation API is not available: ${(error as Error).name}: ${(error as Error).message}`);
      return text(503, "Service Unavailable", { "retry-after": "5" });
    }
  }

  // The dependencies of the outbound work of the knowledge federation: the loader that the server actor signs with, and the key of the signer of a task.
  #knowledgeDeps(config: ServerConfig, ctx: Context<void>, documentLoader: KnowledgeDeliveryDeps["documentLoader"]): KnowledgeDeliveryDeps {
    return {
      api: this.#api,
      documentLoader,
      origin: config.canonical_origin,
      isBlocked: (host: string) => this.isBlocked(host),
      now: () => this.#now() * 1000,
      signingKey: async (signer: string) => {
        // The first key pair is the RSA pair, for HTTP Signatures. The key ID is the ID of the key in the actor document.
        if (signer === SERVER_SIGNER) {
          const pairs = await this.#keyPairs(SERVER_ACTOR_ID);
          return pairs.length === 0 ? null : { privateKey: pairs[0].privateKey, keyId: new URL("#main-key", ctx.getActorUri(SERVER_ACTOR_ID)) };
        }
        if (signer.startsWith(ACCOUNT_SIGNER_PREFIX)) {
          // An account actor signs its Offer (unit KF4). Its keys are the ones of the account federation; Claire gives no keys for an account that is not an actor.
          const identifier = signer.slice(ACCOUNT_SIGNER_PREFIX.length);
          if (!PUBLIC_ID.test(identifier)) return null;
          const pairs = await this.#keyPairs(identifier);
          return pairs.length === 0 ? null : { privateKey: pairs[0].privateKey, keyId: new URL("#main-key", ctx.getActorUri(identifier)) };
        }
        if (!PUBLIC_ID.test(signer)) return null;
        const pairs = await this.#kbKeyPairs(signer);
        return pairs.length === 0 ? null : { privateKey: pairs[0].privateKey, keyId: new URL(`${config.canonical_origin}/ap/kb/${signer}#main-key`) };
      },
      sign: (request: Request, privateKey: CryptoKey, keyId: URL) => signRequest(request, privateKey, keyId, { currentTime: instant(this.#now()) }),
      checkAddress: publicAddressCheck(config.allow_private_addresses),
      // The POST of a delivery connects only to the address that it checked (DNS rebinding, KNOWLEDGE_FEDERATION.md Section 14.5). The development switch permits private addresses: then the global fetch is used.
      fetch: config.allow_private_addresses ? undefined : pinnedFetch(),
      onDelivery: (outcome) => {
        if (outcome.result === "delivered") this.#traffic.deliverySucceeded();
        else if (outcome.error === "delivery_timeout") this.#traffic.deliveryFailed("timeout");
        else if (outcome.error === "inbox_unreachable" || outcome.error === "recipient_unavailable") this.#traffic.deliveryFailed("other");
        else if (outcome.error !== null && outcome.error.startsWith("inbox_status_")) this.#traffic.deliveryFailed(Number(outcome.error.slice("inbox_status_".length)) >= 500 ? "http_5xx" : "http_4xx");
      },
    };
  }

  // Takes the outbound activities of the knowledge federation that are ready from Claire and delivers each one (unit KF3, knowledge.ts). Returns the number of tasks that Claire gave.
  async processKnowledgeOutbox(limit = KNOWLEDGE_OUTBOX_BATCH): Promise<number> {
    const config = await this.config();
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    return await processKnowledgeOutbox(this.#knowledgeDeps(config, ctx, await ctx.getDocumentLoader({ identifier: SERVER_ACTOR_ID })), this.knowledgeDeliveryCounters, limit);
  }

  // Takes the reads of documents of other servers for curations from Claire and does them with requests that the server actor signs (unit KF3). Returns the number of reads that Claire gave.
  async processKnowledgeRemoteReads(limit = KNOWLEDGE_READ_BATCH): Promise<number> {
    const config = await this.config();
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    return await processRemoteReads(this.#knowledgeDeps(config, ctx, await ctx.getDocumentLoader({ identifier: SERVER_ACTOR_ID })), this.knowledgeDeliveryCounters, limit);
  }

  // Takes the reads of known servers and of their /kb collections that are due from Claire, and does them with requests that the server actor signs (knowledge.ts). Returns the number of reads that Claire gave.
  async processKnowledgeReads(limit = KNOWLEDGE_READ_BATCH): Promise<number> {
    const config = await this.config();
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    const deps = { api: this.#api, documentLoader: await ctx.getDocumentLoader({ identifier: SERVER_ACTOR_ID }), origin: config.canonical_origin, isBlocked: (host: string) => this.isBlocked(host), now: () => this.#now() * 1000 };
    return await processKnowledgeReads(deps, this.knowledgeReadCounters, limit);
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
      discoverable: record.discoverable,
      manuallyApprovesFollowers: false,
      ...(record.summary ? { summary: `<p>${escapeSummaryHtml(record.summary)}</p>` } : {}),
    });
  }

  #article(ctx: Context<void>, record: ObjectRecord): Article {
    return new Article({
      id: ctx.getObjectUri(ASObject, { id: record.public_id }),
      attribution: ctx.getActorUri(record.actor_public_id),
      name: record.name,
      content: record.content,
      url: record.url === null ? null : new URL(record.url),
      published: instant(record.published_at),
      updated: instant(record.updated_at),
      to: PUBLIC_COLLECTION,
      cc: ctx.getFollowersUri(record.actor_public_id),
      // The quote policy of the document (unit FP4-9Q).
      interactionPolicy: quoteInteractionPolicy(ctx, record.actor_public_id, record.quote_policy),
    });
  }

  // Takes the ready outbox tasks from Claire and gives each one to Fedify as an activity (design Section 9.3). Returns the number of tasks that Claire gave.
  // An error in one task does not stop the other tasks. A task without an acknowledgement comes again after its lease.
  async processOutboxTasks(limit = OUTBOX_TASK_BATCH): Promise<number> {
    const config = await this.config();
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
        // The account is not an actor now (it is not active, or it is purged). Such an account sends nothing (design Section 8.1).
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
    const objectUri = ctx.getObjectUri(ASObject, { id: task.object_public_id });
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

  // Takes the ready tasks of the outbound interactions from Claire (contract 1.6, unit FP4-9) and gives each one to Fedify (notes.ts). Returns the number of tasks that Claire gave.
  async processInteractionTasks(limit = OUTBOX_TASK_BATCH): Promise<number> {
    const config = await this.config();
    const tasks = await this.#api.interactionTasks(limit);
    if (tasks.length === 0) return 0;
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    const deps = { api: this.#api, isBlocked: (host: string) => this.isBlocked(host), deliveryRecipients: (identifier: string) => this.deliveryRecipients(identifier) };
    for (const task of tasks) {
      try {
        this.outboxCounters[await handOffInteraction(deps, ctx, task)] += 1;
      } catch (error) {
        this.outboxCounters.failed += 1;
        console.error(`interaction task ${task.id} (${task.kind}) failed: ${(error as Error).name}: ${(error as Error).message}`);
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

  // Takes the ready thread fetch tasks from Claire (contract 1.5, unit FP4-6) and gives the fetched objects to Claire. Returns the number of tasks that Claire gave. The work is in context.ts.
  async processContextTasks(limit = CONTEXT_TASK_BATCH): Promise<number> {
    const config = await this.config();
    const ctx = this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
    return await processContextTasks({ api: this.#api, ctx, origin: config.canonical_origin, isBlocked: (host) => this.isBlocked(host), counters: this.outboxCounters }, limit);
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
    // Fedify verified the activity before this listener.
    this.#traffic.inboundVerified(activity.actorId.hostname, new URL((await this.config()).canonical_origin).hostname);
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
    const recipient = ctx.recipient !== null && PUBLIC_ID.test(ctx.recipient) ? ctx.recipient : null;
    const remote = { iri: actor.id.href, inbox: actor.inboxId.href, shared_inbox: actor.endpoints?.sharedInbox?.href ?? null };
    // Fedify verified the activity with one of the mechanisms that the activity has. The value names the strongest one that is present.
    const verifiedBy = "proof" in document ? "object-integrity-proof" : "signature" in document ? "ld-signature" : "http-signature";
    // Quotes (unit FP4-9Q): a QuoteRequest for an object of this server, and the answer to the quote request of an own note. Claire applies the quote policy and checks the authorization.
    const quote = await quoteEventOf(activity, (await this.config()).canonical_origin, { event_id: activity.id.href, recipient_public_id: recipient, actor: remote, verified_by: verifiedBy }, { documentLoader: ctx.documentLoader, contextLoader: ctx.contextLoader });
    if (quote !== null) {
      const result = await this.#api.quoteEvent(quote);
      this.counters[result.decision] += 1;
      if (result.decision !== "accepted") console.warn(`inbox activity ${type} ${result.decision}: ${result.reason}`);
      return;
    }
    const timeline = await this.#timelineEvent(ctx, activity, actor, objectType, objectIri, { event_id: activity.id.href, recipient_public_id: recipient, actor: remote, verified_by: verifiedBy });
    if (timeline === "dropped") return;
    if (timeline !== null) {
      const result = await this.#api.timelineEvent(timeline);
      this.counters[result.decision] += 1;
      if (result.decision !== "accepted") console.warn(`inbox activity ${type} ${result.decision}: ${result.reason}`);
      for (const notice of result.notices) await this.#sendNotice(ctx, actor, timeline.object_iri, notice);
      return;
    }
    const event: InboxEvent = { event_id: activity.id.href, type, object_type: objectType, recipient_public_id: recipient, actor: remote, object_iri: objectIri, verified_by: verifiedBy };
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

  // Sends one automatic notice for a non-public message (design Section 6.2 rule 4, unit FP4-8): a Note of the account to the sender only, with inReplyTo = the object and a Mention of the sender. The Note is not public and not in the outbox collection.
  // The ID has a digest of the object ID: a second run of the same inbox activity makes the same Note. Claire finds a reply to a notice by the fragment #auto-replies/ and sends no notice for it.
  async #sendNotice(ctx: Context<void>, sender: Actor, objectIri: string, notice: TimelineNotice): Promise<void> {
    if (sender.id === null || !PUBLIC_ID.test(notice.actor_public_id) || (await ctx.getActorKeyPairs(notice.actor_public_id)).length === 0) return;
    const actorUri = ctx.getActorUri(notice.actor_public_id);
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(objectIri)));
    const noteId = new URL(`#auto-replies/${[...digest.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("")}`, actorUri);
    const published = instant(this.#now());
    const note = new Note({
      id: noteId,
      attribution: actorUri,
      to: sender.id,
      replyTarget: new URL(objectIri),
      contents: [notice.content, new LanguageString(notice.content, notice.language)],
      tags: [new Mention({ href: sender.id, name: actorProfile(sender)?.handle ?? sender.id.href })],
      published,
    });
    await ctx.sendActivity({ identifier: notice.actor_public_id }, sender, new Create({ id: new URL(`${noteId.hash}/create`, actorUri), actor: actorUri, to: sender.id, object: note, published }));
  }

  // The timeline event of an activity (unit FP4-4), null when the activity is not for the timelines (then it goes to Claire as an inbox event), or "dropped" when the object cannot have a normal form.
  // Create, Update, and Announce of a post; Delete of an object that is not the actor; Undo(Announce).
  async #timelineEvent(ctx: InboxContext<void>, activity: Activity, actor: Actor, undoneType: string | null, objectIri: string | null, base: Pick<TimelineEvent, "event_id" | "recipient_public_id" | "actor" | "verified_by">): Promise<TimelineEvent | "dropped" | null> {
    if (objectIri === null || objectIri === base.actor.iri) return null;
    const origin = (await this.config()).canonical_origin;
    // An Announce of an object of this server and its Undo are reactions (unit FP4-8): they stay inbox events, and Claire counts them.
    const ownObject = objectIri.startsWith(`${origin}/`);
    const remoteType = actorType(actor);
    const without = (type: TimelineEvent["type"], isPublicObject: boolean | null): TimelineEvent => ({ event_id: base.event_id, type, recipient_public_id: base.recipient_public_id, actor: base.actor, announced_by: null, object_iri: objectIri, public: isPublicObject, object: null, parents: [], actor_type: remoteType, mentioned: [], in_reply_to: null, verified_by: base.verified_by });
    if (activity instanceof Undo) return undoneType === "Announce" && !ownObject ? without("Undo", null) : null;
    if (activity instanceof Delete) return without("Delete", null);
    const type = activity instanceof Create ? "Create" : activity instanceof Update ? "Update" : activity instanceof Announce ? "Announce" : null;
    if (type === null || (type === "Announce" && ownObject)) return null;
    const drop = (why: string): "dropped" => {
      this.counters.dropped += 1;
      console.warn(`inbox activity ${type} dropped: ${why}`);
      return "dropped";
    };
    // An Update changes only an item that exists, and its object is in the activity. A Create or an Announce can need fetches: Claire says first if an account follows the actor (RS2, RS4).
    const interest = type === "Update" ? null : await this.#api.timelineInterest(base.actor.iri);
    const followed = interest === null || interest.followed;
    // An actor that no account follows gets no fetch. Its Create can still be for an account (a mention or a reply to an own object, Section 6.6 rule 1): only the object in the activity is read.
    if (!followed && type !== "Create") return null;
    const load: LoadOptions & { suppressError: true } = { documentLoader: followed ? ctx.documentLoader : noFetch, contextLoader: ctx.contextLoader, suppressError: true };
    const object = await activity.getObject(load);
    if (!isPost(object) || object.id === null) return null;
    const mentioned = type === "Create" ? await localMentions(object, origin, load) : [];
    const inReplyTo = object.replyTargetId?.href ?? null;
    if (!followed && mentioned.length === 0 && !(inReplyTo !== null && inReplyTo.startsWith(`${origin}/`))) return null;
    if (await this.isBlocked(object.id.hostname)) {
      this.counters.blocked += 1;
      return "dropped";
    }
    if (!isPublic(object)) return { ...without(type, false), object_iri: object.id.href, mentioned, in_reply_to: inReplyTo };
    const selfProfile = actorProfile(actor);
    if (selfProfile === null) return drop("the actor has no ID");
    let author = selfProfile;
    if (type === "Announce") {
      if (object.attributionId === null) return drop("the announced object has no author");
      if (await this.isBlocked(object.attributionId.hostname)) {
        this.counters.blocked += 1;
        return "dropped";
      }
      author = await this.#authorProfile(object, load);
    } else if (object.attributionId?.href !== base.actor.iri) {
      // Only the author makes or changes an object.
      return drop("the object has another author");
    }
    const normal = await normalObject(object, author, load);
    if (typeof normal === "string") return drop(normal);
    const parents = interest !== null && interest.followed && interest.context_fetch === "on" ? await this.#parents(object, interest.context_depth, load) : [];
    return { event_id: base.event_id, type, recipient_public_id: base.recipient_public_id, actor: base.actor, announced_by: type === "Announce" ? selfProfile : null, object_iri: normal.object_iri, public: true, object: normal, parents, actor_type: remoteType, mentioned, in_reply_to: inReplyTo, verified_by: base.verified_by };
  }

  // The profile of the author of an object. When the actor document is not available, the profile has the ID only.
  async #authorProfile(object: Post, load: LoadOptions & { suppressError: true }): Promise<{ iri: string; name: string | null; handle: string | null }> {
    const fetched = await object.getAttribution(load);
    const profile = fetched !== null && fetched.id?.href === object.attributionId?.href ? actorProfile(fetched) : null;
    return profile ?? { iri: object.attributionId!.href, name: null, handle: null };
  }

  // The reply chain of a post, nearest parent first, `depth` levels at most (design Section 6.5 rule 2). The chain stops at a parent that is not a public post, has no author, is on this server or on a blocked domain, or cannot be fetched.
  async #parents(post: Post, depth: number, load: LoadOptions & { suppressError: true }): Promise<TimelineObject[]> {
    const origin = (await this.config()).canonical_origin;
    const parents: TimelineObject[] = [];
    let current = post;
    while (parents.length < depth) {
      const target = current.replyTargetId;
      if (target === null || target.origin === origin || await this.isBlocked(target.hostname)) break;
      const parent = await current.getReplyTarget(load);
      if (!isPost(parent) || parent.id?.href !== target.href || parent.attributionId === null || !isPublic(parent) || await this.isBlocked(parent.attributionId.hostname)) break;
      const normal = await normalObject(parent, await this.#authorProfile(parent, load), load);
      if (typeof normal === "string") break;
      parents.push(normal);
      current = parent;
    }
    return parents;
  }

  // A Fedify context for code that has no HTTP request, for example a signed fetch with the key of the server actor.
  async createContext(): Promise<Context<void>> {
    const config = await this.config();
    return this.#federationFor(config).createContext(new URL(config.canonical_origin), undefined);
  }

  #federationFor(config: ServerConfig): Federation<void> {
    if (this.#federation === null || this.#federation.origin !== config.canonical_origin || this.#federation.allowPrivate !== config.allow_private_addresses) {
      this.#federation = { origin: config.canonical_origin, allowPrivate: config.allow_private_addresses, value: this.#build(config.canonical_origin, config.allow_private_addresses) };
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

  // The key pairs of a knowledge base actor of this server (unit KF3), the RSA pair first. The cache is the cache of the account keys; the entry has the prefix `kb/`, which no actor identifier has.
  async #kbKeyPairs(number: string): Promise<CryptoKeyPair[]> {
    const now = this.#now();
    const entry = `kb/${number}`;
    const cached = this.#keys.get(entry);
    if (cached !== undefined && cached.expires > now) return cached.pairs;
    this.#keys.delete(entry);
    const keys = await this.#api.kbActorKeys(number);
    if (keys === null) return [];
    const pairs: CryptoKeyPair[] = [];
    for (const alg of ["rsa2048", "ed25519"]) {
      const key = keys.keys.find((k) => k.alg === alg);
      if (key === undefined) continue;
      pairs.push({ privateKey: await importJwk(key.private_jwk as JsonWebKey, "private"), publicKey: await importJwk(key.public_jwk as JsonWebKey, "public") });
    }
    this.#keys.set(entry, { pairs, expires: now + KEY_CACHE_SECONDS });
    return pairs;
  }

  #build(origin: string, allowPrivateAddress: boolean): Federation<void> {
    // allowPrivateAddress is false in production: Fedify then refuses each document address that is not public. The development switch of Claire (contract 1.8) permits a test server at a private address.
    // The results of the deliveries go to the traffic counters (GF20): the delivery counter of Fedify gives each success, and the error handler gives the class of each failure.
    const meterProvider = deliveryMeterProvider((success) => {
      if (success) this.#traffic.deliverySucceeded();
    }) as NonNullable<Parameters<typeof createFederation<void>>[0]["meterProvider"]>;
    const federation = createFederation<void>({ kv: this.#kv, queue: this.#queue, origin, allowPrivateAddress, meterProvider, onOutboxError: (error) => this.#traffic.deliveryFailed(deliveryFailureClass(error)) });
    const inbox = (identifier: string) => new URL(`/ap/actors/${identifier}/inbox`, origin);
    const sharedInbox = new URL("/ap/inbox", origin);

    federation
      .setActorDispatcher("/ap/actors/{identifier}", async (ctx, identifier) => {
        if (identifier === SERVER_ACTOR_ID) {
          const config = await this.config();
          const keys = await ctx.getActorKeyPairs(identifier);
          return new Application({
            id: ctx.getActorUri(identifier),
            preferredUsername: SERVER_ACTOR_USERNAME,
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
        if (username.toLowerCase() === SERVER_ACTOR_USERNAME) return SERVER_ACTOR_ID;
        return (await this.#api.actorByUsername(username))?.public_id ?? null;
      })
      .setKeyPairsDispatcher((_ctx, identifier) => this.#keyPairs(identifier));

    federation
      .setInboxListeners("/ap/actors/{identifier}/inbox", "/ap/inbox")
      // One listener for all types: Claire decides which types apply (the v1 set) and which are ignored.
      .on(Activity, (ctx, activity) => this.#onActivity(ctx, activity))
      // Fetches of remote documents for the shared inbox are signed with the key of the server actor.
      .setSharedKeyDispatcher(() => ({ identifier: SERVER_ACTOR_ID }))
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

    // One dispatcher for the published documents (Article) and the own notes (Note): the two have one number space and one path (RT7).
    federation.setObjectDispatcher(ASObject, "/ap/objects/{id}", async (ctx, values) => {
      if (!PUBLIC_ID.test(values.id)) return null;
      const result = await this.#api.object(values.id);
      if (result === null) {
        const note = await this.#api.note(values.id);
        if (note === null) return null;
        if (note.state === "gone") {
          this.#gone.set(`/ap/objects/${values.id}`, note.gone_at);
          this.#goneNotes.add(`/ap/objects/${values.id}`);
          return null;
        }
        return buildNote(ctx, note.record, knownMentions(note.record));
      }
      if (result.state === "gone") {
        this.#gone.set(`/ap/objects/${values.id}`, result.gone_at);
        return null;
      }
      return this.#article(ctx, result.record);
    });

    // The authorization of an approved quote request (FEP-044f, unit FP4-9Q). It does not embed the quoting object. A revoked authorization gets 410 (the wrapper in #respond, as for /ap/objects/).
    federation.setObjectDispatcher(QuoteAuthorization, "/ap/quote-authorizations/{id}", async (ctx, values) => {
      if (!PUBLIC_ID.test(values.id)) return null;
      const result = await this.#api.quoteAuthorization(values.id);
      if (result === null) return null;
      if (result.state === "gone") {
        this.#gone.set(`/ap/quote-authorizations/${values.id}`, result.gone_at);
        return null;
      }
      return buildQuoteAuthorization(ctx, result.record);
    });

    federation
      .setOutboxDispatcher("/ap/actors/{identifier}/outbox", async (ctx, identifier, cursor) => {
        // The cursor is the empty string for the first page, or the ID of the last object of the page before.
        if (!PUBLIC_ID.test(identifier) || (cursor !== null && cursor !== "" && !PUBLIC_ID.test(cursor))) return null;
        const page = await this.#api.actorOutbox(identifier, { limit: OUTBOX_PAGE_SIZE, maxId: cursor || undefined });
        if (page === null) return null;
        const audience = { actor: ctx.getActorUri(identifier), to: PUBLIC_COLLECTION, cc: ctx.getFollowersUri(identifier) };
        const items = page.items.map((item) => {
          const objectUri = ctx.getObjectUri(ASObject, { id: item.id });
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
