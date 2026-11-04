"""네트워크 모드 준비 검사 (설계 §4.2, §17.2, G1–G9).

네트워크 모드 전환(P2 L2-f)과 ``claire instance check --network``가 같은 검사를 쓴다.
G1–G3과 G6은 코드 성질이므로 릴리스 상수 ``NETWORK_MODE_AVAILABLE``(O7)로 지킨다.
실행 중 검사는 이 상수와 함께 G4(설정), G5(서버 등급), G7(관리자 상태), G8(ROUTE_POLICY 항목), G9(쿠키 설정)를 확인한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..api import security
from ..api.host_router import HostRoles
from ..store import accounts as accounts_store
from . import server_tier
from ..store import auth_db

__all__ = [
    "NETWORK_MODE_AVAILABLE",
    "ReadinessItem",
    "ReadinessReport",
    "check_readiness",
]

# P7 완료 후 네트워크 모드 전환이 공식 활성화된다(§17.2, O7).
NETWORK_MODE_AVAILABLE: bool = True


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    id: str
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    items: tuple[ReadinessItem, ...]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "passed": self.passed,
        }

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


def check_readiness(conn: sqlite3.Connection, settings: Any) -> ReadinessReport:
    """G1–G9 검사를 실행하고 종합 결과를 돌려준다(§17.2). 하나라도 실패하면 report.passed는 False다."""
    items: list[ReadinessItem] = []

    # G1: 모든 진입점에서 테마 조회가 엄격하다 (S2, P0 완료, CI 성질)
    items.append(
        ReadinessItem(
            id="G1",
            name="엄격한 테마 조회 (S2)",
            passed=NETWORK_MODE_AVAILABLE,
            detail="모든 진입점에서 테마 조회가 엄격합니다" if NETWORK_MODE_AVAILABLE else "CI 게이트: 네트워크 모드 릴리스 필요 (NETWORK_MODE_AVAILABLE=False)",
        )
    )

    # G2: theme_id 명시 AST 테스트가 통과한다 (S3, P0 완료, CI 성질)
    items.append(
        ReadinessItem(
            id="G2",
            name="theme_id 명시 AST 테스트 (S3)",
            passed=NETWORK_MODE_AVAILABLE,
            detail="theme_id 명시 AST 테스트 통과" if NETWORK_MODE_AVAILABLE else "CI 게이트: 네트워크 모드 릴리스 필요 (NETWORK_MODE_AVAILABLE=False)",
        )
    )

    # G3: 계정 격리 행렬 테스트가 통과한다 (P3 L3-d, CI 성질)
    items.append(
        ReadinessItem(
            id="G3",
            name="계정 격리 행렬 테스트",
            passed=NETWORK_MODE_AVAILABLE,
            detail="계정 격리 행렬 테스트 통과" if NETWORK_MODE_AVAILABLE else "CI 게이트: 네트워크 모드 릴리스 필요 (NETWORK_MODE_AVAILABLE=False)",
        )
    )

    # G4: S1 조치가 끝났다. 운영 환경에서는 usercontent 호스트가 설정되어 있다 (N5)
    env = str(getattr(settings, "environment", "development") or "").strip().lower()
    usercontent_fqdn = str(getattr(settings, "usercontent_fqdn", "") or "").strip()
    if env == "production":
        g4_passed = bool(usercontent_fqdn)
        g4_detail = f"운영 환경 usercontent FQDN 설정됨 ({usercontent_fqdn})" if g4_passed else "운영 환경에서는 CLAIRE_USERCONTENT_FQDN 설정이 필요합니다"
    else:
        g4_passed = True
        g4_detail = f"개발/테스트 환경 ({env})"
    items.append(
        ReadinessItem(
            id="G4",
            name="usercontent 호스트 설정 (N5)",
            passed=g4_passed,
            detail=g4_detail,
        )
    )

    # G5: 프로바이더 정책 적용 지점이 모두 강제되고, 서버 등급이 정해져 있다 (none 포함)
    # 저장된 값이 ServerTier.from_dict로 유효해야 통과한다. 미설정·깨진 값은 실패다(실행 중 집행은 깨진 값을 none으로 본다).
    raw_tier = server_tier.read_stored_server_tier(conn)
    if raw_tier is None:
        g5_passed = False
        g5_detail = "서버 등급이 정해지지 않았습니다 (/admin/instance에서 none 포함 저장 필요)"
    else:
        try:
            tier = server_tier.parse_server_tier(raw_tier)
        except ValueError as exc:
            g5_passed = False
            g5_detail = f"저장된 서버 등급이 유효하지 않습니다 ({exc})"
        else:
            g5_passed = True
            g5_detail = f"서버 등급 설정됨 (provider={tier.provider})"
    items.append(
        ReadinessItem(
            id="G5",
            name="서버 등급 설정 (none 포함)",
            passed=g5_passed,
            detail=g5_detail,
        )
    )

    # G6: 공용 파일 상태와 번들·백업 경계를 계정으로 거른다 (P3 G6, CI 성질)
    items.append(
        ReadinessItem(
            id="G6",
            name="공용 파일 상태와 번들·백업 계정 격리",
            passed=NETWORK_MODE_AVAILABLE,
            detail="공용 파일·번들·백업 계정 격리 확인됨" if NETWORK_MODE_AVAILABLE else "CI 게이트: 네트워크 모드 릴리스 필요 (NETWORK_MODE_AVAILABLE=False)",
        )
    )

    # G7: 관리자 USERNAME과 로컬 비밀번호(또는 IdP 연결)가 설정되어 있다
    store = accounts_store.AccountStore(conn)
    try:
        admin = store.get(accounts_store.ADMIN_ACCOUNT_ID)
        has_username = (admin.username is not None and admin.status == "active")
    except accounts_store.AccountNotFound:
        admin = None
        has_username = False

    has_password = False
    data_dir = getattr(settings, "data_dir", None)
    if data_dir:
        auth_path = auth_db.auth_db_path(Path(data_dir))
        if auth_path.is_file():
            auth_conn = auth_db.connect_auth_db(auth_path)
            try:
                has_password = auth_db.has_password(auth_conn, accounts_store.ADMIN_ACCOUNT_ID)
            finally:
                auth_conn.close()

    g7_passed = bool(has_username and has_password)
    if g7_passed:
        g7_detail = f"관리자 USERNAME(@{admin.username if admin else ''}) 및 비밀번호 설정됨"
    elif not has_username:
        g7_detail = "관리자 USERNAME이 설정되지 않았거나 활성 상태가 아닙니다"
    else:
        g7_detail = "관리자 비밀번호가 설정되지 않았습니다"
    items.append(
        ReadinessItem(
            id="G7",
            name="관리자 USERNAME 및 비밀번호 설정",
            passed=g7_passed,
            detail=g7_detail,
        )
    )

    # G8: /syndication의 public 규칙이 해제되었다
    syndication_rule = security.ROUTE_POLICY.get(("GET", "/syndication"))
    g8_passed = bool(syndication_rule is not None and syndication_rule.access != "public")
    items.append(
        ReadinessItem(
            id="G8",
            name="/syndication public 해제 여부",
            passed=g8_passed,
            detail="syndication public 해제 완료" if g8_passed else "/syndication 라우트가 아직 public으로 열려 있습니다 (P6 예정)",
        )
    )

    # G9: 역할 호스트가 하나라도 있으면 운영 환경 세션 쿠키가 __Host- 전용이다(레거시 쿠키 폴백 제거)
    fallback_removed = getattr(security, "LEGACY_COOKIE_FALLBACK_REMOVED", False)
    try:
        roles = HostRoles.from_settings(settings)
        has_role_hosts = (roles.usercontent is not None or roles.media is not None)
    except Exception:
        has_role_hosts = False

    if env == "production" and has_role_hosts:
        g9_passed = fallback_removed
        g9_detail = "운영 환경 __Host- 전용 쿠키 (레거시 폴백 제거됨)" if g9_passed else "운영 환경에서 레거시 세션 쿠키(claire_session) 폴백이 아직 활성화되어 있습니다 (P3 G9 예정)"
    else:
        g9_passed = fallback_removed
        g9_detail = "__Host- 전용 쿠키" if g9_passed else "레거시 쿠키 폴백 미제거 (P3 G9 예정)"
    items.append(
        ReadinessItem(
            id="G9",
            name="__Host- 세션 쿠키 전용 (레거시 쿠키 폴백 제거)",
            passed=g9_passed,
            detail=g9_detail,
        )
    )

    all_passed = all(item.passed for item in items)
    return ReadinessReport(items=tuple(items), passed=all_passed)
