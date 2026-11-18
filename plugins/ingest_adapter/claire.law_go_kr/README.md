# claire.law_go_kr

Web adapter for law.go.kr (moved from `src/claire/ingest/fetchers/law.py`). The outer page loads `*InfoP.do` in an iframe, and that page loads `*InfoR.do` with JavaScript. The adapter reads `*InfoR.do` directly.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` (rules for code contribution).
- Check: `claire plugin check plugins/ingest_adapter/claire.law_go_kr`.
- Tests never use the network.
