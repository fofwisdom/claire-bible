# Support Bundle 기반 MCP(Model Context Protocol) 팩트 엔지니어링 및 관측성 아키텍처 설계

작성일: 2026-09-22 · 상태: **설계 확정 및 로드맵 수립 (Planned)** · 기준: [GOALS.md](../../upstream/GOALS.md) 트랙 3(검색·UX·웹 UI 및 관측성) · 관련: [`MCP_SUPPORT.md`](MCP_SUPPORT.md), [`TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md`](TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md), [`VMWARE_EXPLORE_ACTIVE_WAITING_RESILIENCE_DESIGN.md`](VMWARE_EXPLORE_ACTIVE_WAITING_RESILIENCE_DESIGN.md)

---

## 1. 개요 및 운영 Support Bundle 실측 팩트 엔지니어링

Claire는 Google Gemini 맞춤형 연결 앱(Spark), Claude Desktop, Antigravity, Cursor 등 외부 AI 하네스와의 상호 운용성을 위해 RFC 6749/6750/7591/7636/8414/9728 기반 OAuth 2.1 인가 서버 및 Model Context Protocol (MCP) Streamable HTTP 엔드포인트(`/mcp`)를 구축하였다([`MCP_SUPPORT.md`](MCP_SUPPORT.md)).

그러나 프로덕션 운영 환경에서 발행된 실제 Support Bundle을 다운로드하여 아티팩트 전량을 해체·검증한 결과, **외부 AI 에이전트의 MCP 연동 및 OAuth 인가 장애를 추적하기 위한 핵심 팩트 데이터가 완전히 누락(100% Blind Spot)**되어 있음이 입증되었다.

### 1.1 운영 Support Bundle 실측 하드 팩트 (`sb_20260922_140548_f84b2c22`)

- **검증 대상 URL**: `https://cb.netspheres.org/support/bundle?token=sb3_qbPX_BGDtYNhZ48ojdpKf2xSWD2wocdZozARBNnRjG0`
- **아카이브 규격**: Zstandard 압축 아카이브 (`tar.zst`, bundle format v3, 31KB)
- **생성 시각**: `2026-09-22 23:05:48 KST` (`2026-09-22T14:05:48.306335+00:00`)
- **실측 번들 아티팩트 구조 전수 점검 결과**:

| 디렉터리 / 파일 경로 | 현재 수집 내용 | MCP 관련 팩트 정보 존재 여부 및 결손 분석 |
| :--- | :--- | :--- |
| `manifest.json` | format_version(3), 생성/만료시각, Git SHA, 빌드 정보 | **전무**. MCP 지원 여부, 프로토콜 사양, 엔드포인트 메타데이터 부재. |
| `diagnostics/system.json` | OS, Python, CPU(2 vCPU), SQLite, zstd, `provider_env`(agy) | **결손**. Python `mcp` SDK 버전, Uvicorn/Starlette ASGI 환경 정보 없음. |
| `diagnostics/config_sanitized.json` | LLM 프로바이더 및 인제스트 설정, FQDN, CORS | **결손**. `cors_allowed_origins`에 `gemini.google.com`만 기재되어 있을 뿐, MCP 엔드포인트 활성화 상태, OAuth 인가 설정 부재. |
| `diagnostics/storage.json` | 바인드 마운트, inode, DB 파일 경로 | **정상(스토리지 전용)**. DB 파일 경로만 수록. |
| `diagnostics/build.json` | 이미지 태그, 패키지 버전(0.1.0), 스키마 버전(13) | **결손**. MCP 프로토콜 스펙 버전, OAuth 2.1 지원 계보 정보 없음. |
| `diagnostics/collector_warnings.json` | 수집기 경고 목록 | **해당 없음**. |
| `pipeline/health.json` | DB liveness(Theme 0, 1), inbox 카운트, 그래프 노드/엣지 수 | **완전 결손**. `/mcp` 엔드포인트 응답성, MCPServer 인스턴스 초기화 상태, 10종 툴 표면 헬스 정보 전무. |
| `pipeline/inbox_summary.json` | raw_inbox 상태별 통계 (done: 412, failed: 4) | **해당 없음 (인제스트 전용)**. |
| `pipeline/failed_items.json` | 인제스트 실패 상세 항목 (Scrapling 오류 등) | **해당 없음 (인제스트 전용)**. |
| `pipeline/shares_index.json` | 문서 공유 토큰 SHA-256 인덱스 | **해당 없음 (공유 링크 전용)**. |
| `pipeline/db_integrity.json` | 지식 DB quick_check, 테이블 카운트 | **중대 결손**. `oauth_clients`, `oauth_tokens`, `oauth_codes`, `oauth_auth_requests` 테이블이 DB에 존재함에도 집계 카운트에서 완전 배제됨. |
| `logs/agy.log` | Antigravity CLI stderr/stdout 로그 | **해당 없음 (LLM CLI 전용)**. agy 내부의 프롬프트 mcp_servers 스킵 경고 1건만 존재. |
| `logs/telegram.log` | 텔레그램 봇 인입/버튼 상호작용 로그 | **결손**. `/oauth/authorize/telegram-push` 푸시 및 인라인 버튼 승인 관련 로그는 분별 불가. |
| `telemetry/telemetry_records.jsonl` | `provider_telemetry` 테이블의 LLM 호출 레코드 전량 | **완전 결손**. 외부 에이전트의 MCP JSON-RPC 호출, 툴 호출, 토큰 인증 성공/실패 텔레메트리 0건 (수집 체계 자체 부재). |
| `telemetry/telemetry_stats.json` | LLM 추론 지연시간(p50/p95), 차단 사유별 통계 | **완전 결손**. MCP 툴별 호출 빈도, 에러율, 레이턴시 통계 전무. |

---

## 2. 근본 문제 및 결손 분석 (Root Cause & Gap Analysis)

### 2.1 관측성 사각지대 (Observability Blind Spot)
현재 Support Bundle 아키텍처([`TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md`](TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md))는 **"문서 인제스트 파이프라인(URL/PDF/동영상 수집)과 LLM 프로바이더(`agy`/`gemini`)의 비정상 요약·차단 분석"**만을 목적으로 설계되었다.

그 결과, Claire의 핵심 가치 중 하나인 **"외부 AI 클라이언트의 지식 그래프 탐색 인터페이스(MCP/OAuth 2.1)"**에 대한 관측성 파이프라인이 전무하여 다음과 같은 현장 장애 발생 시 원격 근본 원인 분석(RCA)이 불가능하다:

1. **OAuth 2.1 인가 핸드셰이크 실패**:
   - Google Gemini Spark 또는 Claude Desktop 연결 시 `GET /.well-known/oauth-protected-resource` 또는 `POST /oauth/register` 실패.
   - 브라우저 PKCE 검증 실패, 리다이렉트 URI 불일치, Telegram Push 인가 타임아웃 발생 시 원인 규명 불가.
2. **토큰 무효 및 만료 (401 Unauthorized)**:
   - 외부 에이전트가 `POST /mcp` 호출 시 Bearer 토큰 인증 실패가 발생해도 어떤 client_id에서 몇 건이 거부되었는지 텔레메트리 부재.
   - Refresh Token 자동 회전(Rotation) 실패 여부 추적 불가.
3. **MCP JSON-RPC 프로토콜 및 도구 실행 결함**:
   - 에이전트가 특정 도구(예: `neighbors`, `find_paths`, `synthesis_context`) 호출 시 타임아웃, 대용량 그래프 순회로 인한 메모리 급증, FTS5 쿼리 문법 에러 발생 여부 기록 전무.
   - 단일 요청에 과도한 토큰/데이터를 반환하는 도구 파라미터 파악 불가.
4. **API 서버 액세스 로그의 번들 누락**:
   - `docker-compose.yml` 상에서 `api` 컨테이너(ASGI Uvicorn)의 로그는 stdout으로만 출력되고 파일로 영구 적재되지 않아 Support Bundle에 `api.log`가 전혀 포함되지 않음.

---

## 3. Support Bundle MCP 관측성 확장 아키텍처 설계

```mermaid
graph TD
    subgraph External_Agents [외부 AI 에이전트 클라이언트]
        Gemini[Google Gemini Spark]
        Claude[Claude Desktop / Code]
        Antigravity[Antigravity / Cursor]
    end

    subgraph ASGI_Gateway [Starlette / Uvicorn API 서버]
        OAuthEndpoint["/oauth/* (RFC 7591/7636/8414/9728)"]
        MCPEndpoint["/mcp (Streamable HTTP / JSON-RPC 2.0)"]
        AccessLogger["claire.api.access (구조화 로그)"]
    end

    subgraph Primary_Storage [정본 지식 DB]
        ClaireDB[("data/claire.db")]
        OAuthTables["oauth_clients / oauth_tokens / oauth_auth_requests"]
        ClaireDB --- OAuthTables
    end

    subgraph Isolated_Telemetry [격리된 관측성 DB (Fire-and-Forget)]
        TelemetryDB[("data/telemetry.db")]
        ProviderTable["provider_telemetry"]
        MCPTable["mcp_telemetry (신규)"]
        TelemetryDB --- ProviderTable
        TelemetryDB --- MCPTable
    end

    subgraph Support_Bundle_Collector [Support Bundle 생성기 (Format v4)]
        DiagnosticsCollector["diagnostics/mcp.json (신규)"]
        HealthCollector["pipeline/health.json (MCP 헬스 확장)"]
        OAuthCollector["pipeline/oauth_summary.json (신규)"]
        DBIntegrityCollector["pipeline/db_integrity.json (OAuth 카운트 확장)"]
        LogCollector["logs/api.log (신규 수집)"]
        MCPTelemetryCollector["telemetry/mcp_records.jsonl & stats (신규)"]
    end

    External_Agents -->|OAuth 2.1 핸드셰이크| OAuthEndpoint
    External_Agents -->|Bearer Token + JSON-RPC| MCPEndpoint
    OAuthEndpoint --> OAuthTables
    MCPEndpoint -->|FTS5 / SQLite Read-Only| ClaireDB
    MCPEndpoint -.->|비동기 계측| MCPTable
    OAuthEndpoint -.->|비동기 계측| MCPTable
    ASGI_Gateway -->|파일 로그 적재| AccessLogger

    DiagnosticsCollector --> Bundle[("support_bundle_*.tar.zst")]
    HealthCollector --> Bundle
    OAuthCollector --> Bundle
    DBIntegrityCollector --> Bundle
    LogCollector --> Bundle
    MCPTelemetryCollector --> Bundle

    style Bundle fill:#fef3c7,stroke:#f59e0b,stroke-width:2px
    style MCPTable fill:#d1fae5,stroke:#10b981,stroke-width:2px
    style DiagnosticsCollector fill:#e0f2fe,stroke:#0284c7,stroke-width:1px
    style OAuthCollector fill:#e0f2fe,stroke:#0284c7,stroke-width:1px
    style MCPTelemetryCollector fill:#e0f2fe,stroke:#0284c7,stroke-width:1px
```

---

## 4. 신규 및 확장 아티팩트 상세 설계 규격

### 4.1. 신규 아티팩트: `diagnostics/mcp.json`
MCP 서버의 런타임 환경, 지원 프로토콜 규격, 등록된 10종 툴 표면 메타데이터를 정적으로 진단한다.

```json
{
  "mcp_runtime": {
    "sdk_version": "1.3.0",
    "protocol_version": "2024-11-05",
    "transport": "streamable_http",
    "endpoint_path": "/mcp",
    "public_endpoint_url": "https://cb.netspheres.org/mcp"
  },
  "oauth_config": {
    "issuer": "https://cb.netspheres.org",
    "authorization_endpoint": "https://cb.netspheres.org/oauth/authorize",
    "token_endpoint": "https://cb.netspheres.org/oauth/token",
    "registration_endpoint": "https://cb.netspheres.org/oauth/register",
    "protected_resource_metadata": "https://cb.netspheres.org/.well-known/oauth-protected-resource",
    "authorization_server_metadata": "https://cb.netspheres.org/.well-known/oauth-authorization-server",
    "scopes_supported": ["readonly"],
    "grant_types_supported": [
      "authorization_code",
      "refresh_token",
      "urn:ietf:params:oauth:grant-type:token-exchange"
    ],
    "code_challenge_methods_supported": ["S256"],
    "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"]
  },
  "tools_registered": [
    {
      "name": "resolve_entity",
      "description": "이름 문자열로 노드 ID 검색 (정확 일치 -> 별칭 -> FTS5 fuzzy 폴백)",
      "read_only": true,
      "parameters": ["name"]
    },
    {
      "name": "search",
      "description": "FTS5 기반 지식 검색 (LLM 호출 0)",
      "read_only": true,
      "parameters": ["query", "entity_type", "near_ids", "limit"]
    },
    {
      "name": "neighbors",
      "description": "다중 노드의 1홉 이웃 합집합 및 degree 조회",
      "read_only": true,
      "parameters": ["entity_ids", "exclude_ids", "limit"]
    },
    {
      "name": "path",
      "description": "두 노드 사이의 최단 연결 경로(BFS) 탐색",
      "read_only": true,
      "parameters": ["from_id", "to_id", "max_hops"]
    },
    {
      "name": "context",
      "description": "노드들의 observations, 연결 관계, 출처 요약 종합 반환",
      "read_only": true,
      "parameters": ["entity_ids", "compact"]
    },
    {
      "name": "overview",
      "description": "지식베이스 전체 엔티티 분포 및 주요 허브 노드 개요",
      "read_only": true,
      "parameters": []
    },
    {
      "name": "node",
      "description": "단일 노드 상세 정보 및 연결 문서(최대 10건) 조회",
      "read_only": true,
      "parameters": ["id", "full"]
    },
    {
      "name": "documents",
      "description": "문서 타임라인 목록 조회 (최대 100건)",
      "read_only": true,
      "parameters": ["limit", "since", "query"]
    },
    {
      "name": "document",
      "description": "단일 문서 본문 및 메타데이터 열람 (mark_seen 부작용 없음)",
      "read_only": true,
      "parameters": ["id"]
    },
    {
      "name": "stats",
      "description": "데이터베이스 통계 요약 (문서/엔티티/관계 수)",
      "read_only": true,
      "parameters": []
    }
  ]
}
```

---

### 4.2. 신규 아티팩트: `pipeline/oauth_summary.json`
외부 에이전트의 OAuth 2.1 등록 현황, 활성 토큰 수, 클라이언트별 인가 상태를 안전하게 마스킹하여 진단한다.
(시크릿, 토큰 원문, 인가 코드는 **절대 포함하지 않으며 SHA-256 해시 프리픽스 및 메타데이터만 포함**한다.)

```json
{
  "summary": {
    "total_registered_clients": 3,
    "active_tokens_count": 5,
    "expired_tokens_count": 2,
    "pending_auth_requests_count": 0
  },
  "clients": [
    {
      "client_id": "claire_mcp_a1b2c3d4",
      "client_name": "Google Gemini Spark",
      "redirect_uris": ["https://gemini.google.com/oauth/callback"],
      "created_at": "2026-09-20T10:15:00+00:00",
      "active_tokens": 2
    },
    {
      "client_id": "claire_mcp_e5f6g7h8",
      "client_name": "Claude Desktop",
      "redirect_uris": ["http://localhost:3300/callback"],
      "created_at": "2026-09-21T14:20:00+00:00",
      "active_tokens": 1
    }
  ],
  "token_inventory": [
    {
      "client_id": "claire_mcp_a1b2c3d4",
      "token_sha256_prefix": "e3b0c442",
      "scope": "readonly",
      "created_at": "2026-09-20T10:15:05+00:00",
      "expires_at": "2026-10-20T10:15:05+00:00",
      "is_expired": false,
      "has_refresh_token": true
    }
  ]
}
```

---

### 4.3. 기존 아티팩트 확장: `pipeline/db_integrity.json`
기존에 `documents`, `entities`, `relations` 등 지식 그래프 테이블만 집계하던 `counts`에 OAuth 테이블군을 필수 집계 항목으로 편입한다.

```json
{
  "claire_db_quick_check": "ok",
  "counts": {
    "documents": 300,
    "entities": 1696,
    "relations": 2718,
    "embeddings": 1696,
    "proposals": 75,
    "jobs": 0,
    "raw_inbox": 371,
    "extractions": 400,
    "refresh_queue": 0,
    "purged_tombstones": 14,
    "oauth_clients": 3,
    "oauth_tokens": 7,
    "oauth_codes": 0,
    "oauth_auth_requests": 0
  },
  "schema_version": 13,
  "schema_lineage": "claire-bible/common",
  "telemetry_db_quick_check": "ok"
}
```

---

### 4.4. 신규 로그 아티팩트: `logs/api.log`
현재 `agy.log`와 `telegram.log`만 수집되는 한계를 극복하기 위해, Starlette/Uvicorn API 서버가 `data/logs/api.log`에 회전 로깅(Rotating File Log)하도록 설정하고 Support Bundle이 이를 자동 수집한다.

- **수집 대상**:
  - `POST /mcp` 요청 및 HTTP 상태 코드 (200, 400, 401, 500)
  - `/.well-known/oauth-*` 메타데이터 요청
  - `/oauth/register`, `/oauth/authorize`, `/oauth/token` 요청 및 에러
  - 텔레그램 푸시 승인 통신 로그
- **마스킹 규칙**:
  - Authorization 헤더의 `Bearer <token>` ➔ `Bearer ***REDACTED***`
  - URL 쿼리 파라미터 `code`, `token`, `state` 마스킹
  - JSON Body의 `client_secret`, `code_verifier`, `refresh_token` 마스킹

---

### 4.5. 신규 텔레메트리 아티팩트: `telemetry/mcp_records.jsonl` 및 `telemetry/mcp_stats.json`
정본 DB와 물리적으로 분리된 `data/telemetry.db` 내에 `mcp_telemetry` 테이블을 신설하고, 외부 에이전트의 호출 이벤트를 Fire-and-Forget 방식으로 계측한다.

#### `data/telemetry.db` 물리 스키마 (`mcp_telemetry`)
```sql
CREATE TABLE IF NOT EXISTS mcp_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    client_id TEXT,
    call_type TEXT NOT NULL,         -- 'jsonrpc_handshake', 'tool_call', 'oauth_token'
    tool_name TEXT,                  -- 'search', 'neighbors', 'context', ...
    duration_ms INTEGER,
    status TEXT NOT NULL,            -- 'SUCCESS', 'UNAUTHORIZED', 'INVALID_PARAMS', 'ERROR'
    error_code INTEGER,              -- JSON-RPC error code (-32600, -32602 등) 또는 HTTP 상태
    error_message TEXT,
    response_bytes INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mcp_time ON mcp_telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_mcp_client ON mcp_telemetry(client_id);
CREATE INDEX IF NOT EXISTS idx_mcp_tool ON mcp_telemetry(tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_status ON mcp_telemetry(status);
```

#### `telemetry/mcp_records.jsonl` 샘플
```jsonl
{"id": 1, "timestamp": 1790074500.12, "client_id": "claire_mcp_a1b2c3d4", "call_type": "tool_call", "tool_name": "resolve_entity", "duration_ms": 12, "status": "SUCCESS", "error_code": null, "error_message": null, "response_bytes": 142}
{"id": 2, "timestamp": 1790074501.05, "client_id": "claire_mcp_a1b2c3d4", "call_type": "tool_call", "tool_name": "neighbors", "duration_ms": 45, "status": "SUCCESS", "error_code": null, "error_message": null, "response_bytes": 3840}
{"id": 3, "timestamp": 1790074505.80, "client_id": "unknown", "call_type": "jsonrpc_handshake", "tool_name": null, "duration_ms": 2, "status": "UNAUTHORIZED", "error_code": 401, "error_message": "Invalid Bearer Token", "response_bytes": 78}
```

#### `telemetry/mcp_stats.json` 샘플
```json
{
  "total_mcp_calls": 1250,
  "successful_calls": 1242,
  "failed_calls": 8,
  "success_rate": 0.9936,
  "latencies_ms": {
    "p50": 18.0,
    "p95": 85.0,
    "p99": 210.0
  },
  "calls_by_tool": {
    "resolve_entity": 420,
    "neighbors": 380,
    "context": 210,
    "search": 150,
    "overview": 50,
    "node": 40
  },
  "errors_by_type": {
    "UNAUTHORIZED": 5,
    "INVALID_PARAMS": 2,
    "TIMEOUT": 1
  }
}
```

---

## 5. 보안 및 안전성 원칙 (Security & Isolation)

1. **지식 DB 무부하/무경합 원칙 유지**:
   - `mcp_telemetry` 기록은 정본 지식 DB(`claire.db`)가 아닌 격리된 `telemetry.db`에만 단기 타임아웃(1초) WAL 모드로 비동기 기록된다.
   - 텔레메트리 기록 실패가 MCP 툴 호출 결과를 실패시키지 않는 Fire-and-Forget 원칙을 철저히 고수한다.
2. **자격증명 소각 및 해시화**:
   - `oauth_summary.json` 및 `logs/api.log`에는 일체의 평문 `client_secret`, `access_token`, `refresh_token`, `auth_code`가 유입되지 않는다.
   - 토큰의 식별과 대조는 단방향 SHA-256의 앞 8자리 접두어만을 활용하여 완전한 익명화 상태에서 진단한다.
3. **Support Bundle 자동 파기 일치**:
   - 신규 추가된 MCP 관측성 아티팩트 역시 기존의 6시간 자동 파기 수명주기(`SUPPORT_BUNDLE_TTL_SECONDS = 21600`)를 엄격히 준수한다.

---

## 6. 구현 로드맵 및 검증 계획

### 6.1 단계별 구현 로드맵

- **Phase 1: Support Bundle 정적 진단 및 스토리지 확장 (즉시 적용 가능)**
  - `src/claire/store/db.py`의 `counts()`에 OAuth 테이블 4종(`oauth_clients`, `oauth_tokens`, `oauth_codes`, `oauth_auth_requests`) 추가.
  - `src/claire/support_bundle.py`에 `diagnostics/mcp.json` 및 `pipeline/oauth_summary.json` 수집기 구현.
  - `docker-compose.yml` 및 `claire serve-api`에 `data/logs/api.log` 파일 로거 연결 및 Support Bundle 수집 대상 등록.
- **Phase 2: 격리된 MCP 텔레메트리 서브시스템 구축**
  - `src/claire/store/telemetry.py`에 `mcp_telemetry` DDL 및 `record_mcp_call()`, `query_mcp_telemetry()`, `mcp_summary_stats()` 구현.
  - `src/claire/api/mcp_tools.py` 및 `server.py`의 미들웨어 계층에 비동기 텔레메트리 인터셉터 장착.
  - `support_bundle.py`에 `telemetry/mcp_records.jsonl` 및 `telemetry/mcp_stats.json` 패키징 추가.

### 6.2 검증 계획
- `tests/test_support_bundle.py`: 번들 압축 해제 후 `diagnostics/mcp.json`, `pipeline/oauth_summary.json`, `pipeline/db_integrity.json` 내 OAuth 카운트 검증 테스트 추가.
- `tests/test_api_mcp.py`: MCP 툴 호출 시 `telemetry.db`의 `mcp_telemetry` 테이블에 레코드가 정상 적재되는지 단위 테스트 검증.
- `tests/test_api_security.py`: 마스킹 정규식이 `Bearer` 토큰 및 `client_secret`을 100% 마스킹하는지 회귀 테스트.
