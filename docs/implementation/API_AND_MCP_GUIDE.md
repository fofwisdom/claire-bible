# Claire Bible REST API 및 MCP 연결 가이드 (통합 구현본)

- **상태**: **구현 완료 (REST API + M1 MCP 툴셋 + OAuth 2.1 표준 인가 서버)**
- **기준 커밋**: `d15ae8e0` (2026-11-16) 및 2026-11-22 작업 트리(§2.7 결정 GD4–GD8 반영). 1.1·1.7·3절은 `d15ae8e0` 코드 기준으로 재검토했고, 그 외 절의 확인 기준은 2026-10-16이다.
- **Design principles**: [`MCP_SUPPORT.md`](../design/MCP_SUPPORT.md) (decisions of MCP and OAuth, and the open proposals MP1 to MP4)
- **기준 프로토콜 및 사양**:
  - OpenAPI 3.1.0 ([`openapi.yaml`](openapi.yaml))
  - Model Context Protocol (MCP) Streamable HTTP Transport (JSON-RPC 2.0)
  - HTTP 경로 ([`API_HTTP_CONTRACT.md`](API_HTTP_CONTRACT.md))
  - OAuth 2.0 / 2.1 RFC 계열:
    - RFC 6749 (The OAuth 2.0 Authorization Framework) / RFC 6750 (Bearer Token Usage)
    - RFC 7636 (Proof Key for Code Exchange by OAuth Public Clients, PKCE S256)
    - RFC 7591 (OAuth 2.0 Dynamic Client Registration Protocol)
    - RFC 8414 (OAuth 2.0 Authorization Server Metadata)
    - RFC 9728 (OAuth 2.0 Protected Resource Metadata)
    - RFC 8693 (OAuth 2.0 Token Exchange, §2.5)

---

## 1. 개요 및 연결 기본 원칙

Claire Bible은 지식 그래프 및 문서 보관소를 외부 애플리케이션, 개발자 스크립트, 그리고 외부 AI 에이전트(Claude, Google Antigravity, Cursor 등 OAuth 2.1/Streamable HTTP 및 공식 마켓플레이스 `fofwisdom/claire-agents` 지원 클라이언트)에서 안전하게 활용할 수 있도록 **표준 REST API**와 **Model Context Protocol (MCP)** 엔드포인트를 단일 ASGI 프로세스(`claire_api`) 내에 통합 제공한다.

### 1.1 접속 주소 (Base URL) 및 Host Authority
- **개발 환경 (`development`)**: `http://<CB_API_BIND>:<CB_API_PORT>` (예: `http://127.0.0.1:8765` 또는 `http://192.168.10.25:8766`)
- **프로덕션 환경 (`production`)**: `https://<CLAIRE_FQDN>` (예: `https://claire.example.com` 또는 `https://cb.netspheres.org`)
- **Host roles** [code] (`HostRouter` in `src/claire/api/host_router.py`):
  - The `Host` header (with the port) selects the role: `app` (the API and `/mcp`), `usercontent`, `media`, `prototype`, and `legacy` (old theme FQDNs: before the sunset `GET` and `HEAD` get 308 to the canonical path, other methods get 421; after the sunset 410).
  - A host name or IP address that has no role gets HTTP `421 Misdirected Request`.
  - 역방향 프록시(Nginx, Cloudflare 등) 구성 시 클라이언트가 요청한 `Host` 헤더를 백엔드로 변조 없이 전달해야 한다. (상세 네트워크 구성은 [`EXTERNAL_ACCESS.md`](EXTERNAL_ACCESS.md) 참조)

### 1.2 인증 스코프 (RBAC) 4계층
Claire Bible은 4단계 역할 기반 접근 제어를 제공한다:

$$\text{owner} \succ \text{write} \succ \text{readonly} \succ \text{anonymous}$$

The scope `write` replaces `collaborator` (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 6.6). It is for the tools of an account: an AI agent, a script, a bot. The rules of each scope and of each route are in [`THEMES.md`](THEMES.md) §4.1. The Telegram command `/webco` and the variable `CLAIRE_COLLABORATOR_TOKEN` are removed; an old token with the scope `collaborator` becomes `readonly`.

| 역할 (Scope) | 자격 증명 (토큰 / 헤더) | 기본 테마 (`seq=0`) | 추가 테마 (공개) | 추가 테마 (비공개) | 테마 관리 / 진단 번들 |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **시스템 관리자 (`owner`)** | OAuth 2.1 / 세션(`claire auth`, 텔레그램 `/web`), 또는 지원 중단 예정 상시 토큰(`cb_...`, `env:owner`) | 읽기 / 쓰기 | 읽기 / 쓰기 | 읽기 / 쓰기 | 가능 (`define`, `update`, `delete`, `bundle`, `tokens`) |
| **Write (`write`)** | OAuth 2.1 `write` 스코프 토큰(권장, `AE2a`) 또는 `claire token create <name> --scope write`(지원 중단 예정) | Personal knowledge base: read, ingest, client extraction (`AE5`), regenerate, publish | Shared knowledge base: read, contributions | — | No |
| **읽기 전용 (`readonly`)** | OAuth 2.1 `readonly` 스코프 토큰(권장) 또는 텔레그램 `/webro` 세션 / 상시 토큰(`cb_...`, `env:readonly`, 지원 중단 예정) | 읽기 전용 | 읽기 전용 | 읽기 전용 | 불가 |
| **익명 사용자 (`anonymous`)** | 자격증명 없음 (`CLAIRE_ANONYMOUS_READONLY=1`) | 공개 읽기<br>(숨김 문서 제외) | 공개 읽기<br>(숨김 문서 제외) | **404 Not Found**<br>(존재 은닉) | 불가 |

### 1.3 인증 아키텍처 전환: OAuth 2.1 기본화 및 수동 상시 토큰 지원 중단 (`AE2a`/`AE2b`/`AE3`)
- **OAuth 2.1 우선 권장**: AI 클라이언트와 도구는 `/account/mcp`에서 관리하는 OAuth 2.1(`S256` PKCE 또는 RFC 8628 `/device`) 사용을 권장한다(§3.1 방법 3).
- **레거시 상시 이용 토큰(`data/auth/api_tokens.json`, `Deprecated`)**: 기존 정적 환경변수(`CLAIRE_INJECT_TOKEN`, `CLAIRE_READONLY_TOKEN`)와 `data/auth/api_tokens.json` 상시 토큰(`cb_...`)은 OAuth 2.1로 대체되어 지원 중단 예정이다. 수동 토큰 Bearer 인증과 `/tokens*` 경로는 `Deprecation: true` 및 `Link: </account/mcp>; rel="successor-version"` 헤더를 반환하며, `TokenStore.set_retired(True)`(`api_tokens.json` `"retired": true`) 또는 `CLAIRE_MANUAL_TOKENS_RETIRED=1` 설정 시 신규 발급은 `410 Gone`(`manual_tokens_retired`)을 반환하고 파일 기반 수동 토큰 검증이 중단된다.
- **토큰 관리 인터페이스**:
  - **OAuth 2.1 인가 관리**: 콘솔 `/account/mcp`, API `/api/me/oauth/grants`, `POST /oauth/revoke`
  - **레거시 상시 토큰 관리 (Deprecated)**: `claire token [list|create|revoke]`, 콘솔 `/account/tokens`(관리자 `/admin/tokens`), 텔레그램 `/token`, REST API `/tokens*`
- **RFC 6750 Bearer 토큰 사양**: 레거시 상시 토큰은 `cb_` 접두사를 가지며, OAuth 2.1 액세스 토큰·세션과 함께 `Authorization: Bearer <TOKEN>`으로 전송된다.

### 1.4 자격증명 채널 및 상호 배타성 규약
인증 시 다음 세 채널 중 **정확히 하나만** 사용해야 한다:
1. **Bearer 토큰**: `Authorization: Bearer <token>`
2. **세션 헤더**: `X-Session: <token>`
3. **세션 쿠키**: `Cookie: claire_session=<token>` (Same-Origin 브라우저 전용)

> [!CAUTION] **자격증명 다중 제공 시 즉시 거부 (Mutual Exclusivity)**
> 하나의 요청에 둘 이상의 인증 채널이 동시에 존재할 경우(예: Bearer 헤더와 Cookie가 동시 포함됨), 자격증명 혼선 및 토큰 하이재킹 공격을 원천 방지하기 위해 요청을 즉시 거부한다:
> - Same-Origin / 일반 요청: HTTP `404 Not Found` (스텔스 거부)
> - Cross-Origin 요청: HTTP `403 Forbidden`
> - `/mcp` 엔드포인트: HTTP `401 Unauthorized` (`WWW-Authenticate: Bearer`)
>
> 또한 비표준 헤더인 `X-Token`은 사용이 엄격히 금지되며 감지 시 즉시 거부된다. Cross-Origin(CORS) 요청은 오직 `Authorization: Bearer` 방식만 허용된다.

### 1.5 스텔스 모드 (Fail-Closed Invariant)
`CLAIRE_ANONYMOUS_READONLY=0`(프라이빗/스텔스 모드)로 구동되는 서버 또는 비공개 테마(`is_public=false`)에 대해 자격증명이 없는 읽기 요청이 인입될 경우, 외부 정찰에 엔드포인트나 테마의 존재 여부를 일절 노출하지 않기 위해 `401 Unauthorized` 또는 `403 Forbidden` 대신 **HTTP `404 Not Found`**를 반환한다.

### 1.6 요청 크기 제한
모든 API 및 MCP 요청 본문은 최대 1MB (`1,048,576` bytes)로 하드 제한된다. 초과 시 즉시 HTTP `413 Request Entity Too Large`로 차단된다.

### 1.7 지식 베이스 선택 (CT23)
- **경로**: 네임스페이스 라우트는 경로가 지식 베이스를 정한다. `/themes/{number}/…`(네트워크 모드는 `/@{u}/themes/{number}/…`)는 그 지식 베이스, 고정이 없으면 계정의 개인 지식 베이스다.
- **`kb` 매개변수**: 네임스페이스가 없는 라우트와 MCP 읽기 도구는 `kb`(번호)가 필수다. 없으면 HTTP 400 `kb_required`, MCP `{"error": "kb_required"}`. 내부 ID와 레이블은 받지 않는다.
- **적재**는 자격증명 계정의 개인 지식 베이스에만 쓴다.
- 옛 `X-Claire-Theme` 헤더와 `theme` 쿼리·본문·MCP 인자는 무시한다.

---

## 2. REST API 연결 및 엔드포인트 가이드

모든 REST API 요청은 JSON 본문을 사용하며(`Content-Type: application/json`), 응답 역시 표준 JSON 형식이다.

### 2.1 서비스 상태 및 신원 확인

#### `GET /health` (Public)
서비스 Liveness 및 데이터베이스 연결 상태를 확인한다.
```bash
curl -s https://claire.example.com/health
```
```json
{"ok": true}
```

#### `GET /whoami` (Read)
현재 요청에 적용된 인증 스코프, 서버 모드, 네임스페이스와의 관계(`owner`·`delegate`·`visitor`), 관리자 여부를 확인한다. `is_admin`은 관리자 계정의 owner 자격증명일 때만 `true`다. 응답에 USERNAME은 없다.
```bash
curl -s -H "Authorization: Bearer <TOKEN>" https://claire.example.com/whoami
```
```json
{"scope": "owner", "server": {"mode": "single"}, "relation": "owner", "is_admin": true}
```

#### `GET /stats` (Read)
지식베이스 규모(문서 수, 엔티티 수, 관계 수, 소스 타입별 통계 등)를 조회한다.
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
        https://claire.example.com/stats
```
```json
{
  "kb_id": 0,
  "theme_label": "기본 지식베이스",
  "documents": 142,
  "entities": 853,
  "relations": 1920,
  "observations": 3410,
  "unseen_documents": 12
}
```

---

### 2.2 지식 검색 및 조회 API

#### `POST /search` (Read)
FTS5 전문 검색 및 하이브리드 지식 검색을 수행한다.
- **헤더**: `Content-Type: application/json`, 인증 헤더
- **본문 필드**:
  - `query` (str, 필수): 검색 질의문
  - `limit` (int, 선택, 기본 8): 최대 반환 건수
  - `entity_type` (str, 선택): 특정 엔티티 타입 필터링
  - `near_ids` (list[str], 선택): 지정 엔티티 노드의 1홉 이웃 범위로 검색 공간 한정
- **호출 예시**:
```bash
curl -s -X POST https://claire.example.com/search \
     -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"query": "Transformer attention mechanism", "limit": 5}'
```

#### `GET /documents` (Read)
아카이브된 문서 목록을 키셋(keyset) 페이지로 조회한다. 정렬은 `fetched_at` 내림차순, 같으면 `id` 내림차순이다. 파라미터가 없으면 최신 300건을 돌려준다.
- **쿼리 파라미터**:
  - `limit` (int, 선택, 기본 300, 1–300): 한 페이지 건수
  - `before` (str, 선택): 이전 응답의 `next_before` 커서(`<fetched_at>:<id>`). 이 커서 뒤의 문서만 돌려준다
  - `pinned`, `hidden` (`0`|`1`, 선택): 즐겨찾기·숨김 여부 필터
  - `q` (str, 선택, 200자 이하): 제목 또는 최신 추출 요약의 부분 일치. 대소문자 무시는 ASCII 문자에만 적용된다
  - `detail` (`missing`|`mismatched`, 선택): 상세 본문이 없거나 렌더링 포맷이 다른 문서
  - `id` (str, 선택, 반복 가능, 100개 이하): 지정한 문서만 조회
  - `theme` (str/int, 선택): 대상 테마 ID 또는 라벨
- **응답 필드**: `documents`, `format_status`, `kb_id`, `theme_label`, `next_before`(다음 페이지 커서, 마지막 페이지면 `null`), `total`(커서를 뺀 필터 일치 건수)
- 잘못된 값은 `400`과 고정 오류 문구를 돌려준다. `anonymous` 범위는 어떤 페이지와 필터에서도 숨김 문서를 받지 않고, `total`에도 세지 않는다.
- MCP 도구 `documents`의 인자(`limit`, `since`, `query`)는 이 HTTP 경로와 다르다.
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/documents?limit=31&q=OAuth"
# 다음 페이지: 앞 응답의 next_before 값을 before로 보낸다
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/documents?limit=31&q=OAuth&before=<next_before>"
```

#### `GET /document` (Read)
문서 1건의 상세 정보(원문 URL, 제목, 요약, 상세 본문, docmeta 등)를 조회한다.
- **쿼리 파라미터**: `id` (str, 필수, 문서 식별자), `theme` (선택)
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/document?id=doc_12345678"
```

#### `GET /graph` (Read)
전체 또는 필터링된 온톨로지 지식 그래프 데이터(nodes, links)를 JSON으로 조회한다.
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/graph"
```

#### `GET /node` (Read)
특정 엔티티 노드의 상세 속성, 관찰(observations), 연결된 1홉 관계 및 소스 문서 목록을 조회한다.
- **쿼리 파라미터**: `id` (str, 필수), `full` (bool, 선택, 기본 false), `docs` (선택, `brief`), `theme` (선택)
- `docs=brief`: 소스 문서마다 `id`, `title`, `url`, `summary`, `fetched_at`, `has_detail`, `source_type`, `has_raw_file`만 돌려준다(`detail`, `detail_format`, `detail_html` 제외). WebUI 상세 패널의 `출처 문서` 목록이 쓴다. 기본 응답은 바뀌지 않는다.
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/node?id=ent_oauth21_pkce"
```

---

### 2.3 지식 적재 (Ingest) API

#### `POST /ingest` (Collaborator / Owner)
새로운 웹 URL 또는 텍스트 본문을 지식베이스에 적재하고 온톨로지 지식그래프를 추출한다.
- **헤더**: `Content-Type: application/json`, `Authorization: Bearer <CLAIRE_INJECT_TOKEN>` (또는 세션 헤더)
- **본문 필드**:
  - `url` (str, 선택): 수집할 대상 웹 페이지/PDF/동영상 URL
  - `text` (str, 선택): 직접 적재할 텍스트 본문 (URL이 없는 경우 필수)
  - `title` (str, 선택): 문서 제목
  - `theme` (str/int, 선택): 적재 대상 테마
```bash
curl -s -X POST https://claire.example.com/ingest \
     -H "Authorization: Bearer <CLAIRE_INJECT_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://datatracker.ietf.org/doc/html/rfc7636",
       "title": "RFC 7636 - PKCE for OAuth Public Clients",
       "theme": 0
     }'
```

#### `POST /ingest-stream` (Collaborator / Owner)
긴 적재 파이프라인의 진행 상태를 NDJSON(Newline Delimited JSON)으로 실시간 스트리밍 수신한다.

---

### 2.4 원격 진단 번들 (Support Bundle) API

시스템 텔레메트리, 인박스 실패 내역 및 추적 정보를 안전하게 추출하기 위한 엔드포인트이다.

#### 1) 생성: `POST /support/bundle` (Owner 전용)
```bash
curl -s -X POST https://claire.example.com/support/bundle \
     -H "Authorization: Bearer <CLAIRE_INJECT_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"days": 1}'
```
```json
{
  "bundle_id": "bundle_20261016_abcd1234",
  "token": "sec_tok_xyz987...",
  "expires_at": "2026-10-16T20:30:00Z",
  "download_url": "https://claire.example.com/support/bundle?token=sec_tok_xyz987..."
}
```

#### 2) 다운로드: `GET /support/bundle?token=<token>` (Public)
발급된 6시간 유효 토큰으로 `.tar.zst` 압축 아카이브를 다운로드한다:
```bash
curl -O -J "https://claire.example.com/support/bundle?token=sec_tok_xyz987..."
```

---

### 2.5 RFC 8693 OAuth 2.0 Token Exchange

계정에 귀속된 세션 토큰을 같은 계정의 `readonly` Bearer 토큰으로 교환한다. 주입 토큰이나 계정에 귀속되지 않은 토큰은 400 `invalid_grant`로 거부한다. (`application/x-www-form-urlencoded` 사용)
```bash
curl -s -X POST https://claire.example.com/oauth/token \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
     -d "subject_token=<SESSION_TOKEN>" \
     -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
     -d "scope=readonly"
```

확인 기준: 계정·IdP·Fedify 준비 봉인 커밋 `7bc536f` (2026-11-09), `src/claire/api/oauth.py`의 `handle_token`. 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다.

- **[코드] 받는 토큰**: `subject_token`은 `validate_session_binding`(`src/claire/store/accounts.py`)이 계정 귀속을 확인할 수 있는 승인된 세션 토큰(`auth_sessions`)이어야 한다. 없거나, 만료되었거나, 계정에 귀속되지 않은 토큰은 400 `invalid_grant`다. `subject_token`이 비어 있으면 400 `invalid_request`다.
- **[코드] 계정 상태**: 귀속 계정이 없거나 상태가 `CREDENTIAL_ACCEPTING_STATUSES`(`setup_required`, `active`)가 아니면 400 `invalid_grant`다.
- **[코드] 발급 토큰**: 그 계정에 귀속된 `readonly` OAuth 접근 토큰과 갱신 토큰이다. `expires_in`은 `OAUTH_ACCESS_TOKEN_TTL`(30일, `2592000`)이다. 클라이언트 식별자는 `token_exchange_client`다.
- **[코드] 읽지 않는 매개변수**: `subject_token_type`, `scope`, `resource`, `audience`, `requested_token_type`, `actor_token`, `actor_token_type`의 값은 판정에 쓰지 않는다. 요청한 `scope`와 무관하게 `readonly`만 발급한다.
- **[코드] 폴백 없음**: 계정을 정하지 못한 요청을 관리자 계정(`accounts.id = 1`)으로 발급하는 분기는 없다(PRINCIPLES C1). 집행: `tests/test_oauth_token_exchange.py`.
- **[결정] Fedify 사이드카는 토큰 교환을 쓰지 않는다.** 사이드카에는 계정이 없으므로 계정 귀속 토큰을 받을 수 없다. 사이드카는 내부 연합 API 전용 서비스 자격증명(`data/auth/fedify_service.key`로 서명한 요청)을 쓴다([설계 원칙](../design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) "Decisions in Force" 4, 구현: [FEDERATION.md](FEDERATION.md) §5.2).

---

### 2.6 API 및 MCP 상시 이용 토큰 관리 API (`/tokens`, Deprecated)

> [!WARNING] **수동 토큰 지원 중단 예정 (`AE3`)**
> - 상시 이용 수동 토큰(`cb_...`, `data/auth/api_tokens.json`) 및 환경변수 토큰(`env:*`)은 OAuth 2.1(`AE2a`/`AE2b`, 콘솔 `/account/mcp`)로 대체되었으며 지원 중단(Deprecated) 표시됩니다.
> - 수동 토큰 Bearer 인증 응답 및 `/tokens`·`/tokens/revoke` 응답에는 `Deprecation: true`와 `Link: </account/mcp>; rel="successor-version"` 헤더가 포함되며, JSON 응답에도 `"deprecated": true` 및 안내 문구가 포함됩니다.
> - `TokenStore.set_retired(True)` 또는 `CLAIRE_MANUAL_TOKENS_RETIRED=1` 활성화 시 `POST /tokens`는 `410 Gone`(`manual_tokens_retired`)을 반환하고 수동 Bearer 검증이 즉시 비활성화됩니다.

외부 AI 도구(Antigravity, Cursor 등) 및 CI/CD 자동화 파이프라인에서 사용하던 레거시 Bearer 접근 토큰을 발급·조회·폐기하는 엔드포인트이다. 신규 연동은 §3.1 방법 3의 OAuth 2.1을 권장한다.

> [!IMPORTANT] **`/tokens` 계정 격리 (Account Isolation, Rev. 2.3)**
> - `/tokens`, `/tokens/revoke`, `DELETE /tokens`는 라우트 정책 상 `surface="any"`, `access="owner"` 규칙을 가지며, 항상 요청 주체의 `Principal.account_id`로 엄격히 필터링된다.
> - `TokenStore`는 토큰 생성 시 호출자의 `account_id`를 바인딩하여 영속화(`data/auth/api_tokens.json`)하며, 조회(`list_api_tokens`, `get_api_token`) 및 폐기(`revoke_api_token`) 시 본인 계정에 귀속된 토큰만 처리한다.
> - 관리자(`account_id=1`)를 포함하여 어떤 계정도 타 계정의 토큰을 조회하거나 폐기할 수 없다 (K5, D5 계정 경계).

#### 1) 토큰 목록 조회: `GET /tokens` (Owner 전용)
등록된 모든 토큰(활성, 만료, 폐기 포함)을 조회한다. 보안을 위해 원본 시크릿은 마스킹(`cb_abcd...wxyz`)되어 반환된다.
```bash
curl -s -H "Authorization: Bearer <OWNER_TOKEN>" https://claire.example.com/tokens
```
```json
{
  "deprecated": true,
  "tokens": [
    {
      "id": 1,
      "name": "Default Knowledge Manager (Owner)",
      "scope": "owner",
      "created_at": 1729065600.0,
      "expires_at": null,
      "last_used_at": 1729068000.0,
      "revoked_at": null,
      "token_masked": "cb_09af...d82a"
    },
    {
      "id": 2,
      "name": "Antigravity Agent",
      "scope": "readonly",
      "created_at": 1729066000.0,
      "expires_at": null,
      "last_used_at": 1729067500.0,
      "revoked_at": null,
      "token_masked": "cb_f38a...110b"
    }
  ]
}
```

#### 2) 신규 토큰 발급: `POST /tokens` (Owner 전용)
새로운 상시 이용 API/MCP 토큰을 발급한다. 발급 시점에만 전체 원본 시크릿 토큰(`token`)이 반환된다. `TokenStore.is_retired()`가 참(`CLAIRE_MANUAL_TOKENS_RETIRED=1` 또는 `api_tokens.json` `"retired": true`)이면 HTTP `410 Gone`(`manual_tokens_retired`)을 반환한다.
- **요청 본문 필드**:
  - `name` (str, 필수): 토큰 식별용 이름 (예: `"Agent Client"`, `"Cursor Agent"`)
  - `scope` (str, 선택, 기본 `"readonly"`): `"owner"`, `"write"`, `"readonly"` 중 선택
  - `expires_in_days` (int, 선택, 기본 `null`): 만료 일수 (생략 또는 `null` 지정 시 무기한 상시 이용)
```bash
curl -s -X POST https://claire.example.com/tokens \
     -H "Authorization: Bearer <OWNER_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Agent Client",
       "scope": "readonly",
       "expires_in_days": null
     }'
```
```json
{
  "ok": true,
  "deprecated": true,
  "message": "API/MCP 토큰이 성공적으로 생성되었습니다.",
  "token": {
    "id": 3,
    "token": "cb_xX9vY27z1234567890abcdefghijklmno",
    "name": "Agent Client",
    "scope": "readonly",
    "created_at": 1729069000.0,
    "expires_at": null,
    "last_used_at": null,
    "revoked_at": null
  }
}
```

#### 3) 토큰 즉시 폐기: `POST /tokens/revoke` 또는 `DELETE /tokens` (Owner 전용)
지정된 토큰을 즉시 폐기(Revoke)한다. 폐기 즉시 해당 토큰을 사용한 모든 API 및 MCP 요청은 `401 Unauthorized`로 차단된다.
- **요청 본문 필드**: `id` 또는 `token_id` (int/str, 필수)
```bash
curl -s -X POST https://claire.example.com/tokens/revoke \
     -H "Authorization: Bearer <OWNER_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{"id": 2}'
```
```json
{
  "ok": true,
  "message": "토큰이 성공적으로 폐기되었습니다."
}
```

### 2.7 연합 발행 API (`/api/federation/*`)

네이티브 연합 액터로 개인 지식 베이스 문서를 발행하거나 철회한다. 세 경로 모두 싱글 모드와 네트워크 모드에서 동일하게 동작한다(`ALL_MODES`). `active` 상태이고 USERNAME이 있는 계정은 `profile_public`과 무관하게 모두 연합 액터이며(결정 GD5), 서버 전체 연합 스위치는 없다(결정 GD4). 결정 GD8(규칙 N1 철회)에 따라 노트 작성·편집·삭제, 좋아요, 부스트, 인용뿐 아니라 개인 지식 베이스 문서 발행(`POST /api/federation/publish`)도 `profile_public`에 구애받지 않고 활성 액터 상태와 대상 개인 지식 베이스·문서의 공개 여부(`theme.is_public`, `not document.hidden`)만으로 허용된다(`409 profile_private` 없음, [`FEDERATION.md`](FEDERATION.md) 3.1절). `/api/federation/status`의 `discoverable` 필드는 액터의 `Person.discoverable` 불리언(`bool(account.profile_public)`)을 나타낸다. 전체 규칙은 [`FEDERATION.md`](FEDERATION.md) 7.5–7.7절, 스키마는 [`openapi.yaml`](openapi.yaml)에 있다.

| 메서드 | 경로 | 스코프 | 기능 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/federation/status` | `owner` | 핸들, 액터 상태, `discoverable`(액터의 `Person.discoverable` 불리언, `bool(profile_public)`), 팔로워 수, 대기 중인 작업 수 |
| `POST` | `/api/federation/publish` | `write` | 본문 `{"kb": "<개인 지식 베이스 번호>", "document_id": "<문서 ID>"}`. 공개 개인 지식 베이스의 숨기지 않은 추출 완료 문서만 발행한다. 처음이면 `create`, 다시 호출하면 같은 객체의 `update` 작업을 만든다. 응답 202 |
| `POST` | `/api/federation/retract` | `write` | 본문 `{"object_id": "<객체 ID>"}`. 객체를 지우고 팔로워에게 `Delete`를 요청한다. 응답 202 |

- 발행은 비동기다. 202는 Claire가 객체와 작업을 저장했다는 뜻이고, 사이드카가 5초 안에 작업을 가져가 팔로워의 서버로 전달한다.
- 오류 코드: 400 `invalid_request`, 404 `theme_not_found`·`document_not_found`·`object_not_found`, 409 `not_an_actor`·`theme_not_public`·`document_not_public`·`document_not_extracted`·`document_has_no_body`.
- 철회와 개인 지식 베이스 비공개 전환은 원격 서버에 삭제를 **요청**한다. 원격 서버가 사본을 지운다는 보장은 없다.
- `readonly` 스코프는 발행할 수 없다. 관리자도 다른 계정을 대신해 발행할 수 없다.

---

### 2.8 클라이언트 추출 스테이징 및 제출 API (`/api/client-extraction/*`, `AE5`)

계정의 `extraction_mode = 'client'`(`PATCH /api/me`, `AE4`)이거나 클라이언트가 원문을 직접 스테이징·추출할 때 사용하는 HTTP REST API다 (`src/claire/api/server.py`, `src/claire/ingest/client_extraction.py`, 스키마 `"agent-extract-v1"`). 모든 경로는 호출자의 개인 지식베이스만 대상으로 하며(`CT4`), 타인 `kb`·`kb_id`·`theme` 매개변수는 거부한다. 세부 4계층 검증 및 옵션 B 서버 임베딩 파이프라인은 [`EXTRACTION.md`](EXTRACTION.md) §9에 있다.

| 메서드 | 경로 | 최소 스코프 | 요청 매개변수 / 본문 | 응답 / 주요 오류 |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/client-extraction/stage` | `write` | JSON `{url_or_text \| url \| text, title?, source_type?, full_content?: bool (기본 true), mode?}`, JSON `{filename, content_base64, ...}`, 또는 `multipart/form-data` `file` (PDF·DOCX·ODT·HTML·MD·TXT·RST·ADOC, 최대 20 MiB) | 200 `{document_id, kb_id, content_hash, raw_chars, extraction_status: "pending", ...}` / 400 `invalid_full_content`·`file_required` / 413 `file_too_large` |
| `GET` | `/api/client-extraction/sources` | `write` | query `status` (기본 `"pending"`, `"all"` 허용), `limit` (기본 `50`) | 200 `{sources: [...], count}` |
| `POST` | `/api/client-extraction/sources/{document_id}/claim` | `write` | 선택 JSON `{client_id?, lease_seconds?: float (기본 300.0)}` | 200 `{document_id, claim_token, lease_expires_at, content_hash, ...}` / 400 `invalid_lease_seconds` / 409 임대 충돌 |
| `POST` | `/api/client-extraction/sources/{document_id}/renew` | `write` | JSON `{claim_token, lease_seconds?: float (기본 300.0)}` | 200 `{document_id, claim_token, lease_expires_at}` / 400 `invalid_lease_seconds` / 409 |
| `GET` | `/api/client-extraction/spec` | `readonly` | 없음 | 200 `schema_version`, 활성 온톨로지 타입·domain/range 규칙·AsciiDoc/제출 스키마 명세 |
| `GET` | `/api/client-extraction/sources/{document_id}` | `write` | query `claim_token?`, `offset?`, `limit?` (기본 `32000`자), `line_offset?`, `line_limit?`, `full_content?` (`true` 기본) | 200 nonce-bound `<untrusted_source_document nonce="..." document_id="..." sha256="...">` 프레이밍 원문 조각 + `expected_content_hash` |
| `POST` | `/api/client-extraction/sources/{document_id}/submit` | `write` | JSON `{claim_token, expected_content_hash \| content_hash, payload \| 인라인 필드, dry_run?: bool}` (`ConfigDict(extra="forbid", strict=True)`) | 200 `{ok: true, committed: !dry_run, extraction_origin: "agent_client", ...}` / 422 스키마·요약·AsciiDoc·온톨로지·근거 span 검증 실패 / 409 `content_hash_mismatch`·임대 불일치 |

---

## 3. MCP (Model Context Protocol) 연동 및 도구 가이드

Claire Bible은 외부 AI 에이전트가 지식베이스를 탐색할 수 있도록 **Model Context Protocol (MCP)** 공식 사양을 지원한다.

- **엔드포인트**: `POST /mcp` (Streamable / Stateless HTTP JSON-RPC 2.0, JSON responses). `ROUTE_POLICY` gives `GET`, `HEAD`, and `POST /mcp` the access level `read`. A call without a credential gets `401` with `WWW-Authenticate: Bearer ... resource_metadata="{public_origin}/.well-known/oauth-protected-resource"` [code] (`src/claire/api/security.py`).
- **임베딩 아키텍처**: 별도 컨테이너 없이 `claire_api` Starlette 프로세스 내부에서 직접 마운트 서빙.
- **LLM 쿼터 격리 (Zero Gemini Overhead)**:
  - 모든 MCP 도구는 **순수 SQLite 쿼리 및 FTS5 검색 엔진만 사용**한다.
  - 외부 에이전트의 잦은 탐색 루프 중에도 Google Gemini API 호출이나 토큰 소진이 일절 발생하지 않는다.
- **`ContextVar[Principal]` 기반 계정 격리**:
  - `AuthenticationMiddleware`가 인증한 주체는 `mcp_tools.current_principal` (`ContextVar[Principal | None]`)을 통해 MCP 도구 함수 실행 컨텍스트로 전달된다.
  - 모든 MCP 도구의 `_kb_id` 해석은 `current_principal`의 `account_id`의 개인 지식 베이스와 공유 지식 베이스(공개)로만 조회 및 접근을 제한한다. 타 계정 소유 테마 지정 시 존재를 은닉하기 위해 `None`을 반환한다.
- **DB 연결 분리 및 쓰기 가드**:
  - 읽기 도구의 `_conn`은 `dbm.connect_existing(db_file, readonly=True)`로 읽기 전용 연결을 열고 DDL(`init_db`)을 실행하지 않는다.
  - 쓰기 도구(`contrib_submit` 및 `AE5` 클라이언트 추출 쓰기 도구)는 `require_mcp_write_scope()`로 `write`/`owner` 스코프를 강제하고, `AE5` 도구는 호출자 개인 지식베이스만 허용한다(`CT4`). 또한 `mcp_telemetry.client_id`는 OAuth 인가(`oauth:<id>`), 상시 토큰(`api:<id>`), 세션/환경 채널(`env:*`) 단위로 호출 출처를 기록한다.
- **포트 보존 호스트 검증**:
  - MCP `TransportSecuritySettings`의 `allowed_hosts`에 포트를 보존한 호스트 값(`localhost:port`, `127.0.0.1:port`, `fqdn:port`)을 등록하여 비표준 포트 환경에서의 DNS 리바인딩 보호와 정상 통신을 양립한다.

---

### 3.1 MCP 인증 메커니즘

MCP 클라이언트는 다음 세 가지 방식 중 하나로 `/mcp`에 접속할 수 있다:

#### 방법 1: 데이터베이스 기반 상시 이용 토큰 (Persistent Bearer Token, Deprecated)
CLI(`claire token create`), WebUI(`/account/tokens`, 관리자는 `/admin/tokens`), 또는 텔레그램(`/token create`)에서 발급받은 영구/장기 토큰(`cb_...`)을 전달하는 레거시 방식이다 (`Deprecation: true` 및 `Link: </account/mcp>; rel="successor-version"` 헤더 반환). 새 연동은 아래 방법 3(OAuth 2.1)을 사용한다.
- **발급**: `claire token create "Agent Client" --scope readonly` (또는 `--scope write`)
- **헤더**: `Authorization: Bearer <PERSISTENT_TOKEN>`
- **퇴역 스위치**: `TokenStore.set_retired(True)` 또는 `CLAIRE_MANUAL_TOKENS_RETIRED=1` 설정 시 신규 발급이 `410 Gone`으로 차단되고 기존 토큰 검증이 거부된다.

#### 방법 2: 텔레그램 발급 세션 토큰 (X-Session)
텔레그램 봇(`/web`, `/webro`)에서 발급받은 임시 세션 토큰을 전달한다.
- **헤더**: `X-Session: <session_token>` 또는 `Authorization: Bearer <session_token>`

#### 방법 3: OAuth 2.1 표준 인가 서버 (Telegram-Free 브라우저 흐름 & RFC 8628 Device Grant)
Antigravity(`agy`), Google Gemini 맞춤형 연결 앱 및 최신 OAuth 2.1 호환 클라이언트를 위한 통합 플로우이다:

```
[외부 AI 클라이언트]                             [Claire Bible 인가 서버]
              │                                           │
  1. GET /mcp (미인증) ──────────────────────────────────>│ (401 + WWW-Authenticate)
  2. GET /.well-known/oauth-protected-resource (RFC 9728) │
  3. GET /.well-known/oauth-authorization-server (RFC 8414)
  4. POST /oauth/register (RFC 7591 동적 클라이언트 등록) ──>│ (201 Created, client_id/secret)
  5. GET /oauth/authorize (PKCE S256, scope=readonly|write)│
              ├─ [미로그인 시] -> oauth_pending_requests + HttpOnly oauth_tx 쿠키 -> 303 /login?tx=<tx_id>
              │                  (아이디/비밀번호 또는 OIDC 로그인 -> 303 /oauth/authorize?tx=<tx_id>)
              └─ [로그인 시]  -> Zero-JS 동의 화면 (`write` 스코프는 단계 인증 `step_up_password` 필요)
  6. POST /oauth/token (authorization_code + S256 / refresh_token / device_code) ─>│ (access_token + refresh_token)
  7. POST /oauth/revoke (RFC 7009) · GET/DELETE /api/me/oauth/grants              │ (토큰·패밀리 즉시 폐기)
```

- **영구 독립 세션 (`oauth_tokens` 테이블, v40)**:
  - The tables `oauth_clients`, `oauth_codes`, `oauth_tokens`, `oauth_auth_requests`, `oauth_pending_requests`, and `oauth_device_codes` are in `claire.db` [code] (`src/claire/store/db.py`).
  - 텔레그램 웹 세션과 완전히 격리되며, 텔레그램 봇이나 `account_id=1` 제한 없이 모든 활성 계정에서 브라우저 OAuth 또는 RFC 8628 `/device` 흐름을 완료할 수 있다.
- **스코프 영속화·단계 인증·Refresh 회전 및 재사용 탐지**:
  - `scopes_supported` is `["readonly", "write"]`, and only `S256` PKCE is accepted (`plain` rejected with `400 invalid_request`) [code] (`src/claire/api/oauth.py`).
  - Granted scopes are preserved across code/device/refresh exchanges (`granted_scope`); refresh requests may downscope (e.g. `readonly write` to `readonly`), while escalation returns `400 invalid_scope`.
  - Approving `write` scope requires recent session authentication (`STEP_UP_WINDOW_SECONDS`) or inline `step_up_password` verification (`allowCredentials` descriptors are exposed for WebAuthn step-up UI).
  - When a client requests `write`, the consent form of `/oauth/authorize` shows the checkbox `grant_write` (selected by default) with the marker `write_choice=1`. If the user clears it, the server issues the code with scope `readonly` only, needs no step-up, and returns `scope: "readonly"` in the token response (RFC 6749 Section 3.3). A form without the marker keeps the requested scope. The device flow (`/device`) has no such choice. Use: read-only connectors on chat surfaces ([`CLAUDE_AGENT_CLIENT_DESIGN.md`](../design/CLAUDE_AGENT_CLIENT_DESIGN.md) unit CL6).
  - Each `refresh_token` exchange marks the parent row `rotated_at` within a shared `token_family_id` and issues a new pair (`access_ttl` 30d, `refresh_ttl` 1y). Reusing an already-rotated refresh token revokes the entire token family immediately (`revoked_at`).
  - `POST /oauth/revoke` (RFC 7009) and `GET`/`POST`/`DELETE /api/me/oauth/grants` list active client authorizations and revoke individual grants or all grants for the account.
  - Headless fallback: `POST /oauth/device_authorization` returns `device_code` + `user_code` (`ABCD-EFGH`) and `/device` provides the zero-JS verification UI backed by `grant_type=urn:ietf:params:oauth:grant-type:device_code`.
- **다중 계정 귀속 (Account Binding, Rev. 2.3)**:
  - `validate_session_binding`으로 검증된 세션의 실제 `account_id`(`active` 상태 계정 필수)에 인가 코드(`oauth_codes`), 액세스 토큰, 리프레시 토큰(`oauth_tokens`)이 귀속된다.
  - 계정이 정지(`suspended`), 삭제 중(`deleting`), 소각(`purged`) 상태로 변경되면 리프레시 토큰 갱신 요청(`POST /oauth/token` `grant_type=refresh_token`) 시 즉시 `invalid_grant`로 거부된다.
- **CSRF 및 오리진 방어 (`modern-web-guidance`)**:
  - 동의/로그인/기기 화면에 `Referrer-Policy: same-origin` 및 zero-JS HTML(인라인 `<script>` 제거, 단색 SVG)을 강제한다.
  - `same_origin()`은 `Sec-Fetch-Site: same-origin` 및 일치하는 `Origin`과의 `none`만 허용하고 `same-site`·`cross-site`·opaque `"null"` 오리진은 거부한다.
  - `_is_safe_redirect_uri()` 및 `CORSPolicyMiddleware`는 `"null"` 오리진과 위험한 스킴(`javascript:`, `data:`, `vbscript:`, `file:`)을 거부한다.
  - `/oauth/authorize/telegram-push`는 관리자(`account_id=1`)용 선택적 레거시 푸시 경로로만 유지된다.

---

### 3.2 제공 툴셋 (17종 도구, 서버 `"claire"` `v0.2.0`)

`MCPServer("claire", title="Claire Bible", version="0.2.0", instructions=...)`가 `<domain>_<action>` 규칙의 17종 도구와 MCP `ToolAnnotations`(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`)를 제공한다. 모든 읽기·그래프·문서 도구(`kb_list` 제외)는 `kb`가 필수이며(`kb="personal"` 또는 `kb_list()`가 돌려주는 18자리 번호, `CT23`), 알 수 없거나 접근 권한이 없는 지식베이스는 `kb_not_found`로, 없는 항목은 `not_found`로 통일해 반환한다. 클라이언트 추출 도구는 호출자 개인 지식베이스만 대상으로 하며(`CT4`), 쓰기 작업은 `require_mcp_write_scope()`로 `write` 또는 `owner` 스코프를 검사한다.

#### 1) 읽기 전용 탐색 도구 (9종)

| 툴 이름 | 입력 파라미터 | 주요 역할 및 특징 |
|---|---|---|
| `kb_list` | (없음) | 호출자의 개인 지식베이스(`kb="personal"` / 18자리 번호)와 접근 가능한 공유 지식베이스 목록 및 역할 반환. |
| `kb_overview` | `kb: str\|int`, `include_stats: bool = True` | 엔티티·원천 유형 분포, 주요 허브 노드, 교차 검증 엔티티 및 레코드 통계(`include_stats=True` 기본) 요약. |
| `entity_search` | `kb: str\|int`, `query: str`, `entity_type: str?`, `near_ids: list[str]?`, `limit: int = 8` | 정확 이름·별칭 일치 및 FTS5 전문 랭킹 결합 검색 (`entity_type`, `near_ids` 1홉 필터 지원, LLM 호출 0). |
| `entity_get` | `kb: str\|int`, `entity_id: str`, `full: bool = False` | 단일 노드 상세 정보(관찰 목록, 타입 1홉 이웃, 연결 문서 최대 10건) 조회. |
| `graph_neighbors` | `kb: str\|int`, `entity_ids: list[str]\|str`, `exclude_ids: list[str]?`, `limit: int = 50` | **탐색의 핵심 단위**. 여러 노드의 중복 제거된 1홉 이웃을 `degree` 내림차순으로 합집합 조회 (`truncated`, `omitted` 반환). |
| `graph_path` | `kb: str\|int`, `from_id: str`, `to_id: str`, `max_hops: int = 4` | 두 노드 사이의 최단 무방향 연결 경로(BFS) 탐색. |
| `graph_context` | `kb: str\|int`, `entity_ids: list[str]`, `compact: bool = True` | 최대 10개 노드의 관찰, 관계, 출처 요약을 결정론적으로 종합 반환하는 **탐색 종료 단계 툴**. |
| `doc_list` | `kb: str\|int`, `limit: int = 30`, `since: str?`, `query: str?` | 최신 가시 문서 목록 조회 (`limit` 최대 100 하드캡, `since` ISO8601 UTC 및 제목/URL 부분 검색). |
| `doc_get` | `kb: str\|int`, `document_id: str` | 단일 문서 제목, 요약, 렌더링 본문, URL 및 메타데이터 조회 (`mark_seen` 부작용 없음, `fetched_at` ISO8601 UTC 정규화). |

#### 2) 지식 베이스 기여 및 신디케이션 도구 (2종)

| 툴 이름 | 입력 파라미터 | 스코프 / 제약 | 주요 역할 |
|---|---|---|---|
| `contrib_candidates` | `kb: str\|int?`, `theme: str\|int?`, `remote_kb: int?`, `include_uploads: bool = False` | 읽기 (`readonly`) | 개인 지식베이스의 문서 중 지정한 공유 지식베이스, 테마, 또는 원격 공유 지식베이스에 맞는 후보 점수 계산 (LLM 호출 0). |
| `contrib_submit` | `document_ids: list[str]`, `theme: str\|int?`, `remote_kb: int?` | `write` | 개인 지식베이스의 문서를 테마 또는 원격 공유 지식베이스에 기여 제출. |

#### 3) 클라이언트 주도 추출 도구 (6종, `AE5` / `AED9`)

| 툴 이름 | 입력 파라미터 | 스코프 / 제약 | 주요 역할 |
|---|---|---|---|
| `extract_spec` | (없음) | 읽기 (`readonly`) | `"agent-extract-v1"` 엄격 JSON 스키마, 활성 온톨로지 타입·domain/range 규칙, AsciiDoc 검증 규칙 반환. |
| `extract_stage` | `url?`, `text?`, `filename?`, `content_base64?`, `title?`, `source_type?`, `full_content: bool = True`, `mode?`, `kb?` | `write` (`CT4` 개인 KB, `openWorldHint: true`) | 서버 LLM 없이 URL, 일반 텍스트 또는 `content_base64` 파일(PDF·DOCX·ODT·HTML·MD·TXT·RST·ADOC)을 `pending` 상태로 스테이징. |
| `extract_queue` | `status: str? = "pending"`, `limit: int = 50`, `kb?` | `write` (`CT4` 개인 KB) | 개인 지식베이스의 스테이징 대기 목록 조회. |
| `extract_claim` | `document_id: str`, `claim_token: str?`, `client_id: str?`, `lease_seconds: float\|int = 300.0`, `kb?` | `write` (`CT4` 개인 KB) | 300초 기본 임대로 대기 문서를 선점(`client_id` 미지정 시 OAuth 클라이언트 자동 추론)하거나 `claim_token` 전달 시 기존 임대를 연장. |
| `extract_read` | `document_id: str`, `claim_token: str?`, `offset: int = 0`, `limit: int?`, `full_content: bool = True`, `line_offset: int?`, `line_limit: int?`, `kb?` | `write` (`CT4` 개인 KB, 격리 서브에이전트 전용) | 난수 nonce가 포함된 `<untrusted_source_document nonce="..." document_id="..." sha256="...">` 태그로 감싼 원문 조각(기본 `32000`자 또는 줄 슬라이스)과 `expected_content_hash` 반환. |
| `extract_submit` | `document_id: str`, `claim_token: str`, `expected_content_hash: str`, `payload: dict`, `dry_run: bool = False`, `kb?` | `write` (`CT4` 개인 KB) | `ConfigDict(extra="forbid", strict=True)`, 요약·AsciiDoc·근거 span·온톨로지 4계층 검증 및 옵션 B 서버 임베딩 계산 후 원자적 커밋(`extraction_origin = "agent_client"`). |

---

### 3.3 권장 에이전트 탐색 루프 (Agent Exploration Loop)

서버가 거대한 서브그래프 전체를 한 번에 덤프하지 않고, AI 에이전트가 점진적으로 프론티어를 넓혀가는 탐색 패턴을 권장한다 (클라이언트 추출 루프는 [`EXTRACTION.md`](EXTRACTION.md) §9 참조):

```text
[0. 지식베이스 확인] ──> kb_list() -> kb_overview(kb="personal" 또는 18자리 번호)
        │
[1. 진입점 확보]     ──> entity_search(query="...", kb=...)
        │
[2. 프론티어 확장]   ──> graph_neighbors(entity_ids=[...], exclude_ids=[...], limit=30, kb=...)
        │               (degree 높은 허브 선별 및 유망한 관계 가지 확장)
        ▼
[3. 영역 좁히기]     ──> entity_search(query="...", near_ids=[...], kb=...)
        │
[4. 심층 종합]       ──> graph_context(entity_ids=[...], compact=True, kb=...) (최종 근거 수집)
```

---

### 3.4 클라이언트별 연결 설정

#### 1) Claude · Google Antigravity 공식 마켓플레이스 (`fofwisdom/claire-agents` + OAuth 2.1, Option A 권장)
1. **Option A(전역 MCP 1회 등록 + 플러그인 분리)**: 원격 `/mcp` 서버는 클라이언트 전역 설정에 `"claire"`라는 키로 한 번 등록하고, 콘솔 `/account/mcp#pref-mcp-client-bundles`에서 안내하는 공식 마켓플레이스(`fofwisdom/claire-agents` `v0.4.1`, 정본 소스 `agent_clients/`, 선언형 `[adapter]` 계약은 [`AGENT_CLIENT_COMMON_SPEC.md`](../design/AGENT_CLIENT_COMMON_SPEC.md), GitHub Actions 자동 동기화)에서는 규칙·스킬·격리 서브에이전트만 담은 `claire` 플러그인을 설치한다 (업그레이드 시 사용자의 서버 URL을 보존하기 위해 플러그인 내부에는 `mcp_config.json`을 두지 않는다):
   - **Antigravity (GUI 우선)**: `~/.gemini/config/mcp_config.json`에 `"claire"` 서버(`/account/mcp`의 `#pref-mcp-download-mcp-config` 내려받기 지원)를 등록하고 **Customizations -> Installed -> MCP Servers -> claire**에서 OAuth 2.1 로그인을 완료한다. 이어서 **Antigravity 빠른 설치 문구**(`#pref-mcp-antigravity-prompt`)나 `docs/antigravity.ko.md` / `docs/antigravity.en.md`를 따라 GitHub Releases의 `claire-antigravity-v*.zip`(루트 `claire/`)을 플러그인 폴더에 설치한다 (`plugin.json` `suggestedPrompts` 최대 3개, `rules/AGENTS.md`, `agents/claire-extractor.md`, 사용자 노출 2축 스킬 `skills/claire-ingest/SKILL.md` 및 `skills/claire-query/SKILL.md`, `assets/logo.svg`, `README.md`). `claire-ingest`는 URL·로컬 파일·텍스트 노트 직접 적재(Flow A–C)와 대기(`pending`) 큐 추출·빈 큐 온보딩 안내(Flow D)를 모두 담당하고 `claire-extractor`로 위임한다. 첫 도구 실행 시 `Allow using this MCP tool?` 창이 뜨면 **`3 Yes, and always allow`** 선택 후 **`Submit`**을 누른다.
   - **Claude (GUI 우선, 데스크톱 앱 코드 모드와 터미널)**: 데스크톱 앱의 **플러그인 -> 추가 -> 마켓플레이스 추가**에 `fofwisdom/claire-agents`를 넣어 동기화(`.claude-plugin/marketplace.json` -> `./plugins/claude/claire`)하고 `claire` 옆의 **이 컴퓨터에 추가**를 누른다. 이어서 `/account/mcp`의 **Claude 빠른 설정 문구**(`#pref-mcp-claude-prompt`)를 새 대화에 붙여넣으면 `claude plugin install claire --marketplace <repo>`, `claude mcp add --transport http --scope user claire <url>`, `claude mcp login claire`가 차례로 실행된다(터미널 사용자는 `#pref-mcp-claude-setup`의 같은 세 줄을 직접 실행). `--scope user`가 없으면 서버가 현재 폴더에만 등록되고, Claude 커넥터로 추가한 서버는 도구 접두사가 달라 플러그인이 쓰지 못한다. 번들은 `agents/extractor.md`(`claire:extractor`, `mcp__claire__*` 8종 허용 목록), 사용자 노출 2축 스킬 `skills/{ingest,query}/SKILL.md`(`/claire:ingest`, `/claire:query`), 숨겨진 내부 포크 러너 `skills/extract-runner/SKILL.md`(`user-invocable: false`, `context: fork`, `agent: claire:extractor`), `hooks/hooks.json`으로 구성된다. `/claire:ingest`는 URL·파일·노트 직접 적재와 대기(`pending`) 큐 추출을 모두 처리하며 `Skill(claire:extract-runner)`로 `document_id`만 격리 서브에이전트에 넘긴다. `hooks/hooks.json`의 고정 `PreToolUse` 가드는 `claire:extractor` 밖의 `extract_read` 호출을 종료 코드 2로 막고 `/claire:ingest` 또는 `claire:extractor` 사용을 안내한다(1계층 방어). 가이드는 `docs/claude.ko.md` / `docs/claude.en.md`이다.
2. 모든 번들에는 비밀키가 포함되지 않으며, 수동/CLI 환경에서 `mcp_config.json`에 직접 지정할 때는 `url` 형식으로 본인의 Claire Bible 서버 `/mcp` 주소를 넣고 OAuth 2.1(`S256` PKCE 또는 `/device`)로 로그인한다 (`write` 작업은 동의 시 `write` 스코프 및 단계 인증 필요):
```json
{
  "mcpServers": {
    "claire": {
      "url": "https://claire.example.com/mcp"
    }
  }
}
```

#### 2) Google Gemini (Spark / 맞춤형 연결 앱)
1. `gemini.google.com/spark/apps` 접속 ➡️ **[MCP 서버에 연결]** 클릭.
2. **MCP 서버 URL**: `https://<CLAIRE_FQDN>/mcp` 입력 후 [다음] 클릭.
3. 브라우저의 OAuth 2.1 동의 화면(`/oauth/authorize` → `/login?tx=`)에서 로그인 후 권한을 승인한다.

#### 3) Cursor (`.cursor/mcp.json`)
OAuth 2.1 지원 클라이언트에서는 `url`만 등록하거나, 레거시 상시 토큰을 임시 사용할 때는 `Authorization` 헤더를 함께 지정한다:
```json
{
  "mcpServers": {
    "claire": {
      "url": "https://claire.example.com/mcp"
    }
  }
}
```

#### 4) 직접 JSON-RPC 통신 (curl 예시)
```bash
curl -s -X POST https://claire.example.com/mcp \
     -H "Authorization: Bearer <OAUTH_OR_BEARER_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "id": 1,
       "method": "tools/call",
       "params": {
         "name": "entity_search",
         "arguments": {
           "query": "OAuth 2.1 PKCE",
           "limit": 3,
           "kb": "personal"
         }
       }
     }'
```

---

## 4. API 및 MCP 토큰 관리 종합 인터페이스

신규 클라이언트 인가는 콘솔 `/account/mcp`의 **OAuth 2.1 부여 관리 및 `agent_client` 번들 배포**를 기본으로 한다. 레거시 상시 토큰은 CLI, WebUI, Telegram 봇 모두에서 지원 중단 안내(`Deprecation`)를 함께 출력하며 퇴역 전까지만 유지된다.

### 4.1 CLI 토큰 관리 (`claire token`, Deprecated)

터미널이나 스크립트에서 레거시 상시 토큰을 조회·발급·폐기한다(명령 실행 시 `[DEPRECATED]` 안내 출력):

```bash
# 1. 활성 토큰 목록 조회 (폐기/만료 토큰 포함 시 -a 또는 --all)
claire token list
claire token list -a --json

# 2. 상시 이용 토큰 발급 (기본: readonly 권한, 무기한; json 응답 키 mcp_client_config 제공)
claire token create "Agent Client" --scope readonly
claire token create "Cursor Agent" --scope write --expires-in 90 --json

# 3. 토큰 즉시 폐기 (ID 또는 토큰값 지정)
claire token revoke 2
claire token revoke "cb_xX9vY..." --json
```

### 4.2 WebUI OAuth 2.1 · 번들 및 토큰 관리 콘솔 (`/account/mcp`, `/account/tokens`)

- **접근 권한**: `owner`만 들어간다. 루트 `/` 상세 메뉴에는 관리 패널이 없다.
- **`/account/mcp` (권장)**: 최상단 빠른 시작 가이드(3단계 녹색/붉은색 진행 체크리스트 — ① `extraction_mode = 'client'`, ② OAuth 2.1 `write` 인가 연결, ③ 첫 `agent_client` 추출 완료), OAuth 2.1 클라이언트 인가 목록(`/api/me/oauth/grants` 조회·개별/전체 폐기), 서버에 등록된 라이브 17종 `<domain>_<action>` MCP 도구(`tools/list`) 안내, Option A 전역 `"claire"` MCP 서버 연결 스니펫·`mcp_config.json` 내려받기(`#pref-mcp-download-mcp-config`), 공식 마켓플레이스 `fofwisdom/claire-agents`(`plugins/antigravity/claire/`, `plugins/claude/claire/`) 빠른 설치 문구 및 가이드 링크(Antigravity `#pref-mcp-antigravity-prompt`·`#pref-mcp-antigravity-guide`, Claude `#pref-mcp-claude-prompt`·`#pref-mcp-claude-guide`·`#pref-mcp-claude-setup`), `/mcp` 연결 주소 및 수동 토큰 지원 중단 배너를 제공한다.
- **`/account/tokens` (관리자는 `/admin/tokens`, Deprecated)**: 멤버는 `/account/tokens`, 관리자는 `/admin/tokens`(302 리디렉트)에서 본인 계정에 귀속된 레거시 상시 토큰 목록(`Deprecated` 배너 포함), 신규 발급(퇴역 미활성화 시), 즉시 폐기 및 OAuth 클라이언트 목록을 확인한다.

### 4.3 텔레그램 봇 토큰 명령어 (`/token`, Deprecated)

스마트폰이나 텔레그램 메신저에서 원격으로 레거시 상시 토큰 상태를 조회·발급·폐기할 수 있으며, 모든 응답에 OAuth 2.1(`/account/mcp`) 이전 안내가 덧붙는다.

- **목록 조회**: `/token` 또는 `/token list`
- **토큰 생성**: `/token create <이름> [owner|write|readonly]`
  - 예: `/token create AgentClient readonly`
  - 발급 즉시 봇 메시지로 시크릿 토큰, MCP 연결 URL, 지원 중단 안내가 전송된다.
- **토큰 폐기**: `/token revoke <ID 또는 토큰값>`
  - 예: `/token revoke 2`
  - 폐기 즉시 해당 토큰의 모든 API/MCP 연결이 영구 차단된다.

---

## 5. 에이전트 CLI 연동 규약 (Agent-First Contract)

터미널이나 서브프로세스에서 `claire` CLI 명령을 호출하는 에이전트를 위해 `--json` 플래그 및 기계 판독용 계약(`src/claire/api/agent.py`)을 제공한다:

- 대화형 입력 프롬프트 차단: `--json` 모드에서는 `input()` 호출 시 즉시 에러(Exit code 4)를 반환하여 에이전트 행(Hang) 상태를 방지.
- 출력 격리: 일반 안내/로깅 텍스트는 `stderr`로 격리되고, 오직 표준 JSON 구조(`AgentOutput`)만 `stdout`으로 단일 출력됨.
- 반환 JSON 구조:
```json
{
  "status": "success",
  "exit_code": 0,
  "data": { ... }
}
```

---

## 6. 오류 코드 및 트러블슈팅

| HTTP 상태 코드 | 주요 원인 | 조치 사항 |
|---|---|---|
| `401 Unauthorized` | 유효하지 않거나 만료된 토큰 (`WWW-Authenticate` 반환) | 토큰 재확인 또는 OAuth / 텔레그램 재발급 |
| `403 Forbidden` | 자격증명 채널 다중 제공(Bearer + Cookie 등), 쓰기 권한 부족 | 인증 채널을 하나로 통일하거나 스코프 권한 확인 |
| `404 Not Found` | 존재하지 않는 리소스, 또는 **비인증/비공개 테마 스텔스 모드** | `CLAIRE_ANONYMOUS_READONLY` 설정 및 테마 가시성 확인 |
| `413 Request Entity Too Large` | 요청 본문 크기가 1MB를 초과함 | 전송 페이로드 크기 축소 |
| `421 Misdirected Request` | 요청의 `Host` 헤더가 `CLAIRE_FQDN`과 불일치 | 프록시의 Host 헤더 전달 설정 또는 FQDN 환경변수 점검 |
