"""Starlette API integration contracts for the redesigned web service."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from claire.api import server
from claire.extract.provider import emit_progress
from claire.store import db as dbm


OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""


class StubService:
    def __init__(self) -> None:
        self.provider = SimpleNamespace(name="stub")
        self.dedup_scan_calls = 0
        self.search_calls: list[dict[str, Any]] = []

    def dedup_scan(self) -> dict[str, str]:
        self.dedup_scan_calls += 1
        return {"scan": "stub"}

    def ingest(
        self,
        payload: str,
        *,
        source: str,
        expand_max: int | None = None,
    ) -> dict[str, Any]:
        emit_progress(f"{source}:{payload}")
        return {"ok": True, "payload": payload, "expand_max": expand_max}

    def search(
        self,
        query: str,
        *,
        limit: int,
        summarize: bool,
    ) -> SimpleNamespace:
        self.search_calls.append(
            {"query": query, "limit": limit, "summarize": summarize}
        )
        return SimpleNamespace(query=query, answer=None, hits=[])


@pytest.fixture
def settings(tmp_path: Path) -> StubSettings:
    return StubSettings(db_file=tmp_path / "claire.db", data_dir=tmp_path)


@pytest.fixture
def service() -> StubService:
    return StubService()


@pytest.fixture
def client(settings: StubSettings, service: StubService):
    app = server.create_app(settings, service)
    with TestClient(app, base_url=settings.public_url) as test_client:
        yield test_client


def test_public_health_exposes_only_minimal_liveness(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claire import health

    monkeypatch.setattr(
        health,
        "liveness_report",
        lambda _settings: {
            "ok": True,
            "schema_version": dbm.SCHEMA_VERSION,
        },
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_readonly_document_get_does_not_mark_document_seen(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claire import graphview

    monkeypatch.setattr(
        graphview,
        "document_detail",
        lambda _conn, document_id: {"id": document_id, "title": "stub"},
    )

    def unexpected_seen_mutation(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("GET /document must not mutate seen state")

    monkeypatch.setattr(dbm, "set_document_seen", unexpected_seen_mutation)

    response = client.get(
        "/document",
        params={"id": "doc-1"},
        headers=READONLY_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["id"] == "doc-1"


def test_owner_document_seen_post_marks_document_seen(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        dbm,
        "get_document_row",
        lambda _conn, document_id: {"id": document_id},
    )

    def record_seen(_conn: Any, document_id: str, *, seen: bool) -> bool:
        calls.append((document_id, seen))
        return True

    monkeypatch.setattr(dbm, "set_document_seen", record_seen)

    response = client.post(
        "/document/seen",
        json={"id": "doc-1"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"id": "doc-1", "seen": True}
    assert calls == [("doc-1", True)]


def test_legacy_dedup_route_is_absent(client: TestClient) -> None:
    response = client.get("/dedup", headers=OWNER_HEADERS)

    assert response.status_code == 404


def test_owner_dedup_scan_post_reaches_service(
    client: TestClient,
    service: StubService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claire import graphview

    monkeypatch.setattr(
        graphview,
        "dedup_clusters",
        lambda _conn, scan: {"clusters": [], "scan": scan},
    )

    response = client.post("/dedup/scan", headers=OWNER_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "clusters": [],
        "scan": {"scan": "stub"},
    }
    assert service.dedup_scan_calls == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/auth/request"),
        ("GET", "/auth/poll"),
    ],
)
def test_legacy_auth_routes_are_absent(
    client: TestClient,
    method: str,
    path: str,
) -> None:
    response = client.request(method, path, headers=OWNER_HEADERS)

    assert response.status_code == 404


def test_image_route_rejects_svg(
    client: TestClient,
    settings: StubSettings,
) -> None:
    image_dir = settings.data_dir / "images"
    image_dir.mkdir()
    (image_dir / "sample.svg").write_text("<svg/>", encoding="utf-8")

    response = client.get("/image", params={"p": "images/sample.svg"})

    assert response.status_code == 404


def test_ingest_stream_returns_newline_delimited_final_done_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "report_to_dict", lambda report: report)

    response = client.post(
        "/ingest-stream",
        json={"payload": "hello", "expand_max": 2},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.content.endswith(b"\n")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events == [
        {"stage": "work", "msg": "web:hello"},
        {
            "done": True,
            "result": {"ok": True, "payload": "hello", "expand_max": 2},
        },
    ]


def test_readonly_search_never_runs_summary_and_limits_result_count(
    client: TestClient,
    service: StubService,
) -> None:
    response = client.post(
        "/search",
        json={"query": "faith", "limit": 999, "summarize": True},
        headers=READONLY_HEADERS,
    )

    assert response.status_code == 200
    assert service.search_calls[-1] == {
        "query": "faith",
        "limit": 50,
        "summarize": False,
    }


def test_owner_search_can_run_summary_and_clamps_low_limit(
    client: TestClient,
    service: StubService,
) -> None:
    response = client.post(
        "/search",
        json={"query": "hope", "limit": -4, "summarize": True},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert service.search_calls[-1] == {
        "query": "hope",
        "limit": 1,
        "summarize": True,
    }


def test_search_rejects_oversized_query(client: TestClient) -> None:
    response = client.post(
        "/search",
        json={"query": "x" * 2001},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 400


def test_ingest_failure_is_500_without_internal_error_text(
    client: TestClient,
    service: StubService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "https://private.example/token"

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(service, "ingest", fail)
    response = client.post(
        "/ingest",
        json={"payload": "x"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 500
    assert response.json() == {"error": "ingest failed", "ok": False}
    assert secret not in response.text
    assert secret not in caplog.text


def test_method_not_allowed_preserves_allow_header(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405
    assert "GET" in response.headers["allow"]


def test_expensive_job_limit_applies_to_sync_and_stream_ingest(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "_MAX_EXPENSIVE_JOBS", 0)

    sync_response = client.post(
        "/ingest",
        json={"payload": "x"},
        headers=OWNER_HEADERS,
    )
    stream_response = client.post(
        "/ingest-stream",
        json={"payload": "x"},
        headers=OWNER_HEADERS,
    )

    assert sync_response.status_code == 503
    assert sync_response.json() == {"error": "server is busy"}
    assert stream_response.status_code == 503
    assert stream_response.json() == {"error": "server is busy"}


def test_endpoint_exception_is_generic_500_without_secret_logs(
    client: TestClient,
    service: StubService,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "private-provider-response"

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(service, "search", fail)
    caplog.set_level(logging.WARNING, logger="claire.api.access")
    response = client.post(
        "/search",
        json={"query": "x", "summarize": False},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 500
    assert response.json() == {"error": "internal server error"}
    assert secret not in response.text
    assert secret not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
