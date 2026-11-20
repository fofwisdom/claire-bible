# Documentation Root Guide (`docs/`)

Claire Bible 프로젝트의 모든 아키텍처 설계, 구현 명세, 운영 문서는 **문서 정본(`docs/`)** 하위에서 통합 관리합니다.

---

## 디렉터리 개요

```text
docs/
├── README.md                   # 문서 전체 구조 안내 (본 문서)
├── PRINCIPLES.md               # 프로젝트 원칙 정본 (구현이 바뀌어도 유지되는 규칙)
├── GOALS.md                    # 최상위 비전, 범위 및 로드맵 정본
├── FAVICON.md                  # 파비콘 3D 기하학 그래픽 디자인 명세
├── design/                     # [설계] 의도와 이유: 구현을 마치지 않은 아키텍처, 구현을 마친 작업의 설계 원칙 (현재 상태는 말하지 않음)
│   │   # 미구현 또는 일부 구현 (상태 줄에 구현된 부분과 구현 문서 링크)
│   ├── AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md  # 에이전트 주도 추출·agent_client·OAuth 2.1 통일·gemini-embedding-2 지원/이관 계획(AE6a–AE6d, Rev. 2.1), 미구현
│   ├── DATA_STORE_LAYOUT_DESIGN.md        # RD27 구현(client.db), DS-3 미구현
│   ├── EXPANSION_DESIGN.md
│   ├── FEDERATION_REMOTE_READING_DESIGN.md  # FP4 구현 완료, 남은 계획 D1·D2·D4(RD12 보류)·피드 자동 적재·아이디어 I1–I3, 시각 프로토타입은 design/prototypes/
│   ├── CONSOLE_LAYOUT_DESIGN.md           # 설정 및 관리 콘솔의 배치·탐색 초안 (CL 규칙, CQ 질문), 작업 기준 시각 프로토타입은 design/prototypes/console_modern_prototype.html
│   ├── INGEST_ADAPTER_PLUGIN_DESIGN.md    # 적재 어댑터 플러그인화(프로젝트 플러그인 폴더·플러그인 폴더 분리·매니페스트·리비전·추종 커밋·겹침 배타 선택·관리자 켜기/끄기), PL0–PL4 구현
│   ├── JEV_SHADOW_EVALUATION_DESIGN.md    # Jev shadow 전용(필터·하이브리드 제거), 정기 평가의 엔티티 쌍 검토 백엔드 구현(→ GRAPH_BUNDLE_REVIEW.md §5.5), 이용자 판정 프런트엔드·R7 미구현, 평가 결과 JEV_EVALUATION.md
│   ├── KNOWLEDGE_FEDERATION_DESIGN.md     # 서버 간 지식 연합, KF0–KF6 구현, 남은 일 KA1–KA5, 루트 페이지 최근 활동 형태는 design/prototypes/recent_activity_prototypes.html 평가 대기(KD15)
│   ├── MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md  # P0–P7 구현, 남은 계획은 폐기(2026-11-19): /explore 통합 뷰는 /accounts·/themes로, 계정 간 발행 본문은 기여로 대체
│   ├── PUBLIC_DOMAIN_BLOCK_LIST_DESIGN.md  # 차단 도메인 공개 목록 제안(BQ1–BQ6 소유자 답변 대기), 미구현
│   ├── SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md  # 공유 지식베이스·컬렉션 테마 초안 (CT 결정), 작업 기준 시각 프로토타입은 design/prototypes/console_modern_prototype.html (지식 연합·콘솔 배치 설계와 공용), 첫 프로토타입 shared_knowledge_federation_prototype.html은 참고용
│   │   # 구현 완료: 설계 원칙만 (상태 줄의 링크가 현재 동작)
│   ├── ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md  # FP1–FP3 → implementation/FEDERATION.md
│   ├── ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md  # → ACCOUNTS.md
│   ├── ASCIIDOC_ENHANCEMENT_DESIGN.md     # → DOCUMENT_FORMAT.md
│   ├── CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md  # → EXTRACTION.md
│   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md  # → DATA_LIFECYCLE.md
│   ├── DECISION_STREAM_AND_HEATMAP_MATRIX_DESIGN.md  # → GRAPH_BUNDLE_REVIEW.md, 가역 병합·UI 롤백은 구상(ideas/REVERSIBLE_MERGE_IDEAS.md)
│   ├── FOCUS_STANDARDIZATION_AND_PURGE_DESIGN.md  # → DATA_LIFECYCLE.md
│   ├── GRAPH_BUNDLE_REVIEW_EVIDENCE_DESIGN.md  # → GRAPH_BUNDLE_REVIEW.md
│   ├── GRAPH_ENGINE_SHOPIFY_EDITIONS_ADOPTION_DESIGN.md  # → GRAPH_ENGINE.md
│   ├── GRAPHVIEW_MODULARIZATION_AND_STATIC_ASSET_DESIGN.md  # → GRAPH_ENGINE.md
│   ├── FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md  # → INGESTION.md
│   ├── HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md  # HD1–HD14 → WEBUI.md
│   ├── I18N_DESIGN.md                     # ID1–ID6 → I18N.md
│   ├── INITIAL_FULL_INGESTION_AND_EFFORT_DESIGN.md  # → INGESTION.md
│   ├── KNOWLEDGE_GRAPH_LINKING_AND_CALIBRATION_DESIGN.md  # → EXTRACTION.md, 2홉 누락 링크는 구상(ideas/MISSING_LINK_DISCOVERY_IDEAS.md)
│   ├── INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md  # 원문 보존·관측성 원칙 → INGESTION.md
│   ├── LLM_PRESENTATION_AUTHORING_AND_COMPOSITION_DESIGN.md  # → PRESENTATION.md
│   ├── MARKDOWN_RETIREMENT_DESIGN.md      # RD22–RD25 → DOCUMENT_FORMAT.md
│   ├── MCP_SUPPORT.md                     # → API_AND_MCP_GUIDE.md, 미구현 제안 MP1–MP4
│   ├── MULTI_PROVIDER_DESIGN.md           # → PROVIDERS.md
│   ├── MULTI_THEME_ARCHITECTURE_DESIGN.md  # → THEMES.md
│   ├── PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md  # → INGESTION.md
│   ├── PER_ACCOUNT_FEATURES_DESIGN.md     # PA1–PA4 → ACCOUNTS.md
│   ├── PUBLIC_SITE_CRAWLING_AND_INDEXING_DESIGN.md  # → PUBLIC_SITE.md, 남은 색인 항목은 구상(ideas/PUBLIC_SITE_INDEXING_IDEAS.md)
│   ├── PREFERRED_LANGUAGES_DESIGN.md      # → EXTRACTION.md
│   ├── QUALITY_GUARDRAILS_AND_SUMMARY_INTEGRITY_DESIGN.md  # → PROVIDERS.md, EXTRACTION.md
│   ├── TABLE_INGESTION_DESIGN.md          # → INGESTION.md
│   ├── TELEGRAM_ATTACHMENT_LINK_COMPOSITE_INGESTION_DESIGN.md  # → INGESTION.md
│   ├── VIDEO_AUDIO_TRANSCRIPTION_AND_INGESTION_DESIGN.md  # → INGESTION.md
│   ├── VIDEO_PRESENTATION_BUNDLE_INGESTION_DESIGN.md  # → INGESTION.md
│   └── ZSTD_COMPRESSION_MIGRATION_DESIGN.md  # → DATA_LIFECYCLE.md
├── implementation/             # [구현] 지금 있는 것: 구현을 마친 아키텍처, 용어 정의, 에이전트용 CLI 명령어 설명서
│   ├── ACCOUNTS.md             # 계정·로그인·자격증명·초대·외부 IdP·통합 콘솔·계정별 기능(봇·번들·백업) 구현 정본
│   ├── API_AND_MCP_GUIDE.md    # REST API 및 MCP 연결·인증·도구 활용 종합 가이드
│   ├── API_HTTP_CONTRACT.md    # HTTP API 경로와 동작
│   ├── COMMANDS.md             # 에이전트용 CLI 명령어 설명서 (전체 명령어 레퍼런스)
│   ├── DATA_LIFECYCLE.md       # 원문 zstd 아티팩트, 소각·툼스톤·감사, 초점(focus) 저장
│   ├── DOCUMENT_FORMAT.md      # 문서 본문 형식(AsciiDoc)·AOT 렌더링·Markdown 내보내기
│   ├── ENVIRONMENT_VARIABLES.md # 환경변수 설정 및 Pydantic 검증 매뉴얼
│   ├── EXTERNAL_ACCESS.md      # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세
│   ├── EXTRACTION.md           # 추출·엔티티 해소·온톨로지·임베딩·문서 간 링크
│   ├── FEDERATION.md           # Fedify 사이드카 연합 구현 정본 (FP1–FP4: 내부 연합 API, 탐색, 팔로워, 발행, 연합 클라이언트, 원격 읽기, 피드)
│   ├── GLOSSARY.md             # 용어 정의: 지식 코어 데이터 모델 및 시스템 표준 용어집
│   ├── GRAPH_BUNDLE_REVIEW.md  # 그래프 번들, 해소 판정 기록·히트맵·판단 기록 스트림, 검토 증거, Jev shadow 평가, 엔티티 쌍 검토(사람 판정·사전 라벨·정기 평가 보고서)
│   ├── GRAPH_ENGINE.md         # 지식 그래프 엔진(배치·렌더·상호작용·카메라)과 페이지 조립·정적 자산
│   ├── I18N.md                 # 인터페이스 언어: 카탈로그, 언어 결정 순서, 번역 범위, 검색 토크나이저
│   ├── JEV_EVALUATION.md       # Jev 평가 절차의 실행 결과 (라벨, 정밀도·재현율, 관문, 역할)
│   ├── IMPLEMENTATION_STATUS.md # 치명적이지 않은 구현 상태(알려진 제약, 보류 결정, 테스트 기준선)
│   ├── INGESTION.md            # 적재: 라우터·fetcher·사설망 가드·PDF 파서·예산·표·영상 CC/STT·VMware 발표자료·복합 적재
│   ├── KNOWLEDGE_FEDERATION.md # 공유 지식 연합(KF0): 어휘, JSON-LD 컨텍스트, 와이어 계약 빌더·검증기, 계약 픽스처
│   ├── EXPANSION.md         # 링크 확장과 동일 주제 출처 병합
│   ├── OPERATIONS.md           # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
│   ├── PLAN.md                 # 공개 아키텍처와 파이프라인 처리 흐름
│   ├── PRESENTATION.md         # 프레젠테이션 저작·컴파일·뷰어·테마별 스위치
│   ├── PROVIDERS.md            # LLM·STT·VLM 프로바이더 선택, 설정 우선순위, 운영 환경 실패 동작 구현 정본
│   ├── PUBLIC_SITE.md          # 공개 사이트: robots.txt·sitemap.xml·공유 페이지 메타데이터·GA4 태그
│   ├── SCHEMA_VERSIONING.md    # SQLite 스키마 버전과 마이그레이션
│   ├── TELEMETRY.md            # 텔레메트리 DB, Support Bundle 생성·보관·파기
│   ├── THEMES.md               # 멀티 테마(단일 DB 파티셔닝) 저장·조회·라우팅·접근 경계 구현 정본
│   ├── WEBUI.md                # 웹 작업 공간: 앱 바, 홈 개요, 문맥 패널, 리더, 검색, 명령 팔레트, 적재 콕핏
│   └── openapi.yaml            # 기계 판독용 OpenAPI 3.1.0 API 사양서
└── screenshots/                # README 및 UI 설명용 스크린샷 이미지 자산 (7종)
```

---

## 분류 및 참조 가이드

0. **[`PRINCIPLES.md`](PRINCIPLES.md)**
   - 구현이 바뀌어도 유지되는 규칙만 둔다. 어기면 버그다. 원칙 간 충돌과 알려진 위반도 여기에 적는다.

1. **[`docs/implementation/`](implementation/)**
   - 현재 실행 코드와 정확히 일치하는 기능 명세, 와이어 프로토콜, 스키마 및 운영 문서 집합입니다.
   - HTTP 경로는 [`API_HTTP_CONTRACT.md`](implementation/API_HTTP_CONTRACT.md), 인증 채널과 거부 규칙은 [`API_AND_MCP_GUIDE.md`](implementation/API_AND_MCP_GUIDE.md) §1, 기계 판독용 OpenAPI 사양은 [`openapi.yaml`](implementation/openapi.yaml)을 따릅니다.
   - SQLite DB 스키마 버전 및 마이그레이션 규칙은 [`SCHEMA_VERSIONING.md`](implementation/SCHEMA_VERSIONING.md), OAuth 2.0 토큰 교환은 [`API_AND_MCP_GUIDE.md`](implementation/API_AND_MCP_GUIDE.md) §2.5에 있습니다.
   - Fedify 사이드카 연합(ActivityPub 탐색, 내부 연합 API, 액터 키, 준비 검사, reverse proxy 규칙)은 [`FEDERATION.md`](implementation/FEDERATION.md)에 정의되어 있습니다.
   - 지식 모델 표준 용어 및 코드 매핑은 [`GLOSSARY.md`](implementation/GLOSSARY.md), 공개 파이프라인 아키텍처는 [`PLAN.md`](implementation/PLAN.md)를 참조합니다.
   - 새로 설계하고 구현한 내용은 코드와 같은 커밋에서 이 디렉터리의 해당 문서에 기록합니다. 작업 전에 알아야 할 치명적이지 않은 제약, 보류 결정, 이미 실패하는 테스트는 [`IMPLEMENTATION_STATUS.md`](implementation/IMPLEMENTATION_STATUS.md)에 둡니다. 보안 문제는 문서에 적지 않습니다.

2. **[`docs/design/`](design/)**
   - 의도와 이유를 담습니다. 구현을 마치지 않은 아키텍처와 구현을 마친 작업의 설계 원칙만 둡니다([`AGENTS.md`](../AGENTS.md)).
   - 문서 머리의 상태 줄이 종류를 알려 줍니다. 구현된 부분은 상태 줄이 가리키는 `implementation/` 문서가 현재 동작의 정본입니다.

## 문서 정합성 검사

`tests/test_docs_consistency.py`가 `docs/*.md`와 `docs/implementation/*.md`를 검사합니다. 검사 항목은 상대 링크, 본문에 적힌 `src/`·`tests/` 등의 경로, 그리고 `ENVIRONMENT_VARIABLES.md`와 `Settings`의 변수 집합·기본값입니다. `design/`은 검사하지 않습니다.
