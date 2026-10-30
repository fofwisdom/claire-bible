# 외부 IdP 연동 및 사용자별 워크스페이스 설계 제안

- **상태**: 제안 (미구현). 시각 프로토타입: `/syndication/preferences?tab=idp`, `?tab=userdb`
- **작성일**: 2026-10-30
- **관련 문서**: [MULTI_THEME_ARCHITECTURE_DESIGN.md](MULTI_THEME_ARCHITECTURE_DESIGN.md), [MCP_SUPPORT.md](MCP_SUPPORT.md), [API_AND_MCP_GUIDE.md](../implementation/API_AND_MCP_GUIDE.md), [SYNDICATION_ENDPOINT_DESIGN.md §3.8](SYNDICATION_ENDPOINT_DESIGN.md)

---

## 1. 배경과 목표

현재 Claire Bible은 **운영자 한 명**을 위한 시스템이다. 운영자는 텔레그램(`TELEGRAM_OWNER_CHAT_ID`) 승인으로 로그인하고, 다른 사람에게는 owner · collaborator · readonly 토큰을 나눠 준다. 테마는 DB를 물리적으로 격리하지만 모든 테마가 운영자 소유다.

이 제안은 OpenID Connect IdP로 **여러 사용자**가 로그인하고, 사용자마다 **독립 워크스페이스**를 받게 한다. 워크스페이스는 자기 테마 레지스트리와 SQLite DB · 볼트 묶음이다.

목표:
1. 사용자 간 데이터가 섞이지 않는다. DB 경로는 인증 결과로만 결정된다.
2. 기존 운영자 데이터와 텔레그램 로그인은 그대로 둔다. 이관이 필요 없다.
3. 테마 격리 규칙(일련번호 디렉터리, 소각 프로토콜, 툼스톤)을 한 단계 위에서 재사용한다.
4. 공유 LLM 공급자 키를 한 사용자가 독점하지 못한다.

비목표 (v1):
- SAML 직접 지원. 필요하면 Keycloak 같은 브로커를 거친다.
- 사용자 간 테마 공유, 교차 워크스페이스 검색.
- SCIM 프로비저닝.
- 사용자용 텔레그램 봇. 봇은 운영자 전용으로 남는다.

## 2. 용어

| 용어 | 뜻 |
| :--- | :--- |
| 워크스페이스 | 한 주체가 소유한 테마 레지스트리 + 테마별 DB · 볼트 묶음 |
| 운영자 워크스페이스 (seq 0) | 현재의 `data/` 전체. 지식 관리자 소유. 변경 없음 |
| 사용자 워크스페이스 (seq ≥ 1) | `data/users/{seq}/`. IdP로 로그인한 사용자 소유 |
| 식별자 | `(iss, sub)` 쌍. 이메일은 식별자가 아니다 |

## 3. 인증

### 3.1 공급자

| 종류 | 방식 | 식별 키 | 추가 확인 |
| :--- | :--- | :--- | :--- |
| Google | OIDC | `sub` | Workspace 도메인 `hd` |
| Microsoft Entra ID | OIDC | `sub` (앱별 고정) | `tid` 테넌트 |
| Keycloak · Authentik 등 | OIDC | `sub` | `groups` (선택) |
| GitHub | OAuth 2.0 (OIDC 아님) | `GET /user`의 숫자 `id` | `GET /user/emails`의 `verified` |

- 공급자 메타데이터(id, 종류, issuer, client_id, 사용 여부)는 `data/auth/idp_providers.json`(0600, 원자적 쓰기)에 둔다.
- client secret은 파일에 쓰지 않고 `CLAIRE_OIDC_<ID>_CLIENT_SECRET` 환경변수나 비밀 파일로만 받는다. 웹 화면에서 입력받지 않는다.
- OIDC 공급자는 `/.well-known/openid-configuration`으로 엔드포인트와 JWKS를 찾고, JWKS는 캐시하되 `kid` 불일치 시 한 번 다시 받는다.

### 3.2 로그인 흐름

```
GET  /auth/oidc/{id}/start        → state · nonce · code_verifier 저장, IdP authorize로 302 (PKCE S256)
GET  /auth/oidc/{id}/callback     → state 확인 → POST token_endpoint (code_verifier, client secret)
                                  → ID 토큰 검증: JWKS 서명 · iss · aud · exp · iat · nonce (azp가 있으면 확인)
                                  → (iss, sub)로 사용자 조회 → 가입 정책 → 세션 발급
```

- `state`, `nonce`, `code_verifier`는 10분 만료의 서버 측 레코드로 두고, 쿠키에는 무작위 키만 담는다.
- 콜백 실패는 원인을 사용자에게 자세히 노출하지 않고, 운영 로그와 텔레메트리에만 남긴다.

### 3.3 식별과 계정 연결

- 계정은 `user_identities(iss, sub)` UNIQUE로만 찾는다. 이메일은 바뀌거나 재할당될 수 있어 식별에 쓰지 않는다.
- 다른 IdP에서 같은 이메일이 들어와도 **자동으로 합치지 않는다**. 사용자는 기존 수단으로 로그인한 뒤 '로그인 수단 연결'에서 새 IdP로 재인증해야 한다. 이메일만으로 계정을 합치면 IdP 하나의 약점이 계정 탈취로 이어진다.

### 3.4 가입 정책

| 정책 | 동작 |
| :--- | :--- |
| `approval` (기본) | 첫 로그인 시 `pending` 사용자를 만들고 운영자 텔레그램에 `[승인] [거절]` 푸시를 보낸다. OAuth 승인 화면의 텔레그램 푸시와 같은 방식이다 |
| `auto` | 허용 도메인 계정은 즉시 워크스페이스를 만든다 |
| `invite` | 초대 목록의 이메일만 가입한다 |

공통 조건:
- `email_verified=true` 필수 (설정으로 끌 수 있음).
- 허용 도메인 목록이 있으면 이메일 도메인이 목록에 있어야 한다. Google은 `hd`도 확인한다.
- IdP 클레임(그룹 포함)으로 운영자 권한을 만들거나 승격할 수 없다. 운영자는 계속 텔레그램으로만 인증한다.

### 3.5 세션과 로그아웃

- 사용자 세션은 기존과 같은 7일 슬라이딩이다. 쿠키 이름 · 속성은 기존 `claire_session` 규칙을 따른다.
- 현재 `auth_sessions`는 운영자의 `claire.db` 안에 있다. 사용자 세션은 API 토큰을 `data/auth/api_tokens.json`으로 분리한 것과 같은 이유로 `data/auth/auth.db`에 둔다. 지식 DB 장애가 인증으로 번지지 않게 하려는 것이다.
- IdP가 OIDC Back-Channel Logout을 지원하면 `logout_token`을 받아 해당 사용자의 세션과 그 사용자가 발급한 API · MCP 토큰을 끊는다.
- 운영자가 사용자를 정지하면 같은 범위를 즉시 끊는다.

## 4. 사용자별 워크스페이스

### 4.1 디렉터리 구조

```
data/
├─ claire.db                     ← 운영자 워크스페이스 seq 0 · Theme 0 (현행 그대로)
├─ themes.json · themes/{n}/     ← 운영자 테마 (현행 그대로)
├─ auth/
│  ├─ api_tokens.json            ← 토큰마다 workspace_seq 추가 (없으면 0)
│  ├─ idp_providers.json
│  └─ auth.db                    ← users · user_identities · user_sessions · invites · oidc_states
└─ users/{seq}/
   ├─ themes.json                ← 이 사용자의 테마 레지스트리 (Theme 0만으로 시작)
   ├─ claire.db                  ← 이 사용자의 Theme 0
   ├─ themes/{n}/claire.db
   └─ raw/ · images/
vault/users/{seq}/…
```

- 사용자 `seq`는 단조 증가하며 재사용하지 않는다. 테마 일련번호와 같은 이유(경로 안정성, 잠금 충돌 방지)다.
- 워크스페이스 생성은 `init_db`로 빈 Theme 0을 만들고 `themes.json`을 원자적으로 쓴다.

### 4.2 라우팅

1. 게이트 미들웨어가 자격증명을 확인하고 주체(principal) `{workspace_seq, role, credential_id}`를 request state에 기록한다.
2. `WorkspaceManager.get(workspace_seq)`가 그 워크스페이스의 `ThemeManager`를 돌려준다. `IngestServicePool`의 키는 `(workspace_seq, theme_id)`가 된다.
3. `?theme=`, `X-Claire-Theme`, MCP `theme` 인자는 **자기 워크스페이스 안의 테마만** 가리킨다. 요청으로 다른 워크스페이스를 지정하는 방법은 없다.
4. 다른 워크스페이스의 문서 · 테마 id를 넣으면 존재를 숨기는 `404`를 돌려준다.
5. 테마 전용 FQDN은 v1에서 운영자 워크스페이스에만 둔다.

### 4.3 권한

| 주체 | 자기 워크스페이스 | 다른 사용자 워크스페이스 | 운영자 워크스페이스 |
| :--- | :--- | :--- | :--- |
| 사용자 (IdP 로그인) | owner와 같음 (테마 생성 · 적재 · 삭제) | 접근 불가 (404) | 접근 불가 (404) |
| 사용자가 발급한 토큰 | 발급 권한(owner · collaborator · readonly) 안에서 | 접근 불가 | 접근 불가 |
| 운영자 | — | 메타데이터만 (용량 · 건수 · 상태 · 승인 · 정지 · 삭제) | 현행 그대로 |

- 운영자 화면과 support bundle에는 사용자 문서 내용이 들어가지 않는다.
- 예외는 하나다. 사용자가 공개 테마에서 적재 발행한 본문의 **공개 사본**은 공용 발행 색인을 거쳐 다른 사용자가 읽을 수 있다 (§4.7). 워크스페이스 DB 자체는 여전히 열리지 않는다.
- 사용자 워크스페이스의 `is_public` 테마는 v1에서 익명 공개하지 않는다. 공개 범위는 후속 과제로 둔다.

### 4.4 API · MCP

- 엔드포인트는 그대로 하나(`/mcp`, REST 경로)이고, 토큰에 묶인 `workspace_seq`로 DB가 정해진다.
- OAuth 2.1 승인 화면은 로그인한 사용자 본인의 워크스페이스로 토큰을 발급한다. `oauth_tokens`에 `workspace_seq`를 추가한다.
- 이 라우팅이 들어가면 [MCP_SUPPORT.md §7](MCP_SUPPORT.md)의 1번(테마 접근 권한 미적용)도 워크스페이스 · 테마 판정으로 함께 해결해야 한다.

### 4.5 할당량과 공정성

| 항목 | 기본값 (프로토타입) | 초과 시 |
| :--- | :--- | :--- |
| 저장 용량 | 2,048 MB | 적재 거부 (조회는 허용) |
| 문서 수 | 5,000 | 적재 거부 |
| 테마 수 | 5 | 테마 생성 거부 |
| 월 LLM 토큰 | 2,000,000 | 그 사용자의 적재 · 요약 · 확장만 중지 |
| 수집 대상 수 | 100 | 구독 추가 거부 |
| 자체 공급자 키 | 허용 안 함 | — |

- LLM 사용량은 기존 `provider_telemetry`에 `workspace_seq`를 더해 월 단위로 합산한다.
- MCP 도구와 조회는 LLM을 쓰지 않으므로 예산과 무관하게 동작한다.
- recover · refresh · expand 루프는 테마 대신 `(workspace_seq, theme_id)` 쌍을 회전하고, 사이클당 사용자별 상한을 둔다. 한 사용자의 대량 적재가 다른 사용자를 막지 않게 하려는 것이다.
- 자체 공급자 키를 허용하면 키는 서버 측에서 암호화해 저장하고, 화면에는 끝 4자리만 보여 준다.

### 4.6 수명주기

```
pending ──승인──▶ active ──정지──▶ suspended ──재개──▶ active
   │                │                  │
  거절            삭제 요청            삭제
   ▼                ▼                  ▼
 (제거)        deleting (7일 유예, 취소 가능) ──▶ purged
```

- 삭제는 테마 삭제 · 소각 프로토콜을 워크스페이스 단위로 적용하고, 툼스톤으로 같은 원본의 자동 재수집을 막는다.
- 내보내기는 SQLite backup API로 만든 DB 스냅샷과 볼트를 묶어 **본인에게만** 일회성 링크로 준다. 운영자는 사용자 데이터를 내보낼 수 없다.

### 4.7 사용자 간 발행 본문 공유

같은 Claire 인스턴스의 사용자는 다른 사용자가 **공개 발행한 본문**의 목록을 보고, 그 본문을 **자기 연결 계정으로** 다시 발행할 수 있다. 연합 클라이언트 탭과 지식베이스 탭 모두에서 쓴다. 화면은 [SYNDICATION_ENDPOINT_DESIGN.md §3.9](SYNDICATION_ENDPOINT_DESIGN.md)에 있다.

**격리를 깨지 않는 구조**

1. 사용자가 공개 테마에서 `📡 적재 발행`을 하면, 서버가 공개 사본을 `data/shared/publications.db`에 쓴다. 비공개 테마, 초안, 수집 원문, 발행하지 않은 적재 본문은 올라가지 않는다.
2. 다른 사용자는 이 색인만 조회한다. 다른 워크스페이스의 `claire.db`를 여는 경로는 만들지 않는다.
3. 색인에 올라가는 것은 이미 공개된 정보뿐이다. 제목, 요약, 발행 본문 HTML, 엔티티 이름, 공개 공유 링크, 발행된 게시물 URI · CID가 여기에 해당한다. 작성자는 사용자 번호와 표시 이름으로만 나타난다. 이메일과 IdP 식별자는 싣지 않는다.

**목록 규칙**

- 보는 사람 자신의 발행 본문은 제외한다.
- 정지 · 삭제 중인 사용자와 공유를 끈 사용자의 본문은 숨긴다.
- 철회된 본문은 숨긴다. 단, 보는 사람이 이미 가져오거나 재발행했다면 '철회됨'으로 남긴다.

**재사용 범위 (작성자가 정함)**

| 재사용 | 기본 | 작성자가 막을 수 있음 | 비고 |
| :--- | :--- | :--- | :--- |
| 부스트 (reblog · repost) | 허용 | 아니요 | 공개 게시물이므로 서버 동작을 막을 수 없다 |
| 링크 공유 | 허용 | 아니요 | 공개 공유 링크를 인용한 새 글 |
| 인용 발행 | 허용 | 예 | 연합우주에서는 원 게시물의 `quote_approval_policy`도 함께 적용된다 |
| 가져오기 (적재 · 재가공 · 재발행) | 허용 | 예 | 재발행 글에는 원문 링크와 작성자 표시가 붙는다 |

이용 허락 표시(예: CC BY 4.0)는 선택이며 화면 표시용이다. 법적 효력은 운영 약관을 따른다.

**내 계정으로 발행**

- 지금 테마의 쓰기 가능한 연결 계정으로 보낸다. 다른 사용자의 연결 계정이나 토큰은 쓰지 않는다.
- 부스트와 네이티브 인용은 원 작성자가 같은 프로토콜로 발행한 게시물이 있어야 한다. ActivityPub은 원 게시물 URI를 내 서버에서 `resolve=true`로 조회한 뒤 그 id를 쓴다. 같은 프로토콜 게시물이 없으면 링크 공유로 대체한다.
- `가져와 적재 발행`은 세 단계로 진행된다. 먼저 가져오고, 내 테마 DB에 적재하고(내 월 LLM 예산 사용), 만들어진 내 적재 본문을 발행한다.

**가져오기**

- 가져온 본문은 내 지식베이스의 수집 원문이 되며, 출처는 `proto = "claire"`, 피드 `가져온 사용자 발행 본문`이다.
- 원 작성자의 `[[엔티티]]` 표기는 평문으로 바꾸고, 엔티티 이름은 `#수집 태그`로 보존한다. 엔티티는 내가 적재할 때 내 테마 DB에 생성된다. 수집과 적재를 나누는 기존 불변식을 그대로 따른다.
- 사본에는 `peer_pub_id`, 원 작성자 번호, 원문 링크, 이용 허락을 남긴다. 다시 발행할 때 원문 표시를 자동으로 붙이는 데 쓴다.

**철회**

- 작성자가 철회하면 색인 항목과 공개 공유 페이지가 내려간다.
- 연합우주 원 게시물이 삭제되면 부스트는 각 서버가 지운다. 다른 사용자의 링크 공유 · 인용 글은 남지만 링크가 끊긴다.
- 가져간 사본은 가져간 사용자의 워크스페이스에 남는다. 다만 '철회됨'으로 표시되고 다시 발행할 수 없다. 사본까지 지우도록 강제하지는 않는다. 다른 사용자의 워크스페이스를 수정하지 않는다는 원칙 때문이다.

**보안**

- 발행 본문 HTML은 다른 사용자의 브라우저에서 그려진다. 색인에 쓰기 전에 공개 공유 페이지와 같은 허용 목록 기반 sanitizer를 거쳐야 하며, 렌더링 쪽은 스크립트 · 이벤트 속성 · 외부 리소스를 막는 CSP를 유지한다.
- 색인 조회는 로그인한 사용자 주체만 할 수 있다. 사용자 토큰 중 readonly · collaborator는 목록과 본문 조회만 할 수 있고, 재발행과 가져오기는 owner 권한이 필요하다.
- 스팸이나 남용이 생기면 운영자는 사용자를 정지해 그 사용자의 본문을 색인에서 숨긴다. 본문 단위 신고 · 숨김은 후속 과제로 둔다.

## 5. 데이터 모델 (`data/auth/auth.db`)

```sql
CREATE TABLE users (
    seq INTEGER PRIMARY KEY,               -- 워크스페이스 일련번호, 재사용 안 함
    display_name TEXT,
    email TEXT,                            -- 표시 · 연락용. 식별자 아님
    status TEXT NOT NULL,                  -- pending | active | suspended | deleting
    created_at REAL NOT NULL,
    approved_at REAL,
    delete_after REAL,                     -- deleting 상태의 유예 만료
    quota_json TEXT                        -- 기본값을 덮어쓸 때만
);
CREATE TABLE user_identities (
    iss TEXT NOT NULL,
    sub TEXT NOT NULL,
    user_seq INTEGER NOT NULL REFERENCES users(seq),
    provider_id TEXT NOT NULL,
    linked_at REAL NOT NULL,
    last_login_at REAL,
    PRIMARY KEY (iss, sub)
);
CREATE TABLE user_sessions (
    session_token TEXT PRIMARY KEY,
    user_seq INTEGER NOT NULL,
    idp_sid TEXT,                          -- Back-Channel Logout 매칭용
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE invites (email TEXT PRIMARY KEY, invited_at REAL NOT NULL, used_at REAL);
CREATE TABLE oidc_states (key TEXT PRIMARY KEY, provider_id TEXT, state TEXT, nonce TEXT, code_verifier TEXT, expires_at REAL);
```

공용 발행 색인은 `auth.db`와 분리해 `data/shared/publications.db`에 둔다 (§4.7).

```sql
CREATE TABLE publications (
    id TEXT PRIMARY KEY,                   -- 'pub-{user_seq}-{n}'
    user_seq INTEGER NOT NULL,
    theme_label TEXT,
    title TEXT NOT NULL,
    summary TEXT,
    body_html TEXT NOT NULL,               -- sanitizer 통과본
    entity_names_json TEXT,
    share_token TEXT NOT NULL,             -- /p?s=<token>
    posts_json TEXT,                       -- [{proto, handle, uri, cid, url}]
    allow_quote INTEGER NOT NULL DEFAULT 1,
    allow_import INTEGER NOT NULL DEFAULT 1,
    license TEXT,
    published_at REAL NOT NULL,
    retracted_at REAL
);
CREATE INDEX idx_publications_user ON publications(user_seq, published_at);
CREATE TABLE publication_reuse (           -- 집계 · 철회 알림용. 누가 무엇을 했는지만 남긴다
    pub_id TEXT NOT NULL REFERENCES publications(id),
    user_seq INTEGER NOT NULL,
    mode TEXT NOT NULL,                    -- boost | quote | link | import
    at REAL NOT NULL
);
```

## 6. 계획 라우트

| Method | Path | 접근 | 목적 |
| :--- | :--- | :--- | :--- |
| `GET` | `/login` | public | 로그인 화면 (IdP 버튼 + 운영자 텔레그램) |
| `GET` | `/auth/oidc/{id}/start` | public | 인가 요청 시작 |
| `GET` | `/auth/oidc/{id}/callback` | public (state 바인딩) | 콜백 처리 · 세션 발급 |
| `POST` | `/auth/oidc/{id}/backchannel-logout` | public (logout_token 서명 검증) | IdP 로그아웃 수신 |
| `POST` | `/auth/logout` | user | 로그아웃 |
| `GET`, `POST`, `DELETE` | `/me/identities` | user | 로그인 수단 조회 · 연결 · 해제 (마지막 수단은 해제 불가) |
| `POST` | `/me/export` · `/me/delete` | user | 내보내기 · 삭제 요청 |
| `GET`, `PATCH` | `/admin/users` | owner (운영자) | 디렉터리 · 승인 · 정지 · 삭제 |
| `GET`, `PUT` | `/admin/idp` | owner (운영자) | 공급자 메타데이터 (secret 제외) |
| `GET` | `/api/peers/publications` | user | 다른 사용자 공개 발행 사본 목록 · 본문 (§4.7) |
| `POST` | `/syndication/peers/reshare` | user (owner) | 내 연결 계정으로 부스트 · 인용 · 링크 공유 |
| `POST` | `/syndication/peers/import` | user (owner) | 내 테마로 가져오기 (선택: 바로 적재) |
| `PATCH` | `/me/share-policy` | user | 목록 노출 · 인용 · 가져오기 허용 · 이용 허락 |

모든 경로는 `security.ROUTE_POLICY`와 라우트 계약 시험에 함께 등록한다.

## 7. 프로토타입 범위

`/syndication/preferences`의 **계정 · 데이터 격리 (제안)** 그룹에서 다음을 보여 준다. 모든 데이터는 예시이고 서버는 바뀌지 않는다.

- **🪪 외부 IdP 로그인**: 로그인 방식, 공급자 표(사용 스위치 · issuer · client_id · 리디렉트 URI · 식별 키), 공급자 추가와 Discovery 문서 시뮬레이션, 가입 정책(승인 · 즉시 · 초대, 허용 도메인, 검증 이메일, Back-Channel Logout), 로그인 화면 미리보기, 일곱 가지 가상 사용자로 돌리는 로그인 흐름 시뮬레이터(기존 사용자, 신규 승인 대기, 도메인 거부, 미검증 이메일, 같은 이메일 다른 IdP, 정지 사용자 등).
- **🗄️ 사용자별 워크스페이스**: 격리 구조와 불변식, 사용자당 기본 할당량, 운영자 보기(사용자 디렉터리와 승인 · 정지 · 재개 · 삭제 예약 · 취소), 사용자 보기(식별자 · 연결된 로그인 수단 · 할당량 사용률 · API/MCP 라우팅 · 내보내기 · 삭제 요청).
- **👥 사용자 간 발행 본문 공유** (§4.7)
  - `/syndication`의 두 탭에 다른 사용자 발행 본문 목록 · 리더를 둔다.
  - `내 계정으로 발행` 창에서 부스트 · 인용 · 링크 공유 · 가져와 적재 발행을 고르고, 계정별 요청을 미리 본다.
  - 지식베이스로 가져오기와 철회 시뮬레이션을 둔다.
  - 연합 클라이언트 게시물에 `👥 Claire 본문` 배지를 붙인다.
  - 환경설정 › 부스트 · 적재 발행에 작성자 쪽 공유 정책 카드를 둔다.

## 8. 위험과 열린 질문

1. **파일 수 증가**: 사용자 × 테마 수만큼 SQLite 파일이 생긴다. `IngestServicePool`에 LRU 해제와 연결 상한이 필요하다.
2. **단일 worker**: `claire_api`는 단일 ASGI worker다. 동시 사용자가 늘면 무거운 적재 작업의 대기열 정책을 다시 정해야 한다.
3. **세션 저장소 이전**: 운영자 세션까지 `auth.db`로 옮길지, 사용자 세션만 둘지 정해야 한다. v1은 사용자 세션만 둔다.
4. **개인정보**: 이메일 · 이름 보존 기간, 삭제 후 감사 기록 범위를 정해야 한다.
5. **공개 범위**: 사용자 테마의 익명 공개는 v1 이후로 미룬다. 사용자 간 공유는 공개 발행 사본에 한정한다 (§4.7). 테마 자체를 다른 사용자와 공동 편집하는 기능은 다루지 않는다.
6. **다른 사용자 HTML**: 공용 색인의 본문은 다른 사용자의 브라우저에서 그려진다. sanitizer와 CSP 없이 열면 사용자 간 XSS가 된다.
7. **가져간 사본의 수명**: 철회 후에도 가져간 사본이 남는 것을 작성자에게 미리 알려야 한다. 공유 정책 카드에 그 문구를 둔다.

## 9. 단계

| 단계 | 범위 |
| :--- | :--- |
| P1 | `auth.db`, OIDC 로그인(Google · 범용 OIDC), `approval` 가입, 워크스페이스 생성, 게이트의 principal 기록과 워크스페이스 라우팅 |
| P2 | 할당량 · LLM 예산 · 백그라운드 루프 공정성, 운영자 디렉터리 화면 |
| P3 | API 토큰 · OAuth · MCP의 워크스페이스 바인딩, 내보내기 · 삭제, 로그인 수단 연결 |
| P4 | Back-Channel Logout, GitHub · Entra ID, 초대 정책, (선택) SCIM |
| P5 | 공용 발행 색인, 다른 사용자 발행 본문 목록 · 재발행 · 가져오기, 공유 정책, 철회 처리 |
