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
`CLAIRE_ANONYMOUS_READONLY=0`(프라이빗/스텔스 모드)로 구동되는 서버는 자격증명이 없는 읽기 요청에 대해 `401 Unauthorized` 대신 HTTP `404 Not Found`를 반환해야 한다. 이는 외부 정찰에 대해 엔드포인트의 존재 여부와 파라미터 구조를 일절 노출하지 않기 위함이다.

## 4. 공통 에러 페이로드 규약

1. **표준 JSON 에러**: 모든 비정상 상태(4xx, 5xx)의 JSON 응답은 최소 `{"error": "<detail_string>"}` 형태를 보장한다.
2. **MCP 및 RFC 6750 에러**: `/mcp` 엔드포인트는 토큰 무효 시 HTTP `401 Unauthorized`와 함께 `WWW-Authenticate: Bearer error="invalid_token", error_description="..."` 헤더를 반환한다.
3. **내부 서버 오류 은폐**: 500 오류 시 데이터베이스 경로나 내부 스택 트레이스를 반환하지 않고 `{"error": "internal server error"}`로 정제하여 반환한다.

## 5. 최소 공통 엔드포인트 목록

양 계보 구현이 상호 호환성을 위해 제공하는 최소 공통 엔드포인트 집합은 다음과 같다.

| Method | Path | 최소 접근 수준 | 설명 |
|---|---|---|---|
| `GET` | `/health` | `public` | 서비스 liveness 및 DB 연결 검사 (`{"ok": true}`) |
| `GET` | `/whoami` | `read` | 현재 요청의 인증 스코프 확인 (`{"scope": "...", "server": {"mode": "single\|network"}, "relation": "owner\|delegate\|visitor", "is_admin": bool}`. USERNAME은 싣지 않는다) |
| `GET` | `/stats` | `read` | 문서, 엔티티, 릴레이션 카운트 집계 (`owner`·`collaborator` 요청에는 현재 활성 적재 프로바이더의 선택 가능 사고 수준 메타 `ingest_effort`를 포함한다) |
| `POST` | `/oauth/token` | `public` | [RFC 8693] 토큰 교환 및 Bearer 토큰 발급. 토큰 교환은 계정에 귀속된 세션 토큰만 받는다([`TOKEN_EXCHANGE.md`](TOKEN_EXCHANGE.md) §7) |
| `POST` | `/search` | `read` | 하이브리드 / FTS 키워드 지식 검색 |
| `GET` | `/documents` | `read` | 아카이브된 문서 목록 조회. 정렬은 `fetched_at` 내림차순, 같으면 `id` 내림차순. 파라미터가 없으면 기존과 같이 최신 300건을 돌려주고, 응답에 `next_before`(다음 페이지 커서, 마지막 페이지면 `null`)와 `total`(커서를 뺀 필터 일치 건수)만 추가된다. 선택 파라미터: `limit`(1–300, 기본 300), `before=<fetched_at>:<id>`(이전 응답의 `next_before`), `pinned=0\|1`, `hidden=0\|1`, `q`(제목 또는 최신 추출 요약의 부분 일치, 200자 이하), `detail=missing\|mismatched`, `id`(반복 가능, 100개 이하). 잘못된 값은 `400`. `anonymous` 범위는 모든 페이지와 모든 필터에서 숨김 문서를 받지 않는다. In the personal knowledge base, for its account, each document also has `theme_match` (`{"theme": "<number>", "theme_label", "knowledge_type", "score"}`, or `null` when the document has no matched theme) and `never_contribute` (boolean); for each other requester and for a shared knowledge base the two keys are absent |
| `GET` | `/document` | `read` | 특정 문서 상세 및 본문 조회. `nodes[]`의 각 항목은 `id`, `label`, `group`, `observations`, `aliases` (엔티티 별칭 목록)를 포함한다. `extraction_provider`·`extraction_model`은 최근 추출의 프로바이더와 모델이고 추출 행이 없으면 `null`이다. 응답은 `detail`, `detail_html` 등 기존 필드는 엄격히 유지된다 |
| `GET` | `/document/export` | `read` | 문서 하나를 파일로 내보낸다. `id`(필수), `format=markdown`(기본값, `md`도 받는다). AsciiDoc 본문을 Markdown으로 바꾸고 YAML 머리말(title, source, date, summary, claire_document_id)을 붙여 `text/markdown; charset=utf-8` 첨부 파일로 준다. 읽기 전용. 다른 `format`과 `id` 누락은 `400`, 없는 문서는 `404`. Claire가 Markdown을 쓰는 유일한 곳이다(RD24, 현재 동작: [DOCUMENT_FORMAT.md](DOCUMENT_FORMAT.md) §5) |
| `GET` | `/graph` | `read` | 전체 온톨로지 지식 그래프 데이터 (JSON). `nodes[]`의 각 항목은 `id`, `label`, `group`, `sources`, `obs`, `degree`, `created_at` (`entities.created_at`, 에포크 초)를 가진다. 요청 계정에 엔티티 언어(`accounts.entity_language`)가 있으면 `label`은 그 언어의 이름이다([ACCOUNTS.md](ACCOUNTS.md) "엔티티 언어") |
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
- **[코드]** `owner`·`admin` 규칙을 통과하지 못하면 §3.4와 같이 404다. `admin` 규칙은 owner 범위이면서 `server_role=admin`인 자격증명만 통과한다. 쿠키 채널의 `POST`는 `Origin`이 같은 origin이어야 하고, 아니면 403이다.
- **[코드] Single mode root (KNOWLEDGE_FEDERATION_DESIGN.md Section 15)**: when the admin is `active` and has a USERNAME, `GET`/`HEAD /` gives 302 (`Cache-Control: no-store`) to `/@{username}/` with the same query, and a `GET`/`HEAD` of each other root path of the personal knowledge base (for example `/graph`, `/documents`, `/whoami`) gives 308 to `/@{username}/…`. A write at a root path is not redirected. `/@{name}/…` with another name gets 404. Before the first setup: `/` with the admin session gives 302 `/setup`; each other request to `/` gets the HTML page "setup not complete" with 503, `Cache-Control: no-store`, and `Retry-After: 3600`. After `/setup` and `/login` the 303 to `/` then gives the 302. Details: [KNOWLEDGE_FEDERATION.md](KNOWLEDGE_FEDERATION.md) §10.8.

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
| `PATCH` | `/api/me` | `owner` | 200 계정 정보. 본문 키는 `display_name`, `profile_public`(0·1), `locale`(`en`·`ko`·`null`·`""`, 쿠키 `claire_locale`을 쓰거나 지움), `entity_language`(`en`·`ko`·`ja`·`zh`·`null`·`""`) | 400 `invalid_request`, 400 `invalid_display_name`, 400 `invalid_profile_public`, 400 `invalid_locale`, 400 `invalid_entity_language` |
| `POST` | `/api/me/password` | `owner` + 단계 인증 | 200 `{"ok": true, "other_sessions_revoked": n}` | 400 `invalid_request`, 400 `password_rejected`, 401 `reauth_required`, 403 `step_up_requires_session`, 403 `invalid_password`, 409 `account_not_active`, 429 `too_many_attempts` |

### 6.1 단계 인증 응답 규약

- **[코드]** 세션 채널(쿠키·`X-Session`)이 아니면 403 `{"error": "step_up_requires_session"}`. Bearer 토큰은 사람이 다시 인증할 수 없으므로 재인증 경로를 안내하지 않는다.
- **[코드]** 세션이지만 최근 15분 안에 인증하지 않았으면 401 `{"error": "reauth_required", "reauth": "/api/auth/reauth"}`와 `WWW-Authenticate: Session realm="claire", error="reauth_required"`. 클라이언트는 `POST /api/auth/reauth` 뒤에 원래 요청을 다시 보낸다.
- **[코드]** 재인증 비밀번호가 틀리면 403 `invalid_password`, 계정에 비밀번호가 없으면 409 `password_not_set`.
- **[코드]** JSON 본문은 §1.3대로 `application/json`이어야 한다. 본문 키가 정해진 집합과 다르면 모르는 키를 버리지 않고 400 `invalid_request`다.

### 6.2 통합 콘솔 경로 (`/admin/*`, `/settings/*`)

확인 기준: 콘솔 경로 분리 P4 커밋 `cdc0f2a` (2026-11-05). 섹션 목록과 §6.3은 기준 커밋 `d15ae8e0` (2026-11-16). 동작의 정본은 [ACCOUNTS.md](ACCOUNTS.md) §5.9, §5.10이다. 모든 경로는 `GET`과 `HEAD`를 받고, 두 서버 모드에서 열리며, 루트에서만 받는다(`_system`, 접두 경로는 404). 200 HTML 응답과 302 응답은 `Cache-Control: no-store`, `Referrer-Policy: same-origin`이다.

| Method | Path | 규칙 | 성공 | 그 밖의 응답 |
|---|---|---|---|---|
| `GET` | `/admin` | `admin` | 302 `/admin/server` | 404 비관리자(302보다 먼저), 404 잘못된 `?kb=` |
| `GET` | `/admin/{server,accounts,federation,idp,providers,limits,themes,tokens,backups,storage,mcp}` | `admin` | 200 HTML | 404 비관리자, 404 잘못된 `?kb=` |
| `GET` | `/api/admin/mcp/telemetry` | `admin` | 200 `{"records": [...], "count": n}` | 400 `invalid_limit`(`limit`이 1–200 정수가 아님), 404 비관리자 |
| `GET` | `/settings` | `owner` | 302 `/settings/account`, `?from=syndication`이면 302 `/settings/reader` (검증한 `?kb=` 유지) | 404 owner 아님, 404 잘못된 `?kb=` |
| `GET` | `/settings/{account,telegram,bundles,backups,federation,keys,themes,tokens}` | `owner` | 200 HTML. 관리자의 `/settings/tokens`는 302 `/admin/tokens`. 폐지된 `/settings/themes`는 308(관리자는 `/admin/themes`, 그 외 계정은 `/settings/kb`) | 404 owner 아님, 404 잘못된 `?kb=` |
| `GET` | `/settings/{reader,connections,feeds,tags,rules,outbound,opml,mcp,webmcp}` | `owner` | 200 HTML (테마별 섹션) | 404 owner 아님, 404 잘못된 `?kb=` |
| `GET` | `/syndication/preferences` | `owner` (`_system`) | 302 `/settings/*` 또는 관리자의 `idp`·`userdb` 탭은 `/admin/idp`·`/admin/accounts` (ACCOUNTS.md §5.10 표) | 404 owner 아님, 404 잘못된 `?kb=`, 404 빈·모르는 `?tab=` |

- **[코드]** `?kb=`는 호출자 계정의 테마여야 한다. 빈 값, 없는 테마, 다른 계정의 테마는 404이고 기본 테마로 바꾸지 않는다(C1).
- **[코드]** `GET /settings/account?notice=`는 `password_not_saved`, `identity_linked`, `already_linked`만 안내문으로 그린다. 그 밖의 값은 무시한다.
- **[코드]** 비관리자 응답 HTML에는 `/admin/*` 링크와 관리자 섹션 마크업이 없다. 이 경로들과 `GET /whoami`의 응답에는 `@username`이 없다. Rule S9 is withdrawn (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 4): single mode serves the admin at `/@{username}/` (§6, single mode root).
- **[코드]** `GET /api/admin/mcp/telemetry` 쿼리: `limit`(기본 50, 최대 200), `status`, `tool_name`, `since`(ISO 8601, 해석 실패 시 400 `invalid_since`). 레코드 필드: `id`, `timestamp`, `client_id`, `call_type`, `tool_name`, `duration_ms`, `status`, `error_code`, `error_message`, `response_bytes`. 최근 것부터.

### 6.3 계정별 기능 경로 (텔레그램 봇, 번들, 백업)

확인 기준: 커밋 `d15ae8e0` (2026-11-16). 동작의 정본은 [ACCOUNTS.md](ACCOUNTS.md) §14다.

- **[코드]** `/api/me/telegram`, `/api/me/support-bundles`, `/api/me/support-bundles/download`, `/api/me/data-bundles`, `/api/me/data-bundles/download`, `/api/me/backups`, `/api/me/backups/download`, `/api/me/backups/policy` use the rule `_any("owner")`. They use the account of the credential. A bundle or backup of another account is handled as not found (404).
- **[코드]** `/api/admin/account-backups` (GET, HEAD) and `/api/admin/account-backups/restore` (POST) use the rule `_system("admin")`. A restore conflict gives 409 `restore_conflict` and changes nothing.
- **[코드]** Step-up authentication (§6.1) is necessary for bot register, replace, and delete, a change of allowed users, backup delete, and restore.
- **[코드]** Responses never contain the bot token. They contain only metadata (`bot_username`, `fingerprint`, `status`).

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
| `GET` | `/api/feeds/items/{item_id}` | 200 `{"item"}`: the keys of the list, `has_body`, `render_url` (FP4-F3, [FEDERATION.md](FEDERATION.md) §23) | 404 `item_not_found` |
| `POST` | `/api/feeds/discover` | Body `{"url"}`. 200 `{"url", "feeds": [{"url", "title", "format", "subscribed"}], "error"}` | 400 `invalid_request`, 400 `invalid_url` |
| `POST` | `/api/feeds/items/{item_id}/ingest` | Body `{"theme"}`. 200: the ingest report with `linked` (`{"kb_id", "document_id"}` or `null`) | 400 `invalid_request`, 404 `item_not_found`, 404 (theme), 409 `item_without_url` |
| `GET` | `/render/feed/{item_id}?t={token}` | 200 HTML for a sandbox `iframe`. On the app origin only without a `usercontent` host | 404 |

## 8. 연합 클라이언트 발신 상호작용 경로 (FP4-9)

확인 기준: FP4-9 구현 (2026-11-15). 동작의 정본은 [FEDERATION.md](FEDERATION.md) §25다. 모든 경로는 `owner`(`_any`)이고 네트워크 모드에서만 열린다. owner가 아니거나 싱글 모드이면 404다. 쿠키 채널의 상태 변경 요청은 같은 origin이어야 하고, 아니면 403이다. 응답은 `Cache-Control: no-store`다. 다른 계정의 노트·항목 ID는 없는 ID와 같게 다룬다. 삭제를 뺀 모든 경로는 계정이 연합 액터여야 하고, 아니면 409 `not_actor`다.

| Method | Path | 성공 | 그 밖의 응답 |
|---|---|---|---|
| `POST` | `/api/federation/notes` | 본문 `{"content", "in_reply_to"?, "summary"?, "sensitive"?, "language"?, "quote_of"?, "quote_policy"?}`. `content`는 일반 텍스트이고 HTML은 서버가 만든다. `quote_of`는 인용할 타임라인 항목 ID, `quote_policy`는 `public`·`followers`·`manual`·`nobody`다(FP4-9Q, §9). 201 `{"note", "item_id"}` | 400 `invalid_quote_policy`, 409 `own_item`(자기 노트 인용), 400 `invalid_request`, 400 `empty_content`, 400 `content_too_long`(`limit`), 400 `summary_too_long`, 400 `too_many_mentions`, 400 `too_many_tags`, 400 `invalid_language`, 400 `invalid_mention`, 400 `blocked_domain`, 404 `item_not_found`, 409 `not_actor` |
| `PATCH` | `/api/federation/notes/{note_id}` | 본문은 `content`, `summary`, `sensitive`, `language`, `quote_policy` 중 하나 이상. 200 `{"note"}` | `POST`의 400 응답, 404 `note_not_found`, 409 `not_actor` |
| `DELETE` | `/api/federation/notes/{note_id}` | 200 `{"deleted": true, "id"}`. 객체 경로는 그 뒤로 410이다 | 404 `note_not_found` |
| `POST`, `DELETE` | `/api/federation/timeline/{item_id}/favourite` | 200 `{"item_id", "favourited", "boosted"}` | 400 `blocked_domain`, 404 `item_not_found`, 409 `not_actor`, 409 `own_item` |
| `POST`, `DELETE` | `/api/federation/timeline/{item_id}/boost` | 200 `{"item_id", "favourited", "boosted"}` | 400 `blocked_domain`, 404 `item_not_found`, 409 `not_actor`, 409 `own_item` |

## 9. 연합 클라이언트 인용 경로 (FP4-9Q)

확인 기준: FP4-9Q 구현 (2026-11-15). 동작의 정본은 [FEDERATION.md](FEDERATION.md) §26이다. 모든 경로는 `owner`(`_any`)이고 네트워크 모드에서만 열린다. owner가 아니거나 싱글 모드이면 404다. 쿠키 채널의 상태 변경 요청은 같은 origin이어야 하고, 아니면 403이다. 응답은 `Cache-Control: no-store`다. 다른 계정의 요청·객체 ID는 없는 ID와 같게 다룬다.

| Method | Path | 성공 | 그 밖의 응답 |
|---|---|---|---|
| `GET` | `/api/federation/quotes` | 200 `{"requests": [...], "authorizations": [...], "counts": {"pending", "approved"}}`. `requests`는 소유자의 결정을 기다리는 인용 요청, `authorizations`는 승인한 요청이다. 각각 최신 100개까지. 요청 키: `id`, `state`, `object`(`id`, `url`, `kind`, `text`), `quoting_object`, `quoting_actor`, `authorization`, `created_at`, `updated_at` | 404 |
| `PATCH` | `/api/federation/quotes/{quote_id}` | 본문 `{"state"}`: `approved`·`refused`(기다리는 요청), `revoked`(승인한 요청). 200 `{"quote"}`. 사이드카가 `Accept`(`QuoteAuthorization` 포함), `Reject`, 또는 승인의 `Delete`를 보낸다 | 400 `invalid_request`, 404 `quote_not_found`, 409 `invalid_state`(`state`), 409 `not_actor` |
| `PATCH` | `/api/federation/objects/{object_id}` | 본문 `{"quote_policy"}`: `public`·`followers`·`manual`·`nobody`. 200 `{"object": {"id", "kb_id", "document_id", "quote_policy"}}`. 값이 바뀌면 객체의 `Update`를 보낸다 | 400 `invalid_request`, 400 `invalid_quote_policy`, 404 `object_not_found` |

- 타임라인 항목(`GET /api/federation/timeline`, `GET /api/federation/timeline/{item_id}`)에는 `quote` 키가 있다: `null`, 또는 `{"iri", "item_id", "state"}`. `state`는 자기 노트의 인용 상태(`pending`, `approved`, `refused`, `revoked`)이고, 원격 게시물이면 `null`이다.
- `note`에는 `quote_policy`, `quotes`(승인한 인용 수), `quote`(`{"iri", "state"}` 또는 `null`)가 있다. `GET /api/federation/objects`의 항목에는 `quote_policy`와 `quotes`가 있다.
- 승인의 공개 경로 `GET /ap/quote-authorizations/{id}`는 사이드카가 응답한다([FEDERATION.md](FEDERATION.md) §26.1). 철회한 승인은 410이다.

## 공유 지식 베이스 경로와 생성 (CT-1)

- **[코드]** `/kb/{number}/…`: 공유 지식 베이스를 고정한 네임스페이스 라우트(모든 모드). 읽기는 누구나 200. 쓰기는 권한(`maintain`)이 없으면 403 `kb_capability_required`, 익명은 404. 적재는 403 `kb_ingest_by_contribution`. 종류에 맞지 않는 행과 없는 번호는 404.
- **[코드]** 옛 링크 `/themes/{number}/…`, `/@{u}/themes/{number}/…`: 공유는 308 `/kb/{number}/…`, 개인은 읽을 수 있으면 308 그 계정 경로, 아니면 404.
- **[코드] Lifecycle of a knowledge base (CT-3, design Section 10.1)**. The old routes `POST`, `PATCH`, and `DELETE /themes` are removed (404).
  - `POST /kb` (scope `owner`, each active account, no limit): body `label`, `knowledge_type` (`real` or `fictional`, required: else 400 `knowledge_type_required`), and optional `description`, `icon`, `seed_entities` (at most 20 names: 400 `seed_entities_too_many`, `seed_entities_invalid`), `presentation_enabled`, `sorcerer`, `ga_measurement_id`. `join_policy` and `state` are not for a create (400 `<field>_not_at_create`). 201 `{"ok": true, "knowledge_base": {..., "account_id": null, "created_by_account_id", "knowledge_type", "join_policy": "approval", "state": "active", "seed_entities"}}`. The founder becomes the first moderator. `CLAIRE_MULTI_THEME=0` gives 403.
  - `PATCH /kb/{number}/` (shared: capability `settings`) and `PATCH /@{u}/` (personal: its account; in single mode `PATCH /`): the path selects the knowledge base; no body field does. Fields: `label`, `description`, `icon`, `is_public`, `presentation_enabled`, `sorcerer`, `ga_measurement_id`; for a shared knowledge base also `join_policy` (`open`, `approval`), `knowledge_type`, `seed_entities`, and `state` (`active`, `archived`). A field with a wrong type gets 400 `<field>_invalid`; a shared-only field for a personal knowledge base gets 400 `kb_shared_only`; an old alias (`knowledge_manager`, `ga_id`, `public`, `id`) is an unknown field and is ignored. 200 `{"ok": true, "knowledge_base": {...}}`. A path without the last slash gets 308.
  - `DELETE /kb/{number}/` (capability `delete`: the admin; step-up): without `?apply=true` the answer is the plan only, `{"ok": true, "applied": false, "would_delete": {"documents", "entities", "relations", "members", "contributions"}}` (PRINCIPLES D3). With `?apply=true` the knowledge base, its documents, and its graph are deleted, and `admin_audit` gets the action `kb_delete`. A personal knowledge base gets 400 `kb_personal_cannot_delete`.
  - An archived shared knowledge base is read-only: join, contribution, review, curation, maintenance, and removal get 403 `kb_archived`. Settings (to unarchive), people, and delete stay.
- **[코드] Membership and moderation (CT-3, design Section 10.2)**. An account is its public number in paths and bodies.
  - `POST /kb/{number}/membership` (scope `owner`): `open` gives 201 with an active membership, `approval` a pending one; a second request gives 200 and changes nothing. A banned account gets 403 `kb_capability_required`; an archived knowledge base 403 `kb_archived`.
  - `DELETE /kb/{number}/membership`: leave, or withdraw a join request. The last active moderator gets 409 `last_moderator`; no membership gets 404 `membership_not_found`.
  - `GET /kb/{number}/members` (each reader): a collection with `members`: `number`, `account`, `display_name`, `username` (network mode and a public profile only), `role`, `state`, `requested_at`, `decided_at`. Active members for each reader; pending and banned entries only for an account with the capability `people`.
  - `GET /kb/{number}/moderation-log` (scope `owner`, capability `people`): a collection with `log`, the newest entry first: `{"number", "action", "actor": {"account": "<public number>", "display_name"} | null, "target", "reason", "created_at"}`. `actor` is `null` for an action of the server or of an account that is gone. `number` is the sequence number of the entry (the cursor of the pages).
  - `PATCH /kb/{number}/members/{account}` (capability `people`): body `{"action": "approve" | "ban" | "remove", "reason"?}` or `{"role": "collaborator" | "member", "reason"?}`. `approve` needs a pending membership (409 `not_pending`). `ban` also takes an account without a membership. `remove` deletes the membership, also a ban. A role change needs a session with a recent sign-in (403 `step_up_requires_session`, 401 `reauth_required`) and an active member (409 `not_active_member`). A change of an active moderator gets 403 `moderator_protected` (the admin can ban or remove one; the role itself changes only by the admin route). The actor cannot change itself (400 `self_not_allowed`). Each change writes `kb_moderation_log`.
  - `PUT` and `DELETE /api/admin/kb/{number}/moderators/{account}` (admin, step-up): give the role `moderator` (a membership is made when there is none) or remove it (the account is a member again; 404 `not_a_moderator`). Each change writes `kb_moderation_log` and `admin_audit` (`kb_moderator_give`, `kb_moderator_remove`).
  - A shared knowledge base without an active moderator has the admin as its only moderator. The settings view of `GET /kb` has `active_moderators` (the count).
- **[코드] Step-up routes**: `DELETE /kb/{number}/` and the admin moderator routes need a session with a recent sign-in (design Section 6.8): a bearer credential gets 403 `{"error": "step_up_requires_session"}`, a session without a recent sign-in gets 401 `{"error": "reauth_required", "reauth": "/api/auth/reauth"}`.
- **[코드] Scopes and status codes (CT-2)**: credential scopes are `owner`, `write`, `readonly` (`write` replaces `collaborator`; `POST /tokens` refuses each other value with 400). On a route that acts on a knowledge base, a credential of an account whose scope is too small gets 403 `{"error": "scope_insufficient", "scope": "<its scope>", "required": "<write|owner>"}`. A missing capability in a shared knowledge base gets 403 `{"error": "kb_capability_required"}`; a write of content by a path that pins a shared knowledge base gets 403 `{"error": "kb_ingest_by_contribution"}`. `GET /tokens` has `scope_notices`: the tokens whose scope `collaborator` became `readonly`, listed once. The table of each route with its capability and minimum scope is in `tests/test_credential_scopes.py`; the rules are in [`THEMES.md`](THEMES.md) §4.1.
- **[코드] Collections (CT-2)**: `GET /kb` (`?joined=true`, `?type=real|fictional`), `GET /@{u}/kb`, and `GET /themes` (themes, `?type=`); `GET /@{u}/themes` gets 308 to `/@{u}/kb`. Each item of `GET /kb` has `membership_state`: the state of the membership of the requester in that knowledge base (`"active"`, `"pending"`, `"banned"`), or `null` (no membership, or an anonymous requester). Details: [`THEMES.md`](THEMES.md) §4.5.
- **[코드] One path, HTML and JSON**: `/kb`, `/themes`, `/themes/{number}/`, and `/kb/{number}/` give the HTML page to a browser and JSON to an API client.
  - `/kb`, `/themes`, `/themes/{number}/`: `collections.wants_html` selects the HTML page (`Sec-Fetch-Dest: document`, or no such header and `Accept` with `text/html`).
  - `GET /kb/{number}/`: a request that does not want HTML and has `Accept: application/json` gets `{"knowledge_base": {...}}`, the same item as in `GET /kb` (with `role` and `membership_state` of the requester). Each other request gets the HTML page of the knowledge base; with the query parameter `doc` or `node` it gets the workspace page, which shows that document or node.
  - The responses have `Vary: Cookie, Authorization, Sec-Fetch-Dest, Accept`.
  - The old pages `/explore/themes` and `/explore/themes/{number}` are removed (404).
- **[코드] Contributions (CT-4, design Sections 9.1, 9.4 to 9.6)**. A contribution is its public number in paths.
  - `POST /kb/{number}/contributions` (scope `write`, capability `contribute_direct`: an active member): body `{"document": "<document ID>"}`; the document must be in the personal knowledge base of the requester. The merge job runs in the request. 201 `{"ok": true, "contribution": {"number", "document", "title", "origin", "state", "contributor": {"account", "display_name"}, "approved", "approved_at", "updates_stopped", "contributed_at", "revised_at", "reason", "revisions"}}`; the state is `visible`, or `failed` with the code in `reason`. `title` is the title of the document of the contribution; when the copy is gone, the contributor and a reviewer get the title of the source document, and each other reader gets `null`. Errors: 400 `document_required`; 404 `document_not_found`; 409 `document_not_extracted`, `contribution_duplicate` (with `url`), `contribution_tombstoned`; 403 `kb_capability_required`, `kb_archived`.
  - `GET /kb/{number}/contributions` (`?state=`): a collection with `contributions`. Each reader gets the visible ones; a reviewer gets each state; the requester gets its own in each state. `reason` and `revisions` go only to a reviewer and to the contributor.
  - `DELETE /kb/{number}/contributions/{contribution}` (scope `write`): the contributor withdraws. 409 `contribution_approved` after the approval; 404 for a contribution of another account.
  - `PATCH /kb/{number}/contributions/{contribution}` (scope `owner`, capability `review`): `{"action": "approve" | "reject" | "hide" | "unhide" | "stop_updates" | "restart_updates", "reason"?}`. Errors: 400 `action_invalid`, `reason_invalid`; 409 `contribution_approved`, `contribution_not_pending`, `contribution_not_visible`, `contribution_not_hidden`, `contribution_closed`.
  - `POST /kb/{number}/contributions/{contribution}/remove` (scope `owner`, capability `remove`): `{"reason": "<code>"}`.
  - `POST /kb/{number}/contributions/{contribution}/retry` (scope `write`): the contributor or a reviewer starts the merge job of a failed contribution again; 409 `contribution_not_failed`.
- **[코드] Automatic contribution and mutual collaboration (CT-13, design Sections 9.9 and 9.10)**. System surface; each route acts for the account of the credential.
  - `GET /api/auto-rule`: `{"server_switch": bool, "rule": null | {"state": "active" | "paused", "memberships", "mutual", "include_uploads", "themes": [{"number", "label"}], "activated_at"}}`.
  - `PUT /api/auto-rule` (scope `owner`): body `{"state", "memberships", "mutual", "include_uploads", "themes": ["<number>", ...]}`; each field is required. To turn the rule on or to make it wider needs a recent sign-in (403 `step_up_requires_session` for a bearer credential, 401 `reauth_required` for an old session). 200 `{"ok": true, "server_switch", "rule", "contributions": <count of new contributions>}`. Errors: 400 `rule_invalid`; 404 `theme_not_found`.
  - `POST /api/auto-rule/dry-run` (scope `owner`): the body of a rule; `{"server_switch", "targets": [{"knowledge_base", "label", "mode": "approved" | "direct" | "pending", "documents": [{"document", "title", "score"}]}]}`. It writes nothing.
  - `GET /api/contributions` (`?origin=`, one of `direct`, `bulk`, `auto`, `curation`, `fork`, `migration`; another value gets 400 `origin_invalid`): a collection with `contributions`, the contributions with the credit of the requester, the newest first. Each one has `knowledge_base`, `label`, and the fields of a contribution for its contributor. `?origin=auto` gives the automatic contributions. The old path `/api/auto-contributions` is removed.
  - `GET /api/contribution-blocks` `{"documents": [...]}`; `PUT` and `DELETE /api/contribution-blocks/{document}` (scope `owner`): the flag "never contribute"; 404 `document_not_found`.
  - `GET /api/mutual-collaborations` `{"mutual_collaborations": [{"account", "display_name", "state": "pending" | "active", "requested_by_me", "created_at", "decided_at"}]}`; `POST` with `{"account": "<number>"}` (recent sign-in; 201; 404 `account_not_found`; 409 `mutual_exists`); `PUT /api/mutual-collaborations/{account}` accepts the request of that account (recent sign-in; 404 `mutual_not_found`); `DELETE` ends it or removes the request.
  - Admin: `GET` and `PUT /api/admin/auto-contribution`: `{"state": "on" | "off", "settings": {"auto_match_threshold", "auto_run_documents"}}`; the `PUT` body has `state` and the settings; 400 `settings_invalid`.
  - MCP tools: `kb_candidates` (`kb` or `theme`, `include_uploads`) and `kb_submit` (`documents`, `theme`; not for the scope `readonly`).
- **[코드] Reputation (CT-11, design Section 8.6)**.
  - `GET /api/reputation` (each account, for itself; system surface): `{"level": "good" | "limited" | "low", "fiction_share", "fiction_like_documents", "visible_documents", "approval_rate", "decisions", "limits": {"pending", "auto_documents"}, "documents": [{"document", "title", "theme", "theme_label"}]}`. `documents` holds the fiction-like documents that count. `limits.pending` is the number of pending contributions of the account in one shared knowledge base; `limits.auto_documents` is the number of documents of one run and target of the automatic contribution (`null`: no limit; 0: paused).
  - Admin: `GET /api/admin/reputations` (`settings` and `accounts`: each active account with its signals) and `PUT /api/admin/reputations/settings` (keys of `reputation.SETTINGS`; 400 `settings_invalid`).
  - `POST /kb/{number}/contributions` answers 409 `pending_limit` when the level of the requester is `low` and its pending contributions in the knowledge base are at the limit. With `low` the answer has the state `pending`.
  - A reviewer gets `contributor_reputation` `{"level", "approval_rate"}` on each contribution of `GET /kb/{number}/contributions`.
  - The report of an ingest has `notices`: `[{"code": "fiction_like_document", "document", "theme", "theme_label", "message"} | {"code": "fiction_standing_message", "message"}]` (design Section 8.7).
- **[코드] Curation and fork (CT-7, design Sections 9.7 and 9.8)**.
  - `POST /kb/{number}/curations` (scope `owner`, capability `curate`): body `{"source": "<number of S>", "documents": ["<document ID of S>", ...]}`. 201 `{"ok": true, "curations": [{"document", "contribution"?, "error"?, "url"?}]}`; 200 when no document was copied. Errors of the request: 400 `documents_invalid` (with `limit` for too many), `source_invalid` (S is K); 404 `source_not_found` (no knowledge base, or a private personal one); 409 `source_not_in_theme`. Errors of one document: `document_not_found` (not there, hidden, or not a visible contribution of a shared S), `contribution_duplicate` (with `url`), `contribution_tombstoned`, `document_not_extracted`.
  - `POST /kb/{number}/forks` (scope `owner`; each account that reads the shared knowledge base, a banned one included): 201 `{"ok": true, "knowledge_base": {...}, "forked_from": "<number>", "documents", "entities", "relations"}`. `POST /@{u}/forks`: 403 `kb_fork_shared_only` when the requester reads the personal knowledge base, else 404. 500 `fork_failed`: the copy did not complete and the new knowledge base is deleted.
- **[코드] Contribution to a theme (CT-6, design Sections 9.2 and 9.3)**. A submission belongs to the account of the credential; the account prefix of the path has no effect.
  - `POST /submissions` (scope `write`): body `{"documents": ["<document ID>", ...], "theme"?: "<theme number>"}`. 201 `{"ok": true, "submission": {...}}`. Errors: 400 `documents_invalid` (not a list of IDs, an empty list, an ID two times, or more IDs than the limit; with `limit`); 404 `document_not_found` (with `document`; not in the personal knowledge base of the requester), `theme_not_found` (not an active theme).
  - `GET /submissions` (a collection with `submissions`) and `GET /submissions/{number}`: the submissions of the requester; 404 for a submission of another account. A submission is `{"number", "created_at", "items": [{"document", "title", "theme", "theme_label", "routed_by": "account" | "server", "score", "targets": [{"knowledge_base", "label", "state", "contribution"?, "approved"?, "reason"?}], "code"?}]}`. `title` is the title of the source document. `code` is `no_theme` when the item has no theme. The state of a target is the state of its contribution (`pending`, `merging`, `visible`, `hidden`, `rejected`, `removed`, `failed`), `skipped` with `reason` (`contribution_duplicate`, `contribution_tombstoned`, `document_not_extracted`, `pending_limit`), or `withdrawn`.
  - A reviewer gets `review_help` on each contribution of a submission in `GET /kb/{number}/contributions`: `{"other_decisions": [{"knowledge_base", "label", "decision": "pending" | "approved" | "rejected" | "removed", "reason"}], "theme_score", "routed_by", "kb_score"}`.
- **[코드] Themes (CT-5, design Sections 8.2 to 8.4)**.
  - `GET /themes/{number}/` (each reader): JSON `{"number", "url", "label", "description", "knowledge_type", "state", "top_entities", "shared_knowledge_bases", "visible_contributions", "last_activity_at", "knowledge_bases": [{"number", "label", "is_personal", "knowledge_type", "url"}]}`, or an HTML page. A merged theme gets 308 to the theme it was merged into; an empty or unknown number gets 404; a theme has no inner paths.
  - Admin (system surface, 404 for each other account): `GET /api/admin/themes` (`settings`, `last_run_at`, `decision_agreement` `[{"knowledge_base", "label", "decisions", "agreement"}]`, and each theme with `label_locked`, and `pinned`, `excluded`, `source`, `score` of each member); `POST /api/admin/themes` `{"label", "knowledge_type"}` (201); `PUT /api/admin/themes/settings` with `theme_match_threshold`, `document_match_threshold`, `seed_match_threshold` from 0 to 1, and `submission_max_documents`, an integer of 1 or more (400 `settings_invalid`; `settings` of `GET /api/admin/themes` has the four keys); `POST /api/admin/themes/judge` (`report`); `PATCH /api/admin/themes/{number}` `{"label"}` or `{"label_locked": false}`; `PUT /api/admin/themes/{number}/knowledge-bases/{kb}` `{"membership": "pinned" | "excluded" | "computed"}` (409 `knowledge_type_mismatch`, `theme_merged`); `POST /api/admin/themes/{number}/merge` `{"into": "<number>"}` (409 `knowledge_type_mismatch`, `theme_merge_invalid`).
- **[미구현]** 테마 기여·제출(CT-6), 큐레이션·포크(CT-7), 콘솔 화면 `/settings/kb`·`/admin/kb`·`/admin/themes`(CT-9)는 뒤 단위다.

## Knowledge federation: known servers and inbox journal (KF1)

Basis: unit KF1 (2026-11-17). The behaviour is in [KNOWLEDGE_FEDERATION.md](KNOWLEDGE_FEDERATION.md) §10; the internal routes of the sidecar are there too (§10.4). Each route is `admin` (`_system("admin", "none")`): each other principal gets 404. The responses have `Cache-Control: no-store`. A `POST` needs the same origin (403) and a session with a recent sign-in (401 `reauth_required`, 403 `step_up_requires_session`).

| Method | Path | Success | Other responses |
|---|---|---|---|
| `GET` | `/api/admin/federation/known-servers` | 200 `{"servers": [{"domain", "status": "active" \| "blocked", "blocked_by", "source", "created_at", "last_contact_at", "server_actor", "software_name", "software_version", "kb_count", "last_read_at", "error_count", "last_error", "last_error_at"}], "switches": {"federation": "on" \| "off", "kb_auto_contribution": "on" \| "off", "signed_get": "required"}}`, in the order of the domain | 404 |
| `POST` | `/api/admin/federation/known-servers` | Body `{"domain": "<host name>"}` (one key). 201 `{"server": {...}, "created": true}` and `admin_audit` `federation.known_server_add`; 200 `{"server", "created": false}` for a known server (it does not change) | 400 `invalid_request`, `invalid_domain`, `own_domain`; 401; 403; 404 |
| `GET` | `/api/admin/federation/journal` | `?status=received\|processed\|failed\|refused`, `?limit=` (1 to 200, default 50), `?max_id=` (the rows with a smaller ID). 200 `{"entries": [{"id", "activity_iri", "activity_type", "kind", "actor", "actor_domain", "recipient", "verified_by", "status", "error_code", "error_detail", "attempts", "received_at", "processed_at", "replayable"}], "next_max_id": <int> \| null}`, newest first; times are ISO 8601 in UTC | 400 `invalid_status` (with `states`), `invalid_limit`, `invalid_max_id`; 404 |
| `GET` | `/api/admin/federation/journal/{id}` | 200 `{"entry": {..., "payload": <the activity as it came>}}` | 404 `journal_entry_not_found` |
| `POST` | `/api/admin/federation/journal/{id}/replay` | Body `{}`. 200 `{"entry": {...}}` with the new state, and `admin_audit` `federation.journal_replay` | 400 `invalid_request`; 401; 403; 404 `journal_entry_not_found`; 409 `journal_entry_not_replayable` (with `states`: `failed`, `received`); 409 `federation_off` (the row does not change) |

