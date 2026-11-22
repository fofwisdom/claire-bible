// The path record and the traffic counters of the readiness checks (FEDERATION_GO_LIVE_DESIGN.md Sections 7 and 9).

import assert from "node:assert/strict";
import { addressClass, deliveryFailureClass, deliveryMeterProvider, resolvePublicHost, TRAFFIC_WINDOW_SECONDS, TrafficCounters } from "../src/probes.ts";

Deno.test("the path record gives the class of each address of the public host", async () => {
  assert.deepEqual(["127.0.0.1", "10.1.2.3", "172.16.0.1", "172.32.0.1", "192.168.1.1", "100.64.0.1", "169.254.1.1", "203.0.113.5", "::1", "fd00::1", "fe80::1", "2001:db8::1", "::ffff:10.0.0.1"].map(addressClass), ["loopback", "private", "private", "global", "private", "private", "private", "global", "loopback", "private", "private", "global", "private"]);
  // An address in the origin needs no resolver.
  assert.deepEqual(await resolvePublicHost("http://127.0.0.1:8766", () => Promise.reject(new Error("not used"))), { host: "127.0.0.1", addresses: [{ address: "127.0.0.1", class: "loopback" }], error: null });
  const resolver = (_host: string, type: "A" | "AAAA") => (type === "A" ? Promise.resolve(["192.168.10.5", "203.0.113.5"]) : Promise.reject(new Error("no record")));
  assert.deepEqual(await resolvePublicHost("https://claire.example.com", resolver), { host: "claire.example.com", addresses: [{ address: "192.168.10.5", class: "private" }, { address: "203.0.113.5", class: "global" }], error: null });
  assert.deepEqual(await resolvePublicHost("https://claire.example.com", () => Promise.reject(new Error("no record"))), { host: "claire.example.com", addresses: [], error: "dns" });
});

Deno.test("the traffic counters keep the last 24 hours and do not count this server", () => {
  let now = 1_800_000_000;
  const traffic = new TrafficCounters(() => now);
  traffic.inboundVerified("claire.example.com", "claire.example.com");
  traffic.inboundVerified("Remote.example", "claire.example.com");
  traffic.inboundVerified("remote.example", "claire.example.com");
  traffic.inboundVerified("other.example", "claire.example.com");
  traffic.inboundRejected();
  traffic.deliverySucceeded();
  traffic.deliveryFailed(deliveryFailureClass({ statusCode: 403 }));
  traffic.deliveryFailed(deliveryFailureClass({ statusCode: 502 }));
  traffic.deliveryFailed(deliveryFailureClass(new Error("getaddrinfo ENOTFOUND")));
  traffic.deliveryFailed(deliveryFailureClass(new Error("connection refused")));
  assert.deepEqual(traffic.snapshot(), {
    window_seconds: TRAFFIC_WINDOW_SECONDS,
    inbound_verified: { count: 3, hosts: 2, last_at: now },
    inbound_rejected: { count: 1, last_at: now },
    deliveries: { succeeded: 1, failed: { dns: 1, tls: 0, timeout: 0, http_4xx: 1, http_5xx: 1, other: 1 }, last_success_at: now },
  });
  now += TRAFFIC_WINDOW_SECONDS + 3600;
  assert.deepEqual(traffic.snapshot(), {
    window_seconds: TRAFFIC_WINDOW_SECONDS,
    inbound_verified: { count: 0, hosts: 0, last_at: null },
    inbound_rejected: { count: 0, last_at: null },
    deliveries: { succeeded: 0, failed: { dns: 0, tls: 0, timeout: 0, http_4xx: 0, http_5xx: 0, other: 0 }, last_success_at: null },
  });
});

Deno.test("the meter provider reads only the delivery counter of Fedify", () => {
  const seen: boolean[] = [];
  const provider = deliveryMeterProvider((success) => seen.push(success)) as { getMeter: () => { createCounter: (name: string) => { add: (value: number, attributes?: Record<string, unknown>) => void }; createHistogram: (name: string) => { record: (value: number) => void } } };
  const meter = provider.getMeter();
  meter.createCounter("activitypub.delivery.sent").add(1, { "activitypub.delivery.success": true });
  meter.createCounter("activitypub.delivery.sent").add(1, { "activitypub.delivery.success": false });
  meter.createCounter("activitypub.inbox.activity").add(1, { "activitypub.delivery.success": true });
  meter.createHistogram("activitypub.delivery.duration").record(5);
  assert.deepEqual(seen, [true, false]);
});
