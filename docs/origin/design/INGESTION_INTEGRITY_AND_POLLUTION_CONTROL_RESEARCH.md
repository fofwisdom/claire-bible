# 원문 보존·서비스 보호·오염 통제의 상충과 지식 무결성 거버넌스 연구

작성일: 2026-08-28 · 상태: **연구 및 아키텍처 제언** · 기준: [GOALS.md](../../../GOALS.md) 트랙1(안정성) / 트랙2(추출·연결 품질) / 관련: [DATA_LIFECYCLE_AND_PURGE_DESIGN.md](DATA_LIFECYCLE_AND_PURGE_DESIGN.md), [TABLE_INGESTION_DESIGN.md](TABLE_INGESTION_DESIGN.md)

---

## 1. 연구 배경: 실제 수집 사례에서 발견된 구조적 한계

Claire Bible 시스템은 웹, 파일, API, 메신저 등 다양한 채널을 통해 지식을 수집하고, LLM을 통해 지식 그래프(온톨로지)와 가독 렌더링 문서를 생성합니다.

그러나 실제 운영 및 대화 사례([대화 2095e8f6-e695-42e3-b365-800a1cad2d33](conversation://2095e8f6-e695-42e3-b365-800a1cad2d33))에서 국가법령정보센터(`law.go.kr`)의 「인공지능 발전과 신뢰 기반 조성 등에 관한 기본법」(총 35,603자, 43개 조문 및 부칙)과 같은 장문 구조화 문서를 수집하는 과정에서 다음과 같은 **본질적인 트릴레마(Trilemma)**가 확인되었습니다:

```text
               [1. 서비스 & 리소스 보호]
               (비용 통제, 타임아웃 방지,
                웹 노이즈/스팸 차단)
                     ▲          ▲
                    /            \
                   /   TRILEMMA   \
                  ▼                ▼
[2. 문맥 보존 & 지식 무결성] ◄──────► [3. 오염 통제 & 수명주기]
(후반부 조문/결론/벌칙 보존,         (구버전 환각 차단, 툼스톤,
 무손실 재추출 및 RAG 신뢰성)         자동확장 오염 증폭 방어)
```

단순히 "본문 글자 수를 20,000자로 제한한다" 또는 "표는 제외한다"와 같은 **단편적인 정적 정책 정의(Static Declarative Policy)**만으로는 서비스 보호, 문맥 상실 방지, 데이터 오염 통제를 동시에 달성하기 어렵다는 점이 실증되었습니다.

---

## 2. 3대 상충 축의 세부 딜레마 분석

### A. 축 1: 서비스 & 리소스 보호 (Protection & Cost/Latency Control)
* **목적**:
  - LLM API 토큰 비용 폭주 방지 및 호출 지연(Latency)/타임아웃 억제.
  - 웹 스크래핑 시 유입되는 수만 개의 하단 댓글, 무한 보일러플레이트, 내비게이션 잡음, 로그 덤프 등 저품질 데이터 차단.
  - 단일 사용자 로컬 환경에서 SQLite DB 및 FTS5 색인 크기의 무제한 비대화 방지.
* **기존 단순 정책의 조치**:
  - 수집(`fetch_web`) 및 LLM 프롬프트 투입 시 일괄 20,000자 상한 슬라이싱.
* **부작용**:
  - 노이즈를 막으려다 정작 중요한 정규 문서의 본문 후반부를 잘라버림.

---

### B. 축 2: 문맥 보존 & 지식 무결성 (Context Preservation & Knowledge Integrity)
* **목적**:
  - 법령(조문/벌칙/부칙), 학술 논문(실험 결과/결론), 기술 명세/RFC(구현 상세/보안 고려사항), 성경/고전 텍스트 등 **후반부에 핵심 가치가 집중된 문서의 온전한 수집**.
  - 향후 프롬프트나 모델이 개선되었을 때 네트워크 재수집 없이 원문 그대로 재추출(`reextract`)할 수 있는 무손실 재생산성(Lossless Reproducibility).
* **기존 단순 정책 적용 시의 참사**:
  - **인공지능기본법 사례**: 35,603자 중 전반부 56%(20,000자)만 저장되고, **제23조 이후 조문 및 가장 핵심적인 제5장(사업자 책무), 제6장(이용자 권익보호), 벌칙/과태료 규정, 부칙 시행일정**이 원문 아카이브(`data/raw/artifacts/*.txt.gz`) 및 DB 저장 시점에 영구 절단됨.
  - LLM 추출 시 전반부 엔티티(위원회, 안전연구소 등)는 추출되나, 후반부의 법적 의무/과태료 관계 및 타 법령 인용 관계(`개인정보 보호법`, `디지털의료제품법` 등)가 누락되어 지식 그래프가 치명적으로 불완전해짐.

---

### C. 축 3: 오염 통제 & 데이터 수명주기 (Pollution Control & Lifecycle Governance)
* **목적**:
  - 폐기된 레거시 규격, 오염된 스크랩, 저품질 웹 문서가 시스템에 잔존하여 발생하는 LLM 환각(Hallucination) 및 지식 그래프 오염 원천 차단 ([DATA_LIFECYCLE_AND_PURGE_DESIGN.md](DATA_LIFECYCLE_AND_PURGE_DESIGN.md)).
  - 1홉 자동 확장(Auto-Expansion) 루프가 불완전하거나 오염된 문서에서 링크를 추출하여 저품질 페이지를 연쇄 적재하는 현상 방어.
* **기존 단순 정책의 맹점**:
  - 슬라이싱 제한을 무조건 해제(무제한 수집)할 경우, 크롤러나 자동 확장이 수십만 자의 웹 스팸/광고/댓글을 여과 없이 흡수하여 지식망 전체가 오염됨.
  - 반대로 일괄 절단하면 불완전한 문서가 적재되어 "부분 오염(Partial Corruption)" 상태로 그래프에 고착됨.

---

## 3. 단순 정책 정의(Static Policy)가 실패하는 4대 이유

1. **도메인 및 스키마 다양성 무시 (Uniform Budget Fallacy)**:
   - 1,000자짜리 단신 블로그 글과 50,000자짜리 법률 조문, 100,000자짜리 오픈소스 RFC를 동일한 20,000자 단일 잣대로 재단할 수 없습니다.
2. **구조적·의미론적 파괴 (Structural & Semantic Mutilation)**:
   - 글자 수 기준으로 기계적으로 뚝 자르면, 마크다운 표, 코드 블록, 법률 조/항/호, XML/JSON 태그가 중간에 깨져 문법적·구조적 결함을 유발합니다.
3. **묵시적 절단(Silent Truncation)과 관측성 결여**:
   - 절단 사실이 기록되지 않으면, 운영자나 LLM 에이전트는 자신이 "불완전하게 절단한 텍스트"를 다루고 있다는 사실조차 인지하지 못한 채 잘못된 결론을 도출합니다.
4. **자율 루프에 의한 오염 증폭 (Feedback Loop Amplification)**:
   - 절단한 텍스트에서 잘못 형성된 엔티티와 관계가 1홉 확장의 타깃이 되어 추가적인 저품질 수집을 유발합니다.

---

## 4. Claire Bible의 대응 현황 및 단계별 발전 내역

```text
[1단계: 단순 슬라이싱 (레거시)]
  text[:20000] 기계적 절단 ──> 표 절단, 본문 압살, 사후 추적 불가

[2단계: 구조 예외 및 관측성 확보 (현재 완료)]
  ├── table_budget.py: 표(Markdown/AsciiDoc/HTML) 100% 무손실 보존
  ├── doc.meta: raw_truncated, orig_chars, raw_chars, directive 명시 기록
  └── Web UI: docmeta 우측에 '✂️ 원문 일부 절단', '🎯 초점' 뱃지 노출

[3단계: 차세대 지식 무결성 거버넌스 (연구 과제)]
  ├── 도메인 인지형 스토리지 계층 디커플링 (Raw Storage vs Processing View)
  ├── 구문 인지형 계층적 동적 청킹 (Syntax-Aware Chunking)
  └── 메타인지적 품질 점수 및 능동적 적재 승인 체계
```

---

## 5. 차세대 아키텍처 연구 과제 (Research & Roadmap)

### 과제 1: 도메인 인지형 스토리지 계층 디커플링 (Domain-Aware Tiered Architecture)
* **스토리지(Layer 1/2)와 프로세싱(LLM Tier)의 완전 분리**:
  - **Layer 1/2 (Raw Archive)**: `doc.raw_text` 및 `artifacts/*.txt.gz`는 원본 텍스트 전체를 **글자 수 제한 없이 100% 무손실 압축 저장**합니다.
  - **LLM Tier (Extraction View)**: LLM 프롬프트 주입 단계에서만 모델 및 도메인에 맞는 뷰(View)를 동적으로 생성합니다.
* **`source_type` 세분화 및 도메인별 차등 정책**:
  - `law`(법령/규정), `paper`(논문), `spec`(기술명세), `scripture`(경전/역본), `web`(일반웹), `note`(메모).
  - 예: `source_type == "law"`인 경우 Gemini의 대용량 컨텍스트 윈도우를 활용하여 100,000자까지 프롬프트 예산을 탄력 배정.

---

### 과제 2: 구문 인지형 계층적 동적 청킹 (Syntax-Aware Hierarchical Chunking)
* **단순 글자 수 하드 컷(Hard-Cut) 탈피**:
  - **법령 문서**: 장(Chapter) / 절(Section) / 조(Article) 경계 기준 분할.
  - **기술/마크다운 문서**: H1/H2 헤더 및 코드 블록 경계 기준 분할.
  - **테이블**: 기존 [TABLE_INGESTION_DESIGN.md](TABLE_INGESTION_DESIGN.md)의 무손실 보존 메커니즘 유지.
* **다중 청크 맵-리듀스 추출 (Map-Reduce Extraction)**:
  - 초장문 문서는 섹션별로 부분 엔티티를 추출한 뒤 최상위에서 통합 해소(Resolution)하는 파이프라인 연구.

---

### 과제 3: 메타인지적 품질 점수 및 능동적 거버넌스 (Active Quality Governance)
* **손실률 및 정보 밀도 지표 산출**:
  $$\text{Truncation Ratio} = 1 - \frac{\text{raw\_chars}}{\text{orig\_chars}}$$
  - 절단 손실률이 30%를 초과하는 중요 문서가 감지되면 시스템이 경고를 발행하고 운영자에게 예산 확대 재추출(`reextract --budget=extended`)을 추천.
* **초점(Focus, `directive`) 기반 가독 본문 가변 스케일링**:
  - 사용자가 특정 초점(`directive`)을 명시한 경우, 기존 "A4 1~2장 요약" 분량 제약을 넘어 해당 초점에 맞춰 장문 구조를 보존하며 상세 해설을 생성하도록 프롬프트 엔진 확장.

---

## 6. 핵심 설계 원칙 요약

1. **무손실 원천 보존 (Lossless Inbound First)**:
   인입된 원본은 어떤 경우에도 훼손하거나 임의로 잘라버리지 않고 영구 압축 보관한다.
2. **투명한 제약 관측성 (Observable Constraints)**:
   처리 과정에서 리소스 제한으로 인해 절단이나 압축이 발생한 경우, 그 손실 내역(원문 길이, 적재 길이, 절단 여부)을 투명하게 메타데이터로 기록하고 UI에 노출한다.
3. **정적 단일 정책 배제 (Adaptive Governance over Static Rules)**:
   도메인, 스키마, 사용자 지시어(`directive`)의 맥락을 고려하는 적응형 파이프라인을 구축한다.
4. **절단 섹션 상세 작성 배제 (Exclusion of Truncated Sections in Detail Rendering)**:
   원문을 절단하여 적재 및 상세 작성 시, 절단되어 내용이 유실된 섹션(미완성 문단, 잘린 소제목·조항 등)은 상세를 작성하지 않는다. 불완전한 텍스트 파편에 기반한 환각(Hallucination)과 불완전한 지식 생성(Partial Corruption)을 원천 차단하고 온전히 보존된 섹션까지만 상세 본문으로 구성한다.
