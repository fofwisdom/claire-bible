"""HoYoLAB community article fetcher and web adapter with VLM transcription support."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from ...config import get_settings
from ...extract.table_budget import slice_document_text
from ...extract.vision.factory import get_vision_provider
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from ..registry import register_fetcher, register_web_adapter
from .base import BaseFetcher, BaseWebAdapter, FetchError, WebAdapterResult
from .http_policy import BROWSER_USER_AGENT

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(
    r"https?://(?:www\.|m\.)?hoyolab\.com/article/(\d+)",
    re.IGNORECASE,
)
_API_ENDPOINT = "https://bbs-api-os.hoyolab.com/community/post/wapi/getPostFull"
MIN_CONTENT_CHARS = 300


def parse_hoyolab_url(url: str) -> str | None:
    """Extract post_id from HoYoLAB article URL."""
    cleaned = (url or "").strip()
    m = _URL_PATTERN.search(cleaned)
    if m:
        return m.group(1)
    return None


def resolve_hoyolab_language(settings: Any = None) -> str:
    """Map system localization settings to HoYoLAB API language tag."""
    s = settings or get_settings()
    if getattr(s, "effective_vlm_language", None):
        pref = s.effective_vlm_language
    elif getattr(s, "effective_preferred_languages", None):
        pref = s.effective_preferred_languages[0]
    else:
        pref = getattr(s, "preferred_languages", "ko") or "ko"
        pref = pref.split(",")[0].strip().lower()
    if pref.startswith("ko"):
        return "ko-kr"
    if pref.startswith("ja"):
        return "ja-jp"
    if pref.startswith("zh-tw") or pref.startswith("zh-hk"):
        return "zh-tw"
    if pref.startswith("zh"):
        return "zh-cn"
    if pref.startswith("en"):
        return "en-us"
    return "ko-kr"


class HoYoLabClient:
    """Client for HoYoLAB community public API."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    def get_post(self, post_id: str, lang: str | None = None) -> dict[str, Any]:
        """Fetch article payload from HoYoLAB API with optional language localization."""
        query_params: dict[str, str] = {"post_id": post_id}
        headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "application/json",
        }
        if lang:
            query_params["lang"] = lang
            headers["x-rpc-language"] = lang
            headers["Accept-Language"] = lang
        query = urllib.parse.urlencode(query_params)
        api_url = f"{_API_ENDPOINT}?{query}"
        req = urllib.request.Request(
            api_url,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise FetchError(f"Failed to fetch HoYoLAB post {post_id}: {e}") from e

        retcode = data.get("retcode")
        if retcode != 0:
            msg = data.get("message", f"error retcode={retcode}")
            raise FetchError(f"HoYoLAB API error for post {post_id}: {msg}")

        post_data = (data.get("data") or {}).get("post")
        if not post_data or not isinstance(post_data, dict):
            raise FetchError(f"HoYoLAB post data missing for post {post_id}")

        return post_data


def _extract_text_and_images_from_post(post_data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Extract base text and list of images from post data structure."""
    post_info = post_data.get("post") or {}
    raw_content = post_info.get("content") or ""
    structured_raw = post_info.get("structured_content")

    text_parts: list[str] = []
    images: list[dict[str, Any]] = []
    seen_images: set[str] = set()

    for item in post_data.get("image_list") or []:
        url = item.get("url")
        if url and url not in seen_images:
            seen_images.add(url)
            images.append({
                "url": url,
                "width": item.get("width"),
                "height": item.get("height"),
                "size": item.get("size"),
                "alt": item.get("image_id", ""),
            })

    if structured_raw:
        try:
            ops = json.loads(structured_raw) if isinstance(structured_raw, str) else structured_raw
            if isinstance(ops, list):
                for op in ops:
                    if not isinstance(op, dict):
                        continue
                    ins = op.get("insert")
                    if isinstance(ins, str):
                        text_parts.append(ins)
                    elif isinstance(ins, dict) and "image" in ins:
                        img_url = ins["image"]
                        if img_url and img_url not in seen_images:
                            seen_images.add(img_url)
                            images.append({"url": img_url, "alt": ""})
        except Exception as e:
            logger.debug("Failed to parse structured_content Delta: %s", e)

    if not text_parts and raw_content:
        # Fallback to plain html strip if structured_content was absent
        clean_text = re.sub(r"<[^>]+>", " ", raw_content)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()
        text_parts.append(clean_text)

    full_text = "".join(text_parts).strip()
    return full_text, images


def format_post_to_markdown(
    post_data: dict[str, Any],
    *,
    settings: Any = None,
) -> tuple[str, str, list[str], dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    """Format post into structured markdown, invoking VLM transcription when text is insufficient."""
    s = settings or get_settings()
    post_info = post_data.get("post") or {}
    user_info = post_data.get("user") or {}

    subject = (post_info.get("subject") or "").strip()
    author = (user_info.get("nickname") or "HoYoLAB Creator").strip()
    post_id = str(post_info.get("post_id") or "")
    created_at_ts = post_info.get("created_at")

    topics = [t.get("name", "") for t in (post_data.get("topics") or []) if t.get("name")]
    topic_header = f"**Topics**: {', '.join(topics)}\n\n" if topics else ""

    base_text, images = _extract_text_and_images_from_post(post_data)

    vlm_transcribed = False
    vlm_model = ""
    vlm_images_count = 0
    vlm_language = None
    vlm_error = None

    # VLM Trigger: Text is below threshold and images are present
    if len(base_text) < MIN_CONTENT_CHARS and images and getattr(s, "enable_vlm_transcription", True):
        vision_provider = get_vision_provider(s)
        logger.info(
            "HoYoLAB post %s has minimal text (%d chars) but %d images. Invoking VLM transcription...",
            post_id,
            len(base_text),
            len(images),
        )
        target_vlm_lang = getattr(s, "effective_vlm_language", None) or (s.effective_preferred_languages[0] if getattr(s, "effective_preferred_languages", None) else "ko")
        v_res = vision_provider.transcribe_images(images, title=subject, language=target_vlm_lang)
        if v_res.text:
            vlm_transcribed = True
            vlm_model = v_res.model
            vlm_images_count = v_res.images_processed
            vlm_language = v_res.language or target_vlm_lang
            transcribed_section = (
                f"\n\n## Infographic & Image Transcription\n\n"
                f"{v_res.text}\n"
            )
            base_text = (base_text + transcribed_section).strip()
        elif v_res.error:
            vlm_error = v_res.error
            logger.warning("VLM transcription returned error for HoYoLAB post %s: %s", post_id, v_res.error)

    # Build final markdown
    lines: list[str] = [
        f"# {subject}",
        "",
        f"**Author**: {author}  ",
        topic_header.rstrip(),
        f"**Source**: https://www.hoyolab.com/article/{post_id}",
        "",
        "---",
        "",
        base_text,
    ]
    markdown_doc = "\n".join(l for l in lines if l is not None).strip()

    links: list[str] = []
    anchors: dict[str, str] = {}
    for match in re.finditer(r"https?://[^\s)\]\}<>\"']+", markdown_doc):
        found_url = match.group(0).rstrip(".,;)")
        if found_url not in links:
            links.append(found_url)

    meta_extras = {
        "post_id": post_id,
        "author": author,
        "topics": topics,
        "vlm_transcribed": vlm_transcribed,
        "vlm_model": vlm_model,
        "vlm_images_count": vlm_images_count,
        "vlm_language": vlm_language,
        "vlm_error": vlm_error,
        "created_at": created_at_ts,
    }

    return subject, markdown_doc, links[:50], anchors, images[:20], meta_extras


def fetch_hoyolab(
    url: str,
    *,
    full_content: bool = False,
    settings: Any = None,
    client: HoYoLabClient | None = None,
) -> Document:
    """Fetch HoYoLAB article and return Document object."""
    post_id = parse_hoyolab_url(url)
    if not post_id:
        raise FetchError(f"Invalid HoYoLAB article URL: {url}")

    s = settings or get_settings()
    api_client = client or HoYoLabClient()
    target_lang = resolve_hoyolab_language(s)
    post_data = api_client.get_post(post_id, lang=target_lang)

    title, text, links, anchors, images, meta_extras = format_post_to_markdown(post_data, settings=s)

    budget = 0 if full_content else s.raw_char_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", budget, strategy=s.slicing_strategy
    )

    canonical = f"https://www.hoyolab.com/article/{post_id}"
    anchor_pairs = [{"url": u, "anchor": anchors.get(u, "")} for u in links[:50]]

    meta: dict[str, Any] = {
        "fetch_via": "hoyolab",
        "effective_url": canonical,
        "links": links[:50],
        "link_anchors": anchor_pairs,
        "images": images,
        "raw_truncated": is_truncated,
        "orig_chars": orig_chars,
        "raw_chars": raw_chars,
        **meta_extras,
    }

    published_at = None
    created_at_ts = meta_extras.get("created_at")
    if created_at_ts:
        try:
            published_at = datetime.fromtimestamp(created_at_ts, tz=timezone.utc).isoformat()
        except Exception:
            published_at = None

    return Document(
        url=url,
        canonical_url=canonicalize_url(canonical),
        title=title,
        author=meta_extras.get("author") or "HoYoLAB",
        published_at=published_at,
        raw_text=raw_text,
        source_type="hoyolab",
        content_hash=content_hash(title or "", raw_text),
        lang="ko",
        meta=meta,
    )


def try_hoyolab_adapter(url: str, **kwargs: Any) -> WebAdapterResult | None:
    """WebAdapter escalation wrapper for HoYoLAB posts."""
    post_id = parse_hoyolab_url(url)
    if not post_id:
        return None

    try:
        s = kwargs.get("settings") or get_settings()
        client = kwargs.get("client") or HoYoLabClient()
        post_data = client.get_post(post_id)
        title, text, links, anchors, images, meta_extras = format_post_to_markdown(post_data, settings=s)
        return WebAdapterResult(
            title=title,
            text=text,
            links=links,
            anchors=anchors,
            images=images,
            is_pdf=False,
            doc_type="hoyolab",
            parser_info=meta_extras,
        )
    except Exception as e:
        logger.debug("HoYoLAB adapter fallback failed for %s: %s", url, e)
        return None


@register_fetcher("hoyolab", priority=115)
class HoYoLabFetcher(BaseFetcher):
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return bool(parse_hoyolab_url(url))

    @classmethod
    def name(cls) -> str:
        return "hoyolab"

    @classmethod
    def fetch(cls, url: str, **kwargs: Any) -> Document:
        return fetch_hoyolab(url, **kwargs)


@register_web_adapter(name="hoyolab", domains=("hoyolab.com", "*.hoyolab.com"), priority=38)
class HoYoLabAdapter(BaseWebAdapter):
    @classmethod
    def name(cls) -> str:
        return "hoyolab"

    @classmethod
    def try_fetch(cls, url: str, **kwargs: Any) -> WebAdapterResult | None:
        return try_hoyolab_adapter(url, **kwargs)
