"""테마 번호 라우팅 및 호환성 검증 (설계 §6.2, §6.3, §7.2, §7.3, §18.6).

검증 항목:
1. v16 테마 번호 하한 (MIN_CURRENT_NUMBER >= 2^56, created_at=0 하한 고정 포함)
2. CLAIRE_MULTI_THEME=0 일관성 (Theme 0이 DB에서 읽히고, /themes/{number}/ 및 ?theme={number}와 ?theme=0이 동작, Theme 1 등 다른 테마는 404)
3. 테마 고정: 경로에 테마 번호가 있을 때 ns.pinned_theme_id 바인딩
4. 고정과 다른 ?theme= 가 들어오면 400 거부
5. 다른 계정의 테마 번호 요청 시 404
6. 루트 해석기 308 (기존 SPA 쿼리 doc/node/theme 있을 때 /@{admin}/?... 리다이렉트)
7. 0을 포함한 레거시 내부 ID (0 <= id < 2^31) 요청 시 응답에 Deprecation 및 Sunset 헤더 검증
8. v16 공개 번호 (>= 2^56) 요청 시에는 Deprecation 및 Sunset 헤더가 붙지 않는지 검증
9. 테스트 격리 (§18.2-6): tmp_path 사용, CLAIRE_* 환경변수 격리.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from claire import ids
from claire.api.server import create_app
from claire.config import Settings
from claire.store import accounts as accounts_store
from claire.store import db as dbm
from claire.store.theme import ThemeInfo, ThemeManager
from claire.store.token_store import reset_token_stores

ADMIN_LEGACY_UNIX_MS = dbm.ADMIN_LEGACY_PUBLIC_ID_UNIX_MS
MIN_NUMBER = ids.MIN_CURRENT_NUMBER


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch):
    for name in list(os.environ):
        if name.startswith("CLAIRE_"):
            monkeypatch.delenv(name, raising=False)
    reset_token_stores()
    yield
    reset_token_stores()


def _init_test_db(db_file: Path) -> sqlite3.Connection:
    conn = dbm.connect(db_file)
    dbm.init_db(conn)
    return conn


def _create_single_theme_client(tmp_path: Path) -> tuple[TestClient, Settings, ThemeInfo]:
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=False,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    theme_mgr = ThemeManager(s)
    theme0 = theme_mgr.get_theme(0)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    return client, s, theme0


def _create_multi_theme_client(tmp_path: Path) -> tuple[TestClient, Settings, ThemeInfo, ThemeInfo]:
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    now = time.time()
    num1 = ids.timestamp_id("themes", salt=dbm.read_id_salt(conn), unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (1, 1, ?, 'Theme Alpha', 1, 0, 1, ?, ?)",
        (num1, now, now),
    )
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    theme_mgr = ThemeManager(s)
    theme0 = theme_mgr.get_theme(0)
    theme1 = theme_mgr.get_theme(1)
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    return client, s, theme0, theme1


def _create_network_mode_client(tmp_path: Path) -> tuple[TestClient, Settings, dict[str, int]]:
    db_file = tmp_path / "claire.db"
    conn = _init_test_db(db_file)
    now = time.time()
    salt = dbm.read_id_salt(conn)

    conn.execute(
        "UPDATE accounts SET username='adminuser', status='active', activated_at=? WHERE id=1",
        (now,),
    )
    alice_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 1)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'alice', 'member', 'active', 'Alice', 1, ?, ?, ?)",
        (alice_pub, now, now, now),
    )
    alice_id = int(cur.lastrowid)

    carol_pub = ids.timestamp_id("accounts", salt=salt, unix_ms=int(now * 1000) + 2)
    cur = conn.execute(
        "INSERT INTO accounts (public_id, username, role, status, display_name, profile_public, created_at, activated_at, updated_at) "
        "VALUES (?, 'carol', 'member', 'active', 'Carol', 1, ?, ?, ?)",
        (carol_pub, now, now, now),
    )
    carol_id = int(cur.lastrowid)

    num_alice_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 10)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (10, 10, ?, 'Alice Public Theme', ?, 1, 1, ?, ?)",
        (num_alice_pub, alice_id, now, now),
    )

    num_alice_priv = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 11)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (11, 11, ?, 'Alice Private Theme', ?, 0, 0, ?, ?)",
        (num_alice_priv, alice_id, now, now),
    )

    num_carol_pub = ids.timestamp_id("themes", salt=salt, unix_ms=int(now * 1000) + 20)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (20, 20, ?, 'Carol Public Theme', ?, 1, 1, ?, ?)",
        (num_carol_pub, carol_id, now, now),
    )

    store = accounts_store.AccountStore(conn)
    store.set_setting("mode", "network", updated_by=1)
    conn.commit()
    conn.close()

    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    app = create_app(s)
    client = TestClient(app, base_url=s.public_url, follow_redirects=False)
    info = {
        "alice_id": alice_id,
        "carol_id": carol_id,
        "num_alice_pub": num_alice_pub,
        "num_alice_priv": num_alice_priv,
        "num_carol_pub": num_carol_pub,
    }
    return client, s, info


# ============================================================================
# 1. v16 테마 번호 하한 (MIN_CURRENT_NUMBER >= 2^56, created_at=0 하한 고정 포함)
# ============================================================================


def test_v16_theme_number_lower_bound_migration(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = dbm.connect(db_file)
    dbm.init_db(conn)

    # created_at이 0, 0.0 인 레거시 테마 생성 후 번호 백필 검증
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, created_at, updated_at) "
        "VALUES (101, 101, 'Theme Zero Time', 1, 0, 0)"
    )
    conn.execute(
        "INSERT INTO themes (id, seq, label, account_id, created_at, updated_at) "
        "VALUES (102, 102, 'Theme Float Zero', 1, 0.0, 0.0)"
    )
    conn.commit()

    assert dbm.legacy_theme_number_unix_ms(0) == ADMIN_LEGACY_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(None) == ADMIN_LEGACY_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(0.0) == ADMIN_LEGACY_UNIX_MS
    assert dbm.legacy_theme_number_unix_ms(100.0) == ADMIN_LEGACY_UNIX_MS

    filled = dbm.backfill_theme_numbers(conn)
    assert filled == 2
    conn.commit()

    rows = conn.execute("SELECT id, number FROM themes WHERE id IN (101, 102)").fetchall()
    for row in rows:
        num = row["number"]
        assert num is not None
        assert num >= MIN_NUMBER, f"theme {row['id']} number {num} is below MIN_CURRENT_NUMBER"
        assert ids.parse_public_id(num) == num
        is_internal, parsed_val = ids.parse_internal_id_or_number(num)
        assert is_internal is False
        assert parsed_val == num

    # 새로 생성하는 테마 번호도 하한 이상인지 확인
    s = Settings(
        CLAIRE_DB_PATH=str(db_file),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_MULTI_THEME=True,
        CLAIRE_ENVIRONMENT="development",
    )
    mgr = ThemeManager(s)
    new_theme = mgr.define_theme("Fresh Theme")
    assert new_theme.number is not None
    assert new_theme.number >= MIN_NUMBER
    assert ids.parse_public_id(new_theme.number) == new_theme.number

    conn.close()


# ============================================================================
# 2. CLAIRE_MULTI_THEME=0 일관성
# ============================================================================


def test_single_theme_mode_consistency(tmp_path: Path) -> None:
    client, s, theme0 = _create_single_theme_client(tmp_path)

    # Theme 0이 DB에서 올바르게 읽히고 번호가 v16 하한 이상인지 검증
    assert theme0.id == 0
    assert theme0.number is not None
    assert theme0.number >= MIN_NUMBER

    # /themes/{number}/documents 및 /themes/{number}/ 동작 검증
    resp_pin_docs = client.get(f"/themes/{theme0.number}/documents")
    assert resp_pin_docs.status_code == 200

    resp_pin_root = client.get(f"/themes/{theme0.number}/")
    assert resp_pin_root.status_code == 200

    # ?theme={number} 동작 검증
    resp_q_num = client.get(f"/documents?theme={theme0.number}")
    assert resp_q_num.status_code == 200

    # ?theme=0 동작 검증
    resp_q_0 = client.get("/documents?theme=0")
    assert resp_q_0.status_code == 200

    # DB에 Theme 1 등 추가 테마가 있어도 CLAIRE_MULTI_THEME=0 에서는 404
    conn = dbm.connect(Path(s.db_file))
    now = time.time()
    num1 = ids.timestamp_id("themes", salt=dbm.read_id_salt(conn), unix_ms=int(now * 1000) + 100)
    conn.execute(
        "INSERT INTO themes (id, seq, number, label, account_id, is_default, is_public, created_at, updated_at) "
        "VALUES (1, 1, ?, 'Forbidden Second', 1, 0, 1, ?, ?)",
        (num1, now, now),
    )
    conn.commit()
    conn.close()

    assert client.get("/documents?theme=1").status_code == 404
    assert client.get(f"/documents?theme={num1}").status_code == 404
    assert client.get(f"/themes/{num1}/documents").status_code == 404
    assert client.get("/themes/99999999999999999/documents").status_code == 404
    assert client.get("/themes/99999999999999999/").status_code == 404


# ============================================================================
# 3. 테마 고정: 경로에 테마 번호가 있을 때 ns.pinned_theme_id 바인딩
# ============================================================================


def test_theme_pinning_binds_pinned_theme_id(tmp_path: Path) -> None:
    client, _, theme0, theme1 = _create_multi_theme_client(tmp_path)

    # 경로에 테마 번호가 포함되면 graph API에서 해당 테마의 내부 ID를 반환
    r0 = client.get(f"/themes/{theme0.number}/graph")
    assert r0.status_code == 200
    assert r0.json().get("theme_id") == 0
    assert r0.json().get("theme_label") == theme0.label

    r1 = client.get(f"/themes/{theme1.number}/graph")
    assert r1.status_code == 200
    assert r1.json().get("theme_id") == 1
    assert r1.json().get("theme_label") == theme1.label


# ============================================================================
# 4. 고정과 다른 ?theme= 가 들어오면 400 거부
# ============================================================================


def test_contradictory_theme_query_rejected_with_400(tmp_path: Path) -> None:
    client, _, theme0, theme1 = _create_multi_theme_client(tmp_path)
    base_pin0 = f"/themes/{theme0.number}"

    # 고정된 테마 0 경로에서 다른 테마(Theme 1 번호, 내부 ID 1, 미존재 번호) 요청 시 400 거부
    r_diff_num = client.get(f"{base_pin0}/documents?theme={theme1.number}")
    assert r_diff_num.status_code == 400
    assert "contradicts pinned theme" in r_diff_num.json().get("error", "")

    r_diff_id = client.get(f"{base_pin0}/documents?theme=1")
    assert r_diff_id.status_code == 400
    assert "contradicts pinned theme" in r_diff_id.json().get("error", "")

    r_non_existent = client.get(f"{base_pin0}/documents?theme=99999999999999999")
    assert r_non_existent.status_code == 400

    # 헤더 및 본문의 모순 요청도 400 거부
    r_hdr = client.get(f"{base_pin0}/documents", headers={"X-Claire-Theme": "1"})
    assert r_hdr.status_code == 400

    r_post_diff = client.post(f"{base_pin0}/search", json={"query": "test", "theme": 1})
    assert r_post_diff.status_code == 400

    # 동일한 테마를 가리키는 파라미터는 정상 통과 (200 OK)
    assert client.get(f"{base_pin0}/documents?theme={theme0.number}").status_code == 200
    assert client.get(f"{base_pin0}/documents?theme=0").status_code == 200
    assert client.get(f"{base_pin0}/documents?theme={theme0.label}").status_code == 200
    assert client.get(f"{base_pin0}/documents", headers={"X-Claire-Theme": "0"}).status_code == 200
    assert client.post(f"{base_pin0}/search", json={"query": "test", "theme": 0}).status_code == 200


# ============================================================================
# 5. 다른 계정의 테마 번호 요청 시 404
# ============================================================================


def test_cross_account_theme_number_returns_404(tmp_path: Path) -> None:
    client, _, info = _create_network_mode_client(tmp_path)
    num_alice = info["num_alice_pub"]
    num_carol = info["num_carol_pub"]

    # Alice 네임스페이스 아래에서 Carol의 테마 번호 경로 요청 시 404
    r_path = client.get(f"/@alice/themes/{num_carol}/documents")
    assert r_path.status_code == 404

    # Alice 네임스페이스 아래에서 Carol의 테마 번호 쿼리 요청 시 404
    r_query = client.get(f"/@alice/documents?theme={num_carol}")
    assert r_query.status_code == 404

    # Alice 네임스페이스 아래에서 Theme 0(Admin 테마) 요청 시 404
    assert client.get("/@alice/documents?theme=0").status_code == 404

    # Alice 본인의 테마 요청은 정상 200
    assert client.get(f"/@alice/themes/{num_alice}/documents").status_code == 200
    assert client.get(f"/@alice/documents?theme={num_alice}").status_code == 200


# ============================================================================
# 6. 루트 해석기 308 (기존 SPA 쿼리 doc/node/theme 있을 때 /@{admin}/?... 리다이렉트)
# ============================================================================


def test_root_resolver_308_with_legacy_spa_query(tmp_path: Path) -> None:
    client, _, _ = _create_network_mode_client(tmp_path)

    # SPA 쿼리가 있는 경우 /@{admin}/?... 로 308 리다이렉트
    r_doc = client.get("/?doc=42")
    assert r_doc.status_code == 308
    assert r_doc.headers.get("location") == "/@adminuser/?doc=42"

    r_node = client.get("/?node=abc")
    assert r_node.status_code == 308
    assert r_node.headers.get("location") == "/@adminuser/?node=abc"

    r_theme = client.get("/?theme=123")
    assert r_theme.status_code == 308
    assert r_theme.headers.get("location") == "/@adminuser/?theme=123"

    r_multi = client.get("/?doc=42&theme=123")
    assert r_multi.status_code == 308
    assert r_multi.headers.get("location") == "/@adminuser/?doc=42&theme=123"

    # SPA 쿼리가 없는 루트 GET / 는 네트워크 모드에서 공개 디렉터리 렌더링 (200, P3 D4)
    assert client.get("/").status_code == 200
    assert client.get("/?other=param").status_code == 200

    # 싱글 모드에서는 루트 GET /?doc=42 가 리다이렉트 없이 200 응답
    single_client, _, _ = _create_single_theme_client(tmp_path / "single")
    assert single_client.get("/?doc=42").status_code == 200


def test_root_theme_resolver_in_network_mode(tmp_path: Path) -> None:
    client, _, info = _create_network_mode_client(tmp_path)
    num_alice_pub = info["num_alice_pub"]
    num_alice_priv = info["num_alice_priv"]

    # 공개 테마 루트 접근 시 /@{owner}/themes/{number}/... 로 308 리다이렉트
    r_root_theme = client.get(f"/themes/{num_alice_pub}/")
    assert r_root_theme.status_code == 308
    assert r_root_theme.headers.get("location") == f"/@alice/themes/{num_alice_pub}/"

    r_root_theme_doc = client.get(f"/themes/{num_alice_pub}/documents")
    assert r_root_theme_doc.status_code == 308
    assert r_root_theme_doc.headers.get("location") == f"/@alice/themes/{num_alice_pub}/documents"

    # 비공개 테마는 익명 요청 시 404로 숨김
    assert client.get(f"/themes/{num_alice_priv}/").status_code == 404
    assert client.get("/themes/99999999999999999/").status_code == 404


# ============================================================================
# 7. 레거시 내부 ID (0 <= id < 2^31) 요청 시 Deprecation 및 Sunset 헤더 검증
# ============================================================================


def test_deprecation_and_sunset_headers_on_legacy_internal_id(tmp_path: Path) -> None:
    client, _, _, theme1 = _create_multi_theme_client(tmp_path)

    # 쿼리 파라미터 ?theme=0 (Theme 0 내부 ID)
    r0 = client.get("/documents?theme=0")
    assert r0.status_code == 200
    assert r0.headers.get("deprecation") == "@1798761600"
    assert r0.headers.get("sunset") == "Sun, 01 Jan 2027 00:00:00 GMT"

    # 쿼리 파라미터 ?theme=1 (Theme 1 내부 ID)
    r1 = client.get(f"/documents?theme={theme1.id}")
    assert r1.status_code == 200
    assert r1.headers.get("deprecation") == "@1798761600"
    assert r1.headers.get("sunset") == "Sun, 01 Jan 2027 00:00:00 GMT"

    # 헤더 X-Claire-Theme: 0
    r_hdr = client.get("/documents", headers={"X-Claire-Theme": "0"})
    assert r_hdr.status_code == 200
    assert r_hdr.headers.get("deprecation") == "@1798761600"
    assert r_hdr.headers.get("sunset") == "Sun, 01 Jan 2027 00:00:00 GMT"

    # 요청 본문 {"theme": 0}
    r_body = client.post("/search", json={"query": "test", "theme": 0})
    assert r_body.status_code == 200
    assert r_body.headers.get("deprecation") == "@1798761600"
    assert r_body.headers.get("sunset") == "Sun, 01 Jan 2027 00:00:00 GMT"


# ============================================================================
# 8. v16 공개 번호 (>= 2^56) 요청 시 Deprecation 및 Sunset 헤더 없음 검증
# ============================================================================


def test_no_deprecation_headers_on_v16_public_number(tmp_path: Path) -> None:
    client, _, theme0, theme1 = _create_multi_theme_client(tmp_path)

    # 쿼리 파라미터 ?theme={number}
    r_num = client.get(f"/documents?theme={theme0.number}")
    assert r_num.status_code == 200
    assert "deprecation" not in r_num.headers
    assert "sunset" not in r_num.headers

    # 테마 고정 경로 /themes/{number}/documents
    r_pin = client.get(f"/themes/{theme0.number}/documents")
    assert r_pin.status_code == 200
    assert "deprecation" not in r_pin.headers
    assert "sunset" not in r_pin.headers

    # 테마 1 공개 번호
    r1_num = client.get(f"/documents?theme={theme1.number}")
    assert r1_num.status_code == 200
    assert "deprecation" not in r1_num.headers
    assert "sunset" not in r1_num.headers

    # 헤더 X-Claire-Theme: {number}
    r_hdr = client.get("/documents", headers={"X-Claire-Theme": str(theme0.number)})
    assert r_hdr.status_code == 200
    assert "deprecation" not in r_hdr.headers
    assert "sunset" not in r_hdr.headers

    # 요청 본문 {"theme": "{number}"}
    r_body = client.post("/search", json={"query": "test", "theme": str(theme0.number)})
    assert r_body.status_code == 200
    assert "deprecation" not in r_body.headers
    assert "sunset" not in r_body.headers

    # 레이블 참조 시에도 비폐기
    r_label = client.get(f"/documents?theme={theme0.label}")
    assert r_label.status_code == 200
    assert "deprecation" not in r_label.headers
    assert "sunset" not in r_label.headers
