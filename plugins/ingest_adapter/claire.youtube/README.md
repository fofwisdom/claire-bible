# claire.youtube

Fetcher for YouTube (moved from `src/claire/ingest/fetchers/youtube.py`). The page and oEmbed requests use the network client of the plugin. `yt_dlp` and `youtube_transcript_api` open their own connections; the manifest names them in `permissions.network_modules`. Documents keep the source type `youtube` (stored documents use it; the core treats it as media, INGEST_ADAPTER_PLUGIN_DESIGN.md PL-D16).

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` (rules for code contribution).
- Check: `claire plugin check plugins/ingest_adapter/claire.youtube`.
- Tests never use the network.
