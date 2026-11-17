"""Federation of shared knowledge between Claire servers (KNOWLEDGE_FEDERATION_DESIGN.md).

Unit KF0 gives the module ``vocabulary``: the JSON-LD context, the term names, the builders, and the validators of the wire contract. That module has no database access and no network access. The units KF1 and KF2 give the store modules of ``claire.db``: ``known_servers``, ``journal``, ``access``, and ``remote_kbs``. No module of the package makes a request to another server: the sidecar does that.
"""
