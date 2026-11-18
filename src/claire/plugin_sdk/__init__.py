"""Plugin SDK, API level 1 (docs/implementation/INGESTION.md §2.2).

The only part of the core that a plugin imports (PL-D10). It gives names that exist in the core under one stable surface. ``tests/test_plugin_sdk_surface.py`` keeps a snapshot of the names and signatures: a change of the snapshot is a compatible addition (same level) or a break (new level).
"""

from __future__ import annotations

from httpx import HTTPError, HTTPStatusError, RequestError, Response, TimeoutException

from ..config import ProviderUnavailableError, Settings, find_ffmpeg_executable
from ..extract.provider import emit_progress
from ..extract.table_budget import slice_document_text
from ..extract.transcript.factory import get_transcript_provider
from ..extract.vision.factory import get_vision_provider
from ..ingest.fetchers.adoc_text import adoc_cell, adoc_link_text, adoc_list_item, adoc_table
from ..ingest.fetchers.base import (
    BaseFetcher,
    BaseWebAdapter,
    FetchError,
    WebAdapterResult,
    attach_usage_hooks,
    fetch_settings,
)
from ..ingest.fetchers.captions import CaptionAcquisition, acquire_caption, format_chapters
from ..ingest.fetchers.http import MediaResponseDetected
from ..ingest.fetchers.http_policy import BROWSER_USER_AGENT
from ..ingest.fetchers.pdf import extract_pdf_bytes, slice_pdf_text
from ..ingest.fetchers.presentation import PresentationExtract, PresentationFetchError, compose_video_presentations, extract_presentation
from ..ingest.fetchers.video import fetch_video as fetch_media
from ..ingest.fetchers.video import parse_ytdlp_extractor_args
from ..ingest.fetchers.web import _extract_html as extract_html
from ..ingest.fetchers.web import fetch_web, render_html_cdp
from ..ingest.hosts import host_matches_any, url_host
from ..ingest.normalize import canonicalize_url, content_hash
from ..ontology.base import Document, SourceAttachment
from ..plugin_host.context import PluginContext
from ..store.video_cache import delete_cached_video_file, get_cached_video_file, save_video_file_to_cache
from .routing import reroute
from .session import IngestSession, ingest_session

PLUGIN_API_LEVEL = 1

__all__ = [
    "BROWSER_USER_AGENT",
    "BaseFetcher",
    "BaseWebAdapter",
    "CaptionAcquisition",
    "Document",
    "FetchError",
    "HTTPError",
    "HTTPStatusError",
    "IngestSession",
    "MediaResponseDetected",
    "PLUGIN_API_LEVEL",
    "PluginContext",
    "PresentationExtract",
    "PresentationFetchError",
    "ProviderUnavailableError",
    "RequestError",
    "Response",
    "Settings",
    "SourceAttachment",
    "TimeoutException",
    "WebAdapterResult",
    "acquire_caption",
    "adoc_cell",
    "adoc_link_text",
    "adoc_list_item",
    "adoc_table",
    "attach_usage_hooks",
    "canonicalize_url",
    "compose_video_presentations",
    "content_hash",
    "delete_cached_video_file",
    "emit_progress",
    "extract_html",
    "extract_presentation",
    "extract_pdf_bytes",
    "fetch_media",
    "fetch_settings",
    "fetch_web",
    "find_ffmpeg_executable",
    "format_chapters",
    "get_cached_video_file",
    "get_transcript_provider",
    "get_vision_provider",
    "host_matches_any",
    "ingest_session",
    "parse_ytdlp_extractor_args",
    "render_html_cdp",
    "reroute",
    "save_video_file_to_cache",
    "slice_document_text",
    "slice_pdf_text",
    "url_host",
]
