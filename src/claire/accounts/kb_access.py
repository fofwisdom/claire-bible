"""Capabilities in a knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 6.2, 6.5, 6.6, 6.9).

One function decides the matrix: ``decide``. Routes do not check access on their own (Section 6.9 rule 1). Each route declares one capability in ``api.security.ROUTE_POLICY`` (Section 6.2 rule 5).

The result is ``"allow"``, ``"forbidden"`` (403), or ``"not_found"`` (404). A private personal knowledge base and an unknown number give the same 404 (PRINCIPLES C1).

Not in this module yet: the reputation rule of Section 8.6 (unit CT-11).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "forbidden", "not_found"]
# Credential scopes (Section 6.6). ``None``: an anonymous request.
Scope = Literal["owner", "write", "readonly"]

# Capabilities of a shared knowledge base (Section 6.2).
SHARED_CAPABILITIES = (
    "read", "join", "contribute_direct", "contribute_theme", "fork", "review", "curate", "maintain",
    "people", "settings", "remove", "moderators", "delete",
)
# ``share``: make a share link of a document. It is not in the table of Section 6.2: each account that reads a shared knowledge base has it (not a banned one); in a personal knowledge base only its account has it. Each scope has it, as before this design.
SHARE = "share"
# Capabilities of a personal knowledge base (Section 6.5): the owner has all rights; nobody else writes.
PERSONAL_CAPABILITIES = ("read", "write", "settings", SHARE)
# A route that does not act on a knowledge base declares ``NO_KB``.
NO_KB = "none"
# The capabilities that a route can declare. ``write``: a write into the personal knowledge base of the account of the credential (ingest, regeneration, publish); a shared knowledge base refuses it (CT4).
ROUTE_CAPABILITIES = frozenset({NO_KB, "write", SHARE, *SHARED_CAPABILITIES})

# The capabilities that an archived shared knowledge base refuses (Section 10.1: read-only).
ARCHIVED_BLOCKED = frozenset({"contribute_direct", "contribute_theme", "review", "curate", "maintain", "remove"})

_NON_MEMBER = frozenset({"read", "join", "contribute_theme", "fork"})
_MEMBER = _NON_MEMBER | {"contribute_direct"}
_COLLABORATOR = _MEMBER | {"review", "curate", "maintain"}
_MODERATOR = _COLLABORATOR | {"people", "settings", "remove"}
_ADMIN = frozenset(SHARED_CAPABILITIES)
_BANNED = frozenset({"read", "fork"})
# Section 6.6: a scope limits the capabilities of the account; it never adds one. ``write`` is for the tools of the account; the decisions of the person stay with ``owner``.
_SCOPE_SHARED = {
    "owner": _ADMIN | {SHARE},
    "write": frozenset({"read", "contribute_direct", "contribute_theme", SHARE}),
    "readonly": frozenset({"read", SHARE}),
}
_SCOPE_PERSONAL = {
    "owner": frozenset(PERSONAL_CAPABILITIES),
    "write": frozenset({"read", "write", SHARE}),
    "readonly": frozenset({"read", SHARE}),
}


@dataclass(frozen=True, slots=True)
class Actor:
    """The requester, reduced to what the matrix needs."""

    account_id: int | None  # None: anonymous
    scope: Scope | None  # the scope of the credential; None: anonymous
    is_admin: bool = False
    # Membership in the shared knowledge base of the decision: (role, state) or None.
    membership: tuple[str, str] | None = None


def shared_capabilities(actor: Actor) -> frozenset[str]:
    """The capabilities of ``actor`` in a shared knowledge base (Section 6.2 with rules 1 and 4, and Section 6.6)."""
    if actor.account_id is None or actor.scope is None:
        return frozenset({"read"})
    if actor.is_admin:
        caps = _ADMIN
    elif actor.membership is None or actor.membership[1] == "pending":
        caps = _NON_MEMBER
    elif actor.membership[1] == "banned":
        caps = _BANNED
    else:
        caps = {"member": _MEMBER, "collaborator": _COLLABORATOR, "moderator": _MODERATOR}[actor.membership[0]]
    if caps != _BANNED:
        caps = caps | {SHARE}
    return frozenset(caps & _SCOPE_SHARED[actor.scope])


# Route capabilities that are decisions about the knowledge base itself. In a personal knowledge base they need ``settings``.
_PERSONAL_SETTINGS = frozenset({"settings", "delete", "people", "moderators", "join", "fork"})


def personal_capability(route_capability: str) -> str:
    """The capability of a personal knowledge base that a route capability needs (Section 6.5): ``read`` and ``share`` stay; a decision about the knowledge base needs ``settings``; each other capability is a write of its content. The access level of the route gives the minimum scope (Section 6.6)."""
    if route_capability in ("read", SHARE):
        return route_capability
    return "settings" if route_capability in _PERSONAL_SETTINGS else "write"


def decide(
    *,
    is_personal: bool,
    owner_account_id: int | None,
    is_public: bool,
    capability: str,
    actor: Actor,
    single_mode: bool = False,
    archived: bool = False,
) -> Decision:
    """Decide one capability of one knowledge base for one actor.

    ``archived``: an archived shared knowledge base is read-only (Section 10.1): no contribution, review, curation, maintenance, or removal. Its settings (to unarchive it), its people, and its delete stay.

    ``single_mode``: the server has one personal knowledge base, the one of the admin. There a reader of the public knowledge base, an anonymous one included, makes share links of visible documents (the rule from before this design).
    """
    if is_personal:
        if capability not in PERSONAL_CAPABILITIES:
            raise ValueError(f"unknown capability of a personal knowledge base: {capability!r}")
        is_owner = actor.account_id is not None and actor.account_id == owner_account_id
        if capability == "read" or (capability == SHARE and single_mode):
            return "allow" if (is_owner or is_public) else "not_found"
        if is_owner:
            return "allow" if (actor.scope is not None and capability in _SCOPE_PERSONAL[actor.scope]) else "forbidden"
        # Section 6.5: another account gets 403 when it can read, else 404; anonymous gets 404.
        if actor.account_id is None or not is_public:
            return "not_found"
        return "forbidden"
    if capability not in SHARED_CAPABILITIES and capability != SHARE:
        raise ValueError(f"unknown capability of a shared knowledge base: {capability!r}")
    if capability in shared_capabilities(actor):
        return "forbidden" if (archived and capability in ARCHIVED_BLOCKED) else "allow"
    # Section 6.2: anonymous gets 404 for each capability but ``read``.
    return "not_found" if actor.account_id is None else "forbidden"
