"""일련번호 기반 물리 디렉터리 격리 및 레이블 인식 테마 매니저.

지식 관리자가 테마의 레이블이나 설명을 언제든지 자유롭게 수정하더라도
물리적 SQLite 파일이나 디렉터리가 영향받지 않도록, 디렉터리 경로는 순수 일련번호(0, 1, 2...)로
영구 고정 관리하고 이용자 및 시스템에는 레이블(Label)로 인식되도록 합니다.
"""

from __future__ import annotations

import json
import logging
import os
import re
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
    vault_path: str = ""
    is_default: bool = False
    is_public: bool = True
    is_collaborator_accessible: bool = True
    default_focus: str = ""
    fqdn: str = ""
    ga_measurement_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

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
            vault_path=str(data.get("vault_path", "")),
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
                else str(
                    data.get("default_focus")
                    or data.get("focus")
                    or data.get("default_orientation")
                    or data.get("orientation")
                    or data.get("default_directive")
                    or data.get("directive")
                    or ""
                ).strip()
            ),
            fqdn=validate_fqdn(data.get("fqdn")),
            ga_measurement_id=validate_ga_measurement_id(
                data.get("ga_measurement_id", data.get("ga_id"))
            ),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


class ThemeManager:
    """테마 레지스트리 및 일련번호 기반 스토리지 디렉터리 관리자."""

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
        self.reload()

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
        """themes.json 레지스트리를 읽고 메모리에 적재한다.

        싱글 테마 모드에서는 레지스트리를 전혀 읽지 않는다. 멀티 테마 모드에서
        레지스트리가 없으면 기본 레지스트리를 만들지만, 이미 존재하는 레지스트리가
        손상됐으면 기본값으로 덮어쓰거나 숨기지 않고 오류를 전파한다.
        """
        if not getattr(self.settings, "multi_theme", False):
            # 싱글 테마 모드: 파일 I/O 및 락을 원천 차단하고 메모리 상의 기본 테마 1개만 고정 유지
            if not self._themes or 0 not in self._themes:
                self._themes = {0: self._build_default_theme()}
            self._rebuild_fqdn_index()
            return

        if not self.registry_path.is_file():
            if not self.create_registry:
                raise FileNotFoundError(f"테마 레지스트리가 없습니다: {self.registry_path}")
            self._init_default_registry()
            return

        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("registry root must be an object")
            themes_data = raw.get("themes", [])
            self._next_seq = int(raw.get("next_seq", 1))
            self._default_theme_id = int(raw.get("default_theme_id", 0))

            loaded: dict[int, ThemeInfo] = {}
            if isinstance(themes_data, list):
                for item in themes_data:
                    if not isinstance(item, dict):
                        raise ValueError("theme entry must be an object")
                    t = ThemeInfo.from_dict(item)
                    if t.id in loaded:
                        raise ValueError(f"duplicate theme id: {t.id}")
                    loaded[t.id] = t
            elif isinstance(themes_data, dict):
                for item in themes_data.values():
                    if not isinstance(item, dict):
                        raise ValueError("theme entry must be an object")
                    t = ThemeInfo.from_dict(item)
                    if t.id in loaded:
                        raise ValueError(f"duplicate theme id: {t.id}")
                    loaded[t.id] = t
            else:
                raise ValueError("themes must be a list or object")

            if 0 not in loaded:
                raise ValueError("default theme id 0 is missing")
            if self._default_theme_id not in loaded:
                raise ValueError(
                    f"default_theme_id is not registered: {self._default_theme_id}"
                )

            self._themes = loaded
            max_seq = max((t.seq for t in self._themes.values()), default=0)
            if self._next_seq <= max_seq:
                self._next_seq = max_seq + 1
            self._rebuild_fqdn_index()

        except Exception as exc:
            raise RuntimeError(
                f"테마 레지스트리를 읽을 수 없습니다: {self.registry_path}: {exc}"
            ) from exc

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
            is_public=True,
            is_collaborator_accessible=False,
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
        self, *, include_private: bool = True, collaborator: bool = False
    ) -> list[ThemeInfo]:
        """등록된 테마 목록을 일련번호 순서로 반환.

        include_private=True 인 경우 모든 테마 반환.
        collaborator=True 인 경우 공개(is_public=True) 또는 Collaborator 공개(is_collaborator_accessible=True) 테마 반환.
        include_private=False 및 collaborator=False 인 경우 공개(is_public=True) 테마만 필터링하여 반환.
        """
        self.reload()
        themes = sorted(self._themes.values(), key=lambda t: t.id)
        if include_private:
            return themes
        if collaborator:
            return [t for t in themes if t.is_public or t.is_collaborator_accessible]
        return [t for t in themes if t.is_public]

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
                raise ValueError(f"이미 동일한 레이블의 테마가 존재합니다: '{cleaned_label}'")

        if norm_fqdn:
            eff_fqdn = getattr(self.settings, "effective_fqdn", "")
            if eff_fqdn and norm_fqdn == eff_fqdn:
                raise ValueError(f"기본 서비스 도메인('{norm_fqdn}')은 추가 테마의 FQDN으로 지정할 수 없습니다.")
            for t in self._themes.values():
                if t.fqdn and t.fqdn == norm_fqdn:
                    raise ValueError(f"이미 다른 테마(#{t.id} '{t.label}')에 등록된 FQDN입니다: '{norm_fqdn}'")

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
            is_public=bool(is_public),
            is_collaborator_accessible=bool(is_collaborator_accessible),
            default_focus=str(default_focus or "").strip(),
            fqdn=norm_fqdn,
            ga_measurement_id=norm_ga,
            created_at=now,
            updated_at=now,
        )

        self._themes[seq] = theme
        self._save_registry()
        log.info(
            "새 테마 #%d [%s] (공개: %s, 협력자: %s, 기본 초점: %s, FQDN: %s) 정의 및 생성 완료 (경로: %s)",
            seq,
            cleaned_label,
            theme.is_public,
            theme.is_collaborator_accessible,
            theme.default_focus,
            norm_fqdn or "(없음)",
            rel_db_path,
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
    ) -> ThemeInfo:
        """지식 관리자: 테마 레이블, 설명, 아이콘, 공개 여부, 협력자 공개 여부, 기본 적용 초점, FQDN, GA ID 수정 (물리 폴더 경로는 절대 변경되지 않음)."""
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

        theme.updated_at = time.time()
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
        t_settings = self.get_settings_for_theme(tid)

        self._themes.pop(tid)
        self._save_registry()

        if purge:
            # 완전 소각: DB 파일 및 vault 디렉터리 삭제
            try:
                db_p = t_settings.db_file
                if db_p.parent.name == str(tid) and db_p.parent.is_dir():
                    import shutil
                    shutil.rmtree(db_p.parent, ignore_errors=True)
                elif db_p.is_file():
                    db_p.unlink()

                vault_p = t_settings.vault_dir
                if vault_p.name == str(tid) and vault_p.is_dir():
                    import shutil
                    shutil.rmtree(vault_p, ignore_errors=True)
            except Exception as exc:
                log.warning("테마 #%d 디스크 삭제 중 오류 발생: %s", tid, exc)

        log.info("테마 #%d [%s] 삭제 완료 (purge=%s)", tid, theme.label, purge)
        return theme

    def reset_theme(self, theme_id: int | str) -> dict[str, Any]:
        """지식 관리자: 테마 고유 식별자(?theme=id) 및 메타데이터는 보존하고 내부 수집/그래프/볼트 데이터를 완전 초기화."""
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
        t_settings = self.get_settings_for_theme(tid)

        stats: dict[str, Any] = {
            "theme_id": tid,
            "theme_label": theme.label,
            "theme_icon": theme.icon,
            "db_cleared": False,
            "deleted_documents": 0,
            "deleted_entities": 0,
            "deleted_relations": 0,
            "vault_files_unlinked": 0,
        }

        # 1. DB 완전 초기화
        db_p = t_settings.db_file
        if db_p.is_file():
            conn = dbm.connect(db_p)
            try:
                try:
                    stats["deleted_documents"] = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                    stats["deleted_entities"] = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                    stats["deleted_relations"] = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
                except Exception:
                    pass

                dbm.reset_graph(conn)

                for tbl in (
                    "documents",
                    "document_snapshots",
                    "raw_inbox",
                    "extractions",
                    "proposals",
                    "jobs",
                    "refresh_queue",
                    "expand_queue",
                    "doc_shares",
                    "purged_tombstones",
                ):
                    try:
                        conn.execute(f"DELETE FROM {tbl}")
                    except sqlite3.OperationalError:
                        pass

                conn.commit()
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    conn.execute("VACUUM")
                except Exception:
                    pass
                stats["db_cleared"] = True
            finally:
                conn.close()
        else:
            conn = dbm.connect(db_p)
            try:
                dbm.init_db(conn)
                stats["db_cleared"] = True
            finally:
                conn.close()

        # 2. 볼트(Vault) 디스크 파일 정리
        vault_p = t_settings.vault_dir
        if vault_p.is_dir():
            unlinked = 0
            for f in vault_p.rglob("*.md"):
                if tid == 0 and "themes" in f.parts:
                    continue
                if f.is_file():
                    try:
                        f.unlink()
                        unlinked += 1
                    except Exception:
                        pass
            stats["vault_files_unlinked"] = unlinked

        # 3. 메타데이터 updated_at 갱신 (ID, 라벨, 초점 등은 불변 유지)
        theme.updated_at = time.time()
        self._save_registry()

        log.info(
            "테마 #%d [%s] 데이터 완전 초기화 완료 (URI: ?theme=%d, 문서: %d건, 엔티티: %d건, 관계: %d건, 볼트파일: %d개 삭제)",
            tid,
            theme.label,
            tid,
            stats["deleted_documents"],
            stats["deleted_entities"],
            stats["deleted_relations"],
            stats["vault_files_unlinked"],
        )
        return stats

    def get_settings_for_theme(
        self,
        theme_ref: int | str | None = None,
        base_settings: Settings | Any | None = None,
    ) -> Any:
        """지정된 테마의 db_path 및 vault_path 가 적용된 Settings 인스턴스를 반환."""
        theme = self.get_theme(theme_ref)
        base = base_settings or self.settings

        return self._settings_for_theme_info(theme, base)

    @staticmethod
    def _settings_for_theme_info(theme: ThemeInfo, base: Settings | Any) -> Any:
        """글로벌 설정을 유지하고 테마별 저장 경로 두 개만 덮어쓴다."""

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

    def active_theme_settings(
        self,
        base_settings: Settings | Any | None = None,
    ) -> list[tuple[ThemeInfo, Any]]:
        """활성 테마와 경로가 적용된 설정을 테마 ID 순서로 반환한다.

        멀티 테마가 꺼져 있으면 레지스트리 I/O 없이 기본 설정 한 개만 반환한다.
        멀티 테마가 켜져 있으면 레지스트리를 매번 다시 읽어 신규/삭제 테마를
        반영하며, 손상된 레지스트리 오류는 호출자에게 그대로 전파한다.
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

    def resolve_share_token(self, token: str) -> tuple[int, str, dict[str, Any]] | None:
        """등록된 테마 DB를 검색하여 공유 토큰의 (theme_id, document_id, doc_dict)를 자동 해소."""
        from .queries import document_detail

        def _fallback_across_themes(doc_id: str) -> tuple[int, str, dict[str, Any]] | None:
            targets = self.resolve_document_targets(doc_id=doc_id)
            for target in targets:
                tid = target.get("theme_id", 0)
                db_path = target.get("db_file")
                target_db = Path(db_path) if db_path else self.get_settings_for_theme(tid).db_file
                if not target_db.is_file():
                    continue
                try:
                    tconn = dbm.connect_existing(target_db, readonly=True)
                    try:
                        tdoc = document_detail(tconn, doc_id, include_hidden=True)
                        if tdoc:
                            return (tid, doc_id, tdoc)
                    finally:
                        tconn.close()
                except Exception:
                    continue
            return None

        if not getattr(self.settings, "multi_theme", False):
            abs_db = getattr(self.settings, "db_file", None) or Path("data/claire.db")
            if not abs_db.is_file():
                return None
            try:
                conn = dbm.connect_existing(abs_db, readonly=True)
                try:
                    doc_id = dbm.resolve_doc_share(conn, token)
                    if doc_id:
                        doc = document_detail(conn, doc_id, include_hidden=True)
                        if doc:
                            return (0, doc_id, doc)
                        fb = _fallback_across_themes(doc_id)
                        if fb:
                            return fb
                finally:
                    conn.close()
            except Exception:
                pass
            return None

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
                        fb = _fallback_across_themes(doc_id)
                        if fb:
                            return fb
                finally:
                    conn.close()
            except Exception:
                continue
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

        각 결과 딕셔너리에는 theme_id, theme_label, db_file 정보가 함께 포함된다.
        정확 일치(id, doc_id, token, share_token) 항목이 존재할 경우 해당 항목들을 우선 반환한다.
        """
        if not getattr(self.settings, "multi_theme", False):
            abs_db = getattr(self.settings, "db_file", None) or Path("data/claire.db")
            if not Path(abs_db).is_file():
                return []
            try:
                conn = dbm.connect_existing(Path(abs_db), readonly=True)
                try:
                    res = dbm.resolve_document_targets(
                        conn,
                        target=target,
                        doc_id=doc_id,
                        url=url,
                        canonical_url=canonical_url,
                        token=token,
                        pattern=pattern,
                        limit=limit,
                    )
                    default_t = self._themes.get(0, self._build_default_theme())
                    for r in res:
                        r["theme_id"] = 0
                        r["theme_label"] = default_t.label
                        r["db_file"] = str(abs_db)
                    return res
                finally:
                    conn.close()
            except Exception:
                return []

        self.reload()
        exact_matches: list[dict[str, Any]] = []
        url_matches: list[dict[str, Any]] = []
        pattern_matches: list[dict[str, Any]] = []

        for t in sorted(self._themes.values(), key=lambda x: x.id):
            abs_db = self.get_settings_for_theme(t.id).db_file
            if not Path(abs_db).is_file():
                continue
            try:
                conn = dbm.connect_existing(Path(abs_db), readonly=True)
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
                    for m in matched:
                        m["theme_id"] = t.id
                        m["theme_label"] = t.label
                        m["db_file"] = str(abs_db)
                        mb = m.get("matched_by", "")
                        if mb in ("id", "doc_id", "token", "share_token"):
                            exact_matches.append(m)
                        elif mb in ("url", "canonical_url"):
                            url_matches.append(m)
                        else:
                            pattern_matches.append(m)
                finally:
                    conn.close()
            except Exception:
                continue

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
