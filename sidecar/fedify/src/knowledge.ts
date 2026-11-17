// Federation of shared knowledge in the sidecar (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2, 8.1, 9.1, 13, and 14; units KF1 and KF2).
//
// Rules of this module:
// - The context document https://w3id.org/claire-bible/ns/v1 is part of the code (design Section 9.1 rule 2). The file claire_context_v1.json is a copy of src/claire/static/ns/v1.jsonld of Claire; a Python test fails when the two are different. This module puts the document in the table of contexts that the document loaders of Fedify read before they make a request. Thus no loader of Fedify gets the context from the network.
// - An activity of the knowledge federation is an activity whose @context has the Claire context IRI. Claire reads the compact names of the wire contract, so such an activity goes to Claire as the JSON that came, without a new compaction: the sidecar parses the body and does not give it to the vocabulary of Fedify.
// - A collection of shared knowledge (/ap/kb, /ap/themes) answers only a request with a valid HTTP signature. The sidecar verifies the signature and gives Claire the actor of the key; Claire decides (the switch, the server actor rule, the domain blocks). Each refusal of Claire is the same 404.
// - The reads of other servers (NodeInfo, the server actor, the /kb collection) use the document loader of Fedify with the key of the server actor of this server: each request is signed, and the loader refuses a private network address. Claire says which servers are due; the sidecar keeps no list.
// - A document of another server is not trusted: the server actor and each page of a collection must come from the server that was asked. Claire checks each knowledge base again (vocabulary.validate_object).

import type { DocumentLoader } from "@fedify/fedify";
import { preloadedContexts } from "jsr:@fedify/vocab-runtime@^2.4.5";
import claireContext from "./claire_context_v1.json" with { type: "json" };
import type { InternalApi, KbRead, ServerReadResult } from "./internal_api.ts";

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

// Reads NodeInfo and the server actor of one known server (design Section 6.2). Never throws: a read that fails gives a result with an error code.
export async function readServer(deps: KnowledgeReadDeps, domain: string): Promise<ServerReadResult> {
  if (await deps.isBlocked(domain)) return failure(domain, "blocked_domain");
  const root = base(deps, domain);
  let software: Record<string, unknown> | null;
  try {
    const links = record((await deps.documentLoader(`${root}/.well-known/nodeinfo`)).document)?.links;
    const link = Array.isArray(links) ? links.map(record).find((item) => item !== null && NODEINFO_RELS.includes(String(item.rel)) && typeof item.href === "string") : undefined;
    if (link === undefined || link === null) return failure(domain, "nodeinfo_invalid");
    const href = new URL(String(link.href));
    // The NodeInfo document is on the server that was asked.
    if (href.origin !== root) return failure(domain, "nodeinfo_invalid");
    software = record(record((await deps.documentLoader(href.href)).document)?.software);
  } catch {
    return failure(domain, "nodeinfo_unavailable");
  }
  const name = software?.name;
  const version = software?.version;
  if (typeof name !== "string" || typeof version !== "string" || name.length < 1 || version.length < 1 || name.length > MAX_SOFTWARE_LENGTH || version.length > MAX_SOFTWARE_LENGTH) return failure(domain, "nodeinfo_invalid");
  const actorUrl = `${root}${SERVER_ACTOR_PATH}`;
  let actor: Record<string, unknown> | null;
  try {
    const remote = await deps.documentLoader(actorUrl);
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
  if (await deps.isBlocked(start.hostname)) return await fail("blocked_domain");
  const load = async (url: string): Promise<Record<string, unknown> | string> => {
    try {
      const remote = await deps.documentLoader(url);
      if (new URL(remote.documentUrl).origin !== start.origin) return "page_on_other_server";
      return record(remote.document) ?? "collection_invalid";
    } catch {
      return "collection_unavailable";
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
    const result = await readServer(deps, domain);
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
