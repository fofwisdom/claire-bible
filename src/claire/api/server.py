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
        if not s.inject_token:
            return True
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-Token", "")
        return token == s.inject_token

    async def health(_request):
        return web.json_response({"ok": True, "provider": svc.provider.name})

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
            "hits": [{"type": h.entity.type, "name": h.entity.name,
                      "via": h.via, "score": h.score} for h in result.hits],
        })

    app = web.Application()
    app.add_routes([
        web.get("/health", health),
        web.get("/stats", stats),
        web.post("/ingest", do_ingest),
        web.post("/search", do_search),
    ])
    print(f"claire inject API 시작: http://{s.inject_host}:{s.inject_port} "
          f"(token {'설정됨' if s.inject_token else '없음!'})")
    web.run_app(app, host=s.inject_host, port=s.inject_port, print=None)
    return 0
