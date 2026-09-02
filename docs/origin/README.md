# Origin Documentation Directory Guide (`docs/origin/`)

Claire Bible 오리진 저장소([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible))의 문서는 **설계 내역(`design/`)**과 **구현/운영 내역(`implementation/`)**으로 엄격히 구분하여 보관합니다.

> [!NOTE]
> 업스트림([`blackan/claire_bible`](https://github.com/blackan/claire_bible))의 기능을 오리진에 구현/적용했더라도 업스트림 원본 문서를 직접 수정·개정하지 않은 문서(예: `ONEHOP_MERGE_DESIGN.md`, `SYNTHESIS_REDESIGN.md`, 각종 리서치 아티클)는 [`docs/upstream/`](../upstream/README.md)에 단일 보관하며 오리진에 중복 보관하지 않습니다.

---

## 디렉터리 구조

```text
docs/origin/
├── README.md                   # 오리진 문서 보관 규칙 및 구조 안내 (본 문서)
├── FAVICON.md                  # 파비콘 3D 기하학 그래픽 디자인 및 생성 명세
├── design/                     # [설계 내역] 오리진 자체 신규/개정 아키텍처 및 시스템 설계
│   ├── ASCIIDOC_ENHANCEMENT_DESIGN.md    # AsciiDoc 기능 고도화 및 확장 설계 명세서 (Phase 1 수식·상호참조 구현 완료)
│   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md # 데이터 수명주기 및 정리(Purge) 설계
│   ├── DUAL_FORMAT_ADOC_DESIGN.md        # AsciiDoc 및 듀얼 포맷 본문 파이프라인 설계
│   ├── EXPAND_FILTERING_DESIGN.md        # 1홉 확장의 깊이 및 연관성 필터링 설계
│   ├── INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md # 원문 보존·서비스 보호·오염 통제 거버넌스 연구
│   ├── MCP_SUPPORT.md                    # MCP 지원 아키텍처 및 표준 인증 명세 (오리진 개정본)
│   ├── MULTI_PROVIDER_DESIGN.md          # 멀티 LLM 프로바이더 및 캘리브레이션 설계
│   ├── OPERATIONAL_MIGRATION.md          # 운영 지원 업데이트 및 환경변수/DB 마이그레이션 설계
│   ├── PDF_INGESTION_AND_ADAPTIVE_EFFORT_DESIGN.md # PDF 추출 예산 및 적응형 추론(Effort) 설계
│   ├── PREFERRED_LANGUAGES_DESIGN.md     # 프로젝트 광역 선호 언어(Preferred Languages) 설계
│   ├── RIGHT_MENU_COMPACT_DESIGN.md      # 우측 메뉴 컴팩트화 및 반응형 UI 설계
│   └── TABLE_INGESTION_DESIGN.md         # 원문 테이블 적재 및 본문 글자 수 제한 제외 설계
├── implementation/             # [구현/운영 내역] 운영 가이드, 네트워크/인증 명세, 배포 설정
│   ├── COMMANDS.md             # 전체 CLI 명령어 및 미구현/제약사항 상세 레퍼런스
│   ├── EXTERNAL_ACCESS.md      # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세 (오리진 구현본)
│   └── OPERATIONS.md           # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
└── screenshots/                # README 및 UI 설명용 스크린샷 이미지 자산
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

문서를 추가하거나 수정할 때는 반드시 다음 기준에 따라 저장 위치를 결정해야 합니다.

### 1. `docs/origin/design/` (설계 내역)
- **대상**: 오리진 자체 아키텍처 설계, 신규 기능 기획, 알고리즘 및 파이프라인 설계, 업스트림 설계를 기반으로 대폭 개정/확장한 명세.
- **예시**:
  - `ASCIIDOC_ENHANCEMENT_DESIGN.md`: AsciiDoc 기능 고도화 및 확장 설계 명세서 (Phase 1 수식·상호참조 구현 완료)
  - `DATA_LIFECYCLE_AND_PURGE_DESIGN.md`: 데이터 수명주기 및 연쇄 소각 설계
  - `INGESTION_INTEGRITY_AND_POLLUTION_CONTROL_RESEARCH.md`: 원문 보존·서비스 보호·오염 통제 상충 및 지식 무결성 거버넌스 연구
  - `DUAL_FORMAT_ADOC_DESIGN.md`: AsciiDoc/Markdown 듀얼 포맷 렌더링 파이프라인
  - `MULTI_PROVIDER_DESIGN.md`: 멀티 LLM 프로바이더 및 캘리브레이션 설계
  - `MCP_SUPPORT.md`: RFC 6750 표준 인증 기반 MCP 지원 설계

### 2. `docs/origin/implementation/` (구현 및 운영 내역)
- **대상**: 현재 시스템에 실제 구현 및 배포된 기능의 운영 가이드, 호스트 명령 명세, 네트워크 및 프록시 설정, 환경변수 및 보안 경계 가이드.
- **예시**:
  - `EXTERNAL_ACCESS.md`: Reverse proxy 연동, 포트 바인딩, CORS 및 인증 명세 (오리진 배포 환경 기준)
  - `OPERATIONS.md`: `cb-manuscript` 호스트 운영 명령 및 컨테이너 관리 가이드
  - `COMMANDS.md`: `cb-manuscript` 및 `claire` 전체 CLI 상세 명세 및 옵션 상태 레퍼런스

### 3. `docs/origin/screenshots/` (스크린샷 에셋)
- README 및 가이드 문서에서 참조하는 UI/그래프 캡처 이미지 자산.

---

## 상대 경로 및 링크 규칙
- `docs/origin/design/` 또는 `docs/origin/implementation/` 내 문서에서 프로젝트 루트 파일(`GOALS.md`, `PLAN.md`, `README.md` 등)을 참조할 때는 `../../../<FILE>` 상대 경로를 사용합니다.
- 동일 분류 폴더 내 문서는 파일명으로 직접 링크합니다 (예: `[DATA_LIFECYCLE_AND_PURGE_DESIGN.md](DATA_LIFECYCLE_AND_PURGE_DESIGN.md)`).
- 다른 분류의 문서를 참조할 때는 `../<폴더>/<FILE>` 경로를 사용합니다 (예: `[EXTERNAL_ACCESS.md](../implementation/EXTERNAL_ACCESS.md)`).
- 업스트림 원본 문서를 참조할 때는 `../../upstream/<FILE>` 경로를 사용합니다 (예: `[ONEHOP_MERGE_DESIGN.md](../../upstream/ONEHOP_MERGE_DESIGN.md)`).
