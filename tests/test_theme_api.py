import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.ingest.service import IngestService
from claire.store.theme import ThemeManager

OWNER_TOKEN = "owner-" + ("o" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
READONLY_HEADERS = {"Authorization": f"Bearer {READONLY_TOKEN}"}


@pytest.fixture
def theme_app_client(tmp_path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    db_path = str(data_dir / "claire.db")
    vault_path = str(vault_dir)

    settings = Settings(
        CLAIRE_DB_PATH=db_path,
        CLAIRE_VAULT_PATH=vault_path,
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    yield client, settings


def test_theme_api_single_mode_behavior(tmp_path):
    """CLAIRE_MULTI_THEME=0(기본값, 싱글 모드) 시의 API 보안 가드 및 고정 동작 검증."""
    data_dir = tmp_path / "single_data"
    vault_dir = tmp_path / "single_vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)

    s = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
        CLAIRE_MULTI_THEME=False,
    )
    app = create_app(settings=s)
    with TestClient(app, base_url=s.public_url) as client:
        # 1. GET /themes -> multi_theme: False 및 기본 지식베이스 1개만 반환
        resp = client.get("/themes")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("multi_theme") is False
        assert len(data["themes"]) == 1
        assert data["themes"][0]["id"] == 0
        assert data["themes"][0]["label"] == "기본 지식베이스"

        # 2. POST /themes -> 403 차단
        resp_post = client.post("/themes", json={"label": "새 테마"}, headers=OWNER_HEADERS)
        assert resp_post.status_code == 403
        assert "멀티 테마 모드가 비활성화되어 있습니다" in resp_post.json().get("error", "")

        # 3. PATCH /themes -> 403 차단
        resp_patch = client.patch("/themes", json={"id": 0, "label": "수정"}, headers=OWNER_HEADERS)
        assert resp_patch.status_code == 403
        assert "멀티 테마 모드가 비활성화되어 있습니다" in resp_patch.json().get("error", "")

        # 4. DELETE /themes -> 403 차단
        resp_del = client.delete("/themes?id=1", headers=OWNER_HEADERS)
        assert resp_del.status_code == 403
        assert "멀티 테마 모드가 비활성화되어 있습니다" in resp_del.json().get("error", "")

        # 5. POST /ingest with arbitrary theme param -> 싱글 모드에서는 0번 기본 DB로 일관 격리/적재
        ingest_resp = client.post(
            "/ingest",
            json={"payload": "싱글 테마 모드 적재 내용", "theme": 999},
            headers=OWNER_HEADERS,
        )
        assert ingest_resp.status_code == 200
        assert ingest_resp.json().get("theme_id") == 0


def test_get_themes_anonymous_allowed(theme_app_client):
    client, _ = theme_app_client
    resp = client.get("/themes")
    assert resp.status_code == 200
    data = resp.json()
    assert "themes" in data
    themes = data["themes"]
    assert len(themes) >= 1
    assert themes[0]["id"] == 0
    assert themes[0]["label"] == "기본 지식베이스"
    assert "stats" in themes[0]


def test_define_theme_requires_owner(theme_app_client):
    client, _ = theme_app_client
    # 1. 익명 사용자 생성 시도 -> 404 (권한 미달 엔드포인트 숨김)
    resp = client.post("/themes", json={"label": "기술 및 AI"})
    assert resp.status_code in (403, 404)

    # 2. readonly 사용자 생성 시도 -> 404 (권한 미달 엔드포인트 숨김)
    resp = client.post(
        "/themes",
        json={"label": "기술 및 AI"},
        headers=READONLY_HEADERS,
    )
    assert resp.status_code in (403, 404)

    # 3. owner 사용자 생성 시도 -> 201 Created
    resp = client.post(
        "/themes",
        json={"label": "기술 및 AI", "description": "소프트웨어", "icon": "💻"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    theme_data = resp.json()["theme"]
    assert theme_data["id"] == 1
    assert theme_data["label"] == "기술 및 AI"
    assert theme_data["icon"] == "💻"


def test_theme_data_isolation_ingest_and_search(theme_app_client):
    client, _ = theme_app_client

    # 1. owner로 새 테마(id: 1) 정의
    client.post(
        "/themes",
        json={"label": "기술 및 AI", "icon": "💻"},
        headers=OWNER_HEADERS,
    )

    # 2. 테마 1에만 문서 적재
    ingest_resp = client.post(
        "/ingest",
        json={"payload": "테마 1 전용 독점 문서 내용", "theme": 1},
        headers=OWNER_HEADERS,
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json().get("theme_id") == 1

    # 3. 기본 테마(0)의 문서 목록 확인 -> 테마 1의 문서는 0건이어야 함
    docs_t0 = client.get("/documents?theme=0").json()
    assert len(docs_t0["documents"]) == 0

    # 4. 테마 1의 문서 목록 확인 -> 1건 존재해야 함
    docs_t1 = client.get("/documents?theme=1").json()
    assert len(docs_t1["documents"]) == 1

    # 5. 테마 0에서 검색 -> 0건 매치
    search_t0 = client.post(
        "/search",
        json={"query": "독점", "theme": 0},
        headers=OWNER_HEADERS,
    )
    assert len(search_t0.json()["hits"]) == 0

    # 6. 테마 1에서 검색 -> 1건 매치
    search_t1 = client.post(
        "/search",
        json={"query": "독점", "theme": 1},
        headers=OWNER_HEADERS,
    )
    assert len(search_t1.json()["hits"]) == 1


def test_theme_update_label(theme_app_client):
    client, _ = theme_app_client

    # 새 테마 정의
    client.post(
        "/themes",
        json={"label": "원래 이름"},
        headers=OWNER_HEADERS,
    )

    # 레이블 수정
    patch_resp = client.patch(
        "/themes",
        json={"id": 1, "label": "변경된 이름", "icon": "🚀"},
        headers=OWNER_HEADERS,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["theme"]["label"] == "변경된 이름"
    assert patch_resp.json()["theme"]["icon"] == "🚀"

    # 목록 조회 시 변경된 레이블 반영 확인
    list_resp = client.get("/themes")
    themes = list_resp.json()["themes"]
    t1 = next(t for t in themes if t["id"] == 1)
    assert t1["label"] == "변경된 이름"


def test_theme_visibility_api_security(theme_app_client):
    """지식 관리자의 비공개 테마 설정 및 익명 사용자 격리/보안 검증."""
    client, _ = theme_app_client

    # 1. owner 권한으로 비공개 테마(id: 1, is_public: false) 정의
    resp_create = client.post(
        "/themes",
        json={"label": "비밀 연구 테마", "icon": "🔒", "is_public": False},
        headers=OWNER_HEADERS,
    )
    assert resp_create.status_code == 201
    assert resp_create.json()["theme"]["is_public"] is False

    # 2. owner 권한으로 비공개 테마에 문서 적재
    ingest_resp = client.post(
        "/ingest",
        json={"payload": "비밀 프로젝트 기밀 문서", "theme": 1},
        headers=OWNER_HEADERS,
    )
    assert ingest_resp.status_code == 200
    assert ingest_resp.json().get("theme_id") == 1

    # 3. 익명(anonymous) 사용자 GET /themes -> 비공개 테마(1)는 목록에서 완전히 배제
    anon_list = client.get("/themes")
    assert anon_list.status_code == 200
    anon_theme_ids = [t["id"] for t in anon_list.json()["themes"]]
    assert 0 in anon_theme_ids
    assert 1 not in anon_theme_ids

    # 4. 인증된 owner 및 readonly 사용자 GET /themes -> 비공개 테마(1) 포함 및 is_public=False 표기
    owner_list = client.get("/themes", headers=OWNER_HEADERS)
    assert owner_list.status_code == 200
    owner_themes = owner_list.json()["themes"]
    t1 = next(t for t in owner_themes if t["id"] == 1)
    assert t1["is_public"] is False

    readonly_list = client.get("/themes", headers=READONLY_HEADERS)
    assert readonly_list.status_code == 200
    ro_theme_ids = [t["id"] for t in readonly_list.json()["themes"]]
    assert 1 in ro_theme_ids

    # 5. 익명 사용자가 비공개 테마에 직접 접근 시도 -> 404 차단
    # 5-1. /stats?theme=1
    assert client.get("/stats?theme=1").status_code == 404
    # 5-2. /documents?theme=1
    assert client.get("/documents?theme=1").status_code == 404
    # 5-3. /graph?theme=1
    assert client.get("/graph?theme=1").status_code == 404
    # 5-4. X-Claire-Theme 헤더로 접근
    assert client.get("/stats", headers={"X-Claire-Theme": "1"}).status_code == 404

    # 6. 인증된 owner는 비공개 테마 데이터 접근 정상 허용
    assert client.get("/stats?theme=1", headers=OWNER_HEADERS).status_code == 200
    assert client.get("/documents?theme=1", headers=OWNER_HEADERS).status_code == 200
    assert client.get("/graph?theme=1", headers=OWNER_HEADERS).status_code == 200

    # 7. 지식 관리자(owner)가 테마를 공개로 전환 (PATCH /themes, is_public: true)
    patch_resp = client.patch(
        "/themes",
        json={"id": 1, "is_public": True},
        headers=OWNER_HEADERS,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["theme"]["is_public"] is True

    # 8. 공개 전환 후 익명 사용자에게도 정상 노출 및 접근 허용
    anon_list_after = client.get("/themes")
    assert 1 in [t["id"] for t in anon_list_after.json()["themes"]]
    assert client.get("/stats?theme=1").status_code == 200
    assert client.get("/documents?theme=1").status_code == 200


def test_delete_theme_api(theme_app_client):
    """기본 테마 삭제 차단 및 추가 테마 삭제/소각(purge) API 검증."""
    client, _ = theme_app_client

    # 1. 새 테마 생성 (id: 1, 2)
    resp1 = client.post("/themes", json={"label": "삭제 대상 1"}, headers=OWNER_HEADERS)
    assert resp1.status_code == 201
    resp2 = client.post("/themes", json={"label": "삭제 대상 2"}, headers=OWNER_HEADERS)
    assert resp2.status_code == 201

    # 2. 기본 테마(0) 삭제 시도 -> 400 에러 차단
    del_default = client.delete("/themes?id=0", headers=OWNER_HEADERS)
    assert del_default.status_code == 400
    err_msg = str(del_default.json().get("error") or del_default.json().get("detail") or "")
    assert "기본 테마" in err_msg or "기본 지식베이스" in err_msg

    # 3. 권한 없는 사용자(익명/readonly) 삭제 시도 -> 403/404 차단
    assert client.delete("/themes?id=1").status_code in (403, 404)
    assert client.delete("/themes?id=1", headers=READONLY_HEADERS).status_code in (403, 404)

    # 4. owner 사용자가 쿼리 파라미터로 추가 테마 1 삭제 (purge=1)
    del_t1 = client.delete("/themes?id=1&purge=1", headers=OWNER_HEADERS)
    assert del_t1.status_code == 200
    assert del_t1.json()["deleted"]["id"] == 1

    # 5. owner 사용자가 JSON body로 추가 테마 2 삭제 (purge: true)
    del_t2 = client.request("DELETE", "/themes", json={"id": 2, "purge": True}, headers=OWNER_HEADERS)
    assert del_t2.status_code == 200
    assert del_t2.json()["deleted"]["id"] == 2

    # 6. 테마 목록 조회 -> 추가 테마들이 정상 제거되었고 기본 테마 0만 남았는지 확인
    themes_after = client.get("/themes", headers=OWNER_HEADERS).json()["themes"]
    remaining_ids = [t["id"] for t in themes_after]
    assert remaining_ids == [0]

