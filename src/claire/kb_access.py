"""Capabilities in a knowledge base (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 6.2, 6.5, 6.9).

One function decides the matrix: ``decide``. Routes do not check access on their own (Section 6.9 rule 1).

The result is ``"allow"``, ``"forbidden"`` (403), or ``"not_found"`` (404). A private personal knowledge base and an unknown number give the same 404 (PRINCIPLES C1).

Not in this module yet (unit CT-2): the credential scopes ``write`` and ``readonly`` of Section 6.6, and the reputation rule of Section 8.6. The caller gives ``can_write``: False for a credential that only reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Decision = Literal["allow", "forbidden", "not_found"]

# Capabilities of a shared knowledge base (Section 6.2).
SHARED_CAPABILITIES = (
    "read", "join", "contribute_direct", "contribute_theme", "fork", "review", "curate", "maintain",
    "people", "settings", "remove", "moderators", "delete",
)
# Capabilities of a personal knowledge base (Section 6.5): the owner has all rights; nobody else writes.
PERSONAL_CAPABILITIES = ("read", "write", "settings")

_NON_MEMBER = frozenset({"read", "join", "contribute_theme", "fork"})
_MEMBER = _NON_MEMBER | {"contribute_direct"}
_COLLABORATOR = _MEMBER | {"review", "curate", "maintain"}
_MODERATOR = _COLLABORATOR | {"people", "settings", "remove"}
_ADMIN = frozenset(SHARED_CAPABILITIES)
_BANNED = frozenset({"read", "fork"})


@dataclass(frozen=True, slots=True)
class Actor:
    """The requester, reduced to what the matrix needs."""

    account_id: int | None  # None: anonymous
    is_admin: bool = False
    can_write: bool = True  # False: the credential only reads (Section 6.6)
    # Membership in the shared knowledge base of the decision: (role, state) or None.
    membership: tuple[str, str] | None = None


def shared_capabilities(actor: Actor) -> frozenset[str]:
    """The capabilities of ``actor`` in a shared knowledge base (Section 6.2 with rules 1 and 4)."""
    if actor.account_id is None:
        return frozenset({"read"})
    if actor.is_admin:
        caps = _ADMIN
    elif actor.membership is None or actor.membership[1] == "pending":
        caps = _NON_MEMBER
    elif actor.membership[1] == "banned":
        caps = _BANNED
    else:
        caps = {"member": _MEMBER, "collaborator": _COLLABORATOR, "moderator": _MODERATOR}[actor.membership[0]]
    if not actor.can_write:
        caps = caps & {"read"}
    return frozenset(caps)


def decide(
    *,
    is_personal: bool,
    owner_account_id: int | None,
    is_public: bool,
    capability: str,
    actor: Actor,
) -> Decision:
    """Decide one capability of one knowledge base for one actor."""
    if is_personal:
        if capability not in PERSONAL_CAPABILITIES:
            raise ValueError(f"unknown capability of a personal knowledge base: {capability!r}")
        is_owner = actor.account_id is not None and actor.account_id == owner_account_id
        if capability == "read":
            return "allow" if (is_owner or is_public) else "not_found"
        if is_owner:
            return "allow" if actor.can_write else "forbidden"
        # Section 6.5: another account gets 403 when it can read, else 404; anonymous gets 404.
        if actor.account_id is None or not is_public:
            return "not_found"
        return "forbidden"
    if capability not in SHARED_CAPABILITIES:
        raise ValueError(f"unknown capability of a shared knowledge base: {capability!r}")
    if capability in shared_capabilities(actor):
        return "allow"
    # Section 6.2: anonymous gets 404 for each capability but ``read``.
    return "not_found" if actor.account_id is None else "forbidden"
