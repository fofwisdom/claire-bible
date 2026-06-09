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
- [ ] (뒷이야기) 선택 노드 종합 시 추가 자동 웹검색 — 사용자가 defer
- [ ] static 경로 boilerplate (사용자 보류) · 검색 품질/필터 고도화

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
