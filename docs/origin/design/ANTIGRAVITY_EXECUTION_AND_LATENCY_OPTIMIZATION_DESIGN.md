# Antigravity CLI 적재 지연 해소 및 실행 격리·최적화 아키텍처 설계

> [!NOTE]
> 전체 프로바이더 통합 스펙 및 설정 레퍼런스는 [MULTI_PROVIDER_DESIGN.md](MULTI_PROVIDER_DESIGN.md)에 집약되어 있습니다. 본 문서는 Antigravity CLI의 실측 프로덕션 지연 분석 및 실행 격리 심층 연구 자료입니다.

작성일: 2026-09-27 · 상태: **Phase 1-2 Implemented / Phase 3-4 Roadmap** · 기준: [GOALS.md](../GOALS.md) 트랙1/2 추출 성능 및 신뢰성 · 관련 문서: [KNOWLEDGE_GRAPH_LINKING_AND_CALIBRATION_DESIGN.md](KNOWLEDGE_GRAPH_LINKING_AND_CALIBRATION_DESIGN.md), [MULTI_PROVIDER_DESIGN.md](MULTI_PROVIDER_DESIGN.md), [TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md](TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md), [CLAIRE_ARCHITECTURE_ROADMAP.md](../CLAIRE_ARCHITECTURE_ROADMAP.md), [ENVIRONMENT_VARIABLES.md](../implementation/ENVIRONMENT_VARIABLES.md)

---

## 1. 배경 및 프로덕션 실사 데이터 분석

### 1.1 프로덕션 실사 배경
2026년 9월 27일, 프로덕션 환경(`cb.netspheres.org`)에서 수집된 최신 Support Bundle(`support_bundle_61a676e4`) 실사 중 단일 웹 문서 적재에 **12분 46초(766초)**가 소요되는 심각한 지연 현상이 확인되었습니다. 직전 적재된 HWP 및 PDF 보고서에서도 각각 9분 26초, 12분 44초가 소요되어 파이프라인 전반에 공통적인 병목이 고착화되어 있음이 입증되었습니다.

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
 └── 01:51:58 ~ 01:52:08 ( 10.0초) : 주기 크롤링 판정 (classify_watch)
[01:52:08] 적재 완료 (네트워크 I/O, Vault 마크다운 동기화, SQLite 커밋 등은 2초 미만)
```

전체 766초 중 **약 740초(96.6%)**가 순수 LLM/CLI 대기 시간이었으며, 이 중 **지식 그래프 엔티티 동일체 판정(185초)과 전역 관계 판정(463초)이 648초(전체의 84.5%)를 차지**했습니다.

---

## 2. 5대 병목 근본 원인 분석 (Root Cause Analysis)

### 2.1 에이전트 도구 오동작 (Agent Tool Pollution)
`antigravity_provider.py`는 `agy` CLI를 비대화형(`-p`)으로 호출합니다. 그러나 `agy`는 기본적으로 파일 읽기, 코드베이스 검색, 셸 명령 실행, 백그라운드 태스크 관리 도구가 탑재된 자율 코딩 에이전트입니다.
* `judge_relationship` 프롬프트에 명시된 *"Decide if there is a DIRECT, FACTUAL, and MEANINGFUL relationship between Entity A and Entity B"* 지시문을 수신한 `agy`는 스스로 사실 관계를 연구하려 시도했습니다.
* 텔레메트리 에러 상세 분석 결과:
  * `PIK ↔ FUIF` (102.0초): `"error":"search path file:///app/tests does not exist"` (코드 검색 시도 후 실패)
  * `PNG ↔ AVIF` (93.5초): `"error":"cannot kill task ... task is not running"` (백그라운드 태스크 제어 시도)
  * `PNG ↔ PlantUML` (63.3초): `"error":"permission check failed for read_file \"/root/.gemini/antigravity-cli/brain\""` (브레인 파일 열람 시도)
* 이로 인해 단순 텍스트/JSON 분류 작업이 다중 턴 에이전트 도구 루프로 변질되어 단일 호출당 60~102초가 소모되었습니다.

### 2.2 작업 디렉터리(`cwd`) 컨텍스트 오염
`subprocess.run(cmd, ...)` 호출 시 `cwd`가 지정되지 않아 컨테이너 애플리케이션 루트(`/app`)가 그대로 워크스페이스로 주입되었습니다.
* `agy` 기동 시 내부 파일 와처(`file_watcher.go`)가 `/app` 내 수천 개 파일과 `.git` 트리를 순회하며 인덱싱하는 오버헤드가 매 CLI 기동마다 누적되었습니다.

### 2.3 추론 강도(Effort) 미분화에 따른 Thinking 턴 지연
전역 설정(`CLAIRE_AGY_EFFORT=medium`)이 모든 하위 작업에 일괄 적용되었습니다.
* `judge_same_entity`는 "SAME" 또는 "DIFFERENT" 한 단어만 출력하면 되는 단순 결정 작업임에도 `medium` 추론 사고가 강제되어 턴당 9~16초가 소요되었습니다.
* 판정 작업에 `effort=low`를 적용할 경우 품질 저하 없이 4~6초 수준으로 단축 가능함이 실증되었습니다.

### 2.4 파이프라인의 완전 동기 직렬(Serial) 실행
`AntigravityProvider`는 `self.max_concurrency = int(getattr(settings, "agy_max_concurrency", 2))` 세마포어를 내장하고 있으나, 호출부인 `pipeline.py`의 엔티티 해소 루프와 관계 판정 루프(`for e_node, cand_id, score in eval_candidates:`)가 완전히 단일 스레드로 직렬 동기 호출되고 있었습니다. 이로 인해 28건(동일체 18건 + 관계 10건)의 CLI 호출이 1건씩 순차 실행되며 지연이 그대로 누적되었습니다.

### 2.5 텔레메트리 식별자 결손
`judge_same_entity`와 `judge_relationship` 내부에서 `_run_cli`를 호출할 때 `call_type`과 `document_id`를 전달하지 않아 텔레메트리에 `document_id=None`, `call_type="cli"`로 기록되었습니다. 이로 인해 어떤 문서의 인제스트 과정에서 몇 건의 판정이 유발되었는지 즉시 집계되지 않는 관측성 결손이 발생했습니다.

---

## 3. 코드 레벨 개선 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Claire Bible Ingestion Core                           │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 ▼                                               ▼
┌───────────────────────────────────┐           ┌───────────────────────────────────┐
│     pipeline.py 동시성 및 최적화     │           │   antigravity_provider.py 격리    │
├───────────────────────────────────┤           ├───────────────────────────────────┤
│ • ThreadPoolExecutor 병렬 판정     │           │ • allow_tools=False (도구 차단)   │
│ • 온톨로지 양립성 사전 게이트        │           │ • cwd="/tmp/claire_agy_clean"     │
│ • 다중 후보 배치(Batch) 인터페이스   │           │ • 작업별 맞춤 effort (low 분기)   │
│ • call_type / doc_id 전달         │           │ • 텔레메트리 식별자 완전 주입     │
└───────────────────────────────────┘           └───────────────────────────────────┘
```

### 3.1 `AntigravityProvider`: 순수 추론 모드 및 격리 환경 구축

#### (1) `allow_tools` 매개변수 및 시스템 지시문 주입
`research()`를 제외한 모든 호출(`extract`, `render_detail`, `judge_*`, `classify_*`)은 도구 호출이 필요 없는 순수 생성/분류 작업입니다.

```python
# src/claire/extract/antigravity_provider.py

def _run_cli(
    self,
    prompt: str,
    *,
    json_schema: dict | None = None,
    output_format: str = "json",
    dangerously_skip_permissions: bool = True,
    effort: str | None = None,
    call_type: str = "cli",
    document_id: str | None = None,
    allow_tools: bool = False,  # 기본값: 도구 비활성화 (순수 추론 모드)
) -> Any:
    # 도구가 불필요한 작업에 강력한 단일 턴 생성 지침 주입
    if not allow_tools:
        suppression_header = (
            "[SYSTEM DIRECTIVE: DIRECT INFERENCE ONLY]\n"
            "Do NOT invoke any tools, search files, read repository paths, or run shell commands.\n"
            "Generate the requested output immediately in a single response turn based solely on "
            "the provided prompt context and internal knowledge.\n\n"
        )
        prompt = suppression_header + prompt
```

#### (2) 작업 디렉터리(`cwd`) 임시 격리
컨테이너 내부 소스코드 탐색 및 파일 와처 오버헤드를 원천 차단하기 위해 독립된 빈 임시 디렉터리를 `cwd`로 지정합니다.

```python
    clean_cwd = Path(tempfile.gettempdir()) / "claire_agy_clean"
    clean_cwd.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        cmd,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=self.timeout,
        cwd=clean_cwd if not allow_tools else None,
        check=False,
    )
```

#### (3) 작업 유형별 `effort` 동적 분기 및 텔레메트리 식별자 전달
단순 분류/판정 작업은 `effort="low"`를 적용하여 Thinking 지연을 최소화합니다.

```python
    def judge_same_entity(
        self, mc: MergeCandidate, *, document_id: str | None = None
    ) -> bool:
        prompt = judge_same_entity_prompt(mc)
        try:
            res = self._run_cli(
                prompt,
                output_format="text",
                effort="low",  # 동일체 판정은 low로 즉시 판정
                call_type="judge_same_entity",
                document_id=document_id,
                allow_tools=False,
            )
            return str(res).strip().upper().startswith("SAME")
        except Exception as e:
            logger.warning("judge_same_entity call failed: %s", e)
            return False

    def judge_relationship(
        self, rc: RelationCandidate, *, document_id: str | None = None
    ) -> RelationJudgement:
        prompt = judge_relationship_prompt(rc)
        schema = RelationJudgement.model_json_schema()
        try:
            data = self._run_cli(
                prompt,
                json_schema=schema,
                output_format="json",
                effort="low",  # 관계 판정 100초 -> 10초 내외 단축
                call_type="judge_relationship",
                document_id=document_id,
                allow_tools=False,
            )
            if isinstance(data, dict):
                return RelationJudgement.model_validate(data)
            return RelationJudgement.model_validate_json(str(data))
        except Exception as e:
            logger.warning("judge_relationship parsing failed: %s", e)
            return RelationJudgement(has_relation=False, reason=f"판정 실패: {e}")
```

---

### 3.2 `pipeline.py`: 판정 루프 동시성 병렬화

`pipeline.py`의 전역 관계 판정(Phase 2) 루프를 `ThreadPoolExecutor` 기반 동시성 실행으로 전환합니다. 이미 `AntigravityProvider` 내부에 세마포어(`self._sem`)가 존재하므로, `max_workers = getattr(provider, "max_concurrency", 2)`로 안전하게 병렬 처리됩니다.

```python
# src/claire/ingest/pipeline.py Phase 2 관계 판정부

if eval_candidates:
    if on_progress:
        on_progress("전역 지식 관계(Cross-link) 판정", f"후보 {len(eval_candidates)}쌍 평가")
    emit_progress(f"전역 지식 관계(Cross-link) 판정 ({len(eval_candidates)}쌍)")

    max_workers = min(len(eval_candidates), getattr(provider, "max_concurrency", 2))

    def _eval_single_candidate(item: tuple[Entity, str, float]):
        e_node, cand_id, score = item
        cand = dbm.get_entity(conn, cand_id)
        if cand is None:
            return None

        rc = RelationCandidate(
            entity_a_name=e_node.name,
            entity_a_type=e_node.type,
            entity_a_observations=e_node.observations[:5],
            entity_a_aliases=e_node.aliases[:5],
            entity_b_name=cand.name,
            entity_b_type=cand.type,
            entity_b_observations=cand.observations[:5],
            entity_b_aliases=cand.aliases[:5],
            similarity_score=score,
            context=f"문서: {doc.title or doc.id}\n요약: {report.summary[:300]}",
        )
        try:
            try:
                judgement = judge_rel_method(rc, document_id=doc.id)
            except TypeError:
                judgement = judge_rel_method(rc)
        except Exception:
            judgement = None
        return (e_node, cand, judgement)

    # 병렬 평가 실행
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        evaluated_results = list(executor.map(_eval_single_candidate, eval_candidates))

    # GraphStore 커밋 (DB 쓰기는 메인 스레드 직렬화 보장)
    for res_item in evaluated_results:
        if not res_item:
            continue
        e_node, cand, judgement = res_item
        if judgement and judgement.has_relation and judgement.relation_type:
            rtype, _ = classify_relation_type(judgement.relation_type)
            s_id, t_id = (cand.id, e_node.id) if judgement.direction == "backward" else (e_node.id, cand.id)
            s_name, t_name = (cand.name, e_node.name) if judgement.direction == "backward" else (e_node.name, cand.name)

            added_rel = gstore.add_edge(s_id, t_id, rtype, confidence=judgement.confidence, sources=[doc.id])
            if added_rel is not None:
                report.relations_added += 1
                report.cross_relations_added += 1
                report.cross_linked_relations.append(f"{s_name} -> {t_name} ({rtype})")
                if cand not in touched_entities:
                    touched_entities.append(cand)
```

---

### 3.3 온톨로지 양립성 사전 게이트 (Heuristic Filter)

벡터 유사도가 임계값(`0.70`)을 넘더라도 현실적으로 온톨로지 관계를 맺을 수 없는 명백한 이종 도메인 조합은 LLM 호출 전에 선별 탈락시킵니다.
* **배제 조합 예시**:
  * `Concept(이미지 압축 포맷)` ↔ `Tool(다이어그램 소프트웨어)` (예: PNG ↔ PlantUML)
  * `Org(표준화 기구)` ↔ `Org(일반 전자상거래 기업)` 간 직접 관계 (예: JPEG Group ↔ Alibaba)
* 엔티티 타입 조합 유효성(`is_plausible_relation_pair(type_a, type_b)`) 검사를 선행하여 10개 후보를 2~4개의 유의미한 후보로 사전 축소합니다.

---

## 4. 성능 개선 전후 비교 및 정량적 벤치마크 기대치

최신 적재 문서(`doc_ddd82c7b40d5`, 기준 12분 46초)에 본 설계를 적용했을 때의 정량적 기대치입니다.

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


