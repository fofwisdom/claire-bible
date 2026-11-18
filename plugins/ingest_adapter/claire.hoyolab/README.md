# claire.hoyolab

HoYoLAB articles and HoYoWiki entries (moved from `src/claire/ingest/fetchers/hoyolab.py` and `hoyowiki.py`). One plugin, because both web adapters claim `*.hoyolab.com` (two plugins would overlap). The command `claire plugin run claire.hoyolab wiki-crawl` replaces `claire hoyowiki`.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` (rules for code contribution).
- Check: `claire plugin check plugins/ingest_adapter/claire.hoyolab`.
- Tests never use the network.
