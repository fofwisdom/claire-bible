// Quotes of the sidecar (FEP-044f, contract 1.7, unit FP4-9Q): QuoteRequest and Create of a quoting note, Accept with a QuoteAuthorization, Reject, Delete of an authorization, the inbound activities as quote events, the public path of an authorization with its 410, canQuote, and the quote property of an inbound post.
// All servers are in this process. The remote server answers WebFinger, actor documents, and an authorization document to a GET, and captures each POST (a delivery to a local test inbox).

import assert from "node:assert/strict";
import { MemoryKvStore, signRequest } from "@fedify/fedify";
import { generateCryptoKeyPair } from "@fedify/fedify";
import { Accept, type Activity, CryptographicKey, Endpoints, Note, Person, QuoteAuthorization, QuoteRequest, Reject } from "@fedify/fedify/vocab";
import { InternalApi } from "../src/internal_api.ts";
import { Sidecar } from "../src/sidecar.ts";
import { quotedObject } from "../src/timeline.ts";
import { fakeClaire, fixture, KEY } from "./fixtures.ts";

const ORIGIN = "https://claire.example.com";
const ALICE = "116412345678901234";
const ALICE_URL = `${ORIGIN}/ap/actors/${ALICE}`;
const QUOTED = "816412345678901240";
const QUOTED_URL = `${ORIGIN}/ap/objects/${QUOTED}`;
const QUOTING = "816412345678901242";
const QUOTING_URL = `${ORIGIN}/ap/objects/${QUOTING}`;
const CLOSED_URL = `${ORIGIN}/ap/objects/816412345678901243`;
const BOB = "https://remote.example/users/bob";
const CAROL = "https://remote.example/users/carol";
const PARENT = `${CAROL}/notes/1`;
const AUTHORIZATION = `${CAROL}/quote-authorizations/1`;
const PREFIX = "/internal/federation/v1";
const AS2 = "application/activity+json";

const remoteKeys = await generateCryptoKeyPair("RSASSA-PKCS1-v1_5");

interface Delivery {
  url: string;
  body: Record<string, unknown>;
}

async function remoteActor(iri: string): Promise<unknown> {
  const url = new URL(iri);
  const actor = new Person({
    id: url,
    preferredUsername: iri.split("/").pop(),
    inbox: new URL(`${iri}/inbox`),
    endpoints: new Endpoints({ sharedInbox: new URL(`${url.origin}/inbox`) }),
    publicKey: new CryptographicKey({ id: new URL(`${iri}#main-key`), owner: url, publicKey: remoteKeys.publicKey }),
  });
  return await actor.toJsonLd();
}

function setup() {
  const fake = fakeClaire();
  const api = new InternalApi({ key: KEY, transport: fake.transport, now: () => fake.now });
  const sidecar = new Sidecar({ api, kv: new MemoryKvStore(), now: () => fake.now });
  const deliveries: Delivery[] = [];
  const fetched: string[] = [];
  // Documents of the remote server that are not actors. The key is the address.
  const documents = new Map<string, unknown>();
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (request.method === "GET") {
      fetched.push(request.url);
      if (documents.has(request.url)) return new Response(JSON.stringify(documents.get(request.url)), { headers: { "content-type": AS2 } });
      if (!url.pathname.startsWith("/users/") || url.pathname.split("/").length !== 3) return new Response("not found", { status: 404 });
      return new Response(JSON.stringify(await remoteActor(request.url.split("#")[0])), { headers: { "content-type": AS2 } });
    }
    deliveries.push({ url: request.url, body: JSON.parse(await request.text()) });
    return new Response(null, { status: 202 });
  };
  fake.overrides.set(`GET ${PREFIX}/actors/${ALICE}/followers?limit=80`, { status: 200, response: { public_id: ALICE, total: 0, items: [], next_max_id: null } });
  const setTasks = (tasks: unknown[]) => fake.overrides.set(`GET ${PREFIX}/interaction-tasks?limit=10`, { status: 200, response: { tasks } });
  const setAck = (id: number, status = "handed_off") => fake.overrides.set(`POST ${PREFIX}/interaction-tasks/${id}/ack`, { status: 200, response: { id, status } });
  const get = (url: string, accept = AS2) => sidecar.handle(new Request(url, { headers: { accept } }));
  // A signed delivery of the remote server to an inbox of this instance.
  const post = async (path: string, activity: Activity | Record<string, unknown>) => {
    const document = "toJsonLd" in activity ? await (activity as Activity).toJsonLd() as Record<string, unknown> : activity;
    const signed = await signRequest(new Request(`${ORIGIN}${path}`, { method: "POST", headers: { "content-type": AS2 }, body: JSON.stringify(document) }), remoteKeys.privateKey, new URL(`${document.actor}#main-key`));
    const response = await sidecar.handle(signed);
    await response.body?.cancel();
    return response.status;
  };
  const of = (type: string) => deliveries.filter((d) => d.body.type === type);
  return { fake, sidecar, deliveries, fetched, documents, setTasks, setAck, get, post, of, restore: () => (globalThis.fetch = realFetch) };
}

function carolAuthorization(changes: Record<string, unknown> = {}): QuoteAuthorization {
  return new QuoteAuthorization({ id: new URL(AUTHORIZATION), attribution: new URL(CAROL), interactingObject: new URL(QUOTING_URL), interactionTarget: new URL(PARENT), ...changes });
}

Deno.test("a quote_request task sends QuoteRequest to the author and then Create with the quote; the answer tasks become Accept with a QuoteAuthorization and Delete", async () => {
  const { fake, sidecar, deliveries, setAck, of, restore } = setup();
  try {
    // The tasks of the contract fixture: a note of alice that quotes a note of carol, the approval of a request of bob, and the revoke of another one.
    for (const id of [20, 21, 22]) setAck(id);
    assert.equal(await sidecar.processInteractionTasks(5), 3);
    assert.deepEqual(sidecar.outboxCounters, { handed_off: 3, rejected: 0, failed: 0 });
    assert.deepEqual(fake.interactionAcks.map((a) => `${a.id} ${a.result}`), ["20 handed_off", "21 handed_off", "22 handed_off"]);
    assert.deepEqual(deliveries.map((d) => d.body.type), ["QuoteRequest", "Create", "Accept", "Delete"]);

    // Design Section 5.4 rule 5: the request goes to the author of the quoted object. `object` is the quoted object, and `instrument` is the quoting Note (RT9).
    const request = of("QuoteRequest")[0];
    assert.equal(request.url, "https://remote.example/inbox");
    assert.deepEqual([request.body.id, request.body.actor, request.body.object, request.body.to], [`${QUOTING_URL}#quote-request`, ALICE_URL, PARENT, CAROL]);
    const instrument = request.body.instrument as Record<string, unknown>;
    assert.deepEqual([instrument.id, instrument.type, instrument.quote], [QUOTING_URL, "Note", PARENT]);

    // The Create has the quote with the keys of other servers (RT9), no authorization while the quote waits, and canQuote of the policy `followers`.
    const create = of("Create")[0];
    assert.equal(create.url, "https://remote.example/inbox");
    const note = create.body.object as Record<string, unknown>;
    assert.deepEqual([note.id, note.quote, note.quoteUrl, note._misskey_quote, note.quoteUri], [QUOTING_URL, PARENT, PARENT, PARENT, PARENT]);
    assert.ok(!("quoteAuthorization" in note));
    assert.equal(note.content, fixture("quote_note").response.content);
    assert.deepEqual(note.interactionPolicy, { canQuote: { automaticApproval: `${ALICE_URL}/followers` } });

    // The approval of the request of bob: Accept of the request by its ID, with the authorization in `result`. The authorization has the ID of the quoting object, and does not embed it.
    const accept = of("Accept")[0];
    assert.equal(accept.url, "https://remote.example/inbox");
    assert.deepEqual([accept.body.id, accept.body.actor, accept.body.to], [`${ALICE_URL}#quote-accepts/1`, ALICE_URL, BOB]);
    const answered = accept.body.object as Record<string, unknown>;
    assert.deepEqual([answered.id, answered.type, answered.actor, answered.object, answered.instrument], [`${BOB}#quote-requests/9`, "QuoteRequest", BOB, QUOTED_URL, "https://remote.example/notes/9"]);
    assert.deepEqual(accept.body.result, { id: `${ORIGIN}/ap/quote-authorizations/1`, type: "QuoteAuthorization", attributedTo: ALICE_URL, interactingObject: "https://remote.example/notes/9", interactionTarget: QUOTED_URL });

    // The revoke: Delete of the authorization.
    const revoke = of("Delete")[0];
    assert.deepEqual([revoke.body.id, revoke.body.actor, revoke.body.object, revoke.body.to], [`${ALICE_URL}#quote-revokes/2`, ALICE_URL, `${ORIGIN}/ap/quote-authorizations/2`, BOB]);
  } finally {
    restore();
  }
});

Deno.test("a request for a `nobody` object goes to Claire as the event of the fixture, and the quote_reject task sends Reject", async () => {
  const { fake, sidecar, deliveries, fetched, setTasks, setAck, post, restore } = setup();
  try {
    const body = fixture("quote_event_nobody").request.body as { event_id: string; instrument_iri: string };
    const quoting = new Note({ id: new URL(body.instrument_iri), attribution: new URL(BOB), content: "<p>A quote</p>", quote: new URL(CLOSED_URL) });
    assert.equal(await post("/ap/inbox", new QuoteRequest({ id: new URL(body.event_id), actor: new URL(BOB), object: new URL(CLOSED_URL), instrument: quoting })), 202);
    // The event has the quoting object as an address only. The sidecar does not fetch it.
    assert.deepEqual([...fake.quoteEvents], [body]);
    assert.equal(fake.events.length, 0);
    assert.deepEqual(sidecar.counters, { accepted: 0, rejected: 1, ignored: 0, blocked: 0, dropped: 0 });
    assert.ok(!fetched.includes(body.instrument_iri));
    assert.equal(deliveries.length, 0);

    // Completion criterion: the request gets a Reject (the task that Claire made for the refused request).
    setTasks([{ id: 40, kind: "quote_reject", actor_public_id: ALICE, ref_id: "4", object_iri: CLOSED_URL, recipients: [BOB], quote: { request_iri: body.event_id, quoting_object_iri: body.instrument_iri }, created_at: 1800000393.0, lease_until: 1800000700.0 }]);
    setAck(40);
    assert.equal(await sidecar.processInteractionTasks(), 1);
    assert.deepEqual(deliveries.map((d) => [d.url, d.body.type]), [["https://remote.example/inbox", "Reject"]]);
    const reject = deliveries[0].body;
    assert.deepEqual([reject.id, reject.actor, reject.to], [`${ALICE_URL}#quote-rejects/4`, ALICE_URL, BOB]);
    assert.deepEqual([(reject.object as Record<string, unknown>).id, (reject.object as Record<string, unknown>).object], [body.event_id, CLOSED_URL]);
    assert.ok(!("result" in reject));
  } finally {
    restore();
  }
});

Deno.test("a QuoteRequest to the inbox of an actor has the recipient, and a QuoteRequest for an object of another server stays an inbox event", async () => {
  const { fake, post, restore } = setup();
  try {
    const body = fixture("quote_event_request").request.body as { event_id: string; instrument_iri: string };
    assert.equal(await post(`/ap/actors/${ALICE}/inbox`, new QuoteRequest({ id: new URL(body.event_id), actor: new URL(BOB), object: new URL(QUOTED_URL), instrument: new URL(body.instrument_iri) })), 202);
    assert.deepEqual([...fake.quoteEvents], [body]);
    fake.decision = { decision: "ignored", reply: null, reason: "unsupported_type" };
    await post("/ap/inbox", new QuoteRequest({ id: new URL(`${BOB}#quote-requests/12`), actor: new URL(BOB), object: new URL(PARENT), instrument: new URL("https://remote.example/notes/12") }));
    assert.equal(fake.quoteEvents.length, 1);
    assert.deepEqual(fake.events.map((e) => [e.type, e.object_iri]), [["QuoteRequest", PARENT]]);
  } finally {
    restore();
  }
});

Deno.test("an Accept with a QuoteAuthorization is the quote event of the fixture, matched by objectId without a fetch of the request", async () => {
  const { fake, sidecar, fetched, documents, post, restore } = setup();
  try {
    const body = fixture("quote_event_accept").request.body as { event_id: string; object_iri: string };
    // The authorization is in the activity, on the origin of the author.
    assert.equal(await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(body.event_id), actor: new URL(CAROL), object: new URL(body.object_iri), result: carolAuthorization() })), 202);
    assert.deepEqual([...fake.quoteEvents], [body]);
    assert.deepEqual(sidecar.counters, { accepted: 1, rejected: 0, ignored: 0, blocked: 0, dropped: 0 });
    // RT9: the request is matched by its ID. No document of this instance and no authorization is fetched.
    assert.ok(!fetched.some((url) => url.startsWith(ORIGIN) || url === AUTHORIZATION));

    // The authorization as an address only: the sidecar reads it from the server of the author.
    documents.set(AUTHORIZATION, await carolAuthorization().toJsonLd());
    await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(`${CAROL}#accepts/quotes/1b`), actor: new URL(CAROL), object: new URL(body.object_iri), result: new URL(AUTHORIZATION) }));
    assert.deepEqual(fake.quoteEvents[1], { ...body, event_id: `${CAROL}#accepts/quotes/1b` });
    assert.ok(fetched.includes(AUTHORIZATION));

    // Untrusted input: an embedded authorization of another origin is not used as it is. Fedify reads the document of its own origin, and that one names another note.
    const foreign = "https://other.example/quote-authorizations/7";
    documents.set(foreign, await carolAuthorization({ id: new URL(foreign), interactingObject: new URL("https://other.example/notes/1") }).toJsonLd());
    fake.quoteDecision = { decision: "rejected", reason: "invalid_authorization", state: null };
    await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(`${CAROL}#accepts/quotes/2`), actor: new URL(CAROL), object: new URL(body.object_iri), result: carolAuthorization({ id: new URL(foreign) }) }));
    assert.deepEqual(fake.quoteEvents[2].authorization, { iri: foreign, interacting_object: "https://other.example/notes/1", interaction_target: PARENT, attributed_to: CAROL });
    // A result that is not an authorization gives an event without the fields; Claire refuses it.
    documents.set(`${CAROL}/notes/5`, await new Note({ id: new URL(`${CAROL}/notes/5`), attribution: new URL(CAROL), content: "x" }).toJsonLd());
    await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(`${CAROL}#accepts/quotes/3`), actor: new URL(CAROL), object: new URL(body.object_iri), result: new URL(`${CAROL}/notes/5`) }));
    assert.deepEqual(fake.quoteEvents[3].authorization, { iri: `${CAROL}/notes/5`, interacting_object: null, interaction_target: null, attributed_to: null });
    await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(`${CAROL}#accepts/quotes/4`), actor: new URL(CAROL), object: new URL(body.object_iri) }));
    assert.equal(fake.quoteEvents[4].authorization, null);

    // A Reject of the request is a quote event without an authorization.
    await post(`/ap/actors/${ALICE}/inbox`, new Reject({ id: new URL(`${CAROL}#rejects/quotes/1`), actor: new URL(CAROL), object: new URL(body.object_iri) }));
    assert.deepEqual([fake.quoteEvents[5].type, fake.quoteEvents[5].object_iri, fake.quoteEvents[5].authorization], ["Reject", body.object_iri, null]);
    // An Accept of another activity of this instance (a follow) stays an inbox event.
    assert.equal(fake.events.length, 0);
    fake.decision = { decision: "ignored", reply: null, reason: "unsupported_type" };
    await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(`${CAROL}#accepts/5`), actor: new URL(CAROL), object: new URL(`${ALICE_URL}#follows/9`) }));
    assert.equal(fake.quoteEvents.length, 6);
    assert.deepEqual(fake.events.map((e) => e.type), ["Accept"]);
  } finally {
    restore();
  }
});

Deno.test("an authorization that is not available now gives no success answer: the sender tries again", async () => {
  const { fake, post, restore } = setup();
  try {
    const body = fixture("quote_event_accept").request.body as { event_id: string; object_iri: string };
    const status = await post(`/ap/actors/${ALICE}/inbox`, new Accept({ id: new URL(body.event_id), actor: new URL(CAROL), object: new URL(body.object_iri), result: new URL(AUTHORIZATION) }));
    assert.ok(status >= 500, String(status));
    assert.equal(fake.quoteEvents.length, 0);
  } finally {
    restore();
  }
});

Deno.test("the path of an authorization gives the QuoteAuthorization without the quoting object, and a revoked authorization gives 410", async () => {
  const { get, restore } = setup();
  try {
    const response = await get(`${ORIGIN}/ap/quote-authorizations/1`);
    assert.equal(response.status, 200);
    const document = await response.json();
    delete document["@context"];
    assert.deepEqual(document, { id: `${ORIGIN}/ap/quote-authorizations/1`, type: "QuoteAuthorization", attributedTo: ALICE_URL, interactingObject: "https://remote.example/notes/9", interactionTarget: QUOTED_URL });

    // Completion criterion: a revoked authorization gives 410.
    const gone = await get(`${ORIGIN}/ap/quote-authorizations/2`);
    assert.equal(gone.status, 410);
    assert.equal(gone.headers.get("content-type"), AS2);
    assert.deepEqual(await gone.json(), { "@context": "https://www.w3.org/ns/activitystreams", id: `${ORIGIN}/ap/quote-authorizations/2`, type: "Tombstone", formerType: "QuoteAuthorization", deleted: "2027-01-15T08:06:32Z" });
    assert.equal((await get(`${ORIGIN}/ap/quote-authorizations/99`)).status, 404);
    assert.equal((await get(`${ORIGIN}/ap/quote-authorizations/abc`)).status, 404);
    assert.equal((await get(`${ORIGIN}/ap/quote-authorizations/1`, "text/html")).status, 406);
  } finally {
    restore();
  }
});

Deno.test("canQuote comes from the quote policy, and an approved quote has the authorization", async () => {
  const { fake, get, restore } = setup();
  try {
    const path = `GET ${PREFIX}/notes/${QUOTING}`;
    const record = fixture("quote_note").response;
    const policies: Record<string, unknown> = {
      public: { automaticApproval: "as:Public" },
      followers: { automaticApproval: `${ALICE_URL}/followers` },
      manual: { automaticApproval: ALICE_URL, manualApproval: "as:Public" },
      nobody: { automaticApproval: ALICE_URL },
    };
    for (const [policy, rule] of Object.entries(policies)) {
      fake.overrides.set(path, { status: 200, response: { ...record, quote_policy: policy } });
      assert.deepEqual((await (await get(QUOTING_URL)).json()).interactionPolicy, { canQuote: rule }, policy);
    }
    fake.overrides.set(path, { status: 200, response: { ...record, quote_authorization: AUTHORIZATION } });
    const approved = await (await get(QUOTING_URL)).json();
    assert.deepEqual([approved.quote, approved.quoteAuthorization], [PARENT, AUTHORIZATION]);
    // A refused or revoked quote: the Note has no quote property, and the HTML keeps the link.
    fake.overrides.set(path, { status: 200, response: { ...record, quote: null } });
    const refused = await (await get(QUOTING_URL)).json();
    assert.ok(!("quote" in refused) && !("quoteUrl" in refused) && !("quoteAuthorization" in refused));
    assert.ok(String(refused.content).includes(`RE: <a href="${PARENT}"`));
  } finally {
    restore();
  }
});

Deno.test("the quote of an inbound post is `quote` first, then the other keys", async () => {
  const first = "https://remote.example/notes/1";
  const second = "https://remote.example/notes/2";
  const base = { id: new URL("https://remote.example/notes/9"), attribution: new URL(CAROL), content: "<p>x</p>" };
  const document = await new Note({ ...base, quote: new URL(first), quoteUrl: new URL(second) }).toJsonLd() as Record<string, unknown>;
  assert.deepEqual([document.quote, document.quoteUrl, document._misskey_quote, document.quoteUri], [first, second, second, second]);
  assert.equal(quotedObject(await Note.fromJsonLd(document)), first);
  // Each fallback key alone gives the quoted object.
  for (const key of ["quoteUrl", "_misskey_quote", "quoteUri"]) {
    const only: Record<string, unknown> = { ...document };
    for (const other of ["quote", "quoteUrl", "_misskey_quote", "quoteUri"]) if (other !== key) delete only[other];
    assert.equal(quotedObject(await Note.fromJsonLd(only)), second, key);
  }
  assert.equal(quotedObject(new Note(base)), null);
  // An address that is not HTTP is not a quoted object.
  assert.equal(quotedObject(new Note({ ...base, quote: new URL("javascript:alert(1)") })), null);
});
