"""Pre-labels from the Gemini Decisions API (external library calls): request shape, answers, and failures."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from claire.extract import decisions_prelabel as dp
from claire.extract.same_entity_rule import SAME_ENTITY_FALSE, SAME_ENTITY_TRUE


def _item(item_id: int, cand: str, *, seq: int = 0, doc: str = "doc_1") -> dict[str, Any]:
    return {
        "id": item_id, "document_id": doc, "seq": seq, "decided_at": 1.0,
        "new_entity": json.dumps({"name": "Antrea CNI", "type": "Tool", "aliases": [], "observations": ["a CNI plugin"]}),
        "candidate": json.dumps({"name": cand, "type": "Framework", "aliases": [], "observations": []}),
    }


def test_one_request_for_each_decision_with_the_one_rule() -> None:
    calls: list[dict[str, Any]] = []

    def create(**kwargs: Any) -> Any:
        calls.append(kwargs)
        # The SDK returns objects; a dict answer also works
        answers = {key: SimpleNamespace(noul=0.93 if i == 0 else 0.04, confidence=0.93 if i == 0 else 0.96) for i, key in enumerate(kwargs["questions"])}
        return SimpleNamespace(answers=answers)

    items = [_item(1, "Antrea"), _item(2, "Calico"), _item(3, "Cilium", seq=4)]
    results = dp.prelabel(items, api_key="AIzaTestKey", create=create)
    assert len(calls) == 2  # two decisions
    first = calls[0]
    assert first["model"] == "gemini-decision-1"
    state = json.loads(first["input"])
    assert state["new_entity"]["name"] == "Antrea CNI" and [c["name"] for c in state["candidates"].values()] == ["Antrea", "Calico"]
    question = first["questions"]["c1"]
    assert question["type"] == "noul"
    assert question["criteria"] == {"true_description": SAME_ENTITY_TRUE, "false_description": SAME_ENTITY_FALSE}
    assert results[1] == {"engine": "gemini-decisions", "model": "gemini-decision-1", "status": "ok", "noul": 0.93, "confidence": 0.93}
    assert results[2]["noul"] == 0.04 and results[3]["status"] == "ok"


def test_failures_give_a_status_and_never_raise() -> None:
    items = [_item(1, "Antrea"), _item(2, "Calico", seq=1)]
    assert {r["status"] for r in dp.prelabel(items, api_key="  ").values()} == {"no_key"}

    def broken(**_kwargs: Any) -> Any:
        raise TimeoutError("slow")

    assert {r["status"] for r in dp.prelabel(items, api_key="k", create=broken).values()} == {"error:TimeoutError"}

    def partial(**kwargs: Any) -> Any:
        return {"answers": {}}

    assert dp.prelabel(items[:1], api_key="k", create=partial)[1]["status"] == "error:no_answer"
    assert dp.prelabel([], api_key="k") == {}


def test_sdk_client_has_the_decisions_resource() -> None:
    """The installed google-genai gives client.decisions.create (no network call)."""
    create = dp._client_create("AIzaTestKeyWithoutNetwork")
    assert callable(create)
