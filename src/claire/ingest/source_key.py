"""Source identifier and source key of a document (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.3 rules 4 to 6).

Documents with the same source key have the same source: their published renditions make one rendition set across accounts and servers. The rules have a version (``SOURCE_ID_VERSION``); a change of the rules is a new version.

Version 1, in this order:

1. A DOI in the address (``doi.org``, or a path with a DOI, as publishers write it): ``https://doi.org/{doi in lower case}``. A DOI of arXiv (``10.48550/arXiv.{id}``) is the arXiv form of rule 2.
2. An arXiv address: ``https://arxiv.org/abs/{id without version}`` (``canonicalize_url``).
3. A web address (``http`` or ``https``): ``canonicalize_url``.
4. Each other document (a file without an address, a text): no identifier.

The DOI of the PDF metadata is not in version 1: no extraction step gives a confirmed DOI now.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import unquote, urlsplit

from .normalize import canonicalize_url

__all__ = ["SOURCE_ID_VERSION", "source_identifier", "source_key", "source_key_of"]

SOURCE_ID_VERSION = 1

# The DOI syntax of Crossref: a prefix "10." with 4 to 9 digits, "/", and a suffix.
_DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:a-z0-9]+)", re.IGNORECASE)
_DOI_HOSTS = ("doi.org", "dx.doi.org")
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def _doi_of(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path or "")
    if host.removeprefix("www.") in _DOI_HOSTS:
        match = _DOI_RE.fullmatch(path.lstrip("/"))
    else:
        match = _DOI_RE.search(path)
    if match is None:
        return None
    # A trailing full stop or file suffix is not a part of a DOI in an address.
    return re.sub(r"(\.pdf|\.)$", "", match.group(1), flags=re.IGNORECASE).lower()


def source_identifier(url: str | None) -> str | None:
    """The source identifier of an address of version 1, or ``None``."""
    if not url:
        return None
    text = url.strip()
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return None
    if scheme not in ("http", "https"):
        return None
    doi = _doi_of(text)
    if doi is not None:
        arxiv = _ARXIV_DOI_RE.match(doi)
        if arxiv is None:
            return f"https://doi.org/{doi}"
        text = f"https://arxiv.org/abs/{arxiv.group(1)}"
    canonical = canonicalize_url(text)
    return canonical.replace("http://arxiv.org/", "https://arxiv.org/", 1) if canonical.startswith("http://arxiv.org/") else canonical


def source_key(identifier: str) -> str:
    """The key on the wire: SHA-256 of the source identifier, as 64 lowercase hexadecimal characters."""
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def source_key_of(url: str | None, canonical_url: str | None = None) -> str | None:
    """The source key of a document from its address (the original address first, then the canonical one), or ``None``."""
    for candidate in (url, canonical_url):
        identifier = source_identifier(candidate)
        if identifier is not None:
            return source_key(identifier)
    return None
