# Federation Go-Live: GF1 to GF17 on a Server Behind a CDN and a WAF

- **Document Identifier**: `DESIGN-FEDERATION-GO-LIVE-20261122`
- **Status**: Units GL1 to GL3 are implemented (2026-11-22): answer attribution, path record, and traffic checks (GL1, GL2) are in [`FEDERATION.md`](../implementation/FEDERATION.md) Section 7.2, and the optional edge service (GL3) is in [`FEDERATION.md`](../implementation/FEDERATION.md) Section 10.1. Unit GL4 (the go-live steps L0 to L6) is an owner step. Owner decisions GD1 to GD7 (Section 12) are locked (2026-11-22) with no open questions: GD4 removes the federation switch, GD5 makes every active account with a USERNAME an actor, GD6 removes step-up authentication from `profile_public`, and GD7 resolves GQ4 using Mastodon and Fedify defaults ([`FEDERATION.md`](../implementation/FEDERATION.md) Section 3.1).
- **Date**: 2026-11-22
- **Base commit**: `171257c`
- **Related Documents**:
  - [`FEDERATION.md`](../implementation/FEDERATION.md) (readiness checks §7.2, reverse proxy rules §10, known limits §12, contract version §5.4)
  - [`KNOWLEDGE_FEDERATION_DESIGN.md`](KNOWLEDGE_FEDERATION_DESIGN.md) (rules of the checks §14.1.1, gate KD13, acceptance path KA1 to KA5 and KD10, KD14)
  - [`KNOWLEDGE_FEDERATION.md`](../implementation/KNOWLEDGE_FEDERATION.md) §17 (unit KF6 and its verification)
  - [`CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md`](CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md) (development network, gateway as the one reference of the proxy rules, CD8)
  - [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) (FD6, principles FK1 to FK10)
  - [`EXTERNAL_ACCESS.md`](../implementation/EXTERNAL_ACCESS.md) (production edge, Cloudflare IP filter, Nginx example)

Evidence tags follow [`PRINCIPLES.md`](../PRINCIPLES.md) F1: **[code]**, **[decision]**, **[proposal]**, **[external]**, **[unverified]**.

---

## 1. Goal

1. The server of the owner passes GF1 to GF17, an account publishes a document, and a remote server gets it (no admin federation switch is needed under GD4).
2. When a check fails, the report tells which party answered and on which path. The owner can then repair the edge without an agent on the server (PRINCIPLES E4).
3. A check passes only when the real requests of other servers can also pass, as far as rule 2 of the checks (no request to another server) permits.

Non-goals at the start: a change of the gate (KD13), which decision GD4 then removed with the switch; new rules for the exchange of shared knowledge; the calibration (KA5); the hosting of the JSON-LD context (KA4).

---

## 2. Facts at the Base Commit

| No. | Fact | Tag |
| :--- | :--- | :--- |
| T1 | GF1 to GF17 exist. GF1 to GF5, GF12, GF13 read files, the database, or settings. GF6, GF8, GF9, GF11 read the sidecar heartbeat. GF7, GF10, GF14 to GF17 send requests from the Claire process to the public origin of this server. All requests go to the own public origin (a hairpin path). | [code] `federation_readiness.py` |
| T2 | All checks passed on a temporary development network of two servers with an Nginx gateway and a private CA. The fault profiles of KF6 made their checks fail. The permanent development network (DE1 to DE5) is not implemented. | [code] KNOWLEDGE_FEDERATION.md §17.4, CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md status |
| T3 | The production text says that the production server stands behind a protection layer such as Sophos Firewall Web Server Protection or Cloudflare. Claire has a filter for the Cloudflare address ranges. The exact chain of the owner's server is not in the repository. | [code] EXTERNAL_ACCESS.md; chain [unverified] |
| T4 | The repository has no readiness report of the owner's server. The support bundle has the sidecar heartbeat (`diagnostics/services.json`), not the report. | [code] `support_bundle_services.py` |
| T5 | GF6 and GF8 use `fetch` of Deno with its default `User-Agent`. GF8 sends no `Accept` header. GF9 sends `Content-Type: application/json` and signs with `signRequest` of Fedify, which uses only the draft-cavage form by default. | [code] `sidecar.ts`; Fedify 2.4.5 `sig.ts` |
| T6 | A delivery of Fedify sends `Content-Type: application/activity+json` and uses the "double knock" of two signature forms. The sidecar sets no `userAgent` option, so deliveries use the default agent of Fedify. | [code] Fedify 2.4.5 `send.ts`; `sidecar.ts` (`createFederation`) |
| T7 | The Claire request checks use the default `User-Agent` of `httpx`. | [code] |
| T8 | GF8 and GF6 pass when the answer has the marker header of this sidecar. The status and the received path are not examined. GF10 accepts any value of the marker. | [code] |
| T9 | The sidecar has no hook for the result of a delivery. The inbox and outbox counters are in memory only, and Claire does not get them. | [code] FEDERATION.md §12 items 12 and 16 |
| T10 | Contract version 1.12. The shapes are sealed; a new heartbeat field needs a new contract version. | [code] FEDERATION.md §5.4 |
| T11 | Cloudflare Bot Fight Mode runs outside the rule engine. Skip, bypass, and allow rules have no effect on it. A challenge answer has the header `cf-mitigated: challenge` and the type `text/html`. | [external] Cloudflare documents (Section 14) |
| T12 | Rules 920420, 920470, and 920600 of the OWASP Core Rule Set blocked ActivityPub requests (a media type with a `profile` parameter, a media type that is not in the allowed list). | [external] CRS issue 3497 |
| T13 | The Sophos WAF has a "Common Threat Filter" with ModSecurity rule IDs and a list "Skip filter rules", an option "Pass host header", and "path-specific routing" (site path routing). Users report site path routing that does not keep the full path. | [external, unverified] Sophos community threads only |

---

## 3. A New View of the Remaining Risk

### 3.1 Where the Earlier Work Stops

The earlier work proved the code: the checks exist, the sidecar works, and two servers pass all checks on a development network (T2). That network has one edge: an Nginx gateway from the repository. A real server has a different edge: a CDN, a WAF, a NAT hairpin, a DNS view, and path rules that the operator writes in a product that the repository cannot test (T3). The remaining risk is in that edge, and the checks see the edge only as one bit: "the marker came back" or "the marker did not come back" (T8).

### 3.2 Five Blind Spots

| ID | Blind spot | Effect |
| :--- | :--- | :--- |
| B1 | **No attribution.** A check does not know who answered. A CDN challenge, a WAF block page, an answer of Claire on a wrong path, a CDN error, and a timeout give the same code. | The detail says "Examine the reverse proxy rules" also when the cause is a bot rule of the CDN or a WAF rule. The owner repairs the wrong part. |
| B2 | **The probes do not look like real traffic.** GF9 sends `application/json`; real deliveries send `application/activity+json` with two signature forms (T5, T6). GF6 and GF8 send the agent of Deno and no ActivityPub `Accept` header. | WAF rules (T12) and bot rules (T11) match exactly these values. A check can pass while real deliveries fail. |
| B3 | **The hairpin path is not the path of other servers.** The probes go from the LAN to the public name. With a split DNS view, they do not cross the CDN that other servers cross. Without NAT hairpin, they fail while outside requests work. | A false pass or a false failure. The report does not record the path. |
| B4 | **The routing is written by the operator.** Four path rules, the `Host` header, the signature headers, and a body limit, in a product of a third party (T13). | Each operator repeats the same work; each product has its own faults, such as a path prefix that is removed. A changed path still reaches the sidecar and passes GF8 (T8). |
| B5 | **No signal after go-live.** No check shows that other servers reach this server or that deliveries succeed (T9). | "GF1 to GF17 pass" does not mean "publishing works". |

### 3.3 Principles of This Design

1. **Attribute each answer (R1).** Each probe records who answered: this sidecar, Claire, the CDN, another intermediary, or nobody.
2. **Probe as a remote server probes (R2).** Same headers, same media types, same signature forms, same sizes.
3. **Record the path (R3).** Record the address that the public name resolves to and whether the answer crossed the CDN. Give the owner one outside probe.
4. **Own the routing where possible (R4, option).** A shipped edge service makes the four path rules a tested file of the repository.
5. **Watch real traffic after go-live (R5, option).** Count verified requests of other servers and the results of deliveries.

---

## 4. Overview [proposal]

| Part | Content | Owner decision | Work unit |
| :--- | :--- | :--- | :--- |
| R1 | Answer attribution in all request checks; path and `Host` verification for GF8 | None (inside the existing checks) | GL1 |
| R2 | Probes with the headers, media types, and signature forms of real traffic | None | GL1 |
| R3 | Path record, warning check GF18, outside probe script | None | GL1 |
| R4 | Edge service `edge` (opt-in `CB_EDGE`) | GQ2 | GL3 |
| R5 | Traffic counters in the heartbeat, warning checks GF19 and GF20 | GQ3 | GL2 |
| Runbook | Go-live steps L0 to L6 for the owner's server (Section 10) | GQ1 (order) | GL4 |

R1 to R3 change the contract to 1.13 (T10). R5 uses the same contract change when it is in the same release.

---

## 5. R1: Answer Attribution

### 5.1 Answer Record

Each probe of a check makes one answer record:

| Field | Value |
| :--- | :--- |
| `party` | `sidecar` (the marker of this sidecar), `sidecar_other` (a marker with another value), `sidecar_unverified` (checks of Claire only: a marker, and no recent heartbeat to compare it with), `claire` (the Claire marker, Section 5.2), `cdn_challenge`, `cdn_block`, `cdn_error`, `intermediary`, `none` |
| `status` | HTTP status, or null for `none` |
| `error` | For `none` only: `dns`, `tls`, `connect`, `timeout`, `other` |
| `via_cdn` | `cloudflare` when the answer has the header `cf-ray`, else null |
| `server` | The `Server` header, at most 64 printable ASCII characters, else null |
| `media_type` | The media type of the answer, at most 64 characters, else null |
| `mitigation` | The value of the header `cf-mitigated`, at most 32 characters, else null |
| `problem`, `received` | Self-checks of the sidecar only: what went wrong (`not_routed`, `path_changed`, `host_changed`, `answer_not_live`, `signature_invalid`) and the path or the `Host` that the sidecar got |

### 5.2 Classification (first match)

1. No answer: `none` with the class of the error.
2. The marker header `X-Claire-Fedify-Server` has the value of this sidecar: `sidecar`. A check of Claire compares with the marker value of the heartbeat (new field `marker`, Section 5.5). Another value: `sidecar_other` (for example an old sidecar process behind a cache).
3. The header `X-Claire-Server` is present: `claire`. **[proposal]** Claire adds this header with the constant value `api` to each answer of the public listener. The sidecar header already tells that the software is Claire, so the new header tells nothing more.
4. The header `cf-mitigated` is present: `cdn_challenge` for the value `challenge`, else `cdn_block` (T11).
5. The header `cf-ray` is present and the status is 520 to 530: `cdn_error` (the CDN did not get an answer from the origin).
6. All other answers: `intermediary`.

Only Cloudflare has a CDN rule now, because only Cloudflare is named in the production text (T3). A rule for another CDN is added when a server needs it.

### 5.3 Codes and Details

Each request check keeps its codes for a passed result. For a failed result, the code comes from the party of the first failed probe. The detail names the path and gives the action. Examples (all in ASD-STE100 English; the console gets `en` and `ko` catalog keys, I18N ID5):

| Party | Code suffix | Detail |
| :--- | :--- | :--- |
| `claire` | `answered_by_claire` | "The public origin sends {path} to Claire. It must send this path to the sidecar." |
| `cdn_challenge` | `cdn_challenge` | "The CDN answered {path} with a challenge (cf-mitigated: challenge). Other servers cannot answer a challenge. Turn off the bot rule that makes the challenge for the federation paths." |
| `cdn_block` | `cdn_block` | "The CDN blocked {path} (cf-mitigated: {value}). Examine the security events of the CDN for this path." |
| `cdn_error` | `cdn_error` | "The CDN did not get an answer from the origin for {path} (HTTP {status}). Examine the connection from the CDN to the reverse proxy." |
| `intermediary` | `intermediary` | "An intermediary answered {path} with HTTP {status} (Server: {server}). If it is a WAF, find the rule ID in its log. Rules for the media type application/activity+json can block ActivityPub requests." |
| `sidecar_other` | `sidecar_other` | "The answer for {path} came from a different sidecar process. A cache can give old answers, or two sidecars run." |
| `none` | `dns_error`, `tls_error`, `connect_error`, `timeout` | Existing texts of GF9, with the path. |

Appendix A gives the repair for each code on Cloudflare, on the Sophos WAF, and on Nginx.

### 5.4 GF8: Verify What Arrived

GF8 sends each GET with the header `X-Claire-Self-Check: {nonce}`. The sidecar records the method, the path with the query, and the `Host` of each request with a known nonce, in memory, for 60 seconds, as for GF9 (KNOWLEDGE_FEDERATION_DESIGN.md §14.1.4 item 2). The record happens before the routing, so it works independently of account or content state (there is no federation switch under GD4).

| Result | Code | Detail |
| :--- | :--- | :--- |
| Path or query changed | `path_changed` | "The sidecar got {received} for {sent}. The reverse proxy must send the full path without a change." |
| `Host` changed | `host_changed` | Existing text of GF9 |
| Marker of this sidecar, but no record of the nonce | `answer_not_live` | "The answer has the marker of this sidecar, but the sidecar did not get the request. A cache gives old answers, or an intermediary removed the header X-Claire-Self-Check." |

GF6 uses the same method for its WebFinger request. Thus a cache in front of the sidecar shows directly in GF6, and not only in GF15 and GF16.

### 5.5 Contract 1.13

1. The heartbeat gets `marker` (the random value of the sidecar process).
2. `self_check` and `knowledge_self_check` get `answers`: the answer records of the failed probes, at most 8, with the path of each.
3. `knowledge_self_check.signed` becomes an object with one result for each signature form (Section 6).
4. The other rules of FEDERATION.md §5.4 apply: Claire refuses a heartbeat of 1.12, and GF2 fails. The fixtures of the contract get the new fields.

---

## 6. R2: Probes as a Remote Server Probes

| Check | Today | New |
| :--- | :--- | :--- |
| GF6 | GET with the agent of Deno | The agent string of the deliveries of this sidecar (the default agent of Fedify, T6) |
| GF8 | GET, no `Accept`, agent of Deno | `Accept: application/activity+json, application/ld+json; profile="https://www.w3.org/ns/activitystreams"` for paths below `/ap/`, `application/json` for NodeInfo; the agent of the deliveries |
| GF9 | `POST`, `application/json`, draft-cavage only | `Content-Type: application/activity+json`, a body in the form of an activity that contains the nonce, and two requests: one signed with draft-cavage and one with RFC 9421, each with its own nonce. `signed` has a result for each form; GF9 passes only when both are `ok` |
| GF10 | One `POST` of 1040384 bytes, any marker value, agent of `httpx` | The same `POST`, and a second small `POST` with `Content-Type: application/ld+json; profile="https://www.w3.org/ns/activitystreams"`. Both must reach this sidecar (marker of the heartbeat) without 413 |
| GF7, GF14 to GF17 | Agent of `httpx` | The agent `claire-bible/{version} (+{origin}/)`, in the form that fediverse software uses |
| GF14 | `Accept: application/activity+json` for the server actor | Also one request with the `ld+json` form with the profile |

Rules:

1. The signature of GF9 uses the same functions as a delivery and an inbox. Only the key is the self-check key (unchanged).
2. A probe that passes today can fail with the new form. This is intended: a failure in the check is better than a failure of real deliveries.
3. A bot rule of a CDN uses more than the headers. The same headers do not guarantee the same decision for a real remote server. R3 and the outside probe cover that gap.

---

## 7. R3: Path Record and the Outside Probe

### 7.1 Path Record

1. Each source (the sidecar for GF6, GF8, GF9; Claire for its request checks) records the addresses that the public host resolves to, with a class: `loopback`, `private`, `global`. Claire adds the class `cloudflare` for a global address in `CLOUDFLARE_NETWORKS` **[code]** (`security.py`).
2. The answer records have `via_cdn` (Section 5.1).
3. The report shows both in the values of the items.

### 7.2 GF18: The Probes Take the Path of Other Servers (Warning)

- Applies: production only, as GF5: a development server has a loopback or private address by its rule. Level: warning (KNOWLEDGE_FEDERATION_DESIGN.md §14.1.1 rule 6).
- Fails when an address of the public host is `loopback` or `private` for a source. Then the probes do not cross the public edge (a split DNS view or a hosts file), and other servers can get a different answer.
- Detail: "The public host {host} resolves to the private address {address} for {source}. The checks do not go the way of other servers. Run the outside probe (ops/federation_outside_probe.sh) from another network."
- Why a warning: rule 2 of the checks forbids a request to another server, so the check cannot prove the outside path.

### 7.3 Outside Probe (an Owner Step, Not a Check)

- Script `ops/federation_outside_probe.sh {origin}`: POSIX shell and `curl` only. It runs on any computer on another network (for example a laptop on a mobile hotspot).
- It sends the unsigned GET requests of GF6, GF8, and GF14 to GF17 and the small `POST` of GF10, with the headers of R2. It prints one line for each path: status, `sidecar: yes|no`, `cf-mitigated`, `Server`.
- It writes no file and sends nothing to another address. A signed request is not possible from outside; an unsigned `POST` that gets 401 with the marker shows that the path reaches the sidecar.
- The owner can give the output to an agent. The output has the public name of the server and no secret.

---

## 8. R4: Edge Service (Option, GQ2)

### 8.1 Function

- A service `edge` in `docker-compose.yml` (image of the stable Nginx release, pinned as the other images; license BSD-2-Clause **[external, unverified]**), in the Compose profile `edge`. `CB_EDGE` is exactly `0` or `1` **[code]** (`cb_manuscript.py`). No value has a default behavior: `cb-manuscript` refuses another value.
- With `CB_EDGE=1`, the edge publishes `CB_API_BIND:CB_API_PORT`. An overlay `docker-compose.edge.yml` removes the host ports of `api` and `fedify` (`ports: !reset []`). The external proxy sends all paths of all hosts of the server to this one upstream.
- The edge sends the federation paths of the app host (`CLAIRE_FQDN`) to `fedify:8790` and all other requests to Claire. The rules of FEDERATION.md §10 apply to the app host only **[code]**.

### 8.2 One Reference File

`ops/edge/claire/routes.conf` has the path rules. The edge (HTTP) and the gateway of the development network (TLS) include the same file. This is decision CD8 of the development network design, extended to production.

### 8.3 Rules of the Edge [proposal]

1. `Host` goes without a change. An unknown `Host` gets no answer (Nginx `444`).
2. Federation paths: no `X-Forwarded-*` header, body limit equal to `INBOX_BODY_LIMIT_BYTES` (1 MiB) **[code]**, a rate limit for `POST` below `/ap/` (FEDERATION.md §12: the sidecar has no rate limit; the values are calibrated in KA5).
3. Claire paths: no buffering of answers (NDJSON), read timeout 300 seconds, as the Nginx example of EXTERNAL_ACCESS.md **[code]**. The body limit is the largest body that Claire accepts **[unverified: measure in GL3]**.

### 8.4 Client Address

With the edge, the peer of Claire is the edge. In edge mode, Claire gets the requests of the edge on a Unix socket in the `run` volume, selected by the listener, as the internal federation API **[code]** (FEDERATION.md §5.1). The edge sets `X-Forwarded-For` to one value: the address that it saw. On that listener only, the login rate limits and the Cloudflare filter use this value. Without edge mode, nothing changes. A socket has no network exposure, and no container address must be configured.

The edge sees the external proxy as its peer. Thus the Cloudflare filter sees the same address as today.

### 8.5 Effect on the Checks

- A path fault of GF6, GF8, or GF14 can then come only from the edge file, which the tests run, or from an external proxy that does not send all requests to the edge. The detail of `answered_by_claire` changes in edge mode: "The external reverse proxy does not send {path} to the edge."
- The faults of B1, B2, and B3 stay possible in the CDN and the WAF. R1 to R3 are necessary also with the edge.

### 8.6 Change on an Existing Server

1. `./cb-manuscript update` with `CB_EDGE=1`.
2. In the same window, the external proxy gets one upstream rule in place of the path rules. The old path rules point to the port of the sidecar, which is then closed (the answer would be 502).
3. Return: `CB_EDGE=0`, `update`, and the old path rules.

### 8.7 Relation to FD6

FD6 says: the reverse proxy sends the federation paths to the sidecar; Claire does not proxy them; inbound federation load does not go through the one ASGI worker; a wrong rule fails closed with 404. The edge is a reverse proxy, Claire proxies nothing, and the reasons stay true. The edge changes the deployment contract of EXTERNAL_ACCESS.md, so it needs an owner decision (GQ2).

---

## 9. R5: Real Traffic After Go-Live (Option, GQ3)

### 9.1 Counters

The heartbeat gets `traffic` (contract 1.13), in memory, for the last 24 hours (FK1: the sidecar stores no canonical data):

| Field | Content |
| :--- | :--- |
| `inbound_verified` | Number of inbound requests with a verified signature of another host, number of other hosts, time of the last one. Self-check requests are not counted. |
| `inbound_rejected` | Number of inbound requests with a signature that did not verify |
| `deliveries` | Successes, failures for each class (`dns`, `tls`, `timeout`, `http_4xx`, `http_5xx`, `other`), time of the last success |

The source of the delivery results is a hook or the OpenTelemetry spans of Fedify **[unverified: the signal that Fedify 2.4.6 gives for a success; GL2 examines it before the work]**.

### 9.2 Checks (Warning)

- **GF19: Other servers reach this server.** Passes when `inbound_verified` has one request or more. Fails with: "No other server sent a signed request in the last 24 hours. Search the handle @{username}@{host} on another server to make the first contact (step L5)."
- **GF20: Deliveries succeed.** Does not apply when there was no delivery. Fails when there are failures and no success: "No delivery succeeded in the last 24 hours. The most frequent failure is {class}."
- The gate does not change (KD13). Warnings never change `report.passed`.

---

## 10. Go-Live Path of the Owner's Server [proposal]

The owner does each step; an agent does not operate production (PRINCIPLES E4). An agent reads the outputs that the owner gives.

| Step | Owner action | Completion criteria |
| :--- | :--- | :--- |
| L0 | Write down the edge chain of the server (CDN, WAF, the place of the path rules, NAT). Not in the repository. | The chain is known |
| L1 | `./cb-manuscript update` (with `CB_EDGE=1` for the edge, GD2). The sidecar starts with the other services (GD4) | GF2, GF3, GF4 pass |
| L2 | `./cb-manuscript app server check --federation --json`. Repair with Appendix A. Repeat. | GF1 to GF16 pass. GF18 passes, or the outside probe (L4) shows the same results. |
| L3 | No action: federation has no switch (GD4). Step L3 of the first version was the change of the switch | GF17 passes, or its warning has a known reason (KA2 rule) |
| L4 | Run `ops/federation_outside_probe.sh` from another network | Each path reaches the sidecar or Claire as in the table of FEDERATION.md §10; no `cf-mitigated`, no `intermediary` |
| L5 | From an account on another fediverse server, search and follow `@{username}@{host}`. Publish one document from `/syndication`. | The remote account sees the document. With GQ3 A: GF19 and GF20 pass |
| L6 | Normal use | A change of the edge repeats L2 and L4 (KA rule 2) |

---

## 11. Tests [proposal]

The rules of the project apply: unit tests only for interfaces, I/O, and calls of external libraries; one E2E test or more for each new feature; no test calls a public server.

| Test | Scope |
| :--- | :--- |
| Unit (Python, Deno) | Classification of answers (I/O of HTTP answers); contract 1.13 fixtures read by both sides (interface) |
| E2E: attribution | A real Claire process and a real sidecar process behind a small intermediary in the test process, on the development origin `http://127.0.0.1:{port}/`. Fault modes of the intermediary: a challenge answer with `cf-mitigated`; 403 for `application/activity+json`; a removed `/ap` prefix; a cache that gives an old answer with the marker; a changed `Host`; an answer of the Claire listener for `/ap/kb`. Each mode gives its code in `claire server check --federation --json`. No Docker. |
| E2E: console | `e2e/federation_admin.spec.js` shows the party and the action text of a failed item |
| E2E: outside probe | The script against the same local server prints the expected lines |
| E2E: edge (GQ2 A) | The edge container with the routing file, behind a gate variable as the DrFed tests (`CB_EDGE_TEST=1`); the gateway of the development network includes the same file when that network exists |
| E2E: traffic (GQ3 A) | Inside the existing DrFed scenarios: a follow and a delivery change the counters, GF19 and GF20 pass |

---

## 12. Owner Decisions (2026-11-22)

| ID | Question | Decision |
| :--- | :--- | :--- |
| GD1 | GQ1: order of the go-live and the acceptance path | A: the go-live of the owner's server (L0 to L6) is the go-live of the federation of accounts and is not a KA step. KA3 and KA4 continue in parallel; KA5 starts after them. |
| GD2 | GQ2: edge service | A: the edge is an opt-in (`CB_EDGE`), built with GL1. The owner turns it on at step L1. |
| GD3 | GQ3: traffic after go-live | A: GF19 and GF20 are in the same release as GL1 (one contract change, 1.13). |
| GD4 | The federation switch (owner, 2026-11-22, after the first report of the owner's server) | Federation is a function of each server that cannot be turned off; it is not a switch. The checks must not need a state that only the switch gives. |
| GD5 | The actor and the private profile (owner, 2026-11-22, after GD4) | Every `active` account with a USERNAME is an ActivityPub actor by default, even when `profile_public` is 0; `profile_public` does not gate actor existence (content scope resolved in GD7). |
| GD6 | The confirmation of identity for the profile switch (owner, 2026-11-22) | Changing `profile_public` in either direction needs no recent authentication (FD11 withdrawn). |
| GD7 | GQ4: notes, favourites, boosts, and quotes with a private profile (owner, 2026-11-22) | Option B: follow Mastodon and Fedify default policies with no Claire-unique ActivityPub restriction on private profiles. `Person.discoverable` reflects `profile_public` (default `false`), `manuallyApprovesFollowers` stays `false` (FD9), and notes, favourites (`Like`), boosts (`Announce`), and note quotes work for every actor in both profile states. Only personal KB and web profile visibility, the public directory, published KB `Article`s, and Knowledge Federation offers and credits require `profile_public = 1`. |

**GD4, facts and consequences.** The first report of the owner's server showed two switches that needed each other: the console switch `federation` needed GF1 to GF13, GF2 and GF6 to GF11 needed the sidecar, and the sidecar started only with the container switch `CB_FEDERATION=1`, which `update` had filled with the default `0`. The decision removes the two switches:

1. There is no server setting `federation`: no method reads or sets it, `POST /api/admin/federation` does not exist, and a row of an older version is not read. The sidecar and the internal API have no state "off" (contract 1.14).
2. There is no `CB_FEDERATION`: the service `fedify` has no Compose profile and starts with the other services.
3. The readiness checks are a report of the state, not a gate. Decision KD13 (the gate) and rule 8 of KNOWLEDGE_FEDERATION_DESIGN.md Section 14.1.1 have no object. GF14 to GF16, GF19, and GF20 apply on each server.
4. An account is an actor when it is `active` and has a public profile and a USERNAME (FD3 without the server switch). An account with a public profile is thus an actor from the update, without a new confirmation; a change to public still needs a recent sign-in (FD11), now on each server. **Changed** by GD5 and GD6 below: the profile is not a condition of the actor, and the change needs no recent sign-in.
5. The owner direction "the federation of shared knowledge is on by default" (KNOWLEDGE_FEDERATION_DESIGN.md Section 2) is replaced by the stronger rule: always on. The switch `kb_auto_contribution` stays (KD4).
6. The edge service stays an option (GD2). Without it, the external reverse proxy needs the path rules of FEDERATION.md Section 10.

**GD5, GD6, and GD7, facts and consequences.** Item 4 of GD4 kept `profile_public` as the gate of the actor and kept step-up confirmation for a change to public. The owner resolved these points in GD5, GD6, and GD7:

1. Each account that is `active` and has a USERNAME is an actor in both server modes; `profile_public` is not a condition. Other servers find the account (WebFinger, actor document) and can follow it (FD3 changed).
2. Under GD7 (resolving GQ4), Claire follows Mastodon and Fedify default policies with no Claire-unique ActivityPub restriction on private profiles: `Person.discoverable` reflects `profile_public` (default `false`), `manuallyApprovesFollowers` stays `false` (FD9), and toggling `profile_public` on an active actor queues `update_actor` so `Update(Person)` updates remote servers.
3. Notes (`Create(Note)`, `Update(Note)`, `Delete`), favourites (`Like`, `Undo`), boosts (`Announce`, `Undo`), and quote requests or approvals on own `Note`s work for every actor in both profile states (Option B). Own notes are served at `/ap/objects/{id}` and included in `/ap/actors/{id}/outbox` for all actors.
4. `profile_public = 1` gates only: (a) web profile `/@{username}/` and personal KB `/kb/{number}/` visibility (404 to non-owners), (b) the public account directory (`/accounts`), (c) published knowledge base documents (`Article`: publishing, object and outbox exposure, `create`/`update` delivery, inbound reactions and quote requests on published documents, and approving a quote of a published document), and (d) Knowledge Federation (outbound offers, automatic contribution remote state, contributor credit on public documents, and recent activity account names).
5. A change of `profile_public` to 0 is not a pause of the actor (FD10 changed); followers, follows, timeline, mentions, notifications, and notes stay active, and changing `profile_public` in either direction needs no recent sign-in (`PATCH /api/me`, GD6; FD11 withdrawn), so `/settings/account` opens no confirmation dialog.

Implementation details and status codes are in [`FEDERATION.md`](../implementation/FEDERATION.md) Section 3.1.

### GQ4: Notes, Favourites, and Boosts of an Account with a Private Profile [decided: GD7]

| Option | Rule | Effect |
| :--- | :--- | :--- |
| A | An account with a private profile writes no note, no favourite, and no boost (409 `profile_private`). It can follow, read, and remove. | Nothing of the account becomes public while the profile is private. A user that wants to answer a post must make the profile public first. |
| B | The account can write them, and they are public as for each account. Only the published documents and the personal knowledge base stay private. | The usual behaviour of a locked account on other software for replies and reactions, but a note of a "private" account is public, because the server has no other audience. |
| C | A new audience "followers only" for the notes of an account with a private profile | The nearest to the usual ActivityPub behaviour. It needs a design: the audience in the note record, the delivery to followers only, the object path with an authorized fetch, and the approval of follow requests (FD9 accepts each follow now). |

The questions as they were asked:

### GQ1: Order of the Go-Live and the Acceptance Path (KD10)

KD10 orders the remaining checks of the knowledge federation: KA1, KA2, KA3, KA4, KA5, and a step starts only after the step before it. At the time of this question, the one switch `federation` turned on both kinds of federation (KD13, later withdrawn by GD4). KA3 is not complete (the scenarios of KF3 to KF5 with real sidecars) and KA4 is not done (T2).

| Option | Rule | Effect |
| :--- | :--- | :--- |
| A (recommended) | The go-live of the owner's server (L0 to L6) is the go-live of the federation of accounts. It is not a KA step. KA3 and KA4 continue in parallel, and KA5 (calibration) starts after them. | Publishing and following start now. The knowledge federation is on but has no peer until another Claire server on the internet appears; the first such exchange is a part of KA5 anyway (KD14). |
| B | No production go-live before KA3 and KA4 are complete (historical option asked when a production switch existed before GD4; KD10 in the strict form) | The permanent development network (DE1 to DE5) and the KF3 to KF5 scenarios come first. Publishing waits. |
| C | Two switches: one for accounts, one for shared knowledge | Reverses KD13 and adds a switch to the console, the checks, and the sidecar. Not recommended. |

### GQ2: Edge Service (R4)

| Option | Rule | Effect |
| :--- | :--- | :--- |
| A (recommended) | Build the edge with GL1 as an opt-in (`CB_EDGE`). The owner turns it on at step L1. | The external proxy gets one upstream rule; the path rules become a tested file; one release cycle less if the first report shows a path fault. The main web path of all users goes through one more hop, with a return path (Section 8.6). |
| B | Build GL1 first. Decide the edge after the first report of the owner's server. | The data comes first; one more release cycle if the report shows a path fault. |
| C | No edge. Appendix A goes into EXTERNAL_ACCESS.md as recipes for each product. | No change of the deployment contract; each operator keeps the path rules. |

### GQ3: Traffic After Go-Live (R5)

| Option | Rule | Effect |
| :--- | :--- | :--- |
| A (recommended) | GL2 in the same release as GL1 (one contract change) | Step L5 has a signal in the console; the owner sees whether publishing works |
| B | After the go-live | One more contract change later |
| C | No traffic checks | "GF1 to GF17 pass" stays the only signal |

---

## 13. Work Units

| Unit | Scope | Completion criteria |
| :--- | :--- | :--- |
| GL1 (implemented) | R1, R2, R3: answer records, the Claire marker header, GF8 and GF6 path and `Host` verification, the probes of R2, the path record, GF18, contract 1.13, catalog keys, the outside probe script | Each fault mode of Section 11 gives its code and party; the probes send the headers of R2 (a test reads the headers at the intermediary); GF18 fails for a private address of the public host and passes for a global address |
| GL2 (implemented) | R5: counters, GF19, GF20 | The DrFed scenario changes the counters; no delivery: GF20 does not apply. Not done: the DrFed scenario was not run (it needs Docker and the gate `CB_DRFED_TEST=1`); the counters have tests of the sidecar code and of the checks |
| GL3 (GD2, implemented) | R4: edge service, overlay, `CB_EDGE` in `cb-manuscript`, Unix socket listener for the edge, shared routing file with the gateway | GF1 to GF17 pass on the development network through the edge with one upstream rule; the login rate limit counts the address that the edge saw; `CB_EDGE=0` gives the current topology |
| GL4 | Steps L0 to L6 (owner) | Section 10 |

Choices made in the code of GL1 and GL2 (the implementation document has the details):

1. GF18 to GF20 have the group `operation` ("Path and traffic") in the report and in the console.
2. A check of Claire that gets a sidecar marker without a recent heartbeat fails with `sidecar_unverified`. Thus GF10 fails also when there is no heartbeat.
3. A check that expects Claire (GF7, GF17) and gets an answer of the sidecar fails with `answered_by_sidecar`.
4. The self-check key and the nonce rules of GF9 did not change. The nonce is in the body and in the header `X-Claire-Self-Check`.
5. The sidecar compares the path and the decoded query parameters of a self-check request, not the percent-encoding of the query.
6. The sidecar resolves the public host with `Deno.resolveDns`, which does not read the hosts file. Claire uses the system resolver.
7. The traffic counters also count the deliveries of the knowledge federation, which do not go through Fedify.

Choices made in the code of GL3:

1. The edge is for production only: `cb-manuscript` refuses `CB_EDGE=1` in development, because the development stack has host ports for the role hosts and the prototype role. (The earlier `CB_FEDERATION=1` prerequisite was removed by GD4.)
2. Section 8.4 said that the edge sets `X-Forwarded-For`. The code uses a separate header, `X-Claire-Edge-Peer`, and Claire puts its value in the peer address of the request. The `X-Forwarded-For` of the external reverse proxy then goes to Claire without a change, and the trusted-proxy rule works as without the edge.
3. Section 8.3 rule 1 (an unknown `Host` gets no answer) is not in the edge: the edge does not know the legacy hosts, which are in the database. Claire refuses an unknown host, as without the edge. The federation rules apply only to the `Host` of the app.
4. Section 8.3 rule 2 (a rate limit for `POST` below `/ap/`) is not in the edge. The peer of the edge is the external reverse proxy, so a limit for each peer address is one limit for all servers; a correct key and the values need the data of KA5.
5. Section 8.3 rule 3: the edge has no body limit on the paths of Claire, because Claire has its own limits.
6. Section 8.5 said that the text of `answered_by_claire` changes in edge mode. The code has one text for the two modes: the check does not know if the edge runs.
7. The completion criterion "GF1 to GF17 pass on the development network through the edge" was done with the edge image in front of a real Claire process and a real sidecar process on one host (the gate `CB_EDGE_TEST=1`). The development network of two servers does not exist yet (phases DE1 to DE5), and its gateway does not yet include the routing file.

Documents to change with the code: FEDERATION.md §7.2, §9, §10, §12; KNOWLEDGE_FEDERATION.md §17; EXTERNAL_ACCESS.md (Appendix A as recipes; the edge with GQ2 A); COMMANDS.md (the help names GF1 to GF20); the status line of this document.

---

## Appendix A: Repair for Each Code [proposal]

| Code (check) | Probable cause | Cloudflare | Sophos WAF [external, unverified] | Nginx |
| :--- | :--- | :--- | :--- | :--- |
| `cdn_challenge` (any) | A bot rule or a security level makes a challenge | Bot Fight Mode cannot be skipped by a rule (T11): turn it off, or use Super Bot Fight Mode with a skip rule for the federation paths. Remove "Managed Challenge" rules and "I'm Under Attack" mode for these paths. | — | — |
| `cdn_block` (any) | A WAF rule or an IP rule of the CDN | Security events: find the rule, add a skip for the federation paths | — | — |
| `cdn_error` (any) | The CDN does not reach the origin | Origin address, TLS mode, the firewall of the origin | The WAF rule for the host is active | — |
| `intermediary` 403 on `POST` (GF9, GF10) | A WAF rule for the media type or its `profile` parameter (T12) | — | Find the rule ID in the WAF log; add it to "Skip filter rules" of the rule for the app host, or allow the media types `application/activity+json` and `application/ld+json` | — |
| `intermediary` other | A block page, a login page, URL hardening | — | No static URL hardening, no form hardening on the federation paths | — |
| `answered_by_claire` (GF6, GF8, GF14) | No path rule | Tunnel ingress: path rules for the four paths | Path-specific routing for the four paths to the sidecar server | Four `location` blocks (EXTERNAL_ACCESS.md), or the edge (GQ2) |
| `path_changed` (GF8) | The rule removes the path prefix | — | Site path routing must keep the full path | `proxy_pass` without a URI part |
| `host_changed` (GF6, GF8, GF9) | The proxy sets `Host` to the upstream name | — | Turn on "Pass host header" | `proxy_set_header Host $host` |
| `signature_invalid` (GF9, one form) | The proxy changes the body or a signature header | Turn off transforms of the body for these paths | Turn off content rewrite for these paths | No `sub_filter`, no body change |
| `inbox_body_too_large` (GF10) | Body limit below 1 MiB | Limit of the plan [unverified] | Body limit of the protection policy | `client_max_body_size 1m` or more on `/ap/` |
| `answer_not_live` (GF6, GF8), stale actor (GF15) | A cache in front of the sidecar | No "Cache Everything" rule for the federation paths | — | No `proxy_cache` on the federation paths |
| `dns_error`, `tls_error`, `connect_error`, `timeout` (sidecar or Claire) | The hairpin path does not work | — | NAT loopback for the public address, or a DNS view to the WAF address | — |
| GF18 | Split DNS view or hosts file | — | — | — |

---

## 14. Sources

- Cloudflare: [Bot Fight Mode (free plan)](https://developers.cloudflare.com/bots/get-started/free/), [Detect a Challenge Page response](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response) (read 2026-11-22).
- OWASP Core Rule Set: [issue 3497](https://github.com/coreruleset/coreruleset/issues/3497) (read 2026-11-22).
- Sophos community: [skip filter rules and rule IDs](https://community.sophos.com/sophos-xg-firewall/f/discussions/118445/id-numbers-from-waf-log---common-threat-filter---skip-filter-rules), [host headers on path routing](https://community.sophos.com/utm-firewall/f/web-server-security/50192/host-headers-on-path-routing), [site path routing does not keep the full path](https://community.sophos.com/utm-firewall/f/web-server-security/113151/waf---site-path-routing-is-not-preserving-the-full-path-when-directing-traffic-to-the-real-web-server) (read 2026-11-22; community text, not Sophos documentation).
- Fedify 2.4.5 source from the Deno cache of the sidecar: `src/federation/send.ts` (media type, double knock), `src/sig/http.ts` (default form of `signRequest`).
