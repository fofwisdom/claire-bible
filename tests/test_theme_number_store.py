"""테마 번호 저장·조회 (설계 §4.2, §6.3, §7.2, §7.3, O4, O5).

모든 테스트는 tmp_path만 쓰고 호스트 셸의 ``CLAIRE_*`` 환경변수와 저장소 ``.env``를 끊는다(§18.2-6).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from claire.core import ids
from claire.core.config import Settings
from claire.store import db as dbm
from claire.store.knowledge_base import (
    THEME_FQDN_RETIRED_MESSAGE,
    KnowledgeBaseInfo,
    KnowledgeBaseManager,
    KnowledgeBaseNotFound,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.startswith("CLAIRE_"):
            monkeypatch.delenv(key, raising=False)


def _settings(tmp_path: Path, **extra: object) -> Settings:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return Settings(
        _env_file=None,
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_PROVIDER="mock",
        **extra,
    )


def _db_row(settings: Settings, kb_id: int) -> dict | None:
    conn = dbm.connect(settings.db_file)
    try:
        row = conn.execute("SELECT * FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def _add_other_account_theme(settings: Settings, *, kb_id: int, label: str) -> int:
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
            "INSERT INTO knowledge_bases (id, seq, label, is_personal, created_at, updated_at, account_id) "
            "VALUES (?, ?, ?, 1, ?, ?, 2)",
            (kb_id, kb_id, label, now, now),
        )
        dbm.backfill_theme_numbers(conn)
        conn.commit()
        return conn.execute("SELECT number FROM knowledge_bases WHERE id=?", (kb_id,)).fetchone()[0]
    finally:
        conn.close()


# --- KnowledgeBaseInfo 직렬화 (O4) ---


def test_to_dict_number_is_decimal_string_or_none() -> None:
    big = (1 << 62) + 12345
    assert KnowledgeBaseInfo(id=1, seq=1, label="x", number=big).to_dict()["number"] == str(big)
    assert KnowledgeBaseInfo(id=1, seq=1, label="x").to_dict()["number"] is None
    d = KnowledgeBaseInfo(id=1, seq=1, label="x", number=big).to_dict()
    assert d["visibility_epoch"] == 1
    assert d["embed_provider"] == "" and d["embed_model"] == ""


def test_from_dict_accepts_string_and_int_numbers() -> None:
    big = (1 << 60) + 7
    assert KnowledgeBaseInfo.from_legacy_registry_entry({"id": 1, "label": "x", "number": str(big)}).number == big
    assert KnowledgeBaseInfo.from_legacy_registry_entry({"id": 1, "label": "x", "number": big}).number == big
    assert KnowledgeBaseInfo.from_legacy_registry_entry({"id": 1, "label": "x"}).number is None
    assert KnowledgeBaseInfo.from_legacy_registry_entry({"id": 1, "label": "x", "number": None}).number is None
    roundtrip = KnowledgeBaseInfo.from_legacy_registry_entry(KnowledgeBaseInfo(id=1, seq=1, label="x", number=big).to_dict())
    assert roundtrip.number == big
    for bad in ("0123", "-5", "abc", 0, True, 1.5):
        with pytest.raises(ValueError):
            KnowledgeBaseInfo.from_legacy_registry_entry({"id": 1, "label": "x", "number": bad})


# --- 새 테마 번호 (§7.2) ---


def test_define_theme_assigns_number_from_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    before_ms = time.time_ns() // 1_000_000
    theme = manager.define_kb("번호 테마", knowledge_type="real")
    after_ms = time.time_ns() // 1_000_000

    assert theme.number is not None and theme.number >= ids.MIN_CURRENT_NUMBER
    assert before_ms <= theme.number >> 16 <= after_ms
    assert _db_row(settings, theme.id)["number"] == theme.number
    assert _db_row(settings, theme.id)["visibility_epoch"] == 1
    # themes.json is retired (v27): the manager writes no copy.
    assert not (settings.data_dir / "themes.json").exists()
    # 다시 읽어도 DB 번호 그대로다.
    fresh = KnowledgeBaseManager(base_settings=settings)
    assert fresh.get_kb(theme.id).number == theme.number
    assert fresh.get_kb_by_number(theme.number).id == theme.id


def test_define_theme_retries_same_millisecond_collision(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    fixed_ms = 1_893_456_000_000
    manager._number_clock = lambda: fixed_ms
    first = manager.define_kb("첫째", knowledge_type="real")
    second = manager.define_kb("둘째", knowledge_type="real")

    conn = dbm.connect(settings.db_file)
    try:
        salt = dbm.read_id_salt(conn)
    finally:
        conn.close()
    assert first.number == ids.timestamp_id("knowledge_bases", salt=salt, unix_ms=fixed_ms, seq=0)
    assert second.number == ids.timestamp_id("knowledge_bases", salt=salt, unix_ms=fixed_ms, seq=1)


def test_define_theme_fails_after_max_collisions(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    manager._number_clock = lambda: 1_893_456_000_000
    for i in range(ids.MAX_INSERT_ATTEMPTS):
        manager.define_kb(f"테마 {i}", knowledge_type="real")
    with pytest.raises(ids.TimestampIdExhausted):
        manager.define_kb("넘침", knowledge_type="real")
    labels = {r["label"] for r in _rows(settings)}
    assert "넘침" not in labels


def _rows(settings: Settings) -> list[dict]:
    conn = dbm.connect(settings.db_file)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM knowledge_bases ORDER BY id")]
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
                "default_kb_id": 0,
                "themes": [
                    {"id": 0, "seq": 0, "label": "기본", "is_personal": True},
                    {"id": 1, "seq": 1, "label": "레지스트리만", "created_at": 0},
                ],
            }
        ),
        encoding="utf-8",
    )
    manager = KnowledgeBaseManager(base_settings=settings)
    theme = manager.get_kb(1)
    assert theme.number is not None and theme.number >= ids.MIN_CURRENT_NUMBER
    assert _db_row(settings, 1)["number"] == theme.number


# --- FQDN 폐지 (O5) ---


def test_define_and_update_reject_fqdn(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    with pytest.raises(ValueError, match="theme FQDN is retired"):
        manager.define_kb("도메인 테마", fqdn="ai.example.com", knowledge_type="real")
    assert [r["id"] for r in _rows(settings)] == [0]

    theme = manager.define_kb("일반 테마", fqdn="", knowledge_type="real")
    with pytest.raises(ValueError) as exc:
        manager.update_kb(theme.id, label="바뀌면 안 됨", fqdn="ai.example.com")
    assert str(exc.value) == THEME_FQDN_RETIRED_MESSAGE
    # 거부된 호출은 다른 필드도 바꾸지 않는다.
    assert manager.get_kb(theme.id).label == "일반 테마"
    assert _db_row(settings, theme.id)["label"] == "일반 테마"


def test_update_with_empty_fqdn_keeps_legacy_column(tmp_path: Path) -> None:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    theme = manager.define_kb("레거시", knowledge_type="real")
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET fqdn='old.example.com' WHERE id=?", (theme.id,))
        conn.commit()
    finally:
        conn.close()
    updated = manager.update_kb(theme.id, description="설명", fqdn="")
    assert updated.description == "설명"
    assert _db_row(settings, theme.id)["fqdn"] == "old.example.com"


# --- DB 기반 기본 지식베이스 일관성 ---


def test_theme_zero_is_read_from_db(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    KnowledgeBaseManager(base_settings=settings)  # DB 초기화
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("UPDATE knowledge_bases SET label='DB 레이블', visibility_epoch=3 WHERE id=0")
        conn.commit()
    finally:
        conn.close()
    row = _db_row(settings, 0)

    manager = KnowledgeBaseManager(base_settings=settings)
    t0 = manager.get_kb(0)
    assert t0.label == "DB 레이블"
    assert t0.number == row["number"] >= ids.MIN_CURRENT_NUMBER
    assert t0.visibility_epoch == 3
    assert t0.account_id == 1
    assert t0.is_personal is True
    assert not (settings.data_dir / "themes.json").exists()
    [(active, _)] = manager.active_kb_settings(settings)
    assert active.number == row["number"]


def test_kb_zero_uses_db_presentation_flag_and_ignores_retired_env(tmp_path: Path) -> None:
    settings = _settings(tmp_path, CLAIRE_PRESENTATION_ENABLED=True, CLAIRE_SORCERER="ignored_env")
    manager = KnowledgeBaseManager(base_settings=settings)
    t0 = manager.get_kb(0)
    assert t0.presentation_enabled is False
    assert _db_row(settings, 0)["presentation_enabled"] == 0
    assert t0.sorcerer == ""
    assert t0.db_path == str(settings.db_path)
    created = manager.define_kb("새 테마", knowledge_type="real")
    assert created.id == 1


def test_recreates_missing_theme_zero_in_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    KnowledgeBaseManager(base_settings=settings)
    conn = dbm.connect(settings.db_file)
    try:
        conn.execute("DELETE FROM knowledge_bases WHERE id=0")
        conn.commit()
    finally:
        conn.close()
    # 생성자의 init_db가 행을 먼저 되살리지 않게 막고, reload 경로가 행을 만드는지 본다.
    monkeypatch.setattr(KnowledgeBaseManager, "_init_db_file", lambda self: None)
    manager = KnowledgeBaseManager(base_settings=settings)
    row = _db_row(settings, 0)
    assert row is not None and row["is_personal"] == 1 and row["account_id"] == 1
    assert row["number"] is not None and row["number"] >= ids.MIN_CURRENT_NUMBER
    assert manager.get_kb(0).number == row["number"]


def test_readonly_without_db_uses_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = KnowledgeBaseManager(base_settings=settings, writable=False)
    t0 = manager.get_kb(0)
    assert t0.id == 0 and t0.number is None
    assert not Path(settings.db_file).exists()


# --- 조회 API (§6.3, §7.3) ---


@pytest.fixture
def multi_manager(tmp_path: Path) -> tuple[KnowledgeBaseManager, Settings]:
    settings = _settings(tmp_path, multi=True)
    manager = KnowledgeBaseManager(base_settings=settings)
    manager.define_kb("Alpha Research", knowledge_type="real")
    manager.define_kb("베타", knowledge_type="real")
    return manager, settings


def test_get_theme_by_number(multi_manager) -> None:
    manager, settings = multi_manager
    beta = manager.get_kb("베타")
    assert manager.get_kb_by_number(beta.number).id == beta.id
    other_number = _add_other_account_theme(settings, kb_id=10, label="Bob Notes")
    # 계정 판정은 호출자 몫이다.
    assert manager.get_kb_by_number(other_number).account_id == 2
    for bad in (beta.number + 1, 0, True, str(beta.number)):
        with pytest.raises(KnowledgeBaseNotFound):
            manager.get_kb_by_number(bad)


def test_list_account_themes_number_descending(multi_manager) -> None:
    manager, settings = multi_manager
    _add_other_account_theme(settings, kb_id=10, label="Bob Notes")
    # list_account_kbs: the knowledge bases that the account owns (its personal one). The selector also has the shared ones.
    assert {t.id for t in manager.list_account_kbs(1)} == {0}
    listed = manager.list_selectable_kbs(1)
    assert {t.id for t in listed} >= {0, 1, 2}
    # The personal knowledge base is first; the shared ones follow, newest first.
    assert listed[0].id == 0
    numbers = [t.number for t in listed[1:]]
    assert numbers == sorted(numbers, reverse=True)
    assert [t.id for t in manager.list_account_kbs(2)] == [10]
    assert manager.list_account_kbs(3) == []


def test_default_theme_for(multi_manager) -> None:
    manager, _ = multi_manager
    assert manager.personal_kb_for(1).id == 0
    with pytest.raises(KnowledgeBaseNotFound):
        manager.personal_kb_for(2)


def test_parse_internal_id_or_number() -> None:
    assert ids.parse_internal_id_or_number("0") == (True, 0)
    assert ids.parse_internal_id_or_number(ids.INTERNAL_ID_LIMIT - 1) == (True, ids.INTERNAL_ID_LIMIT - 1)
    assert ids.parse_internal_id_or_number(str(ids.INTERNAL_ID_LIMIT)) == (False, ids.INTERNAL_ID_LIMIT)
    assert ids.parse_internal_id_or_number(ids.MIN_CURRENT_NUMBER) == (False, ids.MIN_CURRENT_NUMBER)
    for bad in ("", "01", "-1", " 1", "１", -1, True, ids.MAX_NUMBER + 1, str(ids.MAX_NUMBER + 1), 1.0):
        with pytest.raises(ValueError):
            ids.parse_internal_id_or_number(bad)


def test_get_kb_by_reference_takes_numbers_only(multi_manager) -> None:
    """The parameter ``kb`` (CT23): the external number only. No internal ID, no label, no empty value."""
    manager = multi_manager[0]
    t0 = manager.get_kb(0)
    assert manager.get_kb_by_reference(ids.format_public_id(t0.number)).id == 0
    for ref in (None, "", "0", "1", "Alpha Research", " ", True, "-1", "1e3"):
        with pytest.raises(KnowledgeBaseNotFound):
            manager.get_kb_by_reference(ref)
