"""TypeSafe AI Jev (System One) 클라이언트 및 엔티티 해소 shadow 평가.

Jev는 도입 검토 단계이므로 판정 권한이 없다. shadow 모드는 resolver가 후보 루프에 진입할 때 같은 후보 집합을 Jev에도 묻고,
그 확률(noul)을 ResolutionDecision에 기록만 한다. 병합 여부는 언제나 기존 Gate(규칙 + System 2 LLM judge)가 정한다.
See docs/design/JEV_SHADOW_EVALUATION_DESIGN.md.

API 계약 (docs.typesafe.ai/api, 2026-10-31 확인):
  POST {base_url}/systemone, Authorization: Bearer <key>
  요청 {"model", "state", "questions": {name: {"type": "noul", "instructions", "criteria": {"true", "false"}}}}
  응답 {"model", "answers": {name: {"type": "noul", "noul": 0..1}}, "usage": {"input_tokens", "output_tokens"}}
"""

from __future__ import annotations

import concurrent.futures
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..ontology.base import Entity

logger = logging.getLogger(__name__)

DEFAULT_JEV_MODEL = "jev-latest"
SHADOW_ENGINE = "jev"
SHADOW_MAX_CANDIDATES = 24
SNAPSHOT_MAX_ALIASES = 10
SNAPSHOT_MAX_OBSERVATIONS = 6
SNAPSHOT_MAX_OBS_CHARS = 280
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_COOLDOWN_S = 600.0

SAME_ENTITY_TASK = (
    "Knowledge graph entity resolution. Each question asks whether new_entity and one entry of "
    "candidates refer to the same real-world entity and should be merged into a single node."
)
SAME_ENTITY_INSTRUCTIONS = (
    "Do new_entity and candidates.{key} refer to the same real-world entity, "
    "so that they should be merged into one knowledge-graph node?"
)
SAME_ENTITY_CRITERIA = {
    "true": (
        "The same referent written differently: spelling or case variant, alias, abbreviation or acronym, "
        "translation, or a rename or rebrand of the same thing."
    ),
    "false": (
        "Different referents, including different versions, releases, editions, sizes or variants of a product; "
        "a product and its vendor; a person and their organization or work; rival, related or successor "
        "products; a broad concept and a narrower one."
    ),
}


class JevError(Exception):
    """Jev 호출 실패. status 는 기록용 짧은 코드(http_401, timeout, network, bad_response)."""

    def __init__(self, status: str, detail: str = "", http_status: int | None = None):
        super().__init__(f"{status}: {detail}" if detail else status)
        self.status = status
        self.detail = detail
        self.http_status = http_status


class JevClient:
    """POST /systemone 단일 호출기. 재시도 없음 (shadow 는 실패를 기록하고 넘어간다)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.typesafe.ai/v1",
        timeout: float = 15.0,
        model: str = DEFAULT_JEV_MODEL,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.model = model or DEFAULT_JEV_MODEL

    def systemone(self, state: Any, questions: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """(응답 JSON, 지연 ms). 200 이외 응답·네트워크 오류·비정상 본문은 JevError."""
        payload = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            resp = httpx.post(
                f"{self.base_url}/systemone", json=payload, headers=headers, timeout=self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise JevError("timeout", type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            raise JevError("network", type(exc).__name__) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000.0, 1)
        if resp.status_code != 200:
            raise JevError(f"http_{resp.status_code}", resp.text[:200], http_status=resp.status_code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise JevError("bad_response", "body is not JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise JevError("bad_response", "answers object missing")
        return data, elapsed_ms


def entity_snapshot(
    name: str,
    etype: str,
    aliases: list[str] | None = None,
    observations: list[str] | None = None,
) -> dict[str, Any]:
    """Jev state 에 넣을 엔티티 요약. 질문당 문맥 한도(32k)를 넘지 않도록 별칭·관측문을 자른다."""
    return {
        "name": name,
        "type": etype,
        "aliases": [a for a in (aliases or []) if a][:SNAPSHOT_MAX_ALIASES],
        "observations": [
            o[:SNAPSHOT_MAX_OBS_CHARS] for o in (observations or []) if o
        ][:SNAPSHOT_MAX_OBSERVATIONS],
    }


def build_same_entity_request(
    new_entity: dict[str, Any],
    candidates: list[tuple[str, dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """후보마다 noul 질문 1개를 한 요청에 묶는다. (state, questions, 질문키→후보 ID)."""
    keys: dict[str, str] = {}
    cand_state: dict[str, Any] = {}
    questions: dict[str, Any] = {}
    for idx, (cand_id, snap) in enumerate(candidates[:SHADOW_MAX_CANDIDATES], 1):
        key = f"c{idx}"
        keys[key] = cand_id
        cand_state[key] = snap
        questions[key] = {
            "type": "noul",
            "instructions": SAME_ENTITY_INSTRUCTIONS.format(key=key),
            "criteria": SAME_ENTITY_CRITERIA,
        }
    state = {"task": SAME_ENTITY_TASK, "new_entity": new_entity, "candidates": cand_state}
    return state, questions, keys


def parse_noul_answers(response: dict[str, Any], keys: dict[str, str]) -> dict[str, float]:
    """answers.<key>.noul → {후보 ID: 확률}. 누락·비유한 값은 건너뛴다."""
    answers = response.get("answers") if isinstance(response, dict) else None
    if not isinstance(answers, dict):
        return {}
    scores: dict[str, float] = {}
    for key, cand_id in keys.items():
        answer = answers.get(key)
        if not isinstance(answer, dict):
            continue
        try:
            value = float(answer.get("noul"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            scores[cand_id] = round(min(1.0, max(0.0, value)), 6)
    return scores


@dataclass
class ShadowCall:
    """제출된 shadow 요청 핸들. 회로 차단 등으로 건너뛴 경우 future=None, skipped 에 사유."""

    keys: dict[str, str] = field(default_factory=dict)
    future: concurrent.futures.Future | None = None
    skipped: str | None = None


class JevShadow:
    """판정에 관여하지 않는 Jev 동시 평가기.

    HTTP 호출만 워커 스레드에서 수행한다(DB 접근 없음). 연속 실패가 임계치를 넘으면 cooldown 동안 호출을 건너뛰어
    Jev 장애가 수집 지연으로 번지지 않게 한다.
    """

    def __init__(
        self,
        client: JevClient,
        *,
        executor: concurrent.futures.Executor | None = None,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        cooldown_s: float = CIRCUIT_COOLDOWN_S,
        clock: Any = time.monotonic,
    ):
        self.client = client
        self._executor = executor or _shared_executor()
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    def submit(self, new_entity: dict[str, Any], candidates: list[tuple[str, dict[str, Any]]]) -> ShadowCall:
        state, questions, keys = build_same_entity_request(new_entity, candidates)
        if not keys:
            return ShadowCall(skipped="no_candidates")
        with self._lock:
            if self._clock() < self._open_until:
                return ShadowCall(keys=keys, skipped="circuit_open")
        return ShadowCall(keys=keys, future=self._executor.submit(self._call, state, questions))

    def _call(self, state: dict[str, Any], questions: dict[str, Any]) -> tuple[dict[str, Any], float]:
        try:
            result = self.client.systemone(state, questions)
        except Exception:
            with self._lock:
                self._failures += 1
                if self._failures >= self._failure_threshold:
                    self._open_until = self._clock() + self._cooldown_s
                    self._failures = 0
            raise
        with self._lock:
            self._failures = 0
        return result

    def collect(self, call: ShadowCall) -> tuple[dict[str, Any], dict[str, float]]:
        """(결정 단위 shadow 메타, {후보 ID: noul}). 어떤 실패도 예외로 올리지 않는다."""
        meta: dict[str, Any] = {
            "engine": SHADOW_ENGINE,
            "requested_model": self.client.model,
            "questions": len(call.keys),
        }
        if call.future is None:
            meta["status"] = call.skipped or "skipped"
            return meta, {}
        try:
            response, elapsed_ms = call.future.result(timeout=self.client.timeout + 1.0)
        except concurrent.futures.TimeoutError:
            meta["status"] = "timeout"
            return meta, {}
        except JevError as exc:
            meta["status"] = exc.status
            if exc.detail:
                meta["error"] = exc.detail[:200]
            return meta, {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jev shadow call failed: %s", type(exc).__name__)
            meta["status"] = "error"
            meta["error"] = type(exc).__name__
            return meta, {}

        scores = parse_noul_answers(response, call.keys)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        meta.update(
            status="ok" if len(scores) == len(call.keys) else "partial",
            model=response.get("model"),
            latency_ms=elapsed_ms,
            input_tokens=usage.get("input_tokens"),
            answered=len(scores),
        )
        return meta, scores


_executor_lock = threading.Lock()
_executor: concurrent.futures.ThreadPoolExecutor | None = None
_shadow_lock = threading.Lock()
_shadow_cache: dict[tuple[str, str, float, str], JevShadow] = {}


def _shared_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="jev-shadow")
        return _executor


def make_jev_shadow(settings: Any) -> JevShadow | None:
    """enable_jev 와 API 키가 모두 있을 때만 shadow 평가기를 돌려준다. 회로 차단 상태 유지를 위해 설정별로 재사용."""
    if not bool(getattr(settings, "enable_jev", False)):
        return None
    api_key = str(getattr(settings, "jev_api_key", "") or "").strip()
    if not api_key:
        return None
    base_url = str(getattr(settings, "jev_base_url", "") or "https://api.typesafe.ai/v1")
    timeout = float(getattr(settings, "jev_timeout", 15.0) or 15.0)
    model = str(getattr(settings, "jev_model", "") or DEFAULT_JEV_MODEL)
    cache_key = (api_key, base_url, timeout, model)
    with _shadow_lock:
        shadow = _shadow_cache.get(cache_key)
        if shadow is None:
            shadow = JevShadow(JevClient(api_key, base_url, timeout, model))
            _shadow_cache.clear()
            _shadow_cache[cache_key] = shadow
    return shadow


def snapshot_of(entity: Entity) -> dict[str, Any]:
    return entity_snapshot(entity.name, entity.type, entity.aliases, entity.observations)
