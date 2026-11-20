# Antigravity CLI 적재 지연 해소 및 실행 격리·최적화 아키텍처 설계

- **Status**: Partly implemented. Implemented (Phase 1 and 2): tools forbidden and an empty working directory for `agy` calls that need no tools, `effort=low` for judgement tasks, `call_type` and `document_id` in telemetry, and parallel relation judgement. Current behavior: [`PROVIDERS.md`](../implementation/PROVIDERS.md) §4.2. Planned, not implemented: the ontology plausibility gate (Phase 3, §3.3) and the production regression check (Phase 4).
- **Date**: 2026-09-27 · condensed 2026-11-16
- **Related**: [`MULTI_PROVIDER_DESIGN.md`](MULTI_PROVIDER_DESIGN.md)

## 1. 배경 및 프로덕션 실사 데이터 분석

### 1.1 프로덕션 실사 배경
2026년 9월 27일, 프로덕션 서버에서 수집된 최신 Support Bundle(`support_bundle_61a676e4`) 실사 중 단일 웹 문서 적재에 **12분 46초(766초)**가 소요되는 심각한 지연 현상이 확인되었다. 직전 적재된 HWP 및 PDF 보고서에서도 각각 9분 26초, 12분 44초가 소요되어 파이프라인 전반에 공통적인 병목이 고착화되어 있음이 입증되었다.

### 1.2 최신 적재 문서 실측 타임라인 (`doc_ddd82c7b40d5`)
* **대상 문서**: *What is JPEG XL: do we really need another image format? | DebugBear* (`https://www.debugbear.com/blog/jpeg-xl-image-format`)
* **문서 성격**: JPEG XL, AVIF, WEBP, PNG, GIF 등 표준 이미지 포맷과 Chrome, Safari, Photoshop, Cloudinary 등 도구/조직 14개 엔티티가 언급된 웹 성능 기술 블로그
* **실행 시각**: 2026-09-27 01:39:22 ~ 01:52:08 KST (**총 766초 소요**)

```
[01:39:22] 적재 시작 (HTML Fetch 완료: ~0.5초)
 ├── 01:39:27 ~ 01:39:58 ( 31.3초) : 구조화 추출 (extract_json + extract_summary)
 ├── 01:39:58 ~ 01:40:49 ( 51.2초) : 가독 상세 본문 생성 (render_detail)
 ├── 01:41:03 ~ 01:44:12 (185.2초) : 엔티티 동일체 판정 (judge_same_entity, 18회 연속 직렬)
 ├── 01:45:57 ~ 01:51:58 (462.9초) : 전역 관계 판정 (judge_relationship, 10회 연속 직렬) ◄◄ [핵심 병목: 60.4%]
 └── 01:51:58 ~ 01:52:08 ( 10.0초) : 주기 재확인 판정 (classify_watch)
[01:52:08] 적재 완료 (네트워크 I/O, Vault 마크다운 동기화, SQLite 커밋 등은 2초 미만)
```

전체 766초 중 **약 740초(96.6%)**가 순수 LLM/CLI 대기 시간이었으며, 이 중 **지식 그래프 엔티티 동일체 판정(185초)과 전역 관계 판정(463초)이 648초(전체의 84.5%)를 차지**했다.

---

## 2. 5대 병목 근본 원인 분석 (Root Cause Analysis)

### 2.1 에이전트 도구 오동작 (Agent Tool Pollution)
`antigravity_provider.py`는 `agy` CLI를 비대화형(`-p`)으로 호출한다. 그러나 `agy`는 기본적으로 파일 읽기, 코드베이스 검색, 셸 명령 실행, 백그라운드 태스크 관리 도구가 탑재된 자율 코딩 에이전트이다.
* `judge_relationship` 프롬프트에 명시된 *"Decide if there is a DIRECT, FACTUAL, and MEANINGFUL relationship between Entity A and Entity B"* 지시문을 수신한 `agy`는 스스로 사실 관계를 연구하려 시도했다.
* 텔레메트리 에러 상세 분석 결과:
  * `PIK ↔ FUIF` (102.0초): `"error":"search path file:///app/tests does not exist"` (코드 검색 시도 후 실패)
  * `PNG ↔ AVIF` (93.5초): `"error":"cannot kill task ... task is not running"` (백그라운드 태스크 제어 시도)
  * `PNG ↔ PlantUML` (63.3초): `"error":"permission check failed for read_file \"/root/.gemini/antigravity-cli/brain\""` (브레인 파일 열람 시도)
* 이로 인해 단순 텍스트/JSON 분류 작업이 다중 턴 에이전트 도구 루프로 변질되어 단일 호출당 60~102초가 소모되었다.

### 2.2 작업 디렉터리(`cwd`) 컨텍스트 오염
`subprocess.run(cmd, ...)` 호출 시 `cwd`가 지정되지 않아 컨테이너 애플리케이션 루트(`/app`)가 그대로 워크스페이스로 주입되었다.
* `agy` 기동 시 내부 파일 와처(`file_watcher.go`)가 `/app` 내 수천 개 파일과 `.git` 트리를 순회하며 인덱싱하는 오버헤드가 매 CLI 기동마다 누적되었다.

### 2.3 추론 강도(Effort) 미분화에 따른 Thinking 턴 지연
전역 설정(`CLAIRE_AGY_EFFORT=medium`)이 모든 하위 작업에 일괄 적용되었다.
* `judge_same_entity`는 "SAME" 또는 "DIFFERENT" 한 단어만 출력하면 되는 단순 결정 작업임에도 `medium` 추론 사고가 강제되어 턴당 9~16초가 소요되었다.
* 판정 작업에 `effort=low`를 적용할 경우 품질 저하 없이 4~6초 수준으로 단축 가능함이 실증되었다.

### 2.4 파이프라인의 완전 동기 직렬(Serial) 실행
`AntigravityProvider`는 `self.max_concurrency = int(getattr(settings, "agy_max_concurrency", 2))` 세마포어를 내장하고 있으나, 호출부인 `pipeline.py`의 엔티티 해소 루프와 관계 판정 루프(`for e_node, cand_id, score in eval_candidates:`)가 완전히 단일 스레드로 직렬 동기 호출되고 있었다. 이로 인해 28건(동일체 18건 + 관계 10건)의 CLI 호출이 1건씩 순차 실행되며 지연이 그대로 누적되었다.

### 2.5 텔레메트리 식별자 결손
`judge_same_entity`와 `judge_relationship` 내부에서 `_run_cli`를 호출할 때 `call_type`과 `document_id`를 전달하지 않아 텔레메트리에 `document_id=None`, `call_type="cli"`로 기록되었다. 이로 인해 어떤 문서의 인제스트 과정에서 몇 건의 판정이 유발되었는지 즉시 집계되지 않는 관측성 결손이 발생했다.

---

## 3. 코드 레벨 개선 아키텍처

### 3.1 and 3.2 Decisions in force for the implemented part

- **Calls that need no tools forbid tools.** The prompt gets a direct-inference header, and the process starts in an empty working directory. Reason: §2.1 and §2.2. Only research may use tools.
- **Simple judgements use low effort.** Reason: §2.3. The result of a one-word decision did not get better with more thinking time.
- **Relation judgements run in parallel, graph writes stay serial.** The worker count follows the concurrency limit of the provider. Reason: §2.4. SQLite writes must stay in one thread.
- **Each call carries its call type and document.** Reason: §2.5. Telemetry must show which document caused which calls.

### 3.3 온톨로지 양립성 사전 게이트 (Heuristic Filter)

벡터 유사도가 임계값(`0.70`)을 넘더라도 현실적으로 온톨로지 관계를 맺을 수 없는 명백한 이종 도메인 조합은 LLM 호출 전에 선별 탈락시킨다.
* **배제 조합 예시**:
  * `Concept(이미지 압축 포맷)` ↔ `Tool(다이어그램 소프트웨어)` (예: PNG ↔ PlantUML)
  * `Org(표준화 기구)` ↔ `Org(일반 전자상거래 기업)` 간 직접 관계 (예: JPEG Group ↔ Alibaba)
* 엔티티 타입 조합 유효성(`is_plausible_relation_pair(type_a, type_b)`) 검사를 선행하여 10개 후보를 2~4개의 유의미한 후보로 사전 축소한다.

---

## 4. 성능 개선 전후 비교 및 정량적 벤치마크 기대치

최신 적재 문서(`doc_ddd82c7b40d5`, 기준 12분 46초)에 본 설계를 적용했을 때의 정량적 기대치이다.

| 실행 단계 | 기존 실측치 | 개선 후 기대치 | 개선 요인 |
| :--- | :--- | :--- | :--- |
| **`judge_relationship`** (최대 10회) | **462.9초 (7분 43초)** | **20 ~ 30초** | 도구 차단, `effort=low`, 사전 필터(10→3쌍), 병렬화 |
| **`judge_same_entity`** (18회) | **185.2초 (3분 5초)** | **45 ~ 60초** | `effort=low`, 작업 디렉터리 격리 |
| **`extract` + `render_detail`** | **82.5초 (1분 22초)** | **70 ~ 75초** | 작업 디렉터리 격리로 `file_watcher` 지연 제거 |
| **`classify_watch`** | **10.0초** | **5 ~ 6초** | `effort=low` 적용 |
| **총 적재 소요 시간** | **12분 46초 (766초)** | **약 2분 20초 (140초)** | **약 82% 지연 단축 달성** |

---

## 5. 실행 및 배포 로드맵

1. **Phase 1: Provider 레벨 격리 및 도구 차단 (`src/claire/extract/antigravity_provider.py`)** (구현 완료)
   * `_run_cli`에 `allow_tools=False`, 시스템 지침 주입, `cwd` 격리, `effort="low"` 분기 적용.
   * `judge_same_entity`, `judge_relationship`에 `call_type` 및 `document_id` 전달.
2. **Phase 2: Pipeline 레벨 관계 판정 병렬화 (`src/claire/ingest/pipeline.py`)** (구현 완료)
   * Phase 2 `eval_candidates` 루프에 `ThreadPoolExecutor` 적용 및 메인 스레드 안전 DB 커밋 분리.
3. **Phase 3: 온톨로지 양립성 사전 게이트 적용** (로드맵)
   * 무의미한 교차 관계 평가 억제로 LLM 호출 횟수 자체를 60% 이상 절감.
4. **Phase 4: 운영 환경 배포 및 텔레메트리 회귀 검증** (로드맵)
   * 배포 후 신규 적재 문서의 텔레메트리를 통해 평균 적재 시간이 2~3분 이내로 안정화되는지 관측.


