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
import re

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
        # 2) 텔레그램 세션(브라우저) — X-Session 헤더 또는 claire_session 쿠키(/web 진입).
        sess = request.headers.get("X-Session", "") or request.cookies.get("claire_session", "")
        if sess:
            conn = dbm.connect(s.db_file)
            try:
                dbm.init_db(conn)
                if dbm.validate_session(conn, sess):
                    return True
            finally:
                conn.close()
        return False

    def _readonly_match(request) -> bool:
        """읽기전용 공개 토큰(owner bearer 와 별개) — GET 성격의 READONLY_PATHS 에서만
        gate 가 이걸 인정한다(쓰기 라우트는 애초에 도달 불가 — 핸들러가 이 함수를
        믿어서가 아니라 gate 의 경로 화이트리스트가 경계)."""
        if not s.readonly_token:
            return False
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-Token", "")
        return token == s.readonly_token

    def _authed_read(request) -> bool:
        """READONLY_PATHS 전용: owner 인증 또는 읽기전용 토큰이면 통과."""
        return _authed(request) or _readonly_match(request)

    async def health(_request):
        import asyncio

        from ..health import health_report

        rep = await asyncio.to_thread(health_report, s, svc.provider.name)
        return web.json_response(rep, status=200 if rep["ok"] else 503)

    async def stats(request):
        if not _authed_read(request):
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
        if not _authed_read(request):
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

    _IMAGE_PATH_RE = re.compile(r"^images/[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|gif|svg)$")

    async def image_route(request):
        # 로컬 보존 이미지 서빙(사용자 요구 — 외부링크 유실 대비). 경로는 엄격히 검증
        # (images/<파일명> 형태만, 트래버설 불가) — doc_id 를 몰라도 못 유추하고, 원래도
        # 공개 웹의 이미지였던 콘텐츠라 `/p`(공유 핫링크) 공개 열람에서도 보이도록 공개.
        rel = request.query.get("p", "")
        if not _IMAGE_PATH_RE.fullmatch(rel):
            return web.Response(status=404, text="Not Found")
        path = s.data_dir / rel
        if not path.is_file():
            return web.Response(status=404, text="Not Found")
        return web.FileResponse(path)

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

    async def document_detail_route(request):
        import asyncio

        from ..graphview import document_detail as _dd

        did = request.query.get("id", "")
        if not did:
            return web.json_response({"error": "id required"}, status=400)

        def _d():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                rep = _dd(conn, did)
                if rep is not None:
                    dbm.set_document_seen(conn, did, seen=True)  # 문서 열람 → unread 해제
                return rep
            finally:
                conn.close()

        rep = await asyncio.to_thread(_d)
        if rep is None:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(rep)

    async def document_pin_route(request):
        # 즐겨찾기 토글 — 소유자만(읽기전용 토큰은 READONLY_PATHS 에 없어 gate 가 이미 차단).
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        did = (body.get("id") or "").strip()
        if not did:
            return web.json_response({"error": "id required"}, status=400)
        pinned = bool(body.get("pinned", True))

        def _p():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return dbm.set_document_pinned(conn, did, pinned)
            finally:
                conn.close()

        ok = await asyncio.to_thread(_p)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"id": did, "pinned": pinned})

    async def document_hide_route(request):
        # 숨기기 토글 — 목록 전용(그래프 엔티티/관계는 그대로, 사용자 결정). 소유자만.
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        did = (body.get("id") or "").strip()
        if not did:
            return web.json_response({"error": "id required"}, status=400)
        hidden = bool(body.get("hidden", True))

        def _h():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return dbm.set_document_hidden(conn, did, hidden)
            finally:
                conn.close()

        ok = await asyncio.to_thread(_h)
        if not ok:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response({"id": did, "hidden": hidden})

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

    async def research_route(request):
        # 맥락 확장 조사 — LLM(웹검색 grounding)+판정+적재까지 비용 큰 명시적 액션.
        # 수십 초 걸리므로 NDJSON **스트리밍**: 진행 이벤트({stage,msg})를 실시간으로
        # 흘리고 마지막 줄에 {done:true, result:{...}}. 작업은 스레드에서 돌고 이벤트는
        # call_soon_threadsafe 로 이벤트루프의 큐에 합류한다.
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio
        import json

        from ..expand.research import contextual_research

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        query = (body.get("query") or "").strip()
        if not query:
            return web.json_response({"error": "query required"}, status=400)
        node_id = body.get("node_id") or None
        doc_id = body.get("doc_id") or None

        resp = web.StreamResponse()
        resp.content_type = "application/x-ndjson"
        await resp.prepare(request)

        async def send(obj) -> None:  # noqa: ANN001
            await resp.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

        loop = asyncio.get_running_loop()
        events: asyncio.Queue = asyncio.Queue()

        def on_progress(ev: dict) -> None:  # 워커 스레드에서 호출됨
            loop.call_soon_threadsafe(events.put_nowait, ev)

        fut = asyncio.ensure_future(asyncio.to_thread(
            contextual_research, s, svc.provider,
            query=query, node_id=node_id, doc_id=doc_id, progress=on_progress))
        while not (fut.done() and events.empty()):
            try:
                ev = await asyncio.wait_for(events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await send(ev)
        try:
            out = fut.result()
        except Exception as e:  # noqa: BLE001
            # rate limit 등 — 결과 줄의 error 로 반환해 UI 가 안내(ingest 와 동일 관례).
            log.warning("research error: %s", e)
            out = {"error": str(e)}
        await send({"done": True, "result": out})
        await resp.write_eof()
        return resp

    async def ingest_stream_route(request):
        # 웹 UI 적재 — fetch→추출→그래프 적재→1홉 확장(enqueue)까지 수 초~수십 초 걸리므로
        # /research 와 같은 NDJSON 스트리밍으로 단계 진행({stage,msg})을 실시간 표시하고
        # 마지막 줄에 {done:true, result: IngestReport}. 진행은 provider 의 스레드-로컬
        # progress 콜백(emit_progress; pipeline 단계 + gemini rate limit 대기)을 워커
        # 스레드에서 걸어 받는다. 적재 자체는 /ingest 와 동일한 svc.ingest(단일 통로).
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio
        import json

        from ..extract.provider import set_progress_callback

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        payload = (body.get("payload") or "").strip()
        if not payload:
            return web.json_response({"error": "payload required"}, status=400)
        expand_max = body.get("expand_max")

        resp = web.StreamResponse()
        resp.content_type = "application/x-ndjson"
        await resp.prepare(request)

        async def send(obj) -> None:  # noqa: ANN001
            await resp.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())

        loop = asyncio.get_running_loop()
        events: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str) -> None:  # 워커 스레드에서 호출됨(str)
            loop.call_soon_threadsafe(events.put_nowait, {"stage": "work", "msg": msg})

        def _run():
            set_progress_callback(on_progress)  # 이 워커 스레드에 한정(스레드-로컬)
            try:
                return svc.ingest(payload, source="web", expand_max=expand_max)
            finally:
                set_progress_callback(None)  # 스레드풀 재사용 대비 정리

        fut = asyncio.ensure_future(asyncio.to_thread(_run))
        while not (fut.done() and events.empty()):
            try:
                ev = await asyncio.wait_for(events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            await send(ev)
        try:
            out = report_to_dict(fut.result())
        except Exception as e:  # noqa: BLE001
            # 적재 중 예외(예: Gemini quota)도 결과 줄의 error 로 — 원본은 raw_inbox 에
            # 보관돼 replay-failed 로 재적재 가능(/ingest 와 동일 관례).
            log.warning("ingest stream error: %s", e)
            out = {"error": str(e), "ok": False}
        await send({"done": True, "result": out})
        await resp.write_eof()
        return resp

    async def dedup_scan_route(request):
        # 근사중복 클러스터 진단(비파괴) — minhash 백필 + O(n²) 비교라 무거워 스레드에서.
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        from ..graphview import dedup_clusters

        def _scan():
            scan = svc.dedup_scan()
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                return dedup_clusters(conn, scan)
            finally:
                conn.close()

        return web.json_response(await asyncio.to_thread(_scan))

    async def dedup_merge_route(request):
        # [파괴적] 사용자가 고른 keeper 로 loser 문서들을 병합 — 서버가 병합 전 자동 백업.
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        keeper = (body.get("keeper") or "").strip()
        losers = [str(x) for x in (body.get("losers") or []) if x]
        if not keeper or not losers:
            return web.json_response({"error": "keeper and losers required"}, status=400)

        def _merge():
            return svc.merge_one_cluster(keeper, losers, backup=True)

        try:
            res = await asyncio.to_thread(_merge)
        except Exception as e:  # noqa: BLE001
            log.warning("dedup merge error: %s", e)
            return web.json_response({"error": str(e)}, status=200)
        return web.json_response(res)

    async def create_share_route(request):
        # 문서 1개의 읽기 공유 토큰 발급(세션과 분리). 발급은 인증된 UI 에서만.
        if not _authed(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        import asyncio

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return web.json_response({"error": "invalid json"}, status=400)
        did = (body.get("doc_id") or "").strip()
        if not did:
            return web.json_response({"error": "doc_id required"}, status=400)

        def _share():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                if dbm.get_document_row(conn, did) is None:
                    return None
                return dbm.create_doc_share(conn, did)
            finally:
                conn.close()

        tok = await asyncio.to_thread(_share)
        if not tok:
            return web.json_response({"error": "document not found"}, status=404)
        return web.json_response({"token": tok, "path": "/p?s=" + tok})

    async def shared_doc_page(request):
        # [공개] 공유 토큰(/p?s=)으로 문서 1개만 읽기전용 렌더. 세션/그래프 인증과 무관 —
        # 토큰 자체가 인증을 대신하며 유출돼도 그 문서 1개만 노출된다. 게이트 예외(PUBLIC).
        import asyncio

        from ..graphview import document_detail as _dd
        from ..graphview import shared_html

        tok = request.query.get("s", "")

        def _load():
            conn = dbm.connect(s.db_file)
            dbm.init_db(conn)
            try:
                did = dbm.resolve_doc_share(conn, tok)
                if not did:
                    return None
                return _dd(conn, did)
            finally:
                conn.close()

        doc = await asyncio.to_thread(_load)
        if doc is None:
            return web.Response(status=404, text="Not Found")
        return web.Response(text=shared_html(doc), content_type="text/html")

    # --- 전 엔드포인트 게이트(외부 공개 대비) ---
    # 미인증 요청은 401 이 아니라 404 로 응답해 "여기 뭐 없음"처럼 보이게 한다(존재 숨김).
    # 인증은 bearer(CLI) · X-Session 헤더 · claire_session 쿠키. 진입은 /web 가 준 ?t= 링크.
    # /health: 도커 헬스체크(루프백). /p: 문서 공유 핫링크 — 쿼리의 공유 토큰이 자체 인증이라
    # 세션 게이트 예외(핸들러가 토큰을 검증, 무효면 404).
    PUBLIC_PATHS = {"/health", "/p", "/image"}
    # 읽기전용 공개 토큰이 도달 가능한 경로 화이트리스트 — 검색/그래프/노드상세/문서목록만.
    # ingest·dedup/merge·share·synthesize·research(LLM 호출 비용) 등 쓰기/비용 라우트는
    # 이 목록에 없으므로 readonly 토큰으로는 애초에 gate 를 못 지난다(핸들러 신뢰 아님).
    READONLY_PATHS = {"/", "/graph", "/node", "/documents", "/document", "/search", "/stats"}

    @web.middleware
    async def gate(request, handler):
        tok = request.query.get("t")
        if tok and request.path == "/":
            # /web 링크 진입: 토큰(또는 7자+ 프리픽스)이 유효하면 httponly 쿠키에 **전체
            # 토큰**을 담고 토큰 없는 URL 로 리다이렉트(주소창/히스토리/리퍼러 노출 최소화).
            # 이후엔 쿠키(전체 일치)로 인증. 프리픽스 허용은 이 진입 지점뿐.
            conn = dbm.connect(s.db_file)
            try:
                dbm.init_db(conn)
                full = dbm.resolve_session_prefix(conn, tok)
            finally:
                conn.close()
            if full:
                resp = web.HTTPFound("/")
                resp.set_cookie("claire_session", full, max_age=int(dbm.SESSION_TTL),
                                httponly=True, samesite="Lax", secure=True, path="/")
                return resp
            return web.Response(status=404, text="Not Found")
        if request.path in PUBLIC_PATHS or _authed(request):
            return await handler(request)
        if request.path in READONLY_PATHS and _readonly_match(request):
            return await handler(request)
        return web.Response(status=404, text="Not Found")

    app = web.Application(middlewares=[gate])
    app.add_routes([
        web.get("/health", health),
        web.get("/stats", stats),
        web.post("/ingest", do_ingest),
        web.post("/ingest-stream", ingest_stream_route),  # 웹 UI 적재(NDJSON 진행 스트리밍)
        web.post("/search", do_search),
        # 읽기전용 그래프 뷰어(loopback). / = HTML, /graph = vis.js JSON, /node = 상세.
        web.get("/", graph_ui),
        web.get("/graph", graph_data),
        web.get("/node", node_detail),
        web.get("/documents", documents_list_route),
        web.get("/image", image_route),
        web.get("/document", document_detail_route),
        web.post("/document/pin", document_pin_route),
        web.post("/document/hide", document_hide_route),
        web.post("/synthesize", synthesize_route),
        web.post("/research", research_route),
        # 중복 문서 정리(웹) — 진단(GET)·병합(POST, 파괴적·자동백업).
        web.get("/dedup", dedup_scan_route),
        web.post("/dedup/merge", dedup_merge_route),
        # 문서 공유 핫링크 — 발급(인증)·열람(공개, 토큰 자체 인증).
        web.post("/share", create_share_route),
        web.get("/p", shared_doc_page),
        # 웹 UI 인증(텔레그램 버튼 승인 → 세션). poll 은 비인증(세션 획득용).
        web.post("/auth/request", auth_request),
        web.get("/auth/poll", auth_poll),
    ])
    print(f"claire inject API 시작: http://{s.inject_host}:{s.inject_port} "
          f"(token {'설정됨' if s.inject_token else '없음!'})")
    web.run_app(app, host=s.inject_host, port=s.inject_port, print=None)
    return 0
