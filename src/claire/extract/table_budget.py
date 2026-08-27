"""테이블 감지 및 본문 글자 수 제한 예외 처리기.

원문의 테이블 내용을 완전히 보존하고, 데이터 오염/유실을 방지하기 위해
테이블 컨텐트 내의 문자 수는 본문 문자 수 제한(Prose Char Budget)에 포함하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# AsciiDoc 테이블 패턴: [cols=...] / .Title 등의 옵션 블록 + |=== ... |===
_ADOC_TABLE_RE = re.compile(
    r"(?:\n|^)(?:(?:\[[^\]\n]*cols[^\]\n]*\]|\.[^\n]+)\s*\n)?\|={3,}\s*\n[\s\S]*?\n\|={3,}(?:\n|$)",
    re.MULTILINE | re.IGNORECASE,
)

# Markdown 테이블 패턴: 헤더 행 + 구분선 행 (|---|---|) + 데이터 행들
_MD_TABLE_RE = re.compile(
    r"(?:\n|^)(?:\|[^\n]+\|\s*\n\|(?:\s*:?-+:?\s*\|)+\s*(?:\n\|[^\n]+\|)*)(?:\n|$)",
    re.MULTILINE,
)

# HTML 테이블 패턴 (혹시 raw HTML로 들어온 경우)
_HTML_TABLE_RE = re.compile(
    r"<table\b[^>]*>[\s\S]*?</table>",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass
class TextSegment:
    is_table: bool
    content: str


def split_text_segments(text: str) -> list[TextSegment]:
    """텍스트를 일반 본문(Prose)과 테이블(Table) 세그먼트 목록으로 분할.
    
    문서 내에서의 상대적 등장 순서를 그대로 유지한다.
    """
    if not text:
        return []

    # 1. 모든 테이블의 (start, end) 위치 수집
    spans: list[tuple[int, int]] = []
    for pat in (_ADOC_TABLE_RE, _MD_TABLE_RE, _HTML_TABLE_RE):
        for m in pat.finditer(text):
            spans.append((m.start(), m.end()))

    if not spans:
        return [TextSegment(is_table=False, content=text)]

    # 2. 겹치거나 인접한 구간 병합 (Merge overlapping spans)
    spans.sort(key=lambda x: (x[0], x[1]))
    merged_spans: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged_spans:
            merged_spans.append((start, end))
        else:
            prev_start, prev_end = merged_spans[-1]
            if start <= prev_end:
                merged_spans[-1] = (prev_start, max(prev_end, end))
            else:
                merged_spans.append((start, end))

    # 3. 세그먼트 리스트 조립
    segments: list[TextSegment] = []
    curr_pos = 0
    for start, end in merged_spans:
        if start > curr_pos:
            prose = text[curr_pos:start]
            if prose:
                segments.append(TextSegment(is_table=False, content=prose))
        table_content = text[start:end]
        if table_content:
            segments.append(TextSegment(is_table=True, content=table_content))
        curr_pos = end

    if curr_pos < len(text):
        trailing_prose = text[curr_pos:]
        if trailing_prose:
            segments.append(TextSegment(is_table=False, content=trailing_prose))

    return segments


def extract_tables_from_text(text: str) -> tuple[str, list[str]]:
    """텍스트에서 일반 본문과 테이블 목록을 분리. (prose_text, tables)."""
    segments = split_text_segments(text)
    prose_parts: list[str] = []
    tables: list[str] = []
    for seg in segments:
        if seg.is_table:
            tables.append(seg.content)
        else:
            prose_parts.append(seg.content)
    return "".join(prose_parts), tables


def slice_text_with_table_exemption(text: str, limit: int) -> str:
    """테이블 컨텐트를 본문 문자 수 제한에서 제외하고 슬라이싱.

    - 일반 본문(Prose)은 최대 `limit` 글자 수까지만 포함된다.
    - 테이블(Table)은 글자 수 카운트에 포함되지 않으며 100% 온전하게 보존된다.
    - 원문에서의 본문과 테이블 간의 배치 순서를 유지하여 결합한다.
    """
    if not text:
        return ""
    if limit <= 0:
        return text

    segments = split_text_segments(text)
    out_parts: list[str] = []
    remaining_budget = limit

    for seg in segments:
        if seg.is_table:
            # 테이블은 예산(limit)을 소모하지 않고 원본 전체를 보존
            out_parts.append(seg.content)
        else:
            if remaining_budget > 0:
                if len(seg.content) <= remaining_budget:
                    out_parts.append(seg.content)
                    remaining_budget -= len(seg.content)
                else:
                    out_parts.append(seg.content[:remaining_budget])
                    remaining_budget = 0
            # 예산이 소진된 이후의 일반 본문은 생략됨 (단, 뒤에 나오는 테이블은 계속 보존)

    return "".join(out_parts)


def has_tables(text: str | None) -> bool:
    """텍스트에 마크다운, AsciiDoc 또는 HTML 표가 1개 이상 포함되어 있는지 여부 반환."""
    if not text:
        return False
    _, tables = extract_tables_from_text(text)
    return len(tables) > 0


def count_tables(text: str | None) -> int:
    """텍스트에 포함된 표 개수 반환."""
    if not text:
        return 0
    _, tables = extract_tables_from_text(text)
    return len(tables)

