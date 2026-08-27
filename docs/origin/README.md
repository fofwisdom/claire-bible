# Origin Documentation Directory Guide (`docs/origin/`)

Claire Bible 오리진 저장소([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible))의 문서는 **설계 내역(`design/`)**과 **구현/운영 내역(`implementation/`)**으로 엄격히 구분하여 보관합니다.

---

## 디렉터리 구조

```text
docs/origin/
├── README.md                   # 오리진 문서 보관 규칙 및 구조 안내 (본 문서)
├── FAVICON.md                  # 파비콘 3D 기하학 그래픽 디자인 및 생성 명세
├── design/                     # [설계 내역] 아키텍처, 기능 설계, 시스템 설계, 기술 리서치
│   ├── DATA_LIFECYCLE_AND_PURGE_DESIGN.md # 데이터 수명주기 및 정리(Purge) 설계
│   ├── DUAL_FORMAT_ADOC_DESIGN.md        # AsciiDoc 및 듀얼 포맷 본문 파이프라인 설계
│   ├── EXPAND_FILTERING_DESIGN.md        # 1홉 확장의 깊이 및 연관성 필터링 설계
│   ├── MCP_SUPPORT.md                    # MCP 지원 아키텍처 및 툴 명세 (오리진 개정본)
│   ├── MULTI_PROVIDER_DESIGN.md          # 멀티 LLM 프로바이더 및 캘리브레이션 설계
│   ├── ONEHOP_MERGE_DESIGN.md            # 1홉 확장 중복 완화 설계
│   ├── OPERATIONAL_MIGRATION.md          # 운영 지원 업데이트 및 환경변수/DB 마이그레이션 설계
│   ├── RIGHT_MENU_COMPACT_DESIGN.md      # 우측 메뉴 컴팩트화 및 반응형 UI 설계
│   ├── SYNTHESIS_REDESIGN.md             # 다중 노드 종합 재설계
│   ├── TABLE_INGESTION_DESIGN.md         # 원문 테이블 적재 및 본문 글자 수 제한 제외 설계
│   ├── codegraph.md                      # 코드 그래프 리서치
│   ├── files.md                          # 마크다운 파서 리서치
│   ├── graphify.md                       # 지식 그래프 시각화 리서치
│   ├── scrapling.md                      # 웹 스크래핑 라이브러리 리서치
│   ├── search.jpg                        # RAG 검색 아키텍처 다이어그램
│   └── search.md                         # 대규모 RAG 파이프라인 설계 원칙
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
- **대상**: 구현 전/후의 아키텍처 설계, 기능 기획/재설계 문서, 알고리즘 및 파이프라인 설계, 시스템 설계 아티클, 기술 조사/리서치 자료.
- **예시**:
  - `ONEHOP_MERGE_DESIGN.md`: 1홉 확장 중복 완화 설계
  - `SYNTHESIS_REDESIGN.md`: 다중 노드 종합 재설계
  - `MULTI_PROVIDER_DESIGN.md`: 멀티 LLM 프로바이더 설계
  - `search.md`: 대규모 RAG 파이프라인 설계 및 검색 원칙

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
- 동일 분류 폴더 내 문서는 파일명으로 직접 링크합니다 (예: `[SYNTHESIS_REDESIGN.md](SYNTHESIS_REDESIGN.md)`).
- 다른 분류의 문서를 참조할 때는 `../<폴더>/<FILE>` 경로를 사용합니다 (예: `[EXTERNAL_ACCESS.md](../implementation/EXTERNAL_ACCESS.md)`).
- 업스트림 원본 문서를 참조할 때는 `../../upstream/<FILE>` 경로를 사용합니다.
