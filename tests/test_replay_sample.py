from __future__ import annotations

from collections import Counter
from pathlib import Path

from claire.ingest.router import classify
from scripts.replay_sample import parse_items


def test_public_sample_preserves_routing_fixture_shape():
    root = Path(__file__).resolve().parents[1]
    items = parse_items(str(root / "sample.md"))

    assert len(items) == 25
    assert Counter(map(classify, items)) == {
        "redirect": 14,
        "xcom": 5,
        "web": 4,
        "youtube": 1,
        "text": 1,
    }


def test_sample_parser_ignores_comments(tmp_path):
    sample = tmp_path / "sample.md"
    sample.write_text(
        "# 공개 설명은 입력이 아니다.\n"
        "https://example.com/\n"
        "합성 메모는 충분히 긴 텍스트 입력입니다.\n",
        encoding="utf-8",
    )

    assert parse_items(str(sample)) == [
        "https://example.com/",
        "합성 메모는 충분히 긴 텍스트 입력입니다.",
    ]
