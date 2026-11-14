# Common HTTP API & Wire Security Contract

이 문서는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 공유하는 HTTP REST/ASGI 와이어 프로토콜, 인증 채널 및 보안 경계의 최소 공통 계약을 정의한다. 외부 클라이언트(Web UI, CLI, Telegram 봇, MCP 에이전트)가 구현 계보에 무관하게 안전하고 일관되게 통신하기 위한 프로토콜 규약이다.

- **[결정] 테제 계보는 더 이상 없다** (소유자, 2026-11-02). 이 문서는 이 저장소의 HTTP 계약 정본이다. 아래에 남은 "계보", "최소 공통" 표현은 작성 당시의 이력이며, 다른 계보의 승인을 기다리는 항목은 없다.

## 1. 네트워크 및 와이어 보안 불변식

1. **Host Authority 검증**: 애플리케이션은 `X-Forwarded-Host`, `X-Forwarded-Proto` 등 대리 헤더를 맹신하지 않는다. 요청의 `Host` 헤더는 사전에 구성된 공개 서비스 URL(`CLAIRE_PUBLIC_URL`)의 authority와 엄격히 일치해야 하며, 불일치 시 HTTP `421 Misdirected Request`로 거부한다.
2. **요청 본문 크기 제한 (Body Limit)**: 모든 요청 본문은 최대 1MB(`1,048,576` bytes)로 제한된다. 초과 요청은 즉시 HTTP `413 Request Entity Too Large`로 차단한다.
3. **콘텐츠 협상 (Content Negotiation)**:
   - 일반 API 요청 본문은 반드시 `application/json` 또는 `+json` 형식이어야 한다.
   - 토큰 교환([`TOKEN_EXCHANGE.md`](TOKEN_EXCHANGE.md))은 `application/x-www-form-urlencoded`를 사용한다.
   - 진단 아카이브 다운로드는 `application/zstd`를 사용한다.
4. **보안 응답 헤더**: 모든 응답은 `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`를 기본 포함한다.

## 2. 인증 채널 및 상호 배타성 규약

인증은 다음 3가지 채널 중 정확히 하나만 사용해야 한다.

- **Bearer 토큰**: `Authorization: Bearer <token>`
- **세션 헤더**: `X-Session: <token>`
- **세션 쿠키**: `Cookie: claire_session=<token>` (Same-Origin 전용)

### 2.1 자격증명 다중 제공 차단 불변식
하나의 요청에 둘 이상의 인증 채널이 동시에 제공된 경우(예: Bearer 헤더와 Cookie가 동시 존재), 자격증명 혼선 및 주입 공격 방지를 위해 즉시 거부한다:
- Same-Origin 또는 비-CORS 요청: HTTP `404 Not Found` (스텔스 거부)
- Cross-Origin 요청: HTTP `403 Forbidden`
- `/mcp` 엔드포인트: HTTP `401 Unauthorized` (`WWW-Authenticate: Bearer`)

### 2.2 금지 헤더 (`X-Token`)
레거시 또는 비표준 헤더인 `X-Token`은 사용을 금지하며, 발견 시 즉시 거부한다.

### 2.3 Cross-Origin (CORS) 요청 규칙
1. Cross-Origin 요청은 오직 `Authorization: Bearer <token>` 방식만 허용한다.
2. Cross-Origin 요청에 포함된 `Cookie` 또는 `X-Session`은 보안상 무조건 거부(`403 Forbidden`)된다.

## 3. 인가 스코프 계층 및 스텔스(Fail-Closed) 정책

인가 스코프는 다음 계층 구조를 갖는다.

$$\text{owner} \succ \text{readonly} \succ \text{anonymous} \succ \text{unauthenticated}$$

| 스코프 | 권한 범위 | 설명 |
|---|---|---|
| `owner` | 모든 읽기/쓰기, 테마 관리, 지식 적재, 번들 생성, 토큰 관리 | 독립 상시 토큰(`data/auth/api_tokens.json`), 마스터 인젝트 토큰(레거시), 또는 승인된 owner 세션 |
| `readonly` | 모든 공개/비공개 지식 조회, 검색, MCP 툴 호출 | 독립 상시 readonly 토큰(`data/auth/api_tokens.json`), 읽기 전용 토큰(레거시), 또는 readonly 세션 |
| `anonymous` | 공개 지식 조회 및 기본 FTS 검색 (숨김 문서 제외) | 자격증명이 없으나 `CLAIRE_ANONYMOUS_READONLY=1`인 경우 |

### 3.4 스텔스 모드 불변식
`CLAIRE_ANONYMOUS_READONLY=0`(프라이빗/스텔스 모드)로 구동되는 인스턴스는 자격증명이 없는 읽기 요청에 대해 `401 Unauthorized` 대신 HTTP `404 Not Found`를 반환해야 한다. 이는 외부 정찰에 대해 엔드포인트의 존재 여부와 파라미터 구조를 일절 노출하지 않기 위함이다.

## 4. 공통 에러 페이로드 규약

1. **표준 JSON 에러**: 모든 비정상 상태(4xx, 5xx)의 JSON 응답은 최소 `{"error": "<detail_string>"}` 형태를 보장한다.
2. **MCP 및 RFC 6750 에러**: `/mcp` 엔드포인트는 토큰 무효 시 HTTP `401 Unauthorized`와 함께 `WWW-Authenticate: Bearer error="invalid_token", error_description="..."` 헤더를 반환한다.
3. **내부 서버 오류 은폐**: 500 오류 시 데이터베이스 경로나 내부 스택 트레이스를 반환하지 않고 `{"error": "internal server error"}`로 정제하여 반환한다.

## 5. 최소 공통 엔드포인트 목록

양 계보 구현이 상호 호환성을 위해 제공하는 최소 공통 엔드포인트 집합은 다음과 같다.

| Method | Path | 최소 접근 수준 | 설명 |
|---|---|---|---|
| `GET` | `/health` | `public` | 서비스 liveness 및 DB 연결 검사 (`{"ok": true}`) |
| `GET` | `/whoami` | `read` | 현재 요청의 인증 스코프 확인 (`{"scope": "...", "instance": {"mode": "single\|network"}, "relation": "owner\|delegate\|visitor", "is_admin": bool}`. USERNAME은 싣지 않는다) |
| `GET` | `/stats` | `read` | 문서, 엔티티, 릴레이션 카운트 집계 |
| `POST` | `/oauth/token` | `public` | [RFC 8693] 토큰 교환 및 Bearer 토큰 발급. 토큰 교환은 계정에 귀속된 세션 토큰만 받는다([`TOKEN_EXCHANGE.md`](TOKEN_EXCHANGE.md) §7) |
| `POST` | `/search` | `read` | 하이브리드 / FTS 키워드 지식 검색 |
| `GET` | `/documents` | `read` | 아카이브된 문서 목록 조회. 정렬은 `fetched_at` 내림차순, 같으면 `id` 내림차순. 파라미터가 없으면 기존과 같이 최신 300건을 돌려주고, 응답에 `next_before`(다음 페이지 커서, 마지막 페이지면 `null`)와 `total`(커서를 뺀 필터 일치 건수)만 추가된다. 선택 파라미터: `limit`(1–300, 기본 300), `before=<fetched_at>:<id>`(이전 응답의 `next_before`), `pinned=0\|1`, `hidden=0\|1`, `q`(제목 또는 최신 추출 요약의 부분 일치, 200자 이하), `detail=missing\|mismatched`, `id`(반복 가능, 100개 이하). 잘못된 값은 `400`. `anonymous` 범위는 모든 페이지와 모든 필터에서 숨김 문서를 받지 않는다 |
| `GET` | `/document` | `read` | 특정 문서 상세 및 본문 조회 |
| `GET` | `/document/export` | `read` | 문서 하나를 파일로 내보낸다. `id`(필수), `format=markdown`(유일한 값, 기본값). AsciiDoc 본문을 Markdown으로 바꾸고 YAML 머리말(title, source, date, summary, claire_document_id)을 붙여 `text/markdown; charset=utf-8` 첨부 파일로 준다. 읽기 전용. 다른 `format`과 `id` 누락은 `400`, 없는 문서는 `404`. Claire가 Markdown을 쓰는 유일한 곳이다(RD24, [MARKDOWN_RETIREMENT_DESIGN.md](../design/MARKDOWN_RETIREMENT_DESIGN.md) §5) |
| `GET` | `/graph` | `read` | 전체 온톨로지 지식 그래프 데이터 (JSON). `nodes[]`의 각 항목은 `id`, `label`, `group`, `sources`, `obs`, `degree`, `created_at` (`entities.created_at`, 에포크 초)를 가진다 |
| `GET` | `/node` | `read` | 특정 엔티티 노드 세부정보 및 연결선. 선택 파라미터 `docs=brief`: 각 출처 문서를 `id`, `title`, `url`, `summary`, `fetched_at`, `has_detail`, `source_type`, `has_raw_file` 만으로 줄인다(`detail`, `detail_format`, `detail_html` 제외). 파라미터가 없으면 기본 응답은 변하지 않는다 |
| `POST` | `/ingest` | `owner` | URL/텍스트 지식 적재 및 온톨로지 추출 |
| `POST` | `/support/bundle` | `owner` | 진단 및 지원 번들 생성 |
| `GET` | `/support/bundle` | `public` | 유효 토큰 기반 진단 번들 zstd 아카이브 다운로드 |
| `GET` | `/tokens` | `owner` | 발급된 API 및 MCP 상시 토큰 목록 조회 (마스킹) |
| `POST` | `/tokens` | `owner` | 신규 API 및 MCP 상시 토큰 생성 |
| `POST` | `/tokens/revoke` | `owner` | API 및 MCP 상시 토큰 즉시 폐기 |
| `DELETE` | `/tokens` | `owner` | API 및 MCP 상시 토큰 즉시 폐기 |
| `POST` | `/mcp` | `read` | Model Context Protocol JSON-RPC 통신 |

구현별 UI 페이지(`/`, `/syndication`, `/admin/*`, `/settings/*`, `/reference`, `/p`) 및 부가 기능은 각 계보의 확장 라우트로 관리하되 위 최소 공통 엔드포인트의 와이어 형식을 훼손하지 않는다.

## 6. 계정 경로 (증강 계보 확장, P1)

확인 기준: P1 계정 코어 커밋(`d5e8fb4` 다음) (2026-11-02). 아래는 증강 계보의 확장 라우트이며 §5의 최소 공통 엔드포인트가 아니다. 동작의 정본은 [ACCOUNTS.md](ACCOUNTS.md), 접근 규칙의 정본은 `src/claire/api/security.py`의 `ROUTE_POLICY`다. 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다.

- **[코드]** 모든 요청에는 요청 주체(`Principal`)가 하나 정해진다(`src/claire/api/principal.py`). `public` 규칙 라우트는 자격증명을 검증하지 않고 익명 주체를 둔다.
- **[코드]** `owner`·`admin` 규칙을 통과하지 못하면 §3.4와 같이 404다. `admin` 규칙은 owner 범위이면서 `instance_role=admin`인 자격증명만 통과한다. 쿠키 채널의 `POST`는 `Origin`이 같은 origin이어야 하고, 아니면 403이다.

| Method | Path | 규칙 | 성공 | 그 밖의 응답 |
|---|---|---|---|---|
| `GET` | `/setup` | `owner` (+ 관리자·설정 전·세션 채널) | 200 HTML | 조건 밖 404 |
| `POST` | `/setup` | 위와 같음 | 303 `/` + 새 세션 쿠키. 비밀번호 저장만 실패하면 303 `/settings/account?notice=password_not_saved` | 400 입력 거부(HTML), 404 조건 밖·동시 확정 패배, 403 쿠키 POST의 origin 불일치 |
| `GET` | `/login` | `public` | 200 HTML | — |
| `POST` | `/login` | `public` (+ 핸들러의 동일 origin 검사) | 303 `/` + 새 세션 쿠키 | 400 양식 오류, 401 실패(사유 구분 없음), 403 동일 origin 아님, 429 + `Retry-After` |
| `POST` | `/logout` | `public` (+ 동일 origin) | 303 `/login`, 쿠키 삭제 | 403 동일 origin 아님 |
| `POST` | `/api/locale` | `public` (+ 동일 origin) | 200 `{"locale", "effective_locale"}`, 쿠키 `claire_locale`을 쓰거나 지움. owner 세션이면 `accounts.locale`도 저장. 양식 본문(`locale`, `next`=`/login`·`/setup`)이면 303 `<next>` | 400 `invalid_request`, 400 `invalid_locale`, 403 동일 origin 아님 |
| `POST` | `/api/auth/reauth` | `owner` | 200 `{"ok": true, "authenticated_at": <초>}` | 400 `invalid_request`, 403 `step_up_requires_session`, 403 `invalid_password`, 409 `password_not_set`, 429 `too_many_attempts` |
| `GET` | `/admin/accounts` | `admin` | 200 HTML (통합 콘솔, §6.2) | 404 |
| `GET` | `/api/admin/accounts` | `admin` | 200 `{"accounts": [...]}` | 404 |
| `GET` | `/me` | `owner` (`_any`, 접두 아래에서도 받음) | 302 `/settings/account` (쿼리를 넘기지 않음) | 404 |
| `GET` | `/api/me` | `owner` | 200 계정 정보 | 404 |
| `PATCH` | `/api/me` | `owner` | 200 계정 정보. 본문 키는 `display_name`, `profile_public`(0·1), `locale`(`en`·`ko`·`null`·`""`, 쿠키 `claire_locale`을 쓰거나 지움) | 400 `invalid_request`, 400 `invalid_display_name`, 400 `invalid_profile_public`, 400 `invalid_locale` |
| `POST` | `/api/me/password` | `owner` + 단계 인증 | 200 `{"ok": true, "other_sessions_revoked": n}` | 400 `invalid_request`, 400 `password_rejected`, 401 `reauth_required`, 403 `step_up_requires_session`, 403 `invalid_password`, 409 `account_not_active`, 429 `too_many_attempts` |

### 6.1 단계 인증 응답 규약

- **[코드]** 세션 채널(쿠키·`X-Session`)이 아니면 403 `{"error": "step_up_requires_session"}`. Bearer 토큰은 사람이 다시 인증할 수 없으므로 재인증 경로를 안내하지 않는다.
- **[코드]** 세션이지만 최근 15분 안에 인증하지 않았으면 401 `{"error": "reauth_required", "reauth": "/api/auth/reauth"}`와 `WWW-Authenticate: Session realm="claire", error="reauth_required"`. 클라이언트는 `POST /api/auth/reauth` 뒤에 원래 요청을 다시 보낸다.
- **[코드]** 재인증 비밀번호가 틀리면 403 `invalid_password`, 계정에 비밀번호가 없으면 409 `password_not_set`.
- **[코드]** JSON 본문은 §1.3대로 `application/json`이어야 한다. 본문 키가 정해진 집합과 다르면 모르는 키를 버리지 않고 400 `invalid_request`다.

### 6.2 통합 콘솔 경로 (`/admin/*`, `/settings/*`)

확인 기준: 콘솔 경로 분리 P4 커밋 `cdc0f2a` (2026-11-05). 동작의 정본은 [ACCOUNTS.md](ACCOUNTS.md) §5.9, §5.10이다. 모든 경로는 `GET`과 `HEAD`를 받고, 두 인스턴스 모드에서 열리며, 루트에서만 받는다(`_system`, 접두 경로는 404). 200 HTML 응답과 302 응답은 `Cache-Control: no-store`, `Referrer-Policy: same-origin`이다.

| Method | Path | 규칙 | 성공 | 그 밖의 응답 |
|---|---|---|---|---|
| `GET` | `/admin` | `admin` | 302 `/admin/instance` | 404 비관리자(302보다 먼저), 404 잘못된 `?theme=` |
| `GET` | `/admin/{instance,accounts,idp,providers,limits,themes,tokens,mcp}` | `admin` | 200 HTML | 404 비관리자, 404 잘못된 `?theme=` |
| `GET` | `/api/admin/mcp/telemetry` | `admin` | 200 `{"records": [...], "count": n}` | 400 `invalid_limit`(`limit`이 1–200 정수가 아님), 404 비관리자 |
| `GET` | `/settings` | `owner` | 302 `/settings/account`, `?from=syndication`이면 302 `/settings/reader` (검증한 `?theme=` 유지) | 404 owner 아님, 404 잘못된 `?theme=` |
| `GET` | `/settings/{account,keys,themes,tokens}` | `owner` | 200 HTML. 관리자의 `/settings/themes`·`/settings/tokens`는 302 `/admin/themes`·`/admin/tokens` | 404 owner 아님, 404 잘못된 `?theme=` |
| `GET` | `/settings/{reader,connections,feeds,tags,rules,outbound,opml,mcp,webmcp}` | `owner` | 200 HTML (테마별 섹션) | 404 owner 아님, 404 잘못된 `?theme=` |
| `GET` | `/syndication/preferences` | `owner` (`_system`) | 302 `/settings/*` 또는 관리자의 `idp`·`userdb` 탭은 `/admin/idp`·`/admin/accounts` (ACCOUNTS.md §5.10 표) | 404 owner 아님, 404 잘못된 `?theme=`, 404 빈·모르는 `?tab=` |

- **[코드]** `?theme=`는 호출자 계정의 테마여야 한다. 빈 값, 없는 테마, 다른 계정의 테마는 404이고 기본 테마로 바꾸지 않는다(C1).
- **[코드]** `GET /settings/account?notice=`는 `password_not_saved`, `identity_linked`, `already_linked`만 안내문으로 그린다. 그 밖의 값은 무시한다.
- **[코드]** 비관리자 응답 HTML에는 `/admin/*` 링크와 관리자 섹션 마크업이 없다. 싱글 모드에서는 이 경로들과 `GET /whoami`가 `@username`을 내지 않는다(S9).
- **[코드]** `GET /api/admin/mcp/telemetry` 쿼리: `limit`(기본 50, 최대 200), `status`, `tool_name`, `since`(ISO 8601, 해석 실패 시 400 `invalid_since`). 레코드 필드: `id`, `timestamp`, `client_id`, `call_type`, `tool_name`, `duration_ms`, `status`, `error_code`, `error_message`, `response_bytes`. 최근 것부터.

## 7. 계정 피드 경로 (FP4-F1, FP4-F2)

확인 기준: FP4-F1·FP4-F2 구현 (2026-11-13). 동작의 정본은 [FEDERATION.md](FEDERATION.md) §15다. 모든 경로는 `owner`(`_any`, 접두를 무시하고 자격증명 계정을 씀)이고 싱글 모드와 네트워크 모드 모두에서 열린다(RD15). owner가 아니면 404다. 쿠키 채널의 상태 변경 요청은 같은 origin이어야 하고, 아니면 403이다. 응답은 `Cache-Control: no-store`다. 다른 계정의 `feed_id`·항목 ID는 없는 ID와 같게 다룬다.

| Method | Path | 성공 | 그 밖의 응답 |
|---|---|---|---|
| `GET` | `/api/feeds` | 200 `{"feeds": [...], "folders": [...], "limit": 1000}`. 피드 키: `id`, `url`, `site_url`, `title`, `folder`, `format`, `websub_hub`, `last_polled_at`, `last_success_at`, `next_poll_at`, `last_status`, `last_error`, `failure_count`, `created_at`, `items`, `unread` | 404 |
| `POST` | `/api/feeds` | 본문 `{"url", "title"?, "folder"?}`. 201 `{"feed", "created": true}`, 이미 있는 URL은 200 `{"feed", "created": false}` | 400 `invalid_request`, 400 `invalid_url`(`http`·`https`가 아님), 409 `feed_limit` |
| `DELETE` | `/api/feeds` | 본문 `{"feed_id"}`. 200 `{"removed": true, "feed_id", "items": n}`. 연결된 문서는 지우지 않는다 | 400 `invalid_request`, 404 `feed_not_found` |
| `POST` | `/api/feeds/poll` | 본문 `{}` 또는 `{"feed_id"}`. 200 `{"polled", "not_polled", "outcomes": [{"feed_id", "result", "http_status", "added", "updated", "error", "next_poll_at"}]}`. `result`는 `updated`·`not_modified`·`error`·`backoff`. 한 번에 50개까지 | 400 `invalid_request`, 404 `feed_not_found` |
| `GET` | `/api/feeds/opml` | 200 `text/x-opml` OPML 2.0 첨부 파일 | 404 |
| `POST` | `/api/feeds/opml` | 본문 `{"opml": "<문서>"}`. 200 `{"counts": {"added", "skipped", "duplicate", "failed"}, "added", "skipped", "duplicate", "failed"}` | 400 `invalid_request`, 400 `invalid_opml`(`reason`: `invalid_xml`, `not_opml`, `too_large`, `too_many_outlines`), 413 본문 1MB 초과 |
| `GET` | `/api/feeds/items` | 200 `{"items": [...], "next_before_id"}`. 쿼리: `feed_id`, `folder`, `unread`·`starred`(`0`·`1`), `before_id`, `limit`(1–200, 기본 50), `content=1`(HTML 본문 포함). 최신 행부터 | 400 `invalid_request` |
| `PATCH` | `/api/feeds/items` | 본문 `{"item_ids": [...], "unread"?, "starred"?}`(1–500개) 또는 `{"feed_id", "unread"}`. 200 `{"changed": n}` | 400 `invalid_request`, 404 `feed_not_found` |
