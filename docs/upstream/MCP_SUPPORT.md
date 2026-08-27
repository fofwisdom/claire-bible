# MCP 지원 — 설계안 (M1 구현·배포 완료)

작성일: 2026-08-15 · 상태: **M1 배포됨**(`feature/mcp-support` → master 병합,
`claire.blackan.net`에서 실행 중, 원격 실그래프로 검증 완료) · M2(쓰기 툴)는
별도 계획 필요, §12 잔여 결정사항 일부 미해결

> 목적: 축적된 지식(그래프/문서)을 **읽고 쓰는 경로를 다양화** — 텔레그램/웹 UI 외에
> Claude Code·Claude Desktop·기타 MCP 하네스(hermes 등)에서도 접근. **v1은 read
> 전용**(적재/종합/중복정리 등 쓰기는 범위 밖, 나중 마일스톤). 기존 owner/readonly
> 토큰 권한 체계를 MCP에도 그대로 적용 — **토큰 없으면 MCP·사이트 존재 자체를
> 모르게**(기존 게이트의 404 관례 유지), 토큰 있으면 권한에 맞는 툴만 노출.

---

## 1. 현재 상태 (baseline)

### 1.1 인증/게이트 (`src/claire/api/server.py`)
- **3가지 인증 경로**: ① bearer `CLAIRE_INJECT_TOKEN`(owner, 전체 권한) ② bearer
  `CLAIRE_READONLY_TOKEN`(GET 성격 조회만, 헤더 전용) ③ 세션(쿠키 **또는
  `X-Session` 헤더** — `server.py:59`, `:83`에서 둘 다 동등하게 인정, 브라우저
  전용이 아니다. **MCP는 이 헤더 경로에 의존**하므로 "안 쓰는 쿠키 대체 코드"로
  치워지면 안 됨 — §1.3/§5 참고). `/web`·`/webro`가 발급, scope owner|readonly.
- **`gate` 미들웨어**(server.py:810)가 유일한 인가 경계 — `PUBLIC_PATHS`(`/health`,
  `/p`, `/image`)와 `READONLY_PATHS`(그래프·검색·노드·문서 조회) 화이트리스트 밖은
  인증 실패 시 **401이 아니라 404**(server.py:799 "존재 숨김"). 핸들러는 이 경계를
  신뢰하고 자체 재검증하지 않음(server.py:806 명시).
- `_token_matches`는 `hmac.compare_digest` + **빈 문자열이면 무조건 거부**(fail-closed)
  — `CLAIRE_READONLY_TOKEN` 미설정이면 그 경로는 항상 실패.

### 1.2 실제 배포 상태 (이번 세션 원격 확인, 문서와 어긋남 발견)
- `docker-compose.yml`은 `claire_api`를 `127.0.0.1:8765`로만 바인드(변경 없음).
- 하지만 원격 호스트에 **시스템 nginx가 `claire.blackan.net`을 이미 리버스프록시
  중**(`/etc/nginx/sites-enabled/claire.blackan.net` → `proxy_pass
  http://127.0.0.1:8765`), Cloudflare 뒤에서 이미 공개 운영 중. `docs/EXTERNAL_ACCESS.md`
  (2026-06-11 작성)는 "외부노출 미실행/설계만"이라 되어 있어 **문서가 현재 상태를
  반영하지 못함**(별도 정정 필요, §12 잔여 결정사항 참고).
- nginx는 Cloudflare IP 대역만 origin 접근을 허용(`geo`+`return 403`)하지만, 설정
  주석의 "인증=Cloudflare Access(엣지)"는 **외부 curl 검증 결과 실제로 걸려있지
  않음** — `Authorization: Bearer <임의값>`으로 무인증 조회 경로(`/stats`)를 찔러도
  Cloudflare 로그인 페이지가 아니라 **앱 자체의 404**(보안헤더까지 앱 것과 일치)가
  그대로 응답됨. 즉 **앱 레벨 토큰 게이트가 사실상 유일한 방어선**이다.
- 원격 `.env`: `CLAIRE_INJECT_TOKEN`은 설정됨, `CLAIRE_READONLY_TOKEN`은
  미설정. **이번 설계에서는 이 정적 토큰 경로를 쓰지 않기로 결정**(§5) — 새로
  설정할 필요 없음, 확인만 해두고 넘어간다.

### 1.3 텔레그램 동적 세션 발급 (`/web`·`/webro`) — MCP 인증의 기반

`telegram_bot.py:430`(`on_web`)·`telegram_bot.py:456`(`on_webro`)이 이미
**owner/readonly 둘 다 텔레그램으로 기한제 토큰을 동적 발급**한다:
- `/web` → `dbm.create_session(conn)`(scope="owner") → `?t=<token>` 링크.
- `/webro` → `dbm.create_session(conn, scope="readonly")` → 읽기전용 링크.
- `db.py:905` `create_session`: **scope별 단일 활성 세션** —
  `DELETE FROM auth_sessions WHERE scope=?` 를 발급 시마다 먼저 실행. 접속마다
  7일 슬라이딩 연장(`db.py:925` `validate_session`). 즉 같은 scope의 이전
  토큰은 재발급 즉시 전부 무효화되고, owner/readonly 두 scope는 서로 독립.
- 게이트(`server.py:52` `_authed`, `:82` `_session_scope_ok`)는 이 토큰을
  **쿠키뿐 아니라 `X-Session` 헤더로도** 인정한다 — 브라우저 전용이 아니다.

**사용자 결정(2026-08-15)**: 정적 비밀값(`CLAIRE_READONLY_TOKEN`) 신설이나 세션을
다중 슬롯으로 넓히는 방향은 **격리 수준을 낮추는 변경이라 배제**. MCP는 이
기존 기한제 단일슬롯 세션을 그대로 재사용한다(§5). "MCP용으로 쓰다가 `/webro`를
다시 치면 그 MCP 연결이 끊긴다"는 트레이드오프는 고치지 않고 그대로 감수한다.

### 1.4 재사용 가능한 읽기 함수 (그대로 호출 가능)
`graphview.py`의 `graph_json`, `documents_list`, `node_detail`, `document_detail`.
MCP 툴은 이 함수들을 **재사용**하고 재구현하지 않는다.

**함정(구현 중 실측 발견)**: `IngestService.search`(→`retrieval.query.search`)는
`summarize=False`를 줘도 하이브리드(FTS+벡터) 융합을 위해 `provider.embed(query)`를
**무조건 호출**한다(retrieval/query.py:78) — Gemini 임베딩 API 호출이 매 검색마다
발생한다는 뜻. MCP `search` 툴은 그래서 이 함수를 재사용하지 **않고**
`db.fts_search`+`db.get_entity`만 직접 써서 Gemini 호출 0을 보장한다(§6/§7). MCP
바깥에서도 "summarize=False면 비용 없는 검색"이라고 오해하면 안 됨 — 이 발견은
MCP 범위를 넘는 일반적인 함정이라 여기 기록해둔다.

---

## 2. 핵심 설계 크럭스 — gate의 경로 화이트리스트는 "툴별 스코프"를 표현 못 함

§1.1의 불변식("핸들러는 게이트를 신뢰, 자체 재검증 안 함")은 **경로 = 권한 단위**를
전제한다. MCP는 반대다 — 엔드포인트는 `/mcp` 하나뿐이고, 읽기/쓰기 구분은 경로가
아니라 JSON-RPC 바디의 `tools/call` **메서드 인자**로 정해진다. 따라서:

- `/mcp`를 `READONLY_PATHS`에 넣지 않으면 readonly 토큰은 그냥 404 — MCP 요구사항의
  readonly 절반이 죽는다.
- 넣으면 readonly 토큰이 디스패처까지는 도달 — **디스패처 자체가 2차 인가 경계가
  되어야 한다**(제3의 선택지 없음).

**결론(설계 확정)**: `/mcp`를 `READONLY_PATHS`에 추가하되(게이트는 무토큰 요청을
여전히 404로 막음), MCP 디스패처 내부에서:
1. 요청의 `X-Session` 헤더 값을 `dbm.validate_session(scopes=("owner","readonly"))`으로
   판정해 owner/readonly 스코프를 얻는다(`_scope_of(request)`, §1.3 — MCP 클라이언트
   설정에 `/web`·`/webro`로 받은 토큰을 `X-Session` 헤더 값으로 그대로 넣는다).
2. `tools/list` 응답을 **스코프별로 필터링**(readonly 클라이언트는 쓰기 툴 이름
   자체를 못 봄 — graphview.py의 기존 "안 그림" 패턴과 동일 원칙, "존재도 모르게").
3. `tools/call`은 스코프 테이블 기준 **default-deny**(모르는 툴 이름/스코프
   불일치는 즉시 거부, 실제 실행 전).
v1은 read 전용 툴만 존재하므로 owner/readonly 토큰 둘 다 같은 툴 세트를 본다 —
스코프 테이블 구조는 지금 만들어두고, 쓰기 툴이 생기는 다음 마일스톤에서 실제로
갈라진다.

---

## 3. 아키텍처 옵션 비교

| | **A. 같은 프로세스에 임베드**(권장) | B. 별도 컨테이너 + nginx 서브패스 |
|---|---|---|
| 실행 위치 | 기존 `claire_api`(aiohttp, 8765) 안에 `/mcp` 라우트 추가 | 새 컨테이너(`claire_mcp`), nginx `location /mcp { proxy_pass ... }` |
| 인증 경계 | 기존 `gate` 미들웨어 그대로 재사용(1곳) | 별도 인증 로직 필요(2곳, 드리프트 위험) |
| "무토큰 = 존재 안 보임" | 유지됨 — 게이트가 여전히 유일한 관문 | **깨짐** — 컨테이너 재시작/장애 시 nginx가 **502/504**를 반환해 무인증 프로버에게 "여기 뭔가 있다"를 노출(존재은폐는 개별 앱이 아니라 엣지 전체의 속성) |
| 토큰/설정 | `get_settings()` 그대로 공유 | `.env` 별도 로드(동일 값이지만 별 프로세스) |
| DB 접근 | 기존 커넥션 패턴 그대로 | 새 볼륨 마운트 필요(기존 backup/expand 컨테이너와 동일 패턴이라 어렵진 않음) |
| 컨테이너 수 | 6개 유지 | 7개 |
| 구현 난이도 | aiohttp↔ASGI 어댑터(scope/receive/send shim) 필요 — 아래 §4 | SDK의 기본 Starlette 앱을 그대로 씀(어댑터 불필요) |

**권장: A.** 사용자가 명시한 "재사용" 요구와 "토큰 없으면 존재도 모르게" 요구
둘 다 A에서만 동시에 성립한다. B를 택하면 최소한 `error_page 502 504 =
@notfound`(균일 404)와 Starlette 쪽에도 별도 인증 미들웨어 이중화가 필요해져
"재사용"의 이점이 상당 부분 사라진다.

---

## 4. 프로토콜 구현 — 공식 SDK 재사용 (hand-roll 아님)

사용자가 "다른 하네스(hermes 등)에서도 쓸 거야" + "최신 표준에 가깝게"를 요구 →
JSON-RPC/세션ID/SSE 협상을 직접 구현하는 hand-roll은 스펙 드리프트 위험이 커서
기각. 공식 Python SDK(`mcp`, 이번 조사에서 로컬 설치·API 확인, **v2.0.0**,
`LATEST_PROTOCOL_VERSION = 2026-07-28`, `requires-python >=3.10` — 현재 프로젝트
플로어와 호환)를 그대로 쓴다.

- **실제로 돌려서 확인함**(스크래치 venv, `mcp==2.0.0`): 데코레이터 기반 상위
  API는 `mcp.server.lowlevel.Server`가 아니라 **`mcp.server.mcpserver.MCPServer`**
  (구 FastMCP가 이 이름으로 이식됨) — `@mcp.tool()`로 일반 Python 함수를 그대로
  툴로 등록하면 pydantic으로 `inputSchema`가 자동 생성된다. `mcp.streamable_http_app
  (json_response=True, stateless_http=True, transport_security=...)`가 **완성된
  Starlette ASGI 앱**을 반환 — 툴 등록·JSON-RPC 디스패치·capability 협상·에러코드는
  전부 SDK가 담당.
- httpx의 `ASGITransport`로 이 Starlette 앱을 실제 소켓 없이 구동해
  `initialize`→`tools/list`→`tools/call` 왕복을 실행했다 — 아래 §4.1이 그 실제
  캡처(가짜/추정 아님).
- **transport_security 주의(구현 시 필수)**: `TransportSecuritySettings`의
  `allowed_hosts` 기본값이 **빈 리스트**라 DNS-rebinding 방지가 기본으로 *모든*
  Host 헤더를 421로 거부한다(로컬 테스트에서 실제로 걸림). 배포 시
  `allowed_hosts=["claire.blackan.net"]`(및 로컬 검증용 `localhost`)을 명시해야
  함 — 안 하면 nginx를 정상 통과한 요청도 SDK 레벨에서 421.
- **구현 완료·검증됨(2026-08-15, 더 이상 가정 아님)**: `MCPServer.
  streamable_http_app()`이 돌려주는 표준 ASGI 콜러블(`app(scope, receive,
  send)`)을, aiohttp의 `web.Request`↔ASGI `scope`/`receive`/`send` 변환 어댑터
  (`server.py`의 `mcp_route`)로 감싸 실제로 붙였다. lifespan(시작 시
  `StreamableHTTPSessionManager`의 내부 태스크그룹을 띄우는 부분)은
  `app.on_startup`/`on_cleanup`에 연결. **로컬(합성 그래프+curl+공식 SDK
  클라이언트)과 원격 프로덕션(`claire.blackan.net`, 실제 그래프로
  `overview` 등 호출) 양쪽에서 end-to-end 검증 완료** — §3 권장안(A)이
  가정이 아니라 배포된 사실이 됨.
- GET `/mcp`(서버 개시 스트림)는 v1 불필요 — POST만 등록. **실측 정정**: 무인증
  GET은 게이트가 먼저 걸러 여전히 404(존재 은폐 유지). 유효 세션을 **가진**
  클라이언트가 실수로 GET을 보내면 aiohttp 라우터가 "경로는 있는데 메서드가
  없다"고 405를 준다(공격자는 세션이 없어 여기 도달 못 하므로 존재-은폐와
  무관 — 원래 "미등록 경로는 항상 404"라고 썼던 건 부정확했음, 인증 여부에
  따라 갈린다).
- **호환성 리스크 — 구현 완료 후 실제 검증함(더 이상 미검증 아님)**: 공식
  SDK의 **진짜 클라이언트**(`mcp.client.streamable_http.streamable_http_client`
  + `mcp.client.session.ClientSession`, 내가 직접 짠 httpx JSON-RPC가 아니라
  SDK 자체 핸드셰이크 로직)로 로컬 `serve-api`에 접속해 `initialize()` →
  `list_tools()` → `call_tool()` 전체 왕복 확인: `stateless_http=True`라
  `Mcp-Session-Id`가 안 내려와도 **클라이언트가 문제없이 진행**함(에러도
  경고도 없음), 10개 툴 정상 인식, 툴 호출도 정상 응답. hermes 등 제3의
  하네스까지 보장하진 못하지만(스펙 준수 클라이언트라면 문제 없어야 한다는
  스펙 문구와 공식 SDK 클라이언트의 실제 동작이 일치함을 확인) — 최소한
  "공식 SDK가 스스로 문제 삼지 않는다"는 가장 강한 신뢰 신호는 확보. 문제가
  발견되면 `stateless_http=False`로 전환할 여지는 남겨둠.

### 4.1 실제 왕복 캡처 (httpx.ASGITransport, 실행 결과 그대로)

한 엔드포인트(`POST /mcp`)로 JSON-RPC 2.0 메서드만 바뀌며 오간다. 실제 실행
결과(스크래치 venv, 진짜 서버 응답 — 손으로 지어낸 예시 아님):

```
POST /mcp  method=initialize
요청: {"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2026-07-28","capabilities":{},
                  "clientInfo":{"name":"demo-client","version":"0.1"}}}
응답 200 application/json:
{"jsonrpc":"2.0","id":1,"result":{
  "capabilities":{"experimental":{},"prompts":{"listChanged":false},
                   "resources":{"listChanged":false,"subscribe":false},
                   "tools":{"listChanged":false}},
  "protocolVersion":"2025-11-25",
  "serverInfo":{"name":"claire","version":"0.1.0"}}}

POST /mcp  method=notifications/initialized   (알림, id 없음) → 202 Accepted, 빈 바디

POST /mcp  method=tools/list
응답 200: {"jsonrpc":"2.0","id":2,"result":{"tools":[
  {"name":"search","description":"claire 지식베이스 검색(FTS)...",
   "inputSchema":{"type":"object","title":"searchArguments",
                   "properties":{"query":{"title":"Query","type":"string"}},
                   "required":["query"]}},
  {"name":"node", ...}
]}}

POST /mcp  method=tools/call  params={"name":"search","arguments":{"query":"MCP"}}
응답 200: {"jsonrpc":"2.0","id":3,"result":{
  "content":[{"type":"text","text":"{\n  \"hits\": [...]\n}"}],
  "isError":false}}
```

관찰:
- 헤더는 `Content-Type: application/json`, `Accept: application/json,
  text/event-stream`만 있으면 됨. `stateless_http=True`라 `Mcp-Session-Id`
  응답 헤더가 **안 붙는다**(§4의 호환성 리스크 항목 그대로 재확인).
- `inputSchema`는 함수 시그니처에서 **pydantic이 자동 생성** — 툴마다 손으로
  JSON Schema를 안 써도 됨(파라미터 타입 힌트만 정확히 달면 됨).
- **버전 협상 이상 징후**: 클라이언트가 `protocolVersion: "2026-07-28"`
  (`mcp.types.LATEST_PROTOCOL_VERSION`과 동일 값)을 보냈는데 서버가
  `"2025-11-25"`로 응답 — SDK 내부 협상 로직이 요청값을 그대로 안 받아준다는
  뜻. **원인 미조사**(SDK 버전 자체의 특이동작일 수 있음) — M1 스파이크에서
  실제 사용할 프로토콜 버전이 뭐가 되는지 다시 확인 필요, 문서에 "2026-07-28
  사용"이라 단정하면 안 됨.
- `tools/call`의 결과는 `content: [{type:"text", text: "<JSON 문자열>"}]`
  형태 — MCP 스펙상 툴 결과는 기본적으로 텍스트/이미지/리소스 블록이지 raw
  JSON이 아니다. §6 툴들의 반환값은 **JSON을 문자열로 인코딩해 text 블록에
  담는 방식**이 된다(구조화 출력을 원하면 `structured_output=True` 옵션이
  있었음 — MCPServer init 시그니처에서 확인, M1에서 채택 여부 결정).

### 4.2 스펙 이탈 기록
MCP HTTP transport 스펙은 미인증 요청에 `401 + WWW-Authenticate`를 명시하지만,
이 프로젝트는 무토큰 요청에 **404**를 쓴다(기존 게이트 관례, §1.1). 사용자
요구("존재도 모르게")와 일치하는 의도적 이탈이며, 대신 auth discovery가 없다 —
토큰은 항상 out-of-band로 설정해야 한다(MCP 클라이언트 설정 시 헤더 수동 입력).

---

## 5. 인증 매핑

MCP는 **§1.3의 기존 세션 발급을 그대로 재사용**한다. 새 토큰 종류·새 스코프·새
DB 스키마 없음.

| 발급 경로 | 헤더 | 스코프 | 접근 |
|---|---|---|---|
| 텔레그램 `/web` | `X-Session: <owner 세션 토큰>` | owner | v1: read 툴 전체 (추후: write 툴 포함) |
| 텔레그램 `/webro` | `X-Session: <readonly 세션 토큰>` | readonly | v1: read 툴 전체 (owner와 동일 — 쓰기 툴이 생기기 전까진 구분 없음) |
| 없음 / 만료 / 무효 | — | — | `/mcp` 자체가 404 |

**사용법**: 소유자가 텔레그램에서 `/webro`(또는 owner 권한까지 필요하면 `/web`)를
치면 `https://.../?t=9v6gdp8gcxjc`(예시) 형태의 링크가 온다. **`t=` 뒤에 오는
값 전체(12자, `_short_token()` 기본 길이)**가 세션 토큰 그 자체다 — 그 전체
문자열을 MCP 클라이언트 설정의 `X-Session` 커스텀 헤더에 그대로 넣으면 된다.
**실측 확인**(로컬 in-memory DB, `dbm.create_session`→`validate_session`):
전체 12자 토큰은 `scopes=("owner","readonly")` 검증 통과, **7자 프리픽스는
실패**(`False`) — `resolve_session_prefix`의 프리픽스 관대화는 `?t=` 진입 지점
전용이고(server.py:818 "프리픽스 허용은 이 진입 지점뿐"), `X-Session` 경로가 타는
`validate_session`(db.py:938 `WHERE session_token=?`)은 **항상 전체일치만
인정**한다. 즉 (기존 웹 UI의) "토큰 7자 입력 통과" 기능(수동 URL 입력 편의,
GOALS.md 2026-06-11 기록)은 `X-Session` 헤더 설정과는 무관 — 링크의 `t=` 값
전체를 그대로 복사해야 한다.
`CLAIRE_INJECT_TOKEN`/`CLAIRE_READONLY_TOKEN` 정적 bearer 경로는 **이번
설계에서 사용하지 않는다**(§1.3 결정 — 이미 존재하는 코드 경로이므로 남겨는
두되, MCP 문서화·안내에는 등장시키지 않는다).

**받아들이는 트레이드오프**(§1.3): scope별 단일 활성 세션이라 MCP에 물려둔
토큰은 같은 scope로 `/web`·`/webro`를 다시 치는 순간 무효화된다. 별도 완화
장치(다중 세션·전용 scope 신설 등)는 격리 수준을 낮추는 변경이라 이번 설계에서
의도적으로 만들지 않는다 — 연결이 끊기면 다시 `/webro`를 쳐서 새 토큰을 받는
것이 정상 동작이다.

**추가로 인지해야 할 결과(사용자에게 명시 필요)**: `validate_session`은 **유효한
호출마다 TTL을 슬라이딩 연장**한다(db.py:943 `expires_at=now+ttl`). MCP
클라이언트가 주기적으로 툴을 호출하면(사람이 브라우저를 다시 여는 것과 달리
자동으로, 끊임없이) 세션이 사실상 **영구적으로 안 끊길 수 있다** — 사용자가
방금 요구한 "기한제 토큰"의 만료 속성이 상시 연결된 기계 클라이언트 앞에서는
무력화된다. 이건 세션 메커니즘 자체의 기존 동작(브라우저 탭을 계속 열어놔도
동일)이라 MCP만의 새 취약점은 아니지만, MCP 클라이언트는 브라우저보다 훨씬
더 자주/규칙적으로 호출할 가능성이 높아 사실상 상시 유효 토큰이 될 수 있음을
사용자가 인지하고 있어야 한다.

---

## 6. 툴 표면 (v1, read 전용)

| 툴 | 내부 함수 | 비고 |
|---|---|---|
| `search` | **`IngestService.search` 재사용 안 함** — `db.fts_search` 직접 호출(§7 참고, 구현 중 발견) | **`entity_type`/`near_ids` 필터 포함**(§11.1). raw hits만 반환, Gemini 호출 0. |
| `graph` | `graphview.graph_json` | **전체 그래프 덤프 금지** — 노드 1개 기준 N-hop 이웃 + 상한(cap)으로 스코프 축소. 정확한 hop 수/cap 값은 구현 단계에서 결정(잔여 결정사항). |
| `node` | `graphview.node_detail` | **그대로 재사용 안 됨** — 실사용 중 발견(§11.2 하단 추가 항목): 소스 문서마다 `detail`(본문 전문)을 통째로 넣어, 소스 48개짜리 허브 노드는 응답이 통째로 터진다. MCP 레이어(`node_impl`)에서 최신 10개(`MAX_NODE_DOCUMENTS`)로 캡하고, 기본은 `summary`만(전문은 `full=True`일 때만) 반환하도록 후처리. `documents_truncated`/`documents_omitted` 포함. |
| `documents` | `graphview.documents_list` | **그대로 재사용 안 됨** — 실사용 중 `limit=300` 호출이 134KB/3,100줄로 도구 응답 한도를 넘어 파일 강제저장까지 발생(§11.2). `MAX_DOCUMENTS=100` 하드캡 + `since`/`query` 필터(`db.documents_timeline`/`documents_count`에 선택적 파라미터로 추가, 웹 UI 호출부는 무필터라 하위호환 유지) + `truncated`/`omitted`로 해결. |
| `document` | `graphview.document_detail` | **부작용 제거 필요** — 웹 핸들러(`server.py:352`)는 조회 시 `set_document_seen(seen=True)`를 같이 호출해 "안읽음" 마커를 지운다. MCP 조회가 이 부작용을 상속하면 readonly 툴이 사용자의 안읽음 상태를 몰래 바꾸게 됨 — MCP 경로는 `mark_seen` 없이 `graphview.document_detail`만 호출. `fetched_at`은 ISO8601 UTC로 변환해 노출(§11.2). |
| `stats` | `dbm.counts` | 그대로 재사용 가능. |

---

## 7. Gemini/쿼터 격리 — v1은 순수 DB/FTS 읽기만

`svc.search`의 시맨틱(임베딩) 검색과 `summarize=True`는 Gemini 호출을 유발한다.
`gemini_min_interval` 스로틀은 **프로세스-로컬**이고, GOALS.md는 분산 rate-limit
상태를 명시적으로 기각했다(트랙1, circuit breaker 설계 당시 advisor가 반려) — MCP
호출자가 통제 없이 Gemini를 소비하면 트랙1에서 애써 만든 rate-limit 방어를
우회하게 된다. **v1 결정: `summarize=False` 고정, 시맨틱 검색은 defer**(FTS만).
필요해지면 별도 마일스톤에서 프로세스 간 쿼터 조율을 다시 설계.

---

## 8. 의존성/CI 영향

- `mcp==2.0.0` 추가 시 `pyproject.toml`/`uv.lock` 갱신 필요 — `scripts/ci.sh`가
  `uv lock --check`를 게이트로 걸어놔서 lock 누락은 배포 전에 저절로 걸림.
- 전이 의존성: `starlette`, `uvicorn`, `sse-starlette`, `pyjwt[crypto]`,
  `jsonschema`, `opentelemetry-api`, `python-multipart` 등이 Docker 이미지에
  새로 들어감(현재 이미지엔 전부 없음) — 이미지 크기/빌드 시간 소폭 증가.
  `uvicorn`/`starlette`의 자체 ASGI 서버 구동 부분은 **쓰지 않는다**(§4, 라이브러리
  로직만 재사용) — 그래도 패키지 전체는 딸려온다.

---

## 9. 테스트 목록 (음성 경로 포함, 구현 시 필수)

- `X-Session` 헤더 없음 → `/mcp` 404 (존재 은폐).
- 만료되었거나 존재하지 않는 세션 토큰 → 404(`validate_session`이 False 반환하는
  경로 그대로).
- `/web`으로 새 owner 세션을 발급하면 **이전 owner MCP 연결이 즉시 무효화**됨을
  확인(§5 트레이드오프 회귀 테스트 — 실수로 다중세션 허용하는 방향으로 되돌리지
  않았는지 검증).
- readonly 세션(`/webro` 발급) → `tools/list`에 쓰기 툴 이름이 **아예 없음**(v1은
  애초에 쓰기 툴이 없으니 자명하지만, 스코프 테이블 자체의 필터링 로직은 지금
  검증해둘 것 — 다음 마일스톤에서 쓰기 툴 추가 시 회귀 방지).
- owner 세션(`/web` 발급) → 위와 동일 + read 툴 전부 정상 응답.
- 알 수 없는 툴 이름으로 `tools/call` → 디스패처가 게이트가 아니라 **자체적으로**
  거부(§2의 "제3의 선택지 없음" 불변식 회귀 테스트).
- `document` 툴 호출 → DB의 `seen` 상태가 **바뀌지 않음**(§6 부작용 제거 검증).
- `search` 툴 호출 → Gemini provider가 호출되지 않음(mock provider로 호출 횟수
  0 확인, §7 검증).
- **설정 드리프트 회귀**: `/mcp`가 실수로 `READONLY_PATHS`에서 빠지면 readonly
  세션은 게이트 단계에서부터 404(디스패처까지 못 감) — 이 상태를 "정상"으로
  오인해 넘어가지 않도록, readonly 세션으로 `/mcp`가 게이트를 통과하는 것
  자체를 별도 테스트로 고정(§2의 "제3의 선택지 없음"이 실제로 배선돼 있는지
  확인, 디스패처 단 테스트와는 별개로 게이트 단도 명시적으로 검증).

---

## 10. 마일스톤

- **M1 — 완료·배포됨(2026-08-15)**: `/mcp` read 전용 10툴(resolve_entity/
  search/neighbors/path/context/overview/node/documents/document/stats).
  공식 SDK 임베드(§4). 테스트 25개(§9 목록 반영, `tests/test_mcp_tools.py`
  `tests/test_api.py`). `feature/mcp-support` → master 병합 → `claire.blackan.net`
  배포·실그래프 검증 완료.
- **M2(보류, 별도 계획 필요)**: 쓰기 툴(ingest 등) — 스코프 테이블에 owner 전용
  항목 추가. NDJSON 스트리밍 라우트(`/ingest-stream`, `/research`,
  `/synthesize/research`)는 단일 MCP 툴 응답과 형태가 안 맞음(수십 초~수 분) —
  진행 알림(MCP `notifications/progress`) vs job-id 폴링 중 방식 결정 필요.
  큰 기능이라 M1 완료·검증 후 별도 설계 문서로.

---

## 11. 에이전트 전용 툴 제안 (사람 UI 미러링을 넘어서)

§6의 5툴+stats는 기존 웹 UI 핸들러를 그대로 옮긴 것 — 사람이 그래프를 "보면서"
쓰는 조작(전체 그래프 렌더 후 눈으로 필터링, 클릭해서 1홉씩 탐색)을 그대로
에이전트에 준다. 에이전트는 화면을 안 보고 이름/질문만 들고 온다 — 그 특성에
맞는 툴을 그래프 지식창고이기 때문에 가능한 것 위주로 추가 제안한다. **전부
기존 DB 함수 재사용**(재구현 없음), **전부 read-only**(§7 Gemini 격리와 동일
원칙 — LLM 호출 없음).

| 툴 | 재사용 함수 | 왜 에이전트에 필요한가 |
|---|---|---|
| `resolve_entity(name)` | `db.find_entities_by_name_or_alias`(정확+별칭) → 없으면 `db.fts_search` fuzzy 폴백 | 에이전트는 그래프를 안 보고 **이름 문자열**만 들고 온다 — "Claude Code라는 노드가 있나?"에 답할 진입점이 지금 툴 세트엔 없음(`node`는 이미 ID를 안다는 전제). |
| `neighbors(entity_ids: str\|list[str], exclude_ids=None, limit)` | `db.neighbors`를 시드 각각에 대해 호출(합집합, `exclude_ids` 제외) + `graph_json`이 이미 계산하는 `degree`(graphview.py:30-39)를 각 이웃에 실어 반환 | `graph`(전체 덤프 금지, §6)의 실질적 대체재이자 **탐색의 기본 단위**. 단일 ID가 아니라 **리스트**를 받는 게 핵심 — 아래 §11.1. **결과 크기 상한 + 잘림 표시 필수**(§11.2 — advisor가 짚은 정답 정합성 문제). |
| `search(query, entity_type=None, near_ids=None, limit)` | `db.fts_search` + `db.get_entity`(§6과 동일 구현, `IngestService.search`는 안 씀) — `entity_type`은 `type` 컬럼 필터, `near_ids`는 FTS 히트 ∩ 지정 시드들의 이웃 집합 | §6의 기본 `search`는 전역 검색뿐이라 "지금 이 프론티어 안에서 좁혀 찾기"가 안 됨. advisor 상의 결과 추가 — 탐색 루프 3단계를 "이름만 보고 감으로 판단"에서 "구체적 질문으로 좁히기"로 바꿔줌. |
| `context(entity_ids: list, compact=False)` | `graphview.synthesis_context` — **그대로 재사용은 안 됨**, §11.2 참고 | 지금 `synthesize` 툴(LLM 필요, M2 보류)이 쓰는 재료 자체를 노출 — 여러 노드에 대해 "지금까지 알려진 것"을 구조화된 텍스트로. 탐색 루프의 **종료 단계**(먼저 좁힌 다음에 부르는 툴). |
| `overview()` | `db.entity_type_counts` + `db.source_type_counts` + `db.top_connected_entities` + `db.most_merged_entities` | **자기서술적 진입점** — 검색어를 뭘로 시작할지조차 모를 때, 첫 호출로 엔티티 타입 분포·핵심 허브·다출처 수렴 사실 파악. 사람은 그래프를 눈으로 훑으면 되지만 에이전트에겐 이게 유일한 오리엔테이션 수단. |
| `path(from_id, to_id, max_hops)` | **신규 구현 필요** — `db.neighbors` 위에 서버사이드 BFS. 클라이언트(JS)에만 있던 `graphview.py:1712` "🔗 경로" 기능과 같은 로직을 서버로 옮김 | "A와 B가 왜 연결돼있지?"는 에이전트 추론에서 흔한 질문 형태인데, 지금은 전체 그래프를 받아 스스로 경로를 찾아야 함. |

**우선순위 제안(advisor 상의 후 수정)**: `resolve_entity`(진입점) →
`neighbors`(탐색 루프의 핵심 primitive, degree+잘림표시 포함) → `search`
필터 확장 → `context`(용량 제한 포함) → `overview` → `path`(신규 로직이라
뒤로). 원래 있던 `recent_documents`는 **제외** — 문서 타임라인 필터링일
뿐 그래프 탐색이 아니고, 에이전트가 `documents` 툴 결과를 스스로 정렬/필터
해도 충분해 별도 7번째 툴을 늘릴 이유가 없다는 advisor 판단을 받아들임.

### 11.1 검색→탐색 루프 — 에이전트가 스스로 depth를 정하며 넓혀가는 패턴

서버가 "N홉을 한 번에 펼쳐서" 주는 방식(예전 `graph` 전체덤프의 축소판)이
아니라, **에이전트가 한 홉씩 보고 판단해서 다음 홉을 결정**하는 루프를
전제로 위 툴들을 설계했다 — 그래서 `neighbors`가 **시드를 리스트로** 받는다:

1. `search(query)` → FTS raw hits(엔티티 id·name·type·score).
2. `neighbors(entity_ids=[hit.id for hit in 관심있는_hits])` → 그 hits 전체의
   1홉 이웃을 **한 번의 호출로 합집합** 반환, **degree 포함**(허브인지
   말단인지 바로 구분 가능 — 이름 40개를 그냥 나열하면 노이즈지만 degree로
   정렬돼 있으면 "더 팔 가치 있는 가지"가 바로 보인다). 에이전트가 hit별로
   각각 호출할 필요 없음 — 프론티어 전체를 한 번에 넓힌다.
3. 에이전트가 새로 나온 이웃 이름/타입/degree를 보고 "이 쪽으로 더 파볼
   가치가 있나"를 스스로 판단 — 애매하면 `search(query=<구체적 후속질문>,
   near_ids=<지금 프론티어>)`로 "이 동네 안에서" 좁혀 찾을 수 있음(이름만
   보고 감으로 거르는 게 아니라 질문을 던져 좁힘). **이게 사람이 그래프를
   보고 클릭할지 말지 정하는 것과 같은 역할**을 에이전트가 함.
4. 다음 라운드: `neighbors(entity_ids=<골라낸 새 프론티어>,
   exclude_ids=<지금까지 방문한 모든 id>)` — `exclude_ids`가 없으면 이미 본
   노드가 계속 돌아와 루프가 안 끝날 수 있음(순환 그래프이므로 실제로
   발생 가능 — 필수 파라미터로 취급).
5. 충분히 좁혀졌다 싶으면 `context(entity_ids=<최종 관심 노드들>)`로
   갈무리 — observations+연결+출처요약을 한 번에 받아 최종 답변 근거로 씀.
6. 두 특정 노드 사이 "왜 연결돼있나"만 궁금하면 1~4를 생략하고 바로
   `path(from_id, to_id)`.

`overview()`는 이 루프의 **0번째 단계**로도 쓸 수 있다 — 검색어를 뭘로
시작해야 할지 모를 때 허브/타입 분포부터 보고 진입점을 잡는 용도.

이 패턴의 장점: 서버가 "몇 홉이 적당한지"를 미리 정해서 주지 않아도 되고
(§12의 `graph` hop/cap 논쟁 자체가 무의미해짐 — 상한은 `neighbors` 한 번
호출의 결과 크기에만 걸면 됨, 전체 깊이는 에이전트의 호출 횟수가 자연히
제한), 에이전트가 관련 없는 가지를 일찍 쳐낼 수 있어 컨텍스트 낭비가
적다. 전부 FTS/그래프 쿼리뿐이라 §7의 Gemini 격리 원칙과도 충돌 없음 —
탐색을 아무리 깊이/여러 번 반복해도 LLM 호출은 0.

### 11.2 정합성 주의사항 (advisor 지적, §11 "그대로 재사용" 전제를 깨는 지점)

- **`neighbors`의 상한은 "말없이 자르면" 오답 버그다.** 시드 20개 중 하나가
  허브면 한 번의 호출이 수백 엣지를 반환할 수 있어 결과 크기 상한이
  필요한데, 그냥 앞에서 N개 잘라 돌려주면 에이전트는 "이웃이 이게 전부"라고
  확신해버린다(실제로는 300개 중 50개만 본 것). **응답에 `truncated: bool`,
  `omitted: N`을 반드시 포함** — 성능 문제가 아니라 정답 정합성 문제.
- **`context`는 "함수 그대로 재사용" 원칙이 여기서만 안 통한다.**
  `graphview.synthesis_context`(graphview.py:162)는 사람이 손으로 고른 소수
  노드(패널에서 클릭해 모은 몇 개)를 전제로 각 엔티티의 observations
  전체·관계 최대 12개·소스문서마다 `latest_extraction_summary`를 전부
  인라인한다. 에이전트가 방금 프론티어 확장으로 얻은 30개 ID를 그대로
  넘기면 그 함수 하나가 에이전트 자신의 컨텍스트 윈도우를 태워버릴 수
  있다. **엔티티 개수 상한(~10개 안팎) 또는 `compact=True`(출처요약 생략)
  옵션 필요** — §6/§11 다른 툴들과 달리 이건 "그대로 갖다 쓰면 끝"이
  아니라는 걸 명시해둔다.
- **툴 `description` 문자열이 탐색 순서 자체를 알려줘야 한다.** 에이전트는
  이 툴들을 눈으로 안 보고 이름·설명만으로 판단한다 — `resolve_entity`가
  `neighbors`보다 먼저 와야 한다는 것, `context`가 루프의 종료 단계라는
  것을 함수명만으로는 못 알아챈다. 구현 시 각 툴의 `description`에 "언제
  쓰는지"(예: `context`: "충분히 좁힌 후 마지막에 호출 — 넓은 ID 집합에는
  쓰지 말 것")까지 적어야 한다(§4.1에서 확인했듯 `description`은 그대로
  `tools/list` 응답에 실린다 — 에이전트가 보는 유일한 안내문).
- **`documents`/`node`도 "그대로 재사용" 원칙이 안 통했다** — 배포 후 실제
  MCP 클라이언트로 붙어 써보다 발견(2026-08-15). `documents(limit=300)`이
  134KB/3,100줄로 도구 응답 한도를 넘겨 파일 강제저장이 두 번 발생했고,
  소스 48개짜리 허브 노드에 `node()`를 부르면 본문 전문이 통째로 딸려오는
  걸 실측으로 확인(AGENTS.md 엔티티 조회 시 기사 2편 전문이 그대로 반환됨).
  `context`(§11.2 위 항목)와 같은 유형의 결함 — 사람 UI는 한 화면에 한
  문서/한 노드만 펼치니 상한이 필요 없었지만, 에이전트는 그 함수를 호출
  한 번으로 컨텍스트 윈도우를 태울 수 있다. `documents`는 `MAX_DOCUMENTS=100`
  하드캡+`since`/`query` 필터, `node`는 소스문서 최신 10개 캡+기본
  summary-only(`full=True`로 opt-in)로 수정.
- **`fetched_at`은 ISO8601, UTC 타임존 명시(`+00:00`)로 노출한다** — KST
  등 로컬 시간대로 임의 변환하지 않는 이유: 서버는 MCP 호출자(에이전트/
  하네스)가 어느 시간대에 있는지 알 방법이 없고, 클라이언트가 필요하면
  UTC 오프셋에서 스스로 변환하는 게 유일하게 모호하지 않은 방향이다.
  `document`/`node`/`documents` 세 툴 모두 원시 epoch가 아니라 이 형식으로
  통일(웹 UI JS가 쓰는 기존 `fetched_at` 키/포맷은 안 건드림 — MCP 응답
  전용 후처리).

---

## 12. 잔여 결정사항 (사용자 확인 필요, 구현 착수 전)

- [x] ~~`CLAIRE_READONLY_TOKEN` 신설~~ — 배제(§1.3, §5, 사용자 결정: 격리 수준을
      낮추는 변경이라 안 함). `/web`·`/webro` 기한제 세션 재사용으로 확정.
- [ ] `docs/EXTERNAL_ACCESS.md` 상태 정정(§1.2) — 이번 문서에 사실관계만 기록해둠,
      원본 문서 갱신 여부는 사용자 결정.
- [ ] Cloudflare Access를 실제로 켤지(nginx 주석의 원래 의도대로) 여부 — MCP와
      무관하게 현재 앱 게이트가 유일한 방어선이라는 게 맞는 상태인지 확인 필요.
- [ ] `graph` 툴의 이웃 hop 수/결과 상한 구체값.
- [ ] §5의 슬라이딩 TTL 무력화(MCP 상시 호출 시 세션이 사실상 안 끊길 수 있음) —
      **현재 세션 메커니즘 그대로 감수**할지, 아니면 절대 만료 상한(예: 발급 후
      N일이면 슬라이딩과 무관하게 무조건 만료) 같은 별도 장치를 원하는지 확인
      필요. 후자는 스키마/로직 변경이라 "가볍게 훑고 넘어갈 항목 아님".
- [x] M1 구현 착수 승인 및 완료 — 구현·테스트·배포까지 완료(사용자 승인 후
      같은 세션에서 진행).
