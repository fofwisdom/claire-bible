# Origin Documentation Directory Guide (`docs/origin/`)

Claire Bible 저장소([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible))의 문서는 **설계 내역(`design/`)**과 **구현/운영 내역(`implementation/`)**으로 엄격히 구분하여 보관한다.

기존 업스트림 및 공통 계약 중 현재 시스템 구현과 정확히 일치하는 요소(공개 아키텍처 파이프라인, 1홉 확장 병합 명세, OpenAPI 3.1.0 사양, HTTP 와이어/보안 계약, 스키마 버전 v13 계약, OAuth 2.0 토큰 교환, 공통 용어집)는 모두 **`docs/origin/implementation/`**에 정본으로 통합 보관된다.

---

## 디렉터리 구조

```text
docs/origin/
├── README.md                   # 오리진 문서 보관 규칙 및 구조 안내 (본 문서)
├── GOALS.md                    # 오리진 목표와 로드맵 (최상위 정본)
├── FAVICON.md                  # 파비콘 3D 기하학 그래픽 디자인 및 생성 명세
├── CLAIRE_ARCHITECTURE_ROADMAP.md # 듀얼 트랙(Docker/K8s) 아키텍처 로드맵
├── design/                     # [설계 내역] 신규/개정 아키텍처 및 시스템 상세 설계
│   ├── ANTIGRAVITY_EXECUTION_AND_LATENCY_OPTIMIZATION_DESIGN.md # Antigravity CLI 적재 지연 해소 및 실행 격리·최적화 아키텍처 설계
│   ├── ASCIIDOC_ENHANCEMENT_DESIGN.md    # AsciiDoc 기능 고도화 설계 명세서 (수식·상호참조 구현 및 적합성 평가 완료)
│   ├── CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md # 교차 타입 엔티티 해소(Refinement) 및 VLM 언어 일원화 설계 (구현 완료)
│   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md # 데이터 수명주기 및 정리(Purge) 설계
│   ├── DOCUMENT_READER_METADATA_AND_BIBLIO_SEPARATION_DESIGN.md # 문서 리더 UI 위계 및 원문 정보 / 적재 메타데이터(docmeta) 분리 설계
│   ├── DUAL_FORMAT_ADOC_DESIGN.md        # AsciiDoc 및 듀얼 포맷 본문 파이프라인 설계
│   ├── EXPAND_FILTERING_DESIGN.md        # 1홉 확장의 깊이 및 연관성 필터링 설계
│   ├── FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md # 수집기 기여 아키텍처 및 온프레미스 사설망 보안 설계
│   ├── GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md # graphview.py 모듈화 및 정적 자산 분리 설계
│   ├── INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md # 원문 보존·서비스 보호·오염 통제 거버넌스 연구
│   ├── INTERNATIONALIZATION_AND_LOCALIZATION_DESIGN.md # 프로젝트 국제화(i18n) 및 형태소 분석 기반 다국어 지원 설계 명세서 (초안)
│   ├── MCP_SUPPORT.md                    # MCP 지원 아키텍처 및 표준 인증 명세 (오리진 개정본)
│   ├── MULTI_PROVIDER_DESIGN.md          # 멀티 LLM 프로바이더 및 캘리브레이션 설계
│   ├── MULTI_THEME_ARCHITECTURE_DESIGN.md # 시퀀스 기반 지식 테마 다중 DB 격리 및 RBAC 아키텍처 설계
│   ├── OPERATIONAL_MIGRATION.md          # 운영 지원 업데이트 및 환경변수/DB 마이그레이션 설계
│   ├── PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md # PDF 추출 예산 및 적응형 추론(Effort) 설계
│   ├── PREFERRED_LANGUAGES_DESIGN.md     # 프로젝트 광역 선호 언어(Preferred Languages) 설계
│   ├── QUALITY_GUARDRAILS_AND_SUMMARY_INTEGRITY_DESIGN.md # 품질 검증 가드레일 런타임 컴포넌트화, 요약 무결성 및 관측성 개선 설계
│   ├── RIGHT_MENU_COMPACT_DESIGN.md      # 우측 메뉴 컴팩트화 및 반응형 UI 설계
│   ├── ORIGIN_SCHEMA_EXTENSIONS.md        # 오리진 스키마 확장과 retired version 복구 정책
│   ├── TABLE_INGESTION_DESIGN.md         # 원문 테이블 적재 및 본문 글자 수 제한 제외 설계
│   ├── TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md # 프로바이더 텔레메트리 격리 및 Support Bundle 아키텍처 설계
│   ├── TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md # 텔레그램 첨부 파일·하이퍼링크 복합 적재 설계 (구현 완료)
│   ├── TELEGRAM_VIDEO_PDF_BUNDLE_INGESTION_DESIGN.md # 텔레그램 MP4 STT·PDF 복합 적재 설계 (구현 전)
│   ├── VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md # 비디오 음성 자막(전사) 생성 및 지식 적재 파이프라인 설계
│   ├── VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md # VMware Explore 비디오·Presentation PDF 동시 적재 설계 (구현 완료)
│   ├── VIEW_CONTEXTUAL_UNIFIED_SEARCH_DESIGN.md # 뷰 종속형 통합 검색 및 지능형 자동 검색 엔진 아키텍처 설계 (구현 완료)
│   └── VMWARE_EXPLORE_ACTIVE_WAITING_RESILIENCE_DESIGN.md # VMware Explore 프레젠테이션 탐색 능동 대기(Active Waiting) 및 복원력 설계 (구현 완료)
├── implementation/             # [구현/운영 내역] 현재 구현과 정확히 일치하는 정본 명세
│   ├── API_AND_MCP_GUIDE.md    # REST API 및 MCP 연결·인증·도구 활용 종합 가이드
│   ├── API_HTTP_CONTRACT.md    # HTTP REST/ASGI 와이어 프로토콜 및 인증 보안 계약
│   ├── COMMANDS.md             # 전체 CLI 명령어 및 레퍼런스
│   ├── ENVIRONMENT_VARIABLES.md # 환경변수 설정, .env 계층, Pydantic 검증 및 운영 종합 매뉴얼
│   ├── EXTERNAL_ACCESS.md      # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세
│   ├── GLOSSARY.md             # 지식 코어 데이터 모델 및 시스템 공통 표준 용어집
│   ├── ONEHOP_MERGE_DESIGN.md  # 1홉 확장 중복 완화 및 동일 주제 병합 구현 명세
│   ├── OPERATIONS.md           # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
│   ├── PLAN.md                 # 공개 아키텍처 및 파이프라인 처리 계획 정본
│   ├── SCHEMA_VERSIONING.md    # SQLite 공통 schema version v13 및 lineage 계약
│   ├── TOKEN_EXCHANGE.md       # RFC 8693 OAuth 2.0 Token Exchange 토큰 교환 계약
│   └── openapi.yaml            # 기계 판독용 OpenAPI 3.1.0 공통 API 사양서 정본
└── screenshots/                # README 및 UI 설명용 스크린샷 이미지 자산 (7종)
    ├── connection-path.png
    ├── content-ingestion-form.png
    ├── document-reader.png
    ├── favicon-preview.png
    ├── knowledge-graph-overview.png
    ├── multi-node-synthesis.png
    └── search-and-node-details.png
```

---

## 문서 작성 및 저장 규칙

문서를 추가하거나 수정할 때는 반드시 다음 기준에 따라 저장 위치를 결정해야 한다.

### 1. `docs/origin/design/` (설계 내역)
- **대상**: 신규 기능 기획, 알고리즘 및 파이프라인 설계, 아키텍처 결정 내역 및 엔지니어링 리서치.
- **예시**:
  - `ANTIGRAVITY_EXECUTION_AND_LATENCY_OPTIMIZATION_DESIGN.md`: Antigravity CLI 적재 지연 해소, 에이전트 도구 오염 방지(`allow_tools=False`), 작업 디렉터리 격리 및 관계 판정 병렬화 설계 명세서
  - `ASCIIDOC_ENHANCEMENT_DESIGN.md`: AsciiDoc 기능 고도화 설계 명세서 (수식·상호참조 구현 및 적합성 평가 완료)
  - `CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md`: VLM 전사 본문 엔티티의 동일명/별칭 기반 교차 타입(`Concept` ↔ `Person`/`Org` 등) 가역적 병합(Refinement) 및 `CLAIRE_PREFERRED_LANGUAGES` 단일 진실 공급원(SSOT) 기반 VLM 프롬프트 현지화 설계 명세서 (구현 완료)
  - `DATA_LIFECYCLE_AND_PURGE_DESIGN.md`: 데이터 수명주기 및 연쇄 소각 설계
  - `INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md`: 원문 보존·서비스 보호·오염 통제 상충 및 지식 무결성 거버넌스 연구
  - `DUAL_FORMAT_ADOC_DESIGN.md`: AsciiDoc/Markdown 듀얼 포맷 렌더링 파이프라인
  - `FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md`: 수집기 2계층 인터페이스 규약, 3계층 디스커버리, `plugins/fetchers/` Git 무충돌 격리 및 온프레미스 사설망 SSRF 보안 설계
  - `GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md`: graphview.py 모듈화, 정적 자산 분리 및 Phase 1~4 로드맵
  - `MULTI_PROVIDER_DESIGN.md`: 멀티 LLM 프로바이더 및 캘리브레이션 설계
  - `MULTI_THEME_ARCHITECTURE_DESIGN.md`: 시퀀스 기반 지식 테마 다중 DB 격리, 4단계 RBAC 및 적재 초점(default_focus) 아키텍처 설계
  - `QUALITY_GUARDRAILS_AND_SUMMARY_INTEGRITY_DESIGN.md`: 품질 검증 가드레일 런타임 컴포넌트화, 요약 무결성 및 관측성 개선 설계
  - `ORIGIN_SCHEMA_EXTENSIONS.md`: 공동 v13을 기준으로 한 오리진 확장, retired v12 복구와 보조 저장소 version 계획
  - `OPERATIONAL_MIGRATION.md`: 환경변수 migration과 전체 DB inventory 기반 update/backup 계획
  - `TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md`: 프로바이더 및 MCP 텔레메트리 격리, 독립 schema/lineage, MCP 관측성 확장 및 Support Bundle 통합 설계
  - `TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md`: 텔레그램 첨부 파일(PDF/영상/문서)과 캡션 내 하이퍼링크(YouTube/웹)를 단일 메시지에서 원자적으로 감지하여 결합 적재하는 복합 적재 파이프라인 명세서 (Fail-Atomic 무결성 검증 및 구현 완료)
  - `TELEGRAM_VIDEO_PDF_BUNDLE_INGESTION_DESIGN.md`: 텔레그램 앨범 또는 명시적 세션에서 MP4 STT와 PDF를 원자적 단일 문서로 묶는 설계 (구현 전)
  - `MCP_SUPPORT.md`: RFC 6750 표준 인증 기반 MCP 지원 설계
  - `VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md`: 비디오 음성 자막(STT) 생성 및 지식 적재 파이프라인 설계
  - `VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md`: VMware Explore 영상에서 CC/STT와 Presentation PDF를 단일 세션 문서로 원자적 적재하는 설계 및 구현 명세
  - `VIEW_CONTEXTUAL_UNIFIED_SEARCH_DESIGN.md`: 뷰 종속형 통합 검색 및 무설정 백엔드 지능형 검색 엔진 아키텍처 설계 명세서

### 2. `docs/origin/implementation/` (구현 및 운영 내역)
- **대상**: 현재 시스템에 실제 구현 및 배포된 기능의 운영 가이드, 호스트 명령 명세, 네트워크 및 프록시 설정, 환경변수, 와이어 프로토콜 및 스키마 계약.
- **예시**:
  - `API_AND_MCP_GUIDE.md`: REST API 엔드포인트 호출 및 MCP 10종 툴셋, OAuth 2.1 인가 서버 연동 종합 가이드
  - `API_HTTP_CONTRACT.md`: HTTP REST/ASGI 와이어 프로토콜 및 인증 보안 계약
  - `ENVIRONMENT_VARIABLES.md`: 환경변수 설정, `.env` 계층 구조, Pydantic 검증 규칙 및 운영 모범 사례 매뉴얼
  - `EXTERNAL_ACCESS.md`: Reverse proxy 연동, 포트 바인딩, CORS 및 인증 명세
  - `GLOSSARY.md`: 지식 코어 데이터 모델 및 시스템 공통 표준 용어집
  - `ONEHOP_MERGE_DESIGN.md`: 1홉 확장 중복 완화 및 동일 주제 원천 병합 구현 명세
  - `OPERATIONS.md`: `cb-manuscript` 호스트 운영 명령 및 컨테이너 관리 가이드
  - `COMMANDS.md`: `cb-manuscript` 및 `claire` 전체 CLI 상세 명세 및 옵션 상태 레퍼런스
  - `PLAN.md`: 공개 아키텍처 및 파이프라인 처리 계획 정본
  - `SCHEMA_VERSIONING.md`: SQLite 공통 schema version v13 및 lineage 계약
  - `TOKEN_EXCHANGE.md`: RFC 8693 OAuth 2.0 Token Exchange 토큰 교환 계약
  - `openapi.yaml`: 기계 판독용 OpenAPI 3.1.0 공통 API 사양서 정본

### 3. `docs/origin/screenshots/` (스크린샷 에셋)
- README 및 가이드 문서에서 참조하는 UI/그래프 캡처 이미지 자산.

---

## 상대 경로 및 링크 규칙
- 최상위 목표와 로드맵은 `docs/origin/design/`에서 `../GOALS.md`로 참조한다.
- 구현 및 와이어/스키마 계약은 `docs/origin/design/`에서 `../implementation/<FILE>`로 참조한다.
- 동일 분류 폴더 내 문서는 파일명으로 직접 링크한다 (예: `[DATA_LIFECYCLE_AND_PURGE_DESIGN.md](DATA_LIFECYCLE_AND_PURGE_DESIGN.md)`).
- 다른 분류의 문서를 참조할 때는 `../<폴더>/<FILE>` 경로를 사용한다 (예: `[EXTERNAL_ACCESS.md](../implementation/EXTERNAL_ACCESS.md)`).
