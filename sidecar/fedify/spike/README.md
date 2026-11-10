# FP0 Technical Checks

These programs confirm the technical items FT1 to FT7 in Section 15.1 of [`ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md`](../../../docs/design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md). They are checks, not product code. They call no public server. Phase FP1 replaces this directory with the sidecar source and its tests.

## Versions

Deno 2.9.8, `@fedify/fedify` 2.3.15, `@fedify/sqlite` 2.3.15 (`deno.lock`), uvicorn 0.54.0. Date of the checks: 2026-11-10.

## Commands

Run the first command from this directory. Run the second command from the repository root.

```bash
FP0_SOCKET=/tmp/fp0.sock deno run --frozen --allow-net=127.0.0.1,claire.internal:80,unix:/tmp/fp0.sock --allow-read --allow-write --allow-env=FP0_SOCKET ft1_to_ft6.ts
```

```bash
uv run python sidecar/fedify/spike/ft7_dual_listener.py
```

## Results

| ID | Item | Result |
| :--- | :--- | :--- |
| FT1 | Numeric actor identifier with a WebFinger USERNAME mapping | Confirmed. `mapHandle` maps `acct:alice@host` to `/ap/actors/{public_id}`. The number is not accepted as a USERNAME (404). |
| FT2 | Fixed canonical origin that is different from the request host | Confirmed. With the `origin` option, a request to `127.0.0.1:8769` gives URLs on the canonical origin only. |
| FT3 | Queue messages contain sender private keys | Confirmed: they do. An `outbox` message in `fedify_message` has the RSA and Ed25519 private keys in JWK format. The sidecar database is a secret store. |
| FT4 | Private addresses are refused by default | Confirmed for fetch and for delivery (`UrlError: Invalid or private address`). The control run with `allowPrivateAddress: true` reaches the local server. |
| FT5 | Followers total to the public, full list to delivery only | Confirmed. The dispatcher gives an empty list when the context has a request. The counter gives `totalItems`. Delivery gets the follower inbox. |
| FT6 | TCP listener with HTTP calls on a Unix socket | Confirmed. `Deno.serve({ path })` and `Deno.createHttpClient({ proxy: { transport: "unix" } })`. The permission flags must name the socket and the placeholder host of the request URL. |
| FT7 | Internal listener in the same event loop as the public listener | Confirmed. One uvicorn `Server` serves a TCP socket and a Unix socket. `scope["server"]` is `(path, None)` on the Unix socket. A forged `Host` header on the TCP listener does not change it. |
