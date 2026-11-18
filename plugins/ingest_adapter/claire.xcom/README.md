# claire.xcom

Fetcher for x.com posts (moved from `src/claire/ingest/fetchers/xcom.py`). It takes x.com links but calls only the mirror API hosts; the fallback uses the core web chain.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` (rules for code contribution).
- Check: `claire plugin check plugins/ingest_adapter/claire.xcom`.
- Tests never use the network.
