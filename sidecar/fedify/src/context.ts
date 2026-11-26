// Thread fetch of the sidecar (FEDERATION_REMOTE_READING_DESIGN.md Section 6.5 rules 4 and 5, contract 1.5, unit FP4-6).
//
// Rules of this module:
// - A thread fetch task has no activity. The sidecar reads the FEP-7888 `context` collection of the start object (FEP-f228) with `@fedify/backfill`. When that collection gives no object, it reads the reply tree: the `inReplyTo` parents and the `replies` collections.
// - The limits of one task: `max_items` objects (`context_max_items`), `max_depth` levels of the reply tree (`context_depth`), a number of requests (requestLimit), and a time (TASK_SECONDS).
// - Each request uses the document loader of Fedify with the key of the account. That loader refuses a private network address, as for the other fetches of the sidecar. No request goes to this server or to a blocked domain.
// - An object in a collection is not trusted as it is: a collection of one server can contain an object with the ID of another server. Each object that goes to Claire comes from a document on the origin of its own ID, and its author is on that origin too.
// - Only a public post goes to Claire, as the normal form of Section 6.3. Claire makes the final decision for each object.

import { backfill, type BackfillStrategy } from "@fedify/backfill";
import type { Context, DocumentLoader } from "@fedify/fedify";
import { isActor, Object as ASObject } from "@fedify/fedify/vocab";
import type { ContextTask, InternalApi, TimelineActor, TimelineObject } from "./internal_api.ts";
import { actorProfile, isPost, isPublic, normalObject } from "./timeline.ts";

// The number of thread fetch tasks that one poll takes. A task can make many requests.
export const CONTEXT_TASK_BATCH = 2;
// The time of one task. It is less than the lease of Claire (300 seconds), so that the acknowledgement comes before the lease ends.
export const TASK_SECONDS = 120;
// The limits of one `context-objects` request. The body limit of the internal API is 1 MiB.
export const OBJECTS_BATCH = 50;
export const OBJECTS_BATCH_BYTES = 512 * 1024;

// The largest number of requests of one task: the collection pages and the objects (one for each item), a second read of an object that was in a collection document, and the author documents.
export function requestLimit(maxItems: number): number {
  return 3 * maxItems + 20;
}

export interface ThreadFetchOptions {
  // Signed loader of Fedify. It refuses a private network address.
  documentLoader: DocumentLoader;
  contextLoader: DocumentLoader;
  // The canonical origin of this server.
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  maxItems: number;
  maxDepth: number;
  // Unix time in milliseconds after which no request starts.
  deadline?: number;
}

// The public posts of the thread of the object `objectIri`, without that object, `maxItems` at most. "object_not_found": the start object is not an available post.
// The result of a walk: the public posts in the normal form, and the start addresses that are gone at their origin (a 404 or 410).
export interface WalkResult {
  objects: TimelineObject[];
  gone: string[];
}

// Walks from start posts with @fedify/backfill and gives the public posts that it found (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B4 and F).
// Each start post is read from its origin; the walk reads the FEP-7888 `context` collection of each one and its reply tree, and merges the two (Section 5.11 F: Mastodon gives a `context` that is not a useful collection and a complete reply tree).
// includeStarts: the start posts are in the result too (the opinions of an own object start from the replies that this server knows, and a second read refreshes them).
// readIris: posts that are read and put in the result without a walk (the quote posts of an own object: their replies are not opinions of the object, Section 5.11 B4).
export async function walkPosts(startIris: string[], options: ThreadFetchOptions, includeStarts: boolean, readIris: string[] = []): Promise<WalkResult | "object_not_found"> {
  const maxRequests = requestLimit(options.maxItems);
  let requests = 0;
  const gone: string[] = [];
  // The objects that came from a document on the origin of their own ID. The key is the ID.
  const direct = new Map<string, ASObject>();
  // Never throws: a request that is not permitted or that fails gives null.
  const load = async (iri: URL): Promise<ASObject | null> => {
    if (requests >= maxRequests || (options.deadline !== undefined && Date.now() > options.deadline)) return null;
    if (iri.protocol !== "https:" && iri.protocol !== "http:") return null;
    try {
      if (iri.origin === options.origin || await options.isBlocked(iri.hostname)) return null;
      requests += 1;
      const remote = await options.documentLoader(iri.href);
      const object = await ASObject.fromJsonLd(remote.document, { documentLoader: options.documentLoader, contextLoader: options.contextLoader });
      if (object.id !== null && object.id.origin === new URL(remote.documentUrl).origin) direct.set(object.id.href, object);
      // backfill gets its own instance: when it resolves a link that cannot be read (for example the reply target on this server), Fedify keeps an empty value on that instance, and the normal form would lose `inReplyTo`.
      return await ASObject.fromJsonLd(remote.document, { documentLoader: options.documentLoader, contextLoader: options.contextLoader });
    } catch (error) {
      const status = (error as { response?: unknown } | null)?.response instanceof Response ? ((error as { response: Response }).response.status) : null;
      if (status === 404 || status === 410) gone.push(iri.href);
      return null;
    }
  };

  const parse = (raw: string): URL | null => {
    try {
      return new URL(raw);
    } catch {
      return null;
    }
  };
  const seeds: ASObject[] = [];
  for (const raw of startIris) {
    const start = parse(raw);
    if (start === null) continue;
    const seed = await load(start);
    if (isPost(seed) && direct.has(start.href)) seeds.push(seed);
  }
  if (seeds.length === 0 && !includeStarts) return "object_not_found";

  const found = new Map<string, URL>();
  if (includeStarts) for (const seed of seeds) if (seed.id !== null) found.set(seed.id.href, seed.id);
  for (const raw of readIris) {
    const iri = parse(raw);
    if (iri === null || found.size >= options.maxItems) continue;
    if (isPost(await load(iri)) && direct.has(iri.href)) found.set(iri.href, iri);
  }
  for (const seed of seeds) {
    for (const strategy of ["context-auto", "reply-tree"] as BackfillStrategy[]) {
      const left = options.maxItems - found.size;
      if (left < 1 || requests >= maxRequests) break;
      for await (const item of backfill({ documentLoader: load }, seed, { strategies: [strategy], maxItems: left, maxDepth: options.maxDepth, maxRequests: maxRequests - requests })) {
        if (item.id !== undefined && !(seed.id !== null && item.id.href === seed.id.href && !includeStarts)) found.set(item.id.href, item.id);
      }
    }
  }

  const authors = new Map<string, TimelineActor>();
  const objects: TimelineObject[] = [];
  for (const id of found.values()) {
    if (!direct.has(id.href)) await load(id);
    const post = direct.get(id.href);
    if (!isPost(post) || !isPublic(post)) continue;
    const authorId = post.attributionId;
    // The server of the object makes the statement about the author: an author on another origin is not accepted.
    if (authorId === null || authorId.origin !== id.origin) continue;
    let author = authors.get(authorId.href);
    if (author === undefined) {
      const actor = direct.get(authorId.href) ?? await load(authorId);
      // When the actor document is not available, the profile has the ID only.
      author = (isActor(actor) && actor.id?.href === authorId.href ? actorProfile(actor) : null) ?? { iri: authorId.href, name: null, handle: null };
      authors.set(authorId.href, author);
    }
    const normal = await normalObject(post, author, { contextLoader: options.contextLoader });
    if (typeof normal !== "string") objects.push(normal);
  }
  return { objects, gone: gone.filter((iri) => startIris.includes(iri) || readIris.includes(iri)) };
}

// The thread of one remote post for the timeline of an account: the walk from that post, without the post itself.
export async function fetchThread(objectIri: string, options: ThreadFetchOptions): Promise<TimelineObject[] | "object_not_found"> {
  const result = await walkPosts([objectIri], options, false);
  return result === "object_not_found" ? result : result.objects;
}

// The parts of a list of objects for `context-objects` requests: OBJECTS_BATCH objects and OBJECTS_BATCH_BYTES bytes at most in each part (one object can be larger than the byte limit; then it is alone).
export function objectBatches(objects: TimelineObject[]): TimelineObject[][] {
  const batches: TimelineObject[][] = [];
  let current: TimelineObject[] = [];
  let size = 0;
  for (const object of objects) {
    const bytes = new TextEncoder().encode(JSON.stringify(object)).byteLength;
    if (current.length > 0 && (current.length >= OBJECTS_BATCH || size + bytes > OBJECTS_BATCH_BYTES)) {
      batches.push(current);
      current = [];
      size = 0;
    }
    current.push(object);
    size += bytes;
  }
  if (current.length > 0) batches.push(current);
  return batches;
}

export interface ContextTaskDeps {
  api: InternalApi;
  ctx: Context<void>;
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  counters: { handed_off: number; rejected: number; failed: number };
}

// Takes the ready thread fetch tasks from Claire, fetches each thread, and gives the objects to Claire. Returns the number of tasks that Claire gave.
// An error in one task does not stop the other tasks. A task without an acknowledgement comes again after its lease.
export async function processContextTasks(deps: ContextTaskDeps, limit = CONTEXT_TASK_BATCH): Promise<number> {
  const tasks = await deps.api.contextTasks(limit);
  for (const task of tasks) {
    try {
      deps.counters[await runTask(deps, task)] += 1;
    } catch (error) {
      deps.counters.failed += 1;
      console.error(`context task ${task.id} failed: ${(error as Error).name}: ${(error as Error).message}`);
    }
  }
  return tasks.length;
}

async function runTask(deps: ContextTaskDeps, task: ContextTask): Promise<"handed_off" | "rejected"> {
  const reject = async (code: string): Promise<"rejected"> => {
    console.warn(`context task ${task.id} rejected: ${code}`);
    await deps.api.ackClientTask(task.id, "rejected", code);
    return "rejected";
  };
  // The fetch is signed with the key of the account (servers with authorized fetch).
  const sender = { identifier: task.actor_public_id };
  if ((await deps.ctx.getActorKeyPairs(task.actor_public_id)).length === 0) return await reject("no_actor_key");
  const documentLoader = await deps.ctx.getDocumentLoader(sender);
  const deadline = Date.now() + TASK_SECONDS * 1000;
  const objects = await fetchThread(task.object_iri, { documentLoader, contextLoader: deps.ctx.contextLoader, origin: deps.origin, isBlocked: deps.isBlocked, maxItems: task.max_items, maxDepth: task.max_depth, deadline });
  if (objects === "object_not_found") return await reject("object_not_found");
  for (const batch of objectBatches(objects)) {
    const answer = await deps.api.contextObjects(task.id, batch);
    // null: Claire has no such task that waits (for example a second run after the lease). There is nothing to acknowledge.
    if (answer === null) return "rejected";
    if (answer.decision === "rejected") return await reject(answer.reason ?? "rejected");
  }
  await deps.api.ackClientTask(task.id, "handed_off");
  return "handed_off";
}
