// Timeline events of the sidecar (unit FP4-4): the normal form of a remote object, the public rule, the fetch of an announced object, the parent chain of a reply, and the activities that stay inbox events.
// All servers are in this process. The remote servers are a key pair, actor documents, and a table of object documents that the fetch of this file gives.

import assert from "node:assert/strict";
import { generateCryptoKeyPair, MemoryKvStore, signRequest } from "@fedify/fedify";
import { type Activity, Announce, Create, CryptographicKey, Delete, Document, Endpoints, LanguageString, Like, Mention, Note, Page, Person, PUBLIC_COLLECTION, Undo, Update } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { CONTENT_MAX_BYTES, MAX_ATTACHMENTS } from "../src/timeline.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const AS2 = "application/activity+json";
const DAN = "https://remote.example/users/dan";
const BOB = "https://remote.example/users/bob";
const ERIN = "https://forum.example/users/erin";
const PUBLISHED = Temporal.Instant.fromEpochMilliseconds(1_800_000_390_000);

const IGNORED = { decision: "ignored", reply: null, reason: "unsupported_type" };

const remoteKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");

async function remoteActor(iri: string): Promise<unknown> {
  const actor = new Person({
    id: new URL(iri),
    preferredUsername: iri.split("/").pop(),
    inbox: new URL(`${iri}/inbox`),
    endpoints: new Endpoints({ sharedInbox: new URL(`${new URL(iri).origin}/inbox`) }),
    publicKey: new CryptographicKey({ id: new URL(`${iri}#main-key`), owner: new URL(iri), publicKey: remoteKeys.publicKey }),
  });
  return await actor.toJsonLd();
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  // The object documents of the remote servers. A GET of another address gives an actor document.
  const objects = new Map<string, unknown>();
  const fetched: string[] = [];
  // The deliveries of the sidecar to the inboxes of the remote servers (unit FP4-8: the automatic notice).
  const deliveries: { url: string; body: Record<string, unknown> }[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    if (request.method === "POST") {
      deliveries.push({ url: request.url, body: JSON.parse(await request.text()) });
      return new Response(null, { status: 202 });
    }
    const url = request.url.split("#")[0];
    fetched.push(url);
    if (url.endsWith("/missing")) return new Response("Not Found", { status: 404 });
    return new Response(JSON.stringify(objects.get(url) ?? await remoteActor(url)), { headers: { "content-type": AS2 } });
  };
  const post = async (activity: Activity, path = "/ap/inbox") => {
    const document = await activity.toJsonLd() as Record<string, unknown>;
    const request = await signRequest(new Request(`${ORIGIN}${path}`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify(document) }), remoteKeys.privateKey, new URL(`${document.actor}#main-key`));
    const response = await sidecar.handle(request);
    await response.body?.cancel();
    return response.status;
  };
  const add = async (object: Note | Page) => objects.set(object.id!.href, await object.toJsonLd());
  return { fake, sidecar, post, add, objects, fetched, deliveries, restore: () => (globalThis.fetch = realFetch) };
}

function note(id: string, author: string, values: Record<string, unknown> = {}): Note {
  return new Note({ id: new URL(id), attribution: new URL(author), content: "<p>text</p>", to: PUBLIC_COLLECTION, cc: new URL(`${author}/followers`), ...values });
}

// The Note of the timeline_event_create fixture.
function fixtureNote(): Note {
  return note("https://remote.example/notes/1", DAN, {
    url: new URL("https://remote.example/@dan/1"),
    content: new LanguageString("<p>Hello from Dan</p>", "en"),
    published: PUBLISHED,
    attachments: [new Document({ url: new URL("https://remote.example/media/1.png"), mediaType: "image/png", name: "alt text", width: 800, height: 600 })],
    contexts: [new URL("https://remote.example/contexts/1")],
  });
}

Deno.test("a public Create from a followed actor goes to Claire as the timeline event of the contract fixture", async () => {
  const { fake, sidecar, post, fetched, restore } = setup();
  try {
    await post(new Create({ id: new URL("https://remote.example/activities/201"), actor: new URL(DAN), object: fixtureNote(), to: PUBLIC_COLLECTION }));
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_create").request.body]);
    assert.deepEqual(fake.events, []);
    assert.deepEqual(sidecar.counters, { accepted: 1, rejected: 0, ignored: 0, blocked: 0, dropped: 0 });
    // The attachment is not fetched: the viewer gets media through the proxy of Claire only.
    assert.ok(!fetched.some((url) => url.includes("/media/")));
  } finally {
    restore();
  }
});

Deno.test("an Announce from a followed actor fetches the object and its author, and the event has the announcer", async () => {
  const { fake, post, add, fetched, restore } = setup();
  try {
    await add(new Page({ id: new URL("https://forum.example/post/42"), url: new URL("https://forum.example/post/42"), attribution: new URL(ERIN), name: "Forum post", content: "<p>A forum post</p>", published: PUBLISHED, to: PUBLIC_COLLECTION }));
    await post(new Announce({ id: new URL("https://remote.example/activities/202"), actor: new URL(DAN), object: new URL("https://forum.example/post/42"), to: PUBLIC_COLLECTION }));
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_announce").request.body]);
    assert.ok(fetched.includes("https://forum.example/post/42") && fetched.includes(ERIN));
  } finally {
    restore();
  }
});

Deno.test("an object that is not public goes to Claire without its content", async () => {
  const { fake, sidecar, post, restore } = setup();
  try {
    const secret = new Note({ id: new URL("https://remote.example/notes/3"), attribution: new URL(DAN), content: "<p>for followers only</p>", to: new URL(`${DAN}/followers`) });
    await post(new Create({ id: new URL("https://remote.example/activities/203"), actor: new URL(DAN), object: secret, to: new URL(`${DAN}/followers`) }));
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_not_public").request.body]);
    assert.ok(!JSON.stringify(fake.timelineEvents).includes("for followers only"));
    assert.equal(sidecar.counters.ignored, 1);
  } finally {
    restore();
  }
});

Deno.test("a Delete of an object and an Undo(Announce) are timeline events without an object", async () => {
  const { fake, post, restore } = setup();
  try {
    await post(new Delete({ id: new URL("https://remote.example/activities/204"), actor: new URL(DAN), object: new URL("https://remote.example/notes/4") }));
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_delete").request.body]);
    fake.timelineDecision = { decision: "accepted", reason: null, items: 1, notices: [] };
    const announce = new Announce({ id: new URL("https://remote.example/activities/202"), actor: new URL(DAN), object: new URL("https://forum.example/post/42") });
    await post(new Undo({ id: new URL("https://remote.example/activities/205"), actor: new URL(DAN), object: announce }));
    const undo = fake.timelineEvents[1];
    assert.deepEqual([undo.type, undo.object_iri, undo.public, undo.object], ["Undo", "https://forum.example/post/42", null, null]);
    assert.deepEqual(fake.events, []);
  } finally {
    restore();
  }
});

Deno.test("an actor that no account follows gets no fetch: the activity stays an inbox event", async () => {
  const { fake, post, fetched, restore } = setup();
  try {
    fake.decision = IGNORED;
    await post(new Announce({ id: new URL("https://remote.example/activities/210"), actor: new URL(BOB), object: new URL("https://forum.example/post/42") }));
    await post(new Create({ id: new URL("https://remote.example/activities/211"), actor: new URL(BOB), object: note("https://remote.example/notes/11", BOB, { replyTarget: new URL("https://forum.example/post/42") }) }));
    assert.deepEqual(fake.timelineEvents, []);
    assert.deepEqual(fake.events.map((e) => e.type), ["Announce", "Create"]);
    assert.ok(!fetched.some((url) => url.startsWith("https://forum.example/")));
  } finally {
    restore();
  }
});

Deno.test("a reply brings its parent chain, nearest first, within the depth of Claire, and the chain stops at a parent that is not public", async () => {
  const { fake, post, add, restore } = setup();
  try {
    fake.timelineDecision = { decision: "accepted", reason: null, items: 1, notices: [] };
    fake.followed.add(BOB);
    await add(note("https://forum.example/c/3", ERIN, { replyTarget: new URL("https://forum.example/c/2") }));
    await add(note("https://forum.example/c/2", ERIN, { replyTarget: new URL("https://forum.example/c/1") }));
    await add(note("https://forum.example/c/1", ERIN, { replyTarget: new URL("https://forum.example/c/0") }));
    await add(new Note({ id: new URL("https://forum.example/c/0"), attribution: new URL(ERIN), content: "<p>private root</p>", to: new URL(`${ERIN}/followers`) }));
    const reply = (id: string) => new Create({ id: new URL(`https://remote.example/activities/${id}`), actor: new URL(BOB), object: note(`https://remote.example/notes/${id}`, BOB, { replyTarget: new URL("https://forum.example/c/3") }) });

    await post(reply("220"));
    const parents = (index: number) => (fake.timelineEvents[index].parents as { object_iri: string; in_reply_to: string; author: { handle: string } }[]);
    assert.deepEqual(parents(0).map((p) => p.object_iri), ["https://forum.example/c/3", "https://forum.example/c/2", "https://forum.example/c/1"]);
    assert.equal(parents(0)[0].author.handle, "@erin@forum.example");
    assert.ok(!JSON.stringify(fake.timelineEvents).includes("private root"));

    fake.context = { context_fetch: "on", context_depth: 1 };
    await post(reply("221"));
    assert.deepEqual(parents(1).map((p) => p.object_iri), ["https://forum.example/c/3"]);

    fake.context = { context_fetch: "off", context_depth: 5 };
    await post(reply("222"));
    assert.deepEqual(parents(2), []);
    assert.equal((fake.timelineEvents[2].object as { in_reply_to: string }).in_reply_to, "https://forum.example/c/3");
  } finally {
    restore();
  }
});

Deno.test("an object of another author, a body above the limit, and an announced object that is not available make no event", async () => {
  const { fake, sidecar, post, restore } = setup();
  try {
    fake.decision = IGNORED;
    await post(new Create({ id: new URL("https://remote.example/activities/230"), actor: new URL(DAN), object: note("https://remote.example/notes/30", BOB) }));
    await post(new Create({ id: new URL("https://remote.example/activities/231"), actor: new URL(DAN), object: note("https://remote.example/notes/31", DAN, { content: "x".repeat(CONTENT_MAX_BYTES + 1) }) }));
    assert.deepEqual(fake.timelineEvents, []);
    assert.equal(sidecar.counters.dropped, 2);
    // An object that the remote server does not give is not a post: the activity stays an inbox event, and Claire ignores it.
    await post(new Announce({ id: new URL("https://remote.example/activities/232"), actor: new URL(DAN), object: new URL("https://forum.example/missing") }));
    assert.deepEqual(fake.timelineEvents, []);
    assert.deepEqual(fake.events.map((e) => e.type), ["Announce"]);
  } finally {
    restore();
  }
});

Deno.test("an Update has the changed object and asks no interest, and only the first attachments within the limit are kept", async () => {
  const { fake, post, restore } = setup();
  try {
    fake.timelineDecision = { decision: "ignored", reason: "no_item", items: 0, notices: [] };
    const attachments = Array.from({ length: MAX_ATTACHMENTS + 2 }, (_, i) => new Document({ url: new URL(`https://remote.example/media/${i}.png`), mediaType: "image/png" }));
    await post(new Update({ id: new URL("https://remote.example/activities/240"), actor: new URL(BOB), object: note("https://remote.example/notes/40", BOB, { content: "<p>changed</p>", attachments }) }));
    const [event] = fake.timelineEvents;
    assert.equal(event.type, "Update");
    const object = event.object as { content_html: string; attachments: { url: string }[] };
    assert.equal(object.content_html, "<p>changed</p>");
    assert.equal(object.attachments.length, MAX_ATTACHMENTS);
    assert.ok(!fake.calls.some((call) => call.includes("/timeline-interest")));
  } finally {
    restore();
  }
});

// --- Inbound interactions with an account (contract 1.4, unit FP4-8) ---

const ALICE_ACTOR = `${ORIGIN}/ap/actors/116412345678901234`;
const ALICE_OBJECT = `${ORIGIN}/ap/objects/816412345678901234`;

Deno.test("a public reply with a mention from an actor that no account follows is the timeline event of the contract fixture, without a fetch of an object", async () => {
  const { fake, sidecar, post, fetched, restore } = setup();
  try {
    const reply = new Note({ id: new URL("https://remote.example/notes/60"), attribution: new URL(BOB), content: "<p>A public reply to Alice</p>", published: PUBLISHED, to: PUBLIC_COLLECTION, cc: new URL(ALICE_ACTOR), replyTarget: new URL(ALICE_OBJECT), tags: [new Mention({ href: new URL(ALICE_ACTOR), name: "@alice@claire.example.com" })] });
    await post(new Create({ id: new URL("https://remote.example/activities/260"), actor: new URL(BOB), object: reply, to: PUBLIC_COLLECTION }));
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_mention").request.body]);
    assert.deepEqual(fake.events, []);
    assert.equal(sidecar.counters.accepted, 1);
    // Only the actor document is fetched (the signature check). The object of this server and the parents are not.
    assert.ok(fetched.every((url) => url === BOB));
  } finally {
    restore();
  }
});

Deno.test("a non-public message to an account goes to Claire without content, and the notice of Claire goes to the sender only", async () => {
  const { fake, post, deliveries, restore } = setup();
  try {
    const direct = new Note({ id: new URL("https://remote.example/notes/61"), attribution: new URL(BOB), content: "<p>a secret for Alice</p>", to: new URL(ALICE_ACTOR), tags: [new Mention({ href: new URL(ALICE_ACTOR), name: "@alice@claire.example.com" })] });
    const create = () => new Create({ id: new URL("https://remote.example/activities/261"), actor: new URL(BOB), object: direct, to: new URL(ALICE_ACTOR) });
    await post(create());
    assert.deepEqual(fake.timelineEvents, [fixture("timeline_event_private_notice").request.body]);
    assert.ok(!JSON.stringify(fake.timelineEvents).includes("a secret"));
    assert.equal(deliveries.length, 1);
    const [delivery] = deliveries;
    assert.equal(delivery.url, `${BOB}/inbox`);
    const notice = delivery.body.object as Record<string, unknown>;
    const text = (fixture("timeline_event_private_notice").response.notices as { content: string }[])[0].content;
    assert.equal(delivery.body.type, "Create");
    assert.equal(delivery.body.actor, ALICE_ACTOR);
    assert.equal(delivery.body.to, BOB);
    assert.equal(delivery.body.cc, undefined);
    assert.equal(notice.type, "Note");
    assert.match(String(notice.id), new RegExp(`^${ALICE_ACTOR}#auto-replies/[0-9a-f]{32}$`));
    assert.equal(notice.attributedTo, ALICE_ACTOR);
    assert.equal(notice.inReplyTo, "https://remote.example/notes/61");
    assert.equal(notice.to, BOB);
    assert.equal(notice.cc, undefined);
    assert.equal(notice.content, text);
    assert.deepEqual(notice.contentMap, { en: text });
    assert.equal((notice.tag as Record<string, unknown>).type, "Mention");
    assert.equal((notice.tag as Record<string, unknown>).href, BOB);
    assert.ok(!JSON.stringify(delivery.body).includes("Public"));
    // The ID has a digest of the object ID: a second run of the same activity makes the same Note.
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode("https://remote.example/notes/61")));
    assert.equal(notice.id, `${ALICE_ACTOR}#auto-replies/${[...digest.slice(0, 16)].map((b) => b.toString(16).padStart(2, "0")).join("")}`);
  } finally {
    restore();
  }
});

Deno.test("a Create from an actor that no account follows stays an inbox event when its object is not in the activity or has no relation to this server", async () => {
  const { fake, post, add, fetched, restore } = setup();
  try {
    fake.decision = IGNORED;
    // The object is an ID only: the sidecar does not fetch it for an actor that no account follows.
    await add(note("https://remote.example/notes/62", BOB, { tags: [new Mention({ href: new URL(ALICE_ACTOR) })] }));
    await post(new Create({ id: new URL("https://remote.example/activities/262"), actor: new URL(BOB), object: new URL("https://remote.example/notes/62") }));
    // A mention of an actor of another server, and a followers collection of this server in cc, are not a mention of an account.
    await post(new Create({ id: new URL("https://remote.example/activities/263"), actor: new URL(BOB), object: note("https://remote.example/notes/63", BOB, { cc: new URL(`${ALICE_ACTOR}/followers`), tags: [new Mention({ href: new URL(ERIN) })] }) }));
    assert.deepEqual(fake.timelineEvents, []);
    assert.deepEqual(fake.events.map((e) => e.type), ["Create", "Create"]);
    assert.ok(!fetched.includes("https://remote.example/notes/62"));
  } finally {
    restore();
  }
});

Deno.test("a Like and an Announce of an object of this server and their Undo are inbox events for the counters of Claire", async () => {
  const { fake, post, fetched, restore } = setup();
  try {
    const like = new Like({ id: new URL("https://remote.example/activities/250"), actor: new URL(BOB), object: new URL(ALICE_OBJECT) });
    await post(like);
    await post(new Undo({ id: new URL("https://remote.example/activities/251"), actor: new URL(BOB), object: like }));
    assert.deepEqual(fake.events, [fixture("inbox_event_like").request.body, fixture("inbox_event_undo_like").request.body]);
    fake.decision = { decision: "accepted", reply: null, reason: null };
    const announce = new Announce({ id: new URL("https://remote.example/activities/253"), actor: new URL(DAN), object: new URL(ALICE_OBJECT), to: PUBLIC_COLLECTION });
    await post(announce);
    await post(new Undo({ id: new URL("https://remote.example/activities/254"), actor: new URL(DAN), object: announce }));
    assert.deepEqual(fake.events.slice(2).map((e) => [e.type, e.object_type, e.object_iri]), [["Announce", null, ALICE_OBJECT], ["Undo", "Announce", ALICE_OBJECT]]);
    assert.deepEqual(fake.timelineEvents, []);
    // The object of this server is not fetched.
    assert.ok(!fetched.some((url) => url.startsWith(ORIGIN)));
  } finally {
    restore();
  }
});
