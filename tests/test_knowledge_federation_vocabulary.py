"""Wire contract of the federation of shared knowledge (KNOWLEDGE_FEDERATION_DESIGN.md unit KF0).

The fixtures in ``tests/fixtures/knowledge_federation/`` are the contract. Server A (``a.example``) has the account ``alice`` and the server actor; server B (``b.example``) has the shared knowledge base. Each test of "the two sides" builds a value with the data of one server and validates it as the other server does.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from claire.knowledge_federation import vocabulary as v

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "knowledge_federation"
STATIC_NS = ROOT / "src" / "claire" / "static" / "ns"

A = "https://a.example"
B = "https://b.example"
ALICE = f"{A}/ap/actors/1846000000000000001"
SERVER_A = f"{A}/ap/actors/server"
KB = f"{B}/ap/kb/1846000000000000100"
KB_IRIS = {"id": KB, "url": f"{B}/kb/1846000000000000100/", "inbox": f"{KB}/inbox", "outbox": f"{KB}/outbox", "followers": f"{KB}/followers", "documents": f"{KB}/documents"}
T0 = 1794830400  # 2026-11-16T12:00:00Z

KB_ROW = {
    "label": "Ada Lovelace and the Analytical Engine",
    "description": "Documents about the first programs for a mechanical computer.",
    "knowledge_type": "real",
    "documents": 12,
    "entities": 87,
    "relations": 140,
    "signature": {"entities": [["person", "ada lovelace", 9], ["artifact", "analytical engine", 7], ["person", "charles babbage", 6]], "visible_documents": 12, "computed_at": T0 - 3600},
    "accepts_automatic": True,
    "updated": T0 - 1800,
}
EXTRACTION_ROW = {
    "summary": "Ada Lovelace wrote notes about the Analytical Engine of Charles Babbage.",
    "key_claims": ["Note G has an algorithm for Bernoulli numbers."],
    "entities": [
        {"name": "Ada Lovelace", "type": "person", "aliases": ["Augusta Ada King"], "observations": ["Wrote Note G."], "proposed_type": None},
        {"name": "Analytical Engine", "type": "artifact", "aliases": [], "observations": ["A design for a mechanical computer."]},
    ],
    "relations": [{"source": "Ada Lovelace", "target": "Analytical Engine", "type": "wrote_about", "proposed_type": None}],
}
AUTHORITY_IDS = {"Ada Lovelace": ["wd:Q7259"]}
DOC_ROW = {"title": "Notes on the Analytical Engine", "content_html": "<p>Ada Lovelace wrote notes about the Analytical Engine.</p>", "canonical_url": "https://source.example/notes", "content_hash": "3f" * 32, "published": T0, "updated": None}
ROOT_DOC = f"{A}/ap/documents/1846000000000000300"
COPY_DOC = f"{B}/ap/documents/1846000000000000400"


def _extraction() -> dict[str, Any]:
    return v.build_extraction(EXTRACTION_ROW, AUTHORITY_IDS)


def _root_document() -> dict[str, Any]:
    """The document of alice in her personal knowledge base on A: the root."""
    return v.build_document(DOC_ROW, {"id": ROOT_DOC, "attributed_to": ALICE, "origin": None, "root": ROOT_DOC}, _extraction())


def _copy_document() -> dict[str, Any]:
    """The copy on B after the approval: a new IRI, the credit of alice, and the origin."""
    return v.build_document({**DOC_ROW, "updated": T0 + 86400}, {"id": COPY_DOC, "attributed_to": ALICE, "origin": ROOT_DOC, "root": ROOT_DOC}, _extraction())


def built_objects() -> dict[str, Any]:
    return {
        "knowledge_base": v.with_context(v.build_knowledge_base(KB_ROW, KB_IRIS)),
        "document": v.with_context(_copy_document()),
        "extraction": v.with_context(_extraction()),
        "contribution": v.with_context(v.build_contribution(document=_root_document(), attributed_to=ALICE, knowledge_base=KB, contribution_id=f"{B}/ap/contributions/1846000000000000500", automatic=False)),
    }


def built_activities() -> dict[str, Any]:
    follow = v.build_follow(activity_id=f"{SERVER_A}#follows/1", server_actor=SERVER_A, knowledge_base=KB)
    offer = v.build_offer(activity_id=f"{ALICE}#offers/1", contribution=v.build_contribution(document=_root_document(), attributed_to=ALICE, knowledge_base=KB, contribution_id=None, automatic=False), capability=None)
    grant = v.build_grant(activity_id=f"{KB}#grants/1", knowledge_base=KB, account_actor=ALICE, published=T0, end_time=T0 + 182 * 86400)
    return {
        "follow": follow,
        "accept_follow": v.build_accept_follow(activity_id=f"{KB}#accepts/follows/1", follow=follow),
        "undo_follow": v.build_undo_follow(activity_id=f"{SERVER_A}#follows/1/undo", follow=follow),
        "update_knowledge_base": v.build_update_knowledge_base(activity_id=f"{KB}#updates/1794830400000", knowledge_base=v.build_knowledge_base(KB_ROW, KB_IRIS)),
        "delete_knowledge_base": v.build_delete_knowledge_base(activity_id=f"{KB}#delete", knowledge_base=KB, followers=KB_IRIS["followers"]),
        "update_document": v.build_update_document(activity_id=f"{COPY_DOC}#updates/1794916800000", knowledge_base=KB, followers=KB_IRIS["followers"], document=_copy_document()),
        "delete_document": v.build_delete_document(activity_id=f"{COPY_DOC}#delete", knowledge_base=KB, followers=KB_IRIS["followers"], document=COPY_DOC, reason_code="off_subject"),
        "offer_contribution": offer,
        "accept_offer": v.build_accept_offer(activity_id=f"{KB}#accepts/offers/1", knowledge_base=KB, offer=offer["id"], offer_actor=ALICE, result=f"{B}/ap/contributions/1846000000000000500"),
        "reject_offer": v.build_reject_offer(activity_id=f"{KB}#rejects/offers/1", knowledge_base=KB, offer=offer["id"], offer_actor=ALICE, reason_code="duplicate"),
        "grant": grant,
        "revoke": v.build_revoke(activity_id=f"{KB}#grants/1/revoke", knowledge_base=KB, account_actor=ALICE, grant=grant["id"]),
    }


def _automatic_offer() -> dict[str, Any]:
    """An ``Offer`` that the automatic rule of alice sent (unit KF5): the contribution has ``claire:automatic``."""
    return v.build_offer(activity_id=f"{ALICE}#offers/3", contribution=v.build_contribution(document=_root_document(), attributed_to=ALICE, knowledge_base=KB, contribution_id=None, automatic=True), capability=None)


def _edit(value: Any, path: str, new: Any = ...) -> Any:
    """Give a copy of ``value`` where the field at ``path`` (keys with ``/``) has the value ``new``, or is removed when ``new`` is not given."""
    value = copy.deepcopy(value)
    *parents, last = path.split("/")
    node = value
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if new is ...:
        del node[last]
    else:
        node[last] = new
    return value


def invalid_values() -> dict[str, dict[str, Any]]:
    """The important refusals: the name of the fixture, the error code, the field, and the value."""
    objects, activities = built_objects(), built_activities()
    offer = activities["offer_contribution"]
    with_grant = {**offer, v.CAPABILITY: f"{KB}#grants/1"}
    automatic = _automatic_offer()
    cases = {
        "object_not_a_json_object": ("object", "not_an_object", "", ["Article"]),
        "object_without_context": ("object", "missing_context", "@context", _edit(objects["knowledge_base"], "@context")),
        "object_unsupported_type": ("object", "unsupported_type", "type", _edit(objects["document"], "type", "Note")),
        "knowledge_base_without_knowledge_type": ("object", "missing_field", v.KNOWLEDGE_TYPE, _edit(objects["knowledge_base"], v.KNOWLEDGE_TYPE)),
        "knowledge_base_invalid_knowledge_type": ("object", "invalid_knowledge_type", v.KNOWLEDGE_TYPE, _edit(objects["knowledge_base"], v.KNOWLEDGE_TYPE, "mixed")),
        "knowledge_base_inbox_on_other_server": ("object", "origin_mismatch", "inbox", _edit(objects["knowledge_base"], "inbox", "https://c.example/inbox")),
        "knowledge_base_signature_too_large": ("object", "signature_too_large", f"{v.SIGNATURE}.entities", _edit(objects["knowledge_base"], f"{v.SIGNATURE}/entities", [["person", f"name {n}", 1] for n in range(v.MAX_SIGNATURE_ENTITIES + 1)])),
        "document_without_root": ("object", "missing_field", v.ROOT, _edit(objects["document"], v.ROOT)),
        "document_invalid_content_hash": ("object", "invalid_content_hash", v.CONTENT_HASH, _edit(objects["document"], v.CONTENT_HASH, "3F3F")),
        "document_time_without_zone": ("object", "invalid_time", "published", _edit(objects["document"], "published", "2026-11-16T12:00:00")),
        "extraction_entity_without_norm_name": ("object", "missing_field", f"{v.ENTITIES}.0.norm_name", _edit(objects["extraction"], f"{v.ENTITIES}/0/norm_name")),
        "extraction_entity_unknown_field": ("object", "unknown_field", f"{v.ENTITIES}.0.merge_into", _edit(objects["extraction"], f"{v.ENTITIES}/0/merge_into", "ent_1")),
        "extraction_relation_unknown_entity": ("object", "unknown_relation_entity", f"{v.RELATIONS}.0.target", _edit(objects["extraction"], f"{v.RELATIONS}/0/target", "Difference Engine")),
        "contribution_credit_of_other_actor": ("object", "actor_mismatch", f"{v.DOCUMENT}.attributedTo", _edit(objects["contribution"], "attributedTo", f"{A}/ap/actors/1846000000000000002")),
        "activity_unsupported_type": ("activity", "unsupported_type", "type", _edit(activities["follow"], "type", "Announce")),
        "activity_id_on_other_server": ("activity", "origin_mismatch", "id", _edit(activities["follow"], "id", "https://c.example/follows/1")),
        "activity_actor_not_an_iri": ("activity", "invalid_iri", "actor", _edit(activities["follow"], "actor", "acct:claire@a.example")),
        "follow_without_recipient": ("activity", "missing_recipient", "to", _edit(activities["follow"], "to", [f"{B}/ap/actors/server"])),
        "accept_follow_by_other_actor": ("activity", "actor_mismatch", "object.object", _edit(activities["accept_follow"], "object/object", f"{B}/ap/kb/1846000000000000101")),
        "update_document_without_extraction": ("activity", "missing_field", f"object.{v.EXTRACTION_OF}", _edit(activities["update_document"], f"object/{v.EXTRACTION_OF}")),
        "update_document_of_other_server": ("activity", "origin_mismatch", "object.id", _edit(activities["update_document"], "object/id", f"{A}/ap/documents/1")),
        "update_knowledge_base_of_other_actor": ("activity", "actor_mismatch", "object.id", _edit(activities["update_knowledge_base"], "object/id", f"{B}/ap/kb/1846000000000000101")),
        "update_unsupported_object": ("activity", "unsupported_object", "object.type", _edit(activities["update_document"], "object/type", "Note")),
        "delete_document_without_reason_code": ("activity", "missing_field", v.REASON_CODE, _edit(activities["delete_document"], v.REASON_CODE)),
        "delete_document_invalid_reason_code": ("activity", "invalid_reason_code", v.REASON_CODE, _edit(activities["delete_document"], v.REASON_CODE, "legal")),
        "offer_target_mismatch": ("activity", "target_mismatch", "target", _edit(offer, "target", f"{B}/ap/kb/1846000000000000101")),
        "offer_for_other_actor": ("activity", "actor_mismatch", "object.attributedTo", _edit(offer, "actor", f"{A}/ap/actors/1846000000000000002")),
        "offer_capability_of_other_server": ("activity", "origin_mismatch", v.CAPABILITY, _edit(with_grant, v.CAPABILITY, "https://c.example/grants/1")),
        "accept_offer_without_result": ("activity", "missing_field", "result", _edit(activities["accept_offer"], "result")),
        "accept_offer_result_on_other_server": ("activity", "origin_mismatch", "result", _edit(activities["accept_offer"], "result", f"{A}/ap/contributions/1")),
        "reject_offer_without_reason_code": ("activity", "missing_field", v.REASON_CODE, _edit(activities["reject_offer"], v.REASON_CODE)),
        "grant_invalid_role": ("activity", "invalid_role", "object", _edit(activities["grant"], "object", "claire:moderator")),
        "grant_for_other_knowledge_base": ("activity", "actor_mismatch", "context", _edit(activities["grant"], "context", f"{B}/ap/kb/1846000000000000101")),
        "grant_end_before_start": ("activity", "invalid_time", "endTime", _edit(activities["grant"], "endTime", "2026-11-16T12:00:00Z")),
        "grant_without_end_time": ("activity", "missing_field", "endTime", _edit(activities["grant"], "endTime")),
        "revoke_grant_of_other_server": ("activity", "origin_mismatch", "object", _edit(activities["revoke"], "object", "https://c.example/grants/1")),
        "knowledge_base_accepts_automatic_not_a_boolean": ("object", "invalid_field", v.ACCEPTS_AUTOMATIC, _edit(objects["knowledge_base"], v.ACCEPTS_AUTOMATIC, "yes")),
        "offer_automatic_not_a_boolean": ("activity", "invalid_field", f"object.{v.AUTOMATIC}", _edit(automatic, f"object/{v.AUTOMATIC}", 1)),
        "delete_document_with_the_reason_of_a_reject": ("activity", "invalid_reason_code", v.REASON_CODE, _edit(activities["delete_document"], v.REASON_CODE, v.AUTOMATIC_NOT_ACCEPTED)),
        "delete_document_with_the_reason_of_a_limit": ("activity", "invalid_reason_code", v.REASON_CODE, _edit(activities["delete_document"], v.REASON_CODE, v.LIMIT_REACHED)),
        "update_knowledge_base_without_updated": ("activity", "missing_field", "object.updated", _edit(activities["update_knowledge_base"], "object/updated")),
        "knowledge_base_updated_without_zone": ("object", "invalid_time", "updated", _edit(objects["knowledge_base"], "updated", "2026-11-16T12:00:00")),
    }
    return {name: {"validator": validator, "error": error, "path": path, "value": value} for name, (validator, error, path, value) in cases.items()}


def _read(folder: str) -> dict[str, Any]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((FIXTURES / folder).glob("*.json"))}


def write_fixtures() -> None:
    """Write the fixture files from the builders. Run it only when the contract changes: ``python -c "import tests.test_knowledge_federation_vocabulary as t; t.write_fixtures()"``."""
    for folder, values in (("objects", built_objects()), ("activities", built_activities()), ("invalid", invalid_values())):
        (FIXTURES / folder).mkdir(parents=True, exist_ok=True)
        for name, value in values.items():
            (FIXTURES / folder / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ── Fixtures and the two sides ───────────────────────────────────────────────


def test_each_object_and_activity_has_a_fixture() -> None:
    assert set(_read("objects")) == set(v.OBJECT_KINDS)
    assert set(_read("activities")) == set(v.ACTIVITY_KINDS)


@pytest.mark.parametrize("kind", v.OBJECT_KINDS)
def test_object_fixture_is_valid_and_is_the_builder_output(kind: str) -> None:
    fixture = _read("objects")[kind]
    assert v.validate_object(fixture, top_level=True) == kind
    # What one server builds is the text that the other server reads: the value survives the JSON text without a change.
    assert json.loads(json.dumps(built_objects()[kind])) == fixture


@pytest.mark.parametrize("kind", v.ACTIVITY_KINDS)
def test_activity_fixture_is_valid_and_is_the_builder_output(kind: str) -> None:
    fixture = _read("activities")[kind]
    assert v.validate_activity(fixture) == kind
    assert json.loads(json.dumps(built_activities()[kind])) == fixture


def test_invalid_fixtures_are_the_refusals_of_the_validators() -> None:
    fixtures = _read("invalid")
    assert json.loads(json.dumps(invalid_values())) == fixtures
    for name, case in fixtures.items():
        with pytest.raises(v.ContractError) as caught:
            if case["validator"] == "object":
                v.validate_object(case["value"], top_level=True)
            else:
                v.validate_activity(case["value"])
        assert (caught.value.code, caught.value.path) == (case["error"], case["path"]), name
        assert caught.value.code in v.ERROR_CODES


def test_exchange_between_two_servers_refers_to_the_same_objects() -> None:
    """The flow of unit KF4 with a grant: each answer of B refers to what A sent, and A reads each answer."""
    sent = built_activities()
    for kind, activity in sent.items():
        assert v.validate_activity(json.loads(json.dumps(activity))) == kind
    offer, grant = sent["offer_contribution"], sent["grant"]
    assert sent["accept_follow"]["object"]["id"] == sent["follow"]["id"]
    assert sent["accept_offer"]["object"] == offer["id"] == sent["reject_offer"]["object"]
    assert sent["accept_offer"]["to"] == [offer["actor"]]
    assert grant["target"] == offer["actor"] and grant["context"] == offer["target"]
    # After the grant, the next offer of alice has the grant as its capability; the revoke names the same grant.
    second = v.build_offer(activity_id=f"{ALICE}#offers/2", contribution=offer["object"], capability=grant["id"])
    assert v.validate_activity(json.loads(json.dumps(second))) == "offer_contribution"
    assert second[v.CAPABILITY] == sent["revoke"]["object"]
    # The copy on B keeps the origin, the credit, and the root of the document that A offered.
    offered, copy_on_b = offer["object"][v.DOCUMENT], sent["update_document"]["object"]
    assert copy_on_b["origin"] == offered["id"] == copy_on_b[v.ROOT] == offered[v.ROOT]
    assert copy_on_b["attributedTo"] == offered["attributedTo"] and copy_on_b["id"] != offered["id"]


def test_automatic_offer_and_its_refusal_between_two_servers() -> None:
    """Unit KF5: the record of B tells A if B accepts automatic contributions; an automatic offer of A has the mark; B rejects it with the reason ``automatic_not_accepted`` while its switch is off. A manual offer has no mark, and a server that does not send the state of its switch is valid."""
    record = built_objects()["knowledge_base"]
    assert record[v.ACCEPTS_AUTOMATIC] is True and v.build_knowledge_base({**KB_ROW, "accepts_automatic": False}, KB_IRIS)[v.ACCEPTS_AUTOMATIC] is False
    assert v.validate_object(_edit(record, v.ACCEPTS_AUTOMATIC), top_level=True) == "knowledge_base"
    offer = json.loads(json.dumps(_automatic_offer()))
    assert v.validate_activity(offer) == "offer_contribution" and offer["object"][v.AUTOMATIC] is True
    assert v.AUTOMATIC not in built_activities()["offer_contribution"]["object"]
    reject = v.build_reject_offer(activity_id=f"{KB}#rejects/offers/3", knowledge_base=KB, offer=offer["id"], offer_actor=ALICE, reason_code=v.AUTOMATIC_NOT_ACCEPTED)
    assert v.validate_activity(json.loads(json.dumps(reject))) == "reject_offer" and reject[v.REASON_CODE] == "automatic_not_accepted"
    assert v.AUTOMATIC_NOT_ACCEPTED not in v.REASON_CODES and v.REJECT_REASON_CODES == (*v.REASON_CODES, v.AUTOMATIC_NOT_ACCEPTED, v.LIMIT_REACHED)


def test_reject_for_a_limit_of_the_receiver_is_valid_and_is_not_a_reason_of_the_reviewers() -> None:
    """A ``Reject`` that a limit of the receiver gave has its own reason (``limit_reached``), so that the sender can offer the document again later; a ``Delete`` cannot have it."""
    offer = built_activities()["offer_contribution"]
    reject = v.build_reject_offer(activity_id=f"{KB}#rejects/offers/4", knowledge_base=KB, offer=offer["id"], offer_actor=ALICE, reason_code=v.LIMIT_REACHED)
    assert v.validate_activity(json.loads(json.dumps(reject))) == "reject_offer" and reject[v.REASON_CODE] == "limit_reached"
    assert v.LIMIT_REACHED not in v.REASON_CODES
    assert "limit_reached" in v.vocabulary_page()


def test_record_has_the_time_of_its_change_and_an_update_of_it_needs_the_time() -> None:
    """The ``Update`` of a record has ``updated`` (the receiver refuses one that is not newer); a record that no follower got has none and is valid."""
    update = json.loads(json.dumps(built_activities()["update_knowledge_base"]))
    assert update["object"]["updated"] == "2026-11-16T11:30:00Z"
    assert v.validate_activity(update) == "update_knowledge_base"
    without = v.build_knowledge_base({**KB_ROW, "updated": None}, KB_IRIS)
    assert "updated" not in without and v.validate_object(v.with_context(without), top_level=True) == "knowledge_base"


def test_builder_refuses_input_without_a_necessary_key() -> None:
    with pytest.raises(v.ContractError) as caught:
        v.build_knowledge_base({k: val for k, val in KB_ROW.items() if k != "signature"}, KB_IRIS)
    assert (caught.value.code, caught.value.path) == ("missing_field", "kb.signature")
    assert v.SIGNATURE not in v.build_knowledge_base({**KB_ROW, "signature": None}, KB_IRIS)
    with pytest.raises(v.ContractError):
        v.build_document(DOC_ROW, {"id": ROOT_DOC, "attributed_to": ALICE, "root": ROOT_DOC}, None)


# ── Context document, vocabulary page, and the other modules ─────────────────


def _used_terms(value: Any, terms: set[str], names: set[str]) -> None:
    """Collect the keys (``terms``) and the type names (``names``) of a wire value. A JSON value (``@type: @json``) is not JSON-LD, so its content is not read."""
    json_terms = {row["term"] for row in v.TERMS if isinstance(row["definition"], dict) and row["definition"].get("@type") == "@json"}
    if isinstance(value, list):
        for item in value:
            _used_terms(item, terms, names)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == "@context":
                continue
            terms.add(key)
            if key in ("type", "formerType"):
                names.update([item] if isinstance(item, str) else item)
            elif key not in json_terms:
                _used_terms(item, terms, names)
    elif value == v.ROLE_COLLABORATOR:
        names.add(value)


def test_context_file_is_json_ld_and_defines_each_term_of_the_builders() -> None:
    text = (STATIC_NS / "v1.jsonld").read_text(encoding="utf-8")
    assert text == v.context_json()
    context = json.loads(text)["@context"]
    assert context["claire"] == v.CLAIRE_NS == "https://w3id.org/claire-bible/ns#"
    assert v.CLAIRE_CONTEXT == "https://w3id.org/claire-bible/ns/v1"
    for term, definition in context.items():
        if term == "@version":
            continue
        target = definition if isinstance(definition, str) else definition["@id"]
        assert target.startswith(("https://", "claire:")), term
        # A term with a colon must expand to itself (JSON-LD 1.1).
        assert ":" not in term or target == term, term

    terms: set[str] = set()
    names: set[str] = set()
    _used_terms([*built_objects().values(), *built_activities().values(), v.build_offer(activity_id=f"{ALICE}#offers/2", contribution=built_activities()["offer_contribution"]["object"], capability=f"{KB}#grants/1"), _automatic_offer()], terms, names)
    known = {row["term"] for row in v.TERMS}
    for name in terms | names:
        assert name in v.AS2_TERMS or name in known, name
        if name in known and not name.startswith("claire:"):
            assert name in context, name
        if name.startswith("claire:") and name in terms:
            assert name in context, name
    # Each term of the vocabulary is in use, and each has the IRI of decision KD9 or of ForgeFed.
    assert known <= terms | names
    for row in v.TERMS:
        assert v.term_iri(row["term"]).startswith((v.CLAIRE_NS, v.FORGEFED_NS))


def test_vocabulary_page_file_has_each_term() -> None:
    page = (STATIC_NS / "index.html").read_text(encoding="utf-8")
    assert page == v.vocabulary_page()
    for row in v.TERMS:
        assert f'id="{v.term_iri(row["term"]).rsplit("#", 1)[1]}"' in page


def test_code_lists_are_the_ones_of_the_local_modules() -> None:
    from claire import contributions
    from claire.store import kb_collab

    assert v.REASON_CODES == contributions.REASON_CODES
    assert v.KNOWLEDGE_TYPES == kb_collab.KNOWLEDGE_TYPES


def test_extraction_of_the_wire_is_an_extraction_result() -> None:
    """The receiver builds its graph from the wire extraction with the model of the local merge job (``ExtractionResult``)."""
    from claire.extract.provider import ExtractionResult

    wire = _read("objects")["extraction"]
    result = ExtractionResult.model_validate({"summary": wire["summary"], "key_claims": wire[v.KEY_CLAIMS], "entities": wire[v.ENTITIES], "relations": wire[v.RELATIONS]})
    assert [e.name for e in result.entities] == ["Ada Lovelace", "Analytical Engine"]
    assert result.relations[0].source == "Ada Lovelace"


def test_e2e_server_serves_the_context_and_the_vocabulary_page(tmp_path) -> None:
    """KF0: ``/ns/v1`` gives the context document and ``/ns`` gives the vocabulary page, with no credential."""
    from starlette.testclient import TestClient

    from claire.api.server import create_app
    from claire.config import Settings
    from claire.knowledge_federation import vocabulary

    (tmp_path / "data").mkdir()
    settings = Settings(CLAIRE_DB_PATH=str(tmp_path / "data" / "claire.db"), CLAIRE_VAULT_PATH=str(tmp_path / "vault"), CLAIRE_PROVIDER="mock", CLAIRE_ENVIRONMENT="development", CLAIRE_PUBLIC_URL="http://127.0.0.1:8765", CLAIRE_INJECT_TOKEN="owner-" + "s" * 32)
    with TestClient(create_app(settings=settings), base_url="http://127.0.0.1:8765", follow_redirects=False) as client:
        context = client.get("/ns/v1")
        assert context.status_code == 200 and context.headers["content-type"].startswith("application/ld+json") and context.headers["access-control-allow-origin"] == "*"
        assert context.text == vocabulary.context_json() and "@context" in context.json()
        page = client.get("/ns")
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html") and page.text == vocabulary.vocabulary_page()
