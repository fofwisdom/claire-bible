// Outbound interactions of the sidecar (contract 1.6, unit FP4-9): Create, Update, and Delete of a Note, Like, Announce, their Undo, the object path of a note, and the 410 of a deleted note.
// All servers are in this process. The remote server answers WebFinger and actor documents to a GET and captures each POST (a delivery to a local test inbox).

import assert from "node:assert/strict";
import { MemoryKvStore } from "@fedify/fedify";
import { Endpoints, Group, Person } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const NOTE = "816412345678901240";
const NOTE_URL = `${ORIGIN}/ap/objects/${NOTE}`;
const GONE = "816412345678901241";
const GONE_URL = `${ORIGIN}/ap/objects/${GONE}`;
const CAROL = "https://remote.example/users/carol";
const DAN = "https://other.example/users/dan";
const PARENT = `${CAROL}/notes/1`;
const PREFIX = "/internal/federation/v1";
const AS2 = "application/activity+json";
const PUBLIC = "https://www.w3.org/ns/activitystreams#Public";

interface Delivery {
  url: string;
  body: Record<string, unknown>;
}

async function remoteActor(iri: string): Promise<unknown> {
  const url = new URL(iri);
  const name = iri.split("/").pop() ?? "";
  const values = { id: url, preferredUsername: name, name: name.charAt(0).toUpperCase() + name.slice(1), inbox: new URL(`${iri}/inbox`), endpoints: new Endpoints({ sharedInbox: new URL(`${url.origin}/inbox`) }) };
  return await (url.pathname.startsWith("/c/") ? new Group(values) : new Person(values)).toJsonLd();
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const deliveries: Delivery[] = [];
  // Hosts whose actors and WebFinger do not answer.
  const down = new Set<string>();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (request.method === "GET" && down.has(url.hostname)) return new Response("not found", { status: 404 });
    if (request.method === "GET" && url.pathname === "/.well-known/webfinger") {
      const [user, host] = (url.searchParams.get("resource") ?? "").replace(/^acct:/, "").split("@");
      const jrd = { subject: `acct:${user}@${host}`, links: [{ rel: "self", type: AS2, href: `https://${host}/users/${user}` }] };
      return new Response(JSON.stringify(jrd), { headers: { "content-type": "application/jrd+json" } });
    }
    if (request.method === "GET") return new Response(JSON.stringify(await remoteActor(request.url.split("#")[0])), { headers: { "content-type": AS2 } });
    deliveries.push({ url: request.url, body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  // The followers of the fixtures as one page: u3 and u1 have the shared inbox of remote.example, u2 has a personal inbox only.
  const followers = [...(fixture("followers").response.items as unknown[]), ...(fixture("followers_next").response.items as unknown[])];
  const setFollowers = (items: unknown[]) => fake.overrides.set(`GET ${PREFIX}/actors/${ALICE}/followers?limit=80`, { status: 200, response: { public_id: ALICE, total: items.length, items, next_max_id: null } });
  setFollowers(followers);
  const setTasks = (tasks: unknown[]) => fake.overrides.set(`GET ${PREFIX}/interaction-tasks?limit=10`, { status: 200, response: { tasks } });
  const setAck = (id: number, status = "handed_off") => fake.overrides.set(`POST ${PREFIX}/interaction-tasks/${id}/ack`, { status: 200, response: { id, status } });
  const setNote = (changes: Record<string, unknown>) => fake.overrides.set(`GET ${PREFIX}/notes/${NOTE}`, { status: 200, response: { ...fixture("note").response, ...changes } });
  const get = (url: string, accept = AS2) => sidecar.handle(new Request(url, { headers: { accept } }));
  const urls = (type: string) => deliveries.filter((d) => d.body.type === type).map((d) => d.url).sort();
  return { fake, sidecar, deliveries, down, setFollowers, setTasks, setAck, setNote, get, urls, restore: () => (globalThis.fetch = realFetch) };
}

function task(id: number, kind: string, changes: Record<string, unknown> = {}) {
  return { id, kind, actor_public_id: ALICE, ref_id: NOTE, object_iri: null, recipients: [], created_at: 1800000371.0, lease_until: 1800000700.0, ...changes };
}

function tags(object: Record<string, unknown>): Record<string, unknown>[] {
  return (Array.isArray(object.tag) ? object.tag : [object.tag]) as Record<string, unknown>[];
}

Deno.test("a reply arrives at the test inbox with inReplyTo and a Mention; the other tasks of the fixture become Delete, Like, and Announce", async () => {
  const { fake, sidecar, deliveries, setAck, urls, restore } = setup();
  try {
    // The tasks of the contract fixture: a reply of alice to a note of carol, a deleted note, and a favourite and a boost of the note of carol.
    for (const id of [11, 12, 13]) setAck(id);
    assert.equal(await sidecar.processInteractionTasks(), 4);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 4, rejected: 0, failed: 0 });
    // The sidecar resolved the handle of dan with WebFinger and gives the actor to Claire.
    assert.deepEqual(fake.interactionAcks[0], { id: 10, ...(fixture("interaction_task_ack").request.body as object) });
    assert.deepEqual(fake.interactionAcks.slice(1), [11, 12, 13].map((id) => ({ id, result: "handed_off", error_code: null, mentions: [] })));

    // Completion criterion: the Create gets to the inbox of the server of carol (the author of the parent), to the followers, and to the server of dan.
    assert.deepEqual(urls("Create"), ["https://other.example/inbox", "https://remote.example/inbox", "https://remote.example/users/u2/inbox"]);
    const create = deliveries.find((d) => d.body.type === "Create" && d.url === "https://remote.example/inbox")!.body;
    assert.equal(create.id, `${NOTE_URL}#create`);
    assert.equal(create.actor, ALICE_URL);
    assert.equal(create.to, PUBLIC);
    assert.deepEqual(create.cc, [`${ALICE_URL}/followers`, CAROL, DAN]);
    const note = create.object as Record<string, unknown>;
    const record = fixture("note").response;
    assert.equal(note.id, NOTE_URL);
    assert.equal(note.type, "Note");
    assert.equal(note.attributedTo, ALICE_URL);
    assert.equal(note.inReplyTo, PARENT);
    assert.equal(note.to, PUBLIC);
    assert.deepEqual(note.cc, [`${ALICE_URL}/followers`, CAROL, DAN]);
    assert.deepEqual(tags(note), [
      { type: "Mention", href: CAROL, name: "@carol@remote.example" },
      { type: "Mention", href: DAN, name: "@dan@other.example" },
      { type: "Hashtag", href: `${ORIGIN}/tags/claire`, name: "#Claire" },
    ]);
    // Content warning, and content with the language map (RT8). The HTML is the one that Claire made.
    assert.equal(note.summary, "Greeting");
    assert.equal(note.sensitive, true);
    assert.equal(note.content, record.content);
    assert.deepEqual(note.contentMap, { en: record.content });
    assert.equal(note.published, "2027-01-15T08:06:10Z");
    assert.ok(!("updated" in note));

    // Delete of the deleted note: a Tombstone, to the followers and to the actor that Claire gives.
    assert.deepEqual(urls("Delete"), ["https://remote.example/inbox", "https://remote.example/users/u2/inbox"]);
    const del = deliveries.find((d) => d.body.type === "Delete")!.body;
    assert.equal(del.id, `${GONE_URL}#delete`);
    assert.deepEqual(del.object, { id: GONE_URL, type: "Tombstone" });
    assert.ok(!fake.calls.includes(`GET ${PREFIX}/notes/${GONE}`));

    // Like: to the author only, with the number of the task in the ID.
    assert.deepEqual(urls("Like"), ["https://remote.example/inbox"]);
    const like = deliveries.find((d) => d.body.type === "Like")!.body;
    assert.deepEqual([like.id, like.actor, like.object, like.to], [`${ALICE_URL}#likes/12`, ALICE_URL, PARENT, CAROL]);

    // Announce: public, to the followers and the author.
    assert.deepEqual(urls("Announce"), ["https://remote.example/inbox", "https://remote.example/users/u2/inbox"]);
    const announce = deliveries.find((d) => d.body.type === "Announce")!.body;
    assert.deepEqual([announce.id, announce.object, announce.to], [`${ALICE_URL}#announces/13`, PARENT, PUBLIC]);
    assert.deepEqual(announce.cc, [`${ALICE_URL}/followers`, CAROL]);
  } finally {
    restore();
  }
});

Deno.test("an edited note becomes Update with updated, and a reply to a thread of a group goes to the group", async () => {
  const { fake, sidecar, deliveries, setTasks, setAck, setNote, setFollowers, urls, restore } = setup();
  try {
    setFollowers([]);
    const group = "https://forum.example/c/rust";
    setNote({ audience: group, mentions: [{ handle: "@carol@remote.example", iri: CAROL }], updated_at: 1800000390.5, edited_at: 1800000390.5, language: null, summary: null, sensitive: false });
    setTasks([task(20, "update_note")]);
    setAck(20);
    assert.equal(await sidecar.processInteractionTasks(), 1);
    assert.deepEqual(fake.interactionAcks, [{ id: 20, result: "handed_off", error_code: null, mentions: [] }]);
    assert.deepEqual(urls("Update"), ["https://forum.example/inbox", "https://remote.example/inbox"]);
    const update = deliveries[0].body;
    assert.equal(update.id, `${NOTE_URL}#updates/1800000390500`);
    const note = update.object as Record<string, unknown>;
    assert.equal(note.updated, "2027-01-15T08:06:30.5Z");
    assert.equal(note.audience, group);
    assert.deepEqual(note.cc, [`${ALICE_URL}/followers`, CAROL, group]);
    // Without a language the Note has no language map.
    assert.ok(!("contentMap" in note) && !("summary" in note));
  } finally {
    restore();
  }
});

Deno.test("a handle that does not resolve and an actor on a blocked domain get no tag and no delivery", async () => {
  const { fake, sidecar, deliveries, down, setTasks, setAck, setNote, setFollowers, urls, restore } = setup();
  try {
    setFollowers([]);
    down.add("other.example");
    setNote({ mentions: [{ handle: "@carol@remote.example", iri: CAROL }, { handle: "@dan@other.example", iri: null }, { handle: "@mallory@blocked.example", iri: null }, { handle: "@eve@blocked.example", iri: "https://blocked.example/users/eve" }] });
    setTasks([task(21, "note")]);
    setAck(21);
    assert.equal(await sidecar.processInteractionTasks(), 1);
    // The note is sent without the two mentions, and no actor goes to Claire.
    assert.deepEqual(fake.interactionAcks, [{ id: 21, result: "handed_off", error_code: null, mentions: [] }]);
    assert.deepEqual(urls("Create"), ["https://remote.example/inbox"]);
    const note = deliveries[0].body.object as Record<string, unknown>;
    assert.deepEqual(tags(note).filter((t) => t.type === "Mention").map((t) => t.href), [CAROL]);
    assert.ok(!JSON.stringify(deliveries).includes("blocked.example/users"));
  } finally {
    restore();
  }
});

Deno.test("an Undo has the ID of the activity that it undoes, and a task that cannot be sent is rejected", async () => {
  const { fake, sidecar, deliveries, down, setTasks, setAck, setFollowers, urls, restore } = setup();
  try {
    setFollowers([]);
    const remote = { object_iri: PARENT, recipients: [CAROL] };
    setTasks([
      task(30, "undo_like", { ...remote, ref_id: "12" }),
      task(31, "undo_announce", { ...remote, ref_id: "13" }),
      task(32, "like", { ref_id: "32", object_iri: "https://blocked.example/notes/1", recipients: ["https://blocked.example/users/eve"] }),
      task(33, "like", { ref_id: "33", object_iri: "https://other.example/notes/1", recipients: [DAN] }),
      task(34, "note", { ref_id: GONE }),
      task(35, "note", { ref_id: "816412345678901999" }),
    ]);
    down.add("other.example");
    for (const id of [30, 31]) setAck(id);
    for (const id of [32, 33, 34, 35]) setAck(id, "rejected");
    assert.equal(await sidecar.processInteractionTasks(), 6);
    assert.deepEqual(fake.interactionAcks.map((a) => `${a.id} ${a.result} ${a.error_code}`), ["30 handed_off null", "31 handed_off null", "32 rejected blocked_domain", "33 rejected actor_not_found", "34 rejected note_gone", "35 rejected note_not_found"]);
    assert.deepEqual(urls("Undo"), ["https://remote.example/inbox", "https://remote.example/inbox"]);
    const [undoLike, undoAnnounce] = deliveries.map((d) => d.body);
    assert.equal(undoLike.id, `${ALICE_URL}#likes/12/undo`);
    const like = undoLike.object as Record<string, unknown>;
    assert.deepEqual([like.type, like.id, like.object], ["Like", `${ALICE_URL}#likes/12`, PARENT]);
    assert.equal(undoAnnounce.id, `${ALICE_URL}#announces/13/undo`);
    const announce = undoAnnounce.object as Record<string, unknown>;
    assert.deepEqual([announce.type, announce.id, announce.object], ["Announce", `${ALICE_URL}#announces/13`, PARENT]);
    assert.equal(deliveries.length, 2);
  } finally {
    restore();
  }
});

Deno.test("the object path gives the Note, a deleted note gives 410 with a Tombstone, and a published document stays an Article", async () => {
  const { get, restore } = setup();
  try {
    const response = await get(NOTE_URL);
    assert.equal(response.status, 200);
    const note = await response.json();
    assert.deepEqual([note.id, note.type, note.attributedTo, note.inReplyTo, note.url], [NOTE_URL, "Note", ALICE_URL, PARENT, NOTE_URL]);
    // The object path makes no network request: only the mention that Claire knows is a tag.
    assert.deepEqual(tags(note).map((t) => t.type), ["Mention", "Hashtag"]);
    assert.deepEqual(note.cc, [`${ALICE_URL}/followers`, CAROL]);

    // Completion criterion: a deleted note gives 410.
    const gone = await get(GONE_URL);
    assert.equal(gone.status, 410);
    assert.equal(gone.headers.get("content-type"), AS2);
    assert.deepEqual(await gone.json(), { "@context": "https://www.w3.org/ns/activitystreams", id: GONE_URL, type: "Tombstone", formerType: "Note", deleted: "2027-01-15T08:06:20Z" });

    const article = await (await get(`${ORIGIN}/ap/objects/816412345678901234`)).json();
    assert.equal(article.type, "Article");
    const retracted = await get(`${ORIGIN}/ap/objects/816412345678901239`);
    assert.equal(retracted.status, 410);
    assert.equal((await retracted.json()).formerType, "Article");
    assert.equal((await get(`${ORIGIN}/ap/objects/816412345678901999`)).status, 404);
    assert.equal((await get(NOTE_URL, "text/html")).status, 406);
  } finally {
    restore();
  }
});
