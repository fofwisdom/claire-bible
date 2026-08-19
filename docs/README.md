# Documentation Directory Guide (`docs/`)

Claire Bible 프로젝트의 문서는 **설계 내역(`design/`)**과 **구현/운영 내역(`implementation/`)**으로 엄격히 구분하여 보관합니다.

---

## 디렉터리 구조

```text
docs/
├── README.md               # 문서 보관 규칙 및 구조 안내 (본 문서)
├── design/                 # [설계 내역] 아키텍처, 기능 설계, 시스템 설계, 기술 리서치
│   ├── OPERATIONAL_MIGRATION.md # 운영 지원 업데이트 및 환경변수/DB 마이그레이션 설계
│   ├── MULTI_PROVIDER_DESIGN.md # 멀티 프로바이더 및 하이퍼스케일러 캘리브레이션 아키텍처
│   ├── EXPAND_FILTERING_DESIGN.md # 1홉 확장의 깊이 및 연관성 필터링 설계
│   ├── ONEHOP_MERGE_DESIGN.md
│   ├── SYNTHESIS_REDESIGN.md
│   ├── search.md
│   ├── search.jpg
│   └── ... (기타 설계 초안 및 기술 참고 자료)
├── implementation/         # [구현/운영 내역] 운영 가이드, 네트워크/인증 명세, 배포 설정
│   ├── EXTERNAL_ACCESS.md  # 웹 접속, reverse proxy, 포트, 인증/CORS 경계 명세
│   └── OPERATIONS.md       # cb-manuscript 호스트 운영 명령, 서비스 수명주기 가이드
└── screenshots/            # README 및 UI 설명용 스크린샷 이미지 자산
```

---

## 문서 작성 및 저장 규칙

문서를 추가하거나 수정할 때는 반드시 다음 기준에 따라 저장 위치를 결정해야 합니다.

### 1. `docs/design/` (설계 내역)
- **대상**: 구현 전/후의 아키텍처 설계, 기능 기획/재설계 문서, 알고리즘 및 파이프라인 설계, 시스템 설계 아티클, 기술 조사/리서치 자료.
- **예시**:
  - `ONEHOP_MERGE_DESIGN.md`: 1홉 확장 중복 완화 설계 초안
  - `SYNTHESIS_REDESIGN.md`: 다중 노드 종합 재설계
  - `search.md`: 대규모 RAG 파이프라인 설계 및 검색 원칙

### 2. `docs/implementation/` (구현 및 운영 내역)
- **대상**: 현재 시스템에 실제 구현 및 배포된 기능의 운영 가이드, 호스트 명령 명세, 네트워크 및 프록시 설정, 환경변수 및 보안 경계 가이드.
- **예시**:
  - `EXTERNAL_ACCESS.md`: Reverse proxy 연동, 포트 바인딩, CORS 및 인증 명세
  - `OPERATIONS.md`: `cb-manuscript` 호스트 운영 명령 및 컨테이너 관리 가이드

### 3. `docs/screenshots/` (공통 에셋)
- README 및 가이드 문서에서 참조하는 UI/그래프 캡처 이미지.

---

## 상대 경로 및 링크 규칙
- `docs/design/` 또는 `docs/implementation/` 내 문서에서 프로젝트 루트 파일(`GOALS.md`, `PLAN.md`, `README.md` 등)을 참조할 때는 `../../<FILE>` 상대 경로를 사용합니다.
- 동일 분류 폴더 내 문서는 파일명으로 직접 링크합니다 (예: `[SYNTHESIS_REDESIGN.md](SYNTHESIS_REDESIGN.md)`).
- 다른 분류의 문서를 참조할 때는 `../<폴더>/<FILE>` 경로를 사용합니다 (예: `[EXTERNAL_ACCESS.md](../implementation/EXTERNAL_ACCESS.md)`).
