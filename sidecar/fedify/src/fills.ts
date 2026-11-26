// Fill of recent posts after a follow (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.13, phase E10a, contract 1.24).
//
// - Claire makes one fill when a follow of an account becomes accepted. The sidecar reads the first page of the `featured` collection of the followed actor (the pinned posts, as Mastodon does), then the first page of its `outbox` (as FediFetcher does), and gives the posts to Claire.
// - The key of the account signs each read: the account follows the actor, and a server in authorized fetch mode needs a signature (rule 4).
// - The limits of one fill: one page of each collection, 60 requests, and 30 seconds (rule 7). No request goes to this server or to a blocked domain; the document loader of Fedify refuses a private address.
// - A post must be on the origin of the actor, as its collections are (rule 5). An `Announce` in the outbox is skipped (rule 3). Only a public post of the actor goes to Claire; Claire decides the rest (the retention, the replies, 20 posts).

import type { Context, DocumentLoader } from "@fedify/fedify";
import { type Collection, Create, isActor, Object as ASObject } from "@fedify/fedify/vocab";
import { objectBatches } from "./context.ts";
import type { FillTask, InternalApi, TimelineObject } from "./internal_api.ts";
import { actorProfile, isPost, isPublic, normalObject, type Post } from "./timeline.ts";

export const FILL_TASK_BATCH = 2;
export const FILL_SECONDS = 30;
const MAX_REQUESTS = 60;

export interface FillReadOptions {
  documentLoader: DocumentLoader;
  contextLoader: DocumentLoader;
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  maxItems: number;
  deadline: number;
}

// The public posts of the first pages of the `featured` collection and the `outbox` of an actor, in the normal form. An actor that is not available gives no post.
export async function readActorPosts(actorIri: string, options: FillReadOptions): Promise<TimelineObject[]> {
  let requests = 0;
  const loader: DocumentLoader = async (url, init) => {
    const iri = new URL(url);
    if (requests >= MAX_REQUESTS || Date.now() > options.deadline) throw new Error("the limit of the fill");
    if ((iri.protocol !== "https:" && iri.protocol !== "http:") || iri.origin === options.origin || await options.isBlocked(iri.hostname)) throw new Error("a request that is not permitted");
    requests += 1;
    return await options.documentLoader(url, init);
  };
  const load = { documentLoader: loader, contextLoader: options.contextLoader, suppressError: true as const };
  const home = new URL(actorIri).origin;
  let actor: unknown;
  try {
    const remote = await loader(actorIri);
    actor = await ASObject.fromJsonLd(remote.document, { documentLoader: loader, contextLoader: options.contextLoader });
  } catch {
    return [];
  }
  if (!isActor(actor) || actor.id?.href !== actorIri) return [];
  const author = actorProfile(actor) ?? { iri: actorIri, name: null, handle: null };
  const posts = new Map<string, Post>();
  const take = (object: unknown) => {
    if (isPost(object) && object.id !== null && object.id.origin === home && !posts.has(object.id.href)) posts.set(object.id.href, object);
  };
  // A collection without pages has its items; else the first page has them.
  const firstPage = async (collection: Collection | null): Promise<Collection | null> => collection === null ? null : collection.firstId === null ? collection : await collection.getFirst(load);
  const limit = 2 * options.maxItems;
  if (actor.featuredId?.origin === home) {
    const page = await firstPage(await actor.getFeatured(load));
    if (page !== null) {
      for await (const item of page.getItems(load)) {
        take(item);
        if (posts.size >= limit) break;
      }
    }
  }
  if (actor.outboxId?.origin === home && posts.size < limit) {
    const page = await firstPage(await actor.getOutbox(load));
    if (page !== null) {
      for await (const item of page.getItems(load)) {
        if (item instanceof Create && item.actorId?.href === actorIri) take(await item.getObject(load));
        if (posts.size >= limit) break;
      }
    }
  }
  const objects: TimelineObject[] = [];
  for (const post of posts.values()) {
    if (!isPublic(post) || post.attributionId?.href !== actorIri) continue;
    const normal = await normalObject(post, author, { contextLoader: options.contextLoader });
    if (typeof normal !== "string") objects.push(normal);
  }
  return objects;
}

export interface FillTaskDeps {
  api: InternalApi;
  ctx: Context<void>;
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  counters: { handed_off: number; rejected: number; failed: number };
}

// Takes the fills that wait from Claire and does each one. Returns the number of fills that Claire gave. An error in one fill does not stop the others; a fill without its last call comes again after its lease.
export async function processFillTasks(deps: FillTaskDeps, limit = FILL_TASK_BATCH): Promise<number> {
  const tasks = await deps.api.fillTasks(limit);
  for (const task of tasks) {
    try {
      deps.counters[await runFill(deps, task)] += 1;
    } catch (error) {
      deps.counters.failed += 1;
      console.error(`fill task ${task.id} failed: ${(error as Error).name}: ${(error as Error).message}`);
    }
  }
  return tasks.length;
}

async function runFill(deps: FillTaskDeps, task: FillTask): Promise<"handed_off" | "rejected"> {
  if ((await deps.ctx.getActorKeyPairs(task.actor_public_id)).length === 0) {
    console.warn(`fill task ${task.id}: no key of the account`);
    return "rejected";
  }
  const documentLoader = await deps.ctx.getDocumentLoader({ identifier: task.actor_public_id });
  const objects = await readActorPosts(task.actor_iri, { documentLoader, contextLoader: deps.ctx.contextLoader, origin: deps.origin, isBlocked: deps.isBlocked, maxItems: task.max_items, deadline: Date.now() + FILL_SECONDS * 1000 });
  const batches = objectBatches(objects);
  if (batches.length === 0) batches.push([]);
  for (const [index, batch] of batches.entries()) {
    // null: the fill does not wait now (for example a second run after the lease).
    if ((await deps.api.fillObjects(task.id, batch, index === batches.length - 1)) === null) return "rejected";
  }
  return "handed_off";
}
