"""Pre-labels for the entity pair review from the Gemini Decisions API (owner proposal and decision 2026-11-20).

One request for each decision: the shared input has the new entity and the candidates of the queued items, and each item gets
one ``noul`` question with the criteria of the one rule (same_entity_rule.py). The API returns probabilities only, no reason.
A pre-label is never a label. A failure never raises: each item gets a status.

API: ``client.decisions.create(model, input, questions)`` (google-genai 3.2 or later), POST /v1beta/decisions.
See docs/design/JEV_SHADOW_EVALUATION_DESIGN.md (R3).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Callable

from .same_entity_rule import SAME_ENTITY_FALSE, SAME_ENTITY_TRUE

logger = logging.getLogger(__name__)

PRELABEL_ENGINE = "gemini-decisions"
PRELABEL_MODEL = "gemini-decision-1"
PRELABEL_TASK = (
    "Knowledge graph entity resolution review. Each question asks whether new_entity and one entry of candidates "
    "refer to the same real-world entity and should be one knowledge-graph node."
)
PRELABEL_INSTRUCTIONS = "Do new_entity and candidates.{key} refer to the same real-world entity?"

CreateFn = Callable[..., Any]


def _client_create(api_key: str) -> CreateFn:
    from google import genai

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    return client.decisions.create


def _answer_values(answer: Any) -> tuple[float | None, float | None]:
    if answer is None:
        return None, None
    if isinstance(answer, dict):
        noul, conf = answer.get("noul"), answer.get("confidence")
    else:
        noul, conf = getattr(answer, "noul", None), getattr(answer, "confidence", None)
    noul = float(noul) if isinstance(noul, (int, float)) else None
    conf = float(conf) if isinstance(conf, (int, float)) else None
    return noul, conf


def build_request(items: list[dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, int]]:
    """Items of one decision → (input text, questions, question key → item id)."""
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
    return json.dumps(state, ensure_ascii=False), questions, keys


def prelabel(items: list[dict[str, Any]], *, api_key: str, create: CreateFn | None = None) -> dict[int, dict[str, Any]]:
    """Ask the pre-label engine about ``items`` (rows of pair_review_items). Returns {item id: result} for every item."""
    base = {"engine": PRELABEL_ENGINE, "model": PRELABEL_MODEL}
    if not items:
        return {}
    if not str(api_key or "").strip():
        return {int(it["id"]): dict(base, status="no_key") for it in items}
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        groups[(it["document_id"], it["seq"], it["decided_at"])].append(it)
    try:
        create = create or _client_create(str(api_key).strip())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Decisions client could not start: %s", type(exc).__name__)
        return {int(it["id"]): dict(base, status=f"error:{type(exc).__name__}") for it in items}
    results: dict[int, dict[str, Any]] = {}
    for group in groups.values():
        text, questions, keys = build_request(group)
        try:
            response = create(model=PRELABEL_MODEL, input=text, questions=questions)
            answers = response.get("answers") if isinstance(response, dict) else getattr(response, "answers", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Decisions pre-label call failed: %s", type(exc).__name__)
            for item_id in keys.values():
                results[item_id] = dict(base, status=f"error:{type(exc).__name__}")
            continue
        for key, item_id in keys.items():
            noul, conf = _answer_values((answers or {}).get(key))
            if noul is None:
                results[item_id] = dict(base, status="error:no_answer")
            else:
                results[item_id] = dict(base, status="ok", noul=round(noul, 6), confidence=None if conf is None else round(conf, 6))
    return results
