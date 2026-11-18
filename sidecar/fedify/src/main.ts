// Entry point of the Fedify sidecar (design Section 4.2).
//
// Environment variables:
//   CLAIRE_FEDIFY_SOCKET            Unix socket of the Claire internal federation API. Default: /data/run/federation.sock
//   CLAIRE_FEDIFY_SERVICE_KEY_FILE  Service key file (32 bytes). Default: /data/auth/fedify_service.key
//   CLAIRE_FEDIFY_DB                Sidecar database (cache and queue). Default: /data/fedify/fedify.db
//   CLAIRE_FEDIFY_HOST              Listen address. Default: 0.0.0.0
//   CLAIRE_FEDIFY_PORT              Listen port. Default: 8790
//
// Permission flags: --allow-net (public listener, deliveries, the socket, and the placeholder host claire.internal:80),
// --allow-read for the service key file, --allow-read and --allow-write for the database directory and the socket path
// (Deno requires write access to a Unix socket to connect to it), --allow-env for the variables above.

import { DatabaseSync } from "node:sqlite";
import { SqliteKvStore, SqliteMessageQueue } from "@fedify/sqlite";
import { CONTEXT_TASK_BATCH } from "./context.ts";
import { CONTRACT_VERSION, InternalApi } from "./internal_api.ts";
import { KNOWLEDGE_OUTBOX_BATCH, KNOWLEDGE_READ_BATCH } from "./knowledge.ts";
import { OUTBOX_TASK_BATCH, publicSelfCheck, Sidecar } from "./sidecar.ts";

export const SIDECAR_VERSION = "0.1.0";
const HEARTBEAT_SECONDS = 30;
// The interval between two reads of the outbox tasks of Claire (design Section 9.3).
const OUTBOX_POLL_SECONDS = 5;
// The interval between two reads of the knowledge federation reads that are due.
const KNOWLEDGE_POLL_SECONDS = 60;

function env(name: string, fallback: string): string {
  return Deno.env.get(name) || fallback;
}

function openDatabase(path: string): DatabaseSync {
  // The database is a secret store while a delivery waits (design Section 7 item 5). SQLite gives the same mode to the WAL files.
  try {
    Deno.openSync(path, { write: true, createNew: true, mode: 0o600 }).close();
  } catch (error) {
    if (!(error instanceof Deno.errors.AlreadyExists)) throw error;
    Deno.chmodSync(path, 0o600);
  }
  const db = new DatabaseSync(path);
  db.exec("PRAGMA journal_mode=WAL;");
  return db;
}

function queueDepth(db: DatabaseSync): number {
  try {
    return Number(db.prepare('SELECT COUNT(*) AS n FROM "fedify_message"').get()?.n ?? 0);
  } catch {
    // The queue table does not exist before the first message.
    return 0;
  }
}

if (import.meta.main) {
  const socketPath = env("CLAIRE_FEDIFY_SOCKET", "/data/run/federation.sock");
  const key = Deno.readFileSync(env("CLAIRE_FEDIFY_SERVICE_KEY_FILE", "/data/auth/fedify_service.key"));
  const db = openDatabase(env("CLAIRE_FEDIFY_DB", "/data/fedify/fedify.db"));
  const client = Deno.createHttpClient({ proxy: { transport: "unix", path: socketPath } });
  const api = new InternalApi({ key, transport: (request) => fetch(request, { client }) });
  const sidecar = new Sidecar({ api, kv: new SqliteKvStore(db), queue: new SqliteMessageQueue(db) });

  const beat = async () => {
    try {
      const config = await sidecar.config();
      // The self-checks run also while federation is off: GF6, GF8, and GF9 must pass before the admin sets the switch to `on`.
      const [ok, knowledgeSelfCheck] = await Promise.all([
        publicSelfCheck(config, sidecar.marker),
        sidecar.knowledgeSelfCheck(config),
      ]);
      const selfCheck = { ok, checked_at: Date.now() / 1000 };
      await api.heartbeat({
        contract_version: CONTRACT_VERSION,
        sidecar_version: SIDECAR_VERSION,
        queue_depth: queueDepth(db),
        self_check: selfCheck,
        knowledge_self_check: knowledgeSelfCheck,
        loops: sidecar.loops(),
      });
    } catch (error) {
      console.error(`heartbeat failed: ${(error as Error).message}`);
    }
  };

  // One poll at a time. An error (for example: Claire is not available) stops this poll only.
  let polling = false;
  const poll = async () => {
    if (polling) return;
    polling = true;
    try {
      // A full batch can mean that more tasks are ready.
      while ((await sidecar.processOutboxTasks()) >= OUTBOX_TASK_BATCH);
      // The client tasks (contract 1.3): follows, unfollows, and removed followers.
      while ((await sidecar.processClientTasks()) >= OUTBOX_TASK_BATCH);
      // The outbound interactions (contract 1.6): notes, favourites, and boosts.
      while ((await sidecar.processInteractionTasks()) >= OUTBOX_TASK_BATCH);
    } catch (error) {
      console.error(`outbox poll failed: ${(error as Error).name}: ${(error as Error).message}`);
    } finally {
      polling = false;
    }
  };
  poll();
  setInterval(poll, OUTBOX_POLL_SECONDS * 1000);

  // The thread fetches (contract 1.5) have their own loop: one fetch can make many requests, and it must not delay the other tasks.
  let fetching = false;
  const pollContext = async () => {
    if (fetching) return;
    fetching = true;
    try {
      while ((await sidecar.processContextTasks()) >= CONTEXT_TASK_BATCH);
    } catch (error) {
      console.error(`context poll failed: ${(error as Error).name}: ${(error as Error).message}`);
    } finally {
      fetching = false;
    }
  };
  pollContext();
  setInterval(pollContext, OUTBOX_POLL_SECONDS * 1000);

  // The reads of the knowledge federation (KNOWLEDGE_FEDERATION_DESIGN.md Sections 6.2 and 8.1) have their own loop: Claire gives a server one time each day, and one read can make many requests.
  const pollKnowledge = async () => {
    try {
      await sidecar.runKnowledgeReads();
    } catch (error) {
      console.error(`knowledge read poll failed: ${(error as Error).name}: ${(error as Error).message}`);
    }
  };
  const initialKnowledgeReads = pollKnowledge();
  setInterval(pollKnowledge, KNOWLEDGE_POLL_SECONDS * 1000);

  // The outbound activities of the knowledge federation and the reads for curations (unit KF3) have their own loop: a reviewer waits for a read, and one delivery can wait for another server.
  const pollKnowledgeExchange = async () => {
    try {
      await sidecar.runKnowledgeExchange();
    } catch (error) {
      console.error(`knowledge exchange poll failed: ${(error as Error).name}: ${(error as Error).message}`);
    }
  };
  const initialKnowledgeExchange = pollKnowledgeExchange();
  setInterval(pollKnowledgeExchange, OUTBOX_POLL_SECONDS * 1000);

  void (async () => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      Promise.allSettled([initialKnowledgeReads, initialKnowledgeExchange]),
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, 5_000);
      }),
    ]);
    clearTimeout(timer);
    await beat();
  })();
  setInterval(beat, HEARTBEAT_SECONDS * 1000);

  Deno.serve({ hostname: env("CLAIRE_FEDIFY_HOST", "0.0.0.0"), port: Number(env("CLAIRE_FEDIFY_PORT", "8790")) }, (request) => sidecar.handle(request));
}
