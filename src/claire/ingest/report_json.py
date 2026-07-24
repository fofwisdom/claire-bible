"""IngestReport → JSON 직렬화 (inject API 응답 / 검증 assertion 용)."""

from __future__ import annotations

from dataclasses import asdict

from .pipeline import IngestReport


def report_to_dict(r: IngestReport) -> dict:
    return asdict(r)
