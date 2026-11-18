# claire.share_link

Share link resolver (moved from `src/claire/ingest/fetchers/redirect.py` and the `redirect` branch of `router.fetch`). The entry has the host pattern `*` and `can_handle` takes a host `share.google` or a host that starts with `share.`. It gives no document of its own: it routes the destination with `reroute` (at most two levels), or uses the web chain when it finds no destination.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` section 14.
- Check: `claire plugin check plugins/ingest_adapter/claire.share_link`.
- Tests never use the network.
