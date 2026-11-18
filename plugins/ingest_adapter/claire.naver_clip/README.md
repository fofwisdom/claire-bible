# claire.naver_clip

Fetcher for Naver Clip and Naver TV (moved from `src/claire/ingest/fetchers/naver_clip.py`). Documents have the source type `video`. The media streams come from the Naver CDN (`*.pstatic.net` in the recorded answers). When a real stream comes from another host, the host check of `permissions.network` refuses it with a clear error: add the host and raise the revision.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` section 14.
- Check: `claire plugin check plugins/ingest_adapter/claire.naver_clip`.
- Tests never use the network.
