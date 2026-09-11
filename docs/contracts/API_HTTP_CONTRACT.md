# Common HTTP API & Wire Security Contract

이 문서는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 공유하는 HTTP REST/ASGI 와이어 프로토콜, 인증 채널 및 보안 경계의 최소 공통 계약을 정의한다.
외부 클라이언트(Web UI, CLI, Telegram 봇, MCP 에이전트)가 구현 계보에 무관하게 안전하고 일관되게 통신하기 위한 프로토콜 규약이다.

계약 상태는 증강 계보(`fofwisdom`)에서 `adopted`, 테제 계보(`blackan`)에서 `candidate`다.
테제 계보 병합 후에만 `accepted`로 갱신한다.

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
| `owner` | 모든 읽기/쓰기, 테마 관리, 지식 적재, 번들 생성 | 마스터 인젝트 토큰(`CLAIRE_INJECT_TOKEN`) 또는 승인된 owner 세션 |
| `readonly` | 모든 공개/비공개 지식 조회, 검색, MCP 툴 호출 | 읽기 전용 토큰(`CLAIRE_READONLY_TOKEN`) 또는 readonly 세션 |
| `anonymous` | 공개 지식 조회 및 기본 FTS 검색 (숨김 문서 제외) | 자격증명이 없으나 `CLAIRE_ANONYMOUS_READONLY=1`인 경우 |

### 3.4 스텔스 모드 불변식
`CLAIRE_ANONYMOUS_READONLY=0`(프라이빗/스텔스 모드)로 구동되는 인스턴스는 자격증명이 없는 읽기 요청에 대해 `401 Unauthorized` 대신 HTTP `404 Not Found`를 반환해야 한다.
이는 외부 정찰에 대해 엔드포인트의 존재 여부와 파라미터 구조를 일절 노출하지 않기 위함이다.

## 4. 공통 에러 페이로드 규약

1. **표준 JSON 에러**: 모든 비정상 상태(4xx, 5xx)의 JSON 응답은 최소 `{"error": "<detail_string>"}` 형태를 보장한다.
2. **MCP 및 RFC 6750 에러**: `/mcp` 엔드포인트는 토큰 무효 시 HTTP `401 Unauthorized`와 함께 `WWW-Authenticate: Bearer error="invalid_token", error_description="..."` 헤더를 반환한다.
3. **내부 서버 오류 은폐**: 500 오류 시 데이터베이스 경로나 내부 스택 트레이스를 반환하지 않고 `{"error": "internal server error"}`로 정제하여 반환한다.

## 5. 최소 공통 엔드포인트 목록

양 계보 구현이 상호 호환성을 위해 제공하는 최소 공통 엔드포인트 집합은 다음과 같다.

| Method | Path | 최소 접근 수준 | 설명 |
|---|---|---|---|
| `GET` | `/health` | `public` | 서비스 liveness 및 DB 연결 검사 (`{"ok": true}`) |
| `GET` | `/whoami` | `read` | 현재 요청의 인증 스코프 확인 (`{"scope": "..."}`) |
| `GET` | `/stats` | `read` | 문서, 엔티티, 릴레이션 카운트 집계 |
| `POST` | `/oauth/token` | `public` | [RFC 8693] 토큰 교환 및 Bearer 토큰 발급 |
| `POST` | `/search` | `read` | 하이브리드 / FTS 키워드 지식 검색 |
| `GET` | `/documents` | `read` | 아카이브된 문서 목록 조회 |
| `GET` | `/document` | `read` | 특정 문서 상세 및 본문 조회 |
| `GET` | `/graph` | `read` | 전체 온톨로지 지식 그래프 데이터 (JSON) |
| `GET` | `/node` | `read` | 특정 엔티티 노드 세부정보 및 연결선 |
| `POST` | `/ingest` | `owner` | URL/텍스트 지식 적재 및 온톨로지 추출 |
| `POST` | `/support/bundle` | `owner` | 진단 및 지원 번들 생성 |
| `GET` | `/support/bundle` | `public` | 유효 토큰 기반 진단 번들 zstd 아카이브 다운로드 |
| `POST` | `/mcp` | `read` | Model Context Protocol JSON-RPC 통신 |

구현별 UI 페이지(`/`, `/docs`, `/p`) 및 부가 기능은 각 계보의 확장 라우트로 관리하되 위 최소 공통 엔드포인트의 와이어 형식을 훼손하지 않는다.
