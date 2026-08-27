# 원문 테이블 적재 및 문자 수 제한 예외 처리 보완 설계

작성일: 2026-08-27 · 상태: **설계 및 구현 완료** · 기준: [GOALS.md](../../../GOALS.md) 트랙2(추출·연결 품질) / 관련: [ONEHOP_MERGE_DESIGN.md](ONEHOP_MERGE_DESIGN.md)

---

## 1. 배경 및 문제 정의 (Data Corruption Risk)

Claire Bible은 웹, PDF, 로컬 파일 등 다양한 소스로부터 문서를 수집(Ingest)하고, LLM을 통해 지식그래프(엔티티·관계·요약)를 추출하며, 가독 렌더링 문서(Markdown/AsciiDoc)를 생성합니다.

기존 적재 파이프라인에는 다음과 같은 **데이터 오염(Data Corruption) 및 정보 유실 리스크**가 존재했습니다:

1. **HTML 수집 시 테이블 구조 평탄화(Flattening)**:
   - `fetch_web`의 HTML 파서가 `<table>` 태그 내부를 단순 텍스트(`//body//text()`)로 이어붙여, 행/열의 구조적 관계가 붕괴되고 수치·비교 데이터의 맥락이 왜곡됨.
2. **문자 수 제한 슬라이싱 시 테이블 절단 또는 본문 압살**:
   - Fetcher(20,000자) 및 LLM 프롬프트 투입(`doc_to_prompt`, 단일 12,000자 / 병합 24,000자)에서 문자 수를 슬라이싱할 때 **테이블 글자 수가 본문 글자 수에 합산**됨.
   - 이로 인해 긴 테이블이 있으면 일반 본문이 잘려나가거나, 긴 본문 뒤쪽의 테이블이 중간에 잘려 불완전한 데이터로 LLM에 투입되어 환각 및 잘못된 관계 추출 발생.
3. **추출 및 렌더링 템플릿에서의 테이블 누락**:
   - 프롬프트 상에 테이블 내 데이터(벤치마크 점수, 사양 매트릭스 등)에 대한 명시적 보존 지침이 없어, LLM이 표 안의 핵심 정보를 생략하거나 임의 축약함.

---

## 2. 핵심 설계 원칙

1. **테이블 내용의 완전 보존 (Anti-Omission)**:
   - 웹 HTML 수집 시 `<table>` 요소를 마크다운 테이블 표준 문법(`| col1 | col2 |\n|---|---|`)으로 구조화하여 보존한다.
   - LLM 추출 프롬프트(`extract_system_prompt`)와 가독 렌더링 프롬프트(`render_detail_prompt`)에 테이블 내 데이터와 구조를 누락 없이 온전히 반영·재구성하도록 지침을 명시한다.
2. **테이블 문자 수의 본문 문자 수 제한 제외 (Table Content Budget Exemption)**:
   - LLM 프롬프트 투입 예산(`limit`: 단일 12,000자 / 병합 24,000자)을 적용할 때, **일반 본문(Prose) 텍스트에만 예산을 차감**한다.
   - **테이블(Table) 영역의 글자 수는 본문 글자 수 카운트에서 전액 제외**하며, 테이블 전체를 100% 무손실로 보존하여 프롬프트에 주입한다.

---

## 3. 세부 설계 및 구성 요소

### 3.1 HTML 테이블 변환기 (`src/claire/ingest/fetchers/web.py`)
- `_extract_html` 단계에서 `<table>` 요소를 감지하여 `_html_table_to_markdown`을 통해 마크다운 테이블 텍스트로 변환.
- `<th>`, `<tr>`, `<td>`, `<caption>`을 분석하여 행/열 정렬 및 공백 정규화.
- 불필요한 레이아웃 테이블(데이터 없는 빈 테이블 등)은 필터링하고 데이터 테이블을 온전히 보존.

### 3.2 테이블-본문 분리 및 예외 슬라이싱 (`src/claire/extract/table_budget.py`)
- **`extract_tables_from_text(text: str) -> tuple[str, list[str]]`**:
  - 마크다운 테이블(`|...|\n|---|...|`) 및 AsciiDoc 테이블(`|===...|===`) 정규식을 통해 본문 내 테이블 블록을 감지 및 분리.
- **`slice_text_with_table_exemption(text: str, limit: int) -> str`**:
  - 일반 본문 텍스트에 대해서만 `limit` 글자 수까지 슬라이싱.
  - 테이블 블록은 글자 수 카운트에서 제외하여 온전히 보존한 후 결합.

### 3.3 프롬프트 및 템플릿 보완 (`src/claire/extract/prompts.py`)
- **`doc_to_prompt(doc)`**:
  - `(doc.raw_text or "")[:limit]`을 `slice_text_with_table_exemption(doc.raw_text, limit)`으로 교체.
- **`extract_system_prompt`**:
  - `TABLES & DATA MATRICES`: 테이블 안의 엔티티/수치/속성/관계를 누락 없이 정밀하게 추출하도록 룰 추가.
- **`render_detail_prompt_md` & `render_detail_prompt_adoc`**:
  - 원문에 포함된 테이블을 가독 문서 생성 시 생략하거나 뭉개지 않고 온전한 표 형식으로 재구성하도록 가이드라인 강화.
