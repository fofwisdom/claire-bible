"""일련번호 기반 물리 디렉터리 격리 및 레이블 인식 테마 매니저.

지식 관리자가 테마의 레이블이나 설명을 언제든지 자유롭게 수정하더라도 물리적 SQLite 파일이나 디렉터리가 영향받지 않도록, 디렉터리 경로는 순수 일련번호(0, 1, 2...)로 영구 고정 관리하고 이용자 및 시스템에는 레이블(Label)로 인식되도록 합니다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .. import ids
from ..config import ROOT, Settings, get_settings
from . import db as dbm
from . import themes as _themes_store

log = logging.getLogger("claire.theme")

# Retired by claire.db version 27. Only ``_retire_legacy_registry`` reads it, once.
LEGACY_REGISTRY_FILENAME = "themes.json"
# O5: 테마 FQDN 쓰기는 폐지됐다. 옛 호스트는 ``legacy_hosts``(v16)로 308 리다이렉트한다.
THEME_FQDN_RETIRED_MESSAGE = "theme FQDN is retired; legacy hosts redirect via legacy_hosts"

_DNS_NAME_RE = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_GA_ID_RE = re.compile(r"^(?:G|GTM)-[A-Z0-9]{4,20}$")


def validate_fqdn(candidate: str | None) -> str:
    """FQDN 문자열 정규화 및 RFC 1123 DNS 호스트명 검증.

    - 앞뒤 공백 제거 및 소문자 변환
    - http://, https:// 등의 스킴이나 경로(/...), 포트(:...)는 허용하지 않음
    - 유효하지 않은 경우 ValueError 발생
    - 빈 문자열 또는 None은 FQDN 미지정을 의미하며 빈 문자열("") 반환
    """
    if candidate is None:
        return ""
    cleaned = str(candidate).strip().lower()
    if not cleaned:
        return ""
    if "/" in cleaned or "\\" in cleaned or ":" in cleaned or " " in cleaned or "@" in cleaned:
        raise ValueError(f"유효하지 않은 FQDN 형식입니다: {candidate!r} (스킴, 포트, 경로는 포함할 수 없습니다)")
    if cleaned.endswith("."):
        raise ValueError("FQDN 끝에 마침표(.)를 포함할 수 없습니다.")
    if "." not in cleaned:
        raise ValueError(f"FQDN은 도메인과 TLD를 구분하는 마침표(.)를 포함해야 합니다: {candidate!r}")
    if not _DNS_NAME_RE.fullmatch(cleaned):
        raise ValueError(f"유효하지 않은 DNS 호스트명(FQDN)입니다: {candidate!r}")
    return cleaned


def validate_ga_measurement_id(candidate: str | None) -> str:
    """Google Analytics 4 측정 ID (예: G-XXXXXXXXXX, GTM-XXXXXXX) 정규화 및 검증."""
    if candidate is None:
        return ""
    cleaned = str(candidate).strip()
    if not cleaned:
        return ""
    if not _GA_ID_RE.fullmatch(cleaned):
        raise ValueError(f"유효하지 않은 Google Analytics 측정 ID 형식입니다: {candidate!r}")
    return cleaned


class KnowledgeBaseNotFound(KeyError):
    """테마를 찾지 못했거나 접근할 수 없다.

    비공개 테마의 존재를 드러내지 않도록 두 경우의 메시지를 구분하지 않는다. 기존 `except KeyError` 처리와 호환되도록 KeyError를 상속한다.
    """

    def __init__(self, theme_ref: Any) -> None:
        self.theme_ref = theme_ref
        super().__init__(f"테마를 찾을 수 없습니다: {theme_ref}")

    def __str__(self) -> str:
        return str(self.args[0])


@dataclass
class KnowledgeBaseInfo:
    id: int
    seq: int
    label: str
    description: str = ""
    icon: str = "📚"
    db_path: str = ""
    is_personal: bool = False
    is_public: bool = True
    is_collaborator_accessible: bool = True
    fqdn: str = ""
    ga_measurement_id: str = ""
    sorcerer: str = ""
    presentation_enabled: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    # 소유 계정(§13.1). v15 이전 항목과 기본값은 관리자(1)다. 새로 쓰는 themes.json 항목에는 항상 들어간다.
    # P1 싱글 모드에서는 관리자(1)다. P3 L3-e에서 명시 인자로 바꾼다.
    # None for a shared knowledge base: it has no owner (CT2).
    account_id: int | None = dbm.ADMIN_ACCOUNT_ID
    # v28 (shared knowledge bases, design Section 11.2). ``knowledge_type`` is None for a personal knowledge base (CT18).
    created_by_account_id: int | None = None
    knowledge_type: str | None = None
    join_policy: str = "approval"
    state: str = "active"
    # v16(§13.2). 외부 번호(항상 >= 2^56, §7.2). 내부 ``id``는 내부 키로 남는다(K9).
    number: int | None = None
    # 공개 상태 변경·문서 purge 때 올린다. 열람 capability URL을 즉시 무효화한다(§5.2).
    visibility_epoch: int = 1
    # 테마 고정 임베딩 공간(§10.4). 빈 값이면 아직 정하지 않음.
    embed_provider: str = ""
    embed_model: str = ""

    @property
    def knowledge_manager(self) -> str:
        return self.sorcerer

    @property
    def is_shared(self) -> bool:
        """A shared knowledge base: not personal and without an owner (design Section 11.2)."""
        return not self.is_personal and self.account_id is None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["knowledge_manager"] = self.sorcerer
        # O4·§7.1 c: 번호는 JS 정밀도를 넘으므로 JSON에서는 항상 10진 문자열이다.
        d["number"] = ids.format_public_id(self.number) if self.number is not None else None
        return d

    @classmethod
    def from_legacy_registry_entry(cls, data: dict[str, Any]) -> KnowledgeBaseInfo:
        """An entry of the retired ``themes.json``. The file has the names before version 27 (``is_default``)."""
        is_def = bool(data.get("is_default", False))
        tid = int(data.get("id", 0))
        default_collab = not is_def
        return cls(
            id=tid,
            seq=int(data.get("seq", data.get("id", 0))),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            icon=str(data.get("icon", "📁")),
            db_path=str(data.get("db_path", "")),
            is_personal=is_def,
            is_public=bool(data.get("is_public", data.get("public", True))),
            is_collaborator_accessible=bool(
                data.get(
                    "is_collaborator_accessible",
                    data.get("collaborator_accessible", data.get("collaborator", default_collab)),
                )
            ),
            fqdn=validate_fqdn(data.get("fqdn")),
            ga_measurement_id=validate_ga_measurement_id(
                data.get("ga_measurement_id", data.get("ga_id"))
            ),
            sorcerer=str(data.get("sorcerer", data.get("knowledge_manager", "")) or "").strip(),
            presentation_enabled=bool(
                data.get("presentation_enabled", data.get("is_presentation_enabled", False))
            ),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            # themes.json 항목에 account_id가 없으면 v15 이전의 관리자 테마다(§13.1).
            account_id=_owner_account_id(data, "themes.json"),
            number=_theme_number(data.get("number"), "themes.json", data.get("id")),
            visibility_epoch=_visibility_epoch(data.get("visibility_epoch"), "themes.json", data.get("id")),
            embed_provider=str(data.get("embed_provider") or "").strip(),
            embed_model=str(data.get("embed_model") or "").strip(),
        )


def embed_mismatch(theme: KnowledgeBaseInfo, embed_provider: str | None, embed_model: str | None) -> bool:
    """현재 임베딩 설정이 테마의 고정 임베딩 공간과 다른가(§10.4). 화면의 re-embed 필요 표시용.

    아직 고정되지 않은 테마는 첫 저장이 고정하므로 False다. 모델이 다르면 True. 프로바이더는 양쪽 모두 알려져 있고 다를 때만 True다.
    프로바이더 이름은 ``store.vectors.resolve_embed_identity``가 돌려주는 임베딩 백엔드 이름(codex·antigravity → gemini)으로 넘긴다.
    ``theme``은 DB에서 다시 읽은 값이어야 한다. 고정은 첫 임베딩 저장 때 DB에서 일어나므로 캐시된 ``KnowledgeBaseInfo``는 늦을 수 있다.
    """
    pinned_model = (theme.embed_model or "").strip()
    if not pinned_model:
        return False
    if (embed_model or "").strip() != pinned_model:
        return True
    pinned_provider = (theme.embed_provider or "").strip()
    current_provider = (embed_provider or "").strip()
    return bool(pinned_provider and current_provider and pinned_provider != current_provider)


def _theme_number(raw: Any, source: str, kb_id: Any) -> int | None:
    """번호는 10진 문자열·정수 모두 받는다(O4). 없으면 None(v16 이관 전). 형식이 틀리면 추측하지 않고 실패한다(C1)."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return ids.parse_public_id(raw.strip() if isinstance(raw, str) else raw)
    except ValueError as exc:
        raise ValueError(f"{source}: theme {kb_id!r} has invalid number {raw!r}") from exc


def _visibility_epoch(raw: Any, source: str, kb_id: Any) -> int:
    if raw is None:
        return 1
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"{source}: theme {kb_id!r} has invalid visibility_epoch {raw!r}")
    return raw


def _owner_account_id(data: dict[str, Any], source: str, *, allow_none: bool = False) -> int | None:
    """``account_id``가 없는 항목은 v15 이전 관리자 것(1)이다. 값이 비정수면 추측하지 않고 실패한다(C1). NULL은 공유 지식 베이스의 행에서만 받는다(``allow_none``)."""
    if "account_id" not in data:
        return dbm.ADMIN_ACCOUNT_ID
    raw = data["account_id"]
    if raw is None and allow_none:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{source}: theme {data.get('id')!r} has invalid account_id {raw!r}")
    return raw


def _row_to_theme(row: Any, default_db_path: str) -> KnowledgeBaseInfo:
    d = dict(row)
    tid = int(d.get("id", 0))
    is_def = bool(d.get("is_personal", tid == 0))
    db_p = str(d.get("db_path") or "").strip() or default_db_path
    return KnowledgeBaseInfo(
        id=tid,
        seq=int(d.get("seq", tid)),
        label=str(d.get("label", "")),
        description=str(d.get("description", "")),
        icon=str(d.get("icon", "📁")),
        db_path=db_p,
        is_personal=is_def,
        is_public=bool(d.get("is_public", 1)),
        is_collaborator_accessible=bool(d.get("is_collaborator_accessible", not is_def)),
        fqdn=validate_fqdn(d.get("fqdn")),
        ga_measurement_id=validate_ga_measurement_id(d.get("ga_measurement_id")),
        sorcerer=str(d.get("sorcerer") or "").strip(),
        presentation_enabled=bool(d.get("presentation_enabled", 0)),
        created_at=float(d.get("created_at", 0.0)),
        updated_at=float(d.get("updated_at", 0.0)),
        # 열이 없는 행은 v15 이전 DB(관리자 것)다. v15 DB의 NULL은 이관 결함이므로 거부한다.
        account_id=_owner_account_id(d, "knowledge_bases table", allow_none=True),
        created_by_account_id=d.get("created_by_account_id"),
        knowledge_type=d.get("knowledge_type"),
        join_policy=str(d.get("join_policy") or "approval"),
        state=str(d.get("state") or "active"),
        # 열이 없는 행은 v16 이전 DB(읽기 전용 점검)다.
        number=_theme_number(d.get("number"), "themes table", tid),
        visibility_epoch=_visibility_epoch(d.get("visibility_epoch"), "themes table", tid),
        embed_provider=str(d.get("embed_provider") or "").strip(),
        embed_model=str(d.get("embed_model") or "").strip(),
    )


class KnowledgeBaseManager:
    """테마 레지스트리 및 단일 데이터베이스 기반 테마 분할 관리자."""

    # 새 테마 번호의 밀리초 시계. None이면 현재 시각이다. 테스트가 같은 밀리초 충돌을 재현할 때만 바꾼다.
    _number_clock: Callable[[], int] | None = None

    def __init__(
        self,
        base_settings: Settings | None = None,
        *,
        writable: bool = True,
    ) -> None:
        self.settings = base_settings or get_settings()
        self.writable = writable
        self._themes: dict[int, KnowledgeBaseInfo] = {}
        self._fqdn_to_kb_id: dict[str, int] = {}
        self._next_seq: int = 1
        # Rows that are neither a personal nor a shared knowledge base (design Section 12.2 step 3): no route serves them; the admin page lists them.
        self._unavailable: dict[int, tuple[KnowledgeBaseInfo, str]] = {}
        if self.writable:
            self._init_db_file()
        self.reload()

    def _init_db_file(self) -> None:
        db_file = getattr(self.settings, "db_file", None)
        if db_file is not None:
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)
            conn = dbm.connect(db_file)
            try:
                dbm.init_db(conn)
            finally:
                conn.close()

    def _rebuild_fqdn_index(self) -> None:
        idx: dict[str, int] = {}
        for t in self._themes.values():
            if t.fqdn:
                idx[t.fqdn.lower()] = t.id
        self._fqdn_to_kb_id = idx

    def get_kb_by_fqdn(self, fqdn: str | None) -> KnowledgeBaseInfo | None:
        """FQDN 호스트명으로 등록된 테마를 검색. 미등록 시 None."""
        if not fqdn:
            return None
        self.reload()
        cleaned = str(fqdn).strip().lower()
        if ":" in cleaned:
            cleaned = cleaned.split(":", 1)[0]
        tid = self._fqdn_to_kb_id.get(cleaned)
        if tid is not None and tid in self._themes:
            return self._themes[tid]
        return None

    def get_registered_fqdns(self) -> set[str]:
        """현재 등록된 모든 테마의 FQDN 호스트명 집합 반환."""
        self.reload()
        return set(self._fqdn_to_kb_id.keys())

    def has_registered_fqdn(self, fqdn: str | None) -> bool:
        """해당 FQDN이 특정 테마의 서비스 도메인으로 등록되어 있는지 여부."""
        if not fqdn:
            return False
        self.reload()
        cleaned = str(fqdn).strip().lower()
        if ":" in cleaned:
            cleaned = cleaned.split(":", 1)[0]
        return cleaned in self._fqdn_to_kb_id

    def has_any_ga_enabled(self) -> bool:
        """등록된 테마 중 하나라도 GA4 측정 ID를 사용하는지 확인."""
        self.reload()
        return any(bool(t.ga_measurement_id) for t in self._themes.values())

    def reload(self) -> None:
        """Load the knowledge bases from the table ``knowledge_bases`` of ``claire.db``. The database is the only source.

        ``data/themes.json`` is retired (claire.db version 27, design Section 12.1 step 3): the first writable load inserts the entries that are only in the file, as the old code did, and then renames the file to ``themes.json.retired``. No code reads or writes it after that.
        """
        if not getattr(self.settings, "multi_theme", False):
            # 싱글 테마 모드: Theme 0은 claire.db 행에서 한 번 읽어 고정한다(§4.2, K8).
            if not self._themes or 0 not in self._themes:
                self._themes = {0: self._load_single_mode_theme0()}
            self._rebuild_fqdn_index()
            return

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        if not self.writable:
            if not Path(db_file).is_file():
                if not self._themes or 0 not in self._themes:
                    self._themes = {0: self._build_default_theme()}
                self._rebuild_fqdn_index()
                return
            conn = dbm.connect_existing(db_file, readonly=True)
            try:
                try:
                    rows = conn.execute("SELECT * FROM knowledge_bases ORDER BY id").fetchall()
                except (sqlite3.OperationalError, sqlite3.DatabaseError):
                    rows = []
                loaded, unavailable = self._split_by_kind(rows, str(db_file))
                if 0 not in loaded and 0 not in unavailable:
                    loaded[0] = self._build_default_theme()
                self._themes = loaded
                self._unavailable = unavailable
                self._next_seq = max((t.seq for t in [*loaded.values(), *(u[0] for u in unavailable.values())]), default=0) + 1
                self._rebuild_fqdn_index()
                return
            finally:
                conn.close()

        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        conn = dbm.connect(db_file)
        legacy_next_seq = 1
        try:
            dbm.init_db(conn)

            data_dir = getattr(self.settings, "data_dir", None) or Path(db_file).parent
            themes_dir = Path(data_dir) / "themes"
            if themes_dir.is_dir() and any((d / "claire.db").is_file() for d in themes_dir.iterdir() if d.is_dir()):
                try:
                    log.info("Unmigrated theme databases detected in %s, starting migration...", themes_dir)
                    report = dbm.migrate_multi_db_to_single_db(conn, data_dir)
                    log.info("Theme migration completed: %s", report)
                except Exception as e:
                    log.warning("Auto-migration of theme databases failed: %s", e)

            rows = conn.execute("SELECT * FROM knowledge_bases ORDER BY id").fetchall()
            existing_ids = {r["id"] for r in rows}
            legacy_next_seq = self._retire_legacy_registry(conn, existing_ids, db_file=str(db_file))

            if 0 not in existing_ids:
                self._insert_theme_row(conn, self._build_default_theme(), db_path=str(db_file))

            # Rows from the legacy paths have the old shape (an owner and not personal): convert them now (design Section 12.2).
            dbm._migrate_v28_convert_knowledge_bases(conn)
            # 레거시 경로(themes.json·다중 DB 가져오기)로 들어온 행에도 번호를 준다. DB가 번호의 원천이다.
            dbm.backfill_theme_numbers(conn)
            conn.commit()
            rows = conn.execute("SELECT * FROM knowledge_bases ORDER BY id").fetchall()

            loaded, unavailable = self._split_by_kind(rows, str(db_file))
            self._themes = loaded
            self._unavailable = unavailable
            max_seq = max((t.seq for t in [*loaded.values(), *(u[0] for u in unavailable.values())]), default=0)
            self._next_seq = max(max_seq + 1, legacy_next_seq)
            self._rebuild_fqdn_index()
        finally:
            conn.close()

    @staticmethod
    def _split_by_kind(rows: Any, default_db_path: str) -> tuple[dict[int, KnowledgeBaseInfo], dict[int, tuple[KnowledgeBaseInfo, str]]]:
        """Rows of the two kinds (personal, shared) and the rows that break the kinds (unavailable, with the reason)."""
        from . import kb_collab

        loaded: dict[int, KnowledgeBaseInfo] = {}
        unavailable: dict[int, tuple[KnowledgeBaseInfo, str]] = {}
        for r in rows:
            t = _row_to_theme(r, default_db_path=default_db_path)
            reason = kb_collab.kind_violation_reason(t.is_personal, t.account_id, t.is_public, t.knowledge_type)
            if reason:
                unavailable[t.id] = (t, reason)
            else:
                loaded[t.id] = t
        return loaded, unavailable

    def unavailable_kbs(self) -> list[tuple[KnowledgeBaseInfo, str]]:
        """The rows that no route serves, with the reason (for the admin page)."""
        self.reload()
        return [self._unavailable[k] for k in sorted(self._unavailable)]

    def _retire_legacy_registry(self, conn: sqlite3.Connection, existing_ids: set[int], *, db_file: str) -> int:
        """Insert the entries that are only in ``data/themes.json`` (the admin owns an entry without ``account_id``), then rename the file to ``themes.json.retired``. Return the ``next_seq`` of the file (1 when there is no file).

        A file that cannot be read stays where it is, and the start continues without it (operator rule); the log gets a warning.
        """
        path = self.settings.data_dir / LEGACY_REGISTRY_FILENAME
        if not path.is_file():
            return 1
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("registry root must be an object")
            entries = raw.get("themes", [])
            if isinstance(entries, dict):
                entries = list(entries.values())
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                raise ValueError("themes must be a list of objects")
            next_seq = int(raw.get("next_seq", 1))
        except Exception as exc:  # noqa: BLE001
            log.warning("legacy registry %s cannot be read; it stays as it is: %s", path, exc)
            return 1
        for item in entries:
            t_info = KnowledgeBaseInfo.from_legacy_registry_entry(item)
            if t_info.id not in existing_ids:
                self._insert_theme_row(conn, t_info, db_path=db_file)
                existing_ids.add(t_info.id)
        conn.commit()
        target = path.with_name(LEGACY_REGISTRY_FILENAME + ".retired")
        if target.exists():
            target = path.with_name(f"{LEGACY_REGISTRY_FILENAME}.retired.{int(time.time())}")
        os.replace(path, target)
        log.info("legacy registry %s is retired as %s", path, target.name)
        return next_seq

    @staticmethod
    def _insert_theme_row(conn: sqlite3.Connection, theme: KnowledgeBaseInfo, *, db_path: str) -> None:
        """레지스트리에만 있던 테마(또는 Theme 0)를 themes 행으로 넣는다. 이미 있으면 그대로 둔다.

        항목에 번호가 있고 아직 쓰이지 않았으면 그 번호를 지킨다. 없거나 이미 쓰였으면 NULL로 두고 ``backfill_theme_numbers``가 채운다.
        """
        number = theme.number
        if number is not None and conn.execute(
            "SELECT 1 FROM knowledge_bases WHERE number=?", (number,)
        ).fetchone() is not None:
            number = None
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_bases (
                id, seq, label, description, icon, db_path,
                is_personal, is_public, is_collaborator_accessible,
                fqdn, ga_measurement_id, sorcerer,
                presentation_enabled, created_at, updated_at, account_id,
                number, visibility_epoch, embed_provider, embed_model
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                theme.id,
                theme.seq,
                theme.label,
                theme.description,
                theme.icon,
                db_path,
                int(theme.is_personal),
                int(theme.is_public),
                int(theme.is_collaborator_accessible),
                theme.fqdn,
                theme.ga_measurement_id,
                theme.sorcerer,
                int(theme.presentation_enabled),
                theme.created_at,
                theme.updated_at,
                theme.account_id,
                number,
                theme.visibility_epoch,
                theme.embed_provider or None,
                theme.embed_model or None,
            ),
        )

    def _load_single_mode_theme0(self) -> KnowledgeBaseInfo:
        """싱글 테마 모드의 Theme 0을 claire.db themes 행에서 읽는다(§4.2). 행이 없으면 만들어 기록한다.

        설정에서 오는 값(``presentation_enabled``, 지식 관리자, ``db_path``)은 지금까지처럼 설정을 따른다.
        읽기 전용(``writable=False``)이고 DB가 없거나 아직 themes 테이블이 없으면 기록하지 않고 기본값을 쓴다.
        """
        default = self._build_default_theme()
        db_file = getattr(self.settings, "db_file", None)
        if db_file is None:
            return default
        path = Path(db_file)
        row = None
        if self.writable:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = dbm.connect(path)
            try:
                # 생성자가 이미 init_db를 돌렸다. 그 뒤 파일이 바뀐 경우에만 다시 맞춘다.
                if dbm.stored_schema_version(conn) != dbm.SCHEMA_VERSION:
                    dbm.init_db(conn)
                row = conn.execute("SELECT * FROM knowledge_bases WHERE id=0").fetchone()
                if row is None or row["number"] is None:
                    if row is None:
                        self._insert_theme_row(conn, default, db_path=str(path))
                    dbm.backfill_theme_numbers(conn)
                    conn.commit()
                    row = conn.execute("SELECT * FROM knowledge_bases WHERE id=0").fetchone()
            finally:
                conn.close()
        elif path.is_file():
            conn = dbm.connect_existing(path, readonly=True)
            try:
                try:
                    row = conn.execute("SELECT * FROM knowledge_bases WHERE id=0").fetchone()
                except (sqlite3.OperationalError, sqlite3.DatabaseError):
                    row = None
            finally:
                conn.close()
        if row is None:
            return default
        theme = _row_to_theme(row, default_db_path=default.db_path)
        theme.db_path = default.db_path
        theme.sorcerer = default.sorcerer
        theme.presentation_enabled = default.presentation_enabled
        return theme

    def _build_default_theme(self) -> KnowledgeBaseInfo:
        now = time.time()
        db_path = getattr(self.settings, "db_path", None)
        if db_path is None:
            db_file = getattr(self.settings, "db_file", None)
            db_path = str(db_file) if db_file is not None else "data/claire.db"

        default_sorcerer = getattr(
            self.settings,
            "effective_sorcerer",
            getattr(self.settings, "sorcerer", "owner"),
        )
        return KnowledgeBaseInfo(
            id=0,
            seq=0,
            label="기본 지식베이스",
            description="일반 수집 자료 및 기본 지식",
            icon="📚",
            db_path=str(db_path),
            is_personal=True,
            is_public=True,
            is_collaborator_accessible=False,
            sorcerer=str(default_sorcerer).strip() or "owner",
            presentation_enabled=bool(getattr(self.settings, "presentation_enabled", False)),
            created_at=now,
            updated_at=now,
            account_id=dbm.ADMIN_ACCOUNT_ID,
        )

    def list_kbs(
        self,
        *,
        include_private: bool = True,
        collaborator: bool = False,
        host: str | None = None,
    ) -> list[KnowledgeBaseInfo]:
        """등록된 테마 목록을 일련번호 순서로 반환.

        include_private=True 인 경우 모든 테마 반환. collaborator=True 인 경우 공개(is_public=True) 또는 Collaborator 공개(is_collaborator_accessible=True) 테마 반환. include_private=False 및 collaborator=False 인 경우 (익명 사용자):
          - host가 특정 테마의 전용 FQDN인 경우: 해당 FQDN 테마만 반환 (is_public=True).
          - host가 기본 도메인(또는 미지정)인 경우: FQDN이 설정되지 않은 공개 테마만 반환 (기본 지식베이스 및 일반 공개 테마).
        """
        self.reload()
        themes = sorted(self._themes.values(), key=lambda t: t.id)
        if include_private:
            return themes
        if collaborator:
            return [t for t in themes if t.is_public or t.is_collaborator_accessible]

        cleaned_host = ""
        if host:
            cleaned_host = str(host).strip().lower()
            if ":" in cleaned_host:
                cleaned_host = cleaned_host.split(":", 1)[0].strip()

        if cleaned_host and cleaned_host in self._fqdn_to_kb_id:
            dedicated_tid = self._fqdn_to_kb_id[cleaned_host]
            if dedicated_tid is not None:
                return [t for t in themes if t.id == dedicated_tid and t.is_public]

        return [t for t in themes if t.is_public and (not t.fqdn or t.is_personal)]

    def get_kb(
        self,
        theme_ref: int | str | None,
        *,
        include_private: bool = True,
        collaborator: bool = False,
        label_match: Literal["exact", "partial"] = "exact",
    ) -> KnowledgeBaseInfo:
        """일련번호(int/str) 또는 레이블로 테마를 검색. 찾지 못하면 다른 테마로 대신하지 않는다(PRINCIPLES C1).

        - theme_ref 가 None 이거나 빈 문자열이면 기본 테마. 참조를 생략한 경우의 기본값이다.
        - 일련번호(숫자) 일치 우선.
        - 레이블(대소문자 무시 정확 일치) 차선.
        - label_match="partial" 이면 마지막으로 레이블 부분 일치(처음 걸린 테마)를 시도한다. 사람이 직접 입력하는 CLI `--theme`과 텔레그램 `/theme`만 쓴다.
        - 찾지 못했거나 접근할 수 없으면 KnowledgeBaseNotFound. include_private=False 이면 비공개 테마, collaborator=True 이면 협업 비허용 비공개 테마가 접근 불가다. 두 경우의 메시지는 같다.
        """
        if label_match not in ("exact", "partial"):
            raise ValueError(f"알 수 없는 label_match 값입니다: {label_match!r}")
        self.reload()
        found: KnowledgeBaseInfo | None = None
        if theme_ref is None or str(theme_ref).strip() == "":
            try:
                found = self.personal_kb_for(dbm.ADMIN_ACCOUNT_ID)
            except KnowledgeBaseNotFound:
                found = next((t for t in self._themes.values() if t.is_personal), self._themes.get(0, self._build_default_theme()))
        else:
            ref_str = str(theme_ref).strip()
            # 1. 정수 일련번호 매칭
            if ref_str.isdigit():
                tid = int(ref_str)
                if tid in self._themes:
                    found = self._themes[tid]

            # 2. 레이블 정확 일치 (대소문자 무시)
            if found is None:
                ref_lower = ref_str.lower()
                for t in self._themes.values():
                    if t.label.lower() == ref_lower:
                        found = t
                        break

            # 3. 레이블 부분 일치 (사람이 입력하는 진입점에서만 요청)
            if found is None and label_match == "partial":
                ref_lower = ref_str.lower()
                for t in self._themes.values():
                    if ref_lower in t.label.lower():
                        found = t
                        break

        if found is None:
            raise KnowledgeBaseNotFound(theme_ref)
        if not include_private:
            accessible = (found.is_public or found.is_collaborator_accessible) if collaborator else found.is_public
            if not accessible:
                raise KnowledgeBaseNotFound(theme_ref)
        return found

    def get_kb_by_number(self, number: int) -> KnowledgeBaseInfo:
        """외부 번호로 찾는다. 없으면 ``KnowledgeBaseNotFound``. 가시성 판정은 호출자 몫이다(§6.3)."""
        if isinstance(number, bool) or not isinstance(number, int):
            raise KnowledgeBaseNotFound(number)
        self.reload()
        for theme in self._themes.values():
            if theme.number == number:
                return theme
        raise KnowledgeBaseNotFound(number)

    def get_kb_by_reference(self, raw: str | int | None) -> KnowledgeBaseInfo:
        """The parameter ``kb``: the external number only (CT23). No internal ID, no label, no default. Else ``KnowledgeBaseNotFound``. The caller checks the access."""
        if raw is None or isinstance(raw, bool):
            raise KnowledgeBaseNotFound(raw)
        text = str(raw).strip()
        if not text or not text.isascii() or not text.isdigit():
            raise KnowledgeBaseNotFound(raw)
        try:
            number = ids.parse_public_id(text)
        except ValueError:
            raise KnowledgeBaseNotFound(raw) from None
        return self.get_kb_by_number(number)

    def list_account_kbs(self, account_id: int) -> list[KnowledgeBaseInfo]:
        """그 계정의 테마를 번호 내림차순으로. 가시성 필터는 호출자 몫이다(§7.6)."""
        self.reload()
        owned = [t for t in self._themes.values() if t.account_id == account_id]
        # 번호가 없는 행(읽기 전용으로 연 v16 이전 DB)은 맨 뒤에 내부 ID 내림차순으로 둔다.
        return sorted(
            owned,
            key=lambda t: (t.number is not None, t.number or 0, t.id),
            reverse=True,
        )

    def list_selectable_kbs(self, account_id: int) -> list[KnowledgeBaseInfo]:
        """The knowledge bases that the selector of an account shows: its personal knowledge base, then all shared knowledge bases (they are public, CT5), newest first."""
        self.reload()
        shared = sorted(
            (t for t in self._themes.values() if t.is_shared),
            key=lambda t: (t.number is not None, t.number or 0, t.id),
            reverse=True,
        )
        return [*self.list_account_kbs(account_id), *shared]

    def personal_kb_for(self, account_id: int) -> KnowledgeBaseInfo:
        """그 계정의 ``is_personal=1`` 테마. 없으면 ``KnowledgeBaseNotFound``(C1)."""
        self.reload()
        for theme in sorted(self._themes.values(), key=lambda t: t.id):
            if theme.account_id == account_id and theme.is_personal:
                return theme
        raise KnowledgeBaseNotFound(f"default theme of account {account_id}")

    def define_kb(
        self,
        label: str,
        *,
        description: str = "",
        icon: str = "📁",
        is_personal: bool = False,
        is_public: bool = True,
        is_collaborator_accessible: bool = True,
        fqdn: str = "",
        ga_measurement_id: str = "",
        sorcerer: str = "",
        knowledge_manager: str = "",
        presentation_enabled: bool = False,
        account_id: int = dbm.ADMIN_ACCOUNT_ID,
        knowledge_type: str | None = None,
    ) -> KnowledgeBaseInfo:
        """Make a knowledge base.

        - ``is_personal=True``: the one personal knowledge base of ``account_id`` (CT1). It has no knowledge type.
        - Else a shared knowledge base (CT2, CT3): it has no owner, is public, and needs ``knowledge_type`` (``real`` or ``fictional``, CT12; there is no default). ``account_id`` is the founder: it becomes ``created_by_account_id`` and the first moderator (CT8). Labels need not be unique.
        """
        if is_personal:
            if knowledge_type is not None:
                raise ValueError("a personal knowledge base has no knowledge type")
        else:
            if knowledge_type not in ("real", "fictional"):
                raise ValueError("knowledge_type_required")
            if not is_public:
                raise ValueError("a shared knowledge base is always public")
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

        cleaned_label = str(label or "").strip()
        if not cleaned_label:
            raise ValueError("테마 레이블(이름)을 입력해야 합니다.")

        # O5: 테마 FQDN 입력은 폐지됐다. 매개변수는 호출부 정리 뒤 지운다.
        if str(fqdn or "").strip():
            raise ValueError(THEME_FQDN_RETIRED_MESSAGE)
        norm_fqdn = ""
        norm_ga = validate_ga_measurement_id(ga_measurement_id)

        self.reload()

        # 동일 계정 내 동일 레이블 중복 방지 (account_id, label)
        seq = self._next_seq
        self._next_seq += 1

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        db_path = str(db_file)

        now = time.time()
        mgr = str(sorcerer or knowledge_manager or "").strip()
        theme = KnowledgeBaseInfo(
            id=seq,
            seq=seq,
            label=cleaned_label,
            description=str(description or "").strip(),
            icon=str(icon or "📁").strip(),
            db_path=db_path,
            is_personal=bool(is_personal),
            is_public=bool(is_public),
            is_collaborator_accessible=False if is_personal else bool(is_collaborator_accessible),
            fqdn=norm_fqdn,
            ga_measurement_id=norm_ga,
            sorcerer=mgr,
            presentation_enabled=bool(presentation_enabled),
            created_at=now,
            updated_at=now,
            account_id=account_id if is_personal else None,
            created_by_account_id=None if is_personal else account_id,
            knowledge_type=None if is_personal else knowledge_type,
        )

        conn = dbm.connect(db_file)
        try:
            salt = dbm.read_id_salt(conn)

            def _insert(number: int) -> None:
                conn.execute(
                    """
                    INSERT INTO knowledge_bases (
                        id, seq, label, description, icon, db_path,
                        is_personal, is_public, is_collaborator_accessible,
                        fqdn, ga_measurement_id, sorcerer,
                        presentation_enabled, created_at, updated_at, account_id,
                        number, visibility_epoch, created_by_account_id, knowledge_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        theme.id,
                        theme.seq,
                        theme.label,
                        theme.description,
                        theme.icon,
                        theme.db_path,
                        int(theme.is_personal),
                        int(theme.is_public),
                        int(theme.is_collaborator_accessible),
                        theme.fqdn,
                        theme.ga_measurement_id,
                        theme.sorcerer,
                        int(theme.presentation_enabled),
                        theme.created_at,
                        theme.updated_at,
                        theme.account_id,
                        number,
                        theme.visibility_epoch,
                        theme.created_by_account_id,
                        theme.knowledge_type,
                    ),
                )

            # DB가 번호의 원천이다. 같은 밀리초 충돌은 UNIQUE 제약으로 잡고 seq를 올린다(§7.2).
            theme.number = ids.insert_with_timestamp_id(
                conn,
                table="knowledge_bases",
                column="number",
                salt=salt,
                insert=_insert,
                now_ms=self._number_clock,
                # Section 5.4: a knowledge base number is never a theme number.
                is_taken=lambda n: _themes_store.number_is_theme_number(conn, n),
            )
            if not is_personal:
                # CT8: the founder is the first moderator.
                from . import kb_collab

                kb_collab.add_membership(conn, kb_id=theme.id, account_id=account_id, role="moderator", state="active", now=now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        self._themes[seq] = theme
        self._rebuild_fqdn_index()
        log.info(
            "새 테마 #%d [%s] (공개: %s, 협력자: %s, FQDN: %s) 정의 완료 (경로: %s)",
            seq,
            cleaned_label,
            theme.is_public,
            theme.is_collaborator_accessible,
            norm_fqdn or "(없음)",
            db_path,
        )
        return theme

    def update_kb(
        self,
        kb_id: int | str,
        *,
        label: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        is_public: bool | None = None,
        is_collaborator_accessible: bool | None = None,
        fqdn: str | None = None,
        ga_measurement_id: str | None = None,
        sorcerer: str | None = None,
        knowledge_manager: str | None = None,
        presentation_enabled: bool | None = None,
    ) -> KnowledgeBaseInfo:
        """지식 관리자: 테마 레이블, 설명, 아이콘, 공개 여부, 협력자 공개 여부, 기본 적용 초점, GA ID, 지식 관리자 수정 (물리 폴더 경로는 절대 변경되지 않음).

        ``fqdn``은 폐지됐다(O5). 비어 있지 않으면 ``ValueError``, 빈 값은 아무것도 바꾸지 않는다. 기존 ``knowledge_bases.fqdn`` 열은 ``legacy_hosts``의 원본이므로 지우지 않는다(A3).
        """
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")
        if fqdn is not None and str(fqdn).strip():
            raise ValueError(THEME_FQDN_RETIRED_MESSAGE)

        self.reload()
        try:
            tid = int(kb_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            # 문자열 레이블/이름으로 지정된 경우 get_theme로 검색
            target = self.get_kb(kb_id)
            tid = target.id

        theme = self._themes[tid]

        if label is not None:
            cleaned_label = str(label).strip()
            if not cleaned_label:
                raise ValueError("테마 레이블은 비어 있을 수 없습니다.")
            # 동일 계정의 다른 테마와 레이블 중복 검사
            theme.label = cleaned_label

        if description is not None:
            theme.description = str(description).strip()

        if icon is not None:
            theme.icon = str(icon).strip() or "📁"

        became_private = False
        if is_public is not None:
            new_public = bool(is_public)
            if theme.is_shared and not new_public:
                raise ValueError("a shared knowledge base is always public")
            if new_public != theme.is_public:
                became_private = not new_public
                theme.is_public = new_public
                # §5.4: 테마가 비공개로 바뀌거나 공개 상태가 바뀌면 visibility_epoch를 올리고 CDN에서 무효화
                theme.visibility_epoch += 1
                try:
                    from .media import trigger_cdn_invalidation
                    trigger_cdn_invalidation([f"theme_{tid}"])
                except Exception:
                    pass

        if is_collaborator_accessible is not None:
            if theme.is_personal:
                theme.is_collaborator_accessible = False
            else:
                theme.is_collaborator_accessible = bool(is_collaborator_accessible)

        if ga_measurement_id is not None:
            theme.ga_measurement_id = validate_ga_measurement_id(ga_measurement_id)

        if knowledge_manager is not None and sorcerer is None:
            sorcerer = knowledge_manager
        if sorcerer is not None:
            theme.sorcerer = str(sorcerer).strip()

        if presentation_enabled is not None:
            theme.presentation_enabled = bool(presentation_enabled)

        theme.updated_at = time.time()

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        conn = dbm.connect(db_file)
        try:
            conn.execute(
                """
                UPDATE knowledge_bases SET
                    label = ?,
                    description = ?,
                    icon = ?,
                    is_public = ?,
                    is_collaborator_accessible = ?,
                    fqdn = ?,
                    ga_measurement_id = ?,
                    sorcerer = ?,
                    presentation_enabled = ?,
                    visibility_epoch = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    theme.label,
                    theme.description,
                    theme.icon,
                    int(theme.is_public),
                    int(theme.is_collaborator_accessible),
                    theme.fqdn,
                    theme.ga_measurement_id,
                    theme.sorcerer,
                    int(theme.presentation_enabled),
                    theme.visibility_epoch,
                    theme.updated_at,
                    tid,
                ),
            )
            if became_private:
                # Fedify 사이드카 설계 §9.4: 비공개 전환은 발행된 객체마다 Delete 작업을 만든다. visibility_epoch 증가와 같은 트랜잭션이다.
                from . import federation as federation_store

                federation_store.retract_objects(conn, kb_id=tid, now=theme.updated_at)
            conn.commit()
        finally:
            conn.close()

        self._rebuild_fqdn_index()
        log.info("테마 #%d 메타데이터 수정 완료 (레이블: %s, FQDN: %s)", tid, theme.label, theme.fqdn or "(없음)")
        return theme

    def delete_kb(self, kb_id: int | str, *, purge: bool = False) -> KnowledgeBaseInfo:
        """지식 관리자: 테마 삭제 (기본 테마 id 0은 삭제 불가)."""
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

        self.reload()
        try:
            tid = int(kb_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            target = self.get_kb(kb_id)
            tid = target.id

        theme = self._themes[tid]

        if theme.is_personal:
            raise ValueError(f"기본 테마(ID {tid})는 삭제할 수 없습니다.")

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        conn = dbm.connect(db_file)
        try:
            # Fedify 사이드카 설계 §9.4: 테마 삭제는 발행된 객체마다 tombstone과 Delete 작업을 만든다. 테마 행 삭제와 같은 트랜잭션이다(행 삭제가 federation_objects를 연쇄 삭제하기 전).
            from . import federation as federation_store

            if purge:
                dbm.reset_graph(conn, kb_id=tid)
                federation_store.retract_objects(conn, kb_id=tid, now=time.time())
                for tbl in (
                    "documents",
                    "document_snapshots",
                    "document_presentations",
                    "raw_inbox",
                    "extractions",
                    "proposals",
                    "jobs",
                    "refresh_queue",
                    "expand_queue",
                    "doc_shares",
                    "purged_tombstones",
                    "syndication_dispatches",
                    *dbm.V20_RETIRED_SYNDICATION_TABLES,
                ):
                    try:
                        conn.execute(f"DELETE FROM {tbl} WHERE kb_id = ?", (tid,))
                    except sqlite3.OperationalError:
                        pass
                conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (tid,))
                conn.commit()
            else:
                # The PRAGMA has no effect in a transaction, so it comes before the first write.
                conn.execute("PRAGMA foreign_keys = OFF;")
                federation_store.retract_objects(conn, kb_id=tid, now=time.time())
                conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (tid,))
                conn.commit()
                conn.execute("PRAGMA foreign_keys = ON;")
        finally:
            conn.close()

        self._themes.pop(tid, None)
        self._rebuild_fqdn_index()

        log.info("테마 #%d [%s] 삭제 완료 (purge=%s)", tid, theme.label, purge)
        return theme

    def kb_data_counts(self, kb_id: int) -> dict[str, int]:
        """테마에 속한 문서·엔티티·관계 수. 삭제·리셋 dry-run 보고용(읽기 전용)."""
        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        conn = dbm.connect(db_file)
        try:
            return {
                key: conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE kb_id = ?", (kb_id,)).fetchone()[0]
                for key, tbl in (("documents", "documents"), ("entities", "entities"), ("relations", "relations"))
            }
        finally:
            conn.close()

    def reset_kb(self, kb_id: int | str) -> dict[str, Any]:
        """지식 관리자: 테마 고유 식별자(?theme=id) 및 메타데이터는 보존하고 내부 수집/그래프 데이터를 완전 초기화."""
        import sqlite3

        self.reload()
        try:
            tid = int(kb_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            target = self.get_kb(kb_id)
            tid = target.id

        theme = self._themes[tid]

        stats: dict[str, Any] = {
            "kb_id": tid,
            "theme_label": theme.label,
            "theme_icon": theme.icon,
            "db_cleared": False,
            "deleted_documents": 0,
            "deleted_entities": 0,
            "deleted_relations": 0,
        }

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        conn = dbm.connect(db_file)
        try:
            try:
                stats["deleted_documents"] = conn.execute("SELECT COUNT(*) FROM documents WHERE kb_id = ?", (tid,)).fetchone()[0]
                stats["deleted_entities"] = conn.execute("SELECT COUNT(*) FROM entities WHERE kb_id = ?", (tid,)).fetchone()[0]
                stats["deleted_relations"] = conn.execute("SELECT COUNT(*) FROM relations WHERE kb_id = ?", (tid,)).fetchone()[0]
            except Exception:
                pass

            dbm.reset_graph(conn, kb_id=tid)

            for tbl in (
                "documents",
                "document_snapshots",
                "document_presentations",
                "raw_inbox",
                "extractions",
                "proposals",
                "jobs",
                "refresh_queue",
                "expand_queue",
                "doc_shares",
                "purged_tombstones",
                "syndication_dispatches",
                *dbm.V20_RETIRED_SYNDICATION_TABLES,
            ):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE kb_id = ?", (tid,))
                except sqlite3.OperationalError:
                    pass

            now = time.time()
            # 임베딩이 모두 지워졌으므로 고정도 푼다. 다음 첫 저장이 그때의 모델로 다시 고정한다(§10.4).
            conn.execute(
                "UPDATE knowledge_bases SET updated_at = ?, embed_provider = NULL, embed_model = NULL WHERE id = ?",
                (now, tid),
            )
            conn.commit()
            stats["db_cleared"] = True
        finally:
            conn.close()

        theme.updated_at = now
        theme.embed_provider = ""
        theme.embed_model = ""
        self._rebuild_fqdn_index()

        log.info(
            "테마 #%d [%s] 데이터 완전 초기화 완료 (URI: ?theme=%d, 문서: %d건, 엔티티: %d건, 관계: %d건 삭제)",
            tid,
            theme.label,
            tid,
            stats["deleted_documents"],
            stats["deleted_entities"],
            stats["deleted_relations"],
        )
        return stats

    def get_settings_for_kb(
        self,
        theme_ref: int | str | None = None,
        base_settings: Settings | Any | None = None,
    ) -> Any:
        """지정된 테마의 db_path 가 적용된 Settings 인스턴스를 반환. 없는(삭제된) 테마면 KnowledgeBaseNotFound."""
        theme = self.get_kb(theme_ref)
        base = base_settings or self.settings

        return self._settings_for_theme_info(theme, base)

    @staticmethod
    def _settings_for_theme_info(theme: KnowledgeBaseInfo, base: Settings | Any) -> Any:
        """글로벌 설정을 유지하고 테마별 저장 경로 및 테마 설정을 적용한다."""
        db_file = getattr(base, "db_file", Path("data/claire.db"))
        db_path = str(db_file)

        if hasattr(base, "model_copy"):
            override = {
                "db_path": db_path,
                "db_file": db_file,
                "kb_id": theme.id,
            }
            if theme.sorcerer:
                override["sorcerer"] = theme.sorcerer
            return base.model_copy(update=override)

        import copy

        st = copy.copy(base)
        st.db_file = db_file
        if hasattr(st, "db_path"):
            st.db_path = db_path
        if theme.sorcerer and hasattr(st, "sorcerer"):
            st.sorcerer = theme.sorcerer
        st.kb_id = theme.id
        return st

    def active_kb_settings(
        self,
        base_settings: Settings | Any | None = None,
    ) -> list[tuple[KnowledgeBaseInfo, Any]]:
        """활성 테마와 경로가 적용된 설정을 테마 ID 순서로 반환한다.

        멀티 테마가 꺼져 있으면 레지스트리 I/O 없이 기본 설정 한 개만 반환한다. 멀티 테마가 켜져 있으면 레지스트리를 매번 다시 읽어 신규/삭제 테마를 반영하며, 손상된 레지스트리 오류는 호출자에게 그대로 전파한다.
        """
        base = base_settings or self.settings
        if not getattr(base, "multi_theme", False):
            if getattr(self.settings, "multi_theme", False):
                # 멀티 관리자에 싱글 설정을 넘긴 경우: 레지스트리를 읽지 않는다.
                theme = self._build_default_theme()
            else:
                self.reload()
                theme = self._themes[0]
            return [(theme, self._settings_for_theme_info(theme, base))]

        self.reload()
        return [
            (theme, self._settings_for_theme_info(theme, base))
            for theme in sorted(self._themes.values(), key=lambda item: item.id)
        ]

    def resolve_share_token(
        self, token: str, *, include_hidden: bool = True
    ) -> tuple[int, str, dict[str, Any]] | None:
        """단일 통합 SQLite DB에서 공유 토큰의 (kb_id, document_id, doc_dict)를 단일 쿼리로 자동 해소."""
        from .queries import document_detail

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        if not Path(db_file).is_file():
            return None

        try:
            conn = dbm.connect_existing(Path(db_file), readonly=True)
            try:
                row = conn.execute(
                    "SELECT kb_id, document_id FROM doc_shares WHERE token = ?",
                    (token,),
                ).fetchone()
                if row:
                    tid = row["kb_id"]
                    doc_id = row["document_id"]
                    tdoc = document_detail(conn, doc_id, include_hidden=include_hidden, kb_id=tid)
                    if tdoc:
                        return (tid, doc_id, tdoc)
                    # Cross-theme self-healing fallback
                    targets = self.resolve_document_targets(doc_id=doc_id)
                    for target in targets:
                        target_tid = target.get("kb_id", 0)
                        if target_tid != tid:
                            tdoc_fallback = document_detail(
                                conn, doc_id, include_hidden=include_hidden, kb_id=target_tid
                            )
                            if tdoc_fallback:
                                return (target_tid, doc_id, tdoc_fallback)
            finally:
                conn.close()
        except Exception:
            pass
        return None

    def resolve_document_targets(
        self,
        target: str | None = None,
        *,
        doc_id: str | None = None,
        url: str | None = None,
        canonical_url: str | None = None,
        token: str | None = None,
        pattern: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """등록된 테마 DB들을 검색하여 입력값(target, doc_id, url, token 등)과 일치하는 문서 목록을 반환.

        각 결과 딕셔너리에는 kb_id, theme_label, db_file 정보가 함께 포함된다. 정확 일치(id, doc_id, token, share_token) 항목이 존재할 경우 해당 항목들을 우선 반환한다.
        """
        self.reload()
        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        if not Path(db_file).is_file():
            return []

        try:
            conn = dbm.connect_existing(Path(db_file), readonly=True)
            try:
                matched = dbm.resolve_document_targets(
                    conn,
                    target=target,
                    doc_id=doc_id,
                    url=url,
                    canonical_url=canonical_url,
                    token=token,
                    pattern=pattern,
                    limit=limit,
                )
                exact_matches: list[dict[str, Any]] = []
                url_matches: list[dict[str, Any]] = []
                pattern_matches: list[dict[str, Any]] = []

                for m in matched:
                    tid = m.get("kb_id", 0)
                    t_info = self._themes.get(tid)
                    m["kb_id"] = tid
                    m["theme_label"] = t_info.label if t_info else ("기본 지식베이스" if (t_info and t_info.is_personal) or tid == 0 else f"테마 #{tid}")
                    m["db_file"] = str(db_file)

                    mb = m.get("matched_by", "")
                    if mb in ("id", "doc_id", "token", "share_token"):
                        exact_matches.append(m)
                    elif mb in ("url", "canonical_url"):
                        url_matches.append(m)
                    else:
                        pattern_matches.append(m)

                if exact_matches:
                    return exact_matches
                if url_matches:
                    return url_matches
                return pattern_matches[:limit]
            finally:
                conn.close()
        except Exception:
            return []

        if exact_matches:
            return exact_matches
        if url_matches:
            return url_matches
        return pattern_matches[:limit]


def bump_kb_visibility_epoch(
    conn: sqlite3.Connection,
    kb_id: int,
    *,
    invalidated_keys: list[str] | None = None,
) -> int:
    """Increment visibility_epoch of a theme and invoke CDN invalidation hook (설계 §5.4)."""
    conn.execute(
        "UPDATE knowledge_bases SET visibility_epoch = visibility_epoch + 1, updated_at = ? WHERE id = ?",
        (time.time(), int(kb_id)),
    )
    conn.commit()
    row = conn.execute("SELECT visibility_epoch FROM knowledge_bases WHERE id = ?", (int(kb_id),)).fetchone()
    epoch = row[0] if row else 1
    if invalidated_keys:
        try:
            from .media import trigger_cdn_invalidation
            trigger_cdn_invalidation(invalidated_keys)
        except Exception:
            pass
    return epoch




_default_manager: KnowledgeBaseManager | None = None


def get_kb_manager(settings: Settings | None = None) -> KnowledgeBaseManager:
    global _default_manager
    if settings is not None:
        return KnowledgeBaseManager(settings)
    if _default_manager is None:
        _default_manager = KnowledgeBaseManager()
    return _default_manager
