"""원문 슬라이싱 구성 및 .env 환경설정 동적 반영 단위 테스트."""

import pytest
from claire.config import Settings, get_settings
from claire.extract.prompts import doc_to_prompt
from claire.extract.table_budget import (
    extract_tables_from_text,
    slice_document_text,
    slice_text,
)
from claire.ingest.fetchers.textfile import fetch_file
from claire.ontology.base import Document


def test_slice_document_text_table_exemption_vs_strict():
    prose_before = "Alpha " * 1000  # 6000자
    table = "\n| Model | Metric |\n| --- | --- |\n| Claire | 99.9 |\n"
    prose_after = "Beta " * 1000   # 5000자
    full_text = f"{prose_before}{table}{prose_after}"

    # 1. table-exemption 전략: 표는 예산(5000자)을 소모하지 않고 100% 보존됨
    sliced_exemption, is_trunc_e, orig_e, _ = slice_document_text(
        full_text, limit=5000, strategy="table-exemption"
    )
    assert is_trunc_e is True
    assert table in sliced_exemption
    prose_only, tables = extract_tables_from_text(sliced_exemption)
    assert len(prose_only) == 5000
    assert len(tables) == 1

    # 2. strict 전략: 표 여부와 관계없이 정확히 5000자에서 절단됨
    sliced_strict, is_trunc_s, orig_s, len_s = slice_document_text(
        full_text, limit=5000, strategy="strict"
    )
    assert is_trunc_s is True
    assert len(sliced_strict) == 5000
    assert sliced_strict == full_text[:5000]


def test_doc_to_prompt_dynamic_budget(monkeypatch):
    prose = "Body text " * 1000  # 10,000자
    table = "\n| Col1 | Col2 |\n| --- | --- |\n| Val1 | Val2 |\n"
    doc = Document(
        title="Sample Document",
        url="https://example.com/doc",
        raw_text=f"{prose}{table}",
    )

    # 1. 기본 설정 (extract_char_budget = 20,000)
    monkeypatch.delenv("CLAIRE_EXTRACT_CHAR_BUDGET", raising=False)
    get_settings.cache_clear()
    prompt_default = doc_to_prompt(doc)
    assert prose in prompt_default
    assert table in prompt_default

    # 2. 커스텀 설정 (extract_char_budget = 3,000)
    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "3000")
    get_settings.cache_clear()
    prompt_custom = doc_to_prompt(doc)
    assert table in prompt_custom  # 표는 보존됨
    # 프롬프트 본문 중 CONTENT: 부분 추출
    content_part = prompt_custom.split("CONTENT:\n", 1)[1]
    prose_only, tables = extract_tables_from_text(content_part)
    assert len(prose_only) == 3000
    assert len(tables) == 1

    # 캐시 복원
    get_settings.cache_clear()


def test_doc_to_prompt_strict_strategy(monkeypatch):
    prose_before = "X" * 2000
    table = "\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    prose_after = "Y" * 2000
    doc = Document(
        title="Sample",
        url="https://example.com",
        raw_text=f"{prose_before}{table}{prose_after}",
    )

    monkeypatch.setenv("CLAIRE_EXTRACT_CHAR_BUDGET", "1000")
    monkeypatch.setenv("CLAIRE_SLICING_STRATEGY", "strict")
    get_settings.cache_clear()

    prompt = doc_to_prompt(doc)
    content_part = prompt.split("CONTENT:\n", 1)[1]
    assert len(content_part) == 1000
    assert content_part == prose_before[:1000]

    get_settings.cache_clear()


def test_fetch_file_dynamic_raw_char_budget(tmp_path, monkeypatch):
    test_file = tmp_path / "sample.txt"
    long_content = "Z" * 12000
    test_file.write_text(long_content, encoding="utf-8")

    # 1. raw_char_budget=5000 설정
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "5000")
    get_settings.cache_clear()

    doc = fetch_file(str(test_file))
    assert doc.meta["raw_truncated"] is True
    assert doc.meta["orig_chars"] == 12000
    assert doc.meta["raw_chars"] == 5000
    assert len(doc.raw_text) == 5000

    # 2. raw_char_budget=15000 설정 (절단되지 않음)
    monkeypatch.setenv("CLAIRE_RAW_CHAR_BUDGET", "15000")
    get_settings.cache_clear()

    doc2 = fetch_file(str(test_file))
    assert doc2.meta["raw_truncated"] is False
    assert doc2.meta["orig_chars"] == 12000
    assert doc2.meta["raw_chars"] == 12000
    assert len(doc2.raw_text) == 12000

    get_settings.cache_clear()
