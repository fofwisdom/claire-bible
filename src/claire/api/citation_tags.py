"""Citation tags of one document page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.7): data for machines, with no control on the page."""

from __future__ import annotations

import html as _html

__all__ = ["render_citation_tags"]


def render_citation_tags(*, permalink_url: str, title: str = "", author_name: str = "", publication_date: str = "", language: str = "", creator_handle: str = "", indexable: bool = True) -> str:
    """The citation tags of one document page.

    ``permalink_url`` is the preferred address to refer to the page (``rel="cite-as"``, RFC 8574); each page has it. The ``citation_*`` elements are only on a page that search engines can index. ``author_name`` is the account that made the text on this server, not the author of the source. ``publication_date`` has the form ``YYYY/MM/DD``. ``creator_handle`` (``@name@host``) is the ActivityPub actor of the account: Mastodon shows it as the byline of a link preview when the actor document lists the host of the page.
    """
    tags = [f'<link rel="cite-as" href="{_html.escape(permalink_url, quote=True)}"/>']
    if indexable:
        for name, value in (("citation_title", title), ("citation_author", author_name), ("citation_publication_date", publication_date), ("citation_public_url", permalink_url), ("citation_language", language)):
            if value:
                tags.append(f'<meta name="{name}" content="{_html.escape(value, quote=True)}"/>')
    if creator_handle:
        tags.append(f'<meta name="fediverse:creator" content="{_html.escape(creator_handle, quote=True)}"/>')
    return "\n".join(tags)
