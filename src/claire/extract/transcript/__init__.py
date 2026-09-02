"""Audio Speech-to-Text (STT) Transcription Package."""

from __future__ import annotations

from .base import TranscriptProvider, TranscriptResult, TranscriptSegment
from .factory import get_transcript_provider

__all__ = [
    "TranscriptProvider",
    "TranscriptResult",
    "TranscriptSegment",
    "get_transcript_provider",
]
