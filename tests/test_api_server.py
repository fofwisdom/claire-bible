"""Starlette API integration contracts for the redesigned web service."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from claire.api import security, server
from claire.extract.provider import emit_progress
from claire.store import db as dbm

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}
# 단일 테마 모드에서 요청이 쓰는 테마 (server._get_theme_ctx 의 기본 테마)
REQUEST_THEME_ID = 0


@dataclass
class StubSettings:
    db_file: Path
    data_dir: Path
    environment: str = "development"
    public_url: str = "http://127.0.0.1:8765"
    inject_token: str = OWNER_TOKEN
    readonly_token: str = READONLY_TOKEN
    cors_allowed_origins: str = ""
    anonymous_readonly: bool = False


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
        format: str | None = None,
        focus: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        emit_progress(f"{source}:{payload}")
        res: dict[str, Any] = {"ok": True, "payload": payload, "expand_max": expand_max}
        if format is not None:
            res["format"] = format
        if focus is not None:
            res["focus"] = focus
        return res

    def search(
        self,
        query: str,
        *,
        limit: int,
        summarize: bool,
        mode: str,
        include_hidden: bool,
    ) -> SimpleNamespace:
        self.search_calls.append(
            {
                "query": query,
                "limit": limit,
                "summarize": summarize,
                "mode": mode,
                "include_hidden": include_hidden,
            }
        )
        return SimpleNamespace(query=query, answer=None, hits=[])


class BlockingSearchService(StubService):
    def __init__(self, *, blocked_mode: str) -> None:
        super().__init__()
        self.blocked_mode = blocked_mode
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._active = 0

    def search(
        self,
        query: str,
        *,
        limit: int,
        summarize: bool,
        mode: str,
        include_hidden: bool,
    ) -> SimpleNamespace:
        if mode == self.blocked_mode:
            with self._lock:
                self._active += 1
                if self._active == 4:
                    self.started.set()
            if not self.release.wait(timeout=10):
                raise AssertionError("blocking search was not released")
            with self._lock:
                self._active -= 1
        return super().search(
            query,
            limit=limit,
            summarize=summarize,
            mode=mode,
            include_hidden=include_hidden,
        )


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


def test_whoami_returns_actual_authenticated_scope(client: TestClient) -> None:
    owner_response = client.get("/whoami", headers=OWNER_HEADERS)
    readonly_response = client.get("/whoami", headers=READONLY_HEADERS)

    assert owner_response.status_code == 200
    assert owner_response.json() == {"scope": "owner", "instance": {"mode": "single"}, "relation": "owner"}
    assert readonly_response.status_code == 200
    assert readonly_response.json() == {"scope": "readonly", "instance": {"mode": "single"}, "relation": "delegate"}


def test_readonly_document_get_does_not_mark_document_seen(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claire import graphview

    detail_calls: list[tuple[str, bool, int | None]] = []

    def fake_document_detail(
        _conn: Any, document_id: str, include_hidden: bool = True, theme_id: int | None = None
    ) -> dict[str, Any]:
        detail_calls.append((document_id, include_hidden, theme_id))
        return {"id": document_id, "title": "stub"}

    monkeypatch.setattr(graphview, "document_detail", fake_document_detail)

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
    assert detail_calls == [("doc-1", True, REQUEST_THEME_ID)]


def test_owner_document_seen_post_marks_document_seen(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, int | None]] = []
    row_calls: list[tuple[str, int | None]] = []

    def fake_get_document_row(_conn: Any, document_id: str, theme_id: int | None = None) -> dict[str, Any]:
        row_calls.append((document_id, theme_id))
        return {"id": document_id}

    monkeypatch.setattr(dbm, "get_document_row", fake_get_document_row)

    def record_seen(_conn: Any, document_id: str, *, seen: bool, theme_id: int | None = None) -> bool:
        calls.append((document_id, seen, theme_id))
        return True

    monkeypatch.setattr(dbm, "set_document_seen", record_seen)

    response = client.post(
        "/document/seen",
        json={"id": "doc-1"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"id": "doc-1", "seen": True}
    assert row_calls == [("doc-1", REQUEST_THEME_ID)]
    assert calls == [("doc-1", True, REQUEST_THEME_ID)]


def test_owner_document_title_updates_title(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, int | None]] = []

    def record_title(_conn: Any, document_id: str, title: str | None, theme_id: int | None = None) -> bool:
        calls.append((document_id, title, theme_id))
        return True

    monkeypatch.setattr(dbm, "set_document_title", record_title)

    response = client.post(
        "/document/title",
        json={"id": "doc-1", "title": "New Title"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 200
    assert response.json() == {"id": "doc-1", "title": "New Title"}
    assert calls == [("doc-1", "New Title", REQUEST_THEME_ID)]


def test_owner_document_title_rejects_missing_id(client: TestClient) -> None:
    response = client.post(
        "/document/title",
        json={"title": "New Title"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 400


def test_owner_document_title_returns_404_when_document_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int | None] = []

    def missing_title(_conn: Any, _id: str, _t: str | None, theme_id: int | None = None) -> bool:
        calls.append(theme_id)
        return False

    monkeypatch.setattr(dbm, "set_document_title", missing_title)

    response = client.post(
        "/document/title",
        json={"id": "doc-missing", "title": "New Title"},
        headers=OWNER_HEADERS,
    )

    assert response.status_code == 404
    assert calls == [REQUEST_THEME_ID]


def test_legacy_dedup_route_is_absent(client: TestClient) -> None:
    response = client.get("/dedup", headers=OWNER_HEADERS)

    assert response.status_code == 404


def test_owner_dedup_scan_post_reaches_service(
    client: TestClient,
    service: StubService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claire import graphview

    theme_ids: list[int | None] = []

    def fake_dedup_clusters(_conn: Any, scan: dict[str, Any], theme_id: int | None = None) -> dict[str, Any]:
        theme_ids.append(theme_id)
        return {"clusters": [], "scan": scan}

    monkeypatch.setattr(graphview, "dedup_clusters", fake_dedup_clusters)

    response = client.post("/dedup/scan", headers=OWNER_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "clusters": [],
        "scan": {"scan": "stub"},
    }
    assert service.dedup_scan_calls == 1
    assert theme_ids == [REQUEST_THEME_ID]


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
        "mode": "hybrid",
        "include_hidden": True,
    }
    assert response.json()["mode"] == "hybrid"


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
        "mode": "hybrid",
        "include_hidden": True,
    }
    assert response.json()["mode"] == "hybrid"


def test_search_explicit_fts_mode_for_authenticated_scopes(
    client: TestClient,
    service: StubService,
) -> None:
    # owner requesting fts mode
    owner_resp = client.post(
        "/search",
        json={"query": "claire", "mode": "fts", "summarize": True},
        headers=OWNER_HEADERS,
    )
    assert owner_resp.status_code == 200
    assert owner_resp.json()["mode"] == "fts"
    assert service.search_calls[-1] == {
        "query": "claire",
        "limit": 8,
        "summarize": False,
        "mode": "fts",
        "include_hidden": True,
    }

    # readonly requesting fts mode
    ro_resp = client.post(
        "/search",
        json={"query": "claire", "mode": "fts", "limit": 12},
        headers=READONLY_HEADERS,
    )
    assert ro_resp.status_code == 200
    assert ro_resp.json()["mode"] == "fts"
    assert service.search_calls[-1] == {
        "query": "claire",
        "limit": 12,
        "summarize": False,
        "mode": "fts",
        "include_hidden": True,
    }


def test_search_rejects_invalid_mode(
    client: TestClient,
) -> None:
    resp = client.post(
        "/search",
        json={"query": "claire", "mode": "invalid_mode"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 400
    assert "unsupported search mode" in resp.json()["error"]


def test_anonymous_search_forces_fts_without_summary_and_clamps_limit(
    settings: StubSettings,
    service: StubService,
) -> None:
    settings.anonymous_readonly = True
    app = server.create_app(settings, service)

    with TestClient(app, base_url=settings.public_url) as anonymous_client:
        whoami_response = anonymous_client.get("/whoami")
        search_response = anonymous_client.post(
            "/search",
            json={
                "query": "faith",
                "limit": 999,
                "summarize": True,
                "mode": "hybrid",
            },
        )

    assert whoami_response.status_code == 200
    assert whoami_response.json() == {"scope": "anonymous", "instance": {"mode": "single"}, "relation": "visitor"}
    assert search_response.status_code == 200
    assert search_response.json()["mode"] == "fts"
    assert service.search_calls[-1] == {
        "query": "faith",
        "limit": 20,
        "summarize": False,
        "mode": "fts",
        "include_hidden": False,
    }


def test_anonymous_search_has_separate_rate_limit_with_retry_after(
    settings: StubSettings,
    service: StubService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.anonymous_readonly = True
    monkeypatch.setattr(server, "_MAX_ANONYMOUS_SEARCH_JOBS", 0)
    app = server.create_app(settings, service)

    with TestClient(app, base_url=settings.public_url) as anonymous_client:
        response = anonymous_client.post(
            "/search",
            json={"query": "faith"},
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"error": "too many search requests"}
    assert service.search_calls == []


@pytest.mark.asyncio
async def test_anonymous_search_exact_four_job_cap_releases_and_isolates_hybrid(
    settings: StubSettings,
) -> None:
    settings.anonymous_readonly = True
    service = BlockingSearchService(blocked_mode="fts")
    app = server.create_app(settings, service)
    transport = ASGITransport(app=app)

    async with AsyncClient(
        transport=transport,
        base_url=settings.public_url,
    ) as async_client:
        active = [
            asyncio.create_task(
                async_client.post("/search", json={"query": f"anon-{index}"})
            )
            for index in range(4)
        ]
        try:
            assert await asyncio.to_thread(service.started.wait, 5)
            saturated = await asyncio.wait_for(
                async_client.post("/search", json={"query": "fifth"}),
                timeout=2,
            )
            hybrid = await asyncio.wait_for(
                async_client.post(
                    "/search",
                    json={"query": "owner"},
                    headers=OWNER_HEADERS,
                ),
                timeout=2,
            )
            assert saturated.status_code == 429
            assert saturated.headers["retry-after"] == "1"
            assert hybrid.status_code == 200
            assert hybrid.json()["mode"] == "hybrid"
        finally:
            service.release.set()
        assert all(response.status_code == 200 for response in await asyncio.gather(*active))

        reentered = await async_client.post(
            "/search",
            json={"query": "after-release"},
        )
        assert reentered.status_code == 200
        assert reentered.json()["mode"] == "fts"


@pytest.mark.asyncio
async def test_all_hybrid_searches_share_four_job_cap_and_isolate_anonymous(
    settings: StubSettings,
) -> None:
    settings.anonymous_readonly = True
    service = BlockingSearchService(blocked_mode="hybrid")
    app = server.create_app(settings, service)
    transport = ASGITransport(app=app)
    requests = [
        (OWNER_HEADERS, True),
        (READONLY_HEADERS, True),
        (OWNER_HEADERS, False),
        (READONLY_HEADERS, False),
    ]

    async with AsyncClient(
        transport=transport,
        base_url=settings.public_url,
    ) as async_client:
        active = [
            asyncio.create_task(
                async_client.post(
                    "/search",
                    json={"query": f"hybrid-{index}", "summarize": summarize},
                    headers=headers,
                )
            )
            for index, (headers, summarize) in enumerate(requests)
        ]
        try:
            assert await asyncio.to_thread(service.started.wait, 5)
            saturated = await asyncio.wait_for(
                async_client.post(
                    "/search",
                    json={"query": "fifth"},
                    headers=OWNER_HEADERS,
                ),
                timeout=2,
            )
            anonymous = await asyncio.wait_for(
                async_client.post("/search", json={"query": "anonymous"}),
                timeout=2,
            )
            assert saturated.status_code == 503
            assert anonymous.status_code == 200
            assert anonymous.json()["mode"] == "fts"
        finally:
            service.release.set()
        assert all(response.status_code == 200 for response in await asyncio.gather(*active))

        reentered = await async_client.post(
            "/search",
            json={"query": "after-release"},
            headers=READONLY_HEADERS,
        )
        assert reentered.status_code == 200
        assert reentered.json()["mode"] == "hybrid"


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


def test_server_routes_exactly_match_security_policy_with_explicit_head(
    settings: StubSettings,
    service: StubService,
) -> None:
    app: Any = server.create_app(settings, service)
    while not isinstance(app, Starlette):
        app = app.app

    actual = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, Route)
        for method in route.methods
    }
    # 정적 Mount는 경로 매개변수 키(GET·HEAD ``{path:path}``)로 정책에 오른다(§9.1, O2).
    actual |= {
        (method, f"{route.path}/{{path:path}}")
        for route in app.routes
        if isinstance(route, Mount)
        for method in ("GET", "HEAD")
    }

    assert actual == set(security.ROUTE_POLICY)
    assert all(
        ("HEAD", path) in actual
        for method, path in actual
        if method == "GET"
    )


def test_unregistered_method_path_pair_is_hidden(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 404
    assert "allow" not in response.headers


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
    search_response = client.post(
        "/search",
        json={"query": "faith", "summarize": False},
        headers=READONLY_HEADERS,
    )

    assert sync_response.status_code == 503
    assert sync_response.json() == {"error": "server is busy"}
    assert stream_response.status_code == 503
    assert stream_response.json() == {"error": "server is busy"}
    assert search_response.status_code == 503
    assert search_response.json() == {"error": "server is busy"}


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


def test_reference_serves_kepler_themed_scalar_html(client: TestClient) -> None:
    """Verify /reference serves offline HTML with Kepler theme and offline Scalar bundle."""
    response = client.get("/reference")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    text = response.text
    assert 'id="api-reference"' in text
    assert '"theme": "kepler"' in text
    assert "/static/docs/scalar.standalone.js" in text


def test_docs_path_does_not_exist(client: TestClient) -> None:
    """Verify /docs does not exist and returns 404."""
    response = client.get("/docs")
    assert response.status_code == 404


def test_openapi_yaml_serves_valid_openapi_3_1_spec(client: TestClient) -> None:
    """Verify /openapi.yaml returns valid OpenAPI 3.1 YAML matching contracts."""
    yaml = pytest.importorskip("yaml")

    response = client.get("/openapi.yaml")
    assert response.status_code == 200
    assert "application/yaml" in response.headers["content-type"]
    spec = yaml.safe_load(response.text)
    assert spec["openapi"].startswith("3.1")
    assert spec["info"]["title"] == "Claire Knowledge Base API"
    assert "/health" in spec["paths"]
    assert "/search" in spec["paths"]


def test_docs_static_asset_accessible(client: TestClient) -> None:
    """Verify vendored Scalar bundle is served via /static/docs/."""
    response = client.get("/static/docs/scalar.standalone.js")
    assert response.status_code == 200
    assert len(response.content) > 1_000_000


def _patch_share_store(
    monkeypatch: pytest.MonkeyPatch, *, hidden: int, token_prefix: str
) -> list[tuple[str, str, int | None]]:
    """/share 가 부르는 store 함수를 가짜로 바꾸고, 받은 (함수, 문서, theme_id) 호출 기록을 돌려준다."""
    calls: list[tuple[str, str, int | None]] = []

    def fake_get_document_row(_conn: Any, doc_id: str, theme_id: int | None = None) -> dict[str, Any]:
        calls.append(("get_document_row", doc_id, theme_id))
        return {"id": doc_id, "hidden": hidden}

    def fake_create_doc_share(_conn: Any, doc_id: str, *, theme_id: int = 0) -> str:
        calls.append(("create_doc_share", doc_id, theme_id))
        return f"{token_prefix}{doc_id}"

    monkeypatch.setattr(dbm, "get_document_row", fake_get_document_row)
    monkeypatch.setattr(dbm, "create_doc_share", fake_create_doc_share)
    return calls


def test_anonymous_can_create_share_link_in_public_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """공개 모드(anonymous_readonly=True)에서 익명 사용자가 공개 문서의 공유 링크를 생성할 수 있다."""
    settings = StubSettings(
        db_file=tmp_path / "claire.db",
        data_dir=tmp_path,
        anonymous_readonly=True,
    )
    service = StubService()
    app = server.create_app(settings, service)

    calls = _patch_share_store(monkeypatch, hidden=0, token_prefix="tok-")

    with TestClient(app, base_url=settings.public_url) as client:
        response = client.post("/share", json={"doc_id": "doc-public"})
        assert response.status_code == 200
        data = response.json()
        assert data["token"] == "tok-doc-public"
        assert data["path"] == "/p?s=tok-doc-public"
    assert calls == [
        ("get_document_row", "doc-public", REQUEST_THEME_ID),
        ("create_doc_share", "doc-public", REQUEST_THEME_ID),
    ]


def test_anonymous_cannot_share_hidden_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """익명 사용자는 숨김 문서(hidden=1)의 공유 링크를 생성할 수 없다 (404 Not Found)."""
    settings = StubSettings(
        db_file=tmp_path / "claire.db",
        data_dir=tmp_path,
        anonymous_readonly=True,
    )
    service = StubService()
    app = server.create_app(settings, service)

    calls = _patch_share_store(monkeypatch, hidden=1, token_prefix="tok-")

    with TestClient(app, base_url=settings.public_url) as client:
        response = client.post("/share", json={"doc_id": "doc-hidden"})
        assert response.status_code == 404
    # 숨김 문서는 토큰을 만들지 않는다
    assert calls == [("get_document_row", "doc-hidden", REQUEST_THEME_ID)]


def test_readonly_can_create_share_link(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    """읽기전용(readonly) 권한으로도 공유 링크 생성이 가능하다."""
    calls = _patch_share_store(monkeypatch, hidden=0, token_prefix="tok-ro-")

    response = client.post(
        "/share",
        json={"doc_id": "doc-ro"},
        headers=READONLY_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["token"] == "tok-ro-doc-ro"
    assert data["path"] == "/p?s=tok-ro-doc-ro"
    assert calls == [
        ("get_document_row", "doc-ro", REQUEST_THEME_ID),
        ("create_doc_share", "doc-ro", REQUEST_THEME_ID),
    ]


def test_stealth_mode_rejects_unauthenticated_share(
    client: TestClient,
) -> None:
    """비공개 스텔스 모드(anonymous_readonly=False)에서는 인증 없는 /share 요청이 404로 거부된다."""
    response = client.post("/share", json={"doc_id": "doc-1"})
    assert response.status_code == 404

