# Claire Bible — 오리진 목표와 로드맵 (증강 계보)

**계보:** 증강 계보 ([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible)) **기준 문서:** 본 문서는 증강 계보의 최상위 비전, 범위, 로드맵을 정의하는 단일 정본입니다. **관련 문서:** 테제 원본 [`docs/upstream/GOALS.md`](../upstream/GOALS.md) · 공통 계약 [`docs/contracts/`](../contracts/README.md) · 아키텍처 로드맵 [`docs/origin/CLAIRE_ARCHITECTURE_ROADMAP.md`](CLAIRE_ARCHITECTURE_ROADMAP.md)

---

## 1. 비전과 범위 (Scope)

**"원천 지식의 무결성을 보존하고, 실세계 상호작용과 협업을 지원하는 프로덕션급 지식베이스."**

증강 계보는 업스트림(테제 계보)의 핵심 가치인 '원문 보존', '결정론적 검증', '자동 복구' 철학을 온전히 계승하면서, 실세계 운영 환경에서 요구되는 멀티 테마 분리, 엔터프라이즈 표준 인증, 듀얼 포맷 문서 파이프라인, 멀티모달 적재 및 진단 관측성을 체계적으로 확장합니다.

### 범위 정의 (Scope Boundary)

| 범위 안 (In Scope) | 범위 밖 (Out of Scope) |
|---|---|
| **일련번호 기반 멀티 테마(다중 DB 격리)**: 관심사 분리와 오염 방지를 위한 `themes/{seq}/` 물리 격리 (`CLAIRE_MULTI_THEME`) | 범용 B2C 멀티테넌시 및 불특정 다수 셀프 회원가입 |
| **역할 기반 접근 제어(RBAC)**: 지식 관리자(Owner)와 협업자(Collaborator) 간의 권한 분리 | 복잡한 계층형 엔터프라이즈 인사 조직도 연동 |
| **표준 인증 및 토큰 교환**: RFC 8693 OAuth 2.0 Token Exchange, OIDC Discovery, 세션 프리픽스 게이트 | 외부 상용 IAM/IDaaS 서비스에 대한 강제 종속 |
| **듀얼 포맷 지식 표현**: Markdown 및 AsciiDoc(.adoc) 듀얼 포맷 파이프라인, 수식(LaTeX/KaTeX), 표(Table) 구조 보존 | 완전 무제한 임의 바이너리 문서 파싱 |
| **멀티모달 지식 적재**: 일반 웹, X, YouTube, PDF 적응형 추론, 비디오/STT 전사, 프레젠테이션 복합 번들 적재 | 실시간 스트리밍 미디어 중계 서버 |
| **수집기 확장 및 네트워크 보안**: Pre-LLM Content Guard 사전 필터링, 온프레미스 사설망 보안 경계 | 제어되지 않는 외부 무차별 웹 크롤러 |
| **관측성 및 진단**: 프로바이더 텔레메트리 격리, Support Bundle 팩트 엔지니어링 | 민감 운영 데이터 및 자격증명의 외부 원격 전송 |
| **일관된 호스트 오케스트레이션**: `cb-manuscript` 기반 컨테이너 설치, 갱신, 스냅샷 백업·복원 | 무거운 중앙 집중식 쿠버네티스 클러스터 인프라 |

---

## 2. 현재 제공 기능 (Baseline Capabilities)

1. **통합 적재 파이프라인**: 텔레그램 봇, CLI, 로컬 REST API, Web UI, MCP(Model Context Protocol) 도구가 단일 정규화 파이프라인(`svc.ingest`)을 공유합니다.
2. **다양한 소스 수집 및 정규화**:
   - 일반 웹 문서, 리다이렉트 체인, X(트위터) 스레드, YouTube 자막 및 메타데이터 변환.
   - **Pre-LLM Content Guard**: 차단·저품질 HTTP 페이지 사전 감지 및 필터링.
   - **PDF 적응형 추론(Adaptive Effort)**: 문서 복잡도에 따른 추론 예산 자동 조정.
   - **비디오 & 프레젠테이션 복합 번들**: 비디오 STT 전사 및 VMware Explore 발표 슬라이드 동시 적재.
3. **온톨로지 구조화 및 엔티티 해소**:
   - LLM 프로바이더(Gemini, Mock 등)를 통한 엔티티·관계·요약·상세(Detail) 구조화.
   - canonical URL 및 콘텐츠 해시 기반 중복 제거.
   - 약어·동의어 기반 엔티티 해소 및 1홉 확장 연관 문서 자동 수집.
4. **듀얼 포맷 및 지식 렌더링**:
   - Markdown과 AsciiDoc(.adoc) 듀얼 포맷 완벽 지원.
   - 수식(인라인/블록 LaTeX), 콜아웃, 구조화된 테이블 온톨로지 보존.
5. **하이브리드 검색 및 그래프 분석**:
   - SQLite 정본 기반 FTS5 키워드 검색 + 벡터 임베딩 코사인 유사도 검색.
   - 2D 지식 그래프 시각화, 노드 차수(Degree) 필터링, 엔티티 간 최단 관계 경로(BFS) 추적.
   - 다중 노드 종합(Synthesis) 리포트 생성 및 전용 리더 뷰.
6. **멀티 테마 및 거버넌스 (`CLAIRE_MULTI_THEME`)**:
   - 기본 테마(#0) 영구 보존 및 `themes/{seq}/` 독립 DB 디렉터리 라우팅.
   - 테마별 독립 Obsidian Vault 내보내기 및 FTS/벡터 색인 분리.
7. **표준 인증 및 상호운용성**:
   - RFC 8693 OAuth 2.0 Token Exchange 및 OIDC 호환 토큰 교환.
   - OpenAPI 3.1.0 공통 사양 정본([`openapi.yaml`](../contracts/openapi.yaml)) 준수.
8. **신뢰성 및 오퍼레이션**:
   - 지수 백오프 기반 오류 자동 복구 루프(`claire_recover`).
   - `cb-manuscript` 기반 원클릭 배포, 롤백, 데이터 검증.
   - Support Bundle 진단 팩트 엔지니어링 및 텔레메트리 관측성.

---

## 3. 품질 및 거버넌스 원칙

1. **원문과 출처 절대 보존 (Source Preservation)**:
   - LLM이 생성하거나 요약한 결과물이 결코 원문(raw text)과 출처 메타데이터를 대체하거나 파괴할 수 없습니다.
2. **비결정성과 결정론의 엄격한 격리 (Deterministic vs Stochastic Separation)**:
   - 라우팅, 중복 판별, 와이어 프로토콜, 인증, 토큰 교환 등 시스템 코어 로직은 순수 결정론적 단위 테스트로 100% 검증합니다.
   - 모델 추론 결과(추출, 요약 등)는 전용 평가 표본 및 E2E 테스트셋으로 분리하여 관리합니다.
3. **비파괴적 확장 및 스키마 진화 (Non-Destructive Evolution)**:
   - 스키마 확장은 멱등 마이그레이션 체계를 따르며, 업스트림 테제와의 상호 운용성 계약([`docs/contracts/`](../contracts/README.md))을 항상 준수합니다.
4. **최소 권한 및 보안 경계 (Defense in Depth)**:
   - 읽기와 쓰기 권한을 엄격히 분리하고, 네트워크 경계(CORS, Reverse Proxy)와 애플리케이션 인증을 다층으로 보호합니다.
   - 사설망 및 온프레미스 대상 요청은 SSRF 방지 보안 규칙을 엄격히 적용합니다.
5. **비밀값 및 데이터 격리 (Zero Credential Leakage)**:
   - 모든 설정과 자격증명은 환경변수로만 주입하며, 저장소에는 실제 운영 데이터나 개인 식별 정보를 일체 포함하지 않습니다.

---

## 4. 오리진 로드맵 (Origin Roadmaps)

### 트랙 1: 안정성, 복구력, 호스트 오케스트레이션
- [x] 오류 Inbox 및 지수 백오프 기반 rate-limit 자동 복구
- [x] `cb-manuscript` 호스트 오케스트레이션 및 스냅샷 백업·복원 게이트
- [x] VMware Explore 탐색 능동 대기(Active Waiting) 및 복원력 메커니즘
- [ ] 장애 주입(Chaos Engineering) 기반 자동 복구 검증 자동화
- [ ] 데이터 수명주기 관리 및 장기 미참조 임시 데이터 정리(Purge) 고도화

### 트랙 2: 추출, 온톨로지, 멀티모달 수집 품질
- [x] Pre-LLM Content Guard를 통한 웹 스크래핑 저품질 사전 필터링
- [x] 복합 미디어 번들 수집 (비디오 STT 자막 + PDF 프레젠테이션)
- [x] PDF 추출 예산 및 적응형 추론(Adaptive Effort)
- [x] 사설망 온프레미스 수집기 연동 및 보안 경계 격리
- [ ] 다국어 형태소 분석 및 광역 선호 언어(Preferred Languages) 파이프라인 실장
- [ ] 다중 LLM 프로바이더 동적 캘리브레이션 및 라우팅

### 트랙 3: 지식 소비, 포맷 고도화, UI/UX
- [x] Markdown 및 AsciiDoc(.adoc) 듀얼 포맷 렌더링 파이프라인
- [x] 수식(LaTeX), 구조화 테이블, 콜아웃 렌더링 품질 고도화
- [x] 엔티티 간 최단 관계 경로(BFS) 시각화 및 노드 상세 검사 패널
- [x] 우측 메뉴 컴팩트화 및 반응형 웹 레이아웃 개선
- [ ] 그래프 뷰 모듈화 및 정적 자산 분리 배포

### 트랙 4: 멀티 테마, 거버넌스, 상호운용성
- [x] 일련번호 기반 멀티 테마(`themes/{seq}/`) 다중 DB 물리 격리 코어
- [x] 테마별 Web UI 셀렉터, 텔레그램 인라인 지원, REST API 라우팅
- [x] RFC 8693 OAuth 2.0 Token Exchange 및 OIDC 호환 사양 표준화
- [x] Support Bundle 팩트 엔지니어링 및 진단 관측성 파이프라인
- [ ] 멀티 테마 소유자(Owner) / 협업자(Collaborator) 세부 RBAC UI 완료
- [ ] 계보 간(Thesis vs Augmentation) 지식 전송 및 테마 간 문서 마이그레이션 CLI

---

## 5. 공개 저장소 데이터 및 보안 정책

- `sample.adoc` 및 `sample.md`에는 공개 문서와 합성 메모만 사용합니다.
- 실제 운영 데이터베이스, Obsidian Vault, 백업 아카이브, 세션 토큰은 버전 관리에서 영구 제외합니다.
- 모든 문서의 IP, 도메인, 포트, 토큰은 예시값 또는 환경변수 플레이스홀더로만 표기합니다.
- 이슈, 문서, 테스트 fixture에 실제 사용자의 프라이빗 데이터나 운영 DB 식별자를 커밋하지 않습니다.
