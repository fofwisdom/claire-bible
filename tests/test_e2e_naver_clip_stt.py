"""End-to-End (E2E) test for Naver Clip / Shorts video ingestion with STT transcription.

Verifies complete pipeline lifecycle:
1. Short URL redirection and media ID resolution.
2. Direct audio stream acquisition and STT transcription.
3. Document storage in database, ontology entity resolution, and vault export.
4. Total hallucination prevention gate: thin clips without transcript are blocked before extraction.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import urllib.request
from pathlib import Path

from claire.extract.provider import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    MockProvider,
)
from claire.ingest.pipeline import ingest
from claire.store import db as dbm
from claire.store.vectors import VectorStore


def _init_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_e2e_naver_clip_stt_successful_ingest(monkeypatch, tmp_path: Path):
    """E2E flow: naver.me short URL -> CreatorHub stream -> STT -> Graph ingestion."""
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")

    # Mock external network boundary
    card_api_data = {
        "header": {"code": 0, "message": "success"},
        "body": {
            "card": {
                "content": {
                    "title": "[기술밋업] 네이버 개발자가 들려주는 분산 시스템 이야기",
                    "description": "네이버 백엔드 엔지니어링 밋업 강연 세션입니다.",
                    "profile": {"nickname": "NAVER D2"},
                    "videoQuality": {"duration": 3900.0},
                    "vod": {
                        "media": [
                            {"type": "AUDIO", "url": "https://cdn.pstatic.net/audio/stream.m4a"},
                        ]
                    },
                }
            }
        },
    }

    class FakeUrlopen:
        def __init__(self, req, timeout=20.0):
            url_str = req.full_url if hasattr(req, "full_url") else str(req)
            self.url_str = url_str
            self.url = url_str
            self.read_called = False

        def read(self, _size=65536):
            if "creatorhub-api" in self.url_str:
                return json.dumps(card_api_data).encode("utf-8")
            if "stream.m4a" in self.url_str:
                if not self.read_called:
                    self.read_called = True
                    return b"M4A_SYNTHETIC_AUDIO_CONTENT"
                return b""
            # Redirect response for naver.me
            return b'<html><meta property="og:url" content="https://m.naver.com/shorts/?seedMediaId=A08CD689352F2A92D6BF04CC676647DAE467" /></html>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(urllib.request, "urlopen", FakeUrlopen)

    # Mock STT provider to return grounded technical transcription
    class FakeSegment:
        def model_dump(self):
            return {"start_sec": 0.0, "end_sec": 15.0, "text": "네이버 대규모 분산 아키텍처와 트래픽 제어."}

    class FakeSTTResult:
        full_text = (
            "네이버 기술 밋업에 오신 것을 환영합니다. "
            "이번 세션에서는 대규모 분산 아키텍처와 글로벌 캐싱 트래픽 제어 시스템의 내부 구조를 상세히 다룹니다. "
            "분산 트랜잭션 관리와 데이터베이스 복제 지연을 최소화하기 위해 채택한 분산 락 및 메시지 큐 구현 사례를 공유합니다."
        )
        segments = [FakeSegment()]
        duration_sec = 3900.0

    class GroundedSTTProvider:
        def transcribe(self, path, language="ko", timestamps=True):
            return FakeSTTResult()

    monkeypatch.setattr(
        "claire.ingest.fetchers.naver_clip.get_transcript_provider",
        lambda settings: GroundedSTTProvider(),
    )

    # Mock LLM provider with grounded extraction matching the STT transcript
    class GroundedMockProvider(MockProvider):
        def extract(self, doc, target=None, **kwargs):
            return ExtractionResult(
                summary="네이버 기술 밋업의 대규모 분산 아키텍처 및 트래픽 제어 시스템 강연 요약.",
                key_claims=["분산 락과 메시지 큐를 활용해 데이터베이스 복제 지연을 최소화함"],
                entities=[
                    ExtractedEntity(
                        name="분산 트래픽 제어 시스템",
                        type="tool",
                        observations=["네이버 밋업에서 발표된 대규모 트래픽 분산 시스템"],
                    ),
                    ExtractedEntity(
                        name="NAVER D2",
                        type="org",
                        observations=["네이버 개발자 기술 지원 및 지식 공유 플랫폼"],
                    ),
                ],
                relations=[
                    ExtractedRelation(
                        source="NAVER D2",
                        target="분산 트래픽 제어 시스템",
                        type="maintains",
                    )
                ],
                model="mock-grounded",
                prompt_version="test-v1",
                raw_response='{"mock": true}',
            )

        def render_detail(self, doc, **kwargs):
            return (
                "= [기술밋업] 네이버 개발자가 들려주는 분산 시스템 이야기\n\n"
                "== 개요\n"
                "네이버 개발자 기술 밋업에서 발표된 대규모 분산 아키텍처 세션 내용을 다룬다.\n"
            )

    report = ingest(
        "https://naver.me/5eDc8abm",
        conn=conn,
        provider=GroundedMockProvider(),
        vstore=vstore,
        vault_dir=tmp_path,
        data_dir=tmp_path,
    )

    assert report.error is None
    assert report.source_type == "video"
    assert report.is_stt is True
    assert report.entities_created >= 2
    assert report.relations_added >= 1
    assert report.detail_rendered is True

    # Check stored document in SQLite DB
    row = dbm.get_document_row(conn, report.document_id)
    assert row is not None
    assert "[기술밋업]" in row["title"]
    assert "대규모 분산 아키텍처" in row["raw_text"]
    meta = json.loads(row["meta"] or "{}")
    assert meta.get("is_stt") is True
    assert meta.get("media_id") == "A08CD689352F2A92D6BF04CC676647DAE467"


def test_e2e_naver_clip_stt_failure_prevents_hallucination(monkeypatch, tmp_path: Path):
    """Negative E2E: Clip without transcript or substantive description is blocked from ingestion."""
    conn = _init_memory_db()
    vstore = VectorStore(conn, "brute")

    thin_api_data = {
        "header": {"code": 0, "message": "success"},
        "body": {
            "card": {
                "content": {
                    "title": "임피티 밥 먹고 서서 3분만",
                    "description": "#식후혈당 #혈당스파이크",
                    "profile": {"nickname": "임피티"},
                    "videoQuality": {"duration": 40.0},
                    "vod": {"media": []},
                }
            }
        },
    }

    class FakeUrlopenThin:
        def __init__(self, req, timeout=20.0):
            url_str = req.full_url if hasattr(req, "full_url") else str(req)
            self.url_str = url_str
            self.url = url_str

        def read(self, _size=65536):
            if "creatorhub-api" in self.url_str:
                return json.dumps(thin_api_data).encode("utf-8")
            return b'<html><meta property="og:url" content="https://m.naver.com/shorts/?seedMediaId=B12345678901234567890123456789012345" /></html>'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(urllib.request, "urlopen", FakeUrlopenThin)

    report = ingest(
        "https://naver.me/5eDc8abm",
        conn=conn,
        provider=MockProvider(),
        vstore=vstore,
        vault_dir=tmp_path,
        data_dir=tmp_path,
    )

    # Ingestion must be rejected with error and no hallucinated document stored
    assert report.error is not None
    assert "video_transcript_missing" in report.error

    # Ensure no documents were added to the DB
    docs = conn.execute("SELECT COUNT(*) as cnt FROM documents").fetchone()
    assert docs["cnt"] == 0

    # Ensure no hallucinated entities were created
    ents = conn.execute("SELECT COUNT(*) as cnt FROM entities").fetchone()
    assert ents["cnt"] == 0
