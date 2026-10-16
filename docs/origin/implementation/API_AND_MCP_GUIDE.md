# Claire Bible REST API 및 MCP 연결 가이드 (통합 구현본)

- **상태**: **구현 완료 (REST API + M1 MCP 툴셋 + OAuth 2.1 표준 인가 서버)**
- **최종 갱신일**: 2026-10-16
- **기준 프로토콜 및 사양**:
  - OpenAPI 3.1.0 ([`docs/contracts/openapi.yaml`](../../contracts/openapi.yaml))
  - Model Context Protocol (MCP) Streamable HTTP Transport (JSON-RPC 2.0)
  - HTTP REST/ASGI 와이어 프로토콜 및 보안 계약 ([`docs/contracts/API_HTTP_CONTRACT.md`](../../contracts/API_HTTP_CONTRACT.md))
  - OAuth 2.0 / 2.1 RFC 계열:
    - RFC 6749 (The OAuth 2.0 Authorization Framework) / RFC 6750 (Bearer Token Usage)
    - RFC 7636 (Proof Key for Code Exchange by OAuth Public Clients, PKCE S256)
    - RFC 7591 (OAuth 2.0 Dynamic Client Registration Protocol)
    - RFC 8414 (OAuth 2.0 Authorization Server Metadata)
    - RFC 9728 (OAuth 2.0 Protected Resource Metadata)
    - RFC 8693 (OAuth 2.0 Token Exchange, [`docs/contracts/TOKEN_EXCHANGE.md`](../../contracts/TOKEN_EXCHANGE.md))

---

## 1. 개요 및 연결 기본 원칙

Claire Bible은 지식 그래프 및 문서 보관소를 외부 애플리케이션, 개발자 스크립트, 그리고 외부 AI 하네스(Google Gemini, Claude Code/Desktop, Antigravity, Cursor 등)에서 원활하고 안전하게 활용할 수 있도록 **표준 REST API**와 **Model Context Protocol (MCP)** 엔드포인트를 단일 ASGI 프로세스(`claire_api`) 내에 통합 제공한다.

### 1.1 접속 주소 (Base URL) 및 Host Authority
- **개발 환경 (`development`)**: `http://<CB_API_BIND>:<CB_API_PORT>` (예: `http://127.0.0.1:8765` 또는 `http://192.168.10.25:8766`)
- **프로덕션 환경 (`production`)**: `https://<CLAIRE_FQDN>` (예: `https://claire.example.com` 또는 `https://cb.netspheres.org`)
- **Host Authority 불변식**:
  - `HostAuthorityMiddleware`는 모든 요청의 `Host` 헤더가 환경변수에 설정된 `CLAIRE_FQDN`(또는 등록된 테마 전용 FQDN)과 일치하는지 엄격히 검사한다.
  - 임의의 호스트명이나 IP로 우회 접근 시 HTTP `421 Misdirected Request`로 거부된다.
  - 역방향 프록시(Nginx, Cloudflare 등) 구성 시 클라이언트가 요청한 `Host` 헤더를 백엔드로 변조 없이 전달해야 한다. (상세 네트워크 구성은 [`EXTERNAL_ACCESS.md`](EXTERNAL_ACCESS.md) 참조)

### 1.2 인증 스코프 (RBAC) 4계층
Claire Bible은 4단계 역할 기반 접근 제어를 제공한다:

$$\text{owner} \succ \text{collaborator} \succ \text{readonly} \succ \text{anonymous}$$

| 역할 (Scope) | 자격 증명 (토큰 / 헤더) | 기본 테마 (`seq=0`) | 추가 테마 (공개) | 추가 테마 (비공개) | 테마 관리 / 진단 번들 |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **시스템 관리자 (`owner`)** | `CLAIRE_INJECT_TOKEN` 또는 텔레그램 `/web` 세션 | 읽기 / 쓰기 | 읽기 / 쓰기 | 읽기 / 쓰기 | 가능 (`define`, `update`, `delete`, `bundle`) |
| **협력자 (`collaborator`)** | `CLAIRE_COLLABORATOR_TOKEN` 또는 텔레그램 `/webco` 세션 | **쓰기 불가 (차단)**<br>(읽기만 가능) | 읽기 / 쓰기 | 허용 테마만<br>읽기 / 쓰기 | 불가 |
| **읽기 전용 (`readonly`)** | `CLAIRE_READONLY_TOKEN` 또는 텔레그램 `/webro` 세션 | 읽기 전용 | 읽기 전용 | 읽기 전용 | 불가 |
| **익명 사용자 (`anonymous`)** | 자격증명 없음 (`CLAIRE_ANONYMOUS_READONLY=1`) | 공개 읽기<br>(숨김 문서 제외) | 공개 읽기<br>(숨김 문서 제외) | **404 Not Found**<br>(존재 은닉) | 불가 |

### 1.3 자격증명 채널 및 상호 배타성 규약
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

### 1.4 스텔스 모드 (Fail-Closed Invariant)
`CLAIRE_ANONYMOUS_READONLY=0`(프라이빗/스텔스 모드)로 구동되는 인스턴스 또는 비공개 테마(`is_public=false`)에 대해 자격증명이 없는 읽기 요청이 인입될 경우, 외부 정찰에 엔드포인트나 테마의 존재 여부를 일절 노출하지 않기 위해 `401 Unauthorized` 또는 `403 Forbidden` 대신 **HTTP `404 Not Found`**를 반환한다.

### 1.5 요청 크기 제한
모든 API 및 MCP 요청 본문은 최대 1MB (`1,048,576` bytes)로 하드 제한된다. 초과 시 즉시 HTTP `413 Request Entity Too Large`로 차단된다.

### 1.6 멀티 테마(Multi-Theme) 라우팅
멀티 테마(`CLAIRE_MULTI_THEME=1`) 환경에서는 요청 헤더 또는 쿼리 파라미터로 조회/작업 대상 테마를 명시할 수 있다:
- **HTTP 헤더 (REST/MCP 권장)**: `X-Claire-Theme: <theme_id_or_label>` (예: `X-Claire-Theme: 1` 또는 `X-Claire-Theme: 인공지능`)
- **URL 쿼리 파라미터 (브라우저/링크 권장)**: `?theme=<theme_id_or_label>`
- **폴백 규칙**: 테마 지정이 모두 생략되면 기본 지식베이스인 Theme `0`으로 자동 라우팅된다.

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
현재 요청에 적용된 인증 스코프를 확인한다.
```bash
curl -s -H "Authorization: Bearer <TOKEN>" https://claire.example.com/whoami
```
```json
{"scope": "owner"}
```

#### `GET /stats` (Read)
지식베이스 규모(문서 수, 엔티티 수, 관계 수, 소스 타입별 통계 등)를 조회한다.
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
        -H "X-Claire-Theme: 0" \
        https://claire.example.com/stats
```
```json
{
  "theme_id": 0,
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
아카이브된 문서 타임라인 목록을 조회한다.
- **쿼리 파라미터**:
  - `limit` (int, 선택, 기본 30, 최대 100): 조회 건수
  - `since` (float/str, 선택): 특정 시각 이후 문서 필터링 (ISO8601 또는 epoch 초)
  - `query` (str, 선택): 문서 제목/URL 부분 일치 검색
  - `theme` (str/int, 선택): 대상 테마 ID 또는 라벨
```bash
curl -s -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     "https://claire.example.com/documents?limit=10&query=OAuth"
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
     "https://claire.example.com/graph?theme=0"
```

#### `GET /node` (Read)
특정 엔티티 노드의 상세 속성, 관찰(observations), 연결된 1홉 관계 및 소스 문서 목록을 조회한다.
- **쿼리 파라미터**: `id` (str, 필수), `full` (bool, 선택, 기본 false), `theme` (선택)
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

신뢰된 주체 간의 토큰 교환을 지원한다. (`application/x-www-form-urlencoded` 사용)
```bash
curl -s -X POST https://claire.example.com/oauth/token \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "grant_type=urn:ietf:params:oauth:grant-type:token-exchange" \
     -d "subject_token=<INJECT_OR_SESSION_TOKEN>" \
     -d "subject_token_type=urn:ietf:params:oauth:token-type:access_token" \
     -d "scope=readonly"
```

---

## 3. MCP (Model Context Protocol) 연동 및 도구 가이드

Claire Bible은 외부 AI 에이전트가 지식베이스를 탐색할 수 있도록 **Model Context Protocol (MCP)** 공식 사양을 지원한다.

- **엔드포인트**: `POST /mcp` (Streamable / Stateless HTTP JSON-RPC 2.0)
- **임베딩 아키텍처**: 별도 컨테이너 없이 `claire_api` Starlette 프로세스 내부에서 직접 마운트 서빙.
- **LLM 쿼터 격리 (Zero Gemini Overhead)**:
  - 모든 MCP 도구는 **순수 SQLite 쿼리 및 FTS5 검색 엔진만 사용**한다.
  - 외부 에이전트의 잦은 탐색 루프 중에도 Google Gemini API 호출이나 토큰 소진이 일절 발생하지 않는다.

---

### 3.1 MCP 인증 메커니즘

MCP 클라이언트는 다음 세 가지 방식 중 하나로 `/mcp`에 접속할 수 있다:

#### 방법 1: 고정 정적 토큰 (Bearer Token)
CLI 에이전트, 스크립트, 로컬 IDE 등에서 가장 간단하게 연동하는 방식이다.
- 헤더: `Authorization: Bearer <CLAIRE_READONLY_TOKEN>` (또는 `<CLAIRE_INJECT_TOKEN>`)

#### 방법 2: 텔레그램 발급 세션 토큰 (X-Session)
텔레그램 봇(`/web`, `/webco`, `/webro`)에서 발급받은 임시 세션 토큰을 전달한다.
- 헤더: `X-Session: <session_token>` 또는 `Authorization: Bearer <session_token>`

#### 방법 3: OAuth 2.1 표준 인가 서버 (Gemini Spark / 원터치 승인)
Google Gemini 맞춤형 연결 앱(Spark)이나 최신 OAuth 2.1 호환 클라이언트를 위한 완전 자동화 플로우이다:

```
[외부 AI 클라이언트 (Gemini 등)]               [Claire Bible 인가 서버]
              │                                           │
  1. GET /mcp (미인증) ──────────────────────────────────>│ (401 + WWW-Authenticate)
  2. GET /.well-known/oauth-protected-resource (RFC 9728) │
  3. GET /.well-known/oauth-authorization-server (RFC 8414)
  4. POST /oauth/register (RFC 7591 동적 클라이언트 등록) ──>│ (201 Created, client_id/secret)
  5. GET /oauth/authorize (Redirect, PKCE S256) ─────────>│
              │                                           │
              ├─ [브라우저 로그인 시] -> [✅ 바로 승인하기] 1클릭
              └─ [비로그인 시] -> [📱 텔레그램 승인 요청] 1클릭
              │                                           │
  6. POST /oauth/token (인증 코드 + PKCE verifier) ───────>│ (200 OK, access_token + refresh_token)
  7. POST /mcp (Authorization: Bearer <access_token>) ───>│ (JSON-RPC MCP 서비스 개시)
```

- **영구 독립 세션 (`oauth_tokens` 테이블)**:
  - 텔레그램 웹 세션의 단일 활성 정책과 완전히 격리된다. 텔레그램에서 `/web`을 재발급받아도 Gemini에 연결된 토큰은 절대 끊어지지 않는다.
- **Refresh Token 자동 회전**:
  - `access_token` 만료(30일) 시 백그라운드에서 자동 갱신된다.

---

### 3.2 제공 툴셋 (10종 Read-Only 도구)

모든 MCP 도구는 멀티 테마 파라미터(`theme: str | int | None = None`)를 공통 지원한다.

| 툴 이름 | 입력 파라미터 | 주요 역할 및 특징 |
|---|---|---|
| `resolve_entity` | `name: str`, `theme: str\|int?` | **탐색 루프의 진입점**. 이름이나 별칭 문자열로 노드 ID 검색 (정확 일치 -> 별칭 -> FTS5 fuzzy 폴백). |
| `search` | `query: str`, `entity_type: str?`, `near_ids: list[str]?`, `limit: int?`, `theme: str\|int?` | FTS5 전문 검색. `entity_type` 필터 및 특정 프론티어 근방(`near_ids`) 필터링 지원. LLM 호출 0. |
| `neighbors` | `entity_ids: list[str]\|str`, `exclude_ids: list[str]?`, `limit: int?`, `theme: str\|int?` | **탐색의 핵심 단위**. 여러 노드의 1홉 이웃을 합집합으로 조회하며 `degree` 포함. `truncated`, `omitted` 반환. |
| `path` | `from_id: str`, `to_id: str`, `max_hops: int?`, `theme: str\|int?` | 두 노드 사이의 최단 연결 경로(BFS)를 탐색하여 인과/연결 관계 규명. |
| `context` | `entity_ids: list[str]`, `compact: bool?`, `theme: str\|int?` | 노드들의 관찰(observations), 연결선, 출처 요약을 종합 반환하는 **탐색 종료 단계 툴** (최대 10개 노드). |
| `overview` | `theme: str\|int?` | 지식베이스 전체의 엔티티 타입 분포, 주요 허브 노드, 핵심 수렴 정보를 제공하는 **오리엔테이션 툴**. |
| `node` | `id: str`, `full: bool?`, `theme: str\|int?` | 노드 1개의 상세 정보(타입, 별칭, 속성, 관찰 목록). 소스 문서는 최신 10개로 캡. |
| `documents` | `limit: int?`, `since: str?`, `query: str?`, `theme: str\|int?` | 문서 타임라인 목록 조회 (최대 100건 하드캡 및 잘림 표시 제공). |
| `document` | `document_id: str`, `theme: str\|int?` | 단일 문서 본문 및 메타데이터 열람 (`mark_seen` 부작용 제거, `fetched_at`은 ISO8601 UTC 정규화). |
| `stats` | `theme: str\|int?` | 데이터베이스 카운트 요약(문서 수, 엔티티 수, 관계 수, 소스 유형별 통계). |

---

### 3.3 권장 에이전트 탐색 루프 (Agent Exploration Loop)

서버가 거대한 서브그래프 전체를 한 번에 덤프하지 않고, AI 에이전트가 점진적으로 프론티어를 넓혀가는 탐색 패턴을 권장한다:

```text
[0. 오리엔테이션] ──> overview() (지식베이스 전체 도메인 및 허브 감 잡기)
        │
[1. 진입점 확보]  ──> resolve_entity(name="...") 또는 search(query="...")
        │
[2. 프론티어 확장] ──> neighbors(entity_ids=[...], exclude_ids=[...], limit=30)
        │            (degree 높은 허브 선별 및 유망한 관계 가지 확장)
        ▼
[3. 영역 좁히기]  ──> search(query="...", near_ids=[...])
        │
[4. 심층 종합]    ──> context(entity_ids=[...], compact=True) (최종 근거 수집)
```

---

### 3.4 클라이언트별 연결 설정

#### 1) Google Gemini (Spark / 맞춤형 연결 앱)
1. `gemini.google.com/spark/apps` 접속 ➡️ **[MCP 서버에 연결]** 클릭.
2. **MCP 서버 URL**: `https://<CLAIRE_FQDN>/mcp` 입력 후 [다음] 클릭.
3. Gemini가 DCR 및 메타데이터를 자동 감지하여 Claire 승인 페이지를 표시.
4. 브라우저에서 **[✅ 바로 승인하기]** (또는 스마트폰 텔레그램 알림에서 **[승인]**) 클릭 ➡️ 즉시 연동 완료!

#### 2) Claude Desktop (`claude_desktop_config.json`)
- 경로:
  - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
  - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
  - Linux: `~/.config/Claude/claude_desktop_config.json`
```json
{
  "mcpServers": {
    "claire": {
      "url": "https://claire.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <CLAIRE_READONLY_TOKEN_OR_SESSION_TOKEN>"
      }
    }
  }
}
```

#### 3) Claude Code (`~/.claude/settings.json`)
```json
{
  "mcpServers": {
    "claire": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-fetch",
        "https://claire.example.com/mcp"
      ],
      "headers": {
        "Authorization": "Bearer <CLAIRE_READONLY_TOKEN_OR_SESSION_TOKEN>"
      }
    }
  }
}
```

#### 4) Cursor (`.cursor/mcp.json`)
```json
{
  "mcpServers": {
    "claire": {
      "url": "https://claire.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <CLAIRE_READONLY_TOKEN_OR_SESSION_TOKEN>"
      }
    }
  }
}
```

#### 5) Google Antigravity (`~/.gemini/config/mcp_config.json` 또는 `.agents/mcp_config.json`)
```json
{
  "mcpServers": {
    "claire-bible": {
      "serverUrl": "https://claire.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <CLAIRE_READONLY_TOKEN_OR_SESSION_TOKEN>"
      }
    }
  }
}
```

#### 6) 직접 JSON-RPC 통신 (curl 예시)
```bash
curl -s -X POST https://claire.example.com/mcp \
     -H "Authorization: Bearer <CLAIRE_READONLY_TOKEN>" \
     -H "Content-Type: application/json" \
     -d '{
       "jsonrpc": "2.0",
       "id": 1,
       "method": "tools/call",
       "params": {
         "name": "search",
         "arguments": {
           "query": "OAuth 2.1 PKCE",
           "limit": 3
         }
       }
     }'
```

---

## 4. 에이전트 CLI 연동 규약 (Agent-First Contract)

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

## 5. 오류 코드 및 트러블슈팅

| HTTP 상태 코드 | 주요 원인 | 조치 사항 |
|---|---|---|
| `401 Unauthorized` | 유효하지 않거나 만료된 토큰 (`WWW-Authenticate` 반환) | 토큰 재확인 또는 OAuth / 텔레그램 재발급 |
| `403 Forbidden` | 자격증명 채널 다중 제공(Bearer + Cookie 등), 쓰기 권한 부족 | 인증 채널을 하나로 통일하거나 스코프 권한 확인 |
| `404 Not Found` | 존재하지 않는 리소스, 또는 **비인증/비공개 테마 스텔스 모드** | `CLAIRE_ANONYMOUS_READONLY` 설정 및 테마 가시성 확인 |
| `413 Request Entity Too Large` | 요청 본문 크기가 1MB를 초과함 | 전송 페이로드 크기 축소 |
| `421 Misdirected Request` | 요청의 `Host` 헤더가 `CLAIRE_FQDN`과 불일치 | 프록시의 Host 헤더 전달 설정 또는 FQDN 환경변수 점검 |
```
