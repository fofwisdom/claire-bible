# Claire Bible — 오리진 목표와 로드맵 (증강 계보)

**계보:** 증강 계보 ([`fofwisdom/claire-bible`](https://github.com/fofwisdom/claire-bible)) **기준 문서:** 본 문서는 증강 계보의 최상위 비전, 범위, 로드맵을 정의하는 단일 정본이다. **관련 문서:** 프로젝트 원칙 [`docs/PRINCIPLES.md`](PRINCIPLES.md) · 아키텍처 로드맵 [`docs/CLAIRE_ARCHITECTURE_ROADMAP.md`](CLAIRE_ARCHITECTURE_ROADMAP.md) · 공개 아키텍처 [`docs/implementation/PLAN.md`](implementation/PLAN.md) · 구현 및 API 가이드 [`docs/implementation/API_AND_MCP_GUIDE.md`](implementation/API_AND_MCP_GUIDE.md)

> **[결정]** 테제 계보는 더 이상 없다(소유자, 2026-11-02). 이 저장소가 유일한 정본이다. 아래의 "증강 계보", "업스트림" 표현은 출발점을 설명하는 이력이다.

---

## 1. 비전과 범위 (Scope)

**"원천 지식의 무결성을 보존하고, 실세계 상호작용과 협업을 지원하는 프로덕션급 지식베이스."**

증강 계보는 업스트림(테제 계보)의 핵심 가치인 '원문 보존', '결정론적 검증', '자동 복구' 철학을 온전히 계승하면서, 실세계 운영 환경에서 요구되는 멀티 테마 분리, 엔터프라이즈 표준 인증, 듀얼 포맷 문서 파이프라인, 멀티모달 적재 및 진단 관측성을 체계적으로 확장한다.

### 범위 정의 (Scope Boundary)

| 범위 안 (In Scope) | 범위 밖 (Out of Scope) |
|---|---|
| **멀티 테마(단일 DB 파티셔닝)**: 관심사 분리와 오염 방지를 위한 `theme_id` 파티셔닝 (`CLAIRE_MULTI_THEME`) | 범용 B2C 멀티테넌시 및 불특정 다수 셀프 회원가입 |
| **로컬 계정과 네트워크 모드**: 불변 USERNAME, 초대제 가입, 계정 네임스페이스(`/@USERNAME`), 외부 IdP(OIDC·GitHub) 로그인 | 계정별 서브도메인·커스텀 도메인, SCIM·SAML 직접 지원 |
| **신디케이션과 연합**: 연결 계정을 통한 ActivityPub·AT 클라이언트, Fedify 사이드카를 통한 공개 프로필 계정의 ActivityPub 액터(FP1 탐색·FP2 팔로워 구현, 발행은 미구현) | Mastodon 호환 클라이언트 API 서버, 테마 액터, 계정 이전, 릴레이 |
| **역할 기반 접근 제어(RBAC)**: 지식 관리자(Owner)와 협업자(Collaborator) 간의 권한 분리 | 복잡한 계층형 엔터프라이즈 인사 조직도 연동 |
| **표준 인증 및 토큰 교환**: RFC 8693 OAuth 2.0 Token Exchange, OIDC Discovery, 세션 프리픽스 게이트 | 외부 상용 IAM/IDaaS 서비스에 대한 강제 종속 |
| **듀얼 포맷 지식 표현**: Markdown 및 AsciiDoc(.adoc) 듀얼 포맷 파이프라인, 수식(LaTeX/KaTeX), 표(Table) 구조 보존 | 완전 무제한 임의 바이너리 문서 파싱 |
| **멀티모달 지식 적재**: 일반 웹, X, YouTube, PDF 적응형 추론, 비디오/STT 전사, 프레젠테이션 복합 번들 적재 | 실시간 스트리밍 미디어 중계 서버 |
| **수집기 확장 및 네트워크 보안**: Pre-LLM Content Guard 사전 필터링, 온프레미스 사설망 보안 경계 | 제어되지 않는 외부 무차별 웹 크롤러 |
| **관측성 및 진단**: 프로바이더 텔레메트리 격리, Support Bundle 팩트 엔지니어링 | 민감 운영 데이터 및 자격증명의 외부 원격 전송 |
| **일관된 호스트 오케스트레이션**: `cb-manuscript` 기반 컨테이너 설치, 갱신, 스냅샷 백업·복원 | 무거운 중앙 집중식 쿠버네티스 클러스터 인프라 |

---

## 2. 현재 제공 기능 (Baseline Capabilities)

1. **통합 적재 파이프라인**: 텔레그램 봇, CLI, 로컬 REST API, Web UI, MCP(Model Context Protocol) 도구가 단일 정규화 파이프라인(`svc.ingest`)을 공유한다.
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
   - 기본 테마 영구 보존 및 단일 DB(`data/claire.db`)의 `theme_id` 파티셔닝. 구현 정본은 [`THEMES.md`](implementation/THEMES.md)다.
   - 테마별 독립 Obsidian Vault 내보내기 및 FTS/벡터 색인 분리.
7. **표준 인증 및 상호운용성**:
   - RFC 8693 OAuth 2.0 Token Exchange(계정에 귀속된 세션 토큰만 교환, [`TOKEN_EXCHANGE.md`](implementation/TOKEN_EXCHANGE.md)).
   - 로컬 계정, 초대제 가입, 싱글·네트워크 인스턴스 모드, 외부 IdP(OIDC·GitHub) 로그인. 구현 정본은 [`ACCOUNTS.md`](implementation/ACCOUNTS.md)다.
   - OpenAPI 3.1.0 공통 사양 정본([`openapi.yaml`](implementation/openapi.yaml)) 준수.
8. **신뢰성 및 오퍼레이션**:
   - 지수 백오프 기반 오류 자동 복구 루프(`claire_recover`).
   - `cb-manuscript` 기반 원클릭 배포, 롤백, 데이터 검증.
   - Support Bundle 진단 팩트 엔지니어링 및 텔레메트리 관측성.

---

## 3. 품질 및 거버넌스 원칙

[`PRINCIPLES.md`](PRINCIPLES.md)로 옮겼다. 원칙은 그 문서만 고친다.

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
- [x] 텔레그램 단일 메시지 복합 적재 (첨부 파일 + 하이퍼링크 Fail-Atomic 무결성 보장)
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
- [x] 일련번호 기반 멀티 테마(`themes/{seq}/`) 다중 DB 물리 격리 코어 (스키마 v14에서 단일 DB `theme_id` 파티셔닝으로 전환)
- [x] 테마별 Web UI 셀렉터, 텔레그램 인라인 지원, REST API 라우팅
- [x] RFC 8693 OAuth 2.0 Token Exchange 및 OIDC 호환 사양 표준화
- [x] Support Bundle 팩트 엔지니어링 및 진단 관측성 파이프라인
- [ ] 멀티 테마 소유자(Owner) / 협업자(Collaborator) 세부 RBAC UI 완료
- [ ] 테마 간 문서 마이그레이션 CLI
- [x] 로컬 계정 코어, 단일 FQDN URI 네임스페이스, 네트워크 모드, 초대, 계정 API 키(BYOK), 외부 IdP 로그인, Syndication 병합 기반 ([설계](design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) P1–P6)
- [x] `/admin/*`·`/settings/*` 통합 콘솔 경로 분리
- [x] 계정·IdP·Fedify 연동 준비 봉인 (`profile_public` 계약, 연합·인증 예약 이름, 토큰 교환 계정 귀속, Syndication 프로토타입 봉인)
- [x] Fedify 사이드카 연합 FP0: 기술 검증 FT1–FT7 (검증 프로그램은 커밋 `116d530`의 `sidecar/fedify/spike/`에 있으며, FP1-5에서 사이드카 소스로 대체됨)
- [x] Fedify 사이드카 연합 FP1: 내부 계약과 탐색 — WebFinger, NodeInfo, 액터 문서, 준비 검사 ([구현](implementation/FEDERATION.md))
- [x] Fedify 사이드카 연합 FP2: 팔로워 — 인박스, `Accept(Follow)`, 팔로워 컬렉션, 도메인 차단, 콘솔 `/settings/federation`·`/admin/federation` ([구현](implementation/FEDERATION.md))
- [x] Fedify 사이드카 연합 FP3: 발행 — `Article` 발행·갱신·철회, 아웃박스 컬렉션, 수명 주기 작업, 비공개 전환 트리거, `/syndication` 대화상자의 네이티브 액터 대상 ([구현](implementation/FEDERATION.md))
- [ ] Fedify 사이드카 연합 FP4: 원격 읽기 — 설계 개정 필요 ([설계](design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) §14.1)

---

## 5. 공개 저장소 데이터 및 보안 정책

[`PRINCIPLES.md`](PRINCIPLES.md) B4로 옮겼다.
