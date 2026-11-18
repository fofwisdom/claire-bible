# claire.discourse

General web adapter for Discourse forums (moved from `src/claire/ingest/fetchers/discourse.py`). It runs after the static fetch for every host (`*`) and returns nothing for a page that is not a Discourse topic.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` (rules for code contribution).
- Check: `claire plugin check plugins/ingest_adapter/claire.discourse`.
- Tests never use the network.
