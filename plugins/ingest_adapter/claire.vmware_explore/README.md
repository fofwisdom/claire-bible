# claire.vmware_explore

VMware Explore session videos and presentation PDFs (moved from `src/claire/ingest/fetchers/presentation_vmware_explore.py` and the VMware branch of `fetch_video`). The media part is the core service `fetch_media` with the Brightcove player URL; the PDF composition is the core service `compose_video_presentations`. The session page and the PDF download use the network client of the plugin; the rendered page comes from the core renderer `render_html_cdp`.

- Rules for plugins: `docs/design/INGEST_ADAPTER_PLUGIN_DESIGN.md` section 14.
- Check: `claire plugin check plugins/ingest_adapter/claire.vmware_explore`.
- Tests never use the network.
