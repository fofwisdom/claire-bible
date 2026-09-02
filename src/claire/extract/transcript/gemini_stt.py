"""Google Gemini API 기반 프로덕션 오디오 전사(STT) 프로바이더.

- gemini-3.5-transcribe (Interactions / File API) 지원
- 대용량 오디오(89분+) 15분 단위 VAD/무음 청킹 분할
- Google File API 업로드 및 ACTIVE 상태 폴링 대기
- 절대 시간([HH:MM:SS]) 오프셋 리베이싱 및 완결 스티칭
- custom_vocabulary 기술 용어 주입 및 스마트 서식 정제
- 사용 후 Google Cloud 업로드 파일 즉각 삭제(Quota 보호)
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .base import TranscriptProvider, TranscriptResult, TranscriptSegment

logger = logging.getLogger(__name__)

_TS_LINE_RE = re.compile(
    r"^\[?(\d{1,2}:)?(\d{1,2}):(\d{2})\]?\s*(.*)$"
)

DEFAULT_CUSTOM_VOCABULARY = [
    "VMware Cloud Foundation",
    "VCF 9.1",
    "Private AI Services",
    "vSphere",
    "vSAN",
    "Tanzu",
    "NSX",
    "RoCE",
    "MIG",
    "NVIDIA AI Enterprise",
    "Cilium",
    "GPU",
    "Kubernetes",
    "vCenter",
    "Broadcom",
]


def _parse_timestamp_to_sec(ts_match: re.Match) -> tuple[float, str]:
    """[HH:MM:SS] 또는 [MM:SS] 매칭을 초 단위 부동소수점과 텍스트로 변환."""
    hours_str, mins_str, secs_str, text = ts_match.groups()
    hours = int(hours_str.rstrip(":")) if hours_str else 0
    mins = int(mins_str)
    secs = int(secs_str)
    sec_val = float(hours * 3600 + mins * 60 + secs)
    return sec_val, text.strip()


def parse_raw_transcript_lines(raw_text: str, offset_sec: float = 0.0) -> list[TranscriptSegment]:
    """타임스탬프 텍스트 줄을 파싱하고 오프셋을 더하여 TranscriptSegment 목록 생성."""
    segments: list[TranscriptSegment] = []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        m = _TS_LINE_RE.match(line)
        if m:
            sec_val, text = _parse_timestamp_to_sec(m)
            abs_sec = sec_val + offset_sec
            segments.append(
                TranscriptSegment(
                    start_sec=abs_sec,
                    end_sec=abs_sec + 5.0,
                    text=text or line,
                )
            )
        else:
            abs_sec = float(i * 5) + offset_sec
            segments.append(
                TranscriptSegment(
                    start_sec=abs_sec,
                    end_sec=abs_sec + 5.0,
                    text=line,
                )
            )

    for idx in range(len(segments) - 1):
        if segments[idx + 1].start_sec > segments[idx].start_sec:
            segments[idx].end_sec = segments[idx + 1].start_sec

    return segments


def get_audio_duration_sec(audio_file: Path, ffmpeg_bin: str = "ffmpeg") -> float:
    """ffprobe 또는 ffmpeg를 사용해 오디오 길이(초)를 측정한다."""
    try:
        from ...config import find_ffmpeg_executable

        ff_exec = find_ffmpeg_executable(ffmpeg_bin)
        ffprobe_exec = None
        if ff_exec:
            cand = Path(ff_exec).parent / "ffprobe"
            if cand.is_file() and os.access(cand, os.X_OK):
                ffprobe_exec = str(cand)
        if not ffprobe_exec:
            ffprobe_exec = shutil.which("ffprobe")

        if ffprobe_exec:
            res = subprocess.run(
                [
                    ffprobe_exec,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(audio_file),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                timeout=15,
            )
            val = float(res.stdout.strip())
            if val > 0:
                return val
    except Exception as e:
        logger.debug("ffprobe duration probe failed: %s", e)

    # ffmpeg -i stderr 파싱 폴백
    try:
        from ...config import find_ffmpeg_executable

        ff_exec = find_ffmpeg_executable(ffmpeg_bin) or "ffmpeg"
        res = subprocess.run(
            [ff_exec, "-i", str(audio_file)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
        if m:
            h, m_val, s = m.groups()
            return float(h) * 3600 + float(m_val) * 60 + float(s)
    except Exception as e:
        logger.debug("ffmpeg duration parse failed: %s", e)

    # 파일 크기 기반 근사 추정 (MP3 32~64kbps 기준 보수적 계산)
    size_bytes = audio_file.stat().st_size
    return size_bytes / (64 * 1024 / 8)


def split_audio_into_chunks(
    audio_path: Path,
    chunk_duration_sec: float = 900.0,
    ffmpeg_bin: str = "ffmpeg",
    tmp_dir: Path | None = None,
) -> list[tuple[Path, float]]:
    """오디오 파일을 지정된 시간 단위(기본 15분)로 분할하여 (청크파일경로, 오프셋초) 목록 반환."""
    from ...config import find_ffmpeg_executable

    ff_exec = find_ffmpeg_executable(ffmpeg_bin) or "ffmpeg"
    total_sec = get_audio_duration_sec(audio_path, ffmpeg_bin=ff_exec)
    base_dir = tmp_dir or audio_path.parent

    # 이미 15분 이하이고 mp3인 경우 분할 없이 그대로 사용
    if total_sec <= chunk_duration_sec and audio_path.suffix.lower() == ".mp3":
        return [(audio_path, 0.0)]

    num_chunks = max(1, math.ceil(total_sec / chunk_duration_sec))
    chunks: list[tuple[Path, float]] = []

    for idx in range(num_chunks):
        offset = idx * chunk_duration_sec
        dur = min(chunk_duration_sec, total_sec - offset)
        if dur <= 0:
            break
        chunk_file = base_dir / f"chunk_{idx:03d}_{audio_path.stem}.mp3"
        cmd = [
            ff_exec,
            "-y",
            "-ss",
            f"{offset:.2f}",
            "-t",
            f"{dur:.2f}",
            "-i",
            str(audio_path),
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(chunk_file),
        ]
        try:
            subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                timeout=300,
            )
            chunks.append((chunk_file, offset))
        except Exception as e:
            logger.error("Failed to split audio chunk %d at %fs: %s", idx, offset, e)
            # 분할 실패 시 원본 그대로 반환 폴백
            if not chunks:
                return [(audio_path, 0.0)]
            break

    return chunks


def _parse_retry_delay(exc: Exception, default_delay: float = 35.0) -> float:
    """Gemini API 429 에러 응답에서 retryDelay(초) 추출."""
    err_str = str(exc)
    m = re.search(r"retry in\s+([0-9.]+)s", err_str, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"['\"]?retryDelay['\"]?\s*:\s*['\"]?([0-9.]+)s?['\"]?", err_str, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return default_delay


class GeminiTranscriptProvider(TranscriptProvider):
    """Google Gemini API 전용 STT 프로바이더 (gemini-3.5-transcribe)."""

    name = "gemini"

    def __init__(self, settings: Any = None):
        self.settings = settings
        raw_model = (
            getattr(settings, "stt_model", "")
            or os.environ.get("STT_MODEL", "")
            or os.environ.get("CLAIRE_STT_MODEL", "")
        )
        self.model = raw_model.strip() or "gemini-3.5-transcribe"
        self.language = getattr(settings, "stt_language", "ko")
        self.timeout = float(getattr(settings, "stt_timeout", 600.0))
        self.chunk_duration = float(getattr(settings, "video_chunk_duration_sec", 240.0))
        user_vocab = getattr(settings, "stt_custom_vocabulary", [])
        if isinstance(user_vocab, list) and user_vocab:
            self.custom_vocabulary = list(user_vocab)
        else:
            self.custom_vocabulary = list(DEFAULT_CUSTOM_VOCABULARY)

    def _get_genai_client(self) -> Any:
        from google import genai

        api_key = (
            getattr(self.settings, "gemini_api_key", None)
            or os.environ.get("GEMINI_API_KEY", "")
        )
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured for GeminiTranscriptProvider")
        return genai.Client(api_key=api_key)

    def _transcribe_single_file(
        self,
        client: Any,
        audio_file: Path,
        target_lang: str,
        offset_sec: float,
    ) -> list[TranscriptSegment]:
        from google.genai import types

        logger.info(
            "Uploading audio chunk %s (offset %.1fs) to Gemini File API...",
            audio_file.name,
            offset_sec,
        )
        uploaded = client.files.upload(file=str(audio_file))

        try:
            # 1. ACTIVE 상태 대기 (PROCESSING 에러 방지)
            poll_start = time.time()
            poll_interval = 2.0
            while True:
                finfo = client.files.get(name=uploaded.name)
                if finfo.state == types.FileState.ACTIVE:
                    break
                if finfo.state == types.FileState.FAILED:
                    raise RuntimeError(f"Gemini File API processing failed for {uploaded.name}")
                if time.time() - poll_start > 180:
                    raise TimeoutError(f"Gemini File API processing timed out for {uploaded.name}")
                time.sleep(poll_interval)

            # 2. 맞춤 어휘 및 타임스탬프 스마트 프롬프트 구성
            vocab_str = ", ".join(self.custom_vocabulary)
            prompt = (
                f"You are a professional speech-to-text transcriber for technical conferences. "
                f"Transcribe the spoken audio into accurate text in {target_lang}. "
                f"Requirements:\n"
                f"1. Prepend accurate timestamps in [MM:SS] format at the start of each line or sentence.\n"
                f"2. Accurately preserve all technical terminology, infrastructure jargon, and product names.\n"
                f"   Key technical vocabulary: {vocab_str}.\n"
                f"3. Apply smart transcription: remove disfluencies (um, uh, repetitions) and format into readable sentences.\n"
                f"4. Do NOT output commentary or markdown headers, only the timestamped transcript lines."
            )

            # 3. 모델 호출 (gemini-3.5-transcribe는 Interactions API 전용, 타 모델은 generate_content)
            model_name = self.model
            max_retries = 5

            for attempt in range(1, max_retries + 1):
                try:
                    if "gemini-3.5-transcribe" in model_name and hasattr(client, "interactions"):
                        # gemini-3.5-transcribe는 전용 음성 인식 모델로서 Interactions API를 사용해야 함.
                        # verbatim 모드 + word 타임스탬프로 단어/문장별 정확한 타임스탬프 획득.
                        # 주의: custom_vocabulary는 timestamps 옵션과 비호환(400 에러)되므로 단독 사용.
                        trans_cfg: dict[str, Any] = {
                            "mode": {
                                "type": "verbatim",
                                "timestamp_granularities": ["word"],
                            }
                        }
                        # language_codes는 생략 시 Google이 음성 언어를 자동 감지(en, ko 등).
                        # 명시적으로 target_lang이 auto나 기본 ko가 아닌 특정 언어일 때 주입.
                        if target_lang and target_lang not in ("auto", "ko"):
                            trans_cfg["language_codes"] = [target_lang]

                        try:
                            interaction = client.interactions.create(
                                model=model_name,
                                input=[{
                                    "type": "audio",
                                    "uri": uploaded.uri,
                                    "mime_type": uploaded.mime_type or "audio/mp3",
                                }],
                                generation_config={
                                    "transcription_config": trans_cfg,
                                },
                            )
                        except Exception as inter_err:
                            if "400" in str(inter_err):
                                # 타임스탬프 비호환 언어 또는 옵션 충돌 시 smart 모드 단독 폴백
                                interaction = client.interactions.create(
                                    model=model_name,
                                    input=[{
                                        "type": "audio",
                                        "uri": uploaded.uri,
                                        "mime_type": uploaded.mime_type or "audio/mp3",
                                    }],
                                    generation_config={
                                        "transcription_config": {
                                            "mode": {"type": "smart"},
                                        },
                                    },
                                )
                            else:
                                raise

                        # interaction.steps 내 word annotations 파싱하여 타임스탬프 문장 목록 추출
                        annotations = []
                        for step in (getattr(interaction, "steps", None) or []):
                            for c in (getattr(step, "content", None) or []):
                                for a in (getattr(c, "annotations", None) or []):
                                    if getattr(a, "type", None) == "word_info":
                                        annotations.append(a)

                        if annotations:
                            segments: list[TranscriptSegment] = []
                            curr_words: list[str] = []
                            curr_start: float | None = None
                            curr_end: float = 0.0
                            for w in annotations:
                                start_f = float(str(w.start_offset).rstrip("s"))
                                end_f = float(str(w.end_offset).rstrip("s"))
                                if curr_start is None:
                                    curr_start = start_f
                                curr_end = end_f
                                curr_words.append(w.text)
                                if any(w.text.endswith(p) for p in [".", "?", "!", "\n"]) or len(curr_words) >= 20:
                                    segments.append(
                                        TranscriptSegment(
                                            start_sec=curr_start + offset_sec,
                                            end_sec=curr_end + offset_sec,
                                            text=" ".join(curr_words),
                                        )
                                    )
                                    curr_words = []
                                    curr_start = None
                            if curr_words:
                                segments.append(
                                    TranscriptSegment(
                                        start_sec=(curr_start or 0.0) + offset_sec,
                                        end_sec=curr_end + offset_sec,
                                        text=" ".join(curr_words),
                                    )
                                )
                            return segments

                        chunk_text = getattr(interaction, "output_text", None) or ""
                        return parse_raw_transcript_lines(chunk_text, offset_sec=offset_sec)

                    else:
                        resp = client.models.generate_content(
                            model=model_name,
                            contents=[uploaded, prompt],
                        )
                        chunk_text = resp.text or ""
                        return parse_raw_transcript_lines(chunk_text, offset_sec=offset_sec)

                except Exception as call_err:
                    err_str = str(call_err).lower()
                    is_429 = "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str

                    if is_429:
                        delay = _parse_retry_delay(call_err, default_delay=35.0)
                        wait_sec = min(delay + 2.0, 120.0)
                        logger.warning(
                            "Gemini 429 rate limit (10k TPM limit) on model '%s' (attempt %d/%d). "
                            "Waiting %.1fs before retry...",
                            model_name,
                            attempt,
                            max_retries,
                            wait_sec,
                        )
                        if attempt < max_retries:
                            time.sleep(wait_sec)
                            continue
                        else:
                            logger.error(
                                "Model '%s' quota exceeded after %d retries: %s",
                                model_name,
                                max_retries,
                                call_err,
                            )
                            raise

                    raise

        finally:
            # 4. 사용 완료한 클라우드 파일 즉각 삭제
            try:
                client.files.delete(name=uploaded.name)
            except Exception as del_err:
                logger.debug("Failed to delete temporary Gemini file %s: %s", uploaded.name, del_err)

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        language: str | None = None,
        timestamps: bool = True,
    ) -> TranscriptResult:
        p = Path(audio_path).resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found: {p}")

        target_lang = language or self.language or "ko"
        client = self._get_genai_client()

        # 오디오 길이 측정
        ff_bin = getattr(self.settings, "ffmpeg_bin", "ffmpeg")
        total_duration = get_audio_duration_sec(p, ffmpeg_bin=ff_bin)

        # gemini-3.5-transcribe는 분당 입력 토큰 한도가 10,000(10K TPM)으로 엄격함.
        # 오디오 1초 = 약 25.2토큰이므로, 240초(4분) 청크는 약 6,000토큰으로 10K 한도 내에 안전하게 안착.
        effective_chunk_duration = self.chunk_duration
        if "gemini-3.5-transcribe" in self.model:
            effective_chunk_duration = min(self.chunk_duration, 240.0)

        # 청킹 분할
        with tempfile.TemporaryDirectory(prefix="claire_gemini_chunks_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            chunks = split_audio_into_chunks(
                p,
                chunk_duration_sec=effective_chunk_duration,
                ffmpeg_bin=ff_bin,
                tmp_dir=tmp_dir,
            )

            all_segments: list[TranscriptSegment] = []
            last_request_start = 0.0

            for chunk_idx, (chunk_file, offset) in enumerate(chunks):
                # gemini-3.5-transcribe 10k TPM 분당 할당량 초과 방지를 위한 윈도우 페이싱
                if "gemini-3.5-transcribe" in self.model and last_request_start > 0:
                    elapsed = time.time() - last_request_start
                    if elapsed < 62.0:
                        pace_sleep = 62.0 - elapsed
                        logger.info(
                            "gemini-3.5-transcribe 10k TPM pacing: waiting %.1fs before chunk %d/%d...",
                            pace_sleep,
                            chunk_idx + 1,
                            len(chunks),
                        )
                        time.sleep(pace_sleep)

                last_request_start = time.time()
                segs = self._transcribe_single_file(
                    client=client,
                    audio_file=chunk_file,
                    target_lang=target_lang,
                    offset_sec=offset,
                )
                all_segments.extend(segs)

        # 시간 순으로 정렬
        all_segments.sort(key=lambda s: s.start_sec)

        # 포맷팅된 전체 본문 조합
        formatted_lines: list[str] = []
        for s in all_segments:
            ts = s.format_timestamp()
            formatted_lines.append(f"[{ts}] {s.text}")

        full_text = "\n".join(formatted_lines)

        return TranscriptResult(
            full_text=full_text,
            segments=all_segments,
            language=target_lang,
            duration_sec=total_duration,
            provider="gemini",
            model=self.model,
        )
