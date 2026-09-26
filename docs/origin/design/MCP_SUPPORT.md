# MCP 및 OAuth 2.1 지원 사양서 (통합 구현본)

- **상태**: **구현 완료 (M1 툴셋 + OAuth 2.1 표준 인가 서버)**
- **최종 갱신일**: 2026-09-22
- **기준 프로토콜**:
  - Model Context Protocol (MCP) Streamable HTTP Transport (JSON-RPC 2.0)
  - RFC 6749 (OAuth 2.0) / RFC 6750 (Bearer Token Usage)
  - RFC 7636 (Proof Key for Code Exchange by OAuth Public Clients, PKCE S256)
  - RFC 7591 (OAuth 2.0 Dynamic Client Registration Protocol)
  - RFC 8414 (OAuth 2.0 Authorization Server Metadata)
  - RFC 9728 (OAuth 2.0 Protected Resource Metadata)
  - RFC 8693 (OAuth 2.0 Token Exchange)

---

## 1. 아키텍처 개요

Claire의 지식 그래프와 문서를 **Telegram 봇/웹 UI 외에 Claude Code, Claude Desktop, Google Gemini 맞춤형 연결 앱(Spark), Antigravity, Cursor 등 다양한 외부 AI 하네스**에서 접근할 수 있도록 표준 엔드포인트를 제공한다.

```
                                [외부 AI 클라이언트]
                    (Google Gemini Spark / Claude Desktop 등)
                                       │
                      1. GET /mcp (401 + resource_metadata)
                                       ▼
                 2. GET /.well-known/oauth-protected-resource (RFC 9728)
                                       ▼
                3. GET /.well-known/oauth-authorization-server (RFC 8414)
                                       ▼
                     4. POST /oauth/register (RFC 7591 Dynamic Client Reg)
                                       ▼
                   5. GET /oauth/authorize (Redirect to Browser, RFC 7636 PKCE)
                      - 브라우저 로그인 쿠키 시: [승인] 1클릭
                      - 비로그인 새 브라우저 시: [텔레그램 승인 요청] 1클릭
                                       ▼
                         6. POST /oauth/token (Exchange Code with PKCE)
                                       ▼
                  7. POST /mcp (Header: Authorization: Bearer <access_token>)
                     (독립 oauth_tokens 대조 -> JSON-RPC 2.0 MCP 툴 서빙)
```

- **프로세스 내 임베딩 (In-process Embedded)**:
  - 별도 컨테이너가 아닌 기존 `claire_api` 프로세스(Starlette ASGI, 단일 worker) 내에 통합 마운트.
  - 무인가 프로빙에 대한 일관된 보안 경계 및 동일 DB 커넥션 풀 공유.
- **Gemini / LLM 쿼터 격리**:
  - MCP v1 툴은 **순수 DB 쿼리(SQLite + FTS5)만 사용**하며, Gemini 임베딩/요약 API를 호출하지 않아 프로세스 쿼터 소진 위험을 차단한다.
- **엄격한 `readonly` 권한 제한**:
  - 외부 AI 클라이언트에 부여되는 OAuth 토큰은 오직 `readonly` 스코프로 고정되어 지식베이스 변경/문서 적재/삭제를 원천 차단한다.

---

## 2. 인증 및 OAuth 2.1 사양

### 2.1 메타데이터 Discovery 엔드포인트

1. **Protected Resource Metadata (`GET /.well-known/oauth-protected-resource`)** (RFC 9728)
   ```json
   {
     "resource": "https://cb.netspheres.org/mcp",
     "authorization_servers": ["https://cb.netspheres.org"],
     "scopes_supported": ["readonly"],
     "bearer_methods_supported": ["header"]
   }
   ```
2. **Authorization Server Metadata (`GET /.well-known/oauth-authorization-server`)** (RFC 8414)
   ```json
   {
     "issuer": "https://cb.netspheres.org",
     "authorization_endpoint": "https://cb.netspheres.org/oauth/authorize",
     "token_endpoint": "https://cb.netspheres.org/oauth/token",
     "registration_endpoint": "https://cb.netspheres.org/oauth/register",
     "scopes_supported": ["readonly"],
     "response_types_supported": ["code"],
     "grant_types_supported": [
       "authorization_code",
       "refresh_token",
       "urn:ietf:params:oauth:grant-type:token-exchange"
     ],
     "code_challenge_methods_supported": ["S256"],
     "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]
   }
   ```
3. **`WWW-Authenticate` 헤더 (RFC 6750 & RFC 9728)** 미인증 상태로 `/mcp` 호출 시 반환:
   ```http
   HTTP/1.1 401 Unauthorized
   WWW-Authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="https://cb.netspheres.org/.well-known/oauth-protected-resource"
   Content-Type: application/json

   {"error": "invalid_token", "error_description": "Authentication required"}
   ```

### 2.2 동적 클라이언트 등록 (`POST /oauth/register`) (RFC 7591)
- 요청:
  ```json
  {
    "client_name": "Google Gemini Spark",
    "redirect_uris": ["https://gemini.google.com/oauth/callback"]
  }
  ```
- 응답 (201 Created):
  ```json
  {
    "client_id": "claire_mcp_...",
    "client_secret": "...",
    "client_name": "Google Gemini Spark",
    "redirect_uris": ["https://gemini.google.com/oauth/callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "client_secret_post"
  }
  ```

### 2.3 하이브리드 원터치 인가 화면 (`/oauth/authorize`)
Gemini 및 브라우저 사용자가 토큰 복사/붙여넣기 없이 최고 수준의 편의성으로 승인할 수 있도록 설계:
- **표준 인가 서버 CSP (`form-action 'self' https:`)**:
  - OAuth 2.1 인가 서버 규약에 따라 폼 전송 액션을 서브밋 자체와 임의의 안전한 외부 리다이렉트(`https:`) 대상으로 허용하여, Google Gemini의 콜백 엔드포인트(`oauth-redirect.googleusercontent.com` 등)로의 폼 서브밋 차단(Refused to send form data)을 원천 방지.
- **자동 쿠키 감지**: 이미 Claire 웹 UI에 로그인되어 있는 경우 **[✅ 바로 승인하기]** 버튼 1클릭으로 즉시 승인.
- **텔레그램 푸시 연동**: 비로그인 브라우저나 모바일 기기인 경우 **[📱 텔레그램으로 승인 요청 보내기]** 버튼 클릭 시, 소유자의 스마트폰 텔레그램으로 `[✅ 승인] [❌ 거절]` 인라인 버튼 메시지가 전송되어 폰에서 원클릭 승인 완료 (`/oauth/authorize/telegram-push` 및 `/oauth/authorize/poll`).
  - **보안 마스킹 및 공격 벡터 차단**: 미등록 클라이언트나 내부 설정 미비 시 세부 에러나 봇 토큰 존재 여부를 외부 공격자에게 노출하지 않고 표준 RFC 6749 에러 코드(`invalid_request` 400, `temporarily_unavailable` 503)로 통일 마스킹.
  - **원자적 인가 요청 생성**: 텔레그램 메시지 전송 성공 시에만 데이터베이스에 인가 요청 레코드를 원자적으로 영속화하여 미전송 고아 요청 방지.
- **세션 토큰 입력 지원**: 텔레그램 `/web` 또는 `/webro` 발급 토큰을 붙여넣는 수동 폼도 함께 제공.
- **환경 변수 일원화**:
  - 알림 및 승인 대상 관리를 위해 `TELEGRAM_ALLOWED_USERS`와 `TELEGRAM_OWNER_CHAT_ID`로 통일 (기존 `CLAIRE_*` 레거시 변수는 혼선을 방지하기 위해 단절 및 완전 제거).

### 2.4 세션 격리 및 Refresh Token 자동 갱신
- **영구 독립 격리 (`oauth_tokens` 테이블)**:
  - 기존 텔레그램 `/web`, `/webro` 세션의 "단일 활성 세션(발급 시 이전 세션 삭제)" 정책과 완전히 분리.
  - 소유자가 텔레그램에서 웹 링크를 수없이 재발급받더라도 **Gemini에 연결된 OAuth 토큰은 절대 만료되거나 끊어지지 않음**.
- **Refresh Token 백그라운드 갱신**:
  - `access_token` 만료(기본 30일) 시 Gemini가 `grant_type=refresh_token`을 호출하여 사용자 개입 없이 새 토큰을 자동 회전(Rotation) 및 갱신.

---

## 3. MCP 툴 표면 (10종 Read-Only 도구)

| 툴 이름 | 입력 파라미터 | 주요 역할 및 특징 |
|---|---|---|
| `resolve_entity` | `name: str` | **탐색 루프의 진입점**. 이름 문자열로 노드 ID 검색 (정확 일치 -> 별칭 -> FTS5 fuzzy 폴백). |
| `search` | `query: str`, `entity_type: str?`, `near_ids: list[str]?`, `limit: int?` | FTS5 기반 지식 검색. `entity_type` 필터 및 특정 프론티어 근방(`near_ids`) 필터링 지원. LLM 호출 0. |
| `neighbors` | `entity_ids: list[str]`, `exclude_ids: list[str]?`, `limit: int?` | **탐색의 핵심 단위**. 여러 노드의 1홉 이웃을 합집합으로 조회하며 `degree` 포함. `truncated`, `omitted` 반환. |
| `path` | `from_id: str`, `to_id: str`, `max_hops: int?` | 두 노드 사이의 최단 연결 경로(BFS)를 탐색하여 인과/관계 연결 이유 규명. |
| `context` | `entity_ids: list[str]`, `compact: bool?` | 노드들의 observations, 연결 관계, 출처 요약을 종합 반환하는 **탐색 종료 단계 툴**. |
| `overview` | 없음 | 지식베이스 전체의 엔티티 타입 분포, 주요 허브 노드, 핵심 수렴 정보를 제공하는 **오리엔테이션 툴**. |
| `node` | `id: str`, `full: bool?` | 노드 1개의 상세 정보(타입, 별칭, 속성, observations). 과도한 컨텍스트 방지를 위해 소스 문서는 최신 10개로 캡. |
| `documents` | `limit: int?`, `since: float?`, `query: str?` | 문서 타임라인 목록 조회 (최대 100건 하드캡 및 잘림 표시 제공). |
| `document` | `id: str` | 단일 문서 본문 및 메타데이터 열람 (`mark_seen` 부작용 제거, `fetched_at`을 ISO8601 UTC로 정규화). |
| `stats` | 없음 | 데이터베이스 카운트 요약(문서 수, 엔티티 수, 관계 수, 소스 유형별 통계). |

---

## 4. 에이전트 탐색 루프 가이드

서버가 거대한 서브그래프를 한 번에 덤프하지 않고, **AI 에이전트가 스스로 판단하며 깊이를 넓혀가는 표준 탐색 패턴**을 권장한다:

```
[0. 오리엔테이션] -> overview() (도메인 감 잡기)
       │
[1. 진입점 확보] -> resolve_entity(name) 또는 search(query)
       │
[2. 프론티어 확장] -> neighbors(entity_ids=[...], limit=30)
       │          (degree로 허브 구분 -> 유망한 가지 선별)
       ▼
[3. 영역 좁히기] -> search(query=..., near_ids=[...])
       │
[4. 심층 종합]   -> context(entity_ids=[...]) (최종 근거 수집)
```

---

## 5. 클라이언트별 연결 설정

### 5.1 Google Gemini (Spark / 맞춤형 연결 앱)
1. `gemini.google.com/spark/apps` 접속 -> **[MCP 서버에 연결]** 클릭.
2. **MCP 서버 URL**: `https://cb.netspheres.org/mcp` 입력 후 [다음] 클릭.
3. Gemini가 DCR 및 메타데이터를 자동 감지하여 Claire 승인 페이지를 엽니다.
4. 브라우저에서 **[✅ 바로 승인하기]** (또는 스마트폰 텔레그램 알림에서 **[승인]**) 클릭 ➡️ 즉시 연동 완료!

### 5.2 Claude Desktop / Claude Code (`settings.json`)
```json
{
  "mcpServers": {
    "claire": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-fetch",
        "https://cb.netspheres.org/mcp"
      ],
      "headers": {
        "Authorization": "Bearer <access_token_or_session_token>"
      }
    }
  }
}
```

---

## 6. 테스트 및 검증 이력

- **단위/통합 테스트**: `tests/test_oauth_flow.py` (RFC 9728, RFC 8414, DCR, PKCE, 세션 격리, Refresh Token 회전 검증 100% 통과).
- **기존 회귀 테스트**: `tests/test_api_mcp.py`, `tests/test_api_security.py` 등 총 1,238개 테스트 스위트 전원 통과.
- **Git Commit**: `a1dc9f9` (`feat(mcp): implement OAuth 2.1 authorization server and discovery for Gemini apps`).
