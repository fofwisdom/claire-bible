# TypeSafe AI Jev Shadow 평가 설계 — 판정 권한 없는 동시 평가로 도입 여부를 데이터로 검증

작성일: 2026-10-31 · 상태: **Implemented** · 기준: [GOALS.md](../GOALS.md) 트랙2(추출·연결 품질) · 관련 문서: [GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md](GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md), [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md), [MULTI_PROVIDER_DESIGN.md](MULTI_PROVIDER_DESIGN.md)

---

## 1. 배경

### 1.1 현행 기준선 — 2026-10-30 번들 `gb_20261030_143108_7e869214` (format v2) 사실 점검
검토 증거 번들(format v2)로 현행 System 2 LLM judge의 비용·지연·성과를 처음 실측했다.

| 항목 | 값 | 출처 |
| :--- | :--- | :--- |
| `judge_same_entity` 호출 | 1,442회 (2026-09-27 ~ 10-30, 하루 약 42회), antigravity / gemini-3.8-flash | `review_judge_calls` |
| 호출 지연 | p50 9.7 s · p90 12.1 s · p99 18.4 s, 누적 4.09 h | `review_judge_calls.duration_ms` |
| 문서당 판정 대기 | p50 107 s · p90 267 s · 최대 463 s | 문서별 합 |
| 병합 수율 | 1,387회 호출 → `borderline_llm_judge` MERGE 18건 (**1.3%**) | `review_decisions` × `review_judge_calls` |
| 입력 크기 | 약 192만 자 (약 48만 토큰 추정; Jev 단가 환산 약 $0.02) | `review_judge_calls.input_chars` |
| Jev 실측 | **0건**. `generated_by="jev"` 히트맵 15개는 로컬 스텁 산출물(§1.2) | `review_heatmaps`, 텔레메트리 |
| 후보 추적 | 결정 8건(문서 1개)뿐. judge 21회 중 6회(29%)가 Concept ↔ 구체 타입 비교 | `review_decisions.candidates` |
| 판정 점수 | borderline MERGE 18건 대부분 `score=0.0` — FTS 단독 후보라 코사인 점수가 없음 | `review_decisions.score` |
| 경계 구간 | 기존 그래프 217쌍 대부분 근접 변형(예: KB5126651 ↔ KB5126655, ESM2 ↔ ESM2 Medium) | `review_candidate_pairs` |

결론: Jev의 이점은 비용보다 **지연**이다. 그러나 정확도, 특히 근접 변형을 오병합하지 않는 능력은 실측 없이는 판단할 수 없다.

### 1.2 스텁 오염
`extract/decision.py`의 `_call_jev_matrix_api`는 Jev를 호출하지 않았다. difflib와 기존 판정 점수로 행렬을 만든 뒤 `generated_by="jev"`를 붙였다. `enable_jev=true`에 키까지 설정된 운영 환경에서 이 라벨이 Jev 산출물로 오인되었다. 스텁은 제거했다(§3.8).

### 1.3 목적
같은 후보 쌍에 대해 **현행 판정(규칙·System 2)과 Jev 확률을 나란히 기록**한다. Jev에는 판정 권한을 주지 않는다. 그래야 정확도·지연·비용을 운영 분포에서 비교할 수 있다.

---

## 2. API 계약 (공식 문서 docs.typesafe.ai, 2026-10-31 확인)

| 항목 | 내용 |
| :--- | :--- |
| 엔드포인트 | `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <API_KEY>` |
| 요청 | `{"model", "state", "questions"}` (모두 필수). `state`는 문자열·객체·배열. `questions`는 이름 → 질문 객체 |
| noul 질문 | `{"type": "noul", "instructions"?, "criteria"?: {"true", "false"}}` |
| 질문 수 | 별도 상한 없음. 한 요청의 모든 질문은 공유 `state`에 대해 동시에 평가됨 |
| 응답 | `{"model", "answers": {이름: {"type": "noul", "noul": 0..1}}, "usage": {"input_tokens", "output_tokens"}}` — 확률은 **`answers.<이름>.noul`** |
| 문맥 한도 | 요청당 64k, 질문당(state + 질문 1개) 32k |
| 오류 | 400 규칙 위반 · 401 키 · 413 32 MiB 초과 · 415 압축 · 422 검증 · 429 rate limit · 529 과부하 |
| 모델 | `jev-1.14.0`, `jev-1.13.0`. `jev-latest` → 1.14.0. 입력 토큰당 $0.042 / M, 출력 무료 |

기존 `scripts/verify_jev_system1.py`는 확률을 최상위 키에서 읽어 항상 0.0을 얻었다. 문서에 없는 `/v1/classify` 경로도 추측해 호출했다. 둘 다 바로잡았다(§3.8).

---

## 3. 설계

### 3.1 불변식: Jev는 판정하지 않는다
- 병합·생성 여부, 후보 순서, judge 호출 여부는 shadow 유무와 무관하게 같다.
- shadow의 실패(키 오류·타임아웃·비정상 응답)는 예외로 올라가지 않는다. 결정 기록의 `shadow.status`에만 남는다.

### 3.2 호출 시점과 범위 (`extract/resolver.py`)
- Gate 1(정확 일치·별칭·약어)에서 끝나는 결정은 호출하지 않는다. 결정론 규칙이라 비교 대상이 아니다.
- Gate 3 후보 수집 직후, 후보 루프에 들어가는 **모든 후보**(최대 24개)를 한 요청에 묶어 1회 호출한다. 여기에는 `invariant_reject`, `judge_cap`, `not_reached` 후보도 포함된다. 그래야 judge가 보지 못한 쌍에도 Jev 점수가 남는다.

### 3.3 요청 형식 (`extract/jev.py`)
```json
{
  "model": "jev-latest",
  "state": {
    "task": "Knowledge graph entity resolution. ...",
    "new_entity": {"name": "...", "type": "...", "aliases": [...], "observations": [...]},
    "candidates": {"c1": {...}, "c2": {...}}
  },
  "questions": {
    "c1": {"type": "noul",
           "instructions": "Do new_entity and candidates.c1 refer to the same real-world entity, ...?",
           "criteria": {"true": "same referent written differently: ... alias, abbreviation, translation, rebrand",
                        "false": "different versions/releases/editions/variants, product vs vendor, ..."}}
  }
}
```
- 엔티티 요약은 별칭 10개, 관측문 6개(각 280자)까지만 담는다. 질문당 32k 문맥 한도를 넘지 않게 하기 위해서다.
- `criteria.false`에는 경계 구간에서 실제로 관찰된 근접 변형 유형을 명시했다(§1.1).

### 3.4 지연 격리와 장애 차단
1. **병합 전 스냅샷**: 후보 엔티티를 루프 전에 한 번에 읽어 둔다. 병합이 대상 엔티티에 신규 관측문을 추가하기 때문이다. 병합 후 상태로 질의하면 Jev 입력이 '같음' 쪽으로 편향된다.
2. **동시 실행**: 워커 스레드(공유 풀, 4개)는 HTTP 호출만 하고 DB에는 접근하지 않는다. 결과는 결정이 확정되는 `_finish` 시점에 `jev_timeout + 1` 초까지 기다려 수거한다. 대부분 System 2 judge(약 10 s) 뒤에 가려진다.
3. **회로 차단**: 연속 3회 실패하면 600초 동안 호출을 건너뛰고 `status="circuit_open"`으로 기록한다. 같은 설정이면 평가기를 재사용해 문서가 바뀌어도 차단 상태가 유지된다.
4. 재시도는 하지 않는다. shadow 표본의 공백은 `by_status`로 드러난다.

### 3.5 기록 (`documents.meta.resolution_log`, 방안 B)
- 후보별: `candidates[].jev_noul` (0~1, 응답에 없으면 생략)
- 결정 단위 `shadow`:

| 필드 | 의미 |
| :--- | :--- |
| `engine` / `requested_model` / `model` | `jev`, 요청 모델(별칭), 실제 응답 모델(예: `jev-1.14.0`) |
| `status` | `ok`, `partial`(일부 답 누락), `http_<코드>`, `timeout`, `network`, `bad_response`, `circuit_open`, `error` |
| `questions` / `answered` | 질문 수 / 파싱된 답 수 |
| `latency_ms` / `input_tokens` | 왕복 지연, 과금 입력 토큰 |
| `error` | 응답 본문 앞 200자(키는 포함되지 않음) |

### 3.6 설정
- `enable_jev=true`이고 `jev_api_key`가 있으면 shadow가 켜진다. WebUI 체크박스 라벨은 "엔티티 해소 shadow 평가 · 판정 비관여"로 바꿨다.
- `CLAIRE_JEV_MODEL` (`providers.json`의 `jev.model`, 기본 `jev-latest`): 평가 도중 별칭이 다른 버전을 가리키게 되는 것을 막으려면 `jev-1.14.0`처럼 버전을 고정한다.
- `CLAIRE_JEV_TIMEOUT` (기본 15 s): shadow 수거 대기에도 그대로 적용된다.

### 3.7 번들 (review schema v2, `graph_bundle_review.py`)
| 위치 | 내용 |
| :--- | :--- |
| `review_decisions.shadow` | 결정 단위 shadow 메타(JSON) |
| `review_shadow_pairs` | 후보 1개당 1행: 엔티티·후보 이름/타입, origin, vector_score, 현행 outcome, judge_ms, `jev_noul`, jev_status/model, 최종 stage/decision, `merged`(이 후보로 병합됐는가) |
| `review_meta.shadow` | 커버리지(`decisions_with_shadow`, `by_status`, `models`), 지연 p50/p95/max, `input_tokens_total`, **System 2 판정과의 일치표**(임계 0.5 / 0.9), outcome별 Jev '같음' 비율 |
| `review_meta.coverage.heatmap_generator_notes` | 과거 `jev` 히트맵이 스텁 산출물임을 명시 |
| manifest `review` | `schema_version: 2`, `shadow_decisions`, `shadow_pairs_scored` |

일치표 셀은 `both_same`, `both_different`, `jev_same_llm_different`, `jev_different_llm_same`이다. **System 2 판정은 정답이 아니다.** 일치표는 불일치 쌍을 골라 사람이 레이블링할 대상을 정하는 데 쓴다.

### 3.8 동반 수정
- **히트맵 스텁 제거**: `evaluate_decision_matrix`는 Jev 설정과 무관하게 `fallback_vector` 행렬을 만든다.
- **FTS 후보 점수**: `borderline_llm_judge` 병합 시 FTS 단독 후보는 `score=None`으로 기록한다. 기존에는 0.0이었다. reason은 "Judge confirmed SAME (FTS candidate, no vector score)"이다.
- **`scripts/verify_jev_system1.py`**: 운영 shadow와 같은 요청 빌더·파서(`claire.extract.jev`)를 쓴다. 연결 확인은 문서화된 `GET /v1/models`로 한다. 호출이 실패한 케이스는 TN으로 세지 않고 따로 집계한다.

### 3.9 데이터 반출
shadow가 켜지면 신규 엔티티와 후보 엔티티의 이름·타입·별칭·관측문(엔티티당 최대 6개, 각 280자)이 `api.typesafe.ai`(미국 호스팅)로 전송된다. 문서 원문은 전송하지 않는다. 중단하려면 `enable_jev`를 끈다.

---

## 4. 평가 절차 (Go / No-Go)
1. **수집**: System 2 판정 쌍 200개 이상(현재 속도로 약 5일)과, judge가 보지 못한 `judge_cap`·`invariant_reject` 쌍을 함께 모은다.
2. **레이블**: 다음 쌍에 사람 레이블을 붙인다.
   - `review_shadow_pairs`에서 System 2와 Jev가 엇갈린 쌍 전부
   - 무작위 표본
   - 기존 의심 병합(예: Gorgon Halo → Strix Halo, Claude Mythos 5.1 → Mythos)
3. **지표**: 레이블 기준 FPR, Precision, Recall, Jev 지연 p95, 입력 토큰 비용. 같은 쌍에 대해 System 2의 FPR, Precision, Recall도 함께 산출한다.
4. **게이트**(`verify_jev_system1.py`와 동일): 오병합 0건(FPR = 0), Precision ≥ 98%, p95 < 500 ms.
5. **역할 후보**(데이터로 선택):
   - (a) judge 대체
   - (b) judge 앞 여과: noul < x 이면 judge를 생략한다. 현재 judge 호출의 98.7%가 '다름'이므로, `judge_same`을 하나도 잃지 않는 x에서 생략률을 측정한다.
   - (c) 기각

---

## 5. 한계
- 한 요청의 질문은 공유 `state`에 대해 동시에 평가된다. 그래서 후보 1개짜리 요청(검증 스크립트 방식)과 점수가 다를 수 있다. 두 방식을 같은 쌍에서 비교해 확인해야 한다.
- Jev와 System 2는 같은 엔티티 정보를 받지만 프롬프트 표현이 다르다.
- 회로 차단·타임아웃 구간은 표본에서 빠진다. 장애와 무관한 공백인지 `by_status`로 확인해야 한다.
- Jev 호출은 `telemetry.db`에 기록하지 않는다. 지연·토큰은 결정 기록에만 있으므로 결정이 기록되지 않은 실패 문서의 호출은 보이지 않는다.
- 2026-10-31 이전 결정에는 shadow 기록이 없다. 과거 결정은 `scripts/verify_jev_system1.py` 방식의 오프라인 재질의로만 비교할 수 있다.

## 6. 테스트
- `tests/test_jev_shadow.py`
  - 요청 계약(URL, Bearer, model/state/questions)과 후보별 noul 질문·criteria·절단
  - `answers.<key>.noul` 파싱(최상위 키 무시, 비유한 값 무시, 클램프)과 HTTP·타임아웃 오류 분류
  - 회로 차단과 cooldown 복귀, 설정 게이트
  - resolver 통합: 판정 불변, 병합 전 후보 상태로 질의, 실패 시 해소 지속, Gate 1 미호출, FTS 병합 `score=None`
- `tests/test_graph_bundle_review.py`
  - `review_shadow_pairs` 평탄화, 일치표 임계값별 셀, 스텁 히트맵 주석, schema v2 manifest
- `tests/test_decision_stream_and_heatmap.py`
  - Jev 설정과 무관하게 `fallback_vector` 히트맵, 히트맵은 Jev를 호출하지 않음, `CLAIRE_JEV_MODEL` 로딩
