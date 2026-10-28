# Documentation Root Guide (`docs/`)

Claire Bible 프로젝트의 모든 아키텍처 설계, 구현 명세, 와이어 계약 및 운영 문서는 **문서 정본(`docs/`)** 하위에서 통합 관리합니다. 기존 업스트림 및 공통 계약 중 현재 시스템 구현과 정확히 일치하는 요소(공개 아키텍처 파이프라인, 1홉 확장 병합 명세, OpenAPI 3.1.0 사양, HTTP 와이어/보안 계약, 스키마 버전 v13 계약, OAuth 2.0 토큰 교환, 공통 용어집)는 모두 **`docs/implementation/`**에 정본으로 통합 보관됩니다.

---

## 디렉터리 개요

```text
docs/
├── README.md                   # 문서 전체 구조 안내 (본 문서)
├── GOALS.md                    # 최상위 비전, 범위 및 로드맵 정본
├── FAVICON.md                  # 파비콘 3D 기하학 그래픽 디자인 명세
├── CLAIRE_ARCHITECTURE_ROADMAP.md # 듀얼 트랙(Docker/K8s) 아키텍처 로드맵
├── design/                     # [설계 내역] 신규/개정 아키텍처 및 시스템 상세 설계
│   ├── ANTIGRAVITY_EXECUTION_AND_LATENCY_OPTIMIZATION_DESIGN.md
│   ├── ASCIIDOC_ENHANCEMENT_DESIGN.md
│   ├── CROSS_TYPE_ENTITY_RESOLUTION_AND_VLM_LOCALIZATION_DESIGN.md
│   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md
│   ├── DUAL_FORMAT_ADOC_DESIGN.md
│   ├── EXPAND_FILTERING_DESIGN.md
│   ├── FETCHER_CONTRIBUTION_AND_ONPREMISE_NETWORK_DESIGN.md
│   ├── MCP_SUPPORT.md
│   ├── SYNDICATION_ENDPOINT_DESIGN.md
│   └── ... (기능별 상세 아키텍처 설계 명세)
├── implementation/             # [구현/운영 내역] 현재 구현과 정확히 일치하는 정본 명세
│   ├── API_AND_MCP_GUIDE.md    # REST API 및 MCP 연결·인증·도구 활용 종합 가이드
│   ├── API_HTTP_CONTRACT.md    # HTTP REST/ASGI 와이어 프로토콜 및 인증 보안 계약
│   ├── COMMANDS.md             # 전체 CLI 명령어 및 레퍼런스
│   ├── ENVIRONMENT_VARIABLES.md # 환경변수 설정 및 Pydantic 검증 매뉴얼
│   ├── EXTERNAL_ACCESS.md      # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세
│   ├── GLOSSARY.md             # 지식 코어 데이터 모델 및 시스템 공통 표준 용어집
│   ├── ONEHOP_MERGE_DESIGN.md  # 1홉 확장 중복 완화 및 동일 주제 병합 구현 명세
│   ├── OPERATIONS.md           # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
│   ├── PLAN.md                 # 공개 아키텍처 및 파이프라인 처리 계획 정본
│   ├── SCHEMA_VERSIONING.md    # SQLite 공통 schema version v13 및 lineage 계약
│   ├── TOKEN_EXCHANGE.md       # RFC 8693 OAuth 2.0 Token Exchange 토큰 교환 계약
│   └── openapi.yaml            # 기계 판독용 OpenAPI 3.1.0 공통 API 사양서 정본
└── screenshots/                # README 및 UI 설명용 스크린샷 이미지 자산 (7종)
```

---

## 분류 및 참조 가이드

1. **[`docs/implementation/`](implementation/)**
   - 현재 실행 코드와 정확히 일치하는 기능 명세, 와이어 프로토콜, 스키마 계약 및 운영 문서 집합입니다.
   - HTTP 와이어 및 인증 보안 규칙은 [`API_HTTP_CONTRACT.md`](implementation/API_HTTP_CONTRACT.md), 기계 판독용 OpenAPI 사양은 [`openapi.yaml`](implementation/openapi.yaml)을 따릅니다.
   - SQLite DB 스키마 버전 및 마이그레이션 규칙은 [`SCHEMA_VERSIONING.md`](implementation/SCHEMA_VERSIONING.md), OAuth 2.0 토큰 교환은 [`TOKEN_EXCHANGE.md`](implementation/TOKEN_EXCHANGE.md)에 정의되어 있습니다.
   - 지식 모델 표준 용어 및 코드 매핑은 [`GLOSSARY.md`](implementation/GLOSSARY.md), 공개 파이프라인 아키텍처는 [`PLAN.md`](implementation/PLAN.md)를 참조합니다.

2. **[`docs/design/`](design/)**
   - 각 하위 서브시스템의 심층 아키텍처 설계와 엔지니어링 결정 내역입니다.
   - 기능 구현 시의 배경, 대안 평가, 보안 경계 및 다이어그램을 보관합니다.
