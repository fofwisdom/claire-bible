# 그래프 번들 검토 증거(Review Evidence) 확장 설계 — TypeSafe AI Jev(System 1) 도입 검증용

작성일: 2026-10-30 · 상태: **Phase 1 Implemented** · 기준: [GOALS.md](../GOALS.md) 트랙2(추출·연결 품질) · 관련 문서: [DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md](DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md), [TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md](TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md), [MULTI_PROVIDER_DESIGN.md](MULTI_PROVIDER_DESIGN.md)

---

## 1. 배경 및 목적

### 1.1 검토는 데이터로 검증한다
TypeSafe AI Jev(`jev-1.x`, System One 모델)는 **도입 여부를 검토하는 단계**다. 따라서 Jev가 산출한 데이터는 존재하지 않으며 존재할 수도 없다. 검토 결론(Go / No-Go / 역할 제한)을 데이터로 검증하려면 그래프 번들이 다음 두 가지를 실어 날라야 한다.

1. **비교 기준선(Baseline)**: 현행 해소 파이프라인(Gate 1 결정론 규칙, Gate 3 벡터 대역, Gate 4 System 2 LLM judge)이 실제 운영 데이터에서 **무엇을, 어떤 점수로, 얼마나 걸려서** 판정했는가.
2. **재현 입력(Replay Input)**: 같은 판정을 Jev에 오프라인으로 다시 물을 수 있는 입력 재료(판정 시점의 신규 엔티티 상태, 후보 엔티티, 후보 쌍 점수).

### 1.2 기존 번들(format v1)의 공백 — 2026-10-30 번들 `gb_20261030_084027_064eec48` 사실 점검 결과
| # | 공백 | 원인 | 결과 |
| :--- | :--- | :--- | :--- |
| 1 | 판정 기록(`resolution_log`), 히트맵 출처 미포함 | 번들 `documents` 테이블이 id/title/url/source_type/created_at만 반출 | 과거 병합 결정을 감사하거나 레이블로 쓸 수 없음 |
| 2 | 임베딩 제외 | 원본 벡터 비반출 원칙 | Gate 3 점수 분포·대역별 물량 재현 불가 |
| 3 | **Gate 4 DIFFERENT 판정이 어디에도 영속화되지 않음** | resolver는 엔티티당 최종 decision 1건만 기록 | Jev가 대체할 경계선 대역의 System 2 레이블이 소실됨 |
| 4 | System 2 판정 지연·입력 크기 미포함 | `telemetry.db`에만 존재 | 지연(Jev 공식 70–500ms)·비용(입력 토큰 과금) 비교 불가 |
| 5 | 임베딩 해시 폴백 식별 불가 | Gemini 임베딩 실패 시 64차원 sha256 의사 벡터로 폴백하며, resolver는 모든 벡터를 `model="claire"`로 저장 | 의미 없는 코사인 점수가 실제 점수와 섞여도 구분 불가 |

---

## 2. 검토 질문 ↔ 증거 매핑

| 검토 질문 | 필요한 증거 | 번들 위치 (format v2) | 원천 |
| :--- | :--- | :--- | :--- |
| Q1. 정확도(FPR=0, Precision) | 경계선 후보 쌍과 System 2 판정, 판정 시점 입력 | `review_decisions.candidates`(outcome `judge_same`/`judge_different`), `review_decisions.input_snapshot` | resolver 계측(§3.1) → `documents.meta.resolution_log` |
| Q1'. 실데이터 음성 쌍 | 경쟁·대체 관계, 버전 계열 | 기존 `relations`(`competes_with`/`alternative_to`), `entities` | 기존 그래프 |
| Q2. Gate 3 대체·여과 효과 | 후보 쌍 점수 분포, 대역별 물량 | `review_candidate_pairs` | `embeddings` (오프라인 kNN, 벡터 비반출) |
| Q3. 지연 | System 2 판정 호출 지연 | `review_judge_calls.duration_ms`, `candidates[].judge_ms` | `telemetry.db`, resolver 계측 |
| Q4. 비용 | 판정 입력 크기 | `review_judge_calls.input_chars` | `telemetry.db` |
| Q5. 기존 오병합 기준선 | 병합으로 추가된 별칭·관측문 | `review_decisions.rollback_payload` | `documents.meta.resolution_log` |
| Q6. 히트맵 출처 | `generated_by` | `review_heatmaps` | `documents.meta.heatmap_matrix` |
| Q7. 증거 유효성 | 임계값, 임베딩 유효성, 커버리지, 설정 | `review_meta` | 설정·집계 |

---

## 3. 설계

### 3.1 판정 추적 계측 (`src/claire/extract/resolver.py`)
`ResolutionDecision`에 선택 필드 3개를 추가한다. 저장 위치는 기존 방안 B(`documents.meta["resolution_log"]`)를 그대로 쓰므로 **정본 DB 스키마는 변경하지 않는다.** 후보 루프(Gate 2~4)에 진입한 판정에만 채워지며, Gate 1(exact/alias/acronym) 판정은 `None`이다.

| 필드 | 내용 |
| :--- | :--- |
| `candidates` | 후보 루프가 다룬 후보 목록. 항목: `id`, `origin`(`vector`/`fts`), `score`(벡터 코사인, FTS 단독 후보는 `null`), `outcome`, 평가된 후보는 `name`/`type`/`cand_obs_count`, judge 호출 시 `judge_ms` |
| `input_snapshot` | 판정 시점 신규 엔티티 입력: `name`, `type`, `aliases`, `observations`, `provisional` (System 2 `MergeCandidate`의 신규 측과 동일) |
| `gate_params` | 판정 시점 `auto`/`borderline`/`relational` 임계값, `max_judge`, `adaptive_center`, 질의 임베딩 `embedding_dim`, `embedding_hash_fallback` |

`outcome` 값:

| 값 | 의미 |
| :--- | :--- |
| `invariant_reject` | Gate 2 불변식(버전 충돌, 2글자 약어, 타입 불일치) 위반으로 탈락 |
| `auto_merge` | 점수 ≥ `auto` 로 자동 병합 |
| `judge_same` / `judge_different` | Gate 4 System 2 판정 결과 |
| `judge_cap` | 엔티티당 judge 상한(`MAX_JUDGE`) 도달로 판정 생략 |
| `no_judge` | judge 함수 없음(프로바이더 미지원) |
| `not_reached` | 앞선 후보에서 병합이 확정되어 평가되지 않음 (`id`/`origin`/`score`만 기록) |

크기 상한: 후보는 벡터 16건 + FTS 8건 = 엔티티당 최대 24건이다.

### 3.2 번들 format v2 (`graph_export.db` 추가 테이블)
그래프 테이블(`entities`, `relations`, `documents`)은 변경하지 않는다. 검토 증거는 `review_` 접두 테이블로 분리한다.

```sql
CREATE TABLE review_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);  -- value: JSON
CREATE TABLE review_decisions (
    document_id TEXT NOT NULL, seq INTEGER NOT NULL,
    entity TEXT, stage TEXT, decision TEXT, candidate TEXT, score REAL, reason TEXT,
    target_entity_id TEXT, source_entity_id TEXT, timestamp REAL,
    rollback_payload TEXT, candidates TEXT, input_snapshot TEXT, gate_params TEXT,  -- JSON
    PRIMARY KEY (document_id, seq)
);
CREATE TABLE review_heatmaps (
    document_id TEXT PRIMARY KEY, generated_by TEXT, rows_count INTEGER, cols_count INTEGER,
    threshold_auto_merge REAL, threshold_borderline REAL, created_at REAL
);
CREATE TABLE review_candidate_pairs (
    entity_a TEXT NOT NULL, entity_b TEXT NOT NULL, score REAL NOT NULL, band TEXT NOT NULL,
    PRIMARY KEY (entity_a, entity_b)  -- entity_a < entity_b
);
CREATE TABLE review_judge_calls (
    id INTEGER PRIMARY KEY, timestamp REAL, document_id TEXT, provider TEXT, model TEXT,
    call_type TEXT, input_chars INTEGER, duration_ms INTEGER, status TEXT
);
```

> 2026-10-31 (review schema v2): `review_decisions.shadow` 컬럼, `review_shadow_pairs` 테이블, `review_meta.shadow` 요약이 추가되었다. 과거 `jev` 히트맵이 스텁 산출물이라는 주석(`coverage.heatmap_generator_notes`)도 함께 기록한다. 상세는 [JEV_SHADOW_EVALUATION_DESIGN.md](JEV_SHADOW_EVALUATION_DESIGN.md) §3.7 참조.

`manifest.json`은 `bundle_version: 2`가 되고, `review` 섹션에 `schema_version`과 테이블별 건수, 후보 쌍 산출 상태를 기록한다. `graph_export_sha256`는 review 테이블을 포함한 전체 DB의 해시다.

### 3.3 후보 쌍(`review_candidate_pairs`) 산출 규칙
1. 반출 엔티티의 임베딩만 사용한다. 원본 벡터는 번들에 넣지 않고 쌍별 점수만 반출한다.
2. **해시 폴백 벡터를 제외한다.** 판별식: 길이 ≥ 64, 32 주기 반복(`v[i] == v[i+32]`), 모든 값이 `b/127.5 - 1`(b ∈ 0..255) 격자 위. 제외 건수는 `review_meta.embeddings.hash_fallback`에 기록한다.
3. 차원이 섞여 있으면 최빈 차원 그룹만 사용하고, 나머지는 `excluded_other_dim`으로 기록한다.
4. `vector_adaptive_centering`이 켜져 있으면 그룹 평균을 빼고 정규화한다(resolver와 같은 방식이며, 평균 산출 범위만 그룹으로 한정).
5. 엔티티마다 상위 k=10 이웃 중 점수 ≥ `multihop` 하한인 쌍만 남기고, 무방향 중복을 제거한다.
6. `band`는 현행 임계값 기준 `auto_merge` / `borderline` / `relational` / `multihop`.
7. 계산 백엔드: numpy를 쓸 수 있으면 float32 BLOB을 그대로 행렬로 읽어 블록 행렬곱을 쓴다(numpy는 운영 컨테이너의 `ko` extra를 통해 설치됨). numpy가 없으면 엔티티 400개 이하에서만 순수 Python으로 계산하고, 그 이상은 `skipped`와 사유를 기록한다. 엔티티 20,000개 초과이거나 행렬이 128 MiB를 넘어도 `skipped`.
8. 규모 실측(합성 데이터, 운영 번들과 같은 규모): 엔티티 2,651개 × 3,072차원에서 번들 생성 전체 0.39초, 최대 RSS 증가 약 134 MB, 후보 쌍 10,408건.

### 3.4 판정 텔레메트리(`review_judge_calls`) 반출 규칙
- `telemetry.db`를 **read-only**(`connect_existing(readonly=True)`)로 연다.
- `call_type ∈ {judge_same_entity, judge_relationship}`만 반출한다.
- **테마 격리**: `document_id`가 이번 번들의 반출 문서 집합에 속한 행만 반출한다(`telemetry.db`는 테마 공용).
- 컬럼 화이트리스트: `output_snippet`, `error_message`, `google_block_reason`, `summary_verdict`는 반출하지 않는다.
- **귀속 불가 호출 집계**: `document_id`가 비어 있는 성공 호출은 어느 테마의 것인지 알 수 없으므로 행으로 반출하지 않는다. 대신 `review_meta.judge_calls.unattributed_success`에 call_type별 건수와 `duration_ms` p50/p95/max, `input_chars` p50/p95만 남긴다. 로컬 개발 DB를 점검해 보니 judge 호출 216건 중 214건에 `document_id`가 없었다(gemini/codex 프로바이더의 `judge_same_entity`는 `document_id` 인자를 받지 않는다). 운영 데이터에서도 같다면 지연 기준선은 이 집계에 의존한다.

### 3.5 Import 동작
`review_*` 테이블은 그래프 데이터가 아니라 검토 증거이므로 대상 DB에 **적재하지 않는다.** `import_graph_bundle` 결과에 `bundle_version`과 `review_tables`(테이블별 행 수)를 보고만 한다. v1 번들은 그대로 import된다.

### 3.6 보안·프라이버시 경계
- 비밀값은 반출하지 않는다. `jev_api_key`, `gemini_api_key`는 설정 여부(bool)만 기록하고, `jev_base_url`은 userinfo/query/fragment를 제거한다.
- `raw_text`, 원본 임베딩 벡터는 계속 비반출한다.
- `input_snapshot`·`candidates`는 이미 번들에 포함된 엔티티 이름·관측문과 같은 민감도 등급이다.

### 3.7 실패 격리
review 증거의 각 섹션(판정/히트맵, 후보 쌍, 텔레메트리)은 개별 `try`로 감싼다. 실패하면 `review_meta`에 `status: "error"`와 예외 유형을 남기고, **그래프 번들 생성 자체는 중단하지 않는다.**

---

## 4. 한계 (증거 해석 시 반드시 고려)
1. **계측 이전 판정에는 `candidates`가 없다.** 이 계측은 배포 이후 인제스트부터 쌓인다. `review_meta.coverage.decisions_with_candidates`로 커버리지를 확인한다.
2. 후보 측 엔티티 상태는 번들 생성 시점 상태다(판정 시점이 아님). 그 사이의 변화는 `candidates[].cand_obs_count`와 현재 관측문 수의 차이로 정량화한다.
3. 오프라인 후보 쌍은 저장된 엔티티 임베딩끼리의 유사도다. 인제스트 시점의 신규 추출 frame 임베딩과는 다르므로 Gate 3 분포의 **근사**로만 쓴다.
4. 프로바이더의 `judge_same_entity`는 예외가 나면 `False`를 반환하므로, resolver 수준에서는 판정 실패와 DIFFERENT를 구분할 수 없다. `review_judge_calls.status`와 `document_id`로 교차 확인한다.
5. `telemetry.db`는 30일 보존이므로 `review_judge_calls`는 최근 30일분만 담긴다.
6. `generated_by="jev"`인 히트맵은 현재 코드 기준 스텁 산출물이다(`_call_jev_matrix_api`는 네트워크 호출 없이 로컬 휴리스틱으로 계산). Jev 결과로 해석하면 안 된다.
7. System 2 판정은 정답이 아니다. 골든셋 레이블은 이 후보 풀에서 사람이 확정한다.

---

## 5. 검토자 사용 예시

```sql
-- 경계선 대역에서 System 2가 내린 판정 (Jev 대체 대상)
SELECT d.document_id, d.entity, json_extract(c.value, '$.name') AS candidate,
       json_extract(c.value, '$.score') AS cosine, json_extract(c.value, '$.outcome') AS outcome,
       json_extract(c.value, '$.judge_ms') AS judge_ms
FROM review_decisions d, json_each(d.candidates) c
WHERE json_extract(c.value, '$.outcome') IN ('judge_same', 'judge_different');

-- Gate 3 대역별 후보 쌍 물량
SELECT band, count(*) FROM review_candidate_pairs GROUP BY band;

-- System 2 판정 지연 분포
SELECT call_type, count(*), avg(duration_ms), max(duration_ms) FROM review_judge_calls GROUP BY call_type;
```

---

## 6. 테스트
- `tests/test_graph_bundle_review.py`: review 테이블 반출, 해시 폴백 제외, 차원 그룹, 후보 쌍 대역, 텔레메트리 테마 격리·컬럼 화이트리스트, 비밀값 비반출, import 시 비적재, 섹션 실패 격리.
- `tests/test_safe_gating_resolver.py`: 후보 추적 outcome(`invariant_reject`, `judge_same`, `judge_different`, `not_reached`)과 `input_snapshot`/`gate_params` 기록.
