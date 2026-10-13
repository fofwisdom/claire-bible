"""Mock vision transcription provider for deterministic testing."""

from __future__ import annotations

from typing import Any

from .base import VisionTranscriptProvider, VisionTranscriptResult


class MockVisionProvider(VisionTranscriptProvider):
    """Deterministic mock provider that generates predictable markdown transcripts."""

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
                model="mock-vision",
                error=None,
            )

        header = f"# {title or 'Image Infographic Content'}\n\n"
        sections: list[str] = []

        for idx, item in enumerate(images, start=1):
            url_str = item if isinstance(item, str) else item.get("url", f"image_{idx}")
            desc = item.get("desc", "") if isinstance(item, dict) else ""
            card_desc = f": {desc}" if desc else ""
            sections.append(
                f"### Card {idx}{card_desc}\n\n"
                f"- Image Source: `{url_str}`\n"
                f"- Summary: Detailed infographic and Q&A content transcribed from card image {idx}.\n"
                f"- Dialogue / Data: Trailblazer asks key technical and lore questions. The guardian answers with verified factual details.\n"
            )

        full_text = header + "\n".join(sections)
        return VisionTranscriptResult(
            text=full_text.strip(),
            images_processed=len(images),
            model="mock-vision",
            error=None,
        )
