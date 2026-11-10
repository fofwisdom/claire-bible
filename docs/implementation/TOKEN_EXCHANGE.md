# RFC 8693 OAuth 2.0 Token Exchange Contract

이 문서는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 토큰 교환, 인가 위임과 세션 전환을 상호 호환 가능하게 처리하기 위한 RFC 8693 (OAuth 2.0 Token Exchange) 최소 공통 계약을 정의한다. 인증 채널이 다른 클라이언트(웹 UI, Telegram 봇, MCP 에이전트, CI 파이프라인)가 동일한 프로토콜로 Bearer 접근 토큰을 발급받거나 다운스케이프하기 위한 유일한 와이어 규약이다.

> [!NOTE] `POST /oauth/token` 엔드포인트는 본 RFC 8693 외에도, Google Gemini 등 MCP 클라이언트의 표준 OAuth 2.1 인가를 위한 `grant_type=authorization_code` (PKCE S256) 및 `grant_type=refresh_token`을 단일 엔드포인트에서 통합 처리한다. 상세 사양은 [`MCP_SUPPORT.md`](../design/MCP_SUPPORT.md)를 참고한다.

- **[결정] 테제 계보는 더 이상 없다** (소유자, 2026-11-02). 이 문서는 이 저장소의 토큰 교환 계약 정본이다. §1–§6은 와이어 계약이고, 현재 구현이 실제로 받는 범위는 §7이다. 둘이 다르면 §7을 따른다.

## 1. 프로토콜 기본 사양

- 엔드포인트: `POST /oauth/token`
- 요청 Content-Type: `application/x-www-form-urlencoded`
- 응답 Content-Type: `application/json;charset=UTF-8`
- 캐시 정책: `Cache-Control: no-store`, `Pragma: no-cache` 필수 반환
- 보안 경계: 엔드포인트 자체는 HTTP 수준에서 `public` 접근을 허용하되, 요청 본문의 `subject_token` 유효성 검증을 통해 인가를 완료한다.

## 2. 요청 매개변수 규약 (RFC 8693 §2.1)

| 매개변수 | 필수 여부 | 형식 | 계약 불변식 및 설명 |
|---|---|---|---|
| `grant_type` | **필수 (REQUIRED)** | URI | 반드시 `urn:ietf:params:oauth:grant-type:token-exchange` 문자열이어야 한다. |
| `subject_token` | **필수 (REQUIRED)** | String | 교환 대상 원본 보안 토큰 (32~128자 URL-safe 문자열). |
| `subject_token_type` | **필수 (REQUIRED)** | URI | `subject_token`의 형식 식별자 (아래 URN 레지스트리 참조). |
| `resource` | 선택 (OPTIONAL) | URI | 토큰이 사용될 대상 서비스의 절대 URI (예: `https://claire.example.com/mcp`). |
| `audience` | 선택 (OPTIONAL) | String / URI | 의도된 토큰 수신자 논리 식별자 (예: `claire-mcp`, `claire-web`). |
| `scope` | 선택 (OPTIONAL) | String | 요청하는 권한 스코프 (공백 구분, `owner` 또는 `readonly`). 생략 시 원본 토큰의 권한에 따라 결정. |
| `requested_token_type` | 선택 (OPTIONAL) | URI | 발급 희망 토큰 형식. 생략 시 인가 서버 기본값(`urn:ietf:params:oauth:token-type:access_token`) 적용. |
| `actor_token` | 선택 (OPTIONAL) | String | 위임/대리 실행 주체를 증명하는 보안 토큰. |
| `actor_token_type` | 조건부 필수 | URI | `actor_token`이 제공된 경우 반드시 함께 명시해야 한다. |

## 3. URN 식별자 레지스트리 (RFC 8693 §3)

양 계보 구현은 다음 표준 및 도메인 URN 식별자를 상호 호환 계약으로 채택한다.

### 3.1 표준 URN (`urn:ietf:params:oauth:token-type:`)
- `urn:ietf:params:oauth:token-type:access_token`: OAuth 2.0 Bearer 접근 토큰
- `urn:ietf:params:oauth:token-type:refresh_token`: OAuth 2.0 갱신 토큰
- `urn:ietf:params:oauth:token-type:id_token`: OpenID Connect ID 토큰
- `urn:ietf:params:oauth:token-type:jwt`: 일반 JSON Web Token

### 3.2 도메인 확장 URN (`urn:claire:params:oauth:token-type:`)
- `urn:claire:params:oauth:token-type:telegram-bootstrap`: Telegram 봇이 발행한 일회용 원격 접속 티켓
- `urn:claire:params:oauth:token-type:session-token`: SQLite `auth_sessions`에 기록된 세션 토큰

## 4. 응답 매개변수 규약 (RFC 8693 §2.2)

토큰 교환 성공 시 HTTP `200 OK`와 함께 다음 JSON 객체를 반환한다.

```json
{
  "access_token": "s8dfA_77bN0Q81zKx...new_token...9A",
  "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
  "token_type": "Bearer",
  "expires_in": 604800,
  "scope": "owner"
}
```

- `access_token` (필수): 새롭게 발행된 URL-safe 접근 토큰 문자열.
- `issued_token_type` (필수): 발행된 토큰 형식 URN (`urn:ietf:params:oauth:token-type:access_token`).
- `token_type` (필수): 토큰 스키마 식별자 (RFC 6750에 따라 대소문자 무관하게 `Bearer` 사용).
- `expires_in` (권장): 토큰의 유효 기간(초 단위, Claire 기본 세션 수명은 7일 = `604800`초).
- `scope` (조건부 필수): 부여된 최종 권한 스코프 (`owner` 또는 `readonly`). 요청 스코프와 다른 경우 반드시 명시.
- `refresh_token` (선택): 토큰 로테이션 체인을 유지할 때 선택적으로 반환.

## 5. 오류 응답 규약 (RFC 8693 §2.3 및 RFC 6749 §5.2)

교환 실패 시 HTTP `400 Bad Request` (클라이언트 자격증명 무효 시 `401 Unauthorized`)와 함께 JSON 본문을 반환한다.

```json
{
  "error": "invalid_grant",
  "error_description": "The subject_token is expired or has already been consumed."
}
```

- 표준 오류 코드: `invalid_request`, `invalid_client`, `invalid_grant`, `unauthorized_client`, `unsupported_grant_type`, `invalid_scope`.
- RFC 8693 확장 오류 코드:
  - `invalid_target`: 지정한 `resource` 또는 `audience`를 인식할 수 없거나 허용되지 않음.
  - `unsupported_token_type`: 지원되지 않는 `subject_token_type` 또는 `requested_token_type` 요청.

## 6. 토큰 원자적 소비와 로테이션 불변식

1. 일회용 부트스트랩 토큰(`telegram-bootstrap`)은 단일 트랜잭션 안에서 소비와 동시에 새로운 세션 토큰으로 회전되어야 한다.
2. 동일한 일회용 토큰의 재사용 시도(Replay Attack)는 즉시 `invalid_grant` 오류로 거부되어야 한다.
3. 세션 토큰 다운스케이프 시 원본 세션이 가진 권한 스코프를 초과하는 `scope` 요청은 `invalid_scope`로 즉시 차단되어야 한다.
4. 토큰 교환 실패 시 원본 저장소의 데이터나 세션 상태를 손상시키지 않고 트랜잭션을 롤백해야 한다.

## 7. 현재 구현 범위

확인 기준: 계정·IdP·Fedify 준비 봉인 커밋 `7bc536f` (2026-11-09), `src/claire/api/oauth.py`의 `handle_token`. 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다.

- **[코드] 받는 토큰**: `subject_token`은 `validate_session_binding`(`src/claire/store/accounts.py`)이 계정 귀속을 확인할 수 있는 승인된 세션 토큰(`auth_sessions`)이어야 한다. 없거나, 만료되었거나, 계정에 귀속되지 않은 토큰은 400 `invalid_grant`다. `subject_token`이 비어 있으면 400 `invalid_request`다.
- **[코드] 계정 상태**: 귀속 계정이 없거나 상태가 `CREDENTIAL_ACCEPTING_STATUSES`(`setup_required`, `active`)가 아니면 400 `invalid_grant`다.
- **[코드] 발급 토큰**: 그 계정에 귀속된 `readonly` OAuth 접근 토큰과 갱신 토큰이다. `expires_in`은 `OAUTH_ACCESS_TOKEN_TTL`(30일, `2592000`)이다. 클라이언트 식별자는 `token_exchange_client`다.
- **[코드] 읽지 않는 매개변수**: `subject_token_type`, `scope`, `resource`, `audience`, `requested_token_type`, `actor_token`, `actor_token_type`의 값은 판정에 쓰지 않는다. 요청한 `scope`와 무관하게 `readonly`만 발급한다. §3.2의 `telegram-bootstrap` 교환은 이 엔드포인트에 구현되어 있지 않다.
- **[코드] 폴백 없음**: 계정을 정하지 못한 요청을 관리자 계정(`accounts.id = 1`)으로 발급하는 분기는 없다(PRINCIPLES C1). 집행: `tests/test_oauth_token_exchange.py`.
- **[결정] Fedify 사이드카는 토큰 교환을 쓰지 않는다.** 사이드카에는 계정이 없으므로 계정 귀속 토큰을 받을 수 없다. 사이드카는 내부 연합 API 전용 서비스 자격증명을 쓴다([설계](../design/ACCOUNT_IDP_AND_FEDIFY_SIDECAR_INTEGRATION_DESIGN.md) §6.1, §8.3, 미구현).
