"""Measurement M0 (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md, LABORATORY_DESIGN.md LB2): the same audio, transcribed without language hints and with the hints of a list.

The run downloads the audio of one video (at most ``MAX_AUDIO_SECONDS``) with yt-dlp, calls the Google AI Studio STT two times with the server key, and keeps for each variant the length, the time, the share of each writing system, and a part of the text, and the agreement of the two texts. The audio goes to Google; nothing goes into the knowledge graph.
"""

from __future__ import annotations

import difflib
import re
import tempfile
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

from ..accounts.languages import normalize_languages
from ..core.secret_scrub import scrub_exception
from . import runs

KIND: Final = "stt_hints"
MAX_AUDIO_SECONDS: Final = 300
EXCERPT_CHARS: Final = 1500
_COMPARE_CHARS: Final = 6000
_TIMESTAMP_RE: Final = re.compile(r"\[\d{1,2}(?::\d{2}){1,2}\]")


class LabRunError(RuntimeError):
    """A run cannot start or cannot finish; ``code`` is the error code of the API or of the record."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def script_shares(text: str) -> dict[str, float]:
    """The share of the letters of each writing system: ``hangul``, ``kana``, ``han``, ``latin``, ``other``."""
    counts = {"hangul": 0, "kana": 0, "han": 0, "latin": 0, "other": 0}
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        if 0xAC00 <= code <= 0xD7A3 or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
            counts["hangul"] += 1
        elif 0x3040 <= code <= 0x30FF:
            counts["kana"] += 1
        elif 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
            counts["han"] += 1
        elif "LATIN" in unicodedata.name(char, ""):
            counts["latin"] += 1
        else:
            counts["other"] += 1
    total = sum(counts.values())
    return {name: (round(value / total, 3) if total else 0.0) for name, value in counts.items()}


def _normalized(text: str) -> str:
    return " ".join(_TIMESTAMP_RE.sub(" ", text).lower().split())


def agreement(a: str, b: str) -> float:
    """The similarity of the two texts without timestamps (``difflib`` ratio of the first 6000 characters, 0 to 1)."""
    return round(difflib.SequenceMatcher(None, _normalized(a)[:_COMPARE_CHARS], _normalized(b)[:_COMPARE_CHARS], autojunk=False).ratio(), 4)


def download_audio(url: str, settings: Any, workdir: Path) -> tuple[Path, float]:
    """The audio of ``url`` (yt-dlp, the options of ``ingest/fetchers/video.py``) and its duration. A video longer than ``MAX_AUDIO_SECONDS`` or without a known duration is refused before the download."""
    import yt_dlp

    from ..core.config import find_ffmpeg_executable
    from ..ingest.fetchers.video import parse_ytdlp_extractor_args

    ext_args = parse_ytdlp_extractor_args(getattr(settings, "ytdlp_extractor_args", "generic:impersonate"))
    info_opts: dict[str, Any] = {"quiet": True, "no_warnings": True, "skip_download": True}
    if ext_args:
        info_opts["extractor_args"] = ext_args
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False) or {}
    duration = info.get("duration")
    if not isinstance(duration, (int, float)):
        raise LabRunError("duration_unknown")
    if duration > MAX_AUDIO_SECONDS:
        raise LabRunError("audio_too_long", f"{int(duration)} s")
    ffmpeg = find_ffmpeg_executable(getattr(settings, "ffmpeg_bin", "ffmpeg"))
    if ffmpeg is None:
        raise LabRunError("ffmpeg_missing")
    audio_opts: dict[str, Any] = {
        "format": "ba[protocol!*=dash]/ba/b[height<=360]/b",
        "outtmpl": str(workdir / "audio.%(ext)s"),
        "ffmpeg_location": ffmpeg,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
    }
    if ext_args:
        audio_opts["extractor_args"] = ext_args
    with yt_dlp.YoutubeDL(audio_opts) as ydl:
        ydl.download([url])
    files = [p for p in workdir.glob("audio.*") if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
    if not files:
        raise LabRunError("download_failed")
    return files[0], float(duration)


def _stt_provider(settings: Any) -> Any:
    from ..extract.transcript.gemini_stt import GeminiTranscriptProvider

    if not getattr(settings, "gemini_api_key", ""):
        raise LabRunError("no_api_key")
    return GeminiTranscriptProvider(settings)


def measure(
    settings: Any,
    *,
    url: str,
    hints: Iterable[str],
    downloader: Callable[[str, Any, Path], tuple[Path, float]] = download_audio,
    provider_factory: Callable[[Any], Any] = _stt_provider,
) -> dict[str, Any]:
    """Run the two variants on the same audio and return the result of the run."""
    provider = provider_factory(settings)
    variants = (("none", []), ("hints", list(hints)))
    with tempfile.TemporaryDirectory(prefix="claire_lab_") as tmp:
        audio, duration = downloader(url, settings, Path(tmp))
        out: dict[str, Any] = {}
        texts: dict[str, str] = {}
        for name, codes in variants:
            started = time.monotonic()
            transcript = provider.transcribe(audio, language="auto", timestamps=True, language_codes=codes)
            latency = round((time.monotonic() - started) * 1000.0, 1)
            text = str(getattr(transcript, "full_text", "") or "")
            texts[name] = text
            out[name] = {
                "language_codes": codes,
                "chars": len(text),
                "segments": len(getattr(transcript, "segments", None) or []),
                "latency_ms": latency,
                "scripts": script_shares(text),
                "excerpt": text[:EXCERPT_CHARS],
            }
    return {"duration_sec": duration, "model": str(getattr(provider, "model", "")), "variants": out, "agreement": agreement(texts["none"], texts["hints"])}


def start_run(
    db_file: Path,
    settings: Any,
    *,
    url: str,
    hints: Iterable[str],
    created_by: int | None,
    measure_fn: Callable[..., dict[str, Any]] | None = None,
    background: bool = True,
) -> runs.LabRun:
    """Check the input, write the run, and run it in a thread of the process. ``LabRunError`` (``invalid_url``, ``invalid_languages``) or ``runs.RunInProgress`` before any call."""
    from ..accounts.languages import InvalidLanguages
    from ..store.db import connect_existing

    clean_url = str(url or "").strip()
    if not re.match(r"^https?://\S+$", clean_url) or len(clean_url) > 2000:
        raise LabRunError("invalid_url")
    try:
        clean_hints = list(normalize_languages(hints))
    except InvalidLanguages as exc:
        raise LabRunError("invalid_languages") from exc
    conn = connect_existing(db_file)
    try:
        run = runs.start(conn, kind=KIND, params={"url": clean_url, "hints": clean_hints, "max_audio_seconds": MAX_AUDIO_SECONDS}, created_by=created_by)
    finally:
        conn.close()

    def _execute() -> None:
        try:
            result = (measure_fn or measure)(settings, url=clean_url, hints=clean_hints)
        except LabRunError as exc:
            outcome: tuple[str, Any] = ("fail", str(exc))
        except Exception as exc:  # noqa: BLE001 — the record keeps the failure; the next run can start
            outcome = ("fail", scrub_exception(exc))
        else:
            outcome = ("done", result)
        done = connect_existing(db_file)
        try:
            if outcome[0] == "done":
                runs.finish(done, run.id, result=outcome[1])
            else:
                runs.fail(done, run.id, error=str(outcome[1]))
        finally:
            done.close()

    if background:
        threading.Thread(target=_execute, name=f"lab-{run.id}", daemon=True).start()
    else:
        _execute()
    return run
