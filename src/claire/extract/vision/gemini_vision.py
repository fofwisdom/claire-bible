"""Google Gemini API vision transcription provider."""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Any

from .base import VisionTranscriptProvider, VisionTranscriptResult

logger = logging.getLogger(__name__)


class GeminiVisionProvider(VisionTranscriptProvider):
    """Production provider using Google Gemini multimodal capabilities for image OCR and transcription."""

    def __init__(self, settings: Any = None) -> None:
        super().__init__(settings)
        from ...config import get_settings

        s = settings or get_settings()
        self.api_key = s.gemini_api_key
        self.model = s.vlm_model or "gemini-2.5-flash"
        self.max_images = getattr(s, "vlm_max_images", 20)

    def _get_client(self) -> Any:
        from google import genai

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured for GeminiVisionProvider")
        return genai.Client(api_key=self.api_key)

    def _download_image(self, url: str) -> tuple[bytes, str] | None:
        """Download image bytes and determine mime type."""
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                content_type = resp.headers.get_content_type()
                if not content_type or content_type == "application/octet-stream":
                    if url.lower().endswith(".png"):
                        content_type = "image/png"
                    elif url.lower().endswith((".webp")):
                        content_type = "image/webp"
                    else:
                        content_type = "image/jpeg"
                return data, content_type
        except Exception as e:
            logger.warning("Failed to download image from %s: %s", url, e)
            return None

    def transcribe_images(
        self,
        images: list[dict[str, Any] | str],
        *,
        title: str | None = None,
        prompt: str | None = None,
    ) -> VisionTranscriptResult:
        if not images:
            return VisionTranscriptResult(
                text="",
                images_processed=0,
                model=self.model,
                error=None,
            )

        from google.genai import types

        client = self._get_client()
        target_images = images[: self.max_images]
        parts: list[Any] = []

        system_instruction = prompt or (
            "You are an expert multimodal transcription engine. "
            f"The user has provided {len(target_images)} sequential infographic/card images "
            f"from the article: '{title or 'Article'}'.\n"
            "Requirements:\n"
            "1. Transcribe all text, labels, questions, and answers in chronological card order.\n"
            "2. Preserve the exact dialogue, Q&A format, statistics, and lore terminology without hallucinating.\n"
            "3. Format each card clearly using Markdown (e.g. '### Card N', '#### Q.', '**Answer**:').\n"
            "4. Output only the structured transcript in clean Markdown."
        )
        parts.append(system_instruction)

        processed_count = 0
        for item in target_images:
            if isinstance(item, str):
                url = item
            elif isinstance(item, dict):
                url = item.get("url") or item.get("src") or ""
            else:
                continue

            if not url:
                continue

            # Local file or remote URL
            if url.startswith(("http://", "https://")):
                download_res = self._download_image(url)
                if not download_res:
                    continue
                img_bytes, mime_type = download_res
            else:
                p = Path(url)
                if not p.is_file():
                    continue
                img_bytes = p.read_bytes()
                mime_type = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"

            parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
            processed_count += 1

        if processed_count == 0:
            return VisionTranscriptResult(
                text="",
                images_processed=0,
                model=self.model,
                error="No valid image bytes could be loaded",
            )

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=parts,
            )
            text = (response.text or "").strip()
            return VisionTranscriptResult(
                text=text,
                images_processed=processed_count,
                model=self.model,
                error=None,
            )
        except Exception as e:
            logger.error("Gemini vision transcription call failed: %s", e)
            return VisionTranscriptResult(
                text="",
                images_processed=processed_count,
                model=self.model,
                error=str(e),
            )
