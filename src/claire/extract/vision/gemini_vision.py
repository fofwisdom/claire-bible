"""Google Gemini API vision transcription provider."""

from __future__ import annotations

import logging
import re
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
        self.model = s.vlm_model or getattr(s, "gemini_model", None) or "gemini-3.8-flash"
        self.max_images = getattr(s, "vlm_max_images", 20)
        self.language = getattr(s, "vlm_language", "ko") or "ko"

    @staticmethod
    def _detect_text_language(text: str, fallback: str = "ko") -> str:
        """Detect dominant language of transcribed text to prevent false metadata claims."""
        if not text or not text.strip():
            return fallback
        hangul_count = len(re.findall(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]", text))
        latin_count = len(re.findall(r"[a-zA-Z]", text))
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        kana_count = len(re.findall(r"[\u3040-\u30ff]", text))
        total_letters = hangul_count + latin_count + cjk_count + kana_count
        if total_letters == 0:
            return fallback
        if hangul_count / total_letters >= 0.15:
            return "ko"
        if kana_count / total_letters >= 0.10:
            return "ja"
        if cjk_count / total_letters >= 0.20:
            return "zh"
        if latin_count / total_letters >= 0.50 and (hangul_count / total_letters < 0.05):
            return "en"
        return fallback

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
        language: str | None = None,
        prompt: str | None = None,
    ) -> VisionTranscriptResult:
        target_lang = language or self.language or "ko"
        if not images:
            return VisionTranscriptResult(
                text="",
                images_processed=0,
                model=self.model,
                language=target_lang,
                error=None,
            )

        from google.genai import types

        client = self._get_client()
        target_images = images[: self.max_images]
        parts: list[Any] = []

        if prompt:
            system_instruction = prompt
        elif target_lang in ("ko", "ko_KR"):
            system_instruction = (
                "You are an expert multimodal transcription engine. "
                f"The user has provided {len(target_images)} sequential infographic/card images "
                f"from the article: '{title or 'Article'}'.\n"
                "Requirements:\n"
                "1. Transcribe and translate all text, labels, questions, and answers into accurate and natural Korean (한국어) in chronological card order.\n"
                "2. If the text in images is in English or another foreign language, translate it faithfully into Korean while keeping official terminology, character names, and lore concepts accurate without hallucination.\n"
                "3. Preserve the exact dialogue, Q&A format, statistics, and lore terminology.\n"
                "4. Format each card clearly using Markdown (e.g. '### Card N', '#### Q.', '**Answer**:').\n"
                "5. Output only the structured transcript in clean Markdown."
            )
        else:
            system_instruction = (
                "You are an expert multimodal transcription engine. "
                f"The user has provided {len(target_images)} sequential infographic/card images "
                f"from the article: '{title or 'Article'}'.\n"
                f"Requirements:\n"
                f"1. Transcribe all text, labels, questions, and answers in chronological card order into {target_lang}.\n"
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
                language=target_lang,
                error="No valid image bytes could be loaded",
            )

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=parts,
            )
            text = (response.text or "").strip()
            detected_lang = self._detect_text_language(text, fallback=target_lang)
            return VisionTranscriptResult(
                text=text,
                images_processed=processed_count,
                model=self.model,
                language=detected_lang,
                error=None,
            )
        except Exception as e:
            logger.error("Gemini vision transcription call failed: %s", e)
            return VisionTranscriptResult(
                text="",
                images_processed=processed_count,
                model=self.model,
                language=target_lang,
                error=str(e),
            )
