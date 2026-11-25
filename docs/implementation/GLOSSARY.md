# 용어집 (`docs/implementation/GLOSSARY.md`)

이 문서는 Claire Bible의 표준 용어와 그 코드·저장소 식별자를 정의한다. 메인테이너, 협력자, 에이전트는 문서 작성, 코드 구현, API 모델링, 소통에서 이 문서의 이름과 의미를 쓴다.

---

## 1. 지식 코어 및 데이터 모델 용어 (Knowledge Core & Data Models)

문서의 라이프사이클과 지식 추출 단계에서 사용되는 핵심 데이터 단위의 표준 정의이다.

```text
[원문 (Raw Content)] : 외부에서 수집된 미가공 원본 텍스트 (Layer 2 아티팩트 보관)
  │
  ├── [문서 (Document)] : 메타데이터, 원문, 초록, 상세, 엔티티를 포괄하는 최상위 지식 단위
  │     │
  │     ├── [초록 (Summary)] : 1~3문장 순수 평문 에그제큐티브 서머리 (검색·카드·호버용)
  │     │
  │     └── [상세 (Detail)] : 독자가 원문 없이도 맥락을 이해하도록 재구성한 다단락 가독 본문
  │
  └── [온톨로지 (Ontology)]
        ├── [엔티티 (Entity)] / [노드 (Node)] : 개념, 인물, 기술 등의 지식 실체 및 그래프 정점
        └── [관계 (Relation)] / [링크 (Link / Edge)] : 실체 간의 유향 온톨로지 명제 및 그래프 간선
```

### 1.1. 원문 (Raw Content / `raw_text`)
- **정의**: 웹 스크래핑, PDF 파싱, 자막/STT 전사, 로컬 파일 등 외부 입력원으로부터 수집기(Fetcher)가 인입한 순수 미가공 텍스트.
- **저장소**: SQLite `documents.raw_text`, Layer 2 영구 아티팩트(`data/raw/artifacts/<doc_id>.txt.zst` 또는 `.txt.gz`).
- **불변 원칙 (무손실 재생산성)**:
  - 향후 LLM 모델이 변경되거나 프롬프트 엔진이 고도화되었을 때, 원격 네트워크 재수집 없이도 원문 그대로 재추출(`reextract`) 및 재렌더링할 수 있는 불변의 근거 자산이다.
- **적재 정책**:
  - 기본 적재 시에는 시스템 보호를 위해 사전 설정된 글자 수 예산(`budget`)에 따라 안전 절단될 수 있다.
  - 무절단 모드(`full_content=True`) 적용 시에는 글자 수 상한 없이 원문 100%를 보존하며,
- **원문 메타데이터 (Raw Metadata / `rawmeta`)**:
  - **정의**: 웹 응답 헤더, PDF 스트림 속성, 로컬 파일 시스템 메타데이터 등 외부 원천에서 미가공 상태로 인입되는 비검증 부가 속성 (예: HTTP Content-Type, PDF Producer, DTP 작성자 계정, 파일 크기 등).
  - **취급 원칙**: 시스템이나 온톨로지 추출 엔진이 교차 검증하지 않은 미가공 원천 정보이므로, 정본 지식으로 취급하거나 문서 본문에 임의로 주입하지 않는다.

### 1.2. 문서 (Document / `doc_id`, `documents`)
- **정의**: 고유 식별자(`doc_id`), 원본 출처(URL), 시스템 적재 메타데이터(`docmeta`), 원문, 초록, 상세, 온톨로지 추출 결과를 총괄하는 최상위 지식 자산 단위.
- **저장소**: 단일 SQLite `documents` 테이블 (Schema v14, `kb_id` 파티셔닝).
- **식별 체계**: `doc_<hash>` 또는 `doc_<uuid>` 형태의 유일한 식별자.
- **적재 메타데이터 (Document Ingestion Metadata / `docmeta` / `Document.meta`)**:
  - **정의**: 파이프라인이 원문을 수집·가공·적재(Ingestion & Processing)할 때 기록한 시스템 처리 및 실행 이력(Provenance: `focus`, `pdf_parser_used`, `pdf_parser_fallback`, `raw_truncated`, `stt` 등).
  - **경계 원칙**: `docmeta`는 외부 미가공 메타데이터(`rawmeta`)가 아니며, 원 저작물의 서지 정보(저자, 발행기관 등)는 `docmeta`에 혼입하지 않고 오직 온톨로지 지식 그래프의 전유물로 환원하여 관리한다.

### 1.3. 초록 (Summary / `documents.summary`, `ExtractionResult.summary`)
- **옛 이름**: 2026-11-25 전에는 "요약"이라고 불렀다(소유자 결정). 문서의 이 항목을 "요약"이라고 쓰지 않는다. 검색 답변의 요약(`/search`, AI 지식 요약), 내용 경고의 요약, 화면이나 정책의 요약은 다른 개념이며 "요약"으로 남는다. 영어 이름과 코드 식별자는 `summary`이다. 카탈로그와 프롬프트는 바뀌었고(`extract-v9`), 다른 문서의 본문에는 옛 이름이 남아 있다.
- **정의**: LLM이 원문의 핵심 명제, 주장, 결론을 간결하게 압축하여 추출한 에그제큐티브 서머리(Executive Summary).
- **작성 규칙**:
  - **순수 평문(Plain Text)**: AsciiDoc이나 마크다운 서식(`==`, `**`, `[NOTE]`, 표 등)이 일체 배제되어야 한다.
  - **분량 및 문체**: 1~3문장 분량의 명확한 한국어 서술체 문어체(`~한다`, `~이다`).
- **주요 용도**: 하이브리드 검색 인덱싱, 그래프 뷰 노드 호버 툴팁, 좌측 문서 목록 카드, 텔레그램 알림.
- **무결성 가드레일 (`SummaryQualityGuard`)**:
  - 빈 문자열, `[mock]` 접두사, 본문 앞부분 단순 200자 슬라이싱 대체(Silent Mock Fallback)는 엄격히 결손으로 간주한다.
  - 검증 실패 시 해당 문서는 불완전(`partial=1`) 상태로 격리되며 자동 초록 재생성 큐(`refresh_queue`)로 회수된다.

### 1.4. 상세 (Detail: Rendered Detail, Compiled HTML, Detail View)
'상세'는 사용 맥락(데이터 모델, 렌더링 파이프라인, 사용자 인터페이스)에 따라 다음 세 가지 의미로 엄밀히 구분된다.

1. **가독 상세 본문 (Rendered Detail / `documents.detail`)**:
   - **정의**: 사용자가 긴 원문을 직접 읽지 않아도 문서의 핵심 맥락, 배경, 세부 논리를 완전하게 파악할 수 있도록 LLM이 가독성 높게 문어체로 재구성한 본문.
   - **분량 및 형식**: 단순 요약이 아닌 여러 단락(A4 1~4장 분량)으로 구성되며, 마크다운(`md`) 또는 AsciiDoc(`adoc`) 포맷으로 영속화된다.
   - **핵심 특징**: 원문에 포함된 비교표/벤치마크 테이블 보존, 핵심 용어 강조, 코드 블록 유지.
   - **절단 섹션 작성 배제 원칙**: 원문이 수집 과정에서 절단된 경우, 잘려나간 불완전한 문단이나 조항에 대한 환각(Hallucination) 생성을 방지하기 위해 온전히 보존된 섹션까지만 상세 본문으로 작성한다.
2. **사전 컴파일 상세 (Compiled Detail HTML / `documents.detail_html`)**:
   - **정의**: 클라이언트의 실시간 파싱 부하를 없애고 일관된 뷰를 즉각 렌더링하기 위해, `documents.detail` 텍스트를 AOT(Ahead-of-Time) 방식으로 사전 컴파일한 안전한 HTML 코드.
3. **상세 조회 뷰 (Detail View / `GET /document`, UI 우측 `#panel`)**:
   - **정의**: 웹 API 및 UI 화면 관점에서, 특정 문서의 원본 출처 및 적재 메타데이터(`docmeta`), 초록(`summary`), 가독 본문(`detail`), 그리고 연결된 지식 노드(`nodes`)를 종합하여 제공하는 응답 및 뷰 패널. (서지 정보는 온톨로지 지식 노드를 통해 탐색됨)

### 1.5. 엔티티 (Entity) 및 노드 (Node)
- **엔티티 (Entity / `entities` 테이블)**:
  - **정의**: 문서 본문에서 온톨로지 규칙에 따라 추출된 도메인 개념, 인물, 기술, 기관, 법령, 사건 등의 지식 실체.
  - **속성**: 고유 식별자(`id`), 정규화된 이름(`norm_name`), 타입(`type`), 관찰/주장 목록(`observations`), 출처 문서 ID(`sources`), 표준 온톨로지 외 임시 등록 여부(`provisional`).
- **노드 (Node / Graph Node)**:
  - **정의**: 엔티티가 지식 그래프(Knowledge Graph)의 위상 공간(Topology) 상에서 시각화된 정점(Vertex).
  - **속성**: 노드 크기, 색상, 좌표, 중심성(Centrality), 사용자 클릭 시 펼쳐지는 지식 패널 인터랙션을 담당.
- **사용 규칙** (소유자 결정 2026-11-23):
  - 지식 항목 자체를 말할 때는 항상 **엔티티**라고 쓴다: 관리, 대표 레이블 변경, 종합, 조사, 공유, 찾지 못함, 문서에서 추출된 항목.
  - **노드**는 그래프 화면의 점을 조작하는 말에만 쓴다: 노드를 클릭한다, 두 노드 사이 경로, 표시할 노드의 최소 관계 수.
  - **그래프**는 한 지식 베이스의 엔티티와 관계 전체이며, 그것을 그려 보여 주는 화면의 이름이다.
  - "지식 노드", "개념 노드"는 쓰지 않는다.

### 1.6. 관계 (Relation) 및 링크 / 엣지 (Link / Edge)
- **관계 (Relation / `relations` 테이블)**:
  - **정의**: 두 엔티티 사이의 의미적 연결을 표현하는 유향 온톨로지 술어 명제(예: `A -(depends_on)-> B`).
  - **속성**: `source_id`, `target_id`, `type`, `confidence`, `provisional`.
- **링크 / 엣지 (Link / Edge)**:
  - **정의**: 지식 그래프 상에서 두 노드를 시각적으로 연결하는 선이자 그래프 네트워크의 간선.
- **사용 규칙** (소유자 결정 2026-11-23): 엔티티 사이의 서술은 항상 **관계**라고 쓴다(관계 수, 관계순, 관계 없음). **링크**는 그래프 화면의 선에만 쓴다. 이 뜻으로 "연결"은 쓰지 않는다.

### 1.7. 지식 베이스 (Knowledge Base / `kb_id`)
- **정의**: 상이한 도메인의 지식이 단일 지식 베이스에 혼합되어 RAG 검색 정밀도가 저하되거나 그래프가 오염되는 것을 방지하기 위한 논리적 완전 격리 단위. 계정마다 하나인 **개인 지식 베이스**와 소유자가 없는 **공유 지식 베이스**가 있다.
- **구성**: 고유 시퀀스 번호(`seq`), 지식 베이스 식별자(`id`/`kb_id`), 표시 라벨, 설명, 아이콘, 공개 여부, GA4 측정 ID, 계정(`account_id`), 외부 번호(`number`), 지식 유형(`knowledge_type`, 공유 지식 베이스만), 파티셔닝(단일 `data/claire.db` 내 `kb_id` 컬럼 분리).
- **옛 이름**: `claire.db` v27 전에는 지식 베이스를 "테마"라고 불렀다. 지식 베이스를 "테마"라고 쓰지 않는다.

### 1.8. 볼트 (Vault / `vault/`, 영구 제거됨)
- **정의**: 과거 SQLite DB 정본을 Obsidian 등 외부 마크다운 에디터에서 열람할 수 있도록 단방향(export-only)으로 투영했던 파일시스템 저장소. 현재는 볼트 서브시스템 및 마크다운 파일 동기화가 영구 제거되어 비활성화/폐기되었다.

### 1.9. 테마 (Theme / `themes`)
- **정의**: 같은 주제이고 지식 유형이 같은 지식 베이스의 묶음. 테마 판정이 지식 베이스를 테마에 넣고, 서버 관리자가 조정한다. 지식 유형이 다른 지식 베이스는 한 테마에 들어가지 않는다.
- **구성**: 외부 번호(`number`), 라벨, 설명, 지식 유형(`knowledge_type`), 상태(`state`), 이 서버의 멤버(`theme_knowledge_bases`), 다른 서버의 멤버(`theme_remote_knowledge_bases`).

### 1.10. 지식 유형 (Knowledge Type / `knowledge_type`)
- **정의**: 공유 지식 베이스와 테마가 다루는 지식이 현실 세계의 지식인지 지어낸 세계의 지식인지를 나타내는 값. 값은 `real`(**현실**, Real world)과 `fictional`(**허구**, Fiction) 두 가지다. 개인 지식 베이스는 지식 유형이 없고, 테마 판정은 개인 지식 베이스를 `real`로 다룬다.
- **변경**: 공유 지식 베이스의 중재자와 서버 관리자가 바꾼다. 바꾸면 그 지식 베이스는 지금의 테마에서 빠지고 다음 테마 판정이 새 지식 유형의 테마에 넣는다.
- **식별자**: 저장소 `knowledge_bases.knowledge_type`, `themes.knowledge_type`; JSON 필드와 질의 매개변수 `knowledge_type`; 카탈로그 키 `kb.knowledge_type.*` (`label`, `real`, `fictional`).
- **쓰지 않는 이름**: "성격", 단독 "유형"·"type"(엔티티 유형, 출처 유형과 겹친다), 값 `real`의 "현실 세계"·"실제", 값 `fictional`의 "창작"·"가상".

---

### 1.11. 주소 (Address): 고유 주소, 엔티티 주소, 주소 토큰, 링크 공개
- 설계: [`PERMALINK_UNIFICATION_DESIGN.md`](../design/PERMALINK_UNIFICATION_DESIGN.md). 현재 동작: [`PERMALINKS.md`](PERMALINKS.md).
- **고유 주소 (Permalink)**: 문서 또는 노트 하나의 주소 `{origin}/silvercord/{주소 토큰}`. 이용자가 열고, 복사하고, 공유하는 주소는 이것 하나다.
- **Silvercord (`/silvercord/`)**: 고유 주소의 경로 이름. **ActivityPub 객체가 될 수 있는 종류 전용이다**: 문서(`Article`), 노트(`Note`). ActivityPub 객체가 아닌 종류는 이 경로를 쓰지 않고 자기 경로를 가진다. 새 종류를 더할 때 이 기준으로 경로를 정한다.
- **엔티티 주소 (Entity Address)**: 엔티티 하나의 주소 `{origin}/entity/{주소 토큰}`. 열면 그 지식 베이스의 그래프에서 엔티티가 선택된다. 엔티티는 ActivityPub 객체가 아니므로 Silvercord가 아니다.
- **주소 토큰 (Address Token)**: 주소의 마지막 경로 조각. 무작위 16자이며, 항목마다 하나이고 바뀌지 않는다. 주소 대장(`permalinks` 테이블)이 준다.
- **링크 공개 (Link Access)**: 주소를 가진 사람은 누구나 읽게 하는 항목의 설정. 주소는 그대로이고 접근만 바뀐다. 노트는 항상 공개다.
- **게시물 (Post)**: 타임라인에 나오는 노트 또는 문서. **노트 (Note)**: 계정이 서버에서 쓰는 짧은 공개 게시물. 타임라인에서 문서의 종류 이름은 "문서"다. "아티클"은 프로토콜 타입 `Article`을 가리킬 때만 쓴다. **적재 본문**은 문서의 한 부분(Claire가 만든 본문)이며 문서의 다른 이름이 아니다.

### 1.12. 선호 언어 (Preferred Languages) 와 서버 기본 언어 (Server Default Language)
- **선호 언어**: 계정마다의 순서 있는 언어 목록(`accounts.preferred_languages`). 화면이 지원하는 첫 언어가 그 계정의 화면 언어이고, 그 계정의 작업은 이 목록으로 동영상 자막 순서, 음성 전사 힌트, 이미지 설명(VLM) 언어를 정한다. `en`은 항상 목록에 있다. 추출 출력 언어와 엔티티 언어는 정하지 않는다.
- **서버 기본 언어**: 관리자가 `/admin/server`에서 고르는 언어 목록(`server_settings.default_languages`). 자기 목록이 없는 계정과 계정이 없는 작업이 쓴다. 고르지 않을 수 있다.
- **쓰지 않는 이름**: "인터페이스 언어 설정"(계정 화면의 설정 이름으로), "프로젝트 광역 선호 언어". 로그인하지 않은 방문자의 화면 언어(`CLAIRE_LANG`)는 "방문자 화면 언어"라고 쓴다.

## 2. 보안, 권한 및 인증 세션 용어 (Security & Auth)

HTTP API 및 시스템 접근 제어에 적용되는 역할과 자격 증명 용어이다.

| 용어 | 식별 코드 / 헤더 | 정의 및 권한 범위 |
|---|---|---|
| **소유자** | `owner` | 시스템의 모든 읽기·쓰기·재적재·관리자 설정을 수행할 수 있는 최상위 권한 등급. |
| **기여자 / 읽기 전용** | `read` | 지식 그래프 탐색, 문서 목록 및 상세 조회만 허용되고 일체의 변경 작업이 차단된 권한 등급. |
| **익명 접근** | `anonymous` | 인증되지 않은 일반 클라이언트 상태. `CLAIRE_ANONYMOUS_READONLY` 설정에 따라 `read`로 승격되거나 접근이 차단됨. |
| **스텔스 모드** | Stealth Mode (Fail-Closed) | 비인가자가 보호된 엔드포인트에 접근할 때 401/403 대신 404 Not Found를 반환하여 서비스의 실존 자체를 은폐하는 방어 규약. |
| **세션 토큰** | Session Token (`cb_session`) | 쿠키 또는 `Authorization: Bearer <token>`으로 전달되는 서명된 HTTP 작업 세션 자격 증명. |
| **부트스트랩 토큰** | Bootstrap Token | 초기 환경 설정 또는 텔레그램 일회용 링크를 통해 1회성 세션을 발급받기 위한 시드 자격 증명. |
| **토큰 교환** | RFC 8693 Token Exchange | 계정에 귀속된 세션 토큰을 같은 계정의 `readonly` 액세스 토큰으로 교환하는 표준 프로토콜. 현재 구현 범위는 `API_AND_MCP_GUIDE.md` §2.5에 있다. |

---

## 3. 용어 대조 및 매핑 표 (Canonical Mapping Matrix)

코드 및 DB 레벨에서 혼선을 방지하기 위한 대조표이다.

| 개념 | 한국어 표준명 | 영문 표준명 | Python 코드 식별자 | SQLite / 저장소 식별자 |
|---|---|---|---|---|
| 미가공 원문 | **원문** | Raw Content | `Document.raw_text`, `raw_text` | `documents.raw_text`, `*.txt.zst` |
| 원본 부가속성 | **원문 메타데이터** | Raw Metadata (`rawmeta`) | 외부 원본 메타데이터 (비영속/미검증) | PDF/HTTP 헤더 미가공 메타 |
| 지식 단위 | **문서** | Document | `Document`, `doc_id` | `documents` 테이블 |
| 적재 이력 | **적재 메타데이터** | Document Metadata (`docmeta`) | `Document.meta`, `doc.meta` | `documents.meta` (JSON) |
| 핵심 압축 | **초록** | Summary | `ExtractionResult.summary` | `documents.summary` (plain text) |
| 가독 본문 | **상세 (가독 본문)** | Rendered Detail | `render_detail`, `doc.detail` | `documents.detail` (AsciiDoc, [DOCUMENT_FORMAT.md](DOCUMENT_FORMAT.md)) |
| 컴파일 본문 | **사전 컴파일 상세** | Compiled HTML | `detail_html` | `documents.detail_html` |
| 상세 패널 | **상세 뷰** | Detail View | `document_detail_route` | `GET /document?id=...` |
| 기본 개요 | **홈 화면** | Home View | `renderHome()`, `enterHome()` | `body[data-center-view="home"]`, `#homewrap` |
| 우측 문맥 | **문맥 패널** | Context Panel | `hasContext()`, `isContextOpen()`, `syncWorkspaceLayout()` | `aside#detailpane`, 선택이 있을 때만 열린다 (`body.ctx-closed`, `claireContextOpen`) |
| 좌측 목록 | **목록** | List Pane | `aside#docs[data-list]`, `syncListPane()`, `listMode()` | 그래프 뷰 기본 = 엔티티 목록, 그 외 뷰 기본 = 문서 목록. `#listswitch`로 전환 가능 |
| 엔티티 관찰 | **관찰/주장** | Observations | `observations` | 그래프 뷰 엔티티 목록의 펼친 행에 보인다 |
| 지식 실체 | **엔티티** | Entity | `Entity`, `ExtractedEntity` | `entities` 테이블 |
| 그래프 정점 | **노드** | Node | `node`, `nodes` (dict) | GraphView cytoscape element |
| 의미 연결 | **관계** | Relation | `Relation`, `ExtractedRelation` | `relations` 테이블 |
| 그래프 간선 | **링크 / 엣지** | Link / Edge | `edge`, `edges` (dict) | GraphView edge element |
| 도메인 격리 | **지식 베이스** | Knowledge Base | `KnowledgeBaseInfo`, `KnowledgeBaseManager`, `kb_id` | `knowledge_bases` 테이블, `*.kb_id`. v27 전 이름은 "테마"이며 이 뜻으로 "테마"를 쓰지 않는다 |
| 계정의 지식 베이스 | **개인 지식 베이스** | Personal Knowledge Base | `is_personal` | `knowledge_bases.is_personal = 1`. 지식 유형이 없다 |
| 공동의 지식 베이스 | **공유 지식 베이스** | Shared Knowledge Base | `is_personal` | `knowledge_bases.is_personal = 0`. 소유자가 없고 항상 공개이며 지식 유형이 있다 |
| 지식 베이스의 묶음 | **테마** | Theme | `store/themes.py`, `collaboration/theme_judgement.py` | `themes`, `theme_knowledge_bases`, `theme_remote_knowledge_bases`. 주제와 지식 유형이 같은 지식 베이스의 묶음 (§1.9) |
| 현실·허구 구분 | **지식 유형** | Knowledge Type | `knowledge_type` (JSON 필드, 질의 매개변수), 카탈로그 `kb.knowledge_type.*` | `knowledge_bases.knowledge_type`, `themes.knowledge_type`. "성격", 단독 "유형"·"type"은 쓰지 않는다 (§1.10) |
| 지식 유형 값 `real` | **현실** | Real world | `real` | "현실 세계", "실제"는 쓰지 않는다 |
| 지식 유형 값 `fictional` | **허구** | Fiction | `fictional` | "창작", "가상"은 쓰지 않는다 |
| 허구 테마에 맞는 개인 문서 | **허구로 보이는 문서** | Fiction-like Document | `fiction_like_documents` | 개인 지식 베이스의 문서 중 문서 매치가 지식 유형 `fictional`인 테마인 것. "창작물"은 쓰지 않는다 |
| 평판의 허구 비율 | **허구 문서 비율** | Fiction Share | `fiction_share`, `reputation_fiction_share_*` | 허구로 보이는 문서 수 / 개인 지식 베이스의 공개 문서 수 |
| 운영 단위 | **서버** | Server | `ServerMode`, `server_mode()`, `claire server check`, 서버 액터 `/ap/actors/server` | `server_settings` 테이블 (v25 전 이름 `instance_settings`). Claire 설치 하나를 Mastodon 문서([Mastodon 101](https://github.com/mastodon/blog/blob/master/content/posts/2018-08-21_Mastodon-101/index.md))처럼 "서버"라고 부른다. "인스턴스"는 쓰지 않는다 |
| 파일 투영 | **볼트 (폐기됨)** | Vault (Retired) | `vault_path` (legacy default "") | 제거됨 (영구 폐기) |
| 추출 지향성 | **초점** | Extraction Focus | `focus` | `documents.meta["focus"]` (데이터만 남음. 수집 초점은 봉인됨, CT28) |
| 국소 그래프 | **로컬 그래프** | Local Graph | `renderLocalGraphHtml()`, `ctx-local-graph` | 문서 문맥 패널 연결 탭의 인라인 SVG 그래프 (링 1 문서 엔티티 최대 24, 링 2 타 문서 연결 엔티티 최대 12) |
| 본문 마크 | **엔티티 마크** | Entity Mark | `applyEntityMarks()`, `claire-entity` | CSS Custom Highlight API 기반 리더 본문 엔티티 하이라이트 (DOM 트리 변경 없음) |
| 통합 검색·실행 창 | **명령 팔레트** | Command Palette | `ClaireCommandPalette` (`src/bible/static/js/command_palette.js`) | `<dialog id="cmdpalette">` 기반 명령·문서·엔티티 통합 검색 및 이동 모달 (`/` 키) |
| 링크 따라가기 | **링크 확장** | Link Expansion | `expand` (`src/claire/expand/`, `expand_queue`, `expand-loop`, `expand-run`, 등급 기능 `expand`, `CLAIRE_EXPAND_MAX`) | 적재한 문서의 링크를 따라가 관련 문서를 더한다. 깊이는 1로 고정이며 이름에 넣지 않는다. "1홉 확장"은 옛 이름이다 ([EXPANSION.md](EXPANSION.md)) |
| 자동 방식 | **자동 확장** | Automatic Expansion | `auto_expand`, `expand/automatic.py`, 원천값 `expand:<부모 id>` | LLM이 링크를 선별하고 확장 게이트를 통과한 링크를 독립 문서로 적재한다. 끄면 확장 제안이 남는다 |
| 버튼 제안 방식 | **확장 제안** | Expansion Proposal | `expand/proposal.py`, 원천값 `expand-proposal` | 자동 확장이 꺼져 있을 때 텔레그램 버튼으로 후보 링크를 제안한다 |
| 적재 관문 | **확장 게이트** | Expansion Gate | `expand/gate.py`, `judge_gate` | 관련성(0.7)·품질(0.6) 임계. 자동 확장과 맥락 조사가 함께 쓴다 |
| 대상 조사 | **맥락 조사** | Contextual Research | `retrieval/research.py`, `contextual_research` | 사용자가 고른 대상을 맥락 안의 의미로 웹 조사한다. 링크 확장이 아니다 |
| watch 문서 | **주기 재확인** | Watch | `watch_enabled`, `watch_interval_days`, `refresh_queue` (reason `watch`) | 등록 문서를 주기마다 다시 가져온다. "주기 크롤링", "주기 갱신"은 옛 이름이다 |
| 유사 문서 정리 | **근사 중복 병합** | Near-Duplicate Merge | `dedup-scan`, `dedup-merge` | 본문 유사도로 찾은 중복 문서를 대표 문서로 합친다. 동일 주제 출처 병합과 다르다 |
| 같은 주제 자식 문서 | **동일 주제 출처 병합** | Same-Subject Source Merge | `same_subject`, `same_subject_merge`, `same_subject_shadow` | 링크 확장의 자식 문서를 새 문서로 만들지 않고 부모 문서의 추가 출처로 붙인다. 지금은 꺼져 있고, 모드 `shadow`는 판정만 기록한다 ([EXPANSION.md](EXPANSION.md) §3) |
| 해소 판정 감사 | **판단 기록 스트림** | Decision Stream | `ResolutionDecision` | `documents.meta.resolution_log` ([GRAPH_BUNDLE_REVIEW.md](GRAPH_BUNDLE_REVIEW.md)) |
| 기능 추가 단위 | **플러그인** | Plugin | `plugin_host`, `claire.plugin_sdk`, `claire plugin` | `plugin.toml`이 있는 폴더 하나. 코어 파일을 고치지 않고 기능을 더한다. 서버 관리자가 플러그인 폴더에 설치한다. "이용자 플러그인"은 쓰지 않는다 ([INGESTION.md](INGESTION.md) §2.2) |
| 서버 관리자의 설치 위치 | **플러그인 폴더** | Plugin Folder | `CLAIRE_PLUGINS_DIR`, `plugin_folder()` | 호스트 `CB_PLUGINS_DIR`(기본 `./var/plugins`), 컨테이너 `/app/var/plugins`(읽기 전용). 데이터 폴더 밖이다 |
| 릴리스에 포함된 플러그인 | **프로젝트 플러그인** | Project Plugin | `ORIGIN_PROJECT`, 발행자 `claire` | 프로젝트 플러그인 폴더 `<app root>/plugins`와 `plugins/project.lock`에 있다. 출처 값 `project`(플러그인 폴더의 것은 `plugin_folder`) |
| 출처 하나의 플러그인 유형 | **적재 어댑터** | Ingest Adapter | 플러그인 유형 `ingest_adapter` | 레지스트리 항목(fetcher, 웹 어댑터)을 준다. "웹 어댑터"는 레지스트리 항목의 한 종류이고, 적재 어댑터 안에 들어갈 수 있다 |
| 처리할 요청을 알리는 기록 | **알림** | Notification | `notifications`, `claire.notifications`, `/api/me/notifications` | 계정 하나에게 연계 요청 하나(또는 한 묶음)를 알리는 `client.db`의 기록. 종류와 매개변수만 저장하고 문구는 카탈로그 `notify.<kind>.*`에서 만든다. 텔레그램 메시지(`src/claire/ops/notify.py`의 "push")나 외부 Mastodon 계정의 알림과 다르다 ([NOTIFICATIONS.md](NOTIFICATIONS.md)) |
| 다른 주체가 건 요청과 그 응답 | **연계 요청** | Linkage Request | 알림 종류 `KINDS` (분류 `request`·`answer`·`notice`) | 이 서버의 계정(플랫폼), 에이전트 클라이언트(에이전트), 다른 서버의 액터(ActivityPub)가 만들어 계정의 결정을 기다리는 항목, 또는 계정이 건 요청에 온 응답. 소유자 용어다. `/settings/connections`의 "연결", 공유 지식 베이스의 "상호 협력"과 다르다 |
| 알림을 받는 브라우저 | **웹 푸시 구독** | Web Push Subscription | `push_subscriptions`, `/api/me/push/subscriptions` | 계정의 브라우저 하나가 브라우저 제조사의 푸시 서비스로 알림을 받는 등록(RFC 8030). 화면에서는 "기기"라고 쓴다. 등록한 로그인 세션이 끝나면 지운다 |
| 알림이 여는 화면 | **진입 주소** | Entry Address | `notifications.target_path` | 계정이 그 항목을 보거나 결정하는 화면의 이 서버 경로(탭 포함). 예: `/moderation/kb/{번호}?tab=queue` |
| 정적 에이전트 번들 | **에이전트 클라이언트** | Agent Client | `agent_clients/`, `fofwisdom/claire-agents` (`v0.4.4`), `plugins/antigravity/claire`, `plugins/claude/claire` | 전역 MCP 서버(`"claire"`)와 분리해 각 `client.toml` `[adapter]` 선언으로 2축 사용자 스킬(`claire-ingest`·`claire-query` / `/claire:ingest`·`/claire:query`, 직접 적재와 대기 큐 추출 통합)·격리 추출 서브에이전트·규칙/가드(`hooks/hooks.json`, 숨겨진 `extract-runner` 포크 러너, Antigravity 최대 3개 `suggestedPrompts`)를 담아 전용 마켓플레이스 저장소로 배포하는 정적 에이전트 플러그인. 서버 런타임(`plugin_host`)에 적재하지 않으며 Python 실행 진입점·비밀키·실행 비트를 금지한다 ([EXTRACTION.md](EXTRACTION.md) §9, [API_AND_MCP_GUIDE.md](API_AND_MCP_GUIDE.md) §3.4, [AGENT_CLIENT_COMMON_SPEC.md](../design/AGENT_CLIENT_COMMON_SPEC.md)) |
| 추출 실행 주체 선택 | **추출 모드** | Extraction Mode | 적재 요청의 `extraction_mode` (`provider` \| `client`), `raw_inbox.extraction_mode` | 적재 요청마다 고르는 추출 경로(계정 설정 아님, AED4-rev). `provider`는 서버 API로 즉시 추출하고(화면: "API로 바로 적재"), `client`는 원문을 `pending` 상태로 스테이징해 연결된 `agent_client`가 MCP/HTTP로 추출·제출한다(화면: "에이전트 클라이언트 대기"). API가 있는 계정의 기본값은 `provider`, 없으면 `client`뿐이다. 에이전트 클라이언트의 요청은 서버 API를 쓰지 않는다 ([INGESTION.md](INGESTION.md) §1, [EXTRACTION.md](EXTRACTION.md) §9) |
| 클라이언트 추출 대기 원문 | **스테이징 출처** | Staged Source | `documents.extraction_status = 'pending'`, `raw_inbox.status = 'staged'`, `claim_token` | 추출 모드 `client`로 `full_content=True`로 저장된 미추출 문서. 추출 완료(`extracted`) 전까지 질의·원문·공유·리서치·연합에서 격리되며 `recover-loop`도 건너뛴다 ([EXTRACTION.md](EXTRACTION.md) §9) |
| 플러그인 버전 | **플러그인 리비전 · 추종 커밋** | Plugin Revision · Core Commit | `revision`, `core_commit` | 버전은 이 두 값이다(`r3 @ 6010d69b`). 리비전은 커밋된 변경마다 1씩 오른다. 추종 커밋은 작성자가 기준으로 삼은 코어 커밋의 40자 해시다. "기여 리비전"(`kb_contribution_revisions`)과 다르다. API 수준(`api`)은 버전이 아니라 호환성 필드다 |
| 배타 선택 | **겹침** | Overlap | `overlap_group`, 상태 `overlap`, 오류 `plugin_overlap` | 같은 유형의 두 플러그인이 같은 종류·이름의 항목이나 같은 호스트를 가지면 겹친다(`*`는 제외). 겹침 그룹에서 하나만 켤 수 있다 |
| 판정 권한 없는 평가 | **Jev shadow 평가** | Jev Shadow Evaluation | `shadow.role = "shadow"` | 같은 후보를 Jev에 동시 질의하고 결과만 판정 기록에 남긴다 ([GRAPH_BUNDLE_REVIEW.md](GRAPH_BUNDLE_REVIEW.md) §5) |
| Jev shadow 쌍을 사람이 판정 | **엔티티 쌍 검토** | Entity Pair Review | `pair_review_items`, `pair_review_verdicts`, `/api/me/pair-review` | 정보가 있는 shadow 쌍을 대기열에 넣고, 그 지식 베이스를 읽을 수 있는 계정이 판정한다. 판정 2개 이상, 2/3 이상 일치가 합의 라벨이다 ([GRAPH_BUNDLE_REVIEW.md](GRAPH_BUNDLE_REVIEW.md) §5.5) |
| 제3 엔진의 미리 낸 답 | **사전 라벨** | Pre-label | `prelabel_*`, `decisions_prelabel` | Gemini Decisions API의 확률. 라벨로 세지 않고 사람 판정과 비교만 한다. 10건 중 3건은 판정 전까지 숨긴다 |
| 동일 엔티티 기준 | **동일 엔티티 규칙** | Same-Entity Rule | `same_entity_rule.py`, `SAME_ENTITY_RULE_VERSION` | LLM 판정, Jev, 사전 라벨, 사람 판정이 함께 쓰는 한 기준. 버전·에디션·제품과 회사·넓은 개념과 좁은 개념은 다른 것이다 |
