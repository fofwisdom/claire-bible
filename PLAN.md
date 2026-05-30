# claire_bible — 개발 플랜

작성일: 2026-05-30 · 상태: v1 설계 확정, 구현 착수 직전
관련: [research/RESEARCH_NOTES.md](research/RESEARCH_NOTES.md), [sample.md](sample.md)

---

## 1. 한 줄 정의

텔레그램으로 던진 링크/문서/키워드를 **스크랩 → Gemini로 구조화 → 팔란티어식 타입 온톨로지 그래프로 적재**하고,
**새 자료를 기존에 쌓인 그래프와 잘 연결(internal linking)** 하며, 나중에 키워드로 **검색 → LLM 정리** 해주는 개인 지식베이스.

1차 목표는 **"정보를 효율적으로 계속 쌓아가는 적재 파이프라인"**. 시각화/고급 검색은 후순위.

## 2. 확정된 설계 결정 (사용자 답변 기반)

| 항목 | 결정 |
|---|---|
| 용도 | 기술/AI 트렌드 중심 스크랩북 + **봇이 연결되는 것도 스스로 조사·확장** |
| 정리 방식 | 옵시디언/**팔란티어식 온톨로지** — 타입 있는 객체 + 타입 있는 관계 |
| 관계 관리 | **코드 인터페이스(Pydantic)로 온톨로지 정의** → 시드 타입 + LLM이 새 타입 구조화 제안 → 로그/머지 |
| 저장 | **하이브리드** = 마크다운 vault(+`[[wikilink]]`) + 검색용 임베디드 인덱스(SQLite FTS+벡터) |
| 자동 확장 | **1홉**, 외부 확장은 제안 위주(confirm 후 fetch). **기존 그래프와의 내부 연결은 항상 자동·고품질로** (핵심 가치) |
| LLM/임베딩 | **Gemini** (생성+임베딩). API 키는 추후 제공 → 어댑터로 추상화, 그전까지 mock |
| 입력 범위(v1) | 실용 범위: 일반 웹 + YouTube(자막) + google share redirect 해석 + 순수 텍스트/키워드. (x.com은 부분 처리) |

## 3. 아키텍처 개요

```
Telegram(진입)  →  Ingest 파이프라인  →  Extract(Gemini)  →  Store(하이브리드)
   링크/문서/텍스트     fetch+normalize+dedup    온톨로지 매핑+엔티티해소     SQLite(graph+FTS+vec) + vault(.md)
                                                       │
                                              1홉 자동확장(제안) ──┐
                                                                  ▼
                                         (후순위) Retrieval: hybrid 검색 → Gemini 정리(인용)
```

### 3.1 구성요소

1. **Telegram 진입점** (`telegram_bot.py`)
   - `python-telegram-bot`(async), **long-polling** (로컬 WSL에서 공개 엔드포인트 없이 동작).
   - 입력 수용: URL, 전달 메시지, 파일(pdf/md/txt), 자유 텍스트.
   - 각 입력을 ingest job으로 큐잉 (source ref + raw payload + ts + chat 컨텍스트).
   - 응답: 적재 결과 요약 + (확장 후보 있으면) confirm 버튼.

2. **Fetcher / Scraper 레이어** (`ingest/fetchers/*`) — 소스를 *정규화된 Document* 로
   - URL 라우터:
     - 일반 웹 → **Scrapling** (Stealthy/Dynamic fallback 체인)
     - YouTube → 자막(youtube-transcript) + 제목/설명
     - google share redirect → redirect 해석 후 재라우팅
     - 파일(pdf/md/txt) → 직접 텍스트 추출
     - 자유 텍스트/키워드 → `Note`/시드 엔티티로 직행
     - x.com → v1 부분 처리(oEmbed 제목만, partial 표시)
   - 산출: `Document{url, canonical_url, title, author, published_at, fetched_at, raw_text, source_type, content_hash, lang}`
   - **dedup**: content_hash / canonical_url 로 중복 차단.

3. **Extract & 온톨로지 매핑** (`extract/gemini.py`, Gemini structured output)
   - 입력: 정규화 Document + 현재 온톨로지(엔티티/관계 타입) + **그래프에서 후보로 끌어온 기존 엔티티**(임베딩+키워드 검색) → 추출 시점에 기존 노드로의 연결을 유도.
   - 산출(JSON 스키마 강제): summary, key claims, **typed entities**, **typed relations**(가능하면 *기존 엔티티 id* 로 연결 = 내부 연결).
   - **closed-with-escape-hatch (advisor)**: 추출 시 타입은 **시드 enum에서 강제 선택**, 단 `proposed_new_type`(자유) 필드를 별도로 허용. 모델이 매번 타입명을 자유 생성(`competes_with`/`competitor_of`/`rivals` 난립)하지 못하게 일관성 확보.
   - **엔티티 해소(resolver.py) — M2에 최소 버전 포함(advisor)**: 노드 생성 *전에* (정규화 이름 exact match) OR (임베딩 cosine > 임계) → 기존 id 재사용. M3는 이를 *개선*(recall↑)하는 단계지 처음 도입이 아님. (늦추면 오염된 그래프에 retrofit하게 됨)

4. **온톨로지 (코드 인터페이스)** (`ontology/*`)
   - `base.py`: 단일 `Entity`, 단일 `Relation` Pydantic v2 모델 + **검증되는 `type` 필드**. (advisor: per-type 클래스 생성은 과한 의식 → 레지스트리 방식으로 90% 가치/10% 코드)
     - 공통 필드: id, type, name, aliases, props, observations, embedding_ref, sources, created/updated, **`provisional: bool`**.
   - `types.py`: 시드 타입.
     - Entity: `Tool, Framework, Model, Paper, Article, Repo, Concept, Person, Org, Event, Note`
     - Relation: `implements, alternative_to, competes_with, authored_by, cites, part_of, uses, integrates_with, related_to`
   - `registry.py`: 타입 *정의* 레지스트리 — 각 타입의 (name, **허용 source/target 엔티티 타입 = domain/range**, description). 관계 생성 시 domain/range 검증.
   - **권한 모델(advisor 핵심)**: LLM이 enum에 없는 타입을 제안하면 → **엣지를 제안 타입 그대로 생성하되 `provisional=true` 플래그** + `proposals` 테이블에 기록. 정보 손실 없음. 코드로 승격(types.py 추가) 시엔 *재추출이 아니라 단순 relabel/플래그 해제*. (절대 `related_to`로 뭉개지 않음)

5. **저장(하이브리드)** (`store/*`)
   - **정본(canonical)**: **SQLite** 단일 파일. 테이블: `documents, entities, relations, observations, embeddings, proposals, jobs`.
   - **벡터**: 1순위 `sqlite-vec`, **단 M0에서 10분 스파이크로 검증(advisor)** — 확장 로드+insert+query. WSL2 패키징 리스크. 실패 시 **fallback: 임베딩을 blob 저장 + Python brute-force cosine** (수백~수천 노드 규모엔 충분, 의존성 제거). 벡터스토어가 "쌓기" 목표를 막지 않게.
   - **키워드(BM25)**: SQLite **FTS5** (search.md의 하이브리드 검색 1축).
   - **vault 투영**: 엔티티/문서당 `.md` 1개 — YAML frontmatter(type,id,props) + 본문(summary/observations) + 관계는 `[[wikilink]]` → **옵시디언에서 바로 열림**.
   - **정본/투영 정책 — 엄격 단방향(advisor)**: SQLite 정본, vault는 export-only **read-only-by-convention**. 모든 .md frontmatter에 **`generated: true / do not edit` 배너** → export가 손편집을 덮어쓰는 데이터 손실 방지. (round-trip은 명시적으로 비범위; 후순위로 `## Notes` 단일 섹션 역동기화 검토)

6. **1홉 자동 확장** (`expand/onehop.py`)
   - 적재 후 본문 내 링크/언급된 도구·논문 등을 **후보 stub 노드**로 만들고 fetch 제안.
   - 텔레그램: "관련 항목 N개 발견. 가져올까요?" + 버튼 → confirm 시 fetch+ingest.
   - **내부 연결(기존 그래프와의 관계 부여)은 제안이 아니라 항상 자동 실행** (사용자 강조 가치).
   - 예산/개수 상한(자료당 N개) + content_hash 캐시로 비용 통제.

7. **Retrieval (후순위, v1은 최소 stub)** (`retrieval/query.py`)
   - 키워드/질문 → 하이브리드(FTS BM25 + 벡터) 후보 → 그래프 이웃 확장 → Gemini 정리(**인용 포함**, search.md 5·6단계).
   - v1: 텔레그램 `/search <kw>` 로 상위 엔티티 + 짧은 정리. **웹 UI는 다음 단계**.

## 4. 기술 스택

- 언어: **Python 3.11+**, 패키지: **uv**
- 텔레그램: `python-telegram-bot` (async)
- 스크래핑: `scrapling` (+ youtube-transcript, httpx for redirect)
- LLM/임베딩: `google-genai` (Gemini, 생성/임베딩) — **provider 어댑터로 추상화**, 키 도착 전 mock
- DB: `sqlite3` + `sqlite-vec` + FTS5 (필요 시 `sqlite-utils`)
- 검증/온톨로지: `pydantic` v2, 설정 `pydantic-settings`(.env)
- 테스트: `pytest`

## 5. 프로젝트 구조 (목표)

```
claire_bible/
  pyproject.toml            # uv 프로젝트
  .env.example              # GEMINI_API_KEY, TELEGRAM_BOT_TOKEN
  src/claire/
    config.py
    telegram_bot.py         # 진입점
    ingest/{pipeline.py, normalize.py, fetchers/{web,youtube,file,redirect,text}.py}
    ontology/{base.py, types.py, registry.py}
    extract/{gemini.py, resolver.py, provider.py(mock/gemini)}
    store/{db.py, graph.py, vectors.py, search.py, vault.py}
    expand/onehop.py
    retrieval/query.py
  vault/                    # 옵시디언 호환 .md (gitignore data, vault는 보존)
  data/                     # claire.db 등 (gitignore)
  tests/
  research/                 # 참고 클론(codegraph, graphify, Scrapling) — 레퍼런스
  PLAN.md  sample.md  docs/
```

## 6. 마일스톤

- **M0 스캐폴드 ✅(2026-05-30)** — uv 프로젝트, config, SQLite 스키마, 온톨로지 base+시드+registry(domain/range), provider 어댑터(mock), 텔레그램 echo, sqlite-vec 스파이크(미설치→brute-force 확정). 테스트 통과.
- **M1 인제스트 코어 ✅(2026-05-30)** — fetcher 라우터(web/Scrapling, file/text, youtube자막, share redirect, x.com partial) + normalize(canonical+hash) + dedup. CLI/텔레그램 파이프라인 연결. 주입 fetch 로 단위테스트.
- **M2 추출+온톨로지 매핑 (+ 최소 해소) ✅(2026-05-30)** — Gemini(`gemini-3.1-flash-lite`) structured 추출(closed+escape-hatch) → 엔티티/관계, 최소 resolver(exact/alias/임베딩 임계) 머지, provisional/proposals 기록, SQLite + vault(generated 배너+wikilink). **실 Gemini E2E 검증 완료**(typo 타입이 provisional 로 안전 흡수됨). 테스트 21개 통과.
- **M3 내부 연결 품질 향상 ✅(2026-05-30)** — *(사용자 강조 가치)* advisor 조언 반영: ① **eval 하니스**(tests/test_resolution.py, 라벨된 should-merge/must-not-merge 케이스) 먼저 구축, ② exact/alias **단축으로 임베딩 호출 절약**(miss 일 때만 embed_fn lazy 호출), ③ **LLM judge 가 borderline 머지 최종 판정**(provider.judge_same_entity, CANDIDATE_FLOOR~AUTO_MERGE 게이팅 + MAX_JUDGE 상한). 코사인 임계 튜닝은 지렛대 아님. **실 Gemini E2E: 이름 안 겹치는 MemGPT→Letta 수렴 성공**(M2에선 실패했던 케이스). 테스트 33개 통과. (잔여: Concept 동의어 "LLM agent"/"Autonomous LLM agents" 분리 — 후속 튜닝 여지)
- **M4 1홉 자동확장 ✅(2026-05-30)** — expand/onehop.py: 본문 URL 추출 → 잡음호스트(x/youtube/소셜) 제외 + 자기링크/기적재 dedup + 자료당 상한(expand_max). 텔레그램 **inline 버튼 confirm**(가져오기/아니요) → confirm 시 2홉 방지(expand_max=0)로 fetch. CLI `--expand`/`--no-expand`. 내부 연결은 항상 자동. **실 웹 E2E: GeekNews Scrapling 페이지→9엔티티/6관계, 1홉 후보 github 자동 탐지**. 부수 수정: FTS5 쿼리가 `/ . :` 토큰에서 syntax error 나던 것 → 영숫자/한글 토큰만 추출해 OR 매칭. 테스트 34개 통과.
- **M5 검색+정리 ✅(2026-05-30)** — retrieval/query.py: 하이브리드(FTS BM25 + 벡터 cosine) **RRF 융합** → 그래프 1홉 이웃으로 컨텍스트 보강 → Gemini `summarize_search`(검색 컨텍스트만, [인용] 포함, 환각 억제). CLI `search`(--no-summary), 텔레그램 `/search`. **실 E2E: "agent memory 도구?"(한국어) → fts+vec로 Letta 검색 + 인용 답변 생성.** 부수: 웹 fetcher를 httpx+lxml 정적 1차 경로로 재작성(Scrapling은 browserforge/playwright 등 무거운 의존성 요구 → `stealth` extra로 강등, 에스컬레이션만). 실 GeekNews URL E2E 성공(6엔티티/4관계/vault). 테스트 39개 통과.
- **M6 sample.md 품질 패스 ✅(2026-05-30)** — sample.md 대표 입력 전 종류(github/web/redirect/youtube/xcom/text) 실 E2E 통과. **크로스문서 연결 입증**: "Claude Code"가 3개 자료→1노드(deg=3) 수렴, "knowledge graph"가 codegraph+graphify 공유. 6문서→24엔티티/17관계/24임베딩/vault. 부수 수정: ① youtube-transcript-api 1.x 인스턴스 API(get_transcript 제거됨)로 fetcher 갱신, ② 웹 fetcher가 href를 meta.links로 수집→1홉 후보 실동작(GeekNews→github.com/D4Vinci/Scrapling 탐지). 잔여: x.com partial(설계대로), 저품질 엔티티("code" 등) 후속 튜닝. 테스트 39개 통과.

각 마일스톤은 테스트 + sample.md 일부로 검증하며 advisor 체크 후 다음으로.
**해소 품질은 침묵 속에 악화(advisor)** → eval 하니스 없으면 "연결이 좋은가?"가 반증불가. 그래서 M0/M1에 하니스 골격을 둔다.

## 7. 완료(품질) 기준 — sample.md 기반

- sample.md의 각 줄(주로 URL)이 **타입 있는 엔티티 + 요약**으로 적재되고, 관련 기존 노드와 **연결**됨.
- 중복은 dedup, 실패는 명확히 리포트(부분 처리 표시).
- "기존에 쌓아둔 것과의 관계 연결"이 눈에 보이게 동작(같은 도구/개념이 여러 자료에서 하나의 노드로 수렴, 관계 엣지 생성).

## 8. 리스크 / 메모

- **Gemini 키 대기** → provider 인터페이스 + mock로 M0~M1 선행. 키 도착 시 M2 활성화.
- **x.com** v1 비신뢰 → partial(oEmbed 제목)로 저장·표시.
- **google share** redirect 해석 필요(+가끔 JS).
- **정본 정책**: SQLite 정본 / vault export — 양방향 동기화는 후순위.
- **비용 통제**: content_hash 캐시, 임베딩 배치, 1홉 상한.
- **research/ 클론**(7~12MB×3)은 참고용. 불필요하면 삭제 가능.

## 8d. Rate limit 대응 + 검증 가동 (2026-05-30)
- **Gemini 429 대응**: `gemini_provider._call()` 래퍼 — 전역 throttle(min_interval 4s≈15RPM) + 429/5xx 지수백오프 재시도(retryDelay 파싱, max_retries 5, duck-typed `_is_retryable`). extract 429는 raise→raw_inbox error, embed/judge는 삼킴. inject API는 적재 예외도 200+{error}로 반환(루프 지속). `claire replay-failed`로 error 행 재적재.
- **로컬 inject API**(사용자 요구="DM과 동일 통로 공유 로컬 api", telethon 아님): `ingest/service.py IngestService`를 텔레그램·CLI·API 공유. `api/server.py`(aiohttp) `claire serve-api`, 별도 컨테이너, 127.0.0.1:8765 publish + bearer token. IngestReport JSON 반환.
- **배포**: docker-compose 2서비스(claire_bot/claire_api, restart=unless-stopped, data·vault 볼륨), deploy.sh(self-cd+rsync, data/vault 제외). 재시작·재부팅 대응 확인.
- **검증 가동 중**: scripts/replay_sample.py가 sample.md 24항목을 inject API로 5분 간격 발신(원격 setsid nohup, data/replay.jsonl). 초기화는 백업(data/_backup_*/) 후 진행. 1번째(share.google→web, 7엔티티, err 없음) 확인. ~115분 소요. 성공조건: raw_inbox=전송수, 중복0, 반복 엔티티 단일노드 src≥2 수렴, x.com=partial/share.google=redirect.
  - 주의: 발신 루프는 호스트 python 프로세스라 minipc 재부팅 시 중단(봇/API는 복귀). 1회성 검증이므로 허용.

## 8e. `claire status` CLI (2026-05-30)
- 현황 한눈 명령. 4섹션: [운영](provider/모델/토큰/inject api/벡터/경로), [DB/그래프](documents·entities·relations·embeddings·proposals·raw 보관용량·소스타입·엔티티타입 분포), [진행/inbox](받은 쿼리 status 분포·최근 수신·실패목록), [연결](수렴 노드 src≥2·허브 노드 degree). 집계는 store/db.py 헬퍼(inbox_status_counts/entity_type_counts/source_type_counts/top_connected_entities/most_merged_entities/last_inbox_activity). `claire stats`는 카운트만(유지). 원격 실데이터로 검증, 이미지에 빌트인. 테스트 test_status.py 4개(총 55개 통과).

## 8f. 검증 루프 최종 결과 (2026-05-30 완료)
sample.md 25항목 + smoke 1 = inject API로 5분 간격 발신 완료(`[replay] done`).
- **최종 그래프**: documents 26 · entities 95 · relations 80 · embeddings 95 · raw_inbox 26(전부 done) · extractions 26.
- **소스 분류 전부 정상**: web 18, xcom 5, youtube 1, text 1.
- **성공조건(8d) 충족**: ① raw_inbox 26=발신수 ✓ ② 전부 status=done, **에러 0** ✓ ③ x.com 5건 모두 source_type=xcom + partial ✓ ④ youtube 자막 ✓ ⑤ **수렴 14개 노드** — Claude Code ×6, Anthropic ×4, PyTorchKR/CLAUDE.md/Scrapling/GeekNews/Cursor/Codex/graphify/MCP/Claude Desktop/DeerFlow 2.0/Model Context Protocol/Claude Opus 4.6 ×2 (핵심 가치 "기존 것과 연결" 강하게 작동) ⑥ 깨진 엔티티명 없음.
- **발견된 품질 이슈(개선 후보, 치명적 아님)**:
  1. **GeekNews/PyTorchKR가 Org 엔티티로 수렴** — 출처 플랫폼이 콘텐츠 엔티티화되는 잡음. 추출 프롬프트에 "게시 플랫폼/출처 사이트는 엔티티로 만들지 말 것" 추가로 완화 가능.
  2. **같은 canonical_url 재적재**(graphify smoke+20번째): URL 동일하나 GitHub 동적콘텐츠로 content_hash가 달라 dedup 미적용 → 문서 2건. 단 엔티티 graphify는 src=2로 정상 수렴. 개선: dedup을 content_hash AND/OR canonical_url 둘 다로(스키마에 canonical_url 인덱스 이미 존재).
  3. "Model Context Protocol"과 "MCP"가 별도 노드(약어 동의어 미수렴) — M3 judge 임계/별칭 강화 후보.
- **결론**: v1 파이프라인이 실데이터(sample 전량)에서 순차 적재·크로스문서 연결·다중 입력타입을 에러 없이 처리함을 입증. 위 3개는 후속 튜닝 항목.

## 8g. 스크랩 품질 보정 (2026-05-30)
- **원인 2겹**: ① share.google=JS리다이렉트(httpx follow_redirects 로 안 풀림) ② PyTorchKR=Discourse(JS렌더, 정적 HTML엔 제목만).
- **수정**: redirect.py(canonical/og:url로 JS리다이렉트 타겟 추출) + fetchers/discourse.py(.json API 본문) + web.py fallback 체인(static→discourse→stealth)+thin-guard(MIN_CONTENT 300, 미달시 FetchError) + discourse _strip_html 잡음제거(img/메타/이미지치수·용량/단일포스트 username prefix).
- **검증(실측)**: share.google→Discourse 92자→5949~7204자, 잡음지표 0, 본문 보존(보정 19~104자). 비-Discourse static 회귀 없음. 테스트 70개.
- **남은 항목**: static 경로 boilerplate(GeekNews 등 메뉴/댓글UI가 본문에 섞임, div-only 레이아웃) → readability-lxml 등 본문추출 도입 검토(본문 누락 위험으로 보류). 기존 운영 DB 26문서 중 thin이었던 PyTorchKR 7건 재적재 여부 사용자 결정 대기.

## 8h. 전처리 + 복원 메커니즘 (2026-05-31)
- **전처리**: discourse `_strip_html`이 이미지/메타 노드 제거 + 이미지치수·용량 정규식 + 단일포스트 username prefix 생략. `_trim_boilerplate`가 후반부(>=50%)에 나오는 사이트 푸터 마커("더 읽어보기/이 글은 GPT/회원으로 가입/좋아요…")부터 절단. 검증: Harmonist 6298→6235(꼬리 깨끗), 5개 문서 BOILER=CLEAN.
- **복원 메커니즘(재사용 가능)**:
  - `refresh_queue` 테이블 + db 헬퍼(enqueue/pending/update/thin_documents/update_document_content).
  - 파이프라인 공용화: `extract_resolve_store()` 를 ingest 와 refresh 가 공유.
  - `IngestService.refresh_document()` — 원본 payload 재fetch → content_hash 동일하면 nochange, 다르면 **같은 doc id 로 in-place 갱신**(엔티티 sources 연결 보존) + artifact 재보관 + 재추출/해소.
  - CLI: `refresh-mark`(thin/host 문서 큐 등록) · `refresh-run`(1회 처리) · `refresh-loop`(주기 데몬).
  - **claire_refresh 컨테이너**(restart unless-stopped, 1시간마다 5건): 큐가 비면 무동작, 향후 알고리즘 변경 시 refresh-mark 로 대상 등록만 하면 자동 재적재.
- **실증**: pytorch.kr thin 6건(73~111자) → 2782~8076자로 in-place 복원, THIN_REMAINING=0. **docs 26 불변(중복 없음)**, entities 89→119·relations 75→105(빈약 문서가 본문 확보로 그래프 풍부화). 테스트 73개.

## 8i. TODO — rate limit 자동 회복 루프 (미구현, 2026-05-31 메모)
요청: rate limit 회피 + 발생 시 자동 회복. **있는 것**: throttle(min_interval 4s)+429 재시도(_call), error는 raw_inbox 보존, 수동 `replay-failed`. **없는 것(=할 일)**:
1. error inbox 주기 자동 재적재 데몬(현재 수동만) — refresh-loop처럼 recover-loop 추가.
2. circuit breaker — quota 소진 시 일정시간 ingest 일시정지+자동재개(현재 즉시 재시도만).
3. raw_inbox에 attempts/next_retry_at 추가 → 지수백오프, 영구실패(404)/일시실패(429) 구분, 무한재시도 방지.
4. 회복 실패 누적 시 텔레그램 능동 알림.
구현 패턴: refresh_queue + extract_resolve_store 재사용. 상세 메모리 claire-rate-limit-recovery.

## 9. 다음 행동

1. 이 플랜 사용자 확인(또는 수정 지시).
2. 승인 시 **M0 스캐폴드부터 착수**(키 불필요). 키 도착 전까지 M0~M1을 mock 기반으로 진행하고, 도착 즉시 M2 활성화.
