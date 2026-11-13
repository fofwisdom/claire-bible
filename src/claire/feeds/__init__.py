"""Account feeds (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, units FP4-F1 and FP4-F2).

- ``parse``: the item normal form for RSS 2.0 (and RSS 1.0), Atom 1.0, and JSON Feed 1.0 and 1.1.
- ``fetch``: one HTTP fetch of a feed with conditional ``GET``, the private address check on each hop, and a size limit.
- ``poller``: the poll of the feeds of an account or of all due feeds, with backoff on ``429`` and ``5xx``.
- ``opml``: OPML import and export.

The store functions are in ``claire.store.feeds``. Collection is not ingestion: a feed item never enters a theme without an action of the owner.
"""
