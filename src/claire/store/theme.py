"""일련번호 기반 물리 디렉터리 격리 및 레이블 인식 테마 매니저.

지식 관리자가 테마의 레이블이나 설명을 언제든지 자유롭게 수정하더라도
물리적 SQLite 파일이나 디렉터리가 영향받지 않도록, 디렉터리 경로는 순수 일련번호(0, 1, 2...)로
영구 고정 관리하고 이용자 및 시스템에는 레이블(Label)로 인식되도록 합니다.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..config import ROOT, Settings, get_settings
from . import db as dbm

log = logging.getLogger("claire.theme")

THEMES_REGISTRY_FILENAME = "themes.json"


@dataclass
class ThemeInfo:
    id: int
    seq: int
    label: str
    description: str = ""
    icon: str = "📚"
    db_path: str = ""
    vault_path: str = ""
    is_default: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThemeInfo:
        return cls(
            id=int(data.get("id", 0)),
            seq=int(data.get("seq", data.get("id", 0))),
            label=str(data.get("label", "")),
            description=str(data.get("description", "")),
            icon=str(data.get("icon", "📁")),
            db_path=str(data.get("db_path", "")),
            vault_path=str(data.get("vault_path", "")),
            is_default=bool(data.get("is_default", False)),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


class ThemeManager:
    """테마 레지스트리 및 일련번호 기반 스토리지 디렉터리 관리자."""

    def __init__(self, base_settings: Settings | None = None) -> None:
        self.settings = base_settings or get_settings()
        self.registry_path = self.settings.data_dir / THEMES_REGISTRY_FILENAME
        self._themes: dict[int, ThemeInfo] = {}
        self._next_seq: int = 1
        self._default_theme_id: int = 0
        self.reload()

    def reload(self) -> None:
        """themes.json 레지스트리를 읽고 메모리에 적재한다. 없으면 기본 테마로 초기화."""
        if not self.registry_path.is_file():
            self._init_default_registry()
            return

        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            themes_data = raw.get("themes", [])
            self._next_seq = int(raw.get("next_seq", 1))
            self._default_theme_id = int(raw.get("default_theme_id", 0))

            loaded: dict[int, ThemeInfo] = {}
            if isinstance(themes_data, list):
                for item in themes_data:
                    t = ThemeInfo.from_dict(item)
                    loaded[t.id] = t
            elif isinstance(themes_data, dict):
                for k, item in themes_data.items():
                    t = ThemeInfo.from_dict(item)
                    loaded[t.id] = t

            if 0 not in loaded:
                default_theme = self._build_default_theme()
                loaded[0] = default_theme

            self._themes = loaded
            max_seq = max((t.seq for t in self._themes.values()), default=0)
            if self._next_seq <= max_seq:
                self._next_seq = max_seq + 1

        except Exception as exc:
            log.warning("themes.json 읽기 실패 (%s). 기본 테마로 복구합니다.", exc)
            self._init_default_registry()

    def _build_default_theme(self) -> ThemeInfo:
        now = time.time()
        db_path = getattr(self.settings, "db_path", None)
        if db_path is None:
            db_file = getattr(self.settings, "db_file", None)
            db_path = str(db_file) if db_file is not None else "data/claire.db"
        vault_path = getattr(self.settings, "vault_path", None)
        if vault_path is None:
            vault_dir = getattr(self.settings, "vault_dir", None)
            if vault_dir is not None:
                vault_path = str(vault_dir)
            else:
                data_dir = getattr(self.settings, "data_dir", None)
                vault_path = str(data_dir / "vault") if data_dir is not None else "vault"

        return ThemeInfo(
            id=0,
            seq=0,
            label="기본 지식베이스",
            description="일반 수집 자료 및 기본 지식",
            icon="📚",
            db_path=str(db_path),
            vault_path=str(vault_path),
            is_default=True,
            created_at=now,
            updated_at=now,
        )

    def _init_default_registry(self) -> None:
        default_theme = self._build_default_theme()
        self._themes = {0: default_theme}
        self._next_seq = 1
        self._default_theme_id = 0
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

    def list_themes(self) -> list[ThemeInfo]:
        """등록된 테마 목록을 일련번호 순서로 반환."""
        self.reload()
        return sorted(self._themes.values(), key=lambda t: t.id)

    def get_theme(self, theme_ref: int | str | None, *, strict: bool = False) -> ThemeInfo:
        """일련번호(int/str) 또는 레이블로 테마를 검색.

        - theme_ref 가 None 이거나 빈 문자열이면 기본 테마(0) 반환.
        - 일련번호(숫자) 일치 우선.
        - 레이블(대소문자 무시 정확 일치) 차선.
        - 찾지 못한 경우 strict=False 이면 기본 테마(0) 반환, strict=True 이면 KeyError.
        """
        self.reload()
        if theme_ref is None or str(theme_ref).strip() == "":
            return self._themes.get(0, self._build_default_theme())

        ref_str = str(theme_ref).strip()

        # 1. 정수 일련번호 매칭
        if ref_str.isdigit():
            tid = int(ref_str)
            if tid in self._themes:
                return self._themes[tid]

        # 2. 레이블 정확 일치 (대소문자 무시)
        ref_lower = ref_str.lower()
        for t in self._themes.values():
            if t.label.lower() == ref_lower:
                return t

        # 3. 레이블 부분/슬러그 매칭
        for t in self._themes.values():
            if ref_lower in t.label.lower():
                return t

        if strict:
            raise KeyError(f"테마를 찾을 수 없습니다: {theme_ref}")
        return self._themes.get(0, self._build_default_theme())

    def define_theme(
        self,
        label: str,
        *,
        description: str = "",
        icon: str = "📁",
    ) -> ThemeInfo:
        """지식 관리자: 순차 일련번호를 발급하여 새 테마 디렉터리 생성 및 DB 스키마 초기화."""
        cleaned_label = str(label or "").strip()
        if not cleaned_label:
            raise ValueError("테마 레이블(이름)을 입력해야 합니다.")

        self.reload()

        # 동일 레이블 중복 방지
        for t in self._themes.values():
            if t.label.strip().lower() == cleaned_label.lower():
                raise ValueError(f"이미 동일한 레이블의 테마가 존재합니다: '{cleaned_label}'")

        seq = self._next_seq
        self._next_seq += 1

        abs_db_dir = self.settings.data_dir / "themes" / str(seq)
        abs_db_file = abs_db_dir / "claire.db"
        abs_vault_dir = self.settings.vault_dir / "themes" / str(seq)

        # 디렉터리 생성
        abs_db_file.parent.mkdir(parents=True, exist_ok=True)
        abs_vault_dir.mkdir(parents=True, exist_ok=True)

        # SQLite 초기화 및 마이그레이션 일괄 적용
        conn = dbm.connect(abs_db_file)
        try:
            dbm.init_db(conn)
        finally:
            conn.close()

        try:
            rel_db_path = str(abs_db_file.relative_to(ROOT))
        except ValueError:
            rel_db_path = str(abs_db_file)

        try:
            rel_vault_path = str(abs_vault_dir.relative_to(ROOT))
        except ValueError:
            rel_vault_path = str(abs_vault_dir)

        now = time.time()
        theme = ThemeInfo(
            id=seq,
            seq=seq,
            label=cleaned_label,
            description=str(description or "").strip(),
            icon=str(icon or "📁").strip(),
            db_path=rel_db_path,
            vault_path=rel_vault_path,
            is_default=False,
            created_at=now,
            updated_at=now,
        )

        self._themes[seq] = theme
        self._save_registry()
        log.info("새 테마 #%d [%s] 정의 및 생성 완료 (경로: %s)", seq, cleaned_label, rel_db_path)
        return theme

    def update_theme(
        self,
        theme_id: int | str,
        *,
        label: str | None = None,
        description: str | None = None,
        icon: str | None = None,
    ) -> ThemeInfo:
        """지식 관리자: 테마 레이블, 설명, 아이콘 수정 (물리 폴더 경로는 절대 변경되지 않음)."""
        self.reload()
        try:
            tid = int(theme_id)
        except (ValueError, TypeError) as err:
            raise KeyError(f"유효하지 않은 테마 ID: {theme_id}") from err

        if tid not in self._themes:
            raise KeyError(f"존재하지 않는 테마 ID: {tid}")

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

        theme.updated_at = time.time()
        self._save_registry()
        log.info("테마 #%d 메타데이터 수정 완료 (레이블: %s)", tid, theme.label)
        return theme

    def delete_theme(self, theme_id: int | str, *, purge: bool = False) -> ThemeInfo:
        """지식 관리자: 테마 삭제 (기본 테마 id 0은 삭제 불가)."""
        self.reload()
        try:
            tid = int(theme_id)
        except (ValueError, TypeError) as err:
            raise KeyError(f"유효하지 않은 테마 ID: {theme_id}") from err

        if tid == 0:
            raise ValueError("기본 테마(ID 0)는 삭제할 수 없습니다.")

        if tid not in self._themes:
            raise KeyError(f"존재하지 않는 테마 ID: {tid}")

        theme = self._themes.pop(tid)
        self._save_registry()

        if purge:
            # 완전 소각: DB 파일 및 vault 디렉터리 삭제
            try:
                t_settings = self.get_settings_for_theme(tid)
                db_p = t_settings.db_file
                if db_p.is_file():
                    db_p.unlink()
                # WAL/SHM 정리
                for ext in ("-wal", "-shm"):
                    extra = Path(str(db_p) + ext)
                    if extra.is_file():
                        extra.unlink()
                if db_p.parent.name == str(tid) and db_p.parent.is_dir():
                    import shutil
                    shutil.rmtree(db_p.parent, ignore_errors=True)

                vault_p = t_settings.vault_dir
                if vault_p.name == str(tid) and vault_p.is_dir():
                    import shutil
                    shutil.rmtree(vault_p, ignore_errors=True)
            except Exception as exc:
                log.warning("테마 #%d 디스크 삭제 중 오류 발생: %s", tid, exc)

        log.info("테마 #%d [%s] 삭제 완료 (purge=%s)", tid, theme.label, purge)
        return theme

    def get_settings_for_theme(
        self,
        theme_ref: int | str | None = None,
        base_settings: Settings | Any | None = None,
    ) -> Any:
        """지정된 테마의 db_path 및 vault_path 가 적용된 Settings 인스턴스를 반환."""
        theme = self.get_theme(theme_ref)
        base = base_settings or self.settings

        if hasattr(base, "model_copy"):
            override = {
                "db_path": theme.db_path,
                "vault_path": theme.vault_path,
            }
            return base.model_copy(update=override)

        import copy

        st = copy.copy(base)
        st.db_file = Path(theme.db_path)
        if hasattr(st, "db_path"):
            st.db_path = theme.db_path
        if hasattr(st, "vault_path"):
            st.vault_path = theme.vault_path
        if hasattr(st, "vault_dir"):
            st.vault_dir = Path(theme.vault_path)
        return st

    def resolve_share_token(self, token: str) -> tuple[int, str, dict[str, Any]] | None:
        """등록된 모든 테마 DB를 검색하여 공유 토큰의 (theme_id, document_id, doc_dict)를 자동 해소."""
        from .queries import document_detail

        self.reload()
        for t in self._themes.values():
            abs_db = self.get_settings_for_theme(t.id).db_file
            if not abs_db.is_file():
                continue
            try:
                conn = dbm.connect_existing(abs_db, readonly=True)
                try:
                    doc_id = dbm.resolve_doc_share(conn, token)
                    if doc_id:
                        doc = document_detail(conn, doc_id, include_hidden=True)
                        if doc:
                            return (t.id, doc_id, doc)
                finally:
                    conn.close()
            except Exception:
                continue
        return None


_default_manager: ThemeManager | None = None


def get_theme_manager(settings: Settings | None = None) -> ThemeManager:
    global _default_manager
    if settings is not None:
        return ThemeManager(settings)
    if _default_manager is None:
        _default_manager = ThemeManager()
    return _default_manager
