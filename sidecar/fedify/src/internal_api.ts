// Client for the Claire internal federation API (design Sections 6.1 and 6.2).
//
// Each request has a timestamp header and a signature header. The signature is HMAC-SHA256 with the service key over:
//   {METHOD}\n{request target}\n{timestamp}\n{SHA-256 of the body, lowercase hex}
// The Python side is src/claire/api/federation_internal.py. The contract fixtures are in tests/fixtures/federation_contract/.
//
// Each response is checked against a sealed shape: an absent field, an extra field, or a wrong type is an error.

export const CONTRACT_VERSION = "1.1";
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
  reason: "federation_off" | "invalid_actor" | "blocked_domain" | "not_an_actor" | "recipient_mismatch" | "unsupported_type" | null;
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
}

// The result of an object read: the record, the time of removal of a deleted object (410), or null (404).
export type ObjectResult = { state: "found"; record: ObjectRecord } | { state: "gone"; gone_at: number } | null;

// items are newest first.
export interface ActorOutboxPage {
  public_id: string;
  total: number;
  items: { id: string; published_at: number; updated_at: number }[];
  next_max_id: string | null;
}

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
  reason: nullable(oneOf("federation_off", "invalid_actor", "blocked_domain", "not_an_actor", "recipient_mismatch", "unsupported_type")),
});
const followersShape = sealed({ public_id: str, total: int, items: list(sealed({ id: str, actor_iri: str, inbox_iri: str, shared_inbox_iri: nullable(str) })), next_max_id: nullable(str) });
const domainBlocksShape = sealed({ etag: str, domains: list(str) });
const outboxTasksShape = sealed({
  tasks: list(sealed({ id: int, kind: oneOf("create", "update", "delete", "update_actor", "delete_actor"), actor_public_id: str, object_public_id: nullable(str), created_at: num, lease_until: num })),
});
const ackShape = sealed({ id: int, status: oneOf("pending", "handed_off", "rejected") });
const objectShape = sealed({ public_id: str, actor_public_id: str, type: oneOf("Article"), name: str, content: str, url: nullable(str), published_at: num, updated_at: num });
const goneShape = sealed({ error: oneOf("gone"), gone_at: num });
const actorOutboxShape = sealed({ public_id: str, total: int, items: list(sealed({ id: str, published_at: num, updated_at: num })), next_max_id: nullable(str) });
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

  async actor(publicId: string): Promise<ActorRecord | null> {
    const value = await this.#call("GET", `/actors/${encodeURIComponent(publicId)}`);
    if (value !== null) actorShape(value, "actor");
    return value as ActorRecord | null;
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
