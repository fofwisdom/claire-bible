# Fetcher Contribution Architecture and Private Network Policy

- **Status**: Implemented. Design principles only. Current behavior: [`INGESTION.md`](../implementation/INGESTION.md) §2, §4 (the fetcher and web adapter contracts, the registry with built-in fetchers, the single classifier, and the private network guard).
- **Date**: 2026-09-15 · condensed 2026-11-16
- **Related**: [`ENVIRONMENT_VARIABLES.md`](../implementation/ENVIRONMENT_VARIABLES.md), [`INGEST_ADAPTER_PLUGIN_DESIGN.md`](INGEST_ADAPTER_PLUGIN_DESIGN.md) (implemented: site adapters are plugins)

## Goal / problem

Organizations that run their own Claire server need two things:

1. Fetch internal sources (wiki, code hosting, ticket systems) on private addresses.
2. Add their own fetchers without edits to core files, so that a pull from upstream gives no merge conflict.

Before this design, URL classification was duplicated in the router and in the Telegram bot, site adapters were conditions inside the web fetcher, and adapters returned unnamed tuples. A full block of private addresses made internal sources impossible; a full allow exposed cloud metadata endpoints.

## Decisions in force

1. **Two contracts.** A *fetcher* owns a whole URL pattern or protocol. A *web adapter* works inside the general web chain for one site and returns nothing to pass control on. Adapters return a named result type. Reason: clear extension points and no field-order errors.
2. **One registry and one classifier.** The router, the web chain, and the Telegram labels use the same registry. Reason: the label that the user sees and the fetcher that runs must agree.
3. **Priority decides order**, higher first. Site-specific adapters run before the generic static fetch; generic adapters run after it.
4. **Private addresses are blocked by default, and the operator can open them**: all at once, or by an allowlist of networks and host names. Reason: on-premise servers need internal sources; a public server must not reach internal services by accident.
5. **Some networks are never allowed**, whatever the settings: cloud metadata (link-local), multicast, broadcast, and unspecified addresses. Reason: these give credentials or have no fetch purpose.
6. **Check every resolved address of a host**, not only the first. Reason: one host name can resolve to a public and a private address.
7. **Limit size and time of responses.** Reason: one bad source must not use all memory or block a worker.

## Rejected alternatives

- **Hard-coded site conditions inside the web fetcher.** Every new site needed a core edit.
- **A global switch only (all private addresses on or off).** Too coarse for a server that needs one internal host.

## Limits for later changes

- A new fetcher that sends HTTP requests must use the shared guard.
- Keep classification in the registry only; do not add URL rules in a channel.
