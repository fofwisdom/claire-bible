// Answer attribution, the path record, and the traffic counters of the readiness checks (FEDERATION_GO_LIVE_DESIGN.md Sections 5, 7, and 9; contract 1.13).
//
// Rules of this module:
// - A probe answer gets one party: this sidecar, another sidecar process, Claire, the CDN (challenge, block, error), another intermediary, or nobody.
// - The record has no body text and no header value that is longer than 64 characters.
// - The traffic counters are in memory only, for the last 24 hours. The sidecar stores no canonical data (FK1).

import type { AnswerError, AnswerParty, DeliveryFailureClass, ProbeAnswer, ProbeProblem, PublicHost, PublicHostAddress, Traffic } from "./internal_api.ts";

export const SIDECAR_HEADER = "x-claire-fedify-server";
// Claire sets this header on each answer of its public listener.
export const CLAIRE_HEADER = "x-claire-server";
// The header of a self-check request. Its value is a nonce that this process made.
export const SELF_CHECK_HEADER = "x-claire-self-check";
export const TRAFFIC_WINDOW_SECONDS = 86_400;
const BUCKET_SECONDS = 3600;
const HEADER_TEXT_MAX = 64;
const MITIGATION_MAX = 32;
export const PROBE_PATH_MAX = 256;

export interface Answer {
  party: AnswerParty;
  status: number | null;
  error: AnswerError | null;
  via_cdn: "cloudflare" | null;
  server: string | null;
  media_type: string | null;
  mitigation: string | null;
}

// Printable ASCII only, with a length limit. A header of an intermediary is not trusted text.
function headerText(value: string | null, limit: number): string | null {
  if (value === null) return null;
  const clean = value.replace(/[^\x20-\x7e]/g, "").trim().slice(0, limit);
  return clean === "" ? null : clean;
}

export function classifyAnswer(response: Response, marker: string): Answer {
  const headers = response.headers;
  const mitigation = headerText(headers.get("cf-mitigated"), MITIGATION_MAX);
  const viaCdn = headers.has("cf-ray") ? "cloudflare" as const : null;
  const seen = headers.get(SIDECAR_HEADER);
  let party: AnswerParty;
  if (seen !== null) party = seen === marker ? "sidecar" : "sidecar_other";
  else if (headers.has(CLAIRE_HEADER)) party = "claire";
  else if (mitigation !== null) party = mitigation.toLowerCase() === "challenge" ? "cdn_challenge" : "cdn_block";
  else if (viaCdn !== null && response.status >= 520 && response.status <= 530) party = "cdn_error";
  else party = "intermediary";
  const mediaType = headerText((headers.get("content-type") ?? "").split(";", 1)[0].toLowerCase(), HEADER_TEXT_MAX);
  return { party, status: response.status, error: null, via_cdn: viaCdn, server: headerText(headers.get("server"), HEADER_TEXT_MAX), media_type: mediaType, mitigation };
}

export function classifyError(error: unknown): AnswerError {
  const name = (error as { name?: string })?.name ?? "";
  const text = `${name} ${(error as { message?: string })?.message ?? String(error)}`.toLowerCase();
  if (name === "TimeoutError" || name === "AbortError" || /timed? ?out|deadline/.test(text)) return "timeout";
  if (/(?:certificate|tls|ssl|handshake|unknown\s*issuer|self[- ]signed|peer.*cert)/.test(text)) return "tls";
  if (/(?:dns|lookup|getaddrinfo|nxdomain|name or service not known|no such host|nodename nor servname|name resolution)/.test(text)) return "dns";
  if (/(?:connection refused|econnrefused|connect error|connection reset|econnreset|network is unreachable|no route to host)/.test(text)) return "connect";
  return "other";
}

export function noAnswer(error: unknown): Answer {
  return { party: "none", status: null, error: classifyError(error), via_cdn: null, server: null, media_type: null, mitigation: null };
}

export function probeAnswer(path: string, problem: ProbeProblem, answer: Answer, received: string | null = null): ProbeAnswer {
  return { path: path.slice(0, PROBE_PATH_MAX), problem, ...answer, received: received === null ? null : received.slice(0, PROBE_PATH_MAX) };
}

const IPV4 = /^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$/;

// The class of an address: loopback, private (also link-local, unique local, and the shared address space of RFC 6598), or global.
export function addressClass(address: string): PublicHostAddress["class"] {
  const v4 = IPV4.exec(address);
  if (v4 !== null) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    if (a === 127) return "loopback";
    if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254) || (a === 100 && b >= 64 && b <= 127) || a === 0) return "private";
    return "global";
  }
  const v6 = address.toLowerCase().replace(/^\[|\]$/g, "");
  if (v6 === "::1") return "loopback";
  const mapped = /^::ffff:([0-9.]+)$/.exec(v6);
  if (mapped !== null) return addressClass(mapped[1]);
  if (/^f[cd]/.test(v6) || /^fe[89ab]/.test(v6) || v6 === "::") return "private";
  return "global";
}

export type DnsResolver = (host: string, type: "A" | "AAAA") => Promise<string[]>;

// The addresses of the host of the public origin, as the resolver of this process gives them (FEDERATION_GO_LIVE_DESIGN.md Section 7.1).
export async function resolvePublicHost(origin: string, resolver: DnsResolver = (host, type) => Deno.resolveDns(host, type)): Promise<PublicHost> {
  let host: string;
  try {
    host = new URL(origin).hostname;
  } catch {
    return { host: "", addresses: [], error: "dns" };
  }
  const literal = host.replace(/^\[|\]$/g, "");
  if (IPV4.test(literal) || literal.includes(":")) return { host, addresses: [{ address: literal, class: addressClass(literal) }], error: null };
  const found: string[] = [];
  for (const type of ["A", "AAAA"] as const) {
    try {
      for (const address of await resolver(host, type)) if (!found.includes(address)) found.push(address);
    } catch {
      // No record of this type.
    }
  }
  if (found.length === 0) return { host, addresses: [], error: "dns" };
  return { host, addresses: found.slice(0, 16).map((address) => ({ address, class: addressClass(address) })), error: null };
}

interface Bucket {
  verified: number;
  hosts: Set<string>;
  rejected: number;
  succeeded: number;
  failed: Record<DeliveryFailureClass, number>;
}

const FAILURE_CLASSES: DeliveryFailureClass[] = ["dns", "tls", "timeout", "http_4xx", "http_5xx", "other"];

function emptyFailures(): Record<DeliveryFailureClass, number> {
  return { dns: 0, tls: 0, timeout: 0, http_4xx: 0, http_5xx: 0, other: 0 };
}

// The class of a failed delivery, from the HTTP status of the inbox or from the error of the request.
export function deliveryFailureClass(error: unknown): DeliveryFailureClass {
  const status = (error as { statusCode?: unknown })?.statusCode;
  if (typeof status === "number") return status >= 500 ? "http_5xx" : status >= 400 ? "http_4xx" : "other";
  const klass = classifyError(error);
  return klass === "dns" || klass === "tls" || klass === "timeout" ? klass : "other";
}

// Counters of the requests of other servers and of the deliveries to them, in the last 24 hours (FEDERATION_GO_LIVE_DESIGN.md Section 9.1).
export class TrafficCounters {
  readonly #now: () => number;
  readonly #buckets = new Map<number, Bucket>();
  #lastVerifiedAt: number | null = null;
  #lastRejectedAt: number | null = null;
  #lastSuccessAt: number | null = null;

  constructor(now: () => number) {
    this.#now = now;
  }

  #bucket(): Bucket {
    const now = this.#now();
    const index = Math.floor(now / BUCKET_SECONDS);
    const oldest = Math.floor((now - TRAFFIC_WINDOW_SECONDS) / BUCKET_SECONDS);
    for (const key of this.#buckets.keys()) if (key < oldest) this.#buckets.delete(key);
    let bucket = this.#buckets.get(index);
    if (bucket === undefined) {
      bucket = { verified: 0, hosts: new Set(), rejected: 0, succeeded: 0, failed: emptyFailures() };
      this.#buckets.set(index, bucket);
    }
    return bucket;
  }

  // One inbound request with a signature that verified. `ownHost` is the host of this server: a request of this server is not a request of another server.
  inboundVerified(host: string, ownHost: string): void {
    if (host.toLowerCase() === ownHost.toLowerCase()) return;
    const bucket = this.#bucket();
    bucket.verified += 1;
    bucket.hosts.add(host.toLowerCase());
    this.#lastVerifiedAt = this.#now();
  }

  inboundRejected(): void {
    this.#bucket().rejected += 1;
    this.#lastRejectedAt = this.#now();
  }

  deliverySucceeded(): void {
    this.#bucket().succeeded += 1;
    this.#lastSuccessAt = this.#now();
  }

  deliveryFailed(klass: DeliveryFailureClass): void {
    this.#bucket().failed[klass] += 1;
  }

  snapshot(): Traffic {
    const now = this.#now();
    const oldest = Math.floor((now - TRAFFIC_WINDOW_SECONDS) / BUCKET_SECONDS);
    let verified = 0;
    let rejected = 0;
    let succeeded = 0;
    const hosts = new Set<string>();
    const failed = emptyFailures();
    for (const [key, bucket] of this.#buckets) {
      if (key < oldest) continue;
      verified += bucket.verified;
      rejected += bucket.rejected;
      succeeded += bucket.succeeded;
      for (const host of bucket.hosts) hosts.add(host);
      for (const klass of FAILURE_CLASSES) failed[klass] += bucket.failed[klass];
    }
    const recent = (at: number | null) => (at !== null && now - at <= TRAFFIC_WINDOW_SECONDS ? at : null);
    return {
      window_seconds: TRAFFIC_WINDOW_SECONDS,
      inbound_verified: { count: verified, hosts: hosts.size, last_at: recent(this.#lastVerifiedAt) },
      inbound_rejected: { count: rejected, last_at: recent(this.#lastRejectedAt) },
      deliveries: { succeeded, failed, last_success_at: recent(this.#lastSuccessAt) },
    };
  }
}

// A meter provider with the smallest interface that Fedify uses (OpenTelemetry API). It reads one counter, `activitypub.delivery.sent`, and ignores all other instruments.
export function deliveryMeterProvider(onDelivery: (success: boolean) => void): unknown {
  const ignore = { add: () => {}, record: () => {}, addCallback: () => {}, removeCallback: () => {} };
  const meter = {
    createCounter: (name: string) => (name === "activitypub.delivery.sent" ? { add: (_value: number, attributes?: Record<string, unknown>) => onDelivery(attributes?.["activitypub.delivery.success"] === true) } : ignore),
    createHistogram: () => ignore,
    createUpDownCounter: () => ignore,
    createGauge: () => ignore,
    createObservableGauge: () => ignore,
    createObservableCounter: () => ignore,
    createObservableUpDownCounter: () => ignore,
    addBatchObservableCallback: () => {},
    removeBatchObservableCallback: () => {},
  };
  return { getMeter: () => meter };
}
