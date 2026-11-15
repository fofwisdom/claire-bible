// Client for the Claire internal federation API (design Sections 6.1 and 6.2).
//
// Each request has a timestamp header and a signature header. The signature is HMAC-SHA256 with the service key over:
//   {METHOD}\n{request target}\n{timestamp}\n{SHA-256 of the body, lowercase hex}
// The Python side is src/claire/api/federation_internal.py. The contract fixtures are in tests/fixtures/federation_contract/.
//
// Each response is checked against a sealed shape: an absent field, an extra field, or a wrong type is an error.

export const CONTRACT_VERSION = "1.7";
export const INTERNAL_PREFIX = "/internal/federation/v1";
export const TIMESTAMP_HEADER = "x-claire-timestamp";
export const SIGNATURE_HEADER = "x-claire-signature";
export const INSTANCE_ACTOR_ID = "instance";

// The host name is a placeholder. The transport sends the request to the Unix socket.
const BASE_URL = "http://claire.internal";

export type Transport = (request: Request) => Promise<Response>;

export interface InstanceConfig {
  contract_version: string;
  canonical_origin: string;
  handle_host: string;
  mode: "single" | "network";
  federation: "off" | "on";
  software: { name: string; version: string };
  trusted_proxies: string[];
  federated_actors: number;
  limits: { inbox_body_bytes: number };
  // Entity tag of the domain-blocks response. Read the list again when this value changes.
  domain_blocks_etag: string;
}

export interface ActorRecord {
  public_id: string;
  username: string;
  display_name: string;
  activated_at: number | null;
  updated_at: number;
  profile_url: string;
}

export interface ActorKey {
  alg: "rsa2048" | "ed25519";
  public_jwk: Record<string, string>;
  private_jwk: Record<string, string>;
}

export interface ActorKeys {
  public_id: string;
  keys: ActorKey[];
}

export interface Heartbeat {
  contract_version: string;
  sidecar_version: string;
  queue_depth: number;
  self_check: { ok: boolean | null; checked_at: number | null };
}

export interface HeartbeatReply {
  contract_version: string;
  federation: "off" | "on";
}

// One inbound activity that Fedify verified (design Section 6.3).
// type is the type of the activity. object_iri is the ID of its object. For an Undo, object_type is the type of the activity that is undone and object_iri is the object of that activity.
// recipient_public_id is the actor of the inbox that got the activity, or null for the shared inbox.
export interface InboxEvent {
  event_id: string;
  type: string;
  object_type: string | null;
  recipient_public_id: string | null;
  actor: { iri: string; inbox: string; shared_inbox: string | null };
  object_iri: string | null;
  verified_by: "http-signature" | "ld-signature" | "object-integrity-proof";
}

// reply "accept": send Accept(Follow). "rejected": send Reject for a Follow and drop other types. "ignored": drop the activity and count it.
export interface InboxDecision {
  decision: "accepted" | "rejected" | "ignored";
  reply: "accept" | null;
  reason: "federation_off" | "invalid_actor" | "blocked_domain" | "not_an_actor" | "recipient_mismatch" | "unsupported_type" | "no_object" | null;
}

export interface FollowerItem {
  id: string;
  actor_iri: string;
  inbox_iri: string;
  shared_inbox_iri: string | null;
}

// items are newest first. next_max_id is the cursor of the next page, or null when this page is not full.
export interface FollowersPage {
  public_id: string;
  total: number;
  items: FollowerItem[];
  next_max_id: string | null;
}

// domains are lowercase ASCII. A block of a domain also applies to its subdomains.
export interface DomainBlocks {
  etag: string;
  domains: string[];
}

// One outbound task with a lease (design Section 9.3). actor_public_id is the actor that sends the activity.
export interface OutboxTask {
  id: number;
  kind: "create" | "update" | "delete" | "update_actor" | "delete_actor";
  actor_public_id: string;
  object_public_id: string | null;
  created_at: number;
  lease_until: number;
}

// One task of the client outbox (contract 1.3, unit FP4-3). follow: target is the handle or IRI that the owner gave, following_id is the number of the follow.
// undo_follow: target is the actor IRI. reject_follow: target is the actor IRI of a removed follower.
export interface ClientTask {
  id: number;
  kind: "follow" | "undo_follow" | "reject_follow";
  actor_public_id: string;
  target: string;
  following_id: string | null;
  created_at: number;
  lease_until: number;
}

// The actor of a follow task that the sidecar resolved.
export interface ResolvedActor {
  iri: string;
  type: "Person" | "Group" | "Service" | "Application" | "Organization";
  inbox: string;
  shared_inbox: string | null;
  name: string | null;
}

// The author or the announcer of a remote object. handle is @name@host from the actor document.
export interface TimelineActor {
  iri: string;
  name: string | null;
  handle: string | null;
}

// The normal form of one remote object (FEDERATION_REMOTE_READING_DESIGN.md Section 6.3, unit FP4-4). The times are Unix seconds. content_html is the raw HTML of the remote server.
export interface TimelineObject {
  object_iri: string;
  url: string | null;
  type: "Note" | "Article" | "Page" | "Question";
  author: TimelineActor;
  name: string | null;
  summary: string | null;
  sensitive: boolean;
  content_html: string;
  language: string | null;
  published_at: number | null;
  updated_at: number | null;
  attachments: { url: string; media_type: string | null; name: string | null; width: number | null; height: number | null; blurhash: string | null }[];
  context: string | null;
  in_reply_to: string | null;
  // Contract 1.7 (unit FP4-9Q): the object that this object quotes (FEP-044f quote, else quoteUrl with its other keys), or null.
  quote: string | null;
}

// One inbound activity for the timelines of the accounts (unit FP4-4). Undo is an Undo(Announce): object_iri is the object of the Announce.
// public is false for an object that is not public; then object is null. public is null for Delete and Undo. parents is the reply chain of object, nearest parent first.
// Contract 1.4 (unit FP4-8): actor_type is the type of the actor (null for a type outside the list). mentioned has the actors of this instance that the object of a Create mentions (a Mention tag) or addresses (to, cc), MAX_MENTIONED at most. in_reply_to is the reply target of the object, also when object is null.
export interface TimelineEvent {
  event_id: string;
  type: "Create" | "Update" | "Delete" | "Announce" | "Undo";
  recipient_public_id: string | null;
  actor: { iri: string; inbox: string; shared_inbox: string | null };
  announced_by: TimelineActor | null;
  object_iri: string;
  public: boolean | null;
  object: TimelineObject | null;
  parents: TimelineObject[];
  actor_type: "Person" | "Group" | "Service" | "Application" | "Organization" | null;
  mentioned: string[];
  in_reply_to: string | null;
  verified_by: "http-signature" | "ld-signature" | "object-integrity-proof";
}

// One automatic notice for a non-public message (unit FP4-8): the sidecar sends a Note of the actor actor_public_id to the sender of the event only. content is HTML that Claire made; language is the locale of the account.
export interface TimelineNotice {
  actor_public_id: string;
  content: string;
  language: "en" | "ko";
}

// items is the number of items that the event made, changed, or deleted. notices are the automatic notices to send now.
export interface TimelineDecision {
  decision: "accepted" | "rejected" | "ignored";
  reason: "federation_off" | "invalid_actor" | "blocked_domain" | "invalid_object" | "too_large" | "not_public" | "not_followed" | "no_item" | "rate_limited" | null;
  items: number;
  notices: TimelineNotice[];
}

// followed: an account that is an actor follows the remote actor. The sidecar fetches the object of an Announce and the parents of a reply only then.
export interface TimelineInterest {
  followed: boolean;
  context_fetch: "on" | "off";
  context_depth: number;
}

// One thread fetch task (contract 1.5, unit FP4-6): the sidecar reads the thread of object_iri with requests that the actor actor_public_id signs. max_items and max_depth are the limits of the task.
export interface ContextTask {
  id: number;
  actor_public_id: string;
  object_iri: string;
  max_items: number;
  max_depth: number;
  created_at: number;
  lease_until: number;
}

// The answer to the objects of a thread fetch. stored is the number of new items. refused is the number of objects that Claire did not take.
export interface ContextObjectsResult {
  decision: "accepted" | "rejected";
  reason: "federation_off" | "not_an_actor" | "context_fetch_off" | null;
  stored: number;
  refused: number;
}

// The canonical form of one published object. content is HTML that Claire sanitized.
export interface ObjectRecord {
  public_id: string;
  actor_public_id: string;
  type: "Article";
  name: string;
  content: string;
  url: string | null;
  published_at: number;
  updated_at: number;
  // Contract 1.7 (unit FP4-9Q): the quote policy of the document. canQuote of the interaction policy comes from it (RT9).
  quote_policy: QuotePolicy;
}

// The quote policy of an own object (FEDERATION_REMOTE_READING_DESIGN.md Section 5.4 rule 6).
export type QuotePolicy = "public" | "followers" | "manual" | "nobody";

// The result of an object read: the record, the time of removal of a deleted object (410), or null (404).
export type ObjectResult = { state: "found"; record: ObjectRecord } | { state: "gone"; gone_at: number } | null;

// The canonical form of one own note (contract 1.6, unit FP4-9). content is HTML that Claire made from plain text and sanitized.
// A mention has the handle of the text (or null for the author of the parent of a reply without a handle) and the actor IRI (or null until the sidecar resolves the handle).
export interface NoteRecord {
  public_id: string;
  actor_public_id: string;
  type: "Note";
  content: string;
  summary: string | null;
  sensitive: boolean;
  language: string | null;
  in_reply_to: string | null;
  audience: string | null;
  mentions: { handle: string | null; iri: string | null }[];
  tags: { name: string; href: string }[];
  published_at: number;
  updated_at: number;
  edited_at: number | null;
  // Contract 1.7 (unit FP4-9Q). quote: the quoted object while the quote waits or is approved, else null. quote_authorization: the QuoteAuthorization of an approved quote.
  quote_policy: QuotePolicy;
  quote: string | null;
  quote_authorization: string | null;
}

// The result of a note read: the record, the time of removal of a deleted note (410), or null (404).
export type NoteResult = { state: "found"; record: NoteRecord } | { state: "gone"; gone_at: number } | null;

// One task of the outbound interactions (contract 1.6, unit FP4-9).
// note, update_note: ref_id is the number of the note. delete_note: ref_id is the number of the deleted note, recipients are the actors that got the note in addition to the followers.
// like, announce: object_iri is the remote object, recipients has its author, ref_id is the number in the activity ID. undo_like, undo_announce: ref_id is the number of the activity that is undone.
// Quotes (contract 1.7, unit FP4-9Q). quote_request: ref_id is the number of the quoting note, object_iri is the quoted object (null: send the Create only), recipients has its author. quote_authorize, quote_reject, quote_revoke: ref_id is the number of the authorization, object_iri is the own object that is quoted, recipients has the quoting actor, and quote has the request and the quoting object.
export interface InteractionTask {
  id: number;
  kind: "note" | "update_note" | "delete_note" | "like" | "undo_like" | "announce" | "undo_announce" | "quote_request" | "quote_authorize" | "quote_reject" | "quote_revoke";
  actor_public_id: string;
  ref_id: string;
  object_iri: string | null;
  recipients: string[];
  quote: { request_iri: string; quoting_object_iri: string } | null;
  created_at: number;
  lease_until: number;
}

// One verified inbound activity of the quote protocol (contract 1.7, unit FP4-9Q).
// QuoteRequest: object_iri is the quoted object of this instance, instrument_iri is the quoting object. Accept, Reject: object_iri is the ID of the quote request (objectId of the activity, RT9), and authorization is the QuoteAuthorization in result of an Accept.
export interface QuoteEvent {
  event_id: string;
  type: "QuoteRequest" | "Accept" | "Reject";
  recipient_public_id: string | null;
  actor: { iri: string; inbox: string; shared_inbox: string | null };
  object_iri: string;
  instrument_iri: string | null;
  authorization: { iri: string; interacting_object: string | null; interaction_target: string | null; attributed_to: string | null } | null;
  verified_by: "http-signature" | "ld-signature" | "object-integrity-proof";
}

export interface QuoteDecision {
  decision: "accepted" | "rejected" | "ignored";
  reason: "federation_off" | "invalid_actor" | "blocked_domain" | "no_object" | "invalid_object" | "recipient_mismatch" | "rate_limited" | "quote_refused" | "no_quote" | "not_the_author" | "invalid_authorization" | "unsupported_type" | null;
  state: "pending" | "approved" | "refused" | "revoked" | null;
}

// The canonical form of one approved quote request (contract 1.7). The QuoteAuthorization of the public path comes from it.
export interface QuoteAuthorizationRecord {
  id: string;
  actor_public_id: string;
  object_public_id: string;
  quoting_object_iri: string;
}

// The result of a read of an authorization: the record, the time of the revoke (410), or null (404).
export type QuoteAuthorizationResult = { state: "found"; record: QuoteAuthorizationRecord } | { state: "gone"; gone_at: number } | null;

// A handle of a note that the sidecar resolved to an actor.
export interface ResolvedMention {
  handle: string;
  iri: string;
}

// items are newest first.
export interface ActorOutboxPage {
  public_id: string;
  total: number;
  items: { id: string; published_at: number; updated_at: number }[];
  next_max_id: string | null;
}

// The result of an actor read: the record, the time of removal of a purged actor (410), or null (404).
export type ActorResult = { state: "found"; record: ActorRecord } | { state: "gone"; gone_at: number } | null;

export class InternalApiError extends Error {
  // The parsed body of the error answer, when it is JSON.
  detail: unknown = undefined;

  constructor(readonly status: number, readonly code: string) {
    super(`internal federation API error: ${status} ${code}`);
    this.name = "InternalApiError";
  }
}

export class ContractError extends Error {
  constructor(message: string) {
    super(`internal federation API contract error: ${message}`);
    this.name = "ContractError";
  }
}

function hex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function signRequest(key: Uint8Array<ArrayBuffer>, method: string, target: string, timestamp: string, body: Uint8Array<ArrayBuffer>): Promise<string> {
  const bodyHash = hex(await crypto.subtle.digest("SHA-256", body));
  const message = new TextEncoder().encode([method.toUpperCase(), target, timestamp, bodyHash].join("\n"));
  const hmacKey = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return hex(await crypto.subtle.sign("HMAC", hmacKey, message));
}

// --- Sealed shapes ---

type Check = (value: unknown, path: string) => void;

function fail(path: string, expected: string): never {
  throw new ContractError(`${path} is not ${expected}`);
}

const str: Check = (v, p) => {
  if (typeof v !== "string") fail(p, "a string");
};
const num: Check = (v, p) => {
  if (typeof v !== "number" || !Number.isFinite(v)) fail(p, "a number");
};
const int: Check = (v, p) => {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 0) fail(p, "an integer that is 0 or more");
};
const bool: Check = (v, p) => {
  if (typeof v !== "boolean") fail(p, "a boolean");
};
const nullable = (check: Check): Check => (v, p) => {
  if (v !== null) check(v, p);
};
const oneOf = (...values: string[]): Check => (v, p) => {
  if (typeof v !== "string" || !values.includes(v)) fail(p, `one of ${values.join(", ")}`);
};
const list = (check: Check): Check => (v, p) => {
  if (!Array.isArray(v)) fail(p, "a list");
  (v as unknown[]).forEach((item, i) => check(item, `${p}[${i}]`));
};
const stringMap: Check = (v, p) => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(p, "an object");
  for (const [key, value] of Object.entries(v as object)) str(value, `${p}.${key}`);
};
const sealed = (fields: Record<string, Check>): Check => (v, p) => {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(p, "an object");
  const object = v as Record<string, unknown>;
  for (const key of Object.keys(object)) {
    if (!(key in fields)) throw new ContractError(`${p}.${key} is not in the contract`);
  }
  for (const [key, check] of Object.entries(fields)) {
    if (!(key in object)) throw new ContractError(`${p}.${key} is absent`);
    check(object[key], `${p}.${key}`);
  }
};

const configShape = sealed({
  contract_version: str,
  canonical_origin: str,
  handle_host: str,
  mode: oneOf("single", "network"),
  federation: oneOf("off", "on"),
  software: sealed({ name: str, version: str }),
  trusted_proxies: list(str),
  federated_actors: int,
  limits: sealed({ inbox_body_bytes: int }),
  domain_blocks_etag: str,
});
const actorShape = sealed({ public_id: str, username: str, display_name: str, activated_at: nullable(num), updated_at: num, profile_url: str });
const keysShape = sealed({ public_id: str, keys: list(sealed({ alg: oneOf("rsa2048", "ed25519"), public_jwk: stringMap, private_jwk: stringMap })) });
const decisionShape = sealed({
  decision: oneOf("accepted", "rejected", "ignored"),
  reply: nullable(oneOf("accept")),
  reason: nullable(oneOf("federation_off", "invalid_actor", "blocked_domain", "not_an_actor", "recipient_mismatch", "unsupported_type", "no_object")),
});
const followersShape = sealed({ public_id: str, total: int, items: list(sealed({ id: str, actor_iri: str, inbox_iri: str, shared_inbox_iri: nullable(str) })), next_max_id: nullable(str) });
const domainBlocksShape = sealed({ etag: str, domains: list(str) });
const outboxTasksShape = sealed({
  tasks: list(sealed({ id: int, kind: oneOf("create", "update", "delete", "update_actor", "delete_actor"), actor_public_id: str, object_public_id: nullable(str), created_at: num, lease_until: num })),
});
const ackShape = sealed({ id: int, status: oneOf("pending", "handed_off", "rejected") });
const quotePolicy = oneOf("public", "followers", "manual", "nobody");
const objectShape = sealed({ public_id: str, actor_public_id: str, type: oneOf("Article"), name: str, content: str, url: nullable(str), published_at: num, updated_at: num, quote_policy: quotePolicy });
const goneShape = sealed({ error: oneOf("gone"), gone_at: num });
const actorOutboxShape = sealed({ public_id: str, total: int, items: list(sealed({ id: str, published_at: num, updated_at: num })), next_max_id: nullable(str) });
const clientTasksShape = sealed({
  tasks: list(sealed({ id: int, kind: oneOf("follow", "undo_follow", "reject_follow"), actor_public_id: str, target: str, following_id: nullable(str), created_at: num, lease_until: num })),
});
const noteShape = sealed({
  public_id: str,
  actor_public_id: str,
  type: oneOf("Note"),
  content: str,
  summary: nullable(str),
  sensitive: bool,
  language: nullable(str),
  in_reply_to: nullable(str),
  audience: nullable(str),
  mentions: list(sealed({ handle: nullable(str), iri: nullable(str) })),
  tags: list(sealed({ name: str, href: str })),
  published_at: num,
  updated_at: num,
  edited_at: nullable(num),
  quote_policy: quotePolicy,
  quote: nullable(str),
  quote_authorization: nullable(str),
});
const interactionTasksShape = sealed({
  tasks: list(sealed({
    id: int,
    kind: oneOf("note", "update_note", "delete_note", "like", "undo_like", "announce", "undo_announce", "quote_request", "quote_authorize", "quote_reject", "quote_revoke"),
    actor_public_id: str,
    ref_id: str,
    object_iri: nullable(str),
    recipients: list(str),
    quote: nullable(sealed({ request_iri: str, quoting_object_iri: str })),
    created_at: num,
    lease_until: num,
  })),
});
const quoteDecisionShape = sealed({
  decision: oneOf("accepted", "rejected", "ignored"),
  reason: nullable(oneOf("federation_off", "invalid_actor", "blocked_domain", "no_object", "invalid_object", "recipient_mismatch", "rate_limited", "quote_refused", "no_quote", "not_the_author", "invalid_authorization", "unsupported_type")),
  state: nullable(oneOf("pending", "approved", "refused", "revoked")),
});
const quoteAuthorizationShape = sealed({ id: str, actor_public_id: str, object_public_id: str, quoting_object_iri: str });
const followingShape = sealed({ public_id: str, total: int });
const timelineDecisionShape = sealed({
  decision: oneOf("accepted", "rejected", "ignored"),
  reason: nullable(oneOf("federation_off", "invalid_actor", "blocked_domain", "invalid_object", "too_large", "not_public", "not_followed", "no_item", "rate_limited")),
  items: int,
  notices: list(sealed({ actor_public_id: str, content: str, language: oneOf("en", "ko") })),
});
const timelineInterestShape = sealed({ followed: bool, context_fetch: oneOf("on", "off"), context_depth: int });
const contextTasksShape = sealed({ tasks: list(sealed({ id: int, actor_public_id: str, object_iri: str, max_items: int, max_depth: int, created_at: num, lease_until: num })) });
const contextObjectsShape = sealed({ decision: oneOf("accepted", "rejected"), reason: nullable(oneOf("federation_off", "not_an_actor", "context_fetch_off")), stored: int, refused: int });
const heartbeatReplyShape = sealed({ contract_version: str, federation: oneOf("off", "on") });

function sameMajor(version: string): boolean {
  return version.split(".")[0] === CONTRACT_VERSION.split(".")[0];
}

export interface InternalApiOptions {
  key: Uint8Array<ArrayBuffer>;
  transport: Transport;
  // Unix time in seconds.
  now?: () => number;
}

export class InternalApi {
  readonly #key: Uint8Array<ArrayBuffer>;
  readonly #transport: Transport;
  readonly #now: () => number;

  constructor(options: InternalApiOptions) {
    if (options.key.byteLength !== 32) throw new Error("the service key must contain 32 bytes");
    this.#key = options.key;
    this.#transport = options.transport;
    this.#now = options.now ?? (() => Date.now() / 1000);
  }

  // Returns null for 404. Throws InternalApiError for all other statuses that are not 200.
  async #call(method: "GET" | "POST", path: string, body?: unknown): Promise<unknown | null> {
    const target = `${INTERNAL_PREFIX}${path}`;
    const bytes = body === undefined ? new Uint8Array(0) : new TextEncoder().encode(JSON.stringify(body));
    const timestamp = String(Math.floor(this.#now()));
    const headers = new Headers({ [TIMESTAMP_HEADER]: timestamp, [SIGNATURE_HEADER]: await signRequest(this.#key, method, target, timestamp, bytes) });
    if (body !== undefined) headers.set("content-type", "application/json");
    const response = await this.#transport(new Request(`${BASE_URL}${target}`, { method, headers, body: body === undefined ? undefined : bytes }));
    const text = await response.text();
    if (response.status === 404) return null;
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      throw new InternalApiError(response.status, "invalid_json");
    }
    if (response.status !== 200) {
      const code = typeof parsed === "object" && parsed !== null && typeof (parsed as { error?: unknown }).error === "string" ? (parsed as { error: string }).error : "error";
      const error = new InternalApiError(response.status, code);
      error.detail = parsed;
      throw error;
    }
    return parsed;
  }

  async config(): Promise<InstanceConfig> {
    const value = await this.#call("GET", "/config");
    if (value === null) throw new InternalApiError(404, "not_found");
    configShape(value, "config");
    const config = value as InstanceConfig;
    if (!sameMajor(config.contract_version)) throw new ContractError(`Claire has contract version ${config.contract_version}, the sidecar has ${CONTRACT_VERSION}`);
    return config;
  }

  async heartbeat(heartbeat: Heartbeat): Promise<HeartbeatReply> {
    const value = await this.#call("POST", "/heartbeat", heartbeat);
    if (value === null) throw new InternalApiError(404, "not_found");
    heartbeatReplyShape(value, "heartbeat");
    return value as HeartbeatReply;
  }

  async actorByUsername(username: string): Promise<ActorRecord | null> {
    const value = await this.#call("GET", `/actors/by-username/${encodeURIComponent(username)}`);
    if (value !== null) actorShape(value, "actor");
    return value as ActorRecord | null;
  }

  // The record of an actor, or null when the account is not an actor now. A purged actor is null here: use actorResult to see it.
  async actor(publicId: string): Promise<ActorRecord | null> {
    const result = await this.actorResult(publicId);
    return result?.state === "found" ? result.record : null;
  }

  async actorResult(publicId: string): Promise<ActorResult> {
    let value: unknown | null;
    try {
      value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}`);
    } catch (error) {
      if (!(error instanceof InternalApiError) || error.status !== 410) throw error;
      goneShape(error.detail, "gone");
      return { state: "gone", gone_at: (error.detail as { gone_at: number }).gone_at };
    }
    if (value === null) return null;
    actorShape(value, "actor");
    return { state: "found", record: value as ActorRecord };
  }

  // publicId is an account public ID or INSTANCE_ACTOR_ID. The result contains private keys: do not write it to a log.
  async actorKeys(publicId: string): Promise<ActorKeys | null> {
    const value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}/keys`);
    if (value !== null) keysShape(value, "keys");
    return value as ActorKeys | null;
  }

  // Sends one verified inbound activity. The result is the decision of Claire. An error means no decision: the caller must fail, and the Fedify inbox queue tries again.
  async inboxEvent(event: InboxEvent): Promise<InboxDecision> {
    const value = await this.#call("POST", "/inbox-events", event);
    if (value === null) throw new InternalApiError(404, "not_found");
    decisionShape(value, "decision");
    return value as InboxDecision;
  }

  // Sends one verified inbound activity for the timelines (unit FP4-4). An error means no decision: the caller must fail, and the Fedify inbox queue tries again.
  async timelineEvent(event: TimelineEvent): Promise<TimelineDecision> {
    const value = await this.#call("POST", "/timeline-events", event);
    if (value === null) throw new InternalApiError(404, "not_found");
    timelineDecisionShape(value, "timeline_decision");
    return value as TimelineDecision;
  }

  // Asks if an account that is an actor follows the remote actor, and gets the limits of the parent chain.
  async timelineInterest(actorIri: string): Promise<TimelineInterest> {
    const value = await this.#call("GET", `/timeline-interest?${new URLSearchParams({ actor: actorIri })}`);
    if (value === null) throw new InternalApiError(404, "not_found");
    timelineInterestShape(value, "timeline_interest");
    return value as TimelineInterest;
  }

  // One cursor page of the followers of an actor, or null when the account is not an actor.
  async followers(publicId: string, page: { limit?: number; maxId?: string } = {}): Promise<FollowersPage | null> {
    const query = new URLSearchParams();
    if (page.limit !== undefined) query.set("limit", String(page.limit));
    if (page.maxId !== undefined) query.set("max_id", page.maxId);
    const value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}/followers${query.size > 0 ? `?${query}` : ""}`);
    if (value !== null) followersShape(value, "followers");
    return value as FollowersPage | null;
  }

  async domainBlocks(): Promise<DomainBlocks> {
    const value = await this.#call("GET", "/domain-blocks");
    if (value === null) throw new InternalApiError(404, "not_found");
    domainBlocksShape(value, "domain_blocks");
    return value as DomainBlocks;
  }

  // Takes the oldest ready tasks. Each task has a lease: if it is not acknowledged before lease_until, Claire gives it again.
  async outboxTasks(limit?: number): Promise<OutboxTask[]> {
    const value = await this.#call("GET", `/outbox-tasks${limit === undefined ? "" : `?limit=${limit}`}`);
    if (value === null) throw new InternalApiError(404, "not_found");
    outboxTasksShape(value, "outbox_tasks");
    return (value as { tasks: OutboxTask[] }).tasks;
  }

  // Records the result of the handoff to the Fedify queue. Returns the status of the task, or null when there is no such task.
  // A second acknowledgement returns the first result.
  async ackOutboxTask(id: number, result: "handed_off" | "rejected", errorCode: string | null = null): Promise<"pending" | "handed_off" | "rejected" | null> {
    const value = await this.#call("POST", `/outbox-tasks/${id}/ack`, { result, error_code: errorCode });
    if (value === null) return null;
    ackShape(value, "ack");
    return (value as { status: "pending" | "handed_off" | "rejected" }).status;
  }

  // Takes the oldest ready tasks of the client outbox (contract 1.3).
  async clientTasks(limit?: number): Promise<ClientTask[]> {
    const value = await this.#call("GET", `/client-tasks${limit === undefined ? "" : `?limit=${limit}`}`);
    if (value === null) throw new InternalApiError(404, "not_found");
    clientTasksShape(value, "client_tasks");
    return (value as { tasks: ClientTask[] }).tasks;
  }

  // Records the result of a client task. actor is the resolved actor of a follow task that was handed off.
  async ackClientTask(id: number, result: "handed_off" | "rejected", errorCode: string | null = null, actor: ResolvedActor | null = null): Promise<"pending" | "handed_off" | "rejected" | null> {
    const value = await this.#call("POST", `/client-tasks/${id}/ack`, { result, error_code: errorCode, actor });
    if (value === null) return null;
    ackShape(value, "ack");
    return (value as { status: "pending" | "handed_off" | "rejected" }).status;
  }

  // The number of accepted follows of an actor, or null when the account is not an actor.
  async following(publicId: string): Promise<{ public_id: string; total: number } | null> {
    const value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}/following`);
    if (value !== null) followingShape(value, "following");
    return value as { public_id: string; total: number } | null;
  }

  // Takes the oldest ready thread fetch tasks of the client outbox (contract 1.5). The acknowledgement is ackClientTask.
  async contextTasks(limit?: number): Promise<ContextTask[]> {
    const value = await this.#call("GET", `/context-tasks${limit === undefined ? "" : `?limit=${limit}`}`);
    if (value === null) throw new InternalApiError(404, "not_found");
    contextTasksShape(value, "context_tasks");
    return (value as { tasks: ContextTask[] }).tasks;
  }

  // Gives fetched objects of a thread fetch task to Claire. Returns null when Claire has no such task that waits.
  async contextObjects(taskId: number, objects: TimelineObject[]): Promise<ContextObjectsResult | null> {
    const value = await this.#call("POST", "/context-objects", { task_id: taskId, objects });
    if (value === null) return null;
    contextObjectsShape(value, "context_objects");
    return value as ContextObjectsResult;
  }

  async object(publicId: string): Promise<ObjectResult> {
    let value: unknown | null;
    try {
      value = await this.#call("GET", `/objects/${encodeURIComponent(publicId)}`);
    } catch (error) {
      if (!(error instanceof InternalApiError) || error.status !== 410) throw error;
      goneShape(error.detail, "gone");
      return { state: "gone", gone_at: (error.detail as { gone_at: number }).gone_at };
    }
    if (value === null) return null;
    objectShape(value, "object");
    return { state: "found", record: value as ObjectRecord };
  }

  // An own note (contract 1.6): the record, the time of removal (410), or null (404).
  async note(publicId: string): Promise<NoteResult> {
    let value: unknown | null;
    try {
      value = await this.#call("GET", `/notes/${encodeURIComponent(publicId)}`);
    } catch (error) {
      if (!(error instanceof InternalApiError) || error.status !== 410) throw error;
      goneShape(error.detail, "gone");
      return { state: "gone", gone_at: (error.detail as { gone_at: number }).gone_at };
    }
    if (value === null) return null;
    noteShape(value, "note");
    return { state: "found", record: value as NoteRecord };
  }

  // Takes the oldest ready tasks of the outbound interactions (contract 1.6). Each task has a lease.
  async interactionTasks(limit?: number): Promise<InteractionTask[]> {
    const value = await this.#call("GET", `/interaction-tasks${limit === undefined ? "" : `?limit=${limit}`}`);
    if (value === null) throw new InternalApiError(404, "not_found");
    interactionTasksShape(value, "interaction_tasks");
    return (value as { tasks: InteractionTask[] }).tasks;
  }

  // Records the result of an interaction task. mentions are the handles of a note that the sidecar resolved.
  async ackInteractionTask(id: number, result: "handed_off" | "rejected", errorCode: string | null = null, mentions: ResolvedMention[] = []): Promise<"pending" | "handed_off" | "rejected" | null> {
    const value = await this.#call("POST", `/interaction-tasks/${id}/ack`, { result, error_code: errorCode, mentions });
    if (value === null) return null;
    ackShape(value, "ack");
    return (value as { status: "pending" | "handed_off" | "rejected" }).status;
  }

  // Sends one verified inbound activity of the quote protocol (contract 1.7). An error means no decision: the caller must fail, and the Fedify inbox queue tries again.
  async quoteEvent(event: QuoteEvent): Promise<QuoteDecision> {
    const value = await this.#call("POST", "/quote-events", event);
    if (value === null) throw new InternalApiError(404, "not_found");
    quoteDecisionShape(value, "quote_decision");
    return value as QuoteDecision;
  }

  // An approved quote request (contract 1.7): the record, the time of the revoke (410), or null (404).
  async quoteAuthorization(id: string): Promise<QuoteAuthorizationResult> {
    let value: unknown | null;
    try {
      value = await this.#call("GET", `/quote-authorizations/${encodeURIComponent(id)}`);
    } catch (error) {
      if (!(error instanceof InternalApiError) || error.status !== 410) throw error;
      goneShape(error.detail, "gone");
      return { state: "gone", gone_at: (error.detail as { gone_at: number }).gone_at };
    }
    if (value === null) return null;
    quoteAuthorizationShape(value, "quote_authorization");
    return { state: "found", record: value as QuoteAuthorizationRecord };
  }

  // One cursor page of the published objects of an actor, or null when the account is not an actor.
  async actorOutbox(publicId: string, page: { limit?: number; maxId?: string } = {}): Promise<ActorOutboxPage | null> {
    const query = new URLSearchParams();
    if (page.limit !== undefined) query.set("limit", String(page.limit));
    if (page.maxId !== undefined) query.set("max_id", page.maxId);
    const value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}/outbox${query.size > 0 ? `?${query}` : ""}`);
    if (value !== null) actorOutboxShape(value, "outbox");
    return value as ActorOutboxPage | null;
  }
}
