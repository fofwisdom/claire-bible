# RFC 8693 OAuth 2.0 Token Exchange Contract

이 문서는 테제 계보(`blackan/claire_bible`)와 증강 계보(`fofwisdom/claire-bible`)가 토큰 교환, 인가 위임과 세션 전환을 상호 호환 가능하게 처리하기 위한 RFC 8693 (OAuth 2.0 Token Exchange) 최소 공통 계약을 정의한다.
인증 채널이 다른 클라이언트(웹 UI, Telegram 봇, MCP 에이전트, CI 파이프라인)가 동일한 프로토콜로 Bearer 접근 토큰을 발급받거나 다운스케이프하기 위한 유일한 와이어 규약이다.

계약 상태는 증강 계보(`fofwisdom`)에서 `adopted`, 테제 계보(`blackan`)에서 `candidate`다.
테제 계보 병합 후에만 `accepted`로 갱신한다.

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
