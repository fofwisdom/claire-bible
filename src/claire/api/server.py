"""로컬 inject API — 텔레그램 DM 과 동일한 IngestService 통로를 HTTP 로 노출.

사용자가 "내가 DM 던지는 것과 동일한 통로를 공유하는 로컬 api"를 요청 → 봇과 같은
IngestService.ingest() 를 호출한다. 별도 프로세스(`claire serve-api`)로 띄워 봇과
크래시 격리. loopback bind + bearer token 필수. IngestReport 를 JSON 으로 반환해
검증 루프가 assertion 할 수 있게 한다.

엔드포인트:
  GET  /health
  GET  /stats
  POST /ingest   {"payload": str, "expand_max"?: int}   -> IngestReport JSON
  POST /search   {"query": str, "limit"?: int, "summarize"?: bool}
"""

from __future__ import annotations

import logging

from ..config import get_settings
from ..ingest.service import IngestService
from ..ingest.report_json import report_to_dict
from ..store import db as dbm

log = logging.getLogger("claire.api")


def run_api() -> int:
    s = get_settings()
    try:
        from aiohttp import web
    except Exception as e:  # noqa: BLE001
        print(f"aiohttp 미설치: {e}\n  uv sync 후 다시 시도하세요.")
        return 2

    if not s.inject_token:
        print("경고: CLAIRE_INJECT_TOKEN 미설정 → 인증 없이 노출됩니다. .env 에 설정 권장.")

    logging.basicConfig(level=logging.INFO)
    svc = IngestService(s)

    def _authed(request) -> bool:
        # 1) bearer 토큰 — 프로그래밍 호출자(CLI/replay_sample). 토큰 미설정이면 개방(loopback 기본).
        if not s.inject_token:
            return True
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-Token", "")
        if token == s.inject_token:
            return True
        # 2) 텔레그램 승인 세션(브라우저) — bearer 를 대체하지 않고 추가.
        sess = request.headers.get("X-Session", "")
        if sess:
            conn = dbm.connect(s.db_file)
            try:
                dbm.init_db(conn)
                if dbm.validate_session(conn, sess):
                    return True
            finally:
                conn.close()
        return False

    async def health(_request):
        import asyncio

        from ..health import health_report

        rep = await asyncio.to_thread(health_report, s, svc.provider.name)
        return web.json_response(rep, status=200 if rep["ok"] else 503)

    async def stats(request):
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        def _c():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            c = dbm.counts(conn)
            conn.close()
            return c

        return web.json_response(await asyncio.to_thread(_c))

    async def do_ingest(request):
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        payload = (body.get("payload") or "").strip()
        if not payload:
            return web.json_response({"error": "payload required"}, status=400)
        expand_max = body.get("expand_max")
        try:
            report = await asyncio.to_thread(
                svc.ingest, payload, source="api", expand_max=expand_max)
        except Exception as e:  # noqa: BLE001
            # 적재 중 예외(예: Gemini quota)도 200 + error 로 반환 →
            # raw_inbox 에 보관된 원본은 나중에 replay-failed 로 재적재.
            log.warning("ingest error: %s", e)
            return web.json_response({"error": str(e), "ok": False}, status=200)
        return web.json_response(report_to_dict(report))

    async def do_search(request):
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        query = (body.get("query") or "").strip()
        if not query:
            return web.json_response({"error": "query required"}, status=400)
        result = await asyncio.to_thread(
            svc.search, query, limit=int(body.get("limit", 8)),
            summarize=bool(body.get("summarize", True)))
        return web.json_response({
            "query": result.query,
            "answer": result.answer,
            "hits": [{"id": h.entity.id, "type": h.entity.type, "name": h.entity.name,
                      "via": h.via, "score": h.score} for h in result.hits],
        })

    async def graph_data(_request):
        import asyncio

        from ..graphview import graph_json

        def _g():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return graph_json(conn)
            finally:
                conn.close()

        return web.json_response(await asyncio.to_thread(_g))

    async def graph_ui(_request):
        from ..graphview import GRAPH_HTML

        return web.Response(text=GRAPH_HTML, content_type="text/html")

    async def documents_list_route(_request):
        import asyncio

        from ..graphview import documents_list

        def _d():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return {"documents": documents_list(conn)}
            finally:
                conn.close()

        return web.json_response(await asyncio.to_thread(_d))

    # nonce 승인 대기 시간(초). 이 안에 텔레그램 버튼을 안 누르면 만료 + 버튼 제거.
    AUTH_NONCE_TTL = 600.0

    async def auth_request(_request):
        # 웹이 세션을 얻기 위한 시작점: nonce 발급 + 소유자에게 승인 버튼 전송.
        import asyncio

        from ..notify import expire_button, send_approval_button

        def _new():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return dbm.create_auth_nonce(conn, ttl=AUTH_NONCE_TTL)
            finally:
                conn.close()

        nonce = await asyncio.to_thread(_new)
        msg_id = await asyncio.to_thread(
            send_approval_button, s.telegram_bot_token, s.notify_chat_id,
            "🔐 claire 웹 UI 접속 승인 요청\n승인하면 이 브라우저에서 종합 기능을 쓸 수 있습니다.",
            nonce)
        if not msg_id:
            return web.json_response(
                {"error": "승인 버튼 전송 실패(봇 토큰/chat 미설정)"}, status=503)

        async def _expire_later():
            # 일정시간 내 미승인이면 버튼 제거 + 만료 안내(스팸 방지, 사용자 요구).
            await asyncio.sleep(AUTH_NONCE_TTL)

            def _still_pending():
                conn = dbm.connect(s.db_file)
                dbm.init_db(conn)
                try:
                    return dbm.poll_auth_nonce(conn, nonce) is None  # 승인됐으면 token!=None
                finally:
                    conn.close()

            if await asyncio.to_thread(_still_pending):
                await asyncio.to_thread(
                    expire_button, s.telegram_bot_token, s.notify_chat_id, msg_id)

        asyncio.create_task(_expire_later())
        # ttl 을 함께 반환 → 클라의 카운트다운과 폴링 마감이 서버 만료와 동일 값에서 파생.
        return web.json_response({"nonce": nonce, "ttl": AUTH_NONCE_TTL})

    async def auth_poll(request):
        # 비인증(세션을 *얻기 위한* 폴링이라 닭-달걀). 승인된 nonce 만 토큰 반환.
        import asyncio

        nonce = request.query.get("nonce", "")
        if not nonce:
            return web.json_response({"error": "nonce required"}, status=400)

        def _p():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return dbm.poll_auth_nonce(conn, nonce)
            finally:
                conn.close()

        tok = await asyncio.to_thread(_p)
        return web.json_response({"session": tok} if tok else {"pending": True})

    async def node_detail(request):
        import asyncio

        from ..graphview import node_detail as _detail

        nid = request.query.get("id", "")
        if not nid:
            return web.json_response({"error": "id required"}, status=400)

        def _d():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return _detail(conn, nid)
            finally:
                conn.close()

        rep = await asyncio.to_thread(_d)
        if rep is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(rep)

    async def synthesize_route(request):
        # 비용 있는 LLM 종합 → /ingest 와 같은 토큰 인증 + 명시적 POST 만(자동 아님).
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        from ..graphview import synthesize as _syn

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        ids = body.get("node_ids") or []
        if not ids:
            return web.json_response({"error": "node_ids required"}, status=400)
        query = body.get("query")

        def _s():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return _syn(conn, svc.provider, ids, query)
            finally:
                conn.close()

        return web.json_response(await asyncio.to_thread(_s))

    app = web.Application()
    app.add_routes([
        web.get("/health", health),
        web.get("/stats", stats),
        web.post("/ingest", do_ingest),
        web.post("/search", do_search),
        # 읽기전용 그래프 뷰어(loopback). / = HTML, /graph = vis.js JSON, /node = 상세.
        web.get("/", graph_ui),
        web.get("/graph", graph_data),
        web.get("/node", node_detail),
        web.get("/documents", documents_list_route),
        web.post("/synthesize", synthesize_route),
        # 웹 UI 인증(텔레그램 버튼 승인 → 세션). poll 은 비인증(세션 획득용).
        web.post("/auth/request", auth_request),
        web.get("/auth/poll", auth_poll),
    ])
    print(f"claire inject API 시작: http://{s.inject_host}:{s.inject_port} "
          f"(token {'설정됨' if s.inject_token else '없음!'})")
    web.run_app(app, host=s.inject_host, port=s.inject_port, print=None)
    return 0
