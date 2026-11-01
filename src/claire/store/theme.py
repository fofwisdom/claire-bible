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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config import ROOT, Settings, get_settings
from . import db as dbm

log = logging.getLogger("claire.theme")

THEMES_REGISTRY_FILENAME = "themes.json"

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


@dataclass
class ThemeInfo:
    id: int
    seq: int
    label: str
    description: str = ""
    icon: str = "📚"
    db_path: str = ""
    is_default: bool = False
    is_public: bool = True
    is_collaborator_accessible: bool = True
    default_focus: str = ""
    fqdn: str = ""
    ga_measurement_id: str = ""
    sorcerer: str = ""
    presentation_enabled: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def knowledge_manager(self) -> str:
        return self.sorcerer

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["knowledge_manager"] = self.sorcerer
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThemeInfo:
        is_def = bool(data.get("is_default", False))
        tid = int(data.get("id", 0))
        default_collab = not (is_def or tid == 0)
        return cls(
            id=tid,
            seq=int(data.get("seq", data.get("id", 0))),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            icon=str(data.get("icon", "📁")),
            db_path=str(data.get("db_path", "")),
            is_default=is_def,
            is_public=bool(data.get("is_public", data.get("public", True))),
            is_collaborator_accessible=bool(
                data.get(
                    "is_collaborator_accessible",
                    data.get("collaborator_accessible", data.get("collaborator", default_collab)),
                )
            ),
            default_focus=(
                ""
                if (is_def or tid == 0)
                else str(data.get("default_focus") or "").strip()
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
        )


def _row_to_theme(row: Any, default_db_path: str) -> ThemeInfo:
    d = dict(row)
    tid = int(d.get("id", 0))
    is_def = bool(d.get("is_default", tid == 0))
    db_p = str(d.get("db_path") or "").strip() or default_db_path
    return ThemeInfo(
        id=tid,
        seq=int(d.get("seq", tid)),
        label=str(d.get("label", "")),
        description=str(d.get("description", "")),
        icon=str(d.get("icon", "📁")),
        db_path=db_p,
        is_default=is_def,
        is_public=bool(d.get("is_public", 1)),
        is_collaborator_accessible=bool(d.get("is_collaborator_accessible", not is_def)),
        default_focus="" if is_def else str(d.get("default_focus") or "").strip(),
        fqdn=validate_fqdn(d.get("fqdn")),
        ga_measurement_id=validate_ga_measurement_id(d.get("ga_measurement_id")),
        sorcerer=str(d.get("sorcerer") or "").strip(),
        presentation_enabled=bool(d.get("presentation_enabled", 0)),
        created_at=float(d.get("created_at", 0.0)),
        updated_at=float(d.get("updated_at", 0.0)),
    )


class ThemeManager:
    """테마 레지스트리 및 단일 데이터베이스 기반 테마 분할 관리자."""

    def __init__(
        self,
        base_settings: Settings | None = None,
        *,
        create_registry: bool = True,
    ) -> None:
        self.settings = base_settings or get_settings()
        self.create_registry = create_registry
        self.registry_path = self.settings.data_dir / THEMES_REGISTRY_FILENAME
        self._themes: dict[int, ThemeInfo] = {}
        self._fqdn_to_theme_id: dict[str, int] = {}
        self._next_seq: int = 1
        self._default_theme_id: int = 0
        if self.create_registry:
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
        self._fqdn_to_theme_id = idx

    def get_theme_by_fqdn(self, fqdn: str | None) -> ThemeInfo | None:
        """FQDN 호스트명으로 등록된 테마를 검색. 미등록 시 None."""
        if not fqdn:
            return None
        self.reload()
        cleaned = str(fqdn).strip().lower()
        if ":" in cleaned:
            cleaned = cleaned.split(":", 1)[0]
        tid = self._fqdn_to_theme_id.get(cleaned)
        if tid is not None and tid in self._themes:
            return self._themes[tid]
        return None

    def get_registered_fqdns(self) -> set[str]:
        """현재 등록된 모든 테마의 FQDN 호스트명 집합 반환."""
        self.reload()
        return set(self._fqdn_to_theme_id.keys())

    def has_registered_fqdn(self, fqdn: str | None) -> bool:
        """해당 FQDN이 특정 테마의 서비스 도메인으로 등록되어 있는지 여부."""
        if not fqdn:
            return False
        self.reload()
        cleaned = str(fqdn).strip().lower()
        if ":" in cleaned:
            cleaned = cleaned.split(":", 1)[0]
        return cleaned in self._fqdn_to_theme_id

    def has_any_ga_enabled(self) -> bool:
        """등록된 테마 중 하나라도 GA4 측정 ID를 사용하는지 확인."""
        self.reload()
        return any(bool(t.ga_measurement_id) for t in self._themes.values())

    def reload(self) -> None:
        """themes 테이블(SQLite) 및 themes.json 레지스트리를 동기화하고 메모리에 적재한다.

        싱글 테마 모드에서는 레지스트리를 전혀 읽지 않는다. 멀티 테마 모드에서 레지스트리가 없으면 기본 레지스트리를 만들지만, 이미 존재하는 레지스트리가 손상됐으면 기본값으로 덮어쓰거나 숨기지 않고 오류를 전파한다.
        """
        if not getattr(self.settings, "multi_theme", False):
            # 싱글 테마 모드: 파일 I/O 및 락을 원천 차단하고 메모리 상의 기본 테마 1개만 고정 유지
            if not self._themes or 0 not in self._themes:
                self._themes = {0: self._build_default_theme()}
            self._rebuild_fqdn_index()
            return

        raw_themes_from_json: list[dict[str, Any]] = []
        json_next_seq = 1
        json_default_theme_id = 0
        registry_exists = self.registry_path.is_file()

        if registry_exists:
            try:
                raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("registry root must be an object")
                themes_data = raw.get("themes", [])
                json_next_seq = int(raw.get("next_seq", 1))
                json_default_theme_id = int(raw.get("default_theme_id", 0))

                if isinstance(themes_data, list):
                    for item in themes_data:
                        if not isinstance(item, dict):
                            raise ValueError("theme entry must be an object")
                        raw_themes_from_json.append(item)
                elif isinstance(themes_data, dict):
                    for item in themes_data.values():
                        if not isinstance(item, dict):
                            raise ValueError("theme entry must be an object")
                        raw_themes_from_json.append(item)
                else:
                    raise ValueError("themes must be a list or object")
            except Exception as exc:
                raise RuntimeError(
                    f"테마 레지스트리를 읽을 수 없습니다: {self.registry_path}: {exc}"
                ) from exc
        else:
            if not self.create_registry:
                raise FileNotFoundError(f"테마 레지스트리가 없습니다: {self.registry_path}")

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        if not self.create_registry:
            if not Path(db_file).is_file():
                if not self._themes or 0 not in self._themes:
                    self._themes = {0: self._build_default_theme()}
                self._rebuild_fqdn_index()
                return
            conn = dbm.connect_existing(db_file, readonly=True)
            try:
                try:
                    rows = conn.execute("SELECT * FROM themes ORDER BY id").fetchall()
                except (sqlite3.OperationalError, sqlite3.DatabaseError):
                    rows = []
                loaded = {}
                for r in rows:
                    t = _row_to_theme(r, default_db_path=str(db_file))
                    loaded[t.id] = t
                if raw_themes_from_json:
                    for item in raw_themes_from_json:
                        t_info = ThemeInfo.from_dict(item)
                        if t_info.id not in loaded:
                            loaded[t_info.id] = t_info
                if 0 not in loaded:
                    loaded[0] = self._build_default_theme()
                self._themes = loaded
                max_seq = max((t.seq for t in self._themes.values()), default=0)
                self._next_seq = max(max_seq + 1, json_next_seq)
                self._default_theme_id = json_default_theme_id if json_default_theme_id in self._themes else 0
                self._rebuild_fqdn_index()
                return
            finally:
                conn.close()

        Path(db_file).parent.mkdir(parents=True, exist_ok=True)
        conn = dbm.connect(db_file)
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

            rows = conn.execute("SELECT * FROM themes ORDER BY id").fetchall()

            if raw_themes_from_json:
                existing_ids = {r["id"] for r in rows}
                for item in raw_themes_from_json:
                    t_info = ThemeInfo.from_dict(item)
                    if t_info.id not in existing_ids:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO themes (
                                id, seq, label, description, icon, db_path,
                                is_default, is_public, is_collaborator_accessible,
                                default_focus, fqdn, ga_measurement_id, sorcerer,
                                presentation_enabled, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                t_info.id,
                                t_info.seq,
                                t_info.label,
                                t_info.description,
                                t_info.icon,
                                str(db_file),
                                int(t_info.is_default),
                                int(t_info.is_public),
                                int(t_info.is_collaborator_accessible),
                                t_info.default_focus,
                                t_info.fqdn,
                                t_info.ga_measurement_id,
                                t_info.sorcerer,
                                int(t_info.presentation_enabled),
                                t_info.created_at,
                                t_info.updated_at,
                            ),
                        )
                conn.commit()
                rows = conn.execute("SELECT * FROM themes ORDER BY id").fetchall()

            loaded: dict[int, ThemeInfo] = {}
            for r in rows:
                t = _row_to_theme(r, default_db_path=str(db_file))
                loaded[t.id] = t

            if 0 not in loaded:
                t0 = self._build_default_theme()
                conn.execute(
                    """
                    INSERT OR IGNORE INTO themes (
                        id, seq, label, description, icon, db_path,
                        is_default, is_public, is_collaborator_accessible,
                        default_focus, fqdn, ga_measurement_id, sorcerer,
                        presentation_enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        0,
                        0,
                        t0.label,
                        t0.description,
                        t0.icon,
                        str(db_file),
                        1,
                        int(t0.is_public),
                        int(t0.is_collaborator_accessible),
                        t0.default_focus,
                        t0.fqdn,
                        t0.ga_measurement_id,
                        t0.sorcerer,
                        int(t0.presentation_enabled),
                        t0.created_at,
                        t0.updated_at,
                    ),
                )
                conn.commit()
                loaded[0] = t0

            self._themes = loaded
            max_seq = max((t.seq for t in self._themes.values()), default=0)
            self._next_seq = max(max_seq + 1, json_next_seq)
            self._default_theme_id = json_default_theme_id if json_default_theme_id in self._themes else 0
            self._rebuild_fqdn_index()
        finally:
            conn.close()

        if not registry_exists and getattr(self.settings, "multi_theme", False) and self.create_registry:
            self._save_registry()

    def _build_default_theme(self) -> ThemeInfo:
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
        return ThemeInfo(
            id=0,
            seq=0,
            label="기본 지식베이스",
            description="일반 수집 자료 및 기본 지식",
            icon="📚",
            db_path=str(db_path),
            is_default=True,
            is_public=True,
            is_collaborator_accessible=False,
            sorcerer=str(default_sorcerer).strip() or "owner",
            presentation_enabled=bool(getattr(self.settings, "presentation_enabled", False)),
            created_at=now,
            updated_at=now,
        )

    def _init_default_registry(self) -> None:
        default_theme = self._build_default_theme()
        self._themes = {0: default_theme}
        self._next_seq = 1
        self._default_theme_id = 0
        if getattr(self.settings, "multi_theme", False):
            self._save_registry()

    def _save_registry(self) -> None:
        """원자적(atomic write)으로 themes.json 파일 갱신."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "next_seq": self._next_seq,
            "default_theme_id": self._default_theme_id,
            "themes": [t.to_dict() for t in sorted(self._themes.values(), key=lambda x: x.id)],
        }
        raw_json = json.dumps(data, ensure_ascii=False, indent=2)
        dir_path = self.registry_path.parent
        with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
            tf.write(raw_json)
            tmp_name = tf.name
        os.replace(tmp_name, self.registry_path)
        self._rebuild_fqdn_index()

    def list_themes(
        self,
        *,
        include_private: bool = True,
        collaborator: bool = False,
        host: str | None = None,
    ) -> list[ThemeInfo]:
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

        if cleaned_host and cleaned_host in self._fqdn_to_theme_id:
            dedicated_tid = self._fqdn_to_theme_id[cleaned_host]
            if dedicated_tid > 0:
                return [t for t in themes if t.id == dedicated_tid and t.is_public]

        return [t for t in themes if t.is_public and (not t.fqdn or t.is_default)]

    def get_theme(
        self,
        theme_ref: int | str | None,
        *,
        strict: bool = False,
        include_private: bool = True,
        collaborator: bool = False,
    ) -> ThemeInfo:
        """일련번호(int/str) 또는 레이블로 테마를 검색.

        - theme_ref 가 None 이거나 빈 문자열이면 기본 테마(0) 반환.
        - 일련번호(숫자) 일치 우선.
        - 레이블(대소문자 무시 정확 일치) 차선.
        - include_private=False, collaborator=False 이고 매칭된 테마가 비공개인 경우, strict=True 면 KeyError, strict=False 면 기본 공개 테마 반환.
        - collaborator=True 이고 매칭된 테마가 비공개이며 is_collaborator_accessible=False 인 경우 동일하게 제한.
        - 찾지 못한 경우 strict=False 이면 기본 테마(0) 반환, strict=True 이면 KeyError.
        """
        self.reload()
        found: ThemeInfo | None = None
        if theme_ref is None or str(theme_ref).strip() == "":
            found = self._themes.get(0, self._build_default_theme())
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

            # 3. 레이블 부분/슬러그 매칭
            if found is None:
                ref_lower = ref_str.lower()
                for t in self._themes.values():
                    if ref_lower in t.label.lower():
                        found = t
                        break

        def _is_accessible(t: ThemeInfo) -> bool:
            if include_private:
                return True
            if collaborator:
                return t.is_public or t.is_collaborator_accessible
            return t.is_public

        if found is not None:
            if not _is_accessible(found):
                if strict:
                    raise KeyError(f"비공개 테마입니다: {theme_ref}")
                default_t = self._themes.get(0, self._build_default_theme())
                if _is_accessible(default_t):
                    return default_t
                for t in sorted(self._themes.values(), key=lambda x: x.id):
                    if _is_accessible(t):
                        return t
                return found
            return found

        if strict:
            raise KeyError(f"테마를 찾을 수 없습니다: {theme_ref}")
        default_t = self._themes.get(0, self._build_default_theme())
        if not _is_accessible(default_t):
            for t in sorted(self._themes.values(), key=lambda x: x.id):
                if _is_accessible(t):
                    return t
        return default_t

    def define_theme(
        self,
        label: str,
        *,
        description: str = "",
        icon: str = "📁",
        is_public: bool = True,
        is_collaborator_accessible: bool = True,
        default_focus: str = "",
        fqdn: str = "",
        ga_measurement_id: str = "",
        sorcerer: str = "",
        knowledge_manager: str = "",
        presentation_enabled: bool = False,
    ) -> ThemeInfo:
        """지식 관리자: 순차 일련번호를 발급하여 새 테마 디렉터리 생성 및 DB 스키마 초기화."""
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

        cleaned_label = str(label or "").strip()
        if not cleaned_label:
            raise ValueError("테마 레이블(이름)을 입력해야 합니다.")

        norm_fqdn = validate_fqdn(fqdn)
        norm_ga = validate_ga_measurement_id(ga_measurement_id)

        self.reload()

        # 동일 레이블 중복 방지
        for t in self._themes.values():
            if t.label.strip().lower() == cleaned_label.lower():
                raise ValueError(f"이미 존재하는 테마 레이블입니다: '{cleaned_label}'")

        if norm_fqdn:
            eff_fqdn = getattr(self.settings, "effective_fqdn", "")
            if eff_fqdn and norm_fqdn == eff_fqdn:
                raise ValueError(f"기본 서비스 도메인('{norm_fqdn}')은 추가 테마의 FQDN으로 지정할 수 없습니다.")
            for t in self._themes.values():
                if t.fqdn and t.fqdn == norm_fqdn:
                    raise ValueError(f"이미 다른 테마(#{t.id} '{t.label}')에 등록된 FQDN입니다: '{norm_fqdn}'")

        seq = self._next_seq
        self._next_seq += 1

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        db_path = str(db_file)

        now = time.time()
        mgr = str(sorcerer or knowledge_manager or "").strip()
        theme = ThemeInfo(
            id=seq,
            seq=seq,
            label=cleaned_label,
            description=str(description or "").strip(),
            icon=str(icon or "📁").strip(),
            db_path=db_path,
            is_default=False,
            is_public=bool(is_public),
            is_collaborator_accessible=bool(is_collaborator_accessible),
            default_focus=str(default_focus or "").strip(),
            fqdn=norm_fqdn,
            ga_measurement_id=norm_ga,
            sorcerer=mgr,
            presentation_enabled=bool(presentation_enabled),
            created_at=now,
            updated_at=now,
        )

        conn = dbm.connect(db_file)
        try:
            conn.execute(
                """
                INSERT INTO themes (
                    id, seq, label, description, icon, db_path,
                    is_default, is_public, is_collaborator_accessible,
                    default_focus, fqdn, ga_measurement_id, sorcerer,
                    presentation_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    theme.id,
                    theme.seq,
                    theme.label,
                    theme.description,
                    theme.icon,
                    theme.db_path,
                    int(theme.is_default),
                    int(theme.is_public),
                    int(theme.is_collaborator_accessible),
                    theme.default_focus,
                    theme.fqdn,
                    theme.ga_measurement_id,
                    theme.sorcerer,
                    int(theme.presentation_enabled),
                    theme.created_at,
                    theme.updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        self._themes[seq] = theme
        self._save_registry()
        log.info(
            "새 테마 #%d [%s] (공개: %s, 협력자: %s, 기본 초점: %s, FQDN: %s) 정의 완료 (경로: %s)",
            seq,
            cleaned_label,
            theme.is_public,
            theme.is_collaborator_accessible,
            theme.default_focus,
            norm_fqdn or "(없음)",
            db_path,
        )
        return theme

    def update_theme(
        self,
        theme_id: int | str,
        *,
        label: str | None = None,
        description: str | None = None,
        icon: str | None = None,
        is_public: bool | None = None,
        is_collaborator_accessible: bool | None = None,
        default_focus: str | None = None,
        fqdn: str | None = None,
        ga_measurement_id: str | None = None,
        sorcerer: str | None = None,
        knowledge_manager: str | None = None,
        presentation_enabled: bool | None = None,
    ) -> ThemeInfo:
        """지식 관리자: 테마 레이블, 설명, 아이콘, 공개 여부, 협력자 공개 여부, 기본 적용 초점, FQDN, GA ID, 지식 관리자 수정 (물리 폴더 경로는 절대 변경되지 않음)."""
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

        self.reload()
        try:
            tid = int(theme_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            # 문자열 레이블/이름으로 지정된 경우 get_theme로 검색
            target = self.get_theme(theme_id, strict=True)
            tid = target.id

        theme = self._themes[tid]

        if label is not None:
            cleaned_label = str(label).strip()
            if not cleaned_label:
                raise ValueError("테마 레이블은 비어 있을 수 없습니다.")
            # 다른 테마와 레이블 중복 검사
            for other in self._themes.values():
                if other.id != tid and other.label.strip().lower() == cleaned_label.lower():
                    raise ValueError(f"이미 존재하는 테마 레이블입니다: '{cleaned_label}'")
            theme.label = cleaned_label

        if description is not None:
            theme.description = str(description).strip()

        if icon is not None:
            theme.icon = str(icon).strip() or "📁"

        if is_public is not None:
            theme.is_public = bool(is_public)

        if is_collaborator_accessible is not None:
            if tid == 0:
                theme.is_collaborator_accessible = False
            else:
                theme.is_collaborator_accessible = bool(is_collaborator_accessible)

        if default_focus is not None:
            if tid == 0:
                theme.default_focus = ""
            else:
                theme.default_focus = str(default_focus or "").strip()

        if fqdn is not None:
            norm_fqdn = validate_fqdn(fqdn)
            if norm_fqdn:
                eff_fqdn = getattr(self.settings, "effective_fqdn", "")
                if eff_fqdn and norm_fqdn == eff_fqdn and tid != 0:
                    raise ValueError(f"기본 서비스 도메인('{norm_fqdn}')은 추가 테마의 FQDN으로 지정할 수 없습니다.")
                for other in self._themes.values():
                    if other.id != tid and other.fqdn and other.fqdn == norm_fqdn:
                        raise ValueError(f"이미 다른 테마(#{other.id} '{other.label}')에 등록된 FQDN입니다: '{norm_fqdn}'")
            theme.fqdn = norm_fqdn

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
                UPDATE themes SET
                    label = ?,
                    description = ?,
                    icon = ?,
                    is_public = ?,
                    is_collaborator_accessible = ?,
                    default_focus = ?,
                    fqdn = ?,
                    ga_measurement_id = ?,
                    sorcerer = ?,
                    presentation_enabled = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    theme.label,
                    theme.description,
                    theme.icon,
                    int(theme.is_public),
                    int(theme.is_collaborator_accessible),
                    theme.default_focus,
                    theme.fqdn,
                    theme.ga_measurement_id,
                    theme.sorcerer,
                    int(theme.presentation_enabled),
                    theme.updated_at,
                    tid,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        self._save_registry()
        log.info("테마 #%d 메타데이터 수정 완료 (레이블: %s, FQDN: %s)", tid, theme.label, theme.fqdn or "(없음)")
        return theme

    def delete_theme(self, theme_id: int | str, *, purge: bool = False) -> ThemeInfo:
        """지식 관리자: 테마 삭제 (기본 테마 id 0은 삭제 불가)."""
        if not getattr(self.settings, "multi_theme", False):
            raise RuntimeError("멀티 테마 모드가 비활성화되어 있습니다 (CLAIRE_MULTI_THEME=1 필요)")

        self.reload()
        try:
            tid = int(theme_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            target = self.get_theme(theme_id, strict=True)
            tid = target.id

        if tid == 0:
            raise ValueError("기본 테마(ID 0)는 삭제할 수 없습니다.")

        theme = self._themes[tid]

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        conn = dbm.connect(db_file)
        try:
            if purge:
                dbm.reset_graph(conn, theme_id=tid)
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
                    "syndication_items",
                ):
                    try:
                        conn.execute(f"DELETE FROM {tbl} WHERE theme_id = ?", (tid,))
                    except sqlite3.OperationalError:
                        pass
                conn.execute("DELETE FROM themes WHERE id = ?", (tid,))
                conn.commit()
            else:
                conn.execute("PRAGMA foreign_keys = OFF;")
                conn.execute("DELETE FROM themes WHERE id = ?", (tid,))
                conn.commit()
                conn.execute("PRAGMA foreign_keys = ON;")
        finally:
            conn.close()

        self._themes.pop(tid, None)
        self._save_registry()

        log.info("테마 #%d [%s] 삭제 완료 (purge=%s)", tid, theme.label, purge)
        return theme

    def reset_theme(self, theme_id: int | str) -> dict[str, Any]:
        """지식 관리자: 테마 고유 식별자(?theme=id) 및 메타데이터는 보존하고 내부 수집/그래프 데이터를 완전 초기화."""
        import sqlite3

        self.reload()
        try:
            tid = int(theme_id)
            if tid not in self._themes:
                raise KeyError(f"존재하지 않는 테마 ID: {tid}")
        except (ValueError, TypeError):
            target = self.get_theme(theme_id, strict=True)
            tid = target.id

        theme = self._themes[tid]

        stats: dict[str, Any] = {
            "theme_id": tid,
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
                stats["deleted_documents"] = conn.execute("SELECT COUNT(*) FROM documents WHERE theme_id = ?", (tid,)).fetchone()[0]
                stats["deleted_entities"] = conn.execute("SELECT COUNT(*) FROM entities WHERE theme_id = ?", (tid,)).fetchone()[0]
                stats["deleted_relations"] = conn.execute("SELECT COUNT(*) FROM relations WHERE theme_id = ?", (tid,)).fetchone()[0]
            except Exception:
                pass

            dbm.reset_graph(conn, theme_id=tid)

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
                "syndication_items",
            ):
                try:
                    conn.execute(f"DELETE FROM {tbl} WHERE theme_id = ?", (tid,))
                except sqlite3.OperationalError:
                    pass

            now = time.time()
            conn.execute("UPDATE themes SET updated_at = ? WHERE id = ?", (now, tid))
            conn.commit()
            stats["db_cleared"] = True
        finally:
            conn.close()

        theme.updated_at = now
        self._save_registry()

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

    def get_settings_for_theme(
        self,
        theme_ref: int | str | None = None,
        base_settings: Settings | Any | None = None,
    ) -> Any:
        """지정된 테마의 db_path 가 적용된 Settings 인스턴스를 반환."""
        theme = self.get_theme(theme_ref)
        base = base_settings or self.settings

        return self._settings_for_theme_info(theme, base)

    @staticmethod
    def _settings_for_theme_info(theme: ThemeInfo, base: Settings | Any) -> Any:
        """글로벌 설정을 유지하고 테마별 저장 경로 및 테마 설정을 적용한다."""
        db_file = getattr(base, "db_file", Path("data/claire.db"))
        db_path = str(db_file)

        if hasattr(base, "model_copy"):
            override = {
                "db_path": db_path,
                "db_file": db_file,
                "theme_id": theme.id,
                "default_focus": theme.default_focus,
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
        st.theme_id = theme.id
        st.default_focus = theme.default_focus
        return st

    def active_theme_settings(
        self,
        base_settings: Settings | Any | None = None,
    ) -> list[tuple[ThemeInfo, Any]]:
        """활성 테마와 경로가 적용된 설정을 테마 ID 순서로 반환한다.

        멀티 테마가 꺼져 있으면 레지스트리 I/O 없이 기본 설정 한 개만 반환한다. 멀티 테마가 켜져 있으면 레지스트리를 매번 다시 읽어 신규/삭제 테마를 반영하며, 손상된 레지스트리 오류는 호출자에게 그대로 전파한다.
        """
        base = base_settings or self.settings
        if not getattr(base, "multi_theme", False):
            theme = self._build_default_theme()
            return [(theme, self._settings_for_theme_info(theme, base))]

        self.reload()
        return [
            (theme, self._settings_for_theme_info(theme, base))
            for theme in sorted(self._themes.values(), key=lambda item: item.id)
        ]

    def resolve_share_token(
        self, token: str, *, include_hidden: bool = True
    ) -> tuple[int, str, dict[str, Any]] | None:
        """단일 통합 SQLite DB에서 공유 토큰의 (theme_id, document_id, doc_dict)를 단일 쿼리로 자동 해소."""
        from .queries import document_detail

        db_file = getattr(self.settings, "db_file", None) or Path("data/claire.db")
        if not Path(db_file).is_file():
            return None

        try:
            conn = dbm.connect_existing(Path(db_file), readonly=True)
            try:
                row = conn.execute(
                    "SELECT theme_id, document_id FROM doc_shares WHERE token = ?",
                    (token,),
                ).fetchone()
                if row:
                    tid = row["theme_id"]
                    doc_id = row["document_id"]
                    tdoc = document_detail(conn, doc_id, include_hidden=include_hidden, theme_id=tid)
                    if tdoc:
                        return (tid, doc_id, tdoc)
                    # Cross-theme self-healing fallback
                    targets = self.resolve_document_targets(doc_id=doc_id)
                    for target in targets:
                        target_tid = target.get("theme_id", 0)
                        if target_tid != tid:
                            tdoc_fallback = document_detail(
                                conn, doc_id, include_hidden=include_hidden, theme_id=target_tid
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

        각 결과 딕셔너리에는 theme_id, theme_label, db_file 정보가 함께 포함된다. 정확 일치(id, doc_id, token, share_token) 항목이 존재할 경우 해당 항목들을 우선 반환한다.
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
                    tid = m.get("theme_id", 0)
                    t_info = self._themes.get(tid)
                    m["theme_id"] = tid
                    m["theme_label"] = t_info.label if t_info else ("기본 지식베이스" if tid == 0 else f"테마 #{tid}")
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



_default_manager: ThemeManager | None = None


def get_theme_manager(settings: Settings | None = None) -> ThemeManager:
    global _default_manager
    if settings is not None:
        return ThemeManager(settings)
    if _default_manager is None:
        _default_manager = ThemeManager()
    return _default_manager
