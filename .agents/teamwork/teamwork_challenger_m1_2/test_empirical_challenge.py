"""Adversarial Challenge Test Suite for Requirement R2 (Reserved Usernames Synchronization).

Empirically challenges:
1. Complete set coverage of RESERVED_USERNAMES (especially all Fedify and IdP terms).
2. Sub-fragment matching on compound usernames.
3. Case insensitivity and normalization.
4. Boundary lengths and syntax rules.
5. Absence of false positives on valid usernames.
6. Consistency with route surface and account routes contracts.
"""

from __future__ import annotations

import string
import pytest

from claire.accounts.username_policy import (
    RESERVED_USERNAMES,
    UsernameRejected,
    instance_reserved_labels,
    normalize_username,
    username_fragments,
    validate_username,
)
from tests.test_username_policy import SPEC_RESERVED

FEDIFY_IDP_TERMS = {
    "inbox", "outbox", "actor", "actors", "ap", "fedify", "federation", "nodeinfo",
    "oauth", "auth", "followers", "following", "shared_inbox", "sharedinbox",
    "webfinger", "idp", "oidc", "openid", "activitypub", "wellknown", "well_known",
}


def _reason(raw: str, **kwargs) -> str:
    with pytest.raises(UsernameRejected) as exc:
        validate_username(raw, **kwargs)
    return exc.value.reason


def test_challenge_all_fedify_idp_terms_present():
    """Verify that every single Fedify/IdP term is present in RESERVED_USERNAMES."""
    missing = FEDIFY_IDP_TERMS - RESERVED_USERNAMES
    assert not missing, f"Missing Fedify/IdP reserved terms: {missing}"


def test_challenge_reserved_usernames_and_spec_exact_match():
    """Verify bidirectional synchronization between implementation and spec test."""
    assert RESERVED_USERNAMES == frozenset(SPEC_RESERVED)
    assert FEDIFY_IDP_TERMS.issubset(RESERVED_USERNAMES)


@pytest.mark.parametrize("term", sorted(FEDIFY_IDP_TERMS))
def test_challenge_exact_term_rejection(term: str):
    """Verify each exact Fedify/IdP term is rejected appropriately."""
    if len(term) < 3:
        assert _reason(term) == "syntax"
        assert _reason(term.upper()) == "syntax"
    else:
        assert _reason(term) == "reserved"
        assert _reason(term.upper()) == "reserved"
        assert _reason(term.capitalize()) == "reserved"


@pytest.mark.parametrize("term", sorted(FEDIFY_IDP_TERMS))
def test_challenge_compound_prefix_suffix_number_fragments(term: str):
    """Test compound usernames with reserved fragments."""
    # When term has underscore (like shared_inbox or well_known), check behavior
    # Prefix compound
    name_pre = f"{term}_foo"
    if len(name_pre) <= 30:
        if term == "well_known":
            # well_known splits into ("well", "known", "foo"), neither in RESERVED_USERNAMES
            assert validate_username(name_pre) == name_pre
        else:
            assert _reason(name_pre) == "reserved"

    # Suffix compound
    name_suf = f"foo_{term}"
    if len(name_suf) <= 30:
        if term == "well_known":
            assert validate_username(name_suf) == name_suf
        else:
            assert _reason(name_suf) == "reserved"

    # Numeric suffix compound
    name_num = f"{term}42"
    if len(name_num) <= 30:
        if term == "well_known":
            # "well_known42" splits into ("well", "known")
            assert validate_username(name_num) == name_num
        else:
            assert _reason(name_num) == "reserved"

    # Infix compound
    name_mid = f"x_{term}_y"
    if len(name_mid) <= 30:
        if term == "well_known":
            assert validate_username(name_mid) == name_mid
        else:
            assert _reason(name_mid) == "reserved"


def test_challenge_prompt_explicit_examples():
    """Test explicit examples from challenge prompt: inbox_123, my_oauth_app, actor_admin."""
    assert _reason("inbox_123") == "reserved"
    assert _reason("INBOX_123") == "reserved"
    assert _reason("Inbox_123") == "reserved"

    assert _reason("my_oauth_app") == "reserved"
    assert _reason("MY_OAUTH_APP") == "reserved"
    assert _reason("My_OAuth_App") == "reserved"

    assert _reason("actor_admin") == "reserved"
    assert _reason("ACTOR_ADMIN") == "reserved"
    assert _reason("Actor_Admin") == "reserved"


def test_challenge_ap_short_reserved_word_compound_handling():
    """'ap' is 2 letters (fails syntax alone), but in compound must trigger 'reserved'."""
    assert _reason("ap") == "syntax"
    assert _reason("ap_node") == "reserved"
    assert _reason("node_ap") == "reserved"
    assert _reason("ap42") == "reserved"
    assert _reason("my_ap_hub") == "reserved"
    assert _reason("AP_NODE") == "reserved"
    assert _reason("Node_Ap") == "reserved"


def test_challenge_well_known_edge_case_behavior():
    """Document well_known vs wellknown behavior under fragment splitting.

    'well_known' exact matches RESERVED_USERNAMES -> rejected with 'reserved'.
    'wellknown' exact matches RESERVED_USERNAMES -> rejected with 'reserved'.
    'wellknown_hub' fragment 'wellknown' in RESERVED_USERNAMES -> rejected with 'reserved'.
    'well_known_hub' fragments are ('well', 'known', 'hub') -> allowed because 'well' and 'known' are not in RESERVED_USERNAMES.
    """
    assert _reason("well_known") == "reserved"
    assert _reason("WELL_KNOWN") == "reserved"
    assert _reason("Well_Known") == "reserved"
    assert _reason("wellknown") == "reserved"
    assert _reason("wellknown_hub") == "reserved"
    assert validate_username("well_known_hub") == "well_known_hub"


def test_challenge_shared_inbox_compound_handling():
    """'shared_inbox' has fragment 'inbox', which is independently reserved."""
    assert _reason("shared_inbox") == "reserved"
    assert _reason("sharedinbox") == "reserved"
    # Even though 'shared' is not reserved, 'inbox' IS reserved, so compound is always rejected:
    assert _reason("shared_inbox_hub") == "reserved"
    assert _reason("my_shared_inbox") == "reserved"
    assert _reason("shared_inbox42") == "reserved"


@pytest.mark.parametrize(
    "valid_name",
    [
        # Substrings of 'ap' without delimiters
        "apple", "april", "laptop", "captain", "grape", "paper", "snap", "tap", "wrap", "shape",
        # Substrings of 'auth' without delimiters
        "author", "authority", "authentic", "authenticate",
        # Substrings of 'actor' without delimiters
        "reactor", "factor", "refactor", "tractor", "contractor",
        # Substrings of 'inbox' without delimiters
        "spinbox",
        # Substrings of 'bot' without delimiters
        "robot", "bottle", "bottom", "botanical",
        # Substrings of 'post' without delimiters
        "poster", "posture", "postscript",
        # Substrings of 'host' without delimiters
        "ghost", "hostel", "hostile",
        # Substrings of 'mod' without delimiters
        "modern", "modify", "model", "modular", "commodity",
        # Substrings of 'all' without delimiters
        "allison", "shallow", "tall", "ball", "wallet",
        # Substrings of 'me' without delimiters
        "memo", "memory", "game", "name", "home", "flame", "theme",
        # Substrings of 'info' without delimiters
        "inform", "information", "rainforest",
        # Substrings of 'fed' without delimiters
        "federal", "federate",
        # Substrings of 'node' without delimiters
        "anode", "cathode", "nodule",
        # Standard Korean Romanized and common usernames
        "kim", "lee", "park", "choi", "jeong", "kang", "cho", "yoon", "jang", "lim",
        "han", "oh_min", "seo", "shin", "kwon", "hwang", "ahn", "song", "jeon", "hong",
        "kim_lee", "alex_smith", "john_doe", "charlie_brown",
    ],
)
def test_challenge_valid_usernames_are_not_falsely_rejected(valid_name: str):
    """Verify that legitimate usernames are NOT falsely rejected."""
    assert validate_username(valid_name) == valid_name
    assert validate_username(valid_name.upper()) == valid_name.lower()


@pytest.mark.parametrize(
    ("raw", "expected_reason"),
    [
        # Length boundaries
        ("a", "syntax"),
        ("ab", "syntax"),
        ("a" * 3, None),
        ("a" * 30, None),
        ("a" * 31, "syntax"),
        # Boundary length with reserved fragment
        ("a" * 24 + "_inbox", "reserved"),  # 30 chars
        ("a" * 25 + "_inbox", "syntax"),    # 31 chars
        ("inbox_" + "a" * 24, "reserved"),  # 30 chars
        ("inbox_" + "a" * 25, "syntax"),    # 31 chars
        # Boundary syntax
        ("_inbox", "syntax"),
        ("inbox_", "syntax"),
        ("inbox__123", "syntax"),
        ("inbox-123", "syntax"),
        ("inbox.123", "syntax"),
        ("1inbox", "syntax"),
        ("inbox 123", "syntax"),
        (" inbox", "syntax"),
        ("inbox ", "syntax"),
        ("inbox\n", "syntax"),
    ],
)
def test_challenge_boundary_lengths_and_syntax(raw: str, expected_reason: str | None):
    if expected_reason is None:
        assert validate_username(raw) == raw
    else:
        assert _reason(raw) == expected_reason


def test_challenge_account_store_rejects_fedify_idp_reserved(tmp_path):
    """Verify that AccountStore enforces the reserved policy on create_member and validate_username."""
    from claire.store import accounts as acc
    from claire.store import db as dbm

    db_path = tmp_path / "claire.db"
    conn = dbm.connect(db_path)
    try:
        dbm.init_db(conn)
        store = acc.AccountStore(conn)

        for term in ["inbox", "outbox", "actor", "oauth", "fedify", "nodeinfo", "shared_inbox"]:
            with pytest.raises(UsernameRejected) as exc:
                store.validate_username(term)
            assert exc.value.reason == "reserved"

            with pytest.raises(UsernameRejected) as exc2:
                store.create_member(username=term, display_name="Test")
            assert exc2.value.reason == "reserved"

        for compound in ["inbox_123", "my_oauth_app", "actor_admin"]:
            with pytest.raises(UsernameRejected) as exc:
                store.validate_username(compound)
            assert exc.value.reason == "reserved"

            with pytest.raises(UsernameRejected) as exc2:
                store.create_member(username=compound, display_name="Test")
            assert exc2.value.reason == "reserved"
    finally:
        conn.close()

