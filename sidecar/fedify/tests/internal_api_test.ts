// Internal API client against the shared contract fixtures (module interface between the sidecar and Claire).

import assert from "node:assert/strict";
import { ContractError, type InboxEvent, InternalApi, InternalApiError, signRequest } from "../src/internal_api.ts";
import { fakeClaire, fixture, FIXTURE_DIR, fixtureNames, KEY, NOW } from "./fixtures.ts";

function client(fake = fakeClaire(), now = NOW) {
  return { fake, api: new InternalApi({ key: KEY, transport: fake.transport, now: () => now }) };
}

Deno.test("the signature agrees with the vector that the Python code made", async () => {
  const vector = JSON.parse(Deno.readTextFileSync(new URL("federation_signature_vector.json", FIXTURE_DIR)));
  const key = Uint8Array.from(vector.key_hex.match(/../g).map((h: string) => parseInt(h, 16)));
  assert.equal(await signRequest(key, vector.method, vector.target, vector.timestamp, new TextEncoder().encode(vector.body)), vector.signature);
});

Deno.test("each fixture has a test in this file", () => {
  assert.deepEqual(fixtureNames(), ["actor", "actor_by_username", "actor_by_username_not_found", "actor_gone", "actor_keys", "actor_keys_not_found", "actor_not_found", "actor_outbox", "actor_outbox_not_found", "client_task_ack", "client_tasks", "config", "domain_blocks", "followers", "followers_next", "followers_not_found", "following", "heartbeat", "heartbeat_version_mismatch", "inbox_event_accept_follow", "inbox_event_blocked_domain", "inbox_event_delete_actor", "inbox_event_follow", "inbox_event_ignored", "inbox_event_not_an_actor", "inbox_event_undo_follow", "inbox_event_update_actor", "instance_actor_keys", "object", "object_gone", "object_not_found", "outbox_task_ack", "outbox_task_ack_not_found", "outbox_task_ack_rejected", "outbox_tasks"]);
});

Deno.test("config, actor, and key calls agree with the fixtures", async () => {
  const { api, fake } = client();
  assert.deepEqual(await api.config(), fixture("config").response);
  assert.deepEqual(await api.actorByUsername("alice"), fixture("actor_by_username").response);
  assert.deepEqual(await api.actor("116412345678901234"), fixture("actor").response);
  assert.deepEqual(await api.actorKeys("116412345678901234"), fixture("actor_keys").response);
  assert.deepEqual(await api.actorKeys("instance"), fixture("instance_actor_keys").response);
  assert.deepEqual(fake.calls, ["config", "actor_by_username", "actor", "actor_keys", "instance_actor_keys"].map((n) => `GET ${fixture(n).request.path}`));
});

Deno.test("a 404 is null", async () => {
  const { api } = client();
  assert.equal(await api.actorByUsername("private_bob"), null);
  assert.equal(await api.actor("216412345678901234"), null);
  assert.equal(await api.actorKeys("216412345678901234"), null);
});

Deno.test("heartbeat agrees with the fixture, and a different major version is an error", async () => {
  const { api } = client();
  const ok = fixture("heartbeat");
  assert.deepEqual(await api.heartbeat(ok.request.body as never), ok.response);
  const mismatch = fixture("heartbeat_version_mismatch");
  await assert.rejects(api.heartbeat(mismatch.request.body as never), (error: InternalApiError) => error.status === 409 && error.code === "contract_version_mismatch");
});

Deno.test("a request outside the time window or with a different key is refused", async () => {
  await assert.rejects(client(fakeClaire(), NOW - 600).api.config(), (error: InternalApiError) => error.status === 401);
  const fake = fakeClaire();
  const other = new InternalApi({ key: new Uint8Array(32), transport: fake.transport, now: () => NOW });
  await assert.rejects(other.config(), (error: InternalApiError) => error.status === 401);
  assert.throws(() => new InternalApi({ key: new Uint8Array(16), transport: fake.transport }));
});

Deno.test("a response outside the sealed shape is an error", async () => {
  for (const change of [{ extra_field: 1 }, { federation: "maybe" }, { federated_actors: "1" }, { trusted_proxies: [1] }, { software: { name: "claire-bible" } }, { limits: {} }, { domain_blocks_etag: null }]) {
    const { api, fake } = client();
    fake.config = change;
    await assert.rejects(api.config(), ContractError, JSON.stringify(change));
  }
  const { api, fake } = client();
  fake.config = { contract_version: "2.0" };
  await assert.rejects(api.config(), ContractError);
  fake.config = { contract_version: "1.7" };
  assert.equal((await api.config()).contract_version, "1.7");
});

Deno.test("follower pages and domain blocks agree with the fixtures", async () => {
  const { api, fake } = client();
  const first = await api.followers("116412345678901234", { limit: 2 });
  assert.deepEqual(first, fixture("followers").response);
  assert.deepEqual(await api.followers("116412345678901234", { limit: 2, maxId: first!.next_max_id! }), fixture("followers_next").response);
  assert.equal(await api.followers("216412345678901234"), null);
  const blocks = await api.domainBlocks();
  assert.deepEqual(blocks, fixture("domain_blocks").response);
  assert.equal(blocks.etag, (await api.config()).domain_blocks_etag);
  assert.deepEqual(fake.calls.slice(0, 4), ["followers", "followers_next", "followers_not_found", "domain_blocks"].map((n) => `GET ${fixture(n).request.path}`));
});

Deno.test("each inbox event gets the decision of its fixture", async () => {
  const { api } = client();
  for (const name of fixtureNames().filter((n) => n.startsWith("inbox_event_"))) {
    const fx = fixture(name);
    assert.deepEqual(await api.inboxEvent(fx.request.body as InboxEvent), fx.response, name);
  }
  assert.deepEqual(await api.inboxEvent(fixture("inbox_event_follow").request.body as InboxEvent), { decision: "accepted", reply: "accept", reason: null });
});

Deno.test("an inbox event without a decision is an error", async () => {
  const { api, fake } = client();
  const event = fixture("inbox_event_follow").request.body as InboxEvent;
  // The fake has no fixture for this body. A 404 is not a decision.
  await assert.rejects(api.inboxEvent({ ...event, event_id: "https://remote.example/activities/unknown" }), (error: InternalApiError) => error.status === 404);
  fake.failStatus = 503;
  await assert.rejects(api.inboxEvent(event), (error: InternalApiError) => error.status === 503);
});

Deno.test("client tasks, their acknowledgement, and the following total agree with the fixtures (contract 1.3)", async () => {
  const { api, fake } = client();
  assert.deepEqual(await api.clientTasks(10), (fixture("client_tasks").response as { tasks: unknown }).tasks);
  const body = fixture("client_task_ack").request.body as { actor: Parameters<typeof api.ackClientTask>[3] };
  assert.equal(await api.ackClientTask(1, "handed_off", null, body.actor), "handed_off");
  assert.equal(await api.ackClientTask(999, "rejected", "actor_not_found"), null);
  assert.deepEqual(await api.following("116412345678901234"), fixture("following").response);
  assert.equal(await api.following("216412345678901234"), null);
  assert.equal(fake.calls[0], `GET ${fixture("client_tasks").request.path}`);
});

Deno.test("outbox tasks, acknowledgements, objects, and the outbox collection agree with the fixtures", async () => {
  const { api, fake } = client();
  assert.deepEqual(await api.outboxTasks(10), (fixture("outbox_tasks").response as { tasks: unknown }).tasks);
  assert.equal(await api.ackOutboxTask(1, "handed_off"), "handed_off");
  assert.equal(await api.ackOutboxTask(2, "rejected", "object_gone"), "rejected");
  assert.equal(await api.ackOutboxTask(999, "handed_off"), null);

  assert.deepEqual(await api.object("816412345678901234"), { state: "found", record: fixture("object").response });
  // A deleted object is not an absent object: the sidecar must answer 410 for it.
  assert.deepEqual(await api.object("816412345678901239"), { state: "gone", gone_at: 1800000330 });
  assert.equal(await api.object("816412345678901999"), null);

  assert.deepEqual(await api.actorOutbox("116412345678901234", { limit: 1 }), fixture("actor_outbox").response);
  // A purged actor (unit FP3-4).
  assert.deepEqual(await api.actorResult("916412345678901234"), { state: "gone", gone_at: 1800000350.0 });
  assert.equal(await api.actor("916412345678901234"), null);
  assert.deepEqual(await api.actorResult("116412345678901234"), { state: "found", record: fixture("actor").response });
  assert.equal(await api.actorResult("216412345678901234"), null);
  assert.equal(await api.actorOutbox("216412345678901234"), null);
  assert.equal(fake.calls[0], `GET ${fixture("outbox_tasks").request.path}`);

  fake.failStatus = 500;
  await assert.rejects(api.object("816412345678901234"), (error: InternalApiError) => error.status === 500);
  await assert.rejects(api.outboxTasks(), (error: InternalApiError) => error.status === 500);
});

Deno.test("a server error is an error, not an absent actor", async () => {
  const { api, fake } = client();
  fake.failStatus = 500;
  await assert.rejects(api.actor("116412345678901234"), (error: InternalApiError) => error.status === 500);
});
