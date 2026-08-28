# Multi-Provider & Hyperscaler Calibration Architecture Design

> **문서 상태**: 설계 완료 (Proposal)  
> **관련 문서**: [SYNTHESIS_REDESIGN.md](../../upstream/SYNTHESIS_REDESIGN.md), [ONEHOP_MERGE_DESIGN.md](../../upstream/ONEHOP_MERGE_DESIGN.md)

---

## 1. 배경 및 문제 정의

Claire Bible은 지식그래프 구조화 추출, 상세 렌더링, 검색 종합, 맥락 웹 리서치 등에 LLM을 활용합니다. 현재는 `GeminiProvider`와 `AntigravityProvider`(`agy` CLI)가 존재하지만, 향후 다양한 하이퍼스케일러(OpenAI, Anthropic Claude, Local/Ollama, AWS Bedrock 등)의 모델을 추가할 때 다음과 같은 구조적 한계와 차이점에 직면합니다.

### 1.1 현재 아키텍처의 한계
1. **프롬프트 템플릿의 결합**: 시스템 프롬프트(`_SYS`), `PROMPT_VERSION`, 렌더링 지침이 `gemini_provider.py`에 모듈 변수로 묶여 있거나 개별 프로바이더 파일에 중복 복사되어 있음.
2. **하이퍼스케일러별 이질성**: 모델마다 구조화 출력(JSON Schema), 그라운딩(Search), 벡터 임베딩 규격, 에러/쿼터 백오프 방식이 완전히 다름.
3. **재적재(Re-extraction) 시험 및 튜닝 체계 부재**: 프로바이더나 하이퍼스케일러를 교체/추가할 때, 기존 그래프 데이터를 파괴하지 않고 모델별 추출 품질을 사전 검증(Dry-run/A-B 비교)할 수 있는 평가 하네스가 없음.

---

## 2. 하이퍼스케일러별 핵심 차이점과 설계 구분점

새 프로바이더 도입 시 모델의 고유 특성으로 인해 동작이 달라지므로, 아키텍처 상에 다음과 같은 **5대 구분점**을 설계에 반영해야 합니다.

```mermaid
graph TD
    subgraph Core Dimensions
        A[1. Structured Output Mechanism]
        B[2. Embedding Dimension & Semantic Space]
        C[3. Grounding & Web Search Interface]
        D[4. Rate Limits & Quota Backoff]
        E[5. Prompt Calibration & Tone Adherence]
    end
    
    A --> P[Provider Adapter Layer]
    B --> P
    C --> P
    D --> P
    E --> P
    P --> EVal[Re-extraction Evaluation & Tuning Harness]
```

### (1) 구조화 출력(Structured Output) 방식의 차이
* **Google Gemini**: `response_format={"type": "text", "mime_type": "application/json", "schema": ...}`
* **OpenAI (Structured Outputs)**: `response_format={"type": "json_schema", "json_schema": {"strict": True, ...}}` (모든 키 `required` 필수, `additionalProperties: False` 엄격 강제).
* **Anthropic Claude**: `tool_choice={"type": "tool", "name": "extract_knowledge"}` 형태의 도구 호출을 통한 강제.
* **Local/Ollama (vLLM/llama.cpp)**: GBNF 문법(Grammar) 샘플링 또는 JSON 블록 파싱 + 방어적 정규화(`_coerce`).
* 👉 **구분점**: 프로바이더 드라이버가 모델에 맞는 `SchemaEnforcementStrategy`를 선택하고, 공통 Pydantic 모델을 각 플랫폼의 스키마 규격으로 변환/폴백 처리해야 함.

### (2) 벡터 임베딩 차원 및 의미 공간의 비호환성
* Gemini `gemini-embedding-001` (768/3072차원), OpenAI `text-embedding-3-small` (1536차원), Voyage AI, 로컬 BGE/E5 (384/768차원) 등 **하이퍼스케일러 간 임베딩 벡터는 차원과 공간이 달라 상호 유사도 비교가 불가능**.
* 👉 **구분점**:
  * `VectorStore`에 프로바이더/임베딩 모델 메타데이터(`embed_provider`, `embed_model`, `embed_dim`)를 명시.
  * 프로바이더 교체 시 **지식그래프 텍스트 추출(Extractions)**과 **벡터 임베딩(Vector Embeddings)**의 재구축 단계를 독립적으로 분리.

### (3) 웹 검색(Grounding) 연동 메커니즘
* **Gemini**: 모델 내장 `google_search` 툴 지원.
* **Antigravity CLI**: `agy` 에이전트 내장 검색 도구 실행.
* **OpenAI / Claude / Local**: 빌트인 검색이 없으므로 Claire 내부의 Search Engine(SerpAPI, Tavily, Brave, DuckDuckGo 등)을 호출하는 Tool Calling 루프가 필요.
* 👉 **구분점**: `research()` 및 `judge_research()` 구현 시 Native Grounding 지원 여부에 따라 `GroundingDriver`를 플러그인 형태로 조립.

### (4) 레이트 리밋(Rate Limit) 및 쿼터 관리
* 하이퍼스케일러마다 반환하는 HTTP 상태 코드 및 헤더가 상이함:
  * Gemini: `RESOURCE_EXHAUSTED` (429), 메시지 내 `retryDelay: Xs`.
  * OpenAI / Anthropic: `RateLimitError` (429), `retry-after` 헤더.
  * Local: Concurrency Semaphore 기반 자체 병렬 제어.
* 👉 **구분점**: 공통 `RateLimiter` 및 `RetryPolicy`를 정의하되 플랫폼별 에러 파서를 어댑터로 분리.

### (5) 프롬프트 캘리브레이션 및 한국어 문어체 준수도
* 모델 크기 및 학습 데이터에 따라 한국어 문어체(`~한다`, `~이다`), 고유명사 원문 보존, JSON 준수 강도가 다름.
* 👉 **구분점**: 기본 템플릿(Base Prompt)을 공유하되, 모델별로 프롬프트 수식어(Modifier / System Role 위치 / 예시 퓨샷)를 보정할 수 있는 캘리브레이션 슬롯 제공.

---

## 3. 상세 아키텍처 설계

```
src/claire/extract/
├── prompts/                      # [신규] 중앙 프롬프트 엔진
│   ├── base.py                   # 공통 템플릿, PROMPT_VERSION, 시스템 룰 정의
│   ├── render.py                 # render_detail, _images_block 템플릿
│   ├── research.py               # research, judge_research 템플릿
│   └── calibration.py            # 모델/하이퍼스케일러별 프롬프트 보정 슬롯
├── drivers/                      # [신규] 하이퍼스케일러 전송 드라이버
│   ├── base.py                   # BaseLLMDriver (인터페이스 및 공통 재시도)
│   ├── gemini.py                 # Gemini GenAI Interactions 드라이버
│   ├── antigravity.py            # Antigravity CLI (agy) 드라이버
│   ├── openai_like.py            # OpenAI / Azure / Ollama 호환 드라이버
│   └── anthropic.py              # Anthropic Claude 드라이버
├── eval/                         # [신규] 재적재 시험 및 튜닝 하네스
│   ├── runner.py                 # Dry-run / Shadow 재추출 실행기
│   ├── comparator.py             # 기존 extraction vs 신규 extraction diff 분석기
│   └── metrics.py                # 엔티티 수, 문어체 통과율, 스키마 유효성 평가
├── provider.py                   # Provider 상위 조율 및 팩토리
├── resolver.py                   # 엔티티 해소/머지 로직
└── types.py                      # ExtractionResult 등 계약 Pydantic 모델
```

### 3.1 프롬프트 엔진 (`prompts/`)
* 모든 프롬프트 문자열을 단일 모듈로 집약하여 중복을 제거합니다.
* `PROMPT_VERSION`을 중앙에서 통제하고, 문어체 규칙(`LANGUAGE & STYLE`)이 모든 파이프라인(추출, 가독 렌더링, 요약, 리서치, 판정)에 일관되게 주입되도록 보장합니다.

### 3.2 드라이버 계층 (`drivers/`)
* **`BaseLLMDriver`**:
  * `generate_text(prompt, system_instruction, temperature, ...) -> str`
  * `generate_structured(prompt, schema, system_instruction, ...) -> dict`
  * `embed(text) -> list[float]`
  * `grounded_search(query, context) -> dict`
* 각 드라이버는 네트워크 호출, SDK 인터페이스, 인증, 에러 핸들링만 전담하여 비즈니스 로직과 프롬프트 로직으로부터 분리됩니다.

### 3.3 재적재 시험 및 튜닝 하네스 (`eval/`)
새 하이퍼스케일러나 새 모델을 도입할 때 전체 그래프를 덮어쓰기 전, 튜닝 및 회귀 검증을 수행하는 프레임워크를 제공합니다.

#### 동작 흐름:
1. **Sample Selection**: 기존 `documents`에서 대표 문서 $N$개(URL, PDF, YouTube, 짧은 메모 등)를 추출.
2. **Shadow Execution**: 타겟 프로바이더(`--test-provider openai --model gpt-4o`)로 비파괴적 추출 수행.
3. **Automated Metrics & Diff Reporting**:
   * **Schema Validity**: JSON Schema 유효성 및 Coerce 필요 여부.
   * **Style Adherence**: 종결어미 검사(문어체 `~한다/~이다` vs 경어체 `~합니다/~해요` 비율).
   * **Entity/Relation Yield**: 기존 Gemini 추출 대비 엔티티/관계 검출 수 및 오탐/누락 비교.
   * **Latency & Cost**: 호출 시간 및 추정 비용 측정.
4. **Tuning Feedback**: 결과에 따라 `calibration.py`에서 온도(`temperature`), 프롬프트 힌트, 스키마 전략 조정.

```bash
# 예시: 신규 프로바이더 튜닝 및 비교 CLI 명령
./cb-manuscript eval --provider openai --model gpt-4o --sample-size 20 --compare-with current
```

---

## 4. 데이터베이스 및 계보(Lineage) 관리

`extractions` 테이블 및 메타데이터를 보강하여 여러 하이퍼스케일러의 실행 이력을 추적합니다:

```sql
-- extractions 테이블 메타데이터 보강
ALTER TABLE extractions ADD COLUMN provider_kind TEXT; -- 'gemini' | 'openai' | 'anthropic' | 'agy' | 'local'
ALTER TABLE extractions ADD COLUMN latency_ms REAL;
ALTER TABLE extractions ADD COLUMN schema_valid INTEGER DEFAULT 1;
```

* **비파괴적 백필(Non-destructive Backfill)**:
  * 프롬프트나 프로바이더 교체 시 `extractions`에 새 레코드가 쌓이고, `documents.detail` 또는 지식그래프 그래프 엔티티는 검증이 완료된 시점에 승격(Promote)하는 방식을 지원합니다.

---

## 5. 단계적 구현 마일스톤 (Milestones)

> **현재 단계 범위**: OpenAI 및 Anthropic은 즉시 구현하지 않고 향후 마일스톤으로 보류합니다. 현재 단계에서는 **프롬프트 엔진 분리(P1)** 및 **Gemini / Antigravity 대상 공통 아키텍처 적용(P2)**을 우선 구현합니다.

| 단계 | 마일스톤 | 대상 | 작업 내용 | 진행 상태 |
|:---|:---|:---|:---|:---|
| **P1** | **중앙 프롬프트 엔진 구축** | 공통 | `prompts/` 모듈로 `_SYS`, 문어체 룰, 렌더링/요약/리서치 템플릿 집약 | **현재 구현** |
| **P2** | **Gemini & Antigravity 구조 적용** | Gemini, agy | 두 프로바이더가 공통 프롬프트 엔진을 사용하도록 리팩토링 및 중복 제거 | **현재 구현** |
| **P3** | **시험/튜닝 하네스 구축** | 검증용 | `eval/` 모듈 및 표본 문서 비교 CLI (`cb-manuscript eval`) 구현 | 향후 마일스톤 |
| **P4** | **외부 하이퍼스케일러 확장** | OpenAI, Claude | OpenAI / Anthropic 호환 드라이버 추가 및 캘리브레이션 슬롯 활성화 | 향후 마일스톤 |
