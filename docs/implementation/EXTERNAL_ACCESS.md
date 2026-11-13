# 웹 접속과 reverse proxy

Claire 웹 서비스는 환경에 따라 두 가지 접속 형태만 지원한다.

- `development`: Docker host의 정확한 IPv4와 port로 직접 HTTP 접속
- `production`: 별도 LAN reverse proxy가 public hostname과 클라이언트 TLS를 담당하고, Claire에는 HTTP로 전달

Claire 컨테이너에 HTTPS를 구성하거나 인증서를 저장하지 않는다. Claire host에서 Let's Encrypt를 실행하는 절차도 이 문서와 현재 구현의 범위가 아니다. 서비스는 hostname의 root(`/`)에 배치하며 subpath 배포는 지원하지 않는다.

## 공통 네트워크 경계

컨테이너 안의 웹 서버는 `0.0.0.0:CB_API_PORT`에서 듣는다. 외부에 공개되는 주소는 Docker port publish의 host 측 `CB_API_BIND`다. `cb-manuscript`는 `CB_API_BIND`를 단일 IPv4로 검사하고 `0.0.0.0`, multicast, hostname과 IPv6를 거부한다. 따라서 컨테이너 listen 주소를 host 공개 범위로 해석하면 안 된다.

`CLAIRE_FQDN`은 링크 생성 및 요청 Host 보안 정책의 기준이다. (기존 `CLAIRE_PUBLIC_URL`은 하위 호환성을 위해 자동 변환된다.) 클레어바이블의 프로덕션은 HTTPS 서비스가 당연하며 상단에 이를 처리하는 보호장치(Sophos Firewall Web Server Protection, Cloudflare 등)가 존재함을 기본으로 하여 동작한다.

| 환경 | 필수 형태 (`CLAIRE_FQDN`) | 자동 도출 Public URL |
|---|---|---|
| development | `<CB_API_BIND>:<CB_API_PORT>` | `http://<CB_API_BIND>:<CB_API_PORT>/` |
| production | `<DNS-hostname>` (예: `claire.example.com`) | `https://<DNS-hostname>/` |

두 환경 모두 canonical authority는 호스트명(또는 개발 authority)으로 관리된다. `CLAIRE_CORS_ALLOWED_ORIGINS`는 path와 wildcard가 없는 정확한 origin의 쉼표 목록이다. 빈 값이면 same-origin만 허용하고, production 목록은 `https` origin만 사용할 수 있다.

exact `CLAIRE_ANONYMOUS_READONLY=1`(기본값)은 canonical same-origin 또는 Origin 헤더가 없는 요청에서 자격증명 없는 읽기 전용 접근을 허용한다. 이 값은 owner 인증과 쓰기 경로를 없애지 않으며, 숨김 문서(`hidden=1`) 및 그와 연관된 엔티티는 익명 읽기 계층에서 철저히 제외되어 안전하게 공개된다. 완전히 인증 전용으로 운영하려면 `CLAIRE_ANONYMOUS_READONLY=0`으로 설정한다. cross-origin anonymous는 허용하지 않으며, CORS allowlist에 origin을 넣어도 Bearer 요구는 유지된다.

기존 `.env`/`.env.dev`를 재사용하는 설치는 `./cb-manuscript init` 또는 `./cb-manuscript update` 실행 시 레거시 `CLAIRE_PUBLIC_URL`이 존재할 경우 `CLAIRE_FQDN`으로 자동 변환된다. 따라서 `.env`의 `CLAIRE_FQDN`을 도메인 형식으로 직접 설정한 뒤 `./cb-manuscript preflight`를 통과시켜야 한다.

애플리케이션은 `Forwarded`와 `X-Forwarded-*`를 신뢰해 scheme, client IP 또는 Host를 바꾸지 않는다. 예외는 로그인 실패 제한의 클라이언트 IP 하나다(아래 「로그인 실패 제한의 클라이언트 IP」). production의 외부 HTTPS 여부는 상단 보호장치의 TLS 오프로딩과 `CLAIRE_FQDN`을 기준으로 결정하며 백엔드 upstream 연결 자체는 내부 HTTP다. 또한 공인 대역 직접 접근 차단을 위해 `CLAIRE_CLOUDFLARE_IPS_ONLY=1` 옵션을 제공한다(상세 내용은 아래 참조).

## Development: IPv4 직접 HTTP

`.env.dev`의 주소와 URL을 같은 authority로 설정한다.

```dotenv
CLAIRE_ENVIRONMENT=development
CB_API_BIND=192.168.10.25
CB_API_PORT=8766
CLAIRE_PUBLIC_URL=http://192.168.10.25:8766/
CLAIRE_CORS_ALLOWED_ORIGINS=
```

```bash
CLAIRE_ENVIRONMENT=development ./cb-manuscript preflight
CLAIRE_ENVIRONMENT=development ./cb-manuscript up
```

브라우저에서는 `http://192.168.10.25:8766/`로 접속한다. 예시 파일의 loopback은 같은 host에서만 접근하는 안전한 초기값이다. 다른 개발 장치에서 접속할 때만 실제 고정 LAN IPv4로 바꾸고 host firewall의 허용 대역도 필요한 개발 LAN으로 제한한다.

## Production: 별도 LAN reverse proxy

권장 흐름은 다음과 같다.

```text
client -- HTTPS / production hostname --> external reverse proxy
       -- HTTP / fixed LAN addresses --> Claire host:CB_API_PORT
```

Claire host의 `.env`에는 proxy가 도달할 수 있는 고정 LAN IPv4와 사용자가 접속할 hostname을 설정한다.

```dotenv
CLAIRE_ENVIRONMENT=production
CB_API_BIND=192.168.10.25
CB_API_PORT=8765
CLAIRE_PUBLIC_URL=https://claire.example.com/
CLAIRE_CORS_ALLOWED_ORIGINS=https://portal.example.com
```

배포 계약은 다음과 같다.

- reverse proxy의 upstream은 `http://192.168.10.25:8765`처럼 고정한다.
- Claire로 보내는 `Host`는 `CLAIRE_PUBLIC_URL`의 authority와 정확히 같아야 한다.
- 알 수 없는 hostname을 Claire upstream으로 보내지 않고 proxy의 기본 virtual host에서 거부한다.
- NDJSON 응답을 즉시 전달하도록 response buffering을 끄고 upstream read timeout을 장시간 작업보다 길게 둔다.
- proxy access log에는 query string, `Referer`, `Authorization`과 cookie를 기록하지 않는다. 기존 인증 진입 query가 proxy 로그로 유출되지 않아야 한다.
- Claire host firewall은 API port의 source를 reverse proxy의 고정 LAN IP로만 허용한다. Host 검사만으로 backend 직접 접근을 막을 수는 없다.

## Nginx 예시

다음 server block은 외부 reverse proxy에 병합하는 예시다. proxy의 기존 TLS 인증서 설정과 기본 virtual host 정책은 그대로 사용하며 여기서는 인증서 발급·갱신을 다루지 않는다.

```nginx
log_format claire_safe
    '$remote_addr "$request_method $uri $server_protocol" '
    '$status $body_bytes_sent $request_time';

# http context: 인증 추측과 장시간 worker 고갈을 한 IP가 독점하지 못하게 한다.
limit_req_zone $binary_remote_addr zone=claire_per_ip:10m rate=10r/s;
# /search는 인증 scope에 따라 provider 호출 또는 FTS worker를 소비하므로 별도 한도를 둔다.
limit_req_zone $binary_remote_addr zone=claire_search_per_ip:10m rate=2r/s;
limit_conn_zone $binary_remote_addr zone=claire_conn_per_ip:10m;

upstream claire_backend {
    server 192.168.10.25:8765;
    keepalive 16;
}

server {
    listen 443 ssl;
    server_name claire.example.com;

    # ssl_* directives are owned by this external proxy's existing TLS policy.
    access_log /var/log/nginx/claire_access.log claire_safe;
    client_max_body_size 1m;
    client_body_timeout 15s;

    location = /search {
        limit_req zone=claire_search_per_ip burst=4 nodelay;
        limit_conn claire_conn_per_ip 10;

        proxy_pass http://claire_backend;
        proxy_http_version 1.1;
        proxy_set_header Host claire.example.com;
        proxy_set_header Connection "";
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-Proto "";
        proxy_set_header Referer "";

        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location / {
        limit_req zone=claire_per_ip burst=20 nodelay;
        limit_conn claire_conn_per_ip 10;

        proxy_pass http://claire_backend;
        proxy_http_version 1.1;
        proxy_set_header Host claire.example.com;
        proxy_set_header Connection "";
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-Proto "";
        proxy_set_header Referer "";

        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

같은 proxy의 unmatched/default server는 연결을 거부해야 한다. `$request_uri`는 query를 포함하므로 위 안전 로그에서는 사용하지 않는다.

## Fedify 사이드카 경로 (연합)

Fedify 사이드카를 쓰는 인스턴스(`CB_FEDERATION=1`)에서는 reverse proxy가 앱 호스트(`CLAIRE_FQDN`)의 아래 경로만 사이드카(`CB_API_BIND:CB_FEDIFY_PORT`, 기본 포트 `8790`)로 보낸다. 나머지 경로는 모두 Claire(`CB_API_PORT`)로 보낸다. 저장소에는 proxy 설정 파일이 없으며 운영자가 직접 구성한다. 구현 정본은 [FEDERATION.md](FEDERATION.md) §10이다.

| 일치 방식 | 경로 | 목적지 |
|---|---|---|
| 정확히 일치 | `/.well-known/webfinger` | 사이드카 |
| 정확히 일치 | `/.well-known/nodeinfo` | 사이드카 |
| 접두어 | `/nodeinfo/` | 사이드카 |
| 접두어 | `/ap/` | 사이드카 |

- `/.well-known/` 접두어 전체를 사이드카로 보내지 않는다. `/.well-known/oauth-authorization-server`와 `/.well-known/oauth-protected-resource`는 Claire가 응답해야 한다.
- 규칙이 틀리면 양쪽 모두 404로 닫힌다. Claire는 연합 경로를 등록하지 않았고, 사이드카는 연합 경로가 아닌 요청에 404를 낸다. 인스턴스 설정 `federation`이 `off`이거나 모드가 `single`이면 사이드카는 연합 경로에도 404를 낸다.
- 사이드카는 URL을 Claire가 알려 준 정규 origin으로 만들고 요청의 `Host`를 쓰지 않는다. `Cookie`, `Authorization`, `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-Port` 헤더는 사이드카가 읽지 않고 지운다.
- 인박스 경로(`/ap/inbox`, `/ap/actors/{public_id}/inbox`)는 원격 서버의 `POST`를 받는다. proxy는 본문과 `Signature`, `Signature-Input`, `Digest`, `Content-Digest`, `Date`, `Host` 헤더를 바꾸지 않고 전달해야 한다. 서명에 `Host`가 들어가므로 사이드카로 넘기는 `Host`는 앱 호스트여야 한다. 사이드카는 본문을 1 MiB까지만 읽고 넘으면 413을 낸다.
- 사이드카에는 요청 속도 제한이 없다. 속도 제한은 proxy에서 건다.
- Claire host firewall은 `CB_FEDIFY_PORT`의 source도 reverse proxy의 고정 LAN IP로만 허용한다.
- 준비 검사 GF6(사이드카가 공개 origin으로 자기 WebFinger 응답을 받는지)과 GF7(공개 origin의 `/.well-known/oauth-authorization-server`가 Claire의 메타데이터인지)이 이 규칙을 양방향으로 확인한다. `./cb-manuscript app instance check --federation --json`으로 실행한다.

아래는 위 Nginx 예시의 `server` 블록에 더하는 예시다. 이 저장소의 테스트는 Nginx 설정을 실행하지 않는다.

```nginx
upstream claire_fedify {
    server 192.168.10.25:8790;
    keepalive 16;
}

# server { ... } 안에 추가한다.
location = /.well-known/webfinger { proxy_pass http://claire_fedify; proxy_http_version 1.1; proxy_set_header Connection ""; }
location = /.well-known/nodeinfo  { proxy_pass http://claire_fedify; proxy_http_version 1.1; proxy_set_header Connection ""; }
location ^~ /nodeinfo/            { proxy_pass http://claire_fedify; proxy_http_version 1.1; proxy_set_header Connection ""; }
location ^~ /ap/                  { proxy_pass http://claire_fedify; proxy_http_version 1.1; proxy_set_header Connection ""; }
```

---

## 인증 계층 및 접근 권한 제어 (Authentication & RBAC)

Claire Bible은 시스템 소유자(Owner), 협력자(Collaborator), 읽기 전용(Readonly), 익명 사용자(Anonymous)로 구성된 4단계 역할 기반 접근 제어(RBAC)를 제공한다.

| 역할 (Scope) | 자격 증명 (토큰 / 헤더) | 기본 테마 (`seq=0`) | 추가 테마 (공개) | 추가 테마 (비공개) | 테마 관리 (생성/수정/삭제) |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **시스템 관리자 (`owner`)** | DB 영구 토큰(`cb_...`), 텔레그램 `/web` 세션, 또는 `claire auth` (레거시 `CLAIRE_INJECT_TOKEN` 자동 이관) | 읽기 / 쓰기 | 읽기 / 쓰기 | 읽기 / 쓰기 | 가능 (`define`, `update`, `delete`, `tokens`) |
| **협력자 (`collaborator`)** | DB 영구 협력자 토큰(`cb_...`) 또는 텔레그램 `/webco` 발급 세션 (레거시 `CLAIRE_COLLABORATOR_TOKEN` 자동 이관) | **쓰기 불가 (차단)**<br>(읽기만 가능) | 읽기 / 쓰기 | 허용 테마만<br>읽기 / 쓰기 | 불가 |
| **읽기 전용 (`readonly`)** | DB 영구 읽기전용 토큰(`cb_...`) 또는 텔레그램 `/webro` 발급 세션 (레거시 `CLAIRE_READONLY_TOKEN` 자동 이관) | 읽기 전용 | 읽기 전용 | 읽기 전용 | 불가 |
| **익명 사용자 (`anonymous`)** | 자격증명 없음 (`CLAIRE_ANONYMOUS_READONLY=1`) | 공개 읽기<br>(숨김 문서 제외) | 공개 읽기<br>(숨김 문서 제외) | **404 Not Found**<br>(존재 은닉) | 불가 |

### 협력자 세션 (`collaborator`) 및 안전 정책
- **발급 및 진입**: 텔레그램 봇의 `/webco` 명령을 통해 24시간 유효한 일회성/재사용 가능 협력자 세션 링크(`/webco?token=...`)를 발급받거나 `claire token create --scope collab`으로 상시 토큰을 발급한다.
- **기본 지식베이스 보호**: 협력자는 시스템의 핵심 기반인 기본 테마(`seq=0`)에 데이터를 적재하거나 수정할 수 없습니다(`403 Forbidden`). 오직 `is_collaborator_accessible=true`로 설정된 추가 테마에만 수집 및 적재가 허용된다.
- **세션 헤더**: API 호출 시 `X-Session: <token>` 또는 `Authorization: Bearer <token>` 헤더를 전달한다.
- **Syndication UI (`/syndication`)**: 루트 전용 `_system("read")` 규칙이다. 1단계 시각 검토용 `public` 규칙은 해제했다([`ACCOUNTS.md`](ACCOUNTS.md) §11). 옛 `/prototype/syndication` 라우트는 지웠다(RD20). 이제 404이고, 예시 데이터만 쓰는 프로토타입은 `prototype` 역할 호스트가 준다(아래 "서브도메인 역할 호스트").

---

## 멀티 테마 웹/API 접근 정책 (Multi-Theme Access Policy)

멀티 테마 모드(`CLAIRE_MULTI_THEME=1`)에서는 요청별로 대상 테마를 명시적으로 지정하여 `theme_id`로 파티셔닝된 지식 데이터를 조회 및 적재할 수 있다.

### HTTP 요청 시 테마 지정 방식
1. **HTTP 헤더**: `X-Claire-Theme: <theme_id_or_label>`
   - 권장 REST/MCP 클라이언트 방식.
   - 예: `X-Claire-Theme: 1` 또는 `X-Claire-Theme: AI 및 클라우드 시스템`
2. **URL 쿼리 파라미터**: `?theme=<theme_id_or_label>`
   - 브라우저 주소창 직접 접근 및 공유 링크 방식.
   - 예: `https://claire.example.com/graph?theme=1`, `https://claire.example.com/doc/42?theme=research`
3. **폴백 (Fallback)**: 테마 헤더 및 쿼리 파라미터가 모두 생략된 경우, 기본 지식베이스인 Theme `0`으로 자동 라우팅된다.

### 비공개(Private) 테마의 Fail-Closed 보안
- 테마의 가시성이 `is_public: false`로 설정된 경우, 인증되지 않은 익명 사용자가 해당 테마로 접근하면 보안상 테마의 존재 자체를 은닉하기 위해 `403 Forbidden` 대신 **`404 Not Found`**를 반환한다.
- `owner`, `collaborator`(허용된 경우), `readonly` 세션을 지닌 사용자에게만 테마 메타데이터와 지식그래프가 노출된다.

### Web UI 상태 동기화
- 웹 인터페이스 우상단 테마 선택기(Theme Selector)에서 사용자가 선택한 테마는 브라우저 `localStorage`(`claire_selected_theme`)에 저장된다.
- 페이지 전환 시 브라우저 히스토리(`history.pushState`) 및 URL `?theme=` 파라미터와 양방향 동기화되어 새로고침 후에도 선택 테마가 유지된다.

---

## MCP (Model Context Protocol) 연동
 
 외부 LLM 클라이언트(Claude Desktop, Cursor 등)에서 지식베이스를 조회할 수 있도록 `/mcp` HTTP 엔드포인트를 제공한다.
 
 - **엔드포인트**: `POST /mcp` (Streamable / Stateless HTTP JSON-RPC)
 - **인증**:
   - `Authorization: Bearer <PERSISTENT_TOKEN>` (CLI `claire token create`, WebUI [🔑 API/MCP 키], 텔레그램 `/token` 발급 영구 토큰)
   - `X-Session: <session_token>` 또는 `Authorization: Bearer <session_token>` (텔레그램 `/web`, `/webco`, `/webro` 발급 세션)
   - 미인증 또는 무효 토큰 요청 시 표준 MCP HTTP 사양(RFC 6750)에 따라 `401 Unauthorized` (`WWW-Authenticate: Bearer` 헤더) 반환
 - **제공 툴셋 (10종 읽기 전용)**: `resolve_entity`, `search`, `neighbors`, `path`, `context`, `overview`, `node`, `documents`, `document`, `stats`
 
 ### Claude Desktop 설정 (`claude_desktop_config.json`)
 
 ```json
 {
   "mcpServers": {
     "claire": {
       "url": "https://claire.example.com/mcp",
       "headers": {
         "Authorization": "Bearer <PERSISTENT_TOKEN>"
       }
     }
   }
 }
 ```
 
 ### Cursor 설정 (`.cursor/mcp.json`)
 
 ```json
 {
   "mcpServers": {
     "claire": {
       "url": "https://claire.example.com/mcp",
       "headers": {
         "Authorization": "Bearer <PERSISTENT_TOKEN>"
       }
     }
   }
 }
 ```
 
 ### Google Antigravity 설정 (`~/.gemini/config/mcp_config.json` 또는 `.agents/mcp_config.json`)
 
 ```json
 {
   "mcpServers": {
     "claire-bible": {
       "serverUrl": "https://claire.example.com/mcp",
       "headers": {
         "Authorization": "Bearer <PERSISTENT_TOKEN>"
       }
     }
   }
 }
 ```
 
 자세한 REST API 엔드포인트 호출 및 MCP 툴셋/OAuth 2.1 연동 상세 가이드는 [API_AND_MCP_GUIDE.md](API_AND_MCP_GUIDE.md)와 [MCP_SUPPORT.md](../design/MCP_SUPPORT.md)를 참고한다.
 
## Support Bundle (RCA 및 진단 번들)

시스템 상태, 프로바이더 텔레메트리, 인박스 실패 내역 및 특정 문서 추적 정보를 zstd 압축 아카이브(`.tar.zst`)로 안전하게 제공하는 엔드포인트이다.

- **생성 엔드포인트**: `POST /support/bundle`
  - **인증**: `Authorization: Bearer <OWNER_TOKEN>` (owner 전용)
  - **본문**: `{"days": 1, "target": "<optional_share_url_or_doc_id>"}`
  - **응답**: `{"bundle_id": "...", "token": "...", "expires_at": "...", "download_url": "..."}`
- **다운로드 엔드포인트**: `GET /support/bundle?token=<token>`
  - **인증**: `public` (추측 불가능한 6시간 보안 토큰 쿼리 파라미터 기반)
  - **응답 헤더**: `Content-Type: application/zstd`, `Content-Disposition: attachment; filename="..."`
  - **수명주기**: 생성 후 6시간이 지나면 파일 및 레코드가 자동 파기되며 `410 Gone`을 반환한다. 자세한 내용은 [TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md](../design/TELEMETRY_AND_SUPPORT_BUNDLE_DESIGN.md)를 참고한다.

## 서브도메인 역할 호스트와 레거시 호스트 308 리다이렉트

P2부터 단일 FQDN 및 3대 역할 호스트(`app`, `usercontent`, `media`) 체계로 전환되었다. 개별 테마 전용 FQDN 입력은 폐지되었으며, 기존에 등록되었던 테마 FQDN은 `legacy_hosts` 테이블을 통해 정본 URI 경로로 308 Permanent Redirect된다.

### 역할 호스트 아키텍처
1. **역할 분리**:
   - `app`: 메인 웹 애플리케이션 및 API (`CLAIRE_FQDN` 기준). 세션 쿠키(`__Host-claire_session`)를 보유한다.
   - `usercontent`: 샌드박스 HTML 렌더링 origin (`CLAIRE_USERCONTENT_FQDN`). 쿠키가 없는 격리 도메인이다.
   - `media`: 미디어 스트리밍 origin (`CLAIRE_MEDIA_FQDN`). 1시간 열람 capability 서명 URL 기반.
   - `prototype`: 관리자용 정적 시각 프로토타입 origin (RD20). 환경 변수나 설정이 없다. 이름은 코드가 `CLAIRE_FQDN`에서 계산한다(`src/claire/api/prototype_host.py`의 `prototype_authority`): 운영은 `prototype.` + `CLAIRE_FQDN`의 등록 도메인이다. 등록 도메인은 `publicsuffixlist` 패키지에 든 Public Suffix List 사본으로 구한다(실행 중 목록을 내려받지 않는다). 예: `cb.netspheres.org` → `prototype.netspheres.org`, `claire.example.co.kr` → `prototype.example.co.kr`. `CLAIRE_FQDN`이 IP 주소, `localhost`, 한 레이블이거나 등록 도메인이 없으면 이 역할이 없다. 계산된 이름이 다른 역할 호스트와 같아도 없다. 개발은 app IPv4의 고정 포트 8769다(`DEV_PROTOTYPE_PORT`, 예: `http://127.0.0.1:8769/`). `GET`·`HEAD /`만 목업 데이터 페이지(`src/claire/templates/prototype/federation_client_prototype.html`)를 주고 그 밖의 모든 경로와 메서드는 404다. 쿠키를 읽지도 쓰지도 않고, API 경로와 서버 데이터가 없다. 응답 헤더: `X-Robots-Tag: noindex, nofollow`, `X-Frame-Options: DENY`, `Content-Security-Policy`(`default-src 'none'`, inline script·style만 허용, `connect-src 'none'`, `frame-ancestors 'none'`). 계산된 이름이 아닌 호스트는 전처럼 421이다.
2. **도메인 설정**:
   - 운영 환경에서는 모든 역할 호스트가 등록 도메인(`CLAIRE_SITE_DOMAIN`)의 서브도메인이어야 한다. 계산된 `prototype` 호스트는 이 검사에 들지 않는다.
   - 개발 환경에서는 `CB_USERCONTENT_PORT`, `CB_MEDIA_PORT` 포트 기반 authority로 분리된다. `prototype`은 고정 포트 8769이고, `docker-compose.dev.yml`이 이 포트를 같은 컨테이너 포트로 연결한다.
   - `usercontent`·`media`·`prototype` origin은 `CLAIRE_CORS_ALLOWED_ORIGINS`에 넣을 수 없다(기동 거부).
3. **HostRouter 라우팅**:
   - `HostRouter`가 인입된 `Host` 헤더를 보고 역할을 판별하여 적절한 하위 앱으로 분기한다.
   - 등록되지 않은 호스트는 `421 Misdirected Request`로 거부된다.
4. **프로토타입 호스트 DNS·proxy 규칙(운영)**: 운영자가 DNS에 `prototype.<CLAIRE_FQDN의 등록 도메인>` 레코드(예: `prototype.netspheres.org`)를 proxy로 추가하고, TLS 인증서가 그 이름을 덮게 한다. 위 Nginx 예시와 같은 upstream으로 보내는 server block을 하나 더 둔다. `server_name`과 `proxy_set_header Host`는 그 호스트 이름이다. Claire 설정은 바꾸지 않는다.
   ```nginx
   server {
       listen 443 ssl;
       server_name prototype.example.com;
       access_log /var/log/nginx/claire_access.log claire_safe;
       client_max_body_size 1k;
       location / {
           limit_except GET { deny all; }
           proxy_pass http://claire_backend;
           proxy_http_version 1.1;
           proxy_set_header Host prototype.example.com;
           proxy_set_header Connection "";
           proxy_set_header Cookie "";
           proxy_set_header Forwarded "";
           proxy_set_header X-Forwarded-For "";
           proxy_set_header X-Forwarded-Host "";
           proxy_set_header X-Forwarded-Proto "";
           proxy_set_header Referer "";
       }
   }
   ```

### 레거시 테마 FQDN 일몰 (Sunset) 정책
1. **정본 경로 308 리다이렉트**:
   - v16 이관 시 기존 `themes.fqdn` 값이 `legacy_hosts` 테이블로 보존된다.
   - 해당 FQDN으로 들어온 모든 요청은 정본 경로(`/themes/{number}/...` 또는 `/@{username}/themes/{number}/...`)로 `308 Permanent Redirect`된다.
2. **일몰 기산 및 시계 관리**:
   - 이관 초기에는 일몰 시계가 정지 상태(`sunset_at = NULL`)로 유지된다.
   - 관리자가 `/admin/instance` 화면이나 `/api/admin/instance/legacy-hosts/sunset` API를 통해 이관을 활성화하면 기본 30일 일몰 시계가 기산된다.
   - 관리자는 일몰 기간을 연장하거나 무기한 `308` 유지(`indefinitely = 1`)를 설정할 수 있다.
3. **일몰 만료 후 처리**:
   - 일몰 시각이 지난 레거시 호스트 요청은 `410 Gone`을 반환한다 (테마 완전 소각 시 `421 Misdirected Request`).

## Cloudflare 공인 IP 대역 제한 (`CLAIRE_CLOUDFLARE_IPS_ONLY`)

외부 공격자가 도메인을 거치지 않고 서버의 공인 IP로 직접 접속하는 행위를 방지하기 위해, Cloudflare 프록시를 사용하는 환경에서 공인 대역 유입 트래픽을 Cloudflare 공식 IP로만 제한할 수 있다.

- **설정**: `CLAIRE_CLOUDFLARE_IPS_ONLY=1` (기본값: 0)
- **동작 방식**:
  - [Cloudflare 공식 IP 대역 목록](https://www.cloudflare.com/ko-kr/ips/) (IPv4 15개 CIDR, IPv6 7개 CIDR)을 메모리에 탑재.
  - 들어오는 요청의 피어 IP가 공인 대역(`is_global == True`)인 경우, Cloudflare 대역에 속하지 않으면 즉시 **`403 Forbidden`** ("Direct public IP access is not allowed")으로 차단.
  - **사설 대역 예외**: 사설망(10.x, 172.16-31.x, 192.168.x), 로컬 루프백(127.0.0.1, ::1), 도커 브릿지 네트워크 등 비공인 대역 IP는 필터링 대상이 아니므로 정상 통과.

자세한 아키텍처 및 설정 가이드는 [THEME_FQDN_REVERSE_PROXY_DESIGN.md](../design/THEME_FQDN_REVERSE_PROXY_DESIGN.md)를 참고한다.

## 로그인 실패 제한의 클라이언트 IP (`CLAIRE_TRUSTED_PROXIES`)

로그인·재인증·비밀번호 변경의 실패 제한은 `(username, IP)`와 `(IP)`를 키로 쓴다([ACCOUNTS.md](ACCOUNTS.md) §7). reverse proxy 뒤에서는 피어 주소가 모두 proxy 주소라 `(IP)` 키 하나를 모든 사용자가 나눠 쓰게 된다.

- **[코드] 판정**(`src/claire/api/security.py`의 `client_ip`): 바로 앞 피어가 `CLAIRE_TRUSTED_PROXIES`(쉼표 구분 CIDR)에 있을 때만 `X-Forwarded-For`를 오른쪽부터 읽어 신뢰 목록에 없는 첫 주소를 쓴다. 그보다 왼쪽 값은 클라이언트가 넣을 수 있으므로 보지 않는다. `CLAIRE_CLOUDFLARE_IPS_ONLY=1`이면 Cloudflare 대역도 신뢰 목록에 들어간다.
- **[코드] 피어 주소를 쓰는 경우**: 목록이 비었을 때(기본값), 피어가 목록 밖일 때, `X-Forwarded-For`가 없거나 여러 줄이거나 항목 하나라도 IP 주소가 아닐 때(포트, 빈 항목, `unknown` 등). 헤더를 추측해 고치지 않는다.
- **[코드]** 잘못된 CIDR(`10.0.0.1/8`처럼 호스트 비트가 켜진 값 포함)이나 빈 항목은 기동 실패다. 단일 주소는 `/32`·`/128`로 쓴다.
- **[코드]** 접근 로그의 IP 표기, Host·scheme 판정, `CLAIRE_CLOUDFLARE_IPS_ONLY` 필터는 이 값과 관계없이 피어 주소를 쓴다.
- 위 Nginx 예시는 `X-Forwarded-For`를 비워 보내므로 그대로 두면 피어 주소(proxy)를 쓴다. 실제 클라이언트 IP로 세려면 proxy에서 `proxy_set_header X-Forwarded-For $remote_addr;`(또는 `$proxy_add_x_forwarded_for`)로 바꾸고 proxy의 고정 LAN IP를 `CLAIRE_TRUSTED_PROXIES`에 넣는다. Claire host firewall이 그 proxy만 upstream port에 닿게 해야 한다(위 Production 절).

## Media 호스트와 CDN 캐싱 정책 (P7)

미디어 파일(이미지, 첨부, 동영상, 프레젠테이션 슬라이드 에셋 등)의 안전하고 격리된 전송을 위해 독립된 `media` 역할 호스트(`CLAIRE_MEDIA_FQDN`)와 `MediaStore`가 도입되었다.

### 1. 스토리지 백엔드 및 불투명 객체 키
- **스토리지 백엔드**: 로컬 파일시스템(`LocalMediaStore`, 기본 경로 `data/media`) 및 S3 호환 오브젝트 스토리지(`S3MediaStore`, AWS S3, Cloudflare R2, MinIO, GCS 지원) 두 가지를 지원한다.
- **불투명 객체 키 (S16)**:
  - 객체 키는 `HMAC(derived_key, f"{account_id}:{sha256(content)}").hexdigest() + ext` 알고리즘으로 파생된다.
  - 키에 `username`, `theme_id`, `doc_id`가 포함되지 않아 싱글 모드의 익명성을 유지하며, 다른 계정이 동일 파일을 보유하고 있는지 추측할 수 없다.
  - 계정 간 중복 제거는 보안상 수행하지 않는다.

### 2. 캐시 및 전송 규칙
- **공개 테마 미디어**:
  - 서명 없는 영구 URL로 제공된다 (`/media/{key}`).
  - 응답 헤더: `Cache-Control: public, max-age=31536000, immutable`.
  - 콘텐츠 주소화 기반이므로 영구 불변 캐싱이 성립한다.
- **비공개 미디어 (Capability 서명 URL)**:
  - 1시간 열람 단위 capability 서명 URL이 강제된다 (`/media/{key}?account_id=...&theme_id=...&visibility_epoch=...&token={exp}.{sig}`).
  - HMAC 서명은 `(account_id, theme_id, visibility_epoch, key, exp)`에 바인딩된다.
  - 응답 헤더: `Cache-Control: private, no-store`.
  - 미디어 스트리밍 및 탐색을 위한 HTTP `Range` 요청(RFC 7233/9110, `206 Partial Content`)을 전면 지원하며 Range 요청 시에도 서명이 유효하게 유지된다.
- **무쿠키 및 CORS**:
  - `media` 호스트는 쿠키를 읽지도 쓰지도 않는다 (`Cookie` 헤더 폐기, `Set-Cookie` 금지).
  - 샌드박스 렌더링 origin(`usercontent`)의 불투명 서브리소스 요청을 지원하기 위해 `Access-Control-Allow-Origin: *` 헤더를 제공한다.

### 3. 무효화 및 수명주기 훅
- 테마의 공개 상태가 비공개로 전환되거나 문서가 소각(purge)될 때:
  1. 테마의 `visibility_epoch`가 즉시 증가한다.
  2. 기존에 발급된 비공개 capability URL은 유효기간 만료 전이라도 즉시 무효화(403)된다.
  3. `register_cdn_invalidator` 훅을 통해 등록된 CDN/프록시 캐시 무효화가 원자적으로 발화된다.

## 적용 확인

development에서는 설정한 IPv4 URL로 직접 접속하고 다른 interface에 port가 게시되지 않았는지 확인한다. production에서는 다음을 각각 확인한다.

1. 올바른 hostname을 통한 HTTPS 요청은 성공한다.
2. 잘못된 Host는 proxy 또는 Claire Bible에서 거부된다.
3. proxy host에서는 Claire Bible HTTP upstream에 접속할 수 있다.
4. proxy 이외의 LAN host에서는 firewall 때문에 같은 upstream port에 접속할 수 없다.
5. 긴 NDJSON 응답이 proxy buffering 없이 순차 전달된다.
6. Claire Bible과 proxy access log에 query string과 인증 정보가 남지 않는다.
7. 레거시 테마 FQDN 접근 시 정규 네임스페이스 경로로 308 리다이렉트되며, 일몰 도래 시 410 Gone으로 응답한다.
8. `CLAIRE_CLOUDFLARE_IPS_ONLY=1` 설정 시 비-Cloudflare 공인 IP 직접 접근이 403으로 차단되고 LAN/사설 접근은 유지된다.
9. Fedify 사이드카를 쓰면 `/.well-known/webfinger`·`/.well-known/nodeinfo`·`/nodeinfo/`·`/ap/`만 사이드카로 가고, `/.well-known/oauth-authorization-server`는 계속 Claire의 메타데이터를 돌려준다(준비 검사 GF6, GF7).
 