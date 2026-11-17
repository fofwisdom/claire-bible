// Shared test code: the contract fixtures of the Claire internal federation API as a transport.
// The Python tests read the same files (tests/test_federation_routes.py).

import { SIGNATURE_HEADER, signRequest, TIMESTAMP_HEADER, type Transport } from "../src/internal_api.ts";

export const FIXTURE_DIR = new URL("../../../tests/fixtures/", import.meta.url);
export const NOW = 1_800_000_400;
export const KEY = new Uint8Array(32).map((_, i) => 200 - i);

export interface Fixture {
  request: { method: string; path: string; body?: unknown };
  status: number;
  response: Record<string, unknown>;
}

export function fixture(name: string): Fixture {
  return JSON.parse(Deno.readTextFileSync(new URL(`federation_contract/${name}.json`, FIXTURE_DIR)));
}

export function fixtureNames(): string[] {
  return [...Deno.readDirSync(new URL("federation_contract/", FIXTURE_DIR))].filter((e) => e.name.endsWith(".json")).map((e) => e.name.slice(0, -5)).sort();
}

// The contract fixtures of the internal routes of the knowledge federation (units KF1 and KF2). The Python test tests/test_knowledge_federation_internal_contract.py makes the same calls on a real internal app.
export function knowledgeFixture(name: string): Fixture {
  return JSON.parse(Deno.readTextFileSync(new URL(`knowledge_federation/internal/${name}.json`, FIXTURE_DIR)));
}

export function knowledgeFixtureNames(): string[] {
  return [...Deno.readDirSync(new URL("knowledge_federation/internal/", FIXTURE_DIR))].filter((e) => e.name.endsWith(".json")).map((e) => e.name.slice(0, -5)).sort();
}

// One file of the wire contract of unit KF0: `kind` is "activities" or "objects".
export function wireFixture(kind: "activities" | "objects", name: string): Record<string, unknown> {
  return JSON.parse(Deno.readTextFileSync(new URL(`knowledge_federation/${kind}/${name}.json`, FIXTURE_DIR)));
}

export interface FakeClaire {
  transport: Transport;
  // Each call: "METHOD path".
  calls: string[];
  // Replaces fields of the config response. The default is the config fixture.
  config: Record<string, unknown>;
  // When set, each call fails with this status.
  failStatus: number | null;
  // Clock of Claire, Unix time in seconds.
  now: number;
  // Answers that come before the fixtures. The key is "METHOD target".
  overrides: Map<string, { status: number; response: unknown }>;
  // When set, each inbox event gets this decision. If not, an event gets the decision of the fixture with the same body.
  decision: Record<string, unknown> | null;
  // The bodies of the inbox events, in sequence.
  events: Record<string, unknown>[];
  // The acknowledgements of outbox tasks, in sequence: "task ID result error code".
  acks: string[];
  // The acknowledgements of client tasks (contract 1.3), in sequence: the body of each request with its task ID.
  clientAcks: { id: number; result: string; error_code: string | null; actor: unknown }[];
  // The acknowledgements of interaction tasks (contract 1.6), in sequence: the body of each request with its task ID.
  interactionAcks: { id: number; result: string; error_code: string | null; mentions: unknown }[];
  // The bodies of the timeline events (unit FP4-4), in sequence.
  timelineEvents: Record<string, unknown>[];
  // When set, each timeline event gets this decision. If not, an event gets the decision of the fixture with the same body.
  timelineDecision: Record<string, unknown> | null;
  // The bodies of the quote events (contract 1.7), in sequence.
  quoteEvents: Record<string, unknown>[];
  // When set, each quote event gets this decision. If not, an event gets the decision of the fixture with the same body.
  quoteDecision: Record<string, unknown> | null;
  // The bodies of the requests of the knowledge federation (units KF1 and KF2), in sequence, with the path below the prefix of the internal API.
  knowledgePosts: { path: string; body: Record<string, unknown> }[];
  // The results of outbound activities of the knowledge federation (unit KF3), in sequence: the body of each request with its task ID.
  knowledgeAcks: { id: number; result: string; error_code: string | null }[];
  // The remote actors that an account follows, in addition to the actor of the timeline_interest fixture. Other actors are not followed.
  followed: Set<string>;
  // The limits of the parent chain in the answer for an actor of `followed`.
  context: { context_fetch: "on" | "off"; context_depth: number };
}

// A transport that answers from the fixtures. It refuses a request with a bad signature, the same as Claire.
export function fakeClaire(): FakeClaire {
  const fixtures = [...fixtureNames().map(fixture), ...knowledgeFixtureNames().map(knowledgeFixture)];
  const fake: FakeClaire = {
    calls: [],
    config: {},
    failStatus: null,
    now: NOW,
    overrides: new Map(),
    decision: null,
    events: [],
    acks: [],
    clientAcks: [],
    interactionAcks: [],
    timelineEvents: [],
    timelineDecision: null,
    quoteEvents: [],
    quoteDecision: null,
    knowledgePosts: [],
    knowledgeAcks: [],
    followed: new Set(),
    context: { context_fetch: "on", context_depth: 5 },
    transport: async (request) => {
      const url = new URL(request.url);
      const target = url.pathname + url.search;
      const body = new Uint8Array(await request.arrayBuffer());
      fake.calls.push(`${request.method} ${target}`);
      const timestamp = request.headers.get(TIMESTAMP_HEADER) ?? "";
      const expected = await signRequest(KEY, request.method, target, timestamp, body);
      if (request.headers.get(SIGNATURE_HEADER) !== expected || Math.abs(fake.now - Number(timestamp)) > 60) return Response.json({ error: "invalid_signature" }, { status: 401 });
      if (fake.failStatus !== null) return Response.json({ error: "internal_error" }, { status: fake.failStatus });
      const text = new TextDecoder().decode(body);
      if (request.method === "POST" && target.endsWith("/inbox-events")) {
        fake.events.push(JSON.parse(text));
        if (fake.decision !== null) return Response.json(fake.decision);
      }
      if (request.method === "POST" && target.endsWith("/timeline-events")) {
        fake.timelineEvents.push(JSON.parse(text));
        if (fake.timelineDecision !== null) return Response.json(fake.timelineDecision);
      }
      if (request.method === "POST" && target.endsWith("/quote-events")) {
        fake.quoteEvents.push(JSON.parse(text));
        if (fake.quoteDecision !== null) return Response.json(fake.quoteDecision);
      }
      const knowledge = /\/(knowledge-[a-z-]+)$/.exec(url.pathname);
      if (request.method === "POST" && knowledge !== null) fake.knowledgePosts.push({ path: `/${knowledge[1]}`, body: JSON.parse(text) });
      const knowledgeAck = /\/knowledge-outbox-tasks\/([0-9]+)\/ack$/.exec(target);
      if (request.method === "POST" && knowledgeAck !== null) fake.knowledgeAcks.push({ id: Number(knowledgeAck[1]), ...JSON.parse(text) });
      const ack = /\/outbox-tasks\/([0-9]+)\/ack$/.exec(target);
      if (request.method === "POST" && ack !== null) {
        const sent = JSON.parse(text) as { result: string; error_code: string | null };
        fake.acks.push(`${ack[1]} ${sent.result} ${sent.error_code}`);
      }
      const clientAck = /\/client-tasks\/([0-9]+)\/ack$/.exec(target);
      if (request.method === "POST" && clientAck !== null) fake.clientAcks.push({ id: Number(clientAck[1]), ...JSON.parse(text) });
      const interactionAck = /\/interaction-tasks\/([0-9]+)\/ack$/.exec(target);
      if (request.method === "POST" && interactionAck !== null) fake.interactionAcks.push({ id: Number(interactionAck[1]), ...JSON.parse(text) });
      const override = fake.overrides.get(`${request.method} ${target}`);
      if (override !== undefined) return Response.json(override.response, { status: override.status });
      const match = fixtures.find((f) => f.request.method === request.method && f.request.path === target && (f.request.body === undefined || JSON.stringify(f.request.body) === text));
      // An actor without a fixture: followed only when the test says so.
      if (match === undefined && request.method === "GET" && url.pathname.endsWith("/timeline-interest")) return Response.json({ followed: fake.followed.has(url.searchParams.get("actor") ?? ""), ...fake.context });
      if (match === undefined) return Response.json({ error: "not_found" }, { status: 404 });
      const response = target.endsWith("/config") ? { ...match.response, ...fake.config } : match.response;
      return Response.json(response, { status: match.status });
    },
  };
  return fake;
}
