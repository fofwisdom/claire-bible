# Documentation Root Guide (`docs/`)

Claire Bible 프로젝트의 문서는 원작 저장소의 원본 문서를 보관하는 **업스트림(`docs/upstream/`)**과 본 저장소의 자체 작업 및 병합 내역을 보관하는 **오리진(`docs/origin/`)**으로 명확히 분리하여 관리합니다.

---

## 디렉터리 개요

```text
docs/
├── README.md               # 문서 전체 구조 안내 (본 문서)
│
├── upstream/               # [업스트림 원본] blackan/claire_bible 원작 저장소 문서
│   ├── README.md           # 업스트림 문서 목록 및 출처 안내
│   ├── EXTERNAL_ACCESS.md  # 원작 초기 외부 접속 설계 초안
│   ├── MCP_SUPPORT.md      # 원작 MCP 지원 설계 명세 (M1 배포본)
│   ├── ONEHOP_MERGE_DESIGN.md # 1홉 확장 중복 완화 설계 초안
│   ├── SYNTHESIS_REDESIGN.md  # 다중 노드 종합 재설계안
│   ├── codegraph.md, files.md, graphify.md, scrapling.md
│   └── search.jpg, search.md # 원작 RAG 리서치 자료
│
└── origin/                 # [오리진 작업/병합] fofwisdom/claire-bible 자체 생성 및 개정 문서
    ├── README.md           # 오리진 문서 분류 규칙 및 작성 가이드
    ├── FAVICON.md          # 파비콘 3D 기하학 그래픽 디자인 명세
    ├── design/             # [설계 내역] 오리진 자체 신규/개정 아키텍처 및 시스템 설계
    │   ├── ASCIIDOC_ENHANCEMENT_DESIGN.md    # AsciiDoc 기능 고도화 및 확장 설계 명세서 (Phase 1 수식·상호참조 구현 완료)
    │   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md # 데이터 수명주기 및 정리(Purge) 설계
    │   ├── DUAL_FORMAT_ADOC_DESIGN.md        # AsciiDoc 및 듀얼 포맷 본문 파이프라인 설계
    │   ├── EXPAND_FILTERING_DESIGN.md        # 1홉 확장의 깊이 및 연관성 필터링 설계
    │   ├── MCP_SUPPORT.md                    # MCP 지원 아키텍처 및 표준 인증 명세 (오리진 개정본)
    │   ├── MULTI_PROVIDER_DESIGN.md          # 멀티 LLM 프로바이더 및 캘리브레이션 설계
    │   ├── OPERATIONAL_MIGRATION.md          # 운영 지원 업데이트 및 환경변수/DB 마이그레이션 설계
    │   ├── PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md # PDF 추출 예산 및 적응형 추론(Effort) 설계
    │   ├── PREFERRED_LANGUAGES_DESIGN.md     # 프로젝트 광역 선호 언어(Preferred Languages) 설계
    │   ├── RIGHT_MENU_COMPACT_DESIGN.md      # 우측 메뉴 컴팩트화 및 반응형 UI 설계
    │   └── TABLE_INGESTION_DESIGN.md         # 원문 테이블 적재 및 본문 글자 수 제한 제외 설계
    ├── implementation/     # [구현/운영 내역] 운영 가이드, 네트워크/인증 명세, 배포 설정
    │   ├── COMMANDS.md     # 전체 CLI 명령어 및 미구현/제약사항 상세 레퍼런스
    │   ├── EXTERNAL_ACCESS.md # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세 (오리진 구현본)
    │   └── OPERATIONS.md   # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
    └── screenshots/        # README 및 UI 설명용 스크린샷 이미지 자산 (7종)
```

---

## 분류 및 참조 가이드

1. **[`docs/upstream/`](upstream/README.md)**
   - 원작 저장소([`blackan/claire_bible`](https://github.com/blackan/claire_bible))의 오리지널 기획, 설계 초안 및 리서치 자료입니다.
   - 업스트림 기능을 구현했더라도 문서를 별도로 개정하지 않은 경우 이곳의 원본 문서를 단일 정본으로 참조합니다.

2. **[`docs/origin/`](origin/README.md)**
   - 본 저장소([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible))에서 직접 신규 작성하였거나, 업스트림 설계를 기반으로 대폭 발전/개정/구현한 문서입니다.
   - 현재 실행 중인 시스템의 실제 구현 상태와 최신 설계는 `docs/origin/`을 기준으로 합니다.
