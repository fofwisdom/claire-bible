"""테마 번호 저장·조회 (설계 §4.2, §6.3, §7.2, §7.3, O4, O5).

모든 테스트는 tmp_path만 쓰고 호스트 셸의 ``CLAIRE_*`` 환경변수와 저장소 ``.env``를 끊는다(§18.2-6).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from claire import ids
from claire.config import Settings
from claire.store import db as dbm
from claire.store.theme import (
    THEME_FQDN_RETIRED_MESSAGE,
    ResolvedThemeRef,
    ThemeInfo,
    ThemeManager,
    ThemeNotFound,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)


def _settings(tmp_path: Path, *, multi: bool, **extra: object) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(tmp_path / "vault"),
        CLAIRE_PROVIDER="mock",
        CLAIRE_MULTI_THEME=multi,
        **extra,
    )


def _db_row(settings: Settings, theme_id: int) -> dict | None:
    conn = dbm.connect(settings.db_file)
    try:
        row = conn.execute("SELECT * FROM themes WHERE id=?", (theme_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _add_other_account_theme(settings: Settings, *, theme_id: int, label: str) -> int:
    """계정 2와 그 계정의 테마를 DB에 직접 넣는다. 번호를 반환한다."""
    conn = dbm.connect(settings.db_file)
    try:
        now = time.time()
        conn.execute(
            "INSERT INTO accounts (id, public_id, username, role, status, created_at, updated_at) "
            "VALUES (2, ?, 'bob', 'member', 'active', ?, ?)",
            (1 << 57, now, now),
        )
        conn.execute(
            "INSERT INTO themes (id, seq, label, is_default, created_at, updated_at, account_id) "
            "VALUES (?, ?, ?, 1, ?, ?, 2)",
            (theme_id, theme_id, label, now, now),
        )
        dbm.backfill_theme_numbers(conn)
        conn.commit()
        return conn.execute("SELECT number FROM themes WHERE id=?", (theme_id,)).fetchone()[0]
    finally:
        conn.close()


# --- ThemeInfo 직렬화 (O4) ---


def test_to_dict_number_is_decimal_string_or_none() -> None:
    big = (1 << 62) + 12345
    assert ThemeInfo(id=1, seq=1, label="x", number=big).to_dict()["number"] == str(big)
    assert ThemeInfo(id=1, seq=1, label="x").to_dict()["number"] is None
    d = ThemeInfo(id=1, seq=1, label="x", number=big).to_dict()
    assert d["visibility_epoch"] == 1
    assert d["embed_provider"] == "" and d["embed_model"] == ""


def test_from_dict_accepts_string_and_int_numbers() -> None:
    big = (1 << 60) + 7
    assert ThemeInfo.from_dict({"id": 1, "label": "x", "number": str(big)}).number == big
    assert ThemeInfo.from_dict({"id": 1, "label": "x", "number": big}).number == big
    assert ThemeInfo.from_dict({"id": 1, "label": "x"}).number is None
    assert ThemeInfo.from_dict({"id": 1, "label": "x", "number": None}).number is None
    roundtrip = ThemeInfo.from_dict(ThemeInfo(id=1, seq=1, label="x", number=big).to_dict())
    assert roundtrip.number == big
    for bad in ("0123", "-5", "abc", 0, True, 1.5):
        with pytest.raises(ValueError):
            ThemeInfo.from_dict({"id": 1, "label": "x", "number": bad})


# --- 새 테마 번호 (§7.2) ---


def test_define_theme_assigns_number_from_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    before_ms = time.time_ns() // 1_000_000
    theme = manager.define_theme("번호 테마")
    after_ms = time.time_ns() // 1_000_000

    assert theme.number is not None and theme.number >= ids.MIN_CURRENT_NUMBER
    assert before_ms <= theme.number >> 16 <= after_ms
    assert _db_row(settings, theme.id)["number"] == theme.number
    assert _db_row(settings, theme.id)["visibility_epoch"] == 1
    # themes.json 사본의 번호는 문자열이다.
    written = json.loads(manager.registry_path.read_text(encoding="utf-8"))
    entry = next(e for e in written["themes"] if e["id"] == theme.id)
    assert entry["number"] == str(theme.number)
    # 다시 읽어도 DB 번호 그대로다.
    fresh = ThemeManager(base_settings=settings)
    assert fresh.get_theme(theme.id).number == theme.number
    assert fresh.get_theme_by_number(theme.number).id == theme.id


def test_define_theme_retries_same_millisecond_collision(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    fixed_ms = 1_893_456_000_000
    manager._number_clock = lambda: fixed_ms
    first = manager.define_theme("첫째")
    second = manager.define_theme("둘째")

    conn = dbm.connect(settings.db_file)
    try:
        salt = dbm.read_id_salt(conn)
    finally:
        conn.close()
    assert first.number == ids.timestamp_id("themes", salt=salt, unix_ms=fixed_ms, seq=0)
    assert second.number == ids.timestamp_id("themes", salt=salt, unix_ms=fixed_ms, seq=1)


def test_define_theme_fails_after_max_collisions(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    manager._number_clock = lambda: 1_893_456_000_000
    for i in range(ids.MAX_INSERT_ATTEMPTS):
        manager.define_theme(f"테마 {i}")
    with pytest.raises(ids.TimestampIdExhausted):
        manager.define_theme("넘침")
    labels = {r["label"] for r in _rows(settings)}
    assert "넘침" not in labels


def _rows(settings: Settings) -> list[dict]:
    conn = dbm.connect(settings.db_file)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM themes ORDER BY id")]
    finally:
        conn.close()


def test_registry_only_theme_gets_number_on_reload(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    registry = settings.data_dir / "themes.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "next_seq": 2,
                "default_theme_id": 0,
                "themes": [
                    {"id": 0, "seq": 0, "label": "기본", "is_default": True},
                    {"id": 1, "seq": 1, "label": "레지스트리만", "created_at": 0},
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = ThemeManager(base_settings=settings)
    theme = manager.get_theme(1)
    assert theme.number is not None and theme.number >= ids.MIN_CURRENT_NUMBER
    assert _db_row(settings, 1)["number"] == theme.number


# --- FQDN 폐지 (O5) ---


def test_define_and_update_reject_fqdn(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    with pytest.raises(ValueError, match="theme FQDN is retired"):
        manager.define_theme("도메인 테마", fqdn="ai.example.com")
    assert [r["id"] for r in _rows(settings)] == [0]

    theme = manager.define_theme("일반 테마", fqdn="")
    with pytest.raises(ValueError) as exc:
        manager.update_theme(theme.id, label="바뀌면 안 됨", fqdn="ai.example.com")
    assert str(exc.value) == THEME_FQDN_RETIRED_MESSAGE
    # 거부된 호출은 다른 필드도 바꾸지 않는다.
    assert manager.get_theme(theme.id).label == "일반 테마"
    assert _db_row(settings, theme.id)["label"] == "일반 테마"


def test_update_with_empty_fqdn_keeps_legacy_column(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    theme = manager.define_theme("레거시")
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE themes SET fqdn='old.example.com' WHERE id=?", (theme.id,))
        conn.commit()
    finally:
        conn.close()
    updated = manager.update_theme(theme.id, description="설명", fqdn="")
    assert updated.description == "설명"
    assert _db_row(settings, theme.id)["fqdn"] == "old.example.com"


# --- CLAIRE_MULTI_THEME=0 일관성 (§4.2) ---


def test_single_mode_reads_theme_zero_from_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=False)
    ThemeManager(base_settings=settings)  # DB 초기화
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE themes SET label='DB 레이블', visibility_epoch=3 WHERE id=0")
        conn.commit()
    finally:
        conn.close()
    row = _db_row(settings, 0)

    manager = ThemeManager(base_settings=settings)
    t0 = manager.get_theme(0)
    assert t0.label == "DB 레이블"
    assert t0.number == row["number"] >= ids.MIN_CURRENT_NUMBER
    assert t0.visibility_epoch == 3
    assert t0.account_id == 1
    assert t0.is_default is True
    # 싱글 모드는 themes.json을 만들지 않는다.
    assert not (settings.data_dir / "themes.json").exists()
    # 활성 테마 목록도 같은 DB 행을 쓴다.
    [(active, _)] = manager.active_theme_settings(settings)
    assert active.number == row["number"]


def test_single_mode_keeps_settings_driven_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=False, CLAIRE_PRESENTATION_ENABLED=True)
    manager = ThemeManager(base_settings=settings)
    t0 = manager.get_theme(0)
    assert t0.presentation_enabled is True
    assert _db_row(settings, 0)["presentation_enabled"] == 0
    assert t0.db_path == str(settings.db_path)
    with pytest.raises(RuntimeError, match="멀티 테마 모드가 비활성화"):
        manager.define_theme("새 테마")


def test_single_mode_recreates_missing_theme_zero_in_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, multi=False)
    ThemeManager(base_settings=settings)
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("DELETE FROM themes WHERE id=0")
        conn.commit()
    finally:
        conn.close()
    # 생성자의 init_db가 행을 먼저 되살리지 않게 막고, reload 경로가 행을 만드는지 본다.
    monkeypatch.setattr(ThemeManager, "_init_db_file", lambda self: None)
    manager = ThemeManager(base_settings=settings)
    row = _db_row(settings, 0)
    assert row is not None and row["is_default"] == 1 and row["account_id"] == 1
    assert row["number"] is not None and row["number"] >= ids.MIN_CURRENT_NUMBER
    assert manager.get_theme(0).number == row["number"]


def test_single_and_multi_mode_see_same_theme_zero_number(tmp_path: Path) -> None:
    single = ThemeManager(base_settings=_settings(tmp_path, multi=False))
    multi = ThemeManager(base_settings=_settings(tmp_path, multi=True))
    assert single.get_theme(0).number == multi.get_theme(0).number is not None
    assert single.default_theme_for(1).number == multi.default_theme_for(1).number


def test_single_mode_readonly_without_db_uses_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=False)
    manager = ThemeManager(base_settings=settings, create_registry=False)
    t0 = manager.get_theme(0)
    assert t0.id == 0 and t0.number is None
    assert not Path(settings.db_file).exists()


def test_single_mode_resolve_theme_ref(tmp_path: Path) -> None:
    manager = ThemeManager(base_settings=_settings(tmp_path, multi=False))
    t0 = manager.get_theme(0)
    assert manager.resolve_theme_ref(None, account_id=1) == ResolvedThemeRef(theme=t0, via_internal_id=False)
    assert manager.resolve_theme_ref("0", account_id=1).via_internal_id is True
    by_number = manager.resolve_theme_ref(str(t0.number), account_id=1)
    assert by_number.theme.id == 0 and by_number.via_internal_id is False
    assert manager.list_account_themes(1) == [t0]
    assert manager.list_account_themes(2) == []
    with pytest.raises(ThemeNotFound):
        manager.default_theme_for(2)
    with pytest.raises(ThemeNotFound):
        manager.resolve_theme_ref(None, account_id=2)


# --- 조회 API (§6.3, §7.3) ---


@pytest.fixture
def multi_manager(tmp_path: Path) -> tuple[ThemeManager, Settings]:
    settings = _settings(tmp_path, multi=True)
    manager = ThemeManager(base_settings=settings)
    manager.define_theme("Alpha Research")
    manager.define_theme("베타")
    return manager, settings


def test_resolve_none_and_empty_is_account_default(multi_manager) -> None:
    manager, _ = multi_manager
    for ref in (None, "", "   "):
        resolved = manager.resolve_theme_ref(ref, account_id=1)
        assert resolved.theme.id == 0 and resolved.via_internal_id is False


def test_resolve_internal_id_branch_includes_zero(multi_manager) -> None:
    manager, _ = multi_manager
    for ref, tid in (("0", 0), (0, 0), ("1", 1), (2, 2)):
        resolved = manager.resolve_theme_ref(ref, account_id=1)
        assert resolved.theme.id == tid
        assert resolved.via_internal_id is True
    # 없는 내부 ID는 번호로 다시 해석하지 않는다.
    with pytest.raises(ThemeNotFound):
        manager.resolve_theme_ref("99", account_id=1)
    with pytest.raises(ThemeNotFound):
        manager.resolve_theme_ref(ids.INTERNAL_ID_LIMIT - 1, account_id=1)


def test_resolve_number_string_and_int(multi_manager) -> None:
    manager, _ = multi_manager
    alpha = manager.get_theme("Alpha Research")
    for ref in (str(alpha.number), alpha.number, f" {alpha.number} "):
        resolved = manager.resolve_theme_ref(ref, account_id=1)
        assert resolved.theme.id == alpha.id and resolved.via_internal_id is False
    # 내부 ID 대역 밖이지만 등록되지 않은 번호
    for ref in (ids.INTERNAL_ID_LIMIT, ids.MIN_CURRENT_NUMBER, str(ids.MAX_NUMBER), str(ids.MAX_NUMBER + 1), -1):
        with pytest.raises(ThemeNotFound):
            manager.resolve_theme_ref(ref, account_id=1)


def test_resolve_rejects_non_canonical_and_bad_types(multi_manager) -> None:
    manager, _ = multi_manager
    for ref in ("01", "00", "+1", "-1", "1.0", True, False, 1.0, b"1"):
        with pytest.raises(ThemeNotFound):
            manager.resolve_theme_ref(ref, account_id=1)


def test_resolve_label_exact_within_account(multi_manager) -> None:
    manager, _ = multi_manager
    resolved = manager.resolve_theme_ref("alpha research", account_id=1)
    assert resolved.theme.label == "Alpha Research" and resolved.via_internal_id is False
    assert manager.resolve_theme_ref("베타", account_id=1).theme.label == "베타"
    # 부분 일치는 없다(D7).
    for ref in ("Alpha", "Research", "베"):
        with pytest.raises(ThemeNotFound):
            manager.resolve_theme_ref(ref, account_id=1)


def test_resolve_rejects_other_account_theme(multi_manager) -> None:
    manager, settings = multi_manager
    other_number = _add_other_account_theme(settings, theme_id=10, label="Bob Notes")
    manager.reload()
    for ref in ("10", 10, str(other_number), other_number, "Bob Notes"):
        with pytest.raises(ThemeNotFound) as exc:
            manager.resolve_theme_ref(ref, account_id=1)
        # 존재 여부를 드러내지 않는다: 없는 테마와 같은 메시지 형식
        assert str(exc.value) == f"테마를 찾을 수 없습니다: {ref}"
    # 그 계정 안에서는 찾는다.
    assert manager.resolve_theme_ref(str(other_number), account_id=2).theme.id == 10
    assert manager.resolve_theme_ref(None, account_id=2).theme.id == 10
    assert manager.default_theme_for(2).id == 10
    # 계정 1의 테마는 계정 2에서 보이지 않는다.
    with pytest.raises(ThemeNotFound):
        manager.resolve_theme_ref("0", account_id=2)
    with pytest.raises(ThemeNotFound):
        manager.resolve_theme_ref("Alpha Research", account_id=2)


def test_get_theme_by_number(multi_manager) -> None:
    manager, settings = multi_manager
    beta = manager.get_theme("베타")
    assert manager.get_theme_by_number(beta.number).id == beta.id
    other_number = _add_other_account_theme(settings, theme_id=10, label="Bob Notes")
    # 계정 판정은 호출자 몫이다.
    assert manager.get_theme_by_number(other_number).account_id == 2
    for bad in (beta.number + 1, 0, True, str(beta.number)):
        with pytest.raises(ThemeNotFound):
            manager.get_theme_by_number(bad)


def test_list_account_themes_number_descending(multi_manager) -> None:
    manager, settings = multi_manager
    _add_other_account_theme(settings, theme_id=10, label="Bob Notes")
    listed = manager.list_account_themes(1)
    assert {t.id for t in listed} == {0, 1, 2}
    numbers = [t.number for t in listed]
    assert numbers == sorted(numbers, reverse=True)
    assert [t.id for t in manager.list_account_themes(2)] == [10]
    assert manager.list_account_themes(3) == []


def test_default_theme_for(multi_manager) -> None:
    manager, _ = multi_manager
    assert manager.default_theme_for(1).id == 0
    with pytest.raises(ThemeNotFound):
        manager.default_theme_for(2)


def test_parse_internal_id_or_number() -> None:
    assert ids.parse_internal_id_or_number("0") == (True, 0)
    assert ids.parse_internal_id_or_number(ids.INTERNAL_ID_LIMIT - 1) == (True, ids.INTERNAL_ID_LIMIT - 1)
    assert ids.parse_internal_id_or_number(str(ids.INTERNAL_ID_LIMIT)) == (False, ids.INTERNAL_ID_LIMIT)
    assert ids.parse_internal_id_or_number(ids.MIN_CURRENT_NUMBER) == (False, ids.MIN_CURRENT_NUMBER)
    for bad in ("", "01", "-1", " 1", "１", -1, True, ids.MAX_NUMBER + 1, str(ids.MAX_NUMBER + 1), 1.0):
        with pytest.raises(ValueError):
            ids.parse_internal_id_or_number(bad)
