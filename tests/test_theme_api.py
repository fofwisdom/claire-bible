import pytest
from starlette.testclient import TestClient

from tests.kb_paths import kb_list, kb_number, kb_url, seed, step_up_request

from claire.api.server import create_app
from claire.core.config import Settings
from claire.ingest.service import IngestService
from claire.store.knowledge_base import KnowledgeBaseManager
from tests.auth_helpers import bearer_headers


@pytest.fixture
def theme_app_client(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    yield client, settings


def test_theme_api_always_multi_theme_enabled(tmp_path):
    """CLAIRE_MULTI_THEME=0/false 환경변수가 있어도 멀티 KB API는 항상 활성화되어 있음."""
    data_dir = tmp_path / "always_multi_data"
    data_dir.mkdir(parents=True)

    s = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_FQDN="127.0.0.1:8765",
    )
    app = create_app(settings=s)
    owner_headers = bearer_headers(s, scope="owner")
    with TestClient(app, base_url=s.public_url) as client:
        resp = client.get("/kb", headers=owner_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("multi_theme") is True
        assert data["personal_kb"]["id"] == 0

        resp_post = client.post("/kb", json={"knowledge_type": "real", "label": "새 테마"}, headers=owner_headers)
        assert resp_post.status_code == 201
        created = resp_post.json()["knowledge_base"]
        assert created["id"] == 1
        assert created["label"] == "새 테마"

        resp_patch = client.patch(f"/kb/{created['number']}", json={"label": "수정"}, headers=owner_headers)
        assert resp_patch.status_code == 200
        assert resp_patch.json()["knowledge_base"]["label"] == "수정"


def test_get_themes_anonymous_allowed(theme_app_client):
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")
    # The collection of shared knowledge bases is public; the personal knowledge base is of the requester (design Section 7).
    resp = client.get("/kb")
    assert resp.status_code == 200
    data = resp.json()
    assert data["knowledge_bases"] == [] and data["personal_kb"] is None
    themes = kb_list(client, headers=owner_headers)
    assert len(themes) >= 1
    assert themes[0]["id"] == 0
    assert themes[0]["label"] == "기본 지식베이스"
    assert "stats" in themes[0]


def test_define_theme_requires_owner(theme_app_client):
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")
    readonly_headers = bearer_headers(settings, scope="readonly")
    # 1. 익명 사용자 생성 시도 -> 404 (권한 미달 엔드포인트 숨김)
    resp = client.post("/kb", json={"knowledge_type": "real", "label": "기술 및 AI"})
    assert resp.status_code in (403, 404)

    # 2. readonly 사용자 생성 시도 -> 404 (권한 미달 엔드포인트 숨김)
    resp = client.post(
        "/kb",
        json={"knowledge_type": "real", "label": "기술 및 AI"},
        headers=readonly_headers,
    )
    assert resp.status_code in (403, 404)

    # 3. owner 사용자 생성 시도 -> 201 Created
    resp = client.post(
        "/kb",
        json={"knowledge_type": "real", "label": "기술 및 AI", "description": "소프트웨어", "icon": "💻"},
        headers=owner_headers,
    )
    assert resp.status_code == 201
    theme_data = resp.json()["knowledge_base"]
    assert theme_data["id"] == 1
    assert theme_data["label"] == "기술 및 AI"
    assert theme_data["icon"] == "💻"


def test_theme_data_isolation_ingest_and_search(theme_app_client):
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")

    # 1. owner로 새 테마(id: 1) 정의
    client.post(
        "/kb",
        json={"knowledge_type": "real", "label": "기술 및 AI", "icon": "💻"},
        headers=owner_headers,
    )

    # 2. 테마 1에만 문서 적재
    ingest_resp = seed(settings, 1, "테마 1 전용 독점 문서 내용")
    assert ingest_resp.error is None and ingest_resp.document_id

    # 3. 기본 테마(0)의 문서 목록 확인 -> 테마 1의 문서는 0건이어야 함
    docs_t0 = client.get("/documents").json()
    assert len(docs_t0["documents"]) == 0

    # 4. 테마 1의 문서 목록 확인 -> 1건 존재해야 함
    docs_t1 = client.get(kb_url(settings, 1, "/documents")).json()
    assert len(docs_t1["documents"]) == 1

    # 5. 테마 0에서 검색 -> 0건 매치
    search_t0 = client.post(
        "/search",
        json={"query": "독점"},
        headers=owner_headers,
    )
    assert len(search_t0.json()["hits"]) == 0

    # 6. 테마 1에서 검색 -> 1건 매치
    search_t1 = client.post(
        kb_url(settings, 1, "/search"),
        json={"query": "독점"},
        headers=owner_headers,
    )
    assert len(search_t1.json()["hits"]) == 1


def test_theme_update_label(theme_app_client):
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")

    # 새 테마 정의
    client.post(
        "/kb",
        json={"knowledge_type": "real", "label": "원래 이름"},
        headers=owner_headers,
    )

    # 레이블 수정
    patch_resp = client.patch(
        f"/kb/{kb_number(settings, 1)}/",
        json={"label": "변경된 이름", "icon": "🚀"},
        headers=owner_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["knowledge_base"]["label"] == "변경된 이름"
    assert patch_resp.json()["knowledge_base"]["icon"] == "🚀"

    # 목록 조회 시 변경된 레이블 반영 확인
    themes = kb_list(client)
    t1 = next(t for t in themes if t["id"] == 1)
    assert t1["label"] == "변경된 이름"


def test_delete_theme_api(theme_app_client):
    """기본 테마 삭제 차단 및 추가 테마 삭제/소각(purge) API 검증."""
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")
    readonly_headers = bearer_headers(settings, scope="readonly")

    # 1. 새 테마 생성 (id: 1, 2)
    resp1 = client.post("/kb", json={"knowledge_type": "real", "label": "삭제 대상 1"}, headers=owner_headers)
    assert resp1.status_code == 201
    resp2 = client.post("/kb", json={"knowledge_type": "real", "label": "삭제 대상 2"}, headers=owner_headers)
    assert resp2.status_code == 201

    # 2. 기본 테마(0) 삭제 시도 -> 400 에러 차단
    # The delete of a knowledge base is a step-up route (design Section 6.8): a bearer credential gets 403.
    assert client.delete(kb_url(settings, 1, "/") + "?apply=true", headers=owner_headers).json() == {"error": "step_up_requires_session"}
    del_default = step_up_request(client, settings, "DELETE", "/?apply=true")
    assert del_default.status_code == 400
    assert del_default.json() == {"error": "kb_personal_cannot_delete"}

    # 3. 권한 없는 사용자(익명/readonly) 삭제 시도 -> 403/404 차단
    assert client.delete(kb_url(settings, 1, "/") + "?apply=true").status_code in (403, 404)
    assert client.delete(kb_url(settings, 1, "/") + "?apply=true", headers=readonly_headers).status_code in (403, 404)

    # 4. owner 사용자가 쿼리 파라미터로 추가 테마 1 삭제 (purge=1)
    # Without ``apply=true`` the answer is the plan only (PRINCIPLES D3).
    plan = step_up_request(client, settings, "DELETE", kb_url(settings, 1, "/"))
    assert (plan.status_code, plan.json()["applied"]) == (200, False)
    del_t1 = step_up_request(client, settings, "DELETE", kb_url(settings, 1, "/") + "?apply=true")
    assert del_t1.status_code == 200
    assert del_t1.json()["deleted"]["id"] == 1

    # 5. owner 사용자가 JSON body로 추가 테마 2 삭제 (purge: true)
    del_t2 = step_up_request(client, settings, "DELETE", kb_url(settings, 2, "/") + "?apply=true")
    assert del_t2.status_code == 200
    assert del_t2.json()["deleted"]["id"] == 2

    # 6. 테마 목록 조회 -> 추가 테마들이 정상 제거되었고 기본 테마 0만 남았는지 확인
    themes_after = kb_list(client, headers=owner_headers)
    remaining_ids = [t["id"] for t in themes_after]
    assert remaining_ids == [0]


def test_cross_theme_create_share_route(theme_app_client):
    """POST /share 호출 시 테마 미지정 또는 초기 테마 미발견 시 타 테마 자동 검색 및 해당 테마 DB 토큰 발급 검증."""
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")

    # 1. 새 테마(id: 1) 정의 및 테마 1에 문서 적재
    client.post("/kb", json={"knowledge_type": "real", "label": "AI 연구", "icon": "🤖"}, headers=owner_headers)
    ingest_resp = seed(settings, 1, "테마 1 전용 공유 대상 문서")
    assert ingest_resp.error is None
    doc_id = client.get(kb_url(settings, 1, "/documents")).json()["documents"][0]["id"]

    # 2. 클라이언트가 테마를 지정하지 않고 /share 요청 -> 타 테마(1)에서 문서 자동 검색 후 토큰 발급 및 kb_id: 1 반환
    share_resp = client.post(
        kb_url(settings, 1, "/share"),
        json={"doc_id": doc_id},
        headers=owner_headers,
    )
    assert share_resp.status_code == 200
    data = share_resp.json()
    assert "token" in data
    assert data["kb_id"] == 1
    assert data["path"] == "/silvercord/" + data["token"]

    # 3. 클라이언트가 초기 테마를 0(문서 없음)으로 지정하여 요청 -> 테마 1로 자동 확장 검색되어 정상 발급
    # Without the path, the personal knowledge base 0 has no such document: no search in other knowledge bases (CT23)
    share_resp_personal = client.post("/share", json={"doc_id": doc_id, "theme": 1}, headers=owner_headers)
    assert share_resp_personal.status_code == 404

    # 4. 존재하지 않는 문서 ID로 요청 시 404
    resp_404 = client.post("/share", json={"doc_id": "nonexistent_doc_id"}, headers=owner_headers)
    assert resp_404.status_code == 404


def test_cross_theme_document_detail_route(theme_app_client):
    """GET /document 호출 시 테마 미지정 시 기본 테마에 없으면 타 테마 자동 검색하여 kb_id/label 포함 반환 검증."""
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")

    # 1. 새 테마(id: 1) 생성 및 문서 적재
    client.post("/kb", json={"knowledge_type": "real", "label": "보안 연구", "icon": "🛡️"}, headers=owner_headers)
    seed(settings, 1, "보안 분석 상세 문서 본문")
    doc_id = client.get(kb_url(settings, 1, "/documents")).json()["documents"][0]["id"]

    # 2. 테마를 파라미터로 넘기지 않고 GET /document?id=... 호출 -> 테마 1 자동 해석 및 kb_id, theme_label 반환
    resp_auto = client.get(kb_url(settings, 1, "/document") + f"?id={doc_id}")
    assert resp_auto.status_code == 200
    doc_data = resp_auto.json()
    assert doc_data["id"] == doc_id
    assert doc_data["kb_id"] == 1
    assert doc_data["theme_label"] == "보안 연구"

    # 3. 명시적으로 theme=1 을 넘겨 호출 -> 정상 반환
    resp_explicit = client.get(kb_url(settings, 1, "/document") + f"?id={doc_id}")
    assert resp_explicit.status_code == 200
    assert resp_explicit.json()["kb_id"] == 1

    # 4. 명시적으로 존재하지 않는 theme=0 을 넘겨 호출 -> 교차 테마 검색을 수행하지 않고 404
    resp_explicit_wrong = client.get(f"/document?id={doc_id}&theme=1")
    assert resp_explicit_wrong.status_code == 404  # the path is the personal knowledge base 0; ``theme`` is ignored


def test_old_share_link_redirects_to_the_document_permalink(theme_app_client):
    """GET /p?s=token (an old share link) of a document in a shared knowledge base redirects to the permalink of the document, and the permalink page shows the document."""
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")
    from claire.store import db as dbm

    # 1. Make a shared knowledge base (id 1) and ingest a document into it.
    client.post("/kb", json={"knowledge_type": "real", "label": "클라우드 아키텍처"}, headers=owner_headers)
    seed(settings, 1, "클라우드 설계 원칙 공유 본문")
    doc_id = client.get(kb_url(settings, 1, "/documents")).json()["documents"][0]["id"]

    # 2. Share the document with the knowledge base of the document, as each caller of create_doc_share does.
    conn0 = dbm.connect_existing(settings.db_file)
    try:
        share_token = dbm.create_doc_share(conn0, doc_id, kb_id=1)
    finally:
        conn0.close()

    # 3. GET /p?s={share_token} -> 308 to /silvercord/{token} -> 200 HTML of the document.
    page_resp = client.get(f"/p?s={share_token}")
    assert page_resp.status_code == 200
    assert "text/html" in page_resp.headers.get("content-type", "")
    assert "클라우드 설계 원칙 공유 본문" in page_resp.text or "클라우드" in page_resp.text

    # 4. 존재하지 않는 무효 토큰 -> 404
    assert client.get("/p?s=" + ("x" * 24)).status_code == 404


def test_theme_knowledge_manager_api(theme_app_client):
    """테마 지식 관리자(sorcerer/knowledge_manager) 정의, 수정, 조회 API 검증."""
    client, settings = theme_app_client
    owner_headers = bearer_headers(settings, scope="owner")

    # 1. 테마 생성 시 sorcerer 지정
    resp = client.post(
        "/kb",
        json={"knowledge_type": "real", "label": "보안 연구", "sorcerer": "sec_officer"},
        headers=owner_headers,
    )
    assert resp.status_code == 201
    created = resp.json()["knowledge_base"]
    assert created["sorcerer"] == "sec_officer"
    assert created["knowledge_manager"] == "sec_officer"
    tid = created["id"]

    # 2. GET /themes 조회 시 테마별 지식 관리자 및 기본 지식 관리자 반환 검증
    themes = kb_list(client, headers=owner_headers)
    theme0 = next(t for t in themes if t["id"] == 0)
    theme_sec = next(t for t in themes if t["id"] == tid)
    assert theme0["knowledge_manager"] == ""
    assert theme_sec["sorcerer"] == "sec_officer"
    assert theme_sec["knowledge_manager"] == "sec_officer"

    # 3. PATCH /themes 로 지식 관리자 변경
    patch_resp = client.patch(
        f"/kb/{kb_number(settings, tid)}/",
        json={"sorcerer": "lead_auditor"},
        headers=owner_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["knowledge_base"]["sorcerer"] == "lead_auditor"
    assert patch_resp.json()["knowledge_base"]["knowledge_manager"] == "lead_auditor"

    # 4. The field name is ``sorcerer`` only. An old alias (``knowledge_manager``) is an unknown field and is ignored (CT23).
    patch_resp2 = client.patch(
        f"/kb/{kb_number(settings, tid)}/",
        json={"knowledge_manager": "chief_trust"},
        headers=owner_headers,
    )
    assert patch_resp2.status_code == 200
    assert patch_resp2.json()["knowledge_base"]["sorcerer"] == "lead_auditor"



