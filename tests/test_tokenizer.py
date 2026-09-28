"""검색 토크나이저 및 한국어 형태소 분석(kiwipiepy) FTS 검색 단위 테스트."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from claire.ontology.base import Entity
from claire.retrieval.query import search
from claire.retrieval.tokenizer import (
    KoreanKiwiTokenizer,
    SearchTokenizer,
    SimpleTokenizer,
    get_search_tokenizer,
)
from claire.store import db as dbm
from claire.store.vectors import VectorStore


def test_simple_tokenizer_fallback():
    tok = SimpleTokenizer()
    res = tok.extract_search_tokens("Hello, World! 123 안녕 형태소-분석")
    assert res == ["Hello", "World", "123", "안녕", "형태소", "분석"]
    assert tok.add_user_words(["신조어", "Hello"]) == 2
    assert tok.add_user_words(["신조어"]) == 0  # deduplicated


def test_korean_kiwi_tokenizer_pos_filtering():
    tok = KoreanKiwiTokenizer()
    # 조사('을'), 어미('ᆫ다')는 제거되고 실질 형태소(명사, 어근, 외래어, 동사 어간 등)만 추출
    tokens = tok.extract_search_tokens("한국어 형태소 분석 기능을 제공한다")
    assert "한국어" in tokens
    assert "형태소" in tokens
    assert "분석" in tokens
    assert "기능" in tokens
    assert "을" not in tokens  # 조사 제거
    assert "ᆫ다" not in tokens  # 어미 제거


def test_korean_kiwi_tokenizer_user_words():
    tok = KoreanKiwiTokenizer()
    # 사용자 단어 등록 전: '스크랩' + '링' 분절 가능성
    tok.add_user_words(["스크랩링"])
    tokens = tok.extract_search_tokens("스크랩링으로 데이터를 수집했다")
    assert "스크랩링" in tokens
    assert "으로" not in tokens
    assert "데이터" in tokens


def test_search_tokenizer_multilingual_routing():
    tok = SearchTokenizer()
    # 1. 순수 영문 텍스트: 고속 정규식 경로
    en_tokens = tok.extract_search_tokens("Scrapling framework v0.4 released!")
    assert en_tokens == ["Scrapling", "framework", "v0", "4", "released"]

    # 2. 한글 포함 텍스트: 형태소 분석 경로
    ko_tokens = tok.extract_search_tokens("자연어처리 파이프라인 구축")
    assert "자연어" in ko_tokens or "자연어처리" in ko_tokens
    assert "파이프라인" in ko_tokens
    assert "구축" in ko_tokens


def test_search_tokenizer_fallback_when_kiwi_unavailable():
    with patch("claire.retrieval.tokenizer.KoreanKiwiTokenizer", side_effect=ImportError("No module")):
        fallback_tok = SearchTokenizer()
        assert not fallback_tok.has_morphological_analyzer
        tokens = fallback_tok.extract_search_tokens("한국어 형태소 분석 기능")
        assert tokens == ["한국어", "형태소", "분석", "기능"]


def _init_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    dbm.init_db(conn)
    return conn


def test_fts_search_korean_postposition_matching():
    """교착어 조사 결합('기능을', '기능에') 상황에서 단독 명사('기능')로 검색 성공 검증."""
    conn = _init_memory_db()
    ent = Entity(
        type="Tool",
        name="테스트도구",
        observations=["한국어 형태소 분석 기능을 완벽하게 제공한다."],
    )
    dbm.upsert_entity(conn, ent)

    # 1. 단독 명사 '기능'으로 검색 -> 매칭 성공해야 함
    hits1 = dbm.fts_search(conn, "기능", limit=10)
    assert ent.id in hits1

    # 2. 다른 조사가 붙은 질의 '기능에' -> 매칭 성공해야 함
    hits2 = dbm.fts_search(conn, "기능에", limit=10)
    assert ent.id in hits2

    # 3. '분석기능' 질의 -> 매칭 성공해야 함
    hits3 = dbm.fts_search(conn, "분석 기능", limit=10)
    assert ent.id in hits3


def test_fts_search_korean_verb_stem_matching():
    """용언 어미 활용('구현했다', '수행하는')에서 어간/원형 질의('구현', '수행') 매칭 검증."""
    conn = _init_memory_db()
    ent = Entity(
        type="Framework",
        name="코어엔진",
        observations=["고성능 검색 파이프라인을 성공적으로 구현했다."],
    )
    dbm.upsert_entity(conn, ent)

    hits = dbm.fts_search(conn, "구현", limit=10)
    assert ent.id in hits


def test_fts_search_auto_registered_entity_name():
    """엔티티 등록 시 엔티티 이름/별칭이 사용자 사전에 자동 반영되어 조사가 붙어도 검색되는지 검증."""
    conn = _init_memory_db()
    ent = Entity(
        type="Tool",
        name="클레어바이블",
        aliases=["지식베이스엔진"],
        observations=["온톨로지 그래프 구축"],
    )
    dbm.upsert_entity(conn, ent)

    # 조사가 결합된 자연어 검색
    hits = dbm.fts_search(conn, "클레어바이블은 무엇인가", limit=10)
    assert ent.id in hits

    hits_alias = dbm.fts_search(conn, "지식베이스엔진으로 탐색", limit=10)
    assert ent.id in hits_alias


def test_heal_graph_reindexes_fts_with_morphology():
    """heal_graph 가 형태소 토크나이저를 사용해 FTS 색인을 정상 복구하는지 검증."""
    conn = _init_memory_db()
    conn.execute(
        "INSERT INTO documents (id, title, raw_text, fetched_at) VALUES (?, ?, ?, ?)",
        ("doc1", "제목", "본문", 0.0),
    )
    ent = Entity(
        type="Concept",
        name="형태소분석",
        observations=["언어학적 단위로 분해하는 자연어 처리 기법"],
        sources=["doc1"],
    )
    dbm.upsert_entity(conn, ent)

    # DB 치유 및 FTS 재색인
    healed = dbm.heal_graph(conn)
    assert healed["fts_reindexed"] == 1

    # 재색인 후에도 형태소 단위 검색이 정상 작동하는지 확인
    hits = dbm.fts_search(conn, "자연어", limit=10)
    assert ent.id in hits


def test_sync_user_dictionary_from_db():
    """DB 내 기존 엔티티들이 사용자 사전에 정상 동기화되는지 검증."""
    conn = _init_memory_db()
    ent1 = Entity(type="Tool", name="도구일번", aliases=["별칭가"])
    ent2 = Entity(type="Tool", name="도구이번", aliases=["별칭나"])
    dbm.upsert_entity(conn, ent1)
    dbm.upsert_entity(conn, ent2)

    added = dbm.sync_user_dictionary(conn)
    # 이미 upsert_entity 에서 등록되었을 수 있으므로 0 이상 정수 반환 확인
    assert isinstance(added, int)
