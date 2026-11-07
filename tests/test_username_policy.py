"""USERNAME 규칙 (설계 §8.1) 결정론 테스트.

퇴역(``retired``)·중복(``taken``)은 `store.accounts.AccountStore` 몫이라 여기서 다루지 않는다.
"""

from __future__ import annotations

import pytest

from claire.accounts.username_policy import (
    RESERVED_USERNAMES,
    UsernameRejected,
    instance_reserved_labels,
    normalize_username,
    username_fragments,
    validate_username,
)

# §8.1-2 목록을 그대로 옮긴다. 모듈 상수와 정확히 같아야 한다(빠짐·추가 없음).
SPEC_RESERVED = {
    # 운영·권한
    "admin", "administrator", "root", "sysadmin", "superuser", "owner", "operator",
    "system", "sysop", "moderator", "mod", "staff", "official", "team",
    # 지원·연락
    "support", "help", "info", "contact", "security", "abuse", "postmaster",
    "hostmaster", "webmaster", "noc", "billing",
    # 일반
    "user", "users", "guest", "anonymous", "nobody", "everyone", "all", "public",
    "private", "me", "self", "null", "undefined", "test", "demo",
    # 서비스
    "claire", "api", "www", "mail", "mcp", "bot", "media", "usercontent", "explore",
    "invite", "settings", "syndication", "prototype",
}


def _reason(raw: str, **kwargs) -> str:
    with pytest.raises(UsernameRejected) as exc:
        validate_username(raw, **kwargs)
    return exc.value.reason


# ── 거부 사유 계약 ──────────────────────────────────────────────


def test_rejected_is_value_error_with_reason():
    err = UsernameRejected("taken")
    assert isinstance(err, ValueError)
    assert err.reason == "taken"


@pytest.mark.parametrize("reason", ["syntax", "reserved", "retired", "taken"])
def test_reject_reasons_are_preserved(reason):
    assert UsernameRejected(reason).reason == reason


# ── 문법 (§8.1-1) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "kim",  # 3자 하한
        "k" * 30,  # 30자 상한
        "kim_lee",
        "kim2",
        "k1m_9",
        "badminton",
    ],
)
def test_valid_names_pass(name):
    assert validate_username(name) == name


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ki",  # 2자
        "k" * 31,  # 31자
        "1kim",  # 숫자 시작
        "_kim",  # 밑줄 시작
        "kim__lee",  # 밑줄 연속
        "kim_",  # 밑줄로 끝남
        "kim-lee",
        "kim.lee",
        "kim lee",
        " kim",  # 앞 공백을 잘라 내지 않는다(C1)
        "kim ",  # 뒤 공백
        "kim\n",  # `$`가 끝 개행 앞에서 맞는 함정
        "kim\t",
    ],
)
def test_syntax_rejected(raw):
    assert _reason(raw) == "syntax"


def test_uppercase_input_is_normalized():
    assert normalize_username("KimLee") == "kimlee"
    assert validate_username("KimLee") == "kimlee"
    assert validate_username("K" * 30) == "k" * 30


def test_uppercase_reserved_is_rejected_after_normalization():
    assert _reason("ADMIN") == "reserved"
    assert _reason("Kim_Admin") == "reserved"


def test_normalize_does_not_trim():
    assert normalize_username(" kim ") == " kim "


# ── 비ASCII 사칭 ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "Kim",  # KELVIN SIGN: lower() → "kim"
        "ｋim",  # 전각 k
        "ａdmin",  # 전각 a + dmin
        "İnfo",  # 터키어 İ: lower() → "i̇nfo"
        "аdmin",  # 키릴 а
        "kimé",
        "kim​",  # 폭 없는 공백
    ],
)
def test_non_ascii_rejected_as_syntax_before_lower(raw):
    with pytest.raises(UsernameRejected) as exc:
        normalize_username(raw)
    assert exc.value.reason == "syntax"
    assert _reason(raw) == "syntax"


def test_kelvin_sign_would_fold_to_ascii():
    # 거부 순서가 lower() 전이어야 하는 이유를 고정한다.
    assert "Kim".lower() == "kim"


# ── 예약 이름 (§8.1-2) ─────────────────────────────────────────


def test_reserved_set_matches_spec_exactly():
    assert RESERVED_USERNAMES == frozenset(SPEC_RESERVED)


@pytest.mark.parametrize("name", sorted(SPEC_RESERVED))
def test_every_reserved_name_is_rejected(name):
    # 3자 미만(`me`)은 문법에서 먼저 막힌다. 순서는 문법 → 예약.
    expected = "syntax" if len(name) < 3 else "reserved"
    assert _reason(name) == expected
    assert _reason(name.upper()) == expected


# ── 예약 조각 (§8.1-3) ─────────────────────────────────────────


@pytest.mark.parametrize("name", ["admin1", "admin_kim", "kim_admin", "administrator2"])
def test_spec_fragment_examples_rejected(name):
    assert _reason(name) == "reserved"


def test_spec_fragment_example_badminton_passes():
    assert validate_username("badminton") == "badminton"


@pytest.mark.parametrize(
    "name",
    [
        "adminkim",  # admin으로 시작하는 조각
        "admins",
        "kim_adminx",
        "kim2admin",  # 숫자로 나뉨
        "kim_root",
        "root42",
        "me_kim",  # 2자 예약 이름도 조각으로는 막힌다
        "kim9me",
        "all_star",
        "mod_kim",
        "kim_claire_lee",
        "test1",
        "support_team",
    ],
)
def test_fragment_rule_rejects(name):
    assert _reason(name) == "reserved"


@pytest.mark.parametrize(
    "name",
    [
        "badminton",
        "kimadmin",  # 조각 "kimadmin": 예약 이름도 아니고 admin으로 시작하지도 않는다
        "rooted",
        "teammate",
        "allison",
        "memo",
        "kim_lee",
    ],
)
def test_fragment_rule_allows(name):
    assert validate_username(name) == name


@pytest.mark.parametrize(
    ("name", "fragments"),
    [
        ("kim_admin2", ("kim", "admin")),
        ("kim", ("kim",)),
        ("a1b2c", ("a", "b", "c")),
        ("kim_99_lee", ("kim", "lee")),
        ("kim123", ("kim",)),
    ],
)
def test_username_fragments(name, fragments):
    assert username_fragments(name) == fragments


# ── 인스턴스 예약 (§8.1-4) ─────────────────────────────────────


def test_instance_labels_from_host():
    assert instance_reserved_labels(["claire.example.com"]) == frozenset(
        {"claire", "example", "com"}
    )


def test_instance_labels_strip_port_lower_and_skip_empty():
    labels = instance_reserved_labels(
        ["Bible.Example.ORG:8443", "", "media.example.org.", "localhost:8000", "usercontent-x.net"]
    )
    assert labels == frozenset(
        {"bible", "example", "org", "media", "localhost", "usercontent-x", "net"}
    )


def test_instance_labels_empty_input():
    assert instance_reserved_labels([]) == frozenset()


def test_instance_reserved_exact_match_only():
    reserved = instance_reserved_labels(["bible.mysite.org:8443"])
    assert _reason("bible", instance_reserved=reserved) == "reserved"
    assert _reason("MySite", instance_reserved=reserved) == "reserved"
    # 인스턴스 예약은 조각 규칙을 따르지 않는다.
    assert validate_username("bible_kim", instance_reserved=reserved) == "bible_kim"
    assert validate_username("mysite2", instance_reserved=reserved) == "mysite2"
    assert validate_username("bibles", instance_reserved=reserved) == "bibles"


def test_instance_reserved_admin_added_names():
    assert _reason("kimlee", instance_reserved=frozenset({"kimlee"})) == "reserved"
    assert validate_username("kimlee") == "kimlee"


# ── 검사 순서 ──────────────────────────────────────────────────


def test_syntax_checked_before_reserved():
    assert _reason("admin_") == "syntax"
    assert _reason("admin__kim") == "syntax"
    assert _reason("Kim", instance_reserved=frozenset({"kim"})) == "syntax"
