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
│   ├── EXTERNAL_ACCESS.md  # 원작 외부 접속 설계 초안
│   ├── MCP_SUPPORT.md      # 원작 MCP 지원 설계 명세
│   ├── ONEHOP_MERGE_DESIGN.md
│   ├── SYNTHESIS_REDESIGN.md
│   └── ... (원작 리서치 및 아티클 자료)
│
└── origin/                 # [오리진 작업/병합] fofwisdom/claire-bible 자체 생성 및 병합/개정 문서
    ├── README.md           # 오리진 문서 분류 규칙 및 작성 가이드
    ├── FAVICON.md          # 파비콘 3D 기하학 그래픽 디자인 명세
    ├── design/             # [설계 내역] 아키텍처, 기능 설계, 시스템 설계, 기술 리서치
    │   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md
    │   ├── DUAL_FORMAT_ADOC_DESIGN.md
    │   ├── EXPAND_FILTERING_DESIGN.md
    │   ├── MCP_SUPPORT.md
    │   ├── MULTI_PROVIDER_DESIGN.md
    │   ├── ONEHOP_MERGE_DESIGN.md
    │   ├── OPERATIONAL_MIGRATION.md
    │   ├── RIGHT_MENU_COMPACT_DESIGN.md
    │   ├── SYNTHESIS_REDESIGN.md
    │   └── ...
    ├── implementation/     # [구현/운영 내역] 운영 가이드, 네트워크/인증 명세, 배포 설정
    │   ├── COMMANDS.md
    │   ├── EXTERNAL_ACCESS.md
    │   └── OPERATIONS.md
    └── screenshots/        # README 및 UI 설명용 스크린샷 이미지 자산
```

---

## 분류 및 참조 가이드

1. **[`docs/upstream/`](upstream/README.md)**
   - 원작 저장소([`blackan/claire_bible`](https://github.com/blackan/claire_bible))의 오리지널 기획, 설계 초안 및 리서치 자료입니다.
   - 업스트림과의 정합성을 추적하고 원본 아이디어를 참고할 때 활용합니다.

2. **[`docs/origin/`](origin/README.md)**
   - 본 저장소([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible))에서 직접 신규 작성하였거나, 업스트림 설계를 기반으로 대폭 발전/개정/구현한 문서입니다.
   - 현재 실행 중인 시스템의 실제 구현 상태와 최신 설계는 `docs/origin/`을 기준으로 합니다.
