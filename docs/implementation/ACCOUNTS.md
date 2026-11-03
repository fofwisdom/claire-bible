# 계정 구현 정본 (Accounts Implementation Reference)

확인 기준: P3 계정·초대·수명주기 구현 (2026-11-03) · 대상 독자: 계정·로그인·자격증명·초대 코드를 고치거나 확장하는 에이전트 · 설계 배경: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §8, §13 (Rev. 2.3)

이 문서는 **지금 코드가 무엇을 어디서 어떻게 하는지**를 적는다. 범위는 설계의 P1(싱글 모드 계정 코어) 및 P3(초대 파이프라인, 계정 수명주기, 퇴역 USERNAME 정보 초기화 및 디스크 정리)이다. 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다.

- **[코드]** 기준 작업 트리의 소스에서 직접 확인한 사실
- **[결정]** 저장소 소유자가 정한 방침. 바꾸기 전에 소유자에게 묻는다
- **[미검증]** 확인하지 못한 것. 사실로 인용하지 않는다

---

## 0. 작업 전에 알아 둘 것

1. **[코드] 계정은 두 DB에 걸친다.** 계정 행과 자격증명의 계정 귀속은 `data/claire.db`(v15)에, 비밀번호 해시·로그인 실패 기록·감사 기록은 `data/auth/auth.db`에 있다. 두 DB를 한 트랜잭션으로 묶지 않는다. 쓰기 순서는 §5.1에 있다.
2. **[코드] 관리자는 `accounts.id = 1` 하나다.** 상수는 `src/claire/store/db.py`의 `ADMIN_ACCOUNT_ID`이고 `src/claire/store/accounts.py`가 다시 내보낸다. `idx_accounts_single_admin`이 `role='admin'` 행을 하나로 제한한다.
3. **[코드] P1의 기존 자격증명 발급 경로는 모두 관리자 계정으로 기록한다.** `create_session`, `create_auth_nonce`, `approve_auth_nonce`, `create_oauth_code`, `create_oauth_token`, `approve_oauth_auth_request`(모두 `src/claire/store/db.py`)와 `TokenStore.create_token`(`src/claire/store/token_store.py`)의 기본값이 `ADMIN_ACCOUNT_ID`다. 코드 주석은 P3 L3-e에서 명시 인자로 바꾼다고 적는다.
4. **[결정] 운영 서버에 접근하지 않는다.** 검증은 개발 모드와 단위 테스트로 한다.

## 1. 파일 지도

| 파일 | 역할 | 주요 기호 |
| :--- | :--- | :--- |
| `src/claire/store/db.py` | v15 스키마와 이관, 기존 자격증명 발급 함수의 계정 기록 | `_migrate_v15_accounts`, `_check_single_default_theme_per_account`, `_ensure_id_salt`, `ADMIN_ACCOUNT_ID`, `ADMIN_LEGACY_PUBLIC_ID_UNIX_MS` |
| `src/claire/store/accounts.py` | 계정 조회·설정 완료, 수명주기 전이, 세션 발급·검증, 퇴역 이름 정보 초기화 | `AccountStore`, `Account`, `CredentialBinding`, `CREDENTIAL_ACCEPTING_STATUSES`, `create_account_session`, `validate_session_binding`, `validate_oauth_binding`, `suspend_account`, `resume_account`, `schedule_account_deletion`, `cancel_account_deletion`, `purge_account`, `preview_release_username`, `release_username` |
| `src/claire/store/auth_db.py` | auth.db 스키마, scrypt 비밀번호, 초대 영속화, 실패 제한, 감사 기록 | `connect_auth_db`, `check_password_policy`, `set_password`, `verify_password`, `begin_login_attempt`, `settle_login_success`, `check_login_throttle`, `record_login_failure`, `record_admin_audit`, `SCRYPT_MAX_CONCURRENCY` |
| `src/claire/store/secrets.py` | AES-256-GCM 마스터 키 비밀 서비스 및 HMAC 파생 | `SecretService`, `CLAIRE_SECRET_KEY` |
| `src/claire/accounts/username_policy.py` | DB 없이 판정하는 USERNAME 규칙 | `validate_username`, `normalize_username`, `RESERVED_USERNAMES`, `instance_reserved_labels`, `UsernameRejected` |
| `src/claire/accounts/invites.py` | Crockford Base32 8자리 초대 코드 생성, 정규화, 매개변수 유효성 검증 | `InvitationInfo`, `normalize_invite_code`, `validate_invite_code_syntax`, `generate_invite_code`, `validate_invite_params`, `InviteError` |
| `src/claire/ids.py` | 시간 정렬 64비트 번호 | `timestamp_id`, `legacy_timestamp_id`, `insert_with_timestamp_id`, `format_public_id`, `parse_public_id` |
| `src/claire/api/principal.py` | 요청 주체 | `Principal`, `ANONYMOUS`, `request_principal`, `has_recent_auth`, `STEP_UP_WINDOW_SECONDS` |
| `src/claire/api/security.py` | 자격증명 검증과 `Principal` 배정, 라우트 접근 규칙, 실패 제한용 클라이언트 IP | `ROUTE_POLICY`, `AuthenticationMiddleware`, `_Credential`, `_bind_account`, `_env_token_credential`, `client_ip`, `parse_trusted_proxies` |
| `src/claire/api/accounts_routes/` | 계정 라우트(`setup.py`, `login.py`, `admin.py`, `me.py`, `invites.py`)와 공용 헬퍼(`common.py`) | `build_account_routes`, `prepare_auth_db`, `require_recent_auth`, `same_origin` |
| `src/claire/templates/` | `setup.html`, `login.html`, `me.html`, `admin_accounts.html` | `$이름` 자리채움(`common.render_template`) |
| `src/claire/static/js/accounts.js` | `/me` 화면의 표시 이름·본인 확인·비밀번호 양식 | `PATCH /api/me`, `POST /api/auth/reauth`, `POST /api/me/password` 호출 |

## 2. 계정 모델과 상태

**[코드]** 테이블 정의는 `_migrate_v15_accounts`에 있다. 열은 설계 §13.1과 같다: `id`, `public_id`(UNIQUE), `username`(UNIQUE, NULL 허용), `role`(`admin`·`member` CHECK), `status`, `display_name`, `profile_public`, `created_at`, `activated_at`, `delete_after`, `updated_at`.

| 항목 | 현재 동작 | 근거 |
| :--- | :--- | :--- |
| 상태 값 | `setup_required`, `active`, `suspended`, `deleting`, `purged`를 타입(`AccountStatus`)으로 정의한다. DB는 `status`에 CHECK를 두지 않는다 | [코드] `src/claire/store/accounts.py` |
| 상태 전이 | ① `setup_required → active`: 최초 관리자 설정 완료 시(`complete_setup`)<br>② `active ↔ suspended`: 관리자의 정지 및 재개. 정지 시 세션·API 토큰·OAuth 토큰이 즉시 거부된다<br>③ `active / suspended → deleting`: 계정 삭제 예약(`delete_after` 유예 시각 설정)<br>④ `deleting → active`: 삭제 예약 취소(`cancel_account_deletion`)<br>⑤ `deleting / suspended → purged`: 계정 영구 소각(`purge_account`). DB 행 cascade 삭제 및 참조 카운트 0 디스크 산출물 GC. USERNAME은 `username_tombstones`에 퇴역 기록 | [코드] `AccountStore`, `tests/test_username_release.py` |
| 자격증명을 받는 상태 | `setup_required`, `active`(`CREDENTIAL_ACCEPTING_STATUSES`). 그 밖의 상태(`suspended`, `deleting`, `purged`)이거나 계정 행이 없으면 자격증명을 "없는 자격증명"과 똑같이 다루어 접근을 즉시 차단하고 토큰 갱신을 거부한다 | [코드] `_bind_account`, `tests/test_principal.py`, `tests/test_token_oauth_mcp_isolation.py` |
| USERNAME 불변 | 트리거 `accounts_username_immutable`이 NULL이 아닌 `username`의 변경을 `ABORT`한다 | [코드] `tests/test_schema_v15.py` |
| 표시 이름 | 64자 이하, 유니코드 범주 `Cc`·`Cf`·`Zl`·`Zp` 문자 거부, 앞뒤 공백을 자르지 않는다 | [코드] `common.validate_display_name` |
| `public_id` | 관리자 행은 `legacy_timestamp_id("accounts", unix_ms=ADMIN_LEGACY_PUBLIC_ID_UNIX_MS, row_id=1)`. 상수는 2026-11-02T00:00Z의 밀리초다. JSON에서는 10진 문자열이다 | [코드] [결정] 설계 §16.3 (c) |
| `id_salt` | `instance_settings.id_salt`(32바이트 hex)가 없을 때만 만든다. 길이가 다르거나 hex가 아니면 이관이 실패한다 | [코드] `_ensure_id_salt` |
| v14 `users` | 그대로 두고 쓰지 않는다 | [코드] `tests/test_schema_v15.py` |

## 3. USERNAME 규칙

**[코드]** `validate_username`(`src/claire/accounts/username_policy.py`)이 아래 순서로 판정하고, 정규화한 이름을 반환하거나 `UsernameRejected(reason)`를 낸다.

1. **정규화**: 비ASCII가 하나라도 있으면 소문자 변환 **전에** `syntax`로 거부한다(`"K".lower() == "k"` 같은 접힘 방지). 그다음 `lower()`. 공백을 자르지 않는다. 저장되는 값은 이 소문자 값이다.
2. **문법**: `^[a-z][a-z0-9_]{2,29}$`를 `fullmatch`로 검사하고, `__`이 있거나 `_`로 끝나면 `syntax`.
3. **예약 이름**: `RESERVED_USERNAMES`(설계 §8.1-2 목록과 같음, `tests/test_username_policy.py`가 정확히 비교)와 일치하면 `reserved`.
4. **예약 조각**: `_`와 숫자로 나눈 조각 중 하나가 예약 이름이거나 `admin`으로 시작하면 `reserved`. `admin1`, `kim_admin`은 막히고 `badminton`은 통과한다. [결정] 이 규칙은 관리자 사칭 피싱을 막기 위해 유지한다(설계 §16.3 (a)).
5. **인스턴스 예약**: 호스트 이름의 모든 DNS 라벨과 정확히 일치하면 `reserved`. `claire.example.com`이면 `claire`, `example`, `com`이다. 라벨을 모으는 호스트는 공개 URL authority, `fqdn`, `effective_fqdn`, `site_domain` 설정값이다(`common.instance_hosts`). 포트는 뗀다.

- **[코드]** `retired`(`username_tombstones`에 있고 `released_at`이 NULL)와 `taken`(다른 계정이 사용)은 `AccountStore`와 `/setup` 입력 검사가 판정한다.
- **[코드]** 사유 코드는 `/setup` 화면에만 보인다. 익명으로 이름 사용 가능 여부를 묻는 API는 없다.

## 4. Principal과 자격증명 귀속

**[코드]** `AuthenticationMiddleware`가 요청마다 `request.state`에 `Principal` 하나를 둔다. 새 코드는 `request_principal(request)`을 쓴다. 미들웨어를 거치지 않은 요청에서 부르면 익명으로 대체하지 않고 `PrincipalMissing`을 낸다. `request_auth_scope`는 호환용으로 남아 있다.

`Principal` 필드: `account_id`(None = 익명), `instance_role`, `scope`(`owner`·`collaborator`·`readonly`·`anonymous`), `channel`(`cookie`·`bearer`·`x-session`·`none`), `credential_id`(감사용, 토큰 값이 아님), `authenticated_at`. 설계 §8.3과 달리 `authenticated_at`이 있다(설계 Rev. 2.2에 반영).

| 자격증명 | 저장 위치 | `account_id` 결정 | `credential_id` | `authenticated_at` |
| :--- | :--- | :--- | :--- | :--- |
| 환경변수 토큰 `CLAIRE_INJECT_TOKEN`, `CLAIRE_COLLABORATOR_TOKEN`, `CLAIRE_READONLY_TOKEN` | 환경변수 | 항상 관리자(1). DB를 읽지 않는다 | `env:<scope>` | None |
| 상시 API·MCP 토큰 | `data/auth/api_tokens.json` | 항목의 `account_id`. 키가 없으면 1. 값이 정수가 아니거나 0 이하이면 그 항목을 무시한다(경고 로그) | `api:<id>` | None |
| OAuth 액세스 토큰 | `oauth_tokens.account_id` | 행 값. NULL이면 거부 | 토큰 SHA-256 hex 앞 16자 | None |
| 세션(쿠키·`X-Session`·Bearer로 보낸 세션 토큰) | `auth_sessions.account_id`, `authenticated_at` | 행 값. NULL이면 거부 | `nonce` SHA-256 hex 앞 16자(토큰 회전에도 유지) | cookie·x-session 채널에서만 행 값. Bearer로 보낸 세션은 None |
| 텔레그램 승인·CLI 매직 링크 | `auth_sessions`(`approve_auth_nonce`, `create_session`) | P1에서는 관리자(1) | 세션과 같음 | 발급·승인·링크 교환 시각 |

- **[코드] 검증 순서(Bearer)**: API 토큰 → 환경변수 토큰 → OAuth 토큰 → 세션 토큰(cross-origin이 아닐 때). 그다음 계정 행을 읽고 상태를 확인한다(`_bind_account`). 환경변수 토큰만 계정 행을 읽지 않는다.
- **[코드] 익명**: 자격증명 없이 `read` 규칙 라우트에 익명 대체가 일어나면 `ANONYMOUS`.
- **[코드] [결정] public 라우트**: 자격증명을 검증하지 않고 `ANONYMOUS`를 둔다. 정적 자산 요청마다 DB를 열지 않기 위해서다(설계 §16.3 (e)). 그래서 계정을 알아야 하는 화면(`/setup`, `GET /`)은 public이 아닌 규칙이고, `/login`·`/logout` 핸들러는 쿠키 값을 검증 없이 읽기만 한다(`common.cookie_session_token`).
- **[코드] v15 이관**은 `account_id`가 NULL인 `themes`, `auth_sessions`, `oauth_codes`, `oauth_tokens`, `oauth_auth_requests` 행을 1로 채운다. `authenticated_at`은 채우지 않는다.
- **[코드] 테마 소유**: `ThemeInfo.account_id`. `themes.json` 항목에 키가 없으면 1, 값이 정수가 아니면 `ValueError`(`src/claire/store/theme.py`의 `_owner_account_id`).

## 5. 라우트 동작

접근 규칙은 `src/claire/api/security.py`의 `ROUTE_POLICY`에 있다. `owner` 규칙을 통과하지 못한 요청은 기존 거부 규칙대로 404다([API_HTTP_CONTRACT.md](API_HTTP_CONTRACT.md) §3). 쿠키 채널의 `POST`·`PUT`·`PATCH`·`DELETE`는 미들웨어가 동일 origin(`Origin` 헤더)을 요구하고 아니면 403이다(`_STATE_CHANGING_METHODS`). HEAD는 GET 경로에 함께 등록된다.

- **[코드] 양식 화면의 `Referrer-Policy`**: `common.render_template`로 만든 화면(`/setup`, `/login`, `/me`, `/admin/accounts`)은 `Referrer-Policy: same-origin`이다(`FORM_PAGE_REFERRER_POLICY`). 다른 응답은 전역 `no-referrer` 그대로다. 전역 값이면 Chromium이 양식 제출(navigate 모드 POST)에 `Origin: null`을 싣고 `CORSPolicyMiddleware`가 403으로 막는다. `Origin: null` 거부는 그대로다. `fetch()`(cors 모드)는 정책과 관계없이 실제 origin을 싣는다.
- **[코드] 세션 쿠키는 응답마다 하나**: 미들웨어는 쿠키 채널 응답에 세션 쿠키를 다시 붙여 만료를 늘리지만, 핸들러가 이미 `claire_session`을 설정하거나 지웠으면(`/setup` 확정) 붙이지 않는다(`_sets_session_cookie`).

### 5.1 `/setup` (최초 관리자 설정)

- **[코드] 열리는 조건**(`setup.setup_pending`): 주체가 계정 1, `instance_role=admin`, `scope=owner`, 채널 cookie 또는 x-session이고, 계정 상태가 `setup_required`. 그 밖(미인증, bearer, 이미 `active`)은 GET·POST 모두 404다.
- **[코드] `GET /`**: 위 조건이면 `/setup`으로 302(`Cache-Control: no-store`). Bearer는 보내지 않는다.
- **[코드] 입력**: `username`, `display_name`, `password`, `password_confirm`, `ack`(form). 문자열이 아닌 값이면 400.
- **[코드] [결정] 확인 문구**: "USERNAME은 바꿀 수 없다", "네트워크 모드에서는 `/@USERNAME`으로 공개된다"를 같은 화면에 적고, `ack=1` 필수 체크박스로 받는다. 별도 확정 화면을 두지 않는 것은 비밀번호를 브라우저로 되돌려 보내지 않기 위해서다(설계 §16.3 (d)).
- **[코드] 처리 순서**(`perform_setup`): 모든 입력 검사(USERNAME 규칙·퇴역·중복, 표시 이름, 비밀번호 정책·확인, 체크박스) → claire.db `complete_setup`(조건부 UPDATE 한 번, 동시 요청 중 하나만 성공) → auth.db `set_password` → 확정 전 세션을 지우고 새 owner 세션 발급(세션 고정 방지) → 감사 기록 `account.setup`.
- **[코드] 응답**: 검사 실패는 400과 사유 목록이 있는 화면. 동시 확정에서 졌으면 404. 성공은 303 `Location: /`와 새 세션 쿠키.
- **[코드] 비밀번호 저장만 실패한 경우**: 확정을 되돌리지 않는다. 303 `Location: /me?notice=password_not_saved`. 계정은 `active`이고 비밀번호가 없어 로그인만 막힌다. `/me`에서 단계 인증만으로 비밀번호를 설정한다.
- **[코드]** USERNAME을 정하는 CLI는 없다.

### 5.2 `/login`, `/logout`

- **[코드] `GET /login`**: public. 로그인 화면.
- **[코드] `POST /login`**: public. 핸들러가 동일 origin을 확인한다(`common.same_origin`): `Sec-Fetch-Site`가 있으면 `same-origin`이어야 하고, `Origin`이 있으면 같은 origin이어야 하며, 둘 다 없으면 403.
- **[코드] 처리 순서**(`perform_login`): 정규화 → 실패 제한 확인과 시도 기록(`begin_login_attempt`, 한 트랜잭션) → 계정 조회 → 상태가 `active`인지 → 비밀번호 검증 → 성공이면 `settle_login_success` → 새 owner 세션(브라우저가 보낸 기존 세션은 지운다).
- **[코드] 응답**: 성공 303 `Location: /`와 새 세션 쿠키. 실패 401, 같은 문구 하나. 계정 없음, `setup_required`, 정지, 비밀번호 행 없음을 구분하지 않고, 이때도 `verify_password(None, ...)`이 같은 비용의 더미 scrypt를 계산한다. 실패 제한 중이면 429와 `Retry-After`(초). 양식 값이 문자열이 아니면 400.
- **[코드] `POST /logout`**: public, 동일 origin 필수. 쿠키의 세션 행 하나를 지우고 쿠키를 지운 뒤 303 `Location: /login`.
- **[코드] 세션 쿠키**: 이름 `claire_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age`는 `SESSION_TTL`(7일), `Secure`는 `CLAIRE_ENVIRONMENT=production`일 때만.

### 5.3 단계 인증과 `/api/auth/reauth`

- **[코드] 창**: `STEP_UP_WINDOW_SECONDS = 900`(15분). `has_recent_auth`는 채널이 cookie·x-session이고 `0 ≤ now − authenticated_at ≤ 900`일 때만 True다. `authenticated_at`이 미래면 False.
- **[코드] `authenticated_at`을 기록하는 시점**: 로그인·`/setup` 확정(`create_account_session`), 텔레그램 승인(`approve_auth_nonce`), CLI·텔레그램 링크 발급(`create_session`), 부트스트랩 링크 교환(`exchange_session_token`), 재인증 성공(`mark_session_authenticated`).
- **[코드] 적용 대상(P1)**: `POST /api/me/password`만 `require_recent_auth`를 부른다. `/admin`의 변경 동작은 아직 없다.

응답 규약(`common.require_recent_auth`, `login.reauth_post`, `me.me_password`):

| 상황 | 상태 | 본문 `error` | 비고 |
| :--- | :--- | :--- | :--- |
| 세션 채널이 아님(bearer) | 403 | `step_up_requires_session` | bearer 토큰은 사람이 다시 인증할 수 없다 |
| 세션이지만 15분 안에 인증하지 않음 | 401 | `reauth_required` | 본문 `reauth: "/api/auth/reauth"`, `WWW-Authenticate: Session realm="claire", error="reauth_required"` |
| 재인증·비밀번호 변경에서 비밀번호 틀림 | 403 | `invalid_password` | 실패 제한에 센다 |
| 재인증 대상 계정에 비밀번호가 없음 | 409 | `password_not_set` | `/api/auth/reauth` |
| 비밀번호 변경 대상 계정이 `active`가 아님 | 409 | `account_not_active` | `/api/me/password` |
| 실패 제한 중 | 429 | `too_many_attempts` | `Retry-After`(초) |
| 본문 형식 오류 | 400 | `invalid_request` | `/api/auth/reauth`는 키가 정확히 `password` 하나여야 한다 |

- **[코드] `POST /api/auth/reauth`**: 규칙 `owner`. 본문 `{"password": "..."}`. 성공 200 `{"ok": true, "authenticated_at": <epoch 초>}`. 세션 행이 사라졌으면 403 `step_up_requires_session`.

### 5.4 `/admin/accounts`, `/api/admin/accounts`, `/api/admin/usernames/*`

- **[코드]** 규칙 `owner`, 핸들러가 `instance_role=admin`·`scope=owner`가 아니면 404.
- **[코드] `GET /api/admin/accounts`**: `{"accounts": [{public_id(문자열), username, status, role, created_at, activated_at, theme_count, delete_after?}]}`, `id` 오름차순.
- **[코드] `GET /admin/accounts`**: 관리 화면. 관리자 계정 카드, 멤버 계정 목록, 초대 관리, 퇴역 USERNAME 관리 패널을 제공한다. 다른 계정의 콘텐츠 본문을 읽거나 가장 로그인하는 API는 두지 않는다(K5, D5).
- **[코드] `PATCH /api/admin/accounts`**: 관리자 단계 인증(`require_recent_auth`, 최근 15분 내 인증) 필수.
  - 계정 정지(`suspend`), 재개(`resume`), 삭제 예약(`delete`), 삭제 예약 취소(`cancel_delete`), 즉시 소각(`purge`), 비밀번호 재설정 링크 발급(`reset_password_link`)을 수행한다.
  - 정지·삭제 시 해당 계정의 활성 세션과 토큰이 즉시 차단/무효화된다.
- **[코드] `GET /api/admin/usernames/retired`**: 퇴역 상태(`released_at IS NULL`)인 USERNAME 목록(`username`, `account_id`, `was_public`, `retired_at`)을 반환한다.
- **[코드] `POST /api/admin/usernames/release`**: 관리자 단계 인증 필수. 퇴역 USERNAME 정보 초기화(미리보기 및 실행, §5.7).

### 5.5 `/me`, `/api/me`, `/api/me/password`

- **[코드]** 모두 규칙 `owner`. collaborator·readonly 자격증명은 404다.
- **[코드] `GET /api/me`**: `{public_id, username, display_name, role, scope, status, has_password}`. `GET /me`는 같은 값을 화면으로 보이고 `notice=password_not_saved`를 안내한다.
- **[코드] `PATCH /api/me`**: 본문 키가 정확히 `display_name` 하나여야 한다(아니면 400 `invalid_request`). 값이 규칙에 맞지 않으면 400 `invalid_display_name`. 성공하면 `GET /api/me`와 같은 본문.
- **[코드] `POST /api/me/password`**: 단계 인증 → 본문 `{current_password?, new_password}` → 비밀번호가 이미 있으면 실패 제한 확인과 현재 비밀번호 검증, 없으면 생략 → 정책 검사(실패 400 `password_rejected`, `reason`, `message`) → 저장 → 이 세션을 뺀 그 계정의 모든 승인 세션 삭제 → 감사 기록. 성공 200 `{"ok": true, "other_sessions_revoked": n}`. API·MCP 토큰과 OAuth 토큰은 지우지 않는다.

### 5.6 초대 파이프라인 (Invitation Pipeline)

**[코드]** `src/claire/accounts/invites.py`, `src/claire/api/accounts_routes/invites.py`.

- **네트워크 모드 전용**: 싱글 모드에서는 `/invite/{code}`, `/api/invites` 모든 접근 시 404를 반환한다.
- **코드 형식 및 엔트로피**:
  - 8자리 Crockford Base32(`0-9`, `A-Z` 중 혼동 문자 `I`, `L`, `O`, `U`를 제외한 32문자). 40비트 엔트로피.
  - 대소문자 무시, 공백 및 하이픈 제거, `O→0`, `I/L→1` 자동 정규화(`normalize_invite_code`).
  - 유효성 패턴: `^[0-9A-HJKMNP-TV-Z]{8}$` (`validate_invite_code_syntax`).
- **생성 매개변수 제약 (`validate_invite_params`)**:
  - `max_uses`: 1~5회 필수 (`MIN_MAX_USES=1`, `MAX_MAX_USES=5`).
  - `expires_in`: 30분 ~ 30일 필수 (`MIN_EXPIRATION_SECONDS=1800`, `MAX_EXPIRATION_SECONDS=2592000`).
  - `reserved_username`: `max_uses=1`일 때만 특정 USERNAME 사전 예약 허용.
- **원자적 차감 트랜잭션**:
  1. `auth.db` 조건부 UPDATE 트랜잭션:
     ```sql
     UPDATE invitations SET uses_count = uses_count + 1
     WHERE id = :id AND revoked_at IS NULL AND expires_at > :now AND uses_count < max_uses;
     ```
     영향받은 행이 1개인지 원자적으로 확인 후 `invitation_redemptions(status='pending')` 기록. 동시 다중 요청 시에도 조건부 UPDATE에 의해 정확히 잔여 횟수만큼만 성공한다.
  2. `claire.db`에 신규 계정(`role='member'`, `status='active'`) 및 기본 테마(`is_default=1`) 생성.
  3. `invitation_redemptions`를 `completed`로 전환하고 `account_id` 기록. 2단계 실패 시 `uses_count`를 롤백하고 `failed` 마킹.
- **GET/HEAD 무부작용 (Safe Links & Robots Policy)**:
  - 메신저 링크 미리보기 봇이나 정찰 요청의 `GET /invite/{code}` 또는 `HEAD /invite/{code}`는 일절 `uses_count`를 차감하거나 실패 카운트를 올리지 않는다 (No side-effects).
  - 응답 헤더에 `X-Robots-Tag: noindex, nofollow`를 강제 부착하고, 화면 내 외부 링크에 `rel="noreferrer noopener"`를 강제하여 초대 URL이 `Referer`로 유출되지 않게 방어한다.
- **저장 보안**:
  - 색인용 HMAC: 마스터 키에서 파생된 HMAC(`code_hmac`)으로 색인하여 DB만 유출되더라도 40비트 코드를 오프라인 전수 조사할 수 없다.
  - 암호문 보존: 관리자가 발급 링크를 다시 확인할 수 있도록 비밀 서비스(`SecretService`)의 AES-256-GCM 암호문(`code_ciphertext`, `code_nonce`)을 함께 보관한다.
- **오용 방지 및 속도 제한**:
  - 미존재 코드 조회가 동일 IP(`security.client_ip`) 기준 분당 10회를 초과하면 429 Too Many Requests 지연을 적용한다.
  - 미존재 코드는 404 Not Found, 만료·소진·비활성 코드는 410 Gone과 사유(`expired`, `exhausted`, `revoked`)를 반환한다.
- **경로 및 권한**:
  - `GET /invite/{code}`: public. 가입 화면.
  - `POST /invite/{code}`: public. 동일 origin 필수. 로컬 가입 제출 (USERNAME, 표시 이름, 비밀번호).
  - `GET /api/invites`: admin (그 외 404). 컬렉션 목록 조회.
  - `POST /api/invites`: admin. 초대 생성.
  - `PATCH /api/invites`: admin. 초대 즉시 비활성화(`revoked_at` 설정).

### 5.7 퇴역 USERNAME 정보 초기화 및 디스크 정리 (Username Release & Disk GC)

**[코드]** `src/claire/store/accounts.py`, `src/claire/api/accounts_routes/admin.py`.

- **퇴역 상태 (`username_tombstones`)**:
  - 계정이 영구 소각(`purged`)되면 USERNAME이 `username_tombstones`에 등록되고 `released_at`은 `NULL`로 유지된다.
  - 퇴역된 이름은 다른 사용자가 재가입에 사용할 수 없으며, 시도 시 `UsernameRejected("retired")`로 거부된다.
  - 소각된 계정이 이전에 공개 프로필(`was_public=1`)이었으면 `/@{name}` 접근 시 410 Gone(삭제된 계정 안내)을 응답하고, 비공개였으면 404 Not Found를 응답한다.
- **디스크 가비지 컬렉션 (Disk GC)**:
  - 계정이 `purged`될 때 DB 외래 키 cascade 삭제에 더해, 다른 계정이나 테마에서 더 이상 참조하지 않는 디스크 산출물(참조 카운트 확인 뒤 아티팩트 `data/raw/artifacts/`, 파일 `data/raw/files/`, 이미지 `data/images/`, 프레젠테이션 컴파일 HTML·캐시, 그래프 번들 아카이브)을 안전하게 삭제한다.
- **정보 초기화 API (`POST /api/admin/usernames/release`)**:
  - **단계 인증 필수**: 관리자 세션이면서 최근 15분 이내 인증(`require_recent_auth`)된 경우에만 실행 가능하다.
  - **미리보기 기본 (`apply=false`, 기본값)**:
    삭제 대상 잔여 항목(410 표식 여부, 초대 테이블의 예약 잔여, 잔여 자격증명 등)을 집계하여 미리보기 데이터를 반환하며, 실제 변경은 수행하지 않는다.
  - **실행 (`apply=true`)**:
    `username_tombstones`에 `released_at`과 `released_by`를 기록하고 `was_public=0`으로 전환(410 표식 해제). 초대 테이블의 `reserved_username` 예약 잔여를 해제하고, 관련 세션·자격증명을 파기하며, `status='purged'`인 accounts 행을 정리한다.
- **재가입 시 격리**:
  - 정보 초기화 후 해당 USERNAME으로 신규 가입하는 사용자는 완전히 새로운 `account_id`와 `public_id`를 발급받으며, 이전 계정의 데이터나 권한이 새 계정으로 승계되지 않는다.
- **CLI 명령**:
  `./cb-manuscript app account release-username <name>`은 dry-run이 기본이고 `--apply`로 실행한다.

## 6. 비밀번호

- **[코드] 해시**: `hashlib.scrypt`, n=2¹⁵, r=8, p=1, dklen=64, 솔트 16바이트, `maxmem` 64MiB. 프로세스 안의 동시 계산은 `SCRYPT_MAX_CONCURRENCY = 4`개로 제한한다(세마포어, 계산 하나 32MiB → 최고 128MiB). 없는 계정의 더미 계산도 같은 세마포어를 거친다. 매개변수는 행의 `params`(JSON)에 저장하고 검증은 저장된 값으로 한다. 비교는 `hmac.compare_digest`. `algo`가 `scrypt`가 아니면 `RuntimeError`.
- **[코드] [결정] 정책**(`check_password_policy`, 새로 설정할 때만): ① ASCII 0x21–0x7E 밖의 문자(공백·비ASCII·제어문자)가 있으면 `invalid_characters` ② 12자 미만이면 `too_short` ③ USERNAME을 대소문자 무시로 포함하면 `contains_username`. 허용 문자는 소유자 결정이다(설계 §16.3 (b)). 저장된 해시 검증에는 적용하지 않는다.

## 7. 실패 제한

**[코드]** `src/claire/store/auth_db.py`. 로그인, 재인증, 비밀번호 변경(현재 비밀번호 확인)이 같은 함수를 쓴다.

| 항목 | 값·동작 |
| :--- | :--- |
| 키 | `(username, IP)`와 `(IP)` 두 개. 둘 다 SHA-256으로 줄여 저장하고 USERNAME 원문을 남기지 않는다. 계정이 없는 이름도 똑같이 센다. 재인증·비밀번호 변경은 USERNAME이 없으면 `#account:<id>`를 이름 자리에 쓴다 |
| IP | `common.client_ip` → `security.client_ip`. 기본은 피어 주소(`request.client.host`). 바로 앞 피어가 `CLAIRE_TRUSTED_PROXIES`(와 `CLAIRE_CLOUDFLARE_IPS_ONLY=1`이면 Cloudflare 대역)에 있을 때만 `X-Forwarded-For`를 오른쪽부터 읽는다([EXTERNAL_ACCESS.md](EXTERNAL_ACCESS.md)) |
| 무료 실패 | 창(`THROTTLE_WINDOW_SECONDS` = 900초) 안에서 5회(`THROTTLE_FREE_FAILURES`)까지 지연 없음 |
| 지연 | 6번째 실패부터 1초, 이후 실패마다 두 배, 상한 900초(`THROTTLE_MAX_DELAY_SECONDS`). 고정 잠금은 없다 |
| 판정 | 두 키 중 하나라도 `retry_after`가 미래면 거부한다. 거부한 시도는 실패로 세지 않는다 |
| 원자성 | `begin_login_attempt`가 비밀번호 검증 **전에** `BEGIN IMMEDIATE` 한 트랜잭션에서 판정하고, 허용이면 이 시도를 실패로 미리 센다. 확인과 기록 사이 틈이 없으므로 동시 요청 중 검증에 닿는 수는 무료 5회 + 첫 지연을 만드는 1회 = 6을 넘지 않는다 |
| 창 리셋 | `now − window_start ≥ 900`이고, **지연이 붙은 키는 `retry_after` 뒤로도 창 길이(900초)만큼 조용해야** 새로 센다(`_window_expired`). `window_start`만 보면 누적 대기가 창보다 길어 상한 전에 매번 초기화되기 때문이다 |
| 성공 | `settle_login_success`: `(username, IP)` 키를 지우고, `(IP)` 키에서는 미리 센 이 시도 하나만 되돌린다(성공은 실패가 아니다). 그 IP의 다른 실패는 남아 여러 이름 대입을 계속 센다. 되돌린 뒤 무료 범위면 `retry_after`도 지운다 |
| 정리 | 서비스 기동 시 `prepare_auth_db`가 `prune_login_throttle`을 부른다. 지우는 조건은 창 리셋 조건과 같다 |

근거 테스트: `tests/test_auth_db.py`(`test_blocked_key_needs_a_quiet_window_after_its_wait` 등), `tests/test_login_flow.py`.

## 8. auth.db

- **[코드] 위치**: `<data_dir>/auth/auth.db`(`auth_db_path`). `data_dir`는 `db_file`의 부모 디렉터리이므로 기본은 `data/auth/auth.db`이고, `api_tokens.json`과 같은 디렉터리다.
- **[코드] 분리 이유**(모듈 설명): graph bundle·support bundle·내보내기에 비밀이 섞이지 않게 하고, 지식 DB 장애가 로그인으로 번지지 않게 한다. support bundle이 이 파일을 실제로 제외하는지 확인하는 테스트는 없다 [미검증].
- **[코드] 파일 권한**: 디렉터리를 0700으로 만들고(이미 있으면 POSIX에서 chmod), 파일은 SQLite가 열기 전에 0600으로 먼저 만든다(이미 있으면 chmod 0600). 디렉터리 chmod는 같은 디렉터리의 `api_tokens.json` 접근에도 적용된다. WAL·SHM 파일 권한은 [미검증].
- **[코드] 스키마**: 설계 §13.3의 테이블 전부와 `meta`를 `CREATE TABLE IF NOT EXISTS`로 만든다. P1이 다루는 테이블은 `account_passwords`, `login_throttle`, `admin_audit`다. 나머지는 만들기만 한다.
- **[코드] 버전**: `meta.schema_version`은 `AUTH_DB_SCHEMA_VERSION = 1`이며 claire.db의 버전과 별개다. 저장된 값이 더 크면 `RuntimeError`(다운그레이드 거부), 1보다 작거나 정수가 아니면 `RuntimeError`. [SCHEMA_VERSIONING.md](SCHEMA_VERSIONING.md) 참고.
- **[코드] 준비 시점**: `claire migrate`가 claire.db보다 먼저 만들고 `auth_db schema_version=1`을 출력한다. 서비스 기동(`create_app`)도 `prepare_auth_db`로 만들고 정리한다. 라우트는 `mode=rw`로 열어 파일이 없으면 만들지 않고 실패한다(`common.open_auth_db`).

## 9. 감사 기록

- **[코드] 원칙**: `admin_audit`는 계정을 `actor_account_id`·`target_account_id`로만 기록한다. `record_admin_audit`는 `detail`의 어느 깊이에든 키 `username`(대소문자 무시)이 있으면 `ValueError`를 낸다(설계 K4). 토큰 값은 넣지 않고 `credential_id`만 넣는다.
- **[코드] 기록하는 동작**

| `action` | 언제 | `detail` |
| :--- | :--- | :--- |
| `account.setup` | `/setup` 확정 | `channel`, `credential_id`, `password_saved` |
| `account.password_change` | `/api/me/password` 성공 | `other_sessions_revoked` |

- **[코드]** 로그인 성공·실패, 로그아웃, 재인증은 감사 기록에 남기지 않는다. 로그인 실패는 `login_throttle`에만 남는다.
- **[코드]** 조회는 `list_admin_audit`(최근 것부터)이며, 이를 노출하는 HTTP 경로는 없다.

## 10. 테스트

`tests/test_schema_v15.py`, `tests/test_account_store.py`, `tests/test_auth_db.py`, `tests/test_ids.py`, `tests/test_username_policy.py`, `tests/test_principal.py`, `tests/test_setup_flow.py`, `tests/test_login_flow.py`, `tests/test_account_routes.py`, `tests/test_api_security.py`, `tests/test_account_forms_browser.py`(설치된 Playwright Chromium으로 실제 양식 제출, 없으면 건너뜀), `tests/test_invite_flow.py`(초대 파이프라인 및 원자적 차감), `tests/test_username_release.py`(수명주기 전이, 디스크 정리 및 퇴역 USERNAME 정보 초기화).
