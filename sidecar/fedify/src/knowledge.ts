// Federation of shared knowledge in the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 8.1, 9.1, 13, and 14; units KF1 and KF2).
//
// Rules of this module:
// - The context document https://w3id.org/claire-bible/ns/v1 is part of the code (design Section 9.1 rule 2). The file claire_context_v1.json is a copy of src/claire/static/ns/v1.jsonld of Claire; a Python test fails when the two are different. This module puts the document in the table of contexts that the document loaders of Fedify read before they make a request. Thus no loader of Fedify gets the context from the network.
// - An activity of the knowledge federation is an activity whose @context has the Claire context IRI. Claire reads the compact names of the wire contract, so such an activity goes to Claire as the JSON that came, without a new compaction: the sidecar parses the body and does not give it to the vocabulary of Fedify.
// - A collection of shared knowledge (/ap/kb, /ap/themes) answers only a request with a valid HTTP signature. The sidecar verifies the signature and gives Claire the actor of the key; Claire decides (the switch, the server actor rule, the domain blocks). Each refusal of Claire is the same 404.
// - The reads of other servers (NodeInfo, the server actor, the /kb collection) use the document loader of Fedify with the key of the server actor of this server: each request is signed, and the loader refuses a private network address. Claire says which servers are due; the sidecar keeps no list.
// - Unit KF3 (design Sections 9.2, 9.3, 10, and 11). Each shared knowledge base of this server is an actor at /ap/kb/{number}: Claire gives the actor document with the public key, the collections, and each document, and the sidecar gives them without a change. An outbound activity is the JSON of Claire: the sidecar does not make an object of the Fedify vocabulary from it (that vocabulary has no Grant and no Revoke, and it does not keep the wire form). The sidecar signs the POST with the key of the actor of the task (the server actor, or a knowledge base actor) and reads the inbox of the recipient from its actor document. A document for a curation is a GET that the server actor signs.
// - A document of another server is not trusted: the server actor and each page of a collection must come from the server that was asked. Claire checks each knowledge base again (vocabulary.validate_object).
// - No other server can stop the work of this module (KS2): each request has a timeout, and each delivery and each read has a time limit. A task that is over its time limit ends with a result that Claire can give again later, and the loop takes the next task.

import type { DocumentLoader } from "@fedify/fedify";
import { preloadedContexts, validatePublicUrl } from "jsr:@fedify/vocab-runtime@^2.4.6";
import claireContext from "./claire_context_v1.json" with { type: "json" };
import { PrivateAddressError } from "./pinned_fetch.ts";
import type { InternalApi, KbRead, KnowledgeOutboxTask, RemoteRead, ServerReadResult } from "./internal_api.ts";

export const CLAIRE_CONTEXT_IRI = "https://w3id.org/claire-bible/ns/v1";
export const CLAIRE_CONTEXT_DOCUMENT: unknown = claireContext;

// The loaders of Fedify look in this table first. The entry makes the Claire context a local document for each loader of this process.
(preloadedContexts as Record<string, unknown>)[CLAIRE_CONTEXT_IRI] = CLAIRE_CONTEXT_DOCUMENT;

// The public paths of the collections of shared knowledge, and the name of each one in the internal API.
export const KNOWLEDGE_COLLECTION_PATHS: ReadonlyMap<string, "kb" | "themes"> = new Map([["/ap/kb", "kb"], ["/ap/themes", "themes"]]);
export const SERVER_ACTOR_PATH = "/ap/actors/server";
// The number of servers that one poll reads.
export const KNOWLEDGE_READ_BATCH = 5;
// The limits of one read of a /kb collection. The time is less than the lease of Claire (600 seconds).
export const KB_READ_MAX_PAGES = 60;
export const KB_READ_SECONDS = 240;
// The limits of one batch for Claire. The body limit of the internal API is 1 MiB.
export const KB_BATCH_ITEMS = 20;
export const KB_BATCH_BYTES = 512 * 1024;
const NODEINFO_RELS = ["http://nodeinfo.diaspora.software/ns/schema/2.1", "http://nodeinfo.diaspora.software/ns/schema/2.0"];
const MAX_SOFTWARE_LENGTH = 100;
// The timeout of one request to another server (a GET of the document loader, or the POST of a delivery), as in publicSelfCheck.
export const REQUEST_TIMEOUT_MS = 15_000;
// The time limit of one task: the read of one server (NodeInfo and the server actor), one delivery, or one read for a curation. One other server cannot hold the loop longer.
export const TASK_SECONDS = 60;

// Runs one task with a time limit. The task gets a signal that aborts at the limit; when the limit comes first, the result is onTimeout and the task does not hold the caller.
export async function withDeadline<T>(work: (signal: AbortSignal) => Promise<T>, milliseconds: number, onTimeout: T): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const limit = new Promise<T>((resolve) => {
    timer = setTimeout(() => {
      controller.abort(new DOMException("time limit", "TimeoutError"));
      resolve(onTimeout);
    }, milliseconds);
  });
  try {
    return await Promise.race([work(controller.signal), limit]);
  } finally {
    clearTimeout(timer);
  }
}

// The signal of one request: it aborts at the timeout of a request or when the task ends.
function requestSignal(task?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  return task === undefined ? timeout : AbortSignal.any([task, timeout]);
}

// The status of the answer of a failed load of the document loader (FetchError of Fedify has the answer), or null.
function failedStatus(error: unknown): number | null {
  const response = (error as { response?: unknown } | null)?.response;
  return response instanceof Response ? response.status : null;
}

// The check of an address before a request that does not use the document loader (the POST of a delivery): an address that is not public is refused, as the document loader refuses it. allowPrivate is the development switch of Claire (contract 1.8).
export function publicAddressCheck(allowPrivate: boolean): (url: URL) => Promise<void> {
  return allowPrivate ? () => Promise.resolve() : (url: URL) => validatePublicUrl(url.href);
}

// True when the JSON of an activity is in the wire contract of the knowledge federation: its @context array has the Claire context IRI.
export function isKnowledgeDocument(document: unknown): document is Record<string, unknown> {
  if (typeof document !== "object" || document === null || Array.isArray(document)) return false;
  const context = (document as Record<string, unknown>)["@context"];
  return Array.isArray(context) && context.includes(CLAIRE_CONTEXT_IRI);
}

// The JSON object of a request body when it is an activity of the knowledge federation, else null. The object is the parsed body: no key is added, removed, or changed.
export function knowledgeDocumentOf(body: Uint8Array): Record<string, unknown> | null {
  try {
    const document: unknown = JSON.parse(new TextDecoder().decode(body));
    return isKnowledgeDocument(document) ? document : null;
  } catch {
    return null;
  }
}

export interface KnowledgeReadDeps {
  api: InternalApi;
  // The loader that signs each request with the key of the server actor of this server.
  documentLoader: DocumentLoader;
  // The canonical origin of this server. Its scheme is the scheme of each read: https, and http only for a development server.
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  // Unix time in milliseconds.
  now?: () => number;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function base(deps: KnowledgeReadDeps, domain: string): string {
  return `${new URL(deps.origin).protocol}//${domain}`;
}

function failure(domain: string, error: string): ServerReadResult {
  return { domain, ok: false, server_actor: null, software_name: null, software_version: null, shared_kb_count: null, error };
}

// Reads NodeInfo and the server actor of one known server (design Section 6.2). Never throws: a read that fails gives a result with an error code. Each request has a timeout; signal ends the read at the time limit of the task.
export async function readServer(deps: KnowledgeReadDeps, domain: string, signal?: AbortSignal): Promise<ServerReadResult> {
  if (await deps.isBlocked(domain)) return failure(domain, "blocked_domain");
  const root = base(deps, domain);
  let software: Record<string, unknown> | null;
  try {
    const links = record((await deps.documentLoader(`${root}/.well-known/nodeinfo`, { signal: requestSignal(signal) })).document)?.links;
    const link = Array.isArray(links) ? links.map(record).find((item) => item !== null && NODEINFO_RELS.includes(String(item.rel)) && typeof item.href === "string") : undefined;
    if (link === undefined || link === null) return failure(domain, "nodeinfo_invalid");
    const href = new URL(String(link.href));
    // The NodeInfo document is on the server that was asked.
    if (href.origin !== root) return failure(domain, "nodeinfo_invalid");
    software = record(record((await deps.documentLoader(href.href, { signal: requestSignal(signal) })).document)?.software);
  } catch {
    return failure(domain, "nodeinfo_unavailable");
  }
  const name = software?.name;
  const version = software?.version;
  if (typeof name !== "string" || typeof version !== "string" || name.length < 1 || version.length < 1 || name.length > MAX_SOFTWARE_LENGTH || version.length > MAX_SOFTWARE_LENGTH) return failure(domain, "nodeinfo_invalid");
  const actorUrl = `${root}${SERVER_ACTOR_PATH}`;
  let actor: Record<string, unknown> | null;
  try {
    const remote = await deps.documentLoader(actorUrl, { signal: requestSignal(signal) });
    if (new URL(remote.documentUrl).origin !== root) return failure(domain, "server_actor_invalid");
    actor = record(remote.document);
  } catch {
    return failure(domain, "server_actor_unavailable");
  }
  // A Claire server has its server actor at this path, with an inbox (FEDERATION.md Section 4.2).
  if (actor === null || actor.id !== actorUrl || typeof actor.inbox !== "string") return failure(domain, "server_actor_invalid");
  // NodeInfo of a Claire server does not give the number of shared knowledge bases: Claire counts the records after the read of the /kb collection.
  return { domain, ok: true, server_actor: actorUrl, software_name: name, software_version: version, shared_kb_count: null, error: null };
}

// The items of one page in batches for Claire: KB_BATCH_ITEMS items and KB_BATCH_BYTES bytes at most. null: one item is larger than a batch.
export function kbBatches(items: Record<string, unknown>[]): Record<string, unknown>[][] | null {
  const batches: Record<string, unknown>[][] = [];
  let current: Record<string, unknown>[] = [];
  let size = 0;
  for (const item of items) {
    const bytes = new TextEncoder().encode(JSON.stringify(item)).byteLength;
    if (bytes > KB_BATCH_BYTES) return null;
    if (current.length >= KB_BATCH_ITEMS || size + bytes > KB_BATCH_BYTES) {
      batches.push(current);
      current = [];
      size = 0;
    }
    current.push(item);
    size += bytes;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

// Reads the /kb collection of one known server in full with signed GET requests (design Section 8.1 rule 2) and gives the items to Claire. Returns "complete", an error code, or "no_read" when Claire has no read for the domain (the lease ended, or a block started).
// The collection is an OrderedCollection with the items in the document or in pages (first, then next). Each page must be on the server that was asked.
export async function readKbCollection(deps: KnowledgeReadDeps, read: KbRead): Promise<string> {
  const now = deps.now ?? (() => Date.now());
  const deadline = now() + KB_READ_SECONDS * 1000;
  const fail = async (error: string): Promise<string> => (await deps.api.recordKbRead({ domain: read.domain, items: [], complete: false, error })) === null ? "no_read" : error;
  let start: URL;
  try {
    start = new URL(read.url);
  } catch {
    return await fail("collection_invalid");
  }
  // The scheme of a read is the scheme of this server: https, and http only for a development server.
  if (start.protocol !== new URL(deps.origin).protocol) return await fail("collection_invalid");
  if (await deps.isBlocked(start.hostname)) return await fail("blocked_domain");
  // The time limit of the read also ends a request that waits: one server cannot hold the loop.
  const readSignal = AbortSignal.timeout(KB_READ_SECONDS * 1000);
  const load = async (url: string): Promise<Record<string, unknown> | string> => {
    try {
      const remote = await deps.documentLoader(url, { signal: requestSignal(readSignal) });
      if (new URL(remote.documentUrl).origin !== start.origin) return "page_on_other_server";
      return record(remote.document) ?? "collection_invalid";
    } catch {
      return readSignal.aborted ? "read_timeout" : "collection_unavailable";
    }
  };
  let page = await load(start.href);
  if (typeof page === "string") return await fail(page);
  if (page.type !== "OrderedCollection" && page.type !== "OrderedCollectionPage") return await fail("collection_invalid");
  const visited = new Set<string>([start.href]);
  for (let pages = 0;; pages += 1) {
    if (pages >= KB_READ_MAX_PAGES) return await fail("too_many_pages");
    if (now() > deadline) return await fail("read_timeout");
    if (page.orderedItems !== undefined) {
      if (!Array.isArray(page.orderedItems)) return await fail("collection_invalid");
      // An item that is not a JSON object is not a knowledge base: Claire refuses it and counts it.
      const batches = kbBatches(page.orderedItems.map((item) => record(item) ?? { type: typeof item }));
      if (batches === null) return await fail("item_too_large");
      for (const items of batches) {
        if ((await deps.api.recordKbRead({ domain: read.domain, items, complete: false, error: null })) === null) return "no_read";
      }
    }
    // The root has `first`; a page has `next` while more pages follow.
    const following = page.type === "OrderedCollection" && page.orderedItems === undefined ? page.first : page.next;
    if (following === undefined || following === null) break;
    let next: URL;
    try {
      next = new URL(String(following));
    } catch {
      return await fail("collection_invalid");
    }
    if (next.origin !== start.origin) return await fail("page_on_other_server");
    if (visited.has(next.href)) return await fail("collection_invalid");
    visited.add(next.href);
    page = await load(next.href);
    if (typeof page === "string") return await fail(page);
    if (page.type !== "OrderedCollectionPage") return await fail("collection_invalid");
  }
  // The end of a complete read: Claire deletes each record of the server that the read did not contain.
  return (await deps.api.recordKbRead({ domain: read.domain, items: [], complete: true, error: null })) === null ? "no_read" : "complete";
}

// Numbers of reads since the start of this process, by result.
export interface KnowledgeReadCounters {
  servers_ok: number;
  servers_failed: number;
  collections_ok: number;
  collections_failed: number;
}

// Takes the reads that are due from Claire and does them: first the servers (NodeInfo and the server actor), then the /kb collections. A server that was read for the first time is due for its collection in the same call. Returns the number of reads that Claire gave.
// An error of the internal API stops the call; the lease of Claire ends, and Claire gives the read again.
export async function processKnowledgeReads(deps: KnowledgeReadDeps, counters: KnowledgeReadCounters, limit = KNOWLEDGE_READ_BATCH): Promise<number> {
  const servers = await deps.api.knowledgeServerReads(limit);
  for (const domain of servers) {
    const result = await withDeadline((signal) => readServer(deps, domain, signal), TASK_SECONDS * 1000, failure(domain, "read_timeout"));
    counters[result.ok ? "servers_ok" : "servers_failed"] += 1;
    if (!result.ok) console.warn(`knowledge server read failed: ${result.error}`);
    await deps.api.recordServerRead(result);
  }
  const reads = await deps.api.knowledgeKbReads(limit);
  for (const read of reads) {
    const result = await readKbCollection(deps, read);
    counters[result === "complete" ? "collections_ok" : "collections_failed"] += 1;
    if (result !== "complete") console.warn(`knowledge kb read failed: ${result}`);
  }
  return servers.length + reads.length;
}

// --- Unit KF3: knowledge base actors, outbound activities, and reads for a curation ---

// The paths of a knowledge base actor: the actor document, its collections, and one document.
export const KB_ACTOR_PATH = /^\/ap\/kb\/([1-9][0-9]{0,18})(?:\/(?:(followers|outbox)|(documents)(?:\/([1-9][0-9]{0,18}))?))?$/;
export const KNOWLEDGE_OUTBOX_BATCH = 10;
export const SERVER_SIGNER = "server";
// The signer of a task that an account actor signs (the Offer of a contribution, unit KF4): this prefix and the public ID of the account.
export const ACCOUNT_SIGNER_PREFIX = "account:";
// The largest document of a read that goes to Claire. The body limit of the internal API is 1 MiB.
export const REMOTE_READ_BYTES = 900 * 1024;
// Answers of an inbox after which a new delivery of the same activity cannot succeed.
const FINAL_STATUSES = new Set([400, 401, 403, 404, 405, 410, 413, 422]);

export interface KnowledgeDeliveryDeps extends KnowledgeReadDeps {
  // The private key and the key ID of the signer of a task: "server", the number of a knowledge base actor, or "account:" and the public ID of an account actor. null: the actor has no key.
  signingKey: (signer: string) => Promise<{ privateKey: CryptoKey; keyId: URL } | null>;
  // Signs a request with an HTTP Signature (signRequest of Fedify).
  sign: (request: Request, privateKey: CryptoKey, keyId: URL) => Promise<Request>;
  // Throws for an address that is not public (publicAddressCheck). The POST of a delivery does not use the document loader, so it needs this check.
  checkAddress: (url: URL) => Promise<void>;
  // The request function of the delivery. The sidecar gives pinnedFetch (pinned_fetch.ts), which connects only to an address that it checked, unless the development switch allow_private_addresses is on; without a value the global fetch is used. A PrivateAddressError of it ends the delivery as rejected (inbox_private_address).
  fetch?: typeof fetch;
  // Gets the outcome of each delivery (the traffic counters of GF20).
  onDelivery?: (outcome: DeliveryOutcome) => void;
}

export interface DeliveryOutcome {
  result: "delivered" | "retry" | "rejected";
  error: string | null;
}

// Numbers of outbound activities and of reads for a curation since the start of this process, by result.
export interface KnowledgeDeliveryCounters {
  delivered: number;
  retry: number;
  rejected: number;
  reads_ok: number;
  reads_failed: number;
}

// Delivers one outbound activity: the inbox of the recipient comes from its actor document (a GET that the server actor signs), and the POST has the JSON of Claire as its body and the HTTP Signature of the actor of the task. Never throws. Each request has a timeout; signal ends the delivery at the time limit of the task.
export async function deliverKnowledgeTask(deps: KnowledgeDeliveryDeps, task: KnowledgeOutboxTask, signal?: AbortSignal): Promise<DeliveryOutcome> {
  let recipient: URL;
  try {
    recipient = new URL(task.recipient);
  } catch {
    return { result: "rejected", error: "recipient_invalid" };
  }
  try {
    if (await deps.isBlocked(recipient.hostname)) return { result: "rejected", error: "blocked_domain" };
  } catch {
    return { result: "retry", error: "claire_unavailable" };
  }
  let key: { privateKey: CryptoKey; keyId: URL } | null;
  try {
    key = await deps.signingKey(task.signer);
  } catch {
    return { result: "retry", error: "claire_unavailable" };
  }
  if (key === null) return { result: "rejected", error: "no_actor_key" };
  let inbox: URL;
  try {
    const remote = await deps.documentLoader(recipient.href, { signal: requestSignal(signal) });
    const actor = record(remote.document);
    if (new URL(remote.documentUrl).origin !== recipient.origin || actor === null || actor.id !== recipient.href || typeof actor.inbox !== "string") return { result: "rejected", error: "recipient_invalid" };
    inbox = new URL(actor.inbox);
  } catch {
    return { result: "retry", error: "recipient_unavailable" };
  }
  // The inbox is on the server of the recipient: an actor document cannot send the activity to another server.
  if (inbox.origin !== recipient.origin) return { result: "rejected", error: "recipient_invalid" };
  // The document loader refuses an address that is not public; the POST does not use it, so the same check is here.
  try {
    await deps.checkAddress(inbox);
  } catch {
    return { result: "rejected", error: "inbox_private_address" };
  }
  let response: Response;
  try {
    const timeout = requestSignal(signal);
    const request = new Request(inbox, { method: "POST", headers: { "content-type": "application/activity+json" }, body: JSON.stringify(task.activity), redirect: "manual", signal: timeout });
    // The signal is given again: the signed request is a new Request object.
    response = await (deps.fetch ?? fetch)(await deps.sign(request, key.privateKey, key.keyId), { signal: timeout });
    await response.body?.cancel();
  } catch (error) {
    // The address of the connection is checked again at the connection (DNS rebinding): an address that is not public now ends the delivery as the check above does.
    if (error instanceof PrivateAddressError) return { result: "rejected", error: "inbox_private_address" };
    return { result: "retry", error: "inbox_unreachable" };
  }
  if (response.status >= 200 && response.status < 300) return { result: "delivered", error: null };
  return { result: FINAL_STATUSES.has(response.status) ? "rejected" : "retry", error: `inbox_status_${response.status}` };
}

// Takes the outbound activities that are ready from Claire, delivers each one, and gives Claire the result. Returns the number of tasks that Claire gave. An error of the internal API stops the call; a task without a result comes again after its lease.
export async function processKnowledgeOutbox(deps: KnowledgeDeliveryDeps, counters: KnowledgeDeliveryCounters, limit = KNOWLEDGE_OUTBOX_BATCH, taskMilliseconds = TASK_SECONDS * 1000): Promise<number> {
  const tasks = await deps.api.knowledgeOutboxTasks(limit);
  for (const task of tasks) {
    // One recipient cannot hold the loop: a delivery that is over its time limit comes again later (KS2).
    const outcome = await withDeadline((signal) => deliverKnowledgeTask(deps, task, signal), taskMilliseconds, { result: "retry", error: "delivery_timeout" } as DeliveryOutcome);
    counters[outcome.result] += 1;
    deps.onDelivery?.(outcome);
    if (outcome.result !== "delivered") console.warn(`knowledge outbox task ${task.id} (${task.kind}) ${outcome.result}: ${outcome.error}`);
    await deps.api.ackKnowledgeOutboxTask(task.id, outcome.result, outcome.error);
  }
  return tasks.length;
}

// Reads one document of another server for a curation with a GET that the server actor signs. Returns the JSON as it came, or an error code. Never throws. A 404 or 410 of the origin is origin_removed: the document is gone there (design Section 10.3). signal ends the read at the time limit of the task.
export async function readRemoteDocument(deps: KnowledgeReadDeps, read: RemoteRead, signal?: AbortSignal): Promise<{ document: Record<string, unknown> | null; error: string | null }> {
  const fail = (error: string) => ({ document: null, error });
  let url: URL;
  try {
    url = new URL(read.url);
  } catch {
    return fail("document_invalid");
  }
  // The scheme of a read is the scheme of this server: https, and http only for a development server.
  if (url.protocol !== new URL(deps.origin).protocol) return fail("document_invalid");
  try {
    if (await deps.isBlocked(url.hostname)) return fail("blocked_domain");
  } catch {
    return fail("claire_unavailable");
  }
  let document: Record<string, unknown> | null;
  try {
    const remote = await deps.documentLoader(url.href, { signal: requestSignal(signal) });
    if (new URL(remote.documentUrl).origin !== url.origin) return fail("document_on_other_server");
    document = record(remote.document);
  } catch (error) {
    const status = failedStatus(error);
    return fail(status === 404 || status === 410 ? "origin_removed" : "document_unavailable");
  }
  if (document === null) return fail("document_invalid");
  if (new TextEncoder().encode(JSON.stringify(document)).byteLength > REMOTE_READ_BYTES) return fail("document_too_large");
  return { document, error: null };
}

// Takes the reads for curations that wait from Claire and does them. Returns the number of reads that Claire gave.
export async function processRemoteReads(deps: KnowledgeReadDeps, counters: KnowledgeDeliveryCounters, limit = KNOWLEDGE_READ_BATCH, taskMilliseconds = TASK_SECONDS * 1000): Promise<number> {
  const reads = await deps.api.knowledgeRemoteReads(limit);
  for (const read of reads) {
    const result = await withDeadline((signal) => readRemoteDocument(deps, read, signal), taskMilliseconds, { document: null, error: "read_timeout" });
    counters[result.error === null ? "reads_ok" : "reads_failed"] += 1;
    if (result.error !== null) console.warn(`knowledge remote read ${read.id} failed: ${result.error}`);
    await deps.api.recordRemoteRead({ id: read.id, ...result });
  }
  return reads.length;
}
