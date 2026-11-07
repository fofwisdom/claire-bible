# 계정 구현 정본 (Accounts Implementation Reference)

확인 기준: 통합 콘솔(`/admin/*`·`/settings/*`) 경로 분리 P4 커밋 `cdc0f2a` (2026-11-05) · 대상 독자: 계정·로그인·자격증명·초대 코드·API 키·관리/설정 화면을 고치거나 확장하는 에이전트 · 설계 배경: [MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md](../design/MULTI_ACCOUNT_NAMESPACE_AND_SINGLE_FQDN_DESIGN.md) §8, §11, §13 (Rev. 2.3), [ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md](../design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md) (D1–D5)

이 문서는 **지금 코드가 무엇을 어디서 어떻게 하는지**를 적는다. 범위는 설계의 P1(싱글 모드 계정 코어), P3(초대 파이프라인, 계정 수명주기, 퇴역 USERNAME 정보 초기화 및 디스크 정리) 및 P4(본인 API 키 관리 `/api/me/keys`)와, 관리자·본인 화면을 `/admin/*`·`/settings/*`로 나눈 통합 콘솔(§5.9)이다. 근거 표기는 [PRINCIPLES.md](../PRINCIPLES.md) F1을 따른다.

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
| `src/claire/store/provider_keys.py` | 멤버 BYOK API 키 암호화 저장소, 시험 호출 검증 | `ProviderKeyMeta`, `ActiveKey`, `put_key`, `load_active_key`, `verify_key`, `delete_key` |
| `src/claire/accounts/username_policy.py` | DB 없이 판정하는 USERNAME 규칙 | `validate_username`, `normalize_username`, `RESERVED_USERNAMES`, `instance_reserved_labels`, `UsernameRejected` |
| `src/claire/accounts/invites.py` | Crockford Base32 8자리 초대 코드 생성, 정규화, 매개변수 유효성 검증 | `InvitationInfo`, `normalize_invite_code`, `validate_invite_code_syntax`, `generate_invite_code`, `validate_invite_params`, `InviteError` |
| `src/claire/ids.py` | 시간 정렬 64비트 번호 | `timestamp_id`, `legacy_timestamp_id`, `insert_with_timestamp_id`, `format_public_id`, `parse_public_id` |
| `src/claire/api/principal.py` | 요청 주체 | `Principal`, `ANONYMOUS`, `request_principal`, `has_recent_auth`, `STEP_UP_WINDOW_SECONDS` |
| `src/claire/api/security.py` | 자격증명 검증과 `Principal` 배정, 라우트 접근 규칙, 실패 제한용 클라이언트 IP | `ROUTE_POLICY`, `AuthenticationMiddleware`, `_Credential`, `_bind_account`, `_env_token_credential`, `client_ip`, `parse_trusted_proxies` |
| `src/claire/api/oidc.py` | OIDC 코어 클라이언트, GitHub 어댑터, PKCE S256 및 JWK 서명 검증 | `OidcClient`, `GitHubAdapter`, `generate_pkce_pair`, `verify_pkce`, `ExternalIdentity` |
| `src/claire/api/accounts_routes/` | 계정 라우트(`setup.py`, `login.py`, `admin.py`, `me.py`, `invites.py`, `keys.py`, `instance.py`, `idp.py`), 통합 콘솔(`console.py`, `console_syndication_panels.py`)과 공용 헬퍼(`common.py`) | `build_account_routes`, `prepare_auth_db`, `require_recent_auth`, `same_origin`, `console.routes`, `SETTINGS_ACCOUNT_NOTICE_TEXT`, `build_syndication_section` |
| `src/claire/templates/` | `setup.html`, `login.html`, `console_shell.html`(`/admin/*`·`/settings/*` 공용 셸), `partials/settings_syndication/*.html`(Group 2–3 섹션 본문) | `$이름` 자리채움(`common.render_template`) |
| `src/claire/static/js/console_account.js` | `/settings/account` 화면의 표시 이름·본인 확인·비밀번호 양식 (`GET /me`는 `/settings/account`로 가는 302) | `PATCH /api/me`, `POST /api/auth/reauth`, `POST /api/me/password` 호출 |
| `src/claire/static/js/console_admin.js` | `/admin/*` 화면 컨트롤러와 단계 인증 모달. `/admin/*` 응답에만 싣는다 | `/api/admin/*`, `/api/auth/reauth` 호출 |
| `src/claire/static/js/console_syndication.js` | `/settings/{reader,connections,feeds,tags,rules,outbound,opml,mcp,webmcp}` 화면 | localStorage 키 `claire_synd_prefs_v2:<storage-key>:t<테마 참조>` |
| `src/claire/static/js/manage_panels.js` | 콘솔(`/admin/*`, `/settings/*`)이 쓰는 관리 패널. 루트 `/`에는 싣지 않는다 | `window.ClaireManagePanels`(`renderProviderManager`, `renderLimitManager`, `renderThemeManager`, `renderTokenManager`) |

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
3. **예약 이름**: `RESERVED_USERNAMES`(설계 §8.1-2 목록에 콘솔·기능 경로용 `settings`, `syndication`, `prototype`을 더한 것, `tests/test_username_policy.py`가 정확히 비교)와 일치하면 `reserved`. `settings`, `syndication`, `prototype`은 루트 고유 기능 경로(`/settings/*`, `/syndication`, `/prototype/syndication`)와 겹치지 않게 하려고 예약한다(콘솔 설계 D1).
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

- **[코드] 양식 화면의 `Referrer-Policy`**: `common.render_template`로 만든 화면(`/setup`, `/login`, `console_shell.html`로 그리는 `/admin/*`·`/settings/*`)은 `Referrer-Policy: same-origin`과 `Cache-Control: no-store`이다(`FORM_PAGE_REFERRER_POLICY`). 다른 응답은 전역 `no-referrer` 그대로다. 전역 값이면 Chromium이 양식 제출(navigate 모드 POST)에 `Origin: null`을 싣고 `CORSPolicyMiddleware`가 403으로 막는다. `Origin: null` 거부는 그대로다. `fetch()`(cors 모드)는 정책과 관계없이 실제 origin을 싣는다.
- **[코드] 세션 쿠키는 응답마다 하나**: 미들웨어는 쿠키 채널 응답에 세션 쿠키를 다시 붙여 만료를 늘리지만, 핸들러가 이미 `claire_session`을 설정하거나 지웠으면(`/setup` 확정) 붙이지 않는다(`_sets_session_cookie`).

### 5.1 `/setup` (최초 관리자 설정)

- **[코드] 열리는 조건**(`setup.setup_pending`): 주체가 계정 1, `instance_role=admin`, `scope=owner`, 채널 cookie 또는 x-session이고, 계정 상태가 `setup_required`. 그 밖(미인증, bearer, 이미 `active`)은 GET·POST 모두 404다.
- **[코드] `GET /`**: 위 조건이면 `/setup`으로 302(`Cache-Control: no-store`). Bearer는 보내지 않는다.
- **[코드] 입력**: `username`, `display_name`, `password`, `password_confirm`, `ack`(form). 문자열이 아닌 값이면 400.
- **[코드] [결정] 확인 문구**: "USERNAME은 바꿀 수 없다", "네트워크 모드에서는 `/@USERNAME`으로 공개된다"를 같은 화면에 적고, `ack=1` 필수 체크박스로 받는다. 별도 확정 화면을 두지 않는 것은 비밀번호를 브라우저로 되돌려 보내지 않기 위해서다(설계 §16.3 (d)).
- **[코드] 처리 순서**(`perform_setup`): 모든 입력 검사(USERNAME 규칙·퇴역·중복, 표시 이름, 비밀번호 정책·확인, 체크박스) → claire.db `complete_setup`(조건부 UPDATE 한 번, 동시 요청 중 하나만 성공) → auth.db `set_password` → 확정 전 세션을 지우고 새 owner 세션 발급(세션 고정 방지) → 감사 기록 `account.setup`.
- **[코드] 응답**: 검사 실패는 400과 사유 목록이 있는 화면. 동시 확정에서 졌으면 404. 성공은 303 `Location: /`와 새 세션 쿠키.
- **[코드] 비밀번호 저장만 실패한 경우**: 확정을 되돌리지 않는다. 303 `Location: /settings/account?notice=password_not_saved`. 계정은 `active`이고 비밀번호가 없어 로그인만 막힌다. `/settings/account`에서 단계 인증만으로 비밀번호를 설정한다.
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
- **[코드] 적용 대상**: `require_recent_auth`를 부르는 곳은 `me.py`(`POST /api/me/password`), `keys.py`(BYOK 키 변경), `admin.py`(계정 수명 주기·퇴역 USERNAME 정리), `instance.py`(모드 전환 등 인스턴스 변경), `idp.py`(공급자 설정 변경)다. 정확한 동작 목록은 각 파일의 호출 위치가 기준이다.

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

- **[코드]** 규칙 `_system("admin")`. 미들웨어가 `scope=owner`이면서 `instance_role=admin`인 자격증명만 통과시키고 나머지는 404다. 핸들러도 `admin.is_admin`으로 한 번 더 확인한다. 접두 경로(`/@{username}/admin/...`)는 404(N5).
- **[코드] `GET /api/admin/accounts`**: `{"accounts": [{public_id(문자열), username, status, role, created_at, activated_at, theme_count, delete_after?}]}`, `id` 오름차순.
- **[코드] `GET /admin/accounts`**: 통합 콘솔 셸(§5.9) 안의 관리 화면. 관리자 계정 카드, 멤버 계정 목록, 초대 관리, 퇴역 USERNAME 관리 패널을 제공한다. 다른 계정의 콘텐츠 본문을 읽거나 가장 로그인하는 API는 두지 않는다(K5, D5).
- **[코드] `PATCH /api/admin/accounts`**: 관리자 단계 인증(`require_recent_auth`, 최근 15분 내 인증) 필수.
  - 계정 정지(`suspend`), 재개(`resume`), 삭제 예약(`delete`), 삭제 예약 취소(`cancel_delete`), 즉시 소각(`purge`), 비밀번호 재설정 링크 발급(`reset_password_link`)을 수행한다.
  - 정지·삭제 시 해당 계정의 활성 세션과 토큰이 즉시 차단/무효화된다.
- **[코드] `GET /api/admin/usernames/retired`**: 퇴역 상태(`released_at IS NULL`)인 USERNAME 목록(`username`, `account_id`, `was_public`, `retired_at`)을 반환한다.
- **[코드] `POST /api/admin/usernames/release`**: 관리자 단계 인증 필수. 퇴역 USERNAME 정보 초기화(미리보기 및 실행, §5.7).

### 5.5 `/me`, `/api/me`, `/api/me/password`

- **[코드]** 모두 규칙 `_any("owner")`. collaborator·readonly 자격증명은 404다.
- **[코드] `GET`·`HEAD /me`**: 화면을 그리지 않는 호환용 302다(콘솔 설계 D4). `Location: /settings/account`(항상 루트 경로, USERNAME 없음, S9), `Cache-Control: no-store`, `Referrer-Policy: same-origin`. 규칙이 `_any`라 옛 `/@{username}/me` 북마크도 같은 302를 받는다. 쿼리(`?notice=` 포함)는 넘기지 않는다. 계정 화면은 `/settings/account`(§5.9)다.
- **[코드] `GET /api/me`**: `{public_id, username, display_name, role, scope, status, has_password}`. `/settings/account`가 이 값을 화면으로 보인다.
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

### 5.8 본인 API 키 관리 (`/api/me/keys`, `/api/me/keys/verify`)

**[코드]** `src/claire/store/provider_keys.py`, `src/claire/api/accounts_routes/keys.py`. 설계 §11.2, §11.3 (P4 L4-b).

- **네트워크 모드 전용**: 싱글 모드에서는 404를 반환한다(`ROUTE_POLICY`의 `modes={"network"}` 및 핸들러의 `is_network_mode`).
- **권한 및 주체**:
  - 규칙 `owner`. `account_id` 필수.
  - 관리자(`instance_role == "admin"`)가 호출하면 403 `admin_uses_provider_settings`를 반환한다 (관리자는 `data/providers.json`을 직접 사용하므로 엔드포인트 접근이 차단된다).
  - 멤버(`is_member`: `role == "member"`, `scope == "owner"`)만 사용 가능하며, 그 외(익명, collaborator, readonly 등)는 404 Not Found다.
- **쓰기 전용 API 키 및 키 미노출 원칙 (Write-only Secrets, §11.2, §11.3, D8)**:
  - 등록 시 전달받은 `api_key`는 요청 본문에서 읽자마자 `register_secret`으로 등록되어 메모리 내 등록 이후의 모든 로그와 예외 출력(`SecretScrubFilter`, `scrub_text`, `scrub_exception`)에서 마스킹된다.
  - 마스터 키(`CLAIRE_SECRET_KEY` 또는 `data/auth/master.key`)에서 용도별로 파생된 암호화 키를 사용해 AES-256-GCM(`provider_keys.put_key`, AAD: `account_id|provider|key_id`)으로 암호화하여 `auth.db`의 `provider_keys` 테이블에 보관한다.
  - 평문 API 키는 저장된 이후 어떤 API·CLI·로그·번들(`support_bundle`, `graph_bundle`)에서도 절대 조회·반환·복사할 수 없다 (쓰기 전용).
  - 메타데이터 및 화면에는 파생 HMAC의 앞 8자리 `fingerprint`만 표시되며, 키의 시작 또는 끝 부분 등 일부 문자도 전혀 보여주지 않는다(D8).
- **엔드포인트 명세**:
  - `GET /api/me/keys`: 등록된 키 메타데이터 목록 반환 (`{"keys": [{provider, fingerprint, status, created_at, last_verified_at, last_used_at}]}`). `Cache-Control: no-store`.
  - `PUT /api/me/keys`: 키 등록 및 교체.
    - 단계 인증(`require_recent_auth`, 최근 15분 이내 인증) 필수.
    - 본문: `{"provider": "gemini", "api_key": "..."}` (`provider`는 `MEMBER_KEY_PROVIDERS`인 `"gemini"`만 지원).
    - 저장 전 `provider_keys.verify_key`로 실제 외부 시험 호출을 수행하여 유효성을 검증한다.
    - 시험 호출에서 키가 거부(401/403 등)되면 저장하지 않고 422 `key_verification_failed`와 에러 메시지(스크러빙 완료)를 반환한다.
    - 일시적 네트워크 장애 등으로 판정 불가 시 저장하지 않고 503 `key_verification_unavailable`을 반환한다.
    - 검증 성공 시 AES-256-GCM 암호문으로 저장하고 상태를 `active`로 설정하며, 감사 기록 `account.provider_key_put`을 남긴다. 응답은 `{"key": <meta>}`.
  - `POST /api/me/keys/verify`: 저장된 키 재검증.
    - 본문: `{"provider": "gemini"}`.
    - 저장된 암호문을 복호화하여 시험 호출을 다시 수행하고 상태를 갱신한다.
    - 시험 호출 성공 시 `status="active"`, 실패(거부) 시 `status="rejected"`, `last_verified_at` 갱신.
    - 키 복호화 실패 시 409 `key_unreadable`.
    - 키가 존재하지 않으면 404 `key_not_found`.
    - 일시 장애로 판정 불가 시 상태와 `last_verified_at`을 변경하지 않고 503 `key_verification_unavailable` (상태 불변).
    - 응답: `{"ok": bool, "message": str, "key": <meta>}`.
  - `DELETE /api/me/keys`: 등록된 키 삭제.
    - 단계 인증(`require_recent_auth`) 필수.
    - 쿼리 매개변수: `?provider=gemini` (미지정 시 해당 계정의 모든 키 삭제).
    - 삭제 성공 시 감사 기록 `account.provider_key_delete`를 남기며, 삭제 후에는 서버 등급 범위에서만 LLM 작업을 수행한다. 응답: `{"ok": true, "deleted": n}`.
- **폴백 없음 (Fail-closed, Principle C2)**:
  - 멤버가 등록한 키가 거부(`rejected`)되거나 할당량이 소진되면 해당 계정의 LLM 작업은 실패(`ProviderKeyRejected`)하며, 서버 등급이나 관리자 CLI로 자동 전환/폴백하지 않는다.
- **WebUI `/settings/keys` 연동**:
  - 네트워크 모드 멤버 계정으로 `/settings/keys`에 접속하면 "내 API 키" 메타데이터 카드 및 등록·재확인·삭제 양식이 렌더링된다.
  - 관리자는 BYOK 양식 대신 `/admin/providers` 링크가 있는 안내 카드를 받는다. 싱글 모드의 비관리자는 `/admin` 링크 없는 안내만 받는다.

### 5.9 통합 콘솔 (`/admin/*`, `/settings/*`)

**[코드]** `src/claire/api/accounts_routes/console.py`(라우트·섹션 정의·셸 렌더링), `admin.py`·`instance.py`·`idp.py`(`/admin/accounts`·`/admin/instance`·`/admin/idp`), `console_syndication_panels.py`(Group 2–3 본문). 설계: [ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md](../design/ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md).

- **경로 분리는 권한 경계다.** 관리자 화면은 `/admin/*`, 본인 설정은 `/settings/*`이고, 둘 다 같은 셸 `console_shell.html`로 그린다. 모든 경로는 `ROUTE_POLICY`에 `GET`·`HEAD` 정확 항목으로 있고 모든 모드(`ALL_MODES`)에서 열린다.
- **`/admin`, `/admin/*`**: 규칙 `_system("admin")`. 관리자(`scope=owner`, `instance_role=admin`)가 아니면 미들웨어와 핸들러 모두 404다. 403을 내지 않고, 비관리자에게는 302도 내지 않는다(B1, N5).

| 경로 | 동작 |
| :--- | :--- |
| `/admin` | 302 `/admin/instance`. `?theme=`가 있으면 관리자 소유 테마인지 먼저 검사하고 아니면 404 |
| `/admin/instance` | 모드 전환, G1–G9 준비 검사, 레거시 호스트 일몰, 기본 서버 등급 (`instance.py`) |
| `/admin/accounts` | §5.4 (`admin.py`) |
| `/admin/idp` | 외부 IdP 공급자, 가입 정책, 로그인 시뮬레이터 (`idp.py`, §10) |
| `/admin/providers` | 인스턴스 LLM·STT·VLM 프로바이더 설정. 본문은 `/providers`, `/providers/test`를 부르는 `manage_panels.js` |
| `/admin/limits` | 인스턴스 적재 제한·슬라이싱 예산(`/limits`) |
| `/admin/themes` | 관리자 계정(`ADMIN_ACCOUNT_ID = 1`)의 테마 관리와, 인스턴스 집계 숫자(`total_accounts` = `status != 'purged'` 계정 수, `total_themes`, `admin_themes`)만 보인다. 멤버 테마의 레이블·설명·본문은 읽지 않는다(D5, K5) |
| `/admin/tokens` | 관리자 계정의 상시 API·MCP 토큰(`/tokens`)과 OAuth 클라이언트 목록 |
| `/admin/mcp` | `mcp_telemetry` 감사 표(`GET /api/admin/mcp/telemetry`), RBAC·MCP 권한 비교표, WebMCP 헤더 정책, `POST /support/bundle` |

- **`GET`·`HEAD /api/admin/mcp/telemetry`**: 규칙 `_system("admin")`, 비관리자 404. 쿼리 `limit`(기본 50, 1–200 정수. 밖이거나 정수가 아니면 400 `invalid_limit`), `status`, `tool_name`, `since`(ISO 8601 시각. 비었거나 해석하지 못하면 400 `invalid_since`, C1). `store.telemetry.query_mcp_telemetry`로 `data/telemetry.db`를 최근 것부터 읽고 `{"records": [{id, timestamp, client_id, call_type, tool_name, duration_ms, status, error_code, error_message, response_bytes}], "count": n}`을 돌려준다. DB 파일이 없으면 빈 목록이다.
- **프로토타입 표식**: `/admin/*` 안에서 실제 서버 설정에 닿지 않는 요소는 서버가 표식 `<span class="proto-badge" data-prototype="…" data-prototype-kind="…">`(`common.prototype_badge`)과 무엇을 하지 않는지 적은 안내문 `.proto-note`(`common.prototype_note`)를 그린다. 종류는 `prototype`(프로토타입), `simulation`(시뮬레이션), `client_only`(브라우저에만 저장), `static`(정적 안내)이다. `/settings/*` 응답에는 표식이 없다.
  - `/admin/accounts`: `admin-accounts-byok-switch`(prototype, '멤버 BYOK 외부 키 허용' 스위치는 켜짐 고정이고 서버 등급에 해당 설정이 없다), `admin-accounts-isolation-table`(static, 격리 구조 설명표). 같은 카드의 통계 숫자와 한도 값은 실제 값이다.
  - `/admin/idp`: `admin-idp-login-policy`(prototype, 로그인 방식·가입 처리·허용 도메인·`email_verified`·Back-Channel Logout 칸은 저장 API가 없는 고정 예시), `admin-idp-login-simulator`(simulation, 실행 결과는 선택값을 문장으로 보여 줄 뿐이다. 미리보기의 공급자 버튼은 실제 `/auth/oidc/{id}/start` 링크).
  - `/admin/tokens`: `admin-token-policy-summary`(static, 코드 기본값을 옮겨 적은 안내문).
  - `/admin/mcp`: `admin-mcp-rbac-table`(static, 판정 칸까지 고정 문구), `admin-webmcp-global-switch`(client_only, `localStorage["claire_admin_webmcp_enabled"]`에만 기억하고 `Permissions-Policy` 헤더나 서버 설정을 바꾸지 않는다).
- **`/settings`, `/settings/*`**: 규칙 `_system("owner")`(콘솔 설계 D1). owner 범위의 계정 자격증명만 통과하고 collaborator·readonly·익명은 404다. 정본 URL은 두 모드 모두 루트 경로이고, 접두 경로(`/@{username}/settings/*`, `/themes/{n}/settings/*`)는 404(N5)다.

| 경로 | 사이드바 그룹 | 동작 |
| :--- | :--- | :--- |
| `/settings` | — | 302 `/settings/account`. `?from=syndication`이면 302 `/settings/reader`. 검증한 `?theme=`를 붙인다 |
| `/settings/account` | 1 내 계정 · 워크스페이스 | 표시 이름, 비밀번호·단계 인증, 외부 신원 연결, 사용량, 테마 번들 내보내기. `/api/me`, `/api/me/password`, `/api/auth/reauth`, `/api/me/identities*`, `POST /graph/bundle`을 쓴다 |
| `/settings/keys` | 1 | §5.8 WebUI |
| `/settings/themes` | 1 | 본인 테마 관리(`/themes`)와 슬롯 수(`내 테마 수 / 서버 등급 max_themes`) |
| `/settings/tokens` | 1 | 본인 상시 토큰(`/tokens`)과 `oauth_tokens.account_id = 호출자`인 OAuth 클라이언트 |
| `/settings/{reader,connections,feeds,tags,rules,outbound,opml}` | 2 신디케이션 · 리더 (테마별) | 신디케이션 설정. 옛 `/syndication/preferences` 탭 |
| `/settings/{mcp,webmcp}` | 3 AI 에이전트 연동 | MCP 연결 안내와 WebMCP 설정 |

- **D3 관리자 리디렉트**: 관리자가 `/settings/themes`·`/settings/tokens`를 요청하면 `?theme=`를 검증한 뒤 302 `/admin/themes`·`/admin/tokens`(검증한 `?theme=` 유지)로 보낸다. 사이드바도 이 두 항목을 `/admin/*` 그룹에만 한 번 보인다. 멤버는 `/settings/themes`·`/settings/tokens`를 그대로 받는다.
- **`?theme=` 검증 (C1)**: 모든 `/admin/*`·`/settings/*` 화면과 두 색인 경로는 `?theme=`가 있으면 호출자 계정의 테마인지 `ThemeManager.resolve_theme_ref(..., account_id=호출자)`로 검사한다. 빈 값, 없는 테마, 다른 계정 테마는 404이고 기본 테마로 바꾸지 않는다. 쿼리가 없으면 호출자의 기본 테마를 쓴다. 테마 선택 상자 `#console-theme-select`는 Group 2–3 섹션에만 나온다. 모르는 섹션 경로는 404다.
- **`/settings/account?notice=`**: 허용 목록 `SETTINGS_ACCOUNT_NOTICE_TEXT`의 `password_not_saved`(`/setup`, §5.1), `identity_linked`, `already_linked`(`/auth/oidc/{id}/callback`의 계정 연결, §10)만 안내문(`data-account-notice`)을 그린다. 목록에 없는 값은 아무것도 그리지 않는다.
- **관리자 마크업 제거**: 비관리자 응답 HTML에는 `/admin/*` 사이드바 링크도 관리자 섹션 마크업도 없다(CSS로 숨기지 않는다). `console_admin.js`는 `/admin/*` 응답에만 싣는다.
- **S9**: 싱글 모드에서는 셸의 그래프·신디케이션 링크가 `/`, `/syndication`이고 `@username`을 내지 않는다. 네트워크 모드이고 USERNAME이 있으면 그래프 링크만 `/@{username}/`이고 고유 기능 경로인 신디케이션 링크는 항상 `/syndication`이다.
- **부트스트랩 JSON**: 셸의 `<script type="application/json">`에는 `area`, `section`, `path`, `mode`, `is_admin`, `theme`과 섹션별 값이 들어간다. `<`, `>`, `&`는 `<`, `>`, `&`으로 이스케이프한다.
- **루트 `/` 진입점**: 상세 메뉴에는 '관리 및 설정' 패널이 없다(프로바이더·적재 제한·테마 관리·API/MCP 키는 콘솔에서만 다룬다. 콘솔 설계 D2의 드로어 빠른 패널은 폐지). 상단바에서 라이트/다크 전환(`#themebtn`) 바로 왼쪽의 아이콘 링크 `#consolelinkbtn`이 owner에게만 보인다. `GET /whoami`의 `is_admin`이 참이면 `🛠️`(`/admin`, 이름 '관리자 콘솔'), 아니면 `⚙️`(`/settings`, 이름 '내 설정')이다. 협업자·읽기 전용·익명에게는 보이지 않는다.

### 5.10 옛 `/syndication/preferences` 호환 302

**[코드]** `server.py:syndication_preferences_ui_route`. 규칙 `_system("owner")`(루트에서만 받으며 접두 경로 `/@{username}/syndication/preferences` 등은 404. owner가 아니면 404, 위치 헤더 없음).

1. `?theme=`가 있으면 먼저 엄격히 해석한다(`_get_theme_ctx`). 없거나 접근할 수 없으면 404.
2. `?tab=`을 아래 표로 바꾼다. 빈 값, 대소문자가 다른 값, 모르는 값은 404다. `?tab=`이 없으면 `reader`와 같다.
3. `/settings/*` 대상에는 검증한 테마의 공개 참조를 `?theme=`로 붙인다. `/admin/*`와 `/settings/account` 대상에는 `?theme=`를 붙이지 않는다.
4. 응답은 302, `Cache-Control: no-store`, `Referrer-Policy: same-origin`. 위치는 항상 루트 경로다(접두 아래 요청도 `/@{username}`을 붙이지 않는다).

| `?tab=` | 관리자 | 그 밖의 owner |
| :--- | :--- | :--- |
| 없음, `reader` | `/settings/reader` | `/settings/reader` |
| `accounts` | `/settings/connections` | `/settings/connections` |
| `feeds`, `tags`, `rules`, `outbound`, `opml`, `mcp`, `webmcp` | `/settings/<같은 이름>` | `/settings/<같은 이름>` |
| `idp` | `/admin/idp` | `/settings/account` |
| `userdb` | `/admin/accounts` | `/settings/account` |
| 그 밖 | 404 | 404 |


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
- **[코드] 스키마**: 설계 §13.3의 테이블 전부와 `meta`를 `CREATE TABLE IF NOT EXISTS`로 만든다. P1이 다루는 테이블은 `account_passwords`, `login_throttle`, `admin_audit`다. P3에서 `invitations`, `invitation_redemptions`, `username_tombstones`, P4에서 `provider_keys`, `account_usage`(v2)가 활성화되었다.
- **[코드] 버전**: `meta.schema_version`은 `AUTH_DB_SCHEMA_VERSION = 3`이며 claire.db의 버전과 별개다. v2(P4)에서 `account_usage`에 UTC 일 적재 집행용 `daily_day`·`daily_ingests` 열이 추가되었고, v3(P5)에서 `oidc_states`에 계정 연결용 `account_id` 열이 추가되었다. 저장된 값이 3보다 크면 `RuntimeError`(다운그레이드 거부), 1보다 작거나 정수가 아니면 `RuntimeError`. [SCHEMA_VERSIONING.md](SCHEMA_VERSIONING.md) 참고.
- **[코드] 준비 시점**: `claire migrate`가 claire.db보다 먼저 만들고 `auth_db schema_version=3`을 출력한다. 서비스 기동(`create_app`)도 `prepare_auth_db`로 만들고 정리한다. 라우트는 `mode=rw`로 열어 파일이 없으면 만들지 않고 실패한다(`common.open_auth_db`).

## 9. 감사 기록

- **[코드] 원칙**: `admin_audit`는 계정을 `actor_account_id`·`target_account_id`로만 기록한다. `record_admin_audit`는 `detail`의 어느 깊이에든 키 `username`(대소문자 무시)이 있으면 `ValueError`를 낸다(설계 K4). 토큰 값은 넣지 않고 `credential_id`만 넣는다.
- **[코드] 기록하는 동작**

| `action` | 언제 | `detail` |
| :--- | :--- | :--- |
| `account.setup` | `/setup` 확정 | `channel`, `credential_id`, `password_saved` |
| `account.password_change` | `/api/me/password` 성공 | `other_sessions_revoked` |
| `account.provider_key_put` | `PUT /api/me/keys` 성공 | `provider` |
| `account.provider_key_delete` | `DELETE /api/me/keys` 성공 | `provider`, `deleted` |
| `identity.link` | `GET /auth/oidc/{id}/callback` 신원 연결 확정 | `iss`, `provider` |

- **[코드]** 로그인 성공·실패, 로그아웃, 재인증은 감사 기록에 남기지 않는다. 로그인 실패는 `login_throttle`에만 남는다.
- **[코드]** 조회는 `list_admin_audit`(최근 것부터)이며, 이를 노출하는 HTTP 경로는 없다.

## 10. 외부 IdP 및 OIDC 연동 (P5)

- **[코드] 공급자 메타데이터**: `data/auth/idp_providers.json` (권한 `0600`, 원자적 교체 쓰기). client secret은 절대 JSON에 저장하지 않으며 오직 환경변수 `CLAIRE_OIDC_<PID>_CLIENT_SECRET` 또는 Docker Secret 파일(`/run/secrets/claire_oidc_<pid>_client_secret`)로만 런타임에 결합한다.
- **[코드] OIDC 코어 & GitHub 어댑터**: `src/claire/api/oidc.py`.
  - `OidcClient`: `/.well-known/openid-configuration` 탐색(24시간 캐시), JWKS 키 캐싱 및 `kid` 불일치 시 1회 강제 리프레시, PKCE S256, ID 토큰의 RS256/ES256 서명 검증(`cryptography`), 클레임(`iss`, `aud`, `azp`, `exp`, `iat`, `nonce`, `hd`, `tid`) 유효성 검증.
  - `GitHubAdapter`: OAuth 2.0 흐름(인가 URL, access_token 교환, `GET /user`의 숫자 `id`를 `sub`로 사용, `GET /user/emails`의 primary & verified 이메일 확인).
- **[코드] 상태 교환 및 재생 방지**: `oidc_states` 테이블에 10분 만료로 `key`, `state`, `nonce`, `code_verifier`, `purpose`, `invitation_id`, `account_id`를 보관하며, `consume_oidc_state`로 1회 조회 즉시 원자적으로 삭제한다.
- **[코드] 초대에 묶인 가입 (Rev. 2.3)**: 콜백 시점에 초대를 미리 차감하지 않고 유효성만 확인한 뒤 10분 만료의 암호화된 가입 세션(`signup_token`)을 발급한다. 사용자가 `/invite/complete`에서 불변 USERNAME을 제출하여 계정을 생성하는 최종 트랜잭션에서 `redeem_invitation_atomic`을 통해 원자적으로 초대를 차감하고 `account_identities`를 연결한다.
- **[코드] 미연결 신원 로그인 방어**: 초대 없이 IdP로 로그인하려는 시도 중 미연결 신원은 가입을 자동 생성하지 않고 403 "초대가 필요합니다" 안내를 반환한다 (§12-5).
- **[코드] 인증된 계정 연결**: `POST /api/me/identities/oidc/{id}/start`는 `owner` 권한 및 단계 인증(`require_recent_auth`)을 통과해야 시작할 수 있다. 연결 해제(`DELETE /api/me/identities/oidc/{id}`) 시 유일한 인증 수단(비밀번호 없음 + 마지막 신원)인 경우 해제를 거부한다.

## 11. Syndication 병합 기반 (P6)

- **[코드] 루트 고유 기능 경로 및 프로토타입 분리 (G8, N5)**: `/syndication`(현재 구현된 실데이터 기반 리더·지식베이스 적재 워크스페이스)과 `/prototype/syndication`(시각적 연합/피어 시뮬레이션 프로토타입)은 모두 루트 전용 `_system("read")` 라우트이며, `/syndication/preferences`는 `_system("owner")`의 호환 302다(§5.10). 비인증 요청은 차단되며 Gate G8이 충족된다. 유저네임 하단 경로(`/@{username}/syndication`, `/@{username}/prototype/syndication`)나 테마 접두 경로(`/themes/{number}/syndication`)는 404 Not Found를 반환하며(N5), `@username`은 루트 프로필/워크스페이스(`/@{username}/`) 및 기능 내 타 유저 호출 용도로만 사용한다.
- **[코드] 발행 주체 귀속**: `POST /api/syndication/dispatch`(`_system("write")`)는 호출자의 `Principal.account_id`를 판별하여 `syndication_dispatches.dispatched_by_account_id`에 영구 기록한다(단일 모드는 관리자 1). 기존 `dispatched_by_user_id` 열은 하위 호환을 위해 보존된다.
- **[코드] 연결 계정 토큰 암호화**: `syndication_accounts.token_enc`는 `SecretService`의 AES-256-GCM 암호화를 사용하며, AAD로 `f"{theme_id}|{account_id}"`를 결합한다. 데이터베이스 행을 다른 테마나 식별자로 무단 이동·변조하면 복호화 시 `SecretDecryptionError`가 발생한다.
- **[코드] 테마 경계 및 계정 격리**: 테마는 자신이 소유하지 않은 연결 계정(다른 `theme_id`의 `syndication_accounts`)을 조회하거나 사용할 수 없다. 또한 타 계정 소유의 테마에는 권한 없이 발행할 수 없다.
- **[코드] 설정 화면 이전**: 옛 `/syndication/preferences` 화면(`syndication_preferences.html`)은 지웠다. 리더 설정 패널은 `/settings/{reader,connections,feeds,tags,rules,outbound,opml,mcp,webmcp}`(§5.9)로, `panel-idp`·`panel-userdb`는 관리자에게 `/admin/idp`·`/admin/accounts`, 멤버에게 `/settings/account`로 옮겼다. `/syndication` 및 `/prototype/syndication` 화면의 설정 링크(`data-settings-path`)는 `/settings/*`를 가리키고 `?theme=<참조>`를 붙인다. 리더는 콘솔과 같은 localStorage 키 `claire_synd_prefs_v2:<storage-key>:t<테마 참조>`를 쓰고, 옛 키 `claireSyndicationPrefs_v2`는 읽지도 옮기지도 않는다(C1).
- **[코드] 본문 렌더링 보안 (K10, S1)**: 연합 게시물 및 수집 본문은 XSS 위험을 방지하기 위해 DOMPurify 살균 및 safe parsing을 거쳐 안전하게 렌더링된다.

## 12. 테스트

`tests/test_schema_v15.py`, `tests/test_account_store.py`, `tests/test_auth_db.py`, `tests/test_ids.py`, `tests/test_username_policy.py`, `tests/test_principal.py`, `tests/test_setup_flow.py`, `tests/test_login_flow.py`, `tests/test_account_routes.py`, `tests/test_api_security.py`, `tests/test_account_forms_browser.py`, `tests/test_invite_flow.py`, `tests/test_username_release.py`, `tests/test_me_keys.py`, `tests/test_secret_write_only.py`, `tests/test_oidc_core.py`(OIDC discovery, JWKS, PKCE, RS256/ES256 토큰 검증, GitHub 어댑터), `tests/test_oidc_flow.py`(가짜 IdP로 로그인·연결·초대 가입·원자적 차감·관리자 IdP 관리), `tests/test_syndication_account_tokens.py`(연결 계정 토큰 암호화 및 AAD 격리), `tests/test_syndication_dispatch_account.py`(발행 주체 계정 귀속 및 권한 검증), `tests/test_e2e_syndication_namespace.py`(P6 단일/네트워크 모드 Syndication E2E 통합 검증), `tests/test_console_routing.py`·`tests/test_admin_instance.py`·`tests/test_settings_account.py`·`tests/test_settings_syndication.py`(통합 콘솔 경로·권한·섹션), `tests/test_syndication_route.py`(옛 `/syndication/preferences` 302 표), `e2e/test_admin_and_settings_console_e2e.py`(콘솔 브라우저 E2E).
