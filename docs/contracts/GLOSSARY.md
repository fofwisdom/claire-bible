# Shared Compatibility Glossary (`docs/contracts/GLOSSARY.md`)

이 문서는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`) 간의
상호 이해와 데이터 호환성을 보장하기 위해 제정된 공식 표준 용어집(Canonical Glossary)이다.

양 계보의 메인테이너, 협력자, 자율 에이전트(AI Agent)는
본 문서에 정의된 표준 명칭과 의미를 준수하여
문서 작성, 코드 구현, API 모델링, 커뮤니케이션 시 발생할 수 있는 의미 왜곡(Semantic Drift)과 오인을 방지한다.

---

## 1. 계보 및 거버넌스 용어 (Lineage & Governance)

| 한국어 표준명 | 영문 표준명 | 공식 저장소 식별자 | 정의 및 핵심 역할 |
|---|---|---|---|
| **테제 계보** | Thesis Lineage | `blackan/claire_bible` (Upstream) | RAG 및 지식 그래프의 근본 원리, 원천 온톨로지 명제, 프로토타입 아키텍처 및 최소 공통 코어 엔진을 연구·실증하는 상위 계보. |
| **증강 계보** | Augmentation Lineage | `fofwisdom/claire-bible` (Origin) | 테제 계보의 이론과 코어를 계승하고, 실제 사용자 상호작용(웹 UI, 텔레그램 봇, 멀티 테마 물리 격리, RFC 8693 토큰 교환, 역방향 프록시 보안 경계, MCP 에이전트 연동)을 확장·증강하는 하위 계보. |

- **계보 관계 원칙**:
  - 테제 계보와 증강 계보는 상하 종속 관계가 아니며,
    각자의 책무(테제 탐구 vs 실세계 증강)를 수행하는 독립적인 연구·개발 계보이다.
  - 상호 호환성을 유지하기 위한 데이터 형식과 통신 인터페이스는
    `docs/contracts/`의 공통 계약을 통해 엄격히 조율한다.

---

## 2. 계약 및 규약 체계 용어 (Contract & Protocol Hierarchy)

문서 명칭 및 설명에서 '계약'과 '규약'은 상하 계층 관계를 가지며 명확히 구분하여 사용한다.

```text
[계약 (Contract)] : 상위 거시적 합의 체계 (문서 단위: SCHEMA_VERSIONING, API_HTTP_CONTRACT, TOKEN_EXCHANGE 등)
  └── [규약 (Protocol / Rules)] : 계약 내부에서 구체적으로 명시된 기술 규칙 (와이어 포맷, 상태 코드, 엔코딩 규칙 등)
```

### 2.1. 계약 (Contract)
- **정의**: 두 계보가 데이터 상호 호환성과 지식 보존을 위해 공식적으로 합의하고 채택하는 거시적 정본 문서.
- **범위**: `docs/contracts/` 디렉터리 내에 위치하는 독립된 Markdown 및 YAML 사양서.
- **예시**:
  - `SCHEMA_VERSIONING.md` (스키마 버전 계약)
  - `API_HTTP_CONTRACT.md` (HTTP API 계약)
  - `TOKEN_EXCHANGE.md` (토큰 교환 계약)
  - `openapi.yaml` (공통 API 사양 계약)
- **성격**: 한 계보의 일방적인 변경이 불가능하며, `candidate` → `adopted` → `accepted`의 공식 절차를 거쳐 확정된다.

### 2.2. 규약 (Protocol / Rules / Convention)
- **정의**: 계약 문서 내부에서 개별 동작 메커니즘을 규정하는 미시적 기술 규칙, 인코딩 사양, 또는 와이어 규칙.
- **범위**: 계약을 구성하는 조항 또는 기술 메커니즘.
- **예시**:
  - 와이어 직렬화 규약 (JSON 직렬화 규칙)
  - 스텔스 모드 에러 처리 규약 (미인증 시 404 강제 변환)
  - RFC 8693 URN 식별 규약 (`urn:ietf:params:oauth:...`)
  - 의미 단위 줄바꿈 규약 (Semantic Line Breaks)

---

## 3. 지식 코어 및 데이터 모델 용어 (Knowledge Core & Data Models)

문서의 라이프사이클과 지식 추출 단계에서 사용되는 핵심 데이터 단위의 표준 정의이다.

```text
[원문 (Raw Content)] : 외부에서 수집된 미가공 원본 텍스트 (Layer 2 아티팩트 보관)
  │
  ├── [문서 (Document)] : 메타데이터, 원문, 요약, 상세, 엔티티를 포괄하는 최상위 지식 단위
  │     │
  │     ├── [요약 (Summary)] : 1~3문장 순수 평문 에그제큐티브 서머리 (검색·카드·호버용)
  │     │
  │     └── [상세 (Detail)] : 독자가 원문 없이도 맥락을 이해하도록 재구성한 다단락 가독 본문
  │
  └── [온톨로지 (Ontology)]
        ├── [엔티티 (Entity)] / [노드 (Node)] : 개념, 인물, 기술 등의 지식 실체 및 그래프 정점
        └── [관계 (Relation)] / [링크 (Link / Edge)] : 실체 간의 유향 온톨로지 명제 및 그래프 간선
```

### 3.1. 원문 (Raw Content / `raw_text`)
- **정의**: 웹 스크래핑, PDF 파싱, 자막/STT 전사, 로컬 파일 등 외부 입력원으로부터 수집기(Fetcher)가 인입한 순수 미가공 텍스트.
- **저장소**: SQLite `documents.raw_text`, Layer 2 영구 아티팩트(`data/raw/artifacts/<doc_id>.txt.zst` 또는 `.txt.gz`).
- **불변 원칙 (무손실 재생산성)**:
  - 향후 LLM 모델이 변경되거나 프롬프트 엔진이 고도화되었을 때,
    원격 네트워크 재수집 없이도 원문 그대로 재추출(`reextract`) 및 재렌더링할 수 있는 불변의 근거 자산이다.
- **적재 정책**:
  - 기본 적재 시에는 시스템 보호를 위해 사전 설정된 글자 수 예산(`budget`)에 따라 안전 절단될 수 있다.
  - 무절단 모드(`full_content=True`) 적용 시에는 글자 수 상한 없이 원문 100%를 보존하며,
- **원문 메타데이터 (Raw Metadata / `rawmeta`)**:
  - **정의**: 웹 응답 헤더, PDF 스트림 속성, 로컬 파일 시스템 메타데이터 등 외부 원천에서 미가공 상태로 인입되는 비검증 부가 속성 (예: HTTP Content-Type, PDF Producer, DTP 작성자 계정, 파일 크기 등).
  - **취급 원칙**: 시스템이나 온톨로지 추출 엔진이 교차 검증하지 않은 미가공 원천 정보이므로, 정본 지식으로 취급하거나 문서 본문에 임의로 주입하지 않는다.

### 3.2. 문서 (Document / `doc_id`, `documents`)
- **정의**: 고유 식별자(`doc_id`), 원본 출처(URL), 시스템 적재 메타데이터(`docmeta`), 원문, 요약, 상세, 온톨로지 추출 결과를 총괄하는 최상위 지식 자산 단위.
- **저장소**: SQLite `documents` 테이블 및 파일시스템 볼트(`vault/<doc_id>.adoc` 또는 `.md`).
- **식별 체계**: `doc_<hash>` 또는 `doc_<uuid>` 형태의 유일한 식별자.
- **적재 메타데이터 (Document Ingestion Metadata / `docmeta` / `Document.meta`)**:
  - **정의**: 파이프라인이 원문을 수집·가공·적재(Ingestion & Processing)할 때 기록한 시스템 처리 및 실행 이력(Provenance: `directive`, `pdf_parser_used`, `pdf_parser_fallback`, `raw_truncated`, `stt` 등).
  - **경계 원칙**: `docmeta`는 외부 미가공 메타데이터(`rawmeta`)가 아니며, 원 저작물의 서지 정보(저자, 발행기관 등)는 `docmeta`에 혼입하지 않고 오직 온톨로지 지식 그래프의 전유물로 환원하여 관리한다.

### 3.3. 요약 (Summary / `documents.summary`, `ExtractionResult.summary`)
- **정의**: LLM이 원문의 핵심 명제, 주장, 결론을 간결하게 압축하여 추출한 에그제큐티브 서머리(Executive Summary).
- **작성 규칙**:
  - **순수 평문(Plain Text)**: AsciiDoc이나 마크다운 서식(`==`, `**`, `[NOTE]`, 표 등)이 일체 배제되어야 한다.
  - **분량 및 문체**: 1~3문장 분량의 명확한 한국어 서술체 문어체(`~한다`, `~이다`).
- **주요 용도**: 하이브리드 검색 인덱싱, 그래프 뷰 노드 호버 툴팁, 좌측 문서 목록 카드, 텔레그램 알림 요약.
- **무결성 가드레일 (`SummaryQualityGuard`)**:
  - 빈 문자열, `[mock]` 접두사, 본문 앞부분 단순 200자 슬라이싱 대체(Silent Mock Fallback)는 엄격히 결손으로 간주한다.
  - 검증 실패 시 해당 문서는 불완전(`partial=1`) 상태로 격리되며 자동 재요약 큐(`refresh_queue`)로 회수된다.

### 3.4. 상세 (Detail: Rendered Detail, Compiled HTML, Detail View)
'상세'는 사용 맥락(데이터 모델, 렌더링 파이프라인, 사용자 인터페이스)에 따라 다음 세 가지 의미로 엄밀히 구분된다.

1. **가독 상세 본문 (Rendered Detail / `documents.detail`)**:
   - **정의**: 사용자가 긴 원문을 직접 읽지 않아도 문서의 핵심 맥락, 배경, 세부 논리를 완전하게 파악할 수 있도록 LLM이 가독성 높게 문어체로 재구성한 본문.
   - **분량 및 형식**: 단순 요약이 아닌 여러 단락(A4 1~4장 분량)으로 구성되며, 마크다운(`md`) 또는 AsciiDoc(`adoc`) 포맷으로 영속화된다.
   - **핵심 특징**: 원문에 포함된 비교표/벤치마크 테이블 보존, 핵심 용어 강조, 코드 블록 유지.
   - **절단 섹션 작성 배제 원칙**: 원문이 수집 과정에서 절단된 경우,
     잘려나간 불완전한 문단이나 조항에 대한 환각(Hallucination) 생성을 방지하기 위해 온전히 보존된 섹션까지만 상세 본문으로 작성한다.
2. **사전 컴파일 상세 (Compiled Detail HTML / `documents.detail_html`)**:
   - **정의**: 클라이언트의 실시간 파싱 부하를 없애고 일관된 뷰를 즉각 렌더링하기 위해,
     `documents.detail` 텍스트를 AOT(Ahead-of-Time) 방식으로 사전 컴파일한 안전한 HTML 코드.
3. **상세 조회 뷰 (Detail View / `GET /document`, UI 우측 `#panel`)**:
   - **정의**: 웹 API 및 UI 화면 관점에서, 특정 문서의 원본 출처 및 적재 메타데이터(`docmeta`), 요약(`summary`), 가독 본문(`detail`), 그리고 연결된 지식 노드(`nodes`)를 종합하여 제공하는 응답 및 뷰 패널. (서지 정보는 온톨로지 지식 노드를 통해 탐색됨)

### 3.5. 엔티티 (Entity) 및 노드 (Node)
- **엔티티 (Entity / `entities` 테이블)**:
  - **정의**: 문서 본문에서 온톨로지 규칙에 따라 추출된 도메인 개념, 인물, 기술, 기관, 법령, 사건 등의 지식 실체.
  - **속성**: 고유 식별자(`id`), 정규화된 이름(`norm_name`), 타입(`type`), 관찰/주장 목록(`observations`), 출처 문서 ID(`sources`), 표준 온톨로지 외 임시 등록 여부(`provisional`).
- **노드 (Node / Graph Node)**:
  - **정의**: 엔티티가 지식 그래프(Knowledge Graph)의 위상 공간(Topology) 상에서 시각화된 정점(Vertex).
  - **속성**: 노드 크기, 색상, 좌표, 중심성(Centrality), 사용자 클릭 시 펼쳐지는 지식 패널 인터랙션을 담당.

### 3.6. 관계 (Relation) 및 링크 / 엣지 (Link / Edge)
- **관계 (Relation / `relations` 테이블)**:
  - **정의**: 두 엔티티 사이의 의미적 연결을 표현하는 유향 온톨로지 술어 명제(예: `A -(depends_on)-> B`).
  - **속성**: `source_id`, `target_id`, `type`, `confidence`, `provisional`.
- **링크 / 엣지 (Link / Edge)**:
  - **정의**: 지식 그래프 상에서 두 노드를 시각적으로 연결하는 선이자 그래프 네트워크의 간선.

### 3.7. 테마 (Theme / `theme_id`)
- **정의**: 상이한 도메인의 지식이 단일 지식 베이스에 혼합되어 RAG 검색 정밀도가 저하되거나 그래프가 오염되는 것을 방지하기 위한 완전 물리 격리 단위.
- **구성**: 고유 시퀀스 번호(`sequence`), 테마 식별자(`theme_id`), 표시 라벨, 아이콘, 전용 SQLite DB 파일(`claire_<theme>.db`), 전용 볼트 디렉터리.

### 3.8. 볼트 (Vault / `vault/`)
- **정의**: SQLite DB에 정본으로 영속화된 지식 자산을 Obsidian 등 외부 마크다운 에디터에서 즉시 열람·활용할 수 있도록 단방향(export-only)으로 투영한 파일시스템 저장소.

---

## 4. 보안, 권한 및 인증 세션 용어 (Security & Auth)

HTTP API 및 시스템 접근 제어에 적용되는 역할과 자격 증명 용어이다.

| 용어 | 식별 코드 / 헤더 | 정의 및 권한 범위 |
|---|---|---|
| **소유자** | `owner` | 시스템의 모든 읽기·쓰기·재적재·관리자 설정을 수행할 수 있는 최상위 권한 등급. |
| **기여자 / 읽기 전용** | `read` | 지식 그래프 탐색, 문서 목록 및 상세 조회만 허용되고 일체의 변경 작업이 차단된 권한 등급. |
| **익명 접근** | `anonymous` | 인증되지 않은 일반 클라이언트 상태. `CLAIRE_ANONYMOUS_READONLY` 설정에 따라 `read`로 승격되거나 접근이 차단됨. |
| **스텔스 모드** | Stealth Mode (Fail-Closed) | 비인가자가 보호된 엔드포인트에 접근할 때 401/403 대신 404 Not Found를 반환하여 서비스의 실존 자체를 은폐하는 방어 규약. |
| **세션 토큰** | Session Token (`cb_session`) | 쿠키 또는 `Authorization: Bearer <token>`으로 전달되는 서명된 HTTP 작업 세션 자격 증명. |
| **부트스트랩 토큰** | Bootstrap Token | 초기 환경 설정 또는 텔레그램 일회용 링크를 통해 1회성 세션을 발급받기 위한 시드 자격 증명. |
| **토큰 교환** | RFC 8693 Token Exchange | 외부 OAuth 토큰, 세션 토큰, 또는 MCP 클라이언트 토큰을 인가 범위에 맞는 새 액세스 토큰으로 안전하게 교환하는 표준 프로토콜. |

---

## 5. 용어 대조 및 매핑 표 (Canonical Mapping Matrix)

코드 및 DB 레벨에서 혼선을 방지하기 위한 대조표이다.

| 개념 | 한국어 표준명 | 영문 표준명 | Python 코드 식별자 | SQLite / 저장소 식별자 |
|---|---|---|---|---|
| 상위 계보 | **테제 계보** | Thesis Lineage | Upstream / blackan | `SCHEMA_LINEAGE = "claire-bible/common"` |
| 증강 계보 | **증강 계보** | Augmentation Lineage | Origin / fofwisdom | `SCHEMA_LINEAGE = "claire-bible/common"` |
| 상호 합의 | **계약** | Contract | `docs/contracts/*.md` | `SCHEMA_VERSION = 13` |
| 세부 규칙 | **규약** | Protocol / Rule | `GateMiddleware`, wire rules | 와이어 프로토콜 / 스텔스 규약 |
| 미가공 원문 | **원문** | Raw Content | `Document.raw_text`, `raw_text` | `documents.raw_text`, `*.txt.zst` |
| 원본 부가속성 | **원문 메타데이터** | Raw Metadata (`rawmeta`) | 외부 원본 메타데이터 (비영속/미검증) | PDF/HTTP 헤더 미가공 메타 |
| 지식 단위 | **문서** | Document | `Document`, `doc_id` | `documents` 테이블, `vault/*.adoc` |
| 적재 이력 | **적재 메타데이터** | Document Metadata (`docmeta`) | `Document.meta`, `doc.meta` | `documents.meta` (JSON) |
| 핵심 압축 | **요약** | Summary | `ExtractionResult.summary` | `documents.summary` (plain text) |
| 가독 본문 | **상세 (가독 본문)** | Rendered Detail | `render_detail`, `doc.detail` | `documents.detail` (md/adoc) |
| 컴파일 본문 | **사전 컴파일 상세** | Compiled HTML | `detail_html` | `documents.detail_html` |
| 상세 패널 | **상세 뷰** | Detail View | `document_detail_route` | `GET /document?id=...` |
| 지식 실체 | **엔티티** | Entity | `Entity`, `ExtractedEntity` | `entities` 테이블 |
| 그래프 정점 | **노드** | Node | `node`, `nodes` (dict) | GraphView cytoscape element |
| 의미 연결 | **관계** | Relation | `Relation`, `ExtractedRelation` | `relations` 테이블 |
| 그래프 간선 | **링크 / 엣지** | Link / Edge | `edge`, `edges` (dict) | GraphView edge element |
| 도메인 격리 | **테마** | Theme | `Theme`, `ThemeSettings` | `claire_<theme>.db`, `vault/themes/<seq>` |
| 파일 투영 | **볼트** | Vault | `vault_dir`, `export_entity` | `vault/` 디렉터리 |
