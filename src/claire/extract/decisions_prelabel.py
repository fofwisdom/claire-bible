from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Callable, NamedTuple, NotRequired, TypedDict

import httpx
from google.genai._gaos.errors import GenAiError, NoResponseError
from google.genai.decisions import CreateDecisionResponse, NoulAnswer

from .same_entity_rule import SAME_ENTITY_FALSE, SAME_ENTITY_TRUE

logger = logging.getLogger(__name__)

PRELABEL_ENGINE = "gemini-decisions"
PRELABEL_MODEL = "gemini-decision-1"
PRELABEL_TASK = (
    "Knowledge graph entity resolution review. Each question asks whether new_entity and one entry of candidates "
    "refer to the same real-world entity and should be one knowledge-graph node."
)
PRELABEL_INSTRUCTIONS = "Do new_entity and candidates.{key} refer to the same real-world entity?"

CreateFn = Callable[..., CreateDecisionResponse]
_CALL_ERRORS = (GenAiError, NoResponseError, httpx.HTTPError)


class Prelabel(TypedDict):
    engine: str
    model: str
    status: str
    noul: NotRequired[float]
    confidence: NotRequired[float]


class PrelabelRequest(NamedTuple):
    input_text: str
    questions: dict[str, Any]
    item_ids: dict[str, int]


def _client_create(api_key: str) -> CreateFn:
    from google import genai

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    return client.decisions.create


def build_request(items: list[dict[str, Any]]) -> PrelabelRequest:
    keys: dict[str, int] = {}
    candidates: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    for idx, it in enumerate(items, 1):
        key = f"c{idx}"
        keys[key] = int(it["id"])
        candidates[key] = json.loads(it["candidate"])
        questions[key] = {
            "type": "noul",
            "instructions": PRELABEL_INSTRUCTIONS.format(key=key),
            "criteria": {"true_description": SAME_ENTITY_TRUE, "false_description": SAME_ENTITY_FALSE},
        }
    state = {"task": PRELABEL_TASK, "new_entity": json.loads(items[0]["new_entity"]), "candidates": candidates}
    return PrelabelRequest(json.dumps(state, ensure_ascii=False), questions, keys)


def _status(status: str) -> Prelabel:
    return Prelabel(engine=PRELABEL_ENGINE, model=PRELABEL_MODEL, status=status)


def prelabel(items: list[dict[str, Any]], *, api_key: str, create: CreateFn | None = None) -> dict[int, Prelabel]:
    if not items:
        return {}
    if not str(api_key or "").strip():
        return {int(it["id"]): _status("no_key") for it in items}
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        groups[(it["document_id"], it["seq"], it["decided_at"])].append(it)
    create = create or _client_create(str(api_key).strip())
    results: dict[int, Prelabel] = {}
    for group in groups.values():
        request = build_request(group)
        try:
            response = create(model=PRELABEL_MODEL, input=request.input_text, questions=request.questions)
        except _CALL_ERRORS as exc:
            logger.warning("Decisions pre-label call failed: %s", type(exc).__name__)
            for item_id in request.item_ids.values():
                results[item_id] = _status(f"error:{type(exc).__name__}")
            continue
        for key, item_id in request.item_ids.items():
            answer = (response.answers or {}).get(key)
            if isinstance(answer, NoulAnswer):
                results[item_id] = Prelabel(engine=PRELABEL_ENGINE, model=PRELABEL_MODEL, status="ok", noul=round(answer.noul, 6), confidence=round(answer.confidence, 6))
            else:
                results[item_id] = _status("error:no_answer")
    return results
