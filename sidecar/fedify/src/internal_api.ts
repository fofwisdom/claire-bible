// Client for the Claire internal federation API (design Sections 6.1 and 6.2).
//
// Each request has a timestamp header and a signature header. The signature is HMAC-SHA256 with the service key over:
//   {METHOD}\n{request target}\n{timestamp}\n{SHA-256 of the body, lowercase hex}
// The Python side is src/claire/api/federation_internal.py. The contract fixtures are in tests/fixtures/federation_contract/.
//
// Each response is checked against a sealed shape: an absent field, an extra field, or a wrong type is an error.

export const CONTRACT_VERSION = "1.0";
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

export class InternalApiError extends Error {
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
});
const actorShape = sealed({ public_id: str, username: str, display_name: str, activated_at: nullable(num), updated_at: num, profile_url: str });
const keysShape = sealed({ public_id: str, keys: list(sealed({ alg: oneOf("rsa2048", "ed25519"), public_jwk: stringMap, private_jwk: stringMap })) });
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
      throw new InternalApiError(response.status, code);
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
}
