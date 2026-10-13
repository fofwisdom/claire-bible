"""Base interfaces and data contracts for vision transcription providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class VisionTranscriptResult:
    """Result container for image transcription operations."""

    text: str
    images_processed: int = 0
    model: str = ""
    error: str | None = None


class VisionTranscriptProvider(ABC):
    """Abstract base provider for extracting text and structure from images."""

    def __init__(self, settings: Any = None) -> None:
        self.settings = settings

    @abstractmethod
    def transcribe_images(
        self,
        images: list[dict[str, Any] | str],
        *,
        title: str | None = None,
        prompt: str | None = None,
    ) -> VisionTranscriptResult:
        """Transcribe text, tables, and structured data from an ordered list of images."""
        pass
