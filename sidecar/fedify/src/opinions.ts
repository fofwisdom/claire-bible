// Fetch of the opinions of a published object (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 B, phase E7a, contract 1.23).
//
// - The server actor signs each request: the opinions belong to the object, not to an account (rule B8).
// - An object of this server: the walk starts from the replies that Claire knows, also the replies of this server (rule B3; since RD30 the sidecar reads its own origin through its loopback) and reads the reply tree and the `context` of each one (rule B4). The quote posts that Claire knows are read without a walk: their replies are not opinions of the object (rule B4). The start posts and the quote posts are in the result: a second read refreshes them, and a 404 or 410 of one tells Claire that it is gone (rule B7).
// - An object of another server (a remote rendition, rule C2): the walk starts from the object.
// - Claire decides which posts are opinions (a reply to the object or to an opinion, or a quote of the object) and applies the rules of a Create for each post.

import type { Context } from "@fedify/fedify";
import { objectBatches, TASK_SECONDS, walkPosts } from "./context.ts";
import { type InternalApi, type OpinionTask, SERVER_ACTOR_ID } from "./internal_api.ts";

export const OPINION_TASK_BATCH = 2;

export interface OpinionTaskDeps {
  api: InternalApi;
  ctx: Context<void>;
  origin: string;
  isBlocked: (host: string) => Promise<boolean>;
  counters: { handed_off: number; rejected: number; failed: number };
}

// Takes the fetches of opinions that wait from Claire and does each one. Returns the number of fetches that Claire gave. An error in one fetch does not stop the others; a fetch without its last call comes again after its lease.
export async function processOpinionTasks(deps: OpinionTaskDeps, limit = OPINION_TASK_BATCH): Promise<number> {
  const tasks = await deps.api.opinionTasks(limit);
  for (const task of tasks) {
    try {
      deps.counters[await runOpinionTask(deps, task)] += 1;
    } catch (error) {
      deps.counters.failed += 1;
      console.error(`opinion task ${task.id} failed: ${(error as Error).name}: ${(error as Error).message}`);
    }
  }
  return tasks.length;
}

async function runOpinionTask(deps: OpinionTaskDeps, task: OpinionTask): Promise<"handed_off" | "rejected"> {
  if ((await deps.ctx.getActorKeyPairs(SERVER_ACTOR_ID)).length === 0) {
    console.warn(`opinion task ${task.id}: no key of the server actor`);
    return "rejected";
  }
  const documentLoader = await deps.ctx.getDocumentLoader({ identifier: SERVER_ACTOR_ID });
  const deadline = Date.now() + TASK_SECONDS * 1000;
  const options = { documentLoader, contextLoader: deps.ctx.contextLoader, origin: deps.origin, isBlocked: deps.isBlocked, maxItems: task.max_items, maxDepth: task.max_depth, deadline };
  const result = task.own ? await walkPosts(task.start, options, true, task.quotes) : await walkPosts([task.object_iri], options, false);
  const objects = result === "object_not_found" ? [] : result.objects;
  const gone = result === "object_not_found" ? [] : result.gone;
  const batches = objectBatches(objects);
  if (batches.length === 0) batches.push([]);
  for (const [index, batch] of batches.entries()) {
    const answer = await deps.api.opinionObjects(task.id, batch, index === 0 ? gone : [], index === batches.length - 1);
    // null: the fetch does not wait now (for example a second run after the lease).
    if (answer === null) return "rejected";
  }
  return "handed_off";
}
