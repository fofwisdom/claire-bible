# claire_bible — 목표 & 로드맵 (상용급 견고화)

작성일: 2026-06-09 · 기준 문서(개발 시 항상 참조) · 설계 상세는 [PLAN.md](PLAN.md)

---

## 1. 비전 · 사용 대상 (확정)

**"개인 지식베이스를 장난감이 아닌 프로덕션급으로 견고하게."**

- **사용 대상: 단일 사용자(소유자) 전용.** 멀티테넌시·인증 재설계·결제·온보딩은 **범위 밖(out of scope)**.
- 현재 아키텍처(단일 SQLite 정본 + vault export + 텔레그램 allowlist + 로컬 inject API + Docker 3컨테이너)를 **유지한 채 위에서 견고화**한다.
- 작업 원칙: **점진적·모듈화·검증/재현 우선** — 큰 빅뱅 금지. 각 개선을 검증까지 끝낸 뒤 다음으로(refresh 로그 가시성 수정처럼).

### 범위 (Scope)
| 범위 안 (In) | 범위 밖 (Out) |
|---|---|
| 안정성·자동복구·관측성 | 멀티테넌시 / 사용자별 데이터 격리 |
| 추출·엔티티 연결 품질 | 인증/권한 시스템 재설계 (텔레그램 allowlist로 충분) |
| 검색·UX·(개인용) 웹 UI | 결제·구독·온보딩 |
| 백업/복구·CI·운영 위생 | 수평 확장·멀티 인스턴스 |

---

## 2. 우선순위 로드맵 (사용자 확정, 순서대로)

### 트랙 1 — 안정성 · 자동복구 · 관측성 (최우선)
파이프라인이 **사람 손 없이 굴러가고, 문제를 스스로 알려주는 것.** 데이터 무결성이 모든 것의 토대.
- [x] (완료 2026-06-09) Docker 로그 가시성 — `PYTHONUNBUFFERED` + refresh heartbeat
- [x] (완료 2026-06-09) **rate-limit 자동복구 루프** (`recover-loop`) — error inbox 주기 자동 재적재. `claire_recover` 컨테이너(10분 주기) 배포·검증. 설계: [[claire-rate-limit-recovery]]
- [x] (완료 2026-06-09) **`raw_inbox.attempts` / `next_retry_at`** — 지수 백오프, 영구실패(`failed`) 구분, 무한재시도 방지. DB 마이그레이션 체계(`_ensure_column`) 동반.
- [x] (완료 2026-06-09) **능동 알림** — recover-loop 가 영구실패(`failed`) 발생 시 소유자에게 텔레그램 DM 경보. `notify.py`(httpx sendMessage), `CLAIRE_OWNER_CHAT_ID`(미설정 시 allowed_users 폴백).
- [x] (완료 2026-06-09) **백업 전략** — `claire_backup` 컨테이너(매일 1회, 7개 보존). VACUUM INTO 스냅샷 + 스냅샷 열어 row count==live 검증(복원가능성). 원격 실데이터 검증 완료(26docs/113ent/107rel 일치). **복구 런북**(README) 작성 + 로컬 실검증(적재→백업→DB삭제→복원→counts 일치).
- [x] (완료 2026-06-09) **헬스/메트릭** — `health.py`(ok=liveness / degraded=주의신호 분리) + `/health` 강화(503 on not-ok) + CLI `health`(JSON, ssh/모니터링용). 원격 검증.
- [x] (완료 2026-06-09) **circuit breaker** (최소·프로세스-로컬·무상태) — `_call`에서 daily-quota 429(즉시 fail-fast) vs rate 429(재시도) 구분. **분산 상태(DB meta) 금지** — 마이그레이션 race와 동급 위험이라 advisor가 기각. 복구는 recover-loop의 긴 호라이즌이 담당.

> **트랙1 완료(2026-06-09)**: 파이프라인이 사람 손 없이 굴러가고(자동복구·백업·circuit breaker), 문제를 스스로 알리며(능동 알림·로그·health), 데이터 무결성이 보장됨(마이그레이션·검증된 백업). 컨테이너 5개(bot/api/refresh/recover/backup). 테스트 73→94개.

### 트랙 2 — 추출 · 연결 품질 (지식베이스의 본질 가치)
> 검증 주의: mock provider는 프롬프트를 무시하고 judge가 결정론적 → **프롬프트/judge 의존 항목은 mock으로 검증 불가**(실 Gemini 필요). 결정론적 항목만 eval 하니스로 반증 가능. (사용자 결정: 결정론적 먼저, quota 0)
- [x] (완료 2026-06-09) **약어 동의어 수렴** (MCP ↔ Model Context Protocol) — resolver 단계 2.5 결정론적 이니셜 매칭(같은 타입+길이≥3). eval 하니스 4케이스(양방향 merge + 다른타입/2글자 must-not), 회귀검증 완료.
- [x] (완료 2026-06-09) **dedup을 content_hash AND canonical_url** — same canonical_url + 다른 content_hash → in-place 갱신(`IngestReport.updated`, sources 연결 보존). naive skip/중복노드 모두 회피. 회귀검증 완료.
- [x] (완료 2026-06-10) **출처 플랫폼 엔티티화 억제** (GeekNews/PyTorchKR가 Org 노드) — 추출 프롬프트 v2 억제 규칙. **실 Gemini before/after 검증 완료**(크레딧 충전 후): GeekNews·PyTorch Korea가 v2에서 엔티티에서 제거됨, 콘텐츠 엔티티(Claude Code/Anthropic/vLLM/PagedAttention)는 보존.
- [ ] static 경로 boilerplate 제거 — **사용자 보류**(본문 깎을 위험)
- [ ] eval 하니스 확장 (변경 항목마다 라벨 케이스 추가 — 침묵 악화 방지)

### 트랙 3 — 검색 · UX · 웹 UI
- [x] (완료 2026-06-10) **그래프 시각화 웹 UI** — inject API에 `/`·`/graph`·`/node`. 읽기전용·loopback·타입별 색상. **노드 클릭→상세 패널**(전체 observations + 소스 문서 제목·URL·summary + 타입 있는 이웃, 이웃 클릭 네비게이션). **검색→focus/zoom + 매치 선택**(엔터=상세). playwright 실데이터 검증. 브라우저: `ssh -L 8765:127.0.0.1:8765` 터널 후 `localhost:8765`.
- [x] (완료 2026-06-10) **선택 노드 → LLM 종합 지식 문서** — `synthesize`(summarize_search 재사용), `/synthesize` POST(토큰 인증)+UI 버튼. **한국어 답변**(고유명사 원문). 실 Gemini 검증(허브 3개→인용 종합).
- [x] (완료 2026-06-10) **점수 기반 관련 필터** — degree-centrality 슬라이더(`연결 ≥ N`). 113→21 축소 검증. ⚠️**유용성 평가 예정**: degree는 전역 허브용 — "특정 주제 기준 관련성"엔 ego-graph(선택 N홉)/클러스터가 더 맞을 수 있음(사용자와 고민).
- [x] (완료 2026-06-10) **텔레그램 UX** — 진행 메시지(5s 경과 편집) + 완료 시 삭제(후보 있으면 버튼 편집) + 원본 reaction(👍/🤔/👎). PTB 22.7 set_reaction. (실 UX는 텔레그램 확인)
- [x] (완료 2026-06-10) **UI 2차**(사용자 피드백): 색 타입 범례 + hover 2초 미리보기(벗어나면 선택 복귀) + 좌측 문서 타임라인 패널(구간검색 + 문서클릭→그 문서 엔티티 필터, degree와 합성).
- [x] (완료 2026-06-10) **웹 UI 인증**(토큰 prompt → 텔레그램 버튼 승인 → 세션) — `auth_sessions` DB, `/auth/request`(버튼+미승인 시 자동 만료-제거), `/auth/poll`(비인증), `_authed`에 X-Session 추가(bearer 유지). negative path 검증(미승인 401·bearer 200·만료 거부). ⚠️실제 텔레그램 승인 흐름 end-to-end는 사용자 확인 예정.
- [x] (완료 2026-06-10) **의미검색 토글** — "의미" 체크 시 백엔드 하이브리드(/search FTS+벡터, 세션 게이팅) → 결과 노드 강조. discriminating 검증(substring 0인 노드를 임베딩으로 찾음).
- [x] (완료 2026-06-10) **UI 3차 폴리시**(사용자 피드백): 문서필터 dim(숨김 대신 opacity, 맥락보존; degree=hidden과 별도 속성) · hover 2s→1s · **종합 수집(synthSet) inspect와 분리**(Ctrl+클릭/➕버튼·칩·카운트) · 인증 상태 아이콘+카운트다운(비블로킹, /auth/request ttl 반환) · 의미검색 버튼(타이핑 0호출 검증) · 문서 일자별 그룹 · 컨트롤/슬라이더 레이아웃 고정. playwright discriminator 검증.
- [ ] (뒷이야기) 선택 노드 종합 시 추가 자동 웹검색 — 사용자가 defer
- [ ] static 경로 boilerplate (사용자 보류)
- [ ] **degree 필터 유용성 평가 + ego-graph 검토** — 며칠 사용 후(사용자와)
- [x] (완료 2026-06-11, **설계만**) **외부 접속 시나리오**(모바일/외부 PC) — 사용자 결정 "설계안만 문서로". `docs/EXTERNAL_ACCESS.md`: 위협모델(읽기 엔드포인트 무인증=루프백 전제) + 옵션비교(SSH터널/Tailscale/Cloudflare Access/직접노출) + **권장=Tailscale**(공개노출 0, 바인드만 변경, 읽기 게이팅 재설계 불필요). 실제 바인드/노출 변경은 다음 세션 승인 후.
- [x] (완료 2026-06-11) **UI 4차 버그/UX**(graphview) — hover↔selection 분리로 일괄 해결:
  - ⑤④ 검색 강조 유지 + hover 분리: `restoreSelection()`(highlightSet/selectedNodeId 를 vis 선택으로 복원)을 blur·빈캔버스클릭 뒤 적용 → hover/빈클릭으로 검색 선택이 사라지지 않음(**사용자 핵심 이슈4**: "검색된 상황에선 선택 유지").
  - ② ESC = `clearSelections()`(검색+inspect 해제, synthSet 보존). 빈클릭 = inspect만 해제·검색 강조 유지.
  - ④ 입력창 focus → `select()`(전체선택).
  - JS 문법 `node --check` 검증.

### 2026-06-11 (2차) 사용자 피드백 4건
- [x] **모바일 캔버스 상단 일부만 차지**(이슈1) — 두 겹 버그: (a) 좁은화면 media query 의 `#net{height:58vh}` 이 base `#net{flex:1}`(basis 0%)에 무력화돼 min-height 까지 쪼그라듦 → media 에 `flex:none` 추가. (b) vis 가 생성 시점 미해결 높이로 캔버스를 150px 로 잡아 박스 상단 50%만 차지 → `setSize('100%','100%')` 은 flex/auto 체인에서 안 먹어 **getBoundingClientRect 실측 px** 로 강제 + **ResizeObserver**(레이아웃확정·세로스택전환·회전 시 재설정). **Playwright 390px 검증: 캔버스 박스충진 50%→100%(452px)**.
- [x] **이중 요약(critical)**(이슈2) — 노드 클릭 시 한글 요약이 너무 짧음 → 설명(summary) → **디테일(detail)** → 원문링크 구조. **advisor 조언대로 detail 을 구조화 추출에서 분리**: `documents.detail` 별도 컬럼 + provider `render_detail`(별도 Gemini 호출, A4 1~2장 분량 한국어 가독 렌더링). → `reset_graph`/rebuild 없이 비파괴 백필 가능(그래프 불변). `pipeline.ensure_document_detail`(신규적재·백필 공용) · `IngestService.backfill_details` · CLI `backfill-detail`. UI: `📖 자세히 읽기`(접힘) → `↗ 원문 열기`. **Playwright 검증: 설명/자세히/원문 3단 렌더**. **실 Gemini 검증**: MCP 샘플→1302자·4단락 평문(마크다운 잔존 0, 고유명사 원문 유지) — 본문 12000자까지 입력하므로 긴 문서면 비례해 길어짐. **배포 후 `claire backfill-detail` 실행 필요**(기존 문서 detail 채우기, 비파괴).
- [x] **'승인 요청 만료' 스팸**(이슈3) — `/web` 쿠키 인증이 주 메커니즘인데 UI authstate **클릭이 레거시 nonce 플로우(`/auth/request`)를 트리거** → 600s 후 `expire_button` 이 텔레그램에 만료 메시지 발송. 쿠키 인증과 무관한 잔재. UI 의 `ensureSession`/`authClick`/nonce 폴링 **제거**, authstate 정적화(🔒 인증됨). 쿠키 만료(7일)는 synthesize/검색 401 → `setAuth('idle')` 안내로 복구.
- [x] **토큰 7자 입력 통과**(이슈4) — 링크는 길게(12자) 주되 수동 입력은 7자+ 프리픽스로 통과(사용자 의도). `db.resolve_session_prefix`(**진입 게이트 전용**, 알파벳외 문자=LIKE 와일드카드 주입 차단, 단일활성이라 프리픽스=단일식별자). 게이트가 쿠키엔 **전체 토큰** 저장 → `validate_session`(쿠키/헤더)은 여전히 전체 일치(보안 불변). **end-to-end 검증: `/?t=<7자>`→302+전체토큰쿠키, 6자/`%`주입→404**.

**오늘(2차) 테스트 130→140개.** 배포 후 `claire backfill-detail` 로 기존 문서 detail 채우고 실 Gemini 로 detail 분량 확인.

### 2026-06-11 발견 이슈 처리 (사용자 보고 5건)
- [x] **(근본원인) router 가 '제목 + 트레일링 URL' 공유 텍스트에서 URL 추출 못함** — 모바일/데스크톱 '공유'는 「제목 … URL」로 와서 http 로 시작 안 함 → 그동안 **순수 메모(text)로 적재돼 링크 fetch 자체가 안 됨**(실관측: share.google/wikidocs 가 url=None 90자 thin 노드 2개). `router.extract_shared_url`(마지막 토큰이 URL일 때만) + `fetch`/`classify`/`classify_input` 반영. **실 검증**: wikidocs 재적재 → 8172자 본문 + 한글 요약 정상. test_router.py.
- [x] **한글 정리 프롬프트**(이슈1·5b) — `_SYS` 에 summary/observations/key_claims **한국어 작성**(고유명사·기술어 원문 유지) 규칙. PROMPT_VERSION `extract-v2`→`extract-v3`. mock 무관(프롬프트 무시)이라 실 Gemini 검증.
- [x] **1홉 후보 blacklist**(이슈2) — arxiv 푸터의 기관/운영 링크(Cornell University·info.arxiv.org·Donate/Help)가 후보로 떠 무의미. `onehop._is_blocked` = 서브도메인 suffix(`info.arxiv.org`,`cornell.edu`…) + boilerplate 경로 prefix(`/about`,`/help`,`/donate`,`/terms`…). **arxiv.org 본체(/abs 논문)는 보존**. test_expand.py.
- [x] **텔레그램 적재완료 메시지 정리**(이슈3) — reaction(👍)으로 결과를 표시하는데 "적재 완료" 텍스트가 yes/no 후에도 잔존=스팸. `no:`→메시지 **삭제**, `exp:`→같은 메시지 **in-place 편집**(진행→결과, 새 메시지 2개 안 만듦).
- [x] **재추출 인프라 + 한글 전수 재추출**(이슈1/5b 기존 데이터) — `_merge` 가 observations 를 *추가*하므로 단순 재추출은 영문+한글 혼재 → `db.reset_graph`(엔티티/관계/임베딩/추출/FTS 비움, **문서·원본 보존**) + `IngestService.reextract_all(rebuild=True)` + CLI `reextract`(**배포전 백업 강제**). 저장된 raw_text 로 전 문서 한글 재구축. test_reextract.py.
- [x] **라이브 데이터 복구**(사용자 결정 "재적재+기존삭제", "전체 재추출") — ①thin AutoLab 2건 삭제 → wikidocs 재적재(8172자). ②**전체 rebuild 재추출 35문서 성공 35/실패 0**(백업 일치 확인 후). ③blacklist 이전에 적재됐던 잡음 문서 5건(Cornell University/Cornell Tech/Our Members·Donate·Help — info.arxiv.org·cornell.edu) **외과적 제거**(+고아 엔티티 10·관계 10, 공유엔티티 0=부수피해 없음). **최종: 문서 30·엔티티 97·관계 87.**

**검증(실 Gemini + Playwright, 2026-06-11):**
- observations 한글 확인(요약 아님): OpenSkill="LLM 에이전트의 오픈 월드 자가 진화를 위한 프레임워크"… 고유명사(OpenSkill/arXiv/Claude Code/CUDA) 원문 유지.
- **이슈4 Playwright 경험적 검증**(`window.claireDebug` 읽기전용 핸들 추가): 검색→강조9·vis선택9 / **빈캔버스클릭→vis가 선택비움(0)→setTimeout(restoreSelection)이 9 복원**(순서 정상)·inspect만 해제 / hover→선택불변 / blur→강조복원 / ESC→전부 해제.
- 문서목록에서 잡음 5건 사라짐 확인.

**오늘 테스트 120→130개.** 배포(rsync+5컨테이너 재빌드) 후 실 Gemini 로 router·한글·blacklist 동시 검증. **주의: rebuild 는 LLM 병합판정을 전부 재실행 → 노드 연결 구조가 이전과 달라질 수 있음(번역이 아니라 재구성). 영문+한글 혼재 회피의 정당한 대가.**

---

## 3. 하드닝 버킷 (트랙 무관, 발견 시 적재)
- [x] (완료 2026-06-09) `config.py` 기본 모델값 드리프트 → `gemini-3.1-flash-lite` 로 일치
- [x] (완료 2026-06-09) DB 스키마 마이그레이션 체계 → `_ensure_column`/`_migrate`(멱등, race 내성)
- [x] (완료 2026-06-10) CI — 배포 전 게이트(`scripts/ci.sh`: uv lock --check + pytest, `deploy.sh`가 rsync 전 호출). remote 없는 프로젝트라 GitHub Actions 대신 배포 게이트가 실효 CI. 깨진 빌드/lock 누락이 원격에 못 올라감.

---

## 4. 현황 baseline (2026-06-09)
- 코드 ~3,700 LOC, 테스트 73개+ 통과. Docker 3컨테이너(bot/api/refresh) 원격 9일+ 무중단.
- DB 2MB (documents 26 · entities 119 · relations 105). 단일 사용자, allowlist 적용.
- 마일스톤 M0~M6 완료(v1 파이프라인 동작). 복원 메커니즘(refresh_queue) 동작.
- Gemini: 생성 `gemini-3.1-flash-lite`, 임베딩 `gemini-embedding-001`. throttle+429 재시도 있음, 자동복구 루프 없음.
