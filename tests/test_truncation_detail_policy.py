"""원문 절단 시 내용 유실 섹션 상세 작성 배제 적재 정책 검증 테스트."""

from __future__ import annotations

import pytest

from claire.extract.prompts import (
    render_detail_prompt,
)


def test_render_detail_prompt_adoc_truncation_policy():
    """render_detail_prompt_adoc 에 절단되어 내용이 유실된 섹션의 상세 작성 배제 규칙이 포함되어 있는지 검증."""
    body = "샘플 원문 본문 내용"
    images = []

    prompt = render_detail_prompt(body, images, merged=False, template="academic")

    # 정책 핵심 문구 검증
    assert "절단 섹션 상세 작성 배제(적재 정책)" in prompt
    assert "원문을 절단하여 적재 및 상세 작성 시, 절단되어 내용이 유실된 섹션은 상세를 작성하지 않는다" in prompt
    assert "온전하게 보존된 섹션까지만 상세를 작성" in prompt
    assert "완전히 제외(생략)" in prompt


def test_render_detail_prompt_router_preserves_policy():
    """The truncation rule is a fixed rule: each rendition template keeps it, also with a merged source and a focus."""
    body = "긴 원문 데이터..."
    images = [{"url": "https://example.com/diag.png", "alt": "도식", "caption": "설명"}]
    focus = "아키텍처 및 핵심 API 중심"

    for template in ("standard", "concise", "academic"):
        prompt = render_detail_prompt(body, images, merged=True, scale=2, focus=focus, template=template)
        assert "원문을 절단하여 적재 및 상세 작성 시, 절단되어 내용이 유실된 섹션은 상세를 작성하지 않는다" in prompt, template
        assert "아키텍처 및 핵심 API 중심" in prompt, template
