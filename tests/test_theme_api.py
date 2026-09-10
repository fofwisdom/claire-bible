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
    )
    app = create_app(settings=settings)
    client = TestClient(app, base_url=settings.public_url)
    yield client, settings


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
