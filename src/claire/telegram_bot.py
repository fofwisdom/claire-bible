"""텔레그램 진입점 (long-polling).

링크/문서/키워드/PDF를 받으면 IngestService(공유 통로)로 적재하고, 1홉 확장 후보가
있으면 inline 버튼으로 "가져오기"를 제안한다. /search 로 검색.
적재 로직은 IngestService 에 있어 inject API · CLI 와 완전히 동일한 경로를 탄다.
"""

from __future__ import annotations

import asyncio
import logging

from .config import get_settings

log = logging.getLogger("claire.telegram")


def _status_emoji(error, duplicate: bool = False) -> str:
    """처리 결과 → 원본 메시지에 달 텔레그램 허용 reaction 이모지."""
    if error:
        return "👎"
    if duplicate:
        return "🤔"
    return "👍"  # 신규/갱신 완료


async def _run_with_ticker(status, label, work):
    """work(블로킹)를 스레드에서 실행하며 status 메시지를 5초마다 편집(진행 표시).

    파이프라인에 단계 콜백이 없으므로 경과 시간만 갱신 = '살아있음'을 알리되 스팸 아님.
    덕타이핑(status.edit_text)이라 PTB 의존 없음."""
    stop = asyncio.Event()

    async def tick():
        t = 0
        while not stop.is_set():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            t += 5
            try:
                await status.edit_text(f"⏳ {label} ({t}s)")
            except Exception:  # noqa: BLE001
                pass  # 편집 실패(동일내용/rate)는 무시

    tk = asyncio.create_task(tick())
    try:
        return await asyncio.to_thread(work)
    finally:
        stop.set()
        tk.cancel()


async def _react(msg, emoji: str) -> None:
    """원본 메시지에 reaction(best-effort — 불허 이모지/권한 실패는 무시)."""
    try:
        await msg.set_reaction(emoji)
    except Exception:  # noqa: BLE001
        pass


def classify_input(text: str) -> str:
    """입력 텍스트를 source_type 후보로 분류(사용자 안내용)."""
    t = (text or "").strip()
    if not t:
        return "empty"
    # '제목 + 트레일링 링크' 공유 텍스트면 그 링크 기준으로 라벨링(router 와 동일 규칙).
    if not t.lower().startswith(("http://", "https://")):
        from .ingest.router import extract_shared_url

        shared = extract_shared_url(t)
        if shared:
            t = shared
    low = t.lower()
    if low.startswith("http://") or low.startswith("https://"):
        if "youtube.com" in low or "youtu.be" in low:
            return "youtube"
        if "x.com" in low or "twitter.com" in low:
            return "xcom"
        if "share.google" in low or "share.g" in low:
            return "redirect"
        return "web"
    return "text"


import re

# 플래그: ASCII 하이픈(-, --), en-dash(–, ––), em-dash(—, ——), horizontal bar(―) 및 한국어 플래그 지원
# 예: --orientation, —orientation, –orientation, -orientation, --directive, —directive, --관점, —관점, -o, —o
_DIRECTIVE_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?:orientation|directive|focus|perspective|관점|방향성|방향|초점|지침)|[-–—―]o)\s+([^\n]+)",
    re.IGNORECASE,
)
_DIRECTIVE_PREFIX_RE = re.compile(
    r"^(?:\[(?:방향성|방향|초점|관점|지침|directive|orientation|focus|perspective)\]|#(?:방향성|방향|초점|관점|지침|directive|orientation|focus|perspective)|(?:방향성|방향|초점|관점|지침|directive|orientation|focus|perspective)\s*[:：])\s*(.+)$",
    re.IGNORECASE,
)
# 파이프(|, ｜, ¦) 또는 대시(--, —, –) 구분자 지원 (파이프 중심 통일)
_DIRECTIVE_SEP_RE = re.compile(
    r"(?:\s*([|｜¦])\s*|\s+([—–-]{1,2})\s+)",
)


def parse_message_directive(text: str) -> tuple[str, str | None]:
    """메시지 본문에서 페이로드(URL/텍스트)와 본문 작성 방향성(directive)을 분리 추출.

    지원 패턴:
    1. 파이프 구분: `URL | <지침>` 또는 `URL | <지침>` (주 문법)
    2. 줄바꿈 구분: 첫 줄이 단일 URL이고 다음 줄에 텍스트나 [방향] 태그가 오는 경우
    3. 구분자/플래그: `URL -- <지침>`, `URL — <지침>`, `URL --orientation <지침>` 등 (호환)
    """
    t = (text or "").strip()
    if not t:
        return "", None

    # 1. 플래그 호환 (--orientation, —orientation, -o 등)
    m = _DIRECTIVE_FLAG_RE.search(t)
    if m:
        dir_val = m.group(1).strip()
        payload = (t[:m.start()] + " " + t[m.end():]).strip()
        if payload:
            return payload, dir_val or None

    lines = [line.strip() for line in t.splitlines() if line.strip()]
    if not lines:
        return t, None

    # 2. 줄 단위 명시적 프리픽스 ([방향성], 방향:, #방향 등) 검사
    dir_lines = []
    payload_lines = []
    for line in lines:
        pm = _DIRECTIVE_PREFIX_RE.match(line)
        if pm:
            dir_lines.append(pm.group(1).strip())
        else:
            payload_lines.append(line)

    if dir_lines and payload_lines:
        return "\n".join(payload_lines), " ".join(dir_lines)

    # 3. 첫 줄에 파이프/대시 구분자가 있는 경우 (단일행 또는 다중행 모두 지원)
    first_line = lines[0]
    m_sep = _DIRECTIVE_SEP_RE.search(first_line)
    if m_sep:
        part_a = first_line[:m_sep.start()].strip()
        part_b = first_line[m_sep.end():].strip()
        if part_a:
            extra_lines = lines[1:]
            full_dir = "\n".join([part_b] + extra_lines).strip() if (part_b or extra_lines) else None
            return part_a, full_dir

    # 4. 첫 줄이 URL이고 줄바꿈(2번째 줄 이상) 뒤에 텍스트가 있는 경우 (URL\n지침 형태)
    from .ingest.router import _URL_RE
    if len(lines) >= 2:
        first = lines[0]
        if _URL_RE.fullmatch(first) or (first.lower().startswith(("http://", "https://")) and len(first.split()) == 1):
            rest = "\n".join(lines[1:]).strip()
            pm = _DIRECTIVE_PREFIX_RE.match(rest)
            if pm:
                rest = pm.group(1).strip()
            return first, rest or None

    return t, None


def parse_caption_directive(caption: str | None) -> str | None:
    """파일/문서 첨부 캡션에서 방향성 추출."""
    c = (caption or "").strip()
    if not c:
        return None
    pm = _DIRECTIVE_PREFIX_RE.match(c)
    if pm:
        return pm.group(1).strip() or None
    return c


def _is_allowed(user_id: int | None) -> bool:
    s = get_settings()
    allow = s.allowed_user_ids
    if not allow:
        return True
    return user_id in allow


def run_bot() -> int:
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN 이 없습니다. .env 에 설정하세요.")
        return 2

    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except Exception as e:  # noqa: BLE001
        print(f"python-telegram-bot 미설치: {e}\n  uv sync 후 다시 시도하세요.")
        return 2

    import asyncio
    import tempfile
    from pathlib import Path

    from .ingest.service import IngestService

    logging.basicConfig(level=logging.INFO)
    svc = IngestService(s)
    pending: dict[str, list[str]] = {}  # 확장 후보 임시 보관(콜백 토큰 -> urls)

    def _markup(update_id: int, candidates: list[str]):
        token = f"{update_id}"
        pending[token] = candidates
        kb = [
            [InlineKeyboardButton(
                f"🔗 관련 링크 {len(candidates)}개 가져오기",
                callback_data=f"exp:{token}")],
            [InlineKeyboardButton("아니요", callback_data=f"no:{token}")],
        ]
        return InlineKeyboardMarkup(kb)

    async def _settle(status, msg, summary: str, cands: list, update_id: int) -> None:
        """완료 처리: 1홉 후보가 있으면 진행 메시지를 결과+버튼으로 편집(버튼 보존),
        없으면 진행 메시지를 삭제(스팸 방지 — 결과는 원본 reaction 으로 표시됨)."""
        if cands:
            markup = _markup(update_id, cands)
            try:
                await status.edit_text(summary, reply_markup=markup)
            except Exception:  # noqa: BLE001
                await msg.reply_text(summary, reply_markup=markup)
        else:
            try:
                await status.delete()
            except Exception:  # noqa: BLE001
                pass

    HELP = (
        "📚 Claire Bible — 개인 지식베이스 봇\n"
        f"  (리포지토리: {s.effective_source_base_url})\n"
        "\n"
        "그냥 보내면 적재됩니다:\n"
        "  • 링크(웹/유튜브/x.com/google share)\n"
        "  • PDF·텍스트 파일 (캡션에 방향성 작성 가능)\n"
        "  • 키워드/메모 등 자유 텍스트\n"
        "→ 스크랩 → Gemini 구조화 → 그래프로 저장, 기존 항목과 자동 연결.\n"
        "  관련 링크가 보이면 '가져오기' 버튼으로 1홉 확장.\n"
        "\n"
        "💡 본문 작성 방향성(초점/관점) 지정 방법:\n"
        "  • 파이프 구분: https://example.com/doc | 시스템 아키텍처 중심\n"
        "  • 줄바꿈 구분: https://example.com/doc\n    초보자 튜토리얼 관점으로 작성해줘\n"
        "  • 파일/PDF 전송 시 캡션에 원하는 방향성을 적어서 전송\n"
        "\n"
        "명령어:\n"
        "  /search <키워드> — 하이브리드 검색 + 요약(인용)\n"
        "  /ingest <URL|텍스트> [| <방향>] — 방향성 지정 적재\n"
        "  /regenerate <문서ID|토큰|URL> [| <새 방향성>] — 가독 본문(detail) 맞춤 재생성\n"
        "  /web — 1회용 웹 로그인 링크 발급(로그인 쿠키 7일, 적재/수정 가능)\n"
        "  /webro — 읽기전용 웹 링크 발급(그래프·검색·문서만, 공유해도 안전)\n"
        "  /repo — 소스 리포지토리 접근 링크\n"
        "  /status — 현황(그래프 규모·수렴·최근 수신)\n"
        "  /failed — 실패/영구실패 항목 점검\n"
        "  /retry <번호> — 특정 실패 항목 재시도\n"
        "  /help — 이 도움말\n"
        "  /start — 시작 안내"
    )

    async def on_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(HELP)

    async def on_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(HELP)

    async def on_message(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text("허용되지 않은 사용자입니다.")
            return
        text = (update.message.text or "").strip()
        if not text:
            return
        payload, directive = parse_message_directive(text)
        msg = update.message
        label = f"처리 중… ({classify_input(payload)})"
        if directive:
            label += f" [방향: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        try:
            report = await _run_with_ticker(
                status, label,
                lambda: svc.ingest(
                    payload,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    directive=directive,
                ),
            )
            summary, cands = report.telegram_summary(), report.candidates
            emoji = _status_emoji(report.error, report.duplicate)
        except Exception as e:  # noqa: BLE001
            summary, cands, emoji = f"❌ 처리 오류: {e}", [], "👎"
        await _react(msg, emoji)
        await _settle(status, msg, summary, cands, update.update_id)

    async def on_document(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text("허용되지 않은 사용자입니다.")
            return
        doc = update.message.document
        if not doc:
            return
        name = doc.file_name or "document"
        msg = update.message
        caption = update.message.caption
        directive = parse_caption_directive(caption)
        label = f"파일 처리 중… ({name})"
        if directive:
            label += f" [방향: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None

        async def _download() -> str:
            tg_file = await doc.get_file()
            tmp = Path(tempfile.gettempdir()) / f"claire_{doc.file_unique_id}_{name}"
            await tg_file.download_to_drive(str(tmp))
            return str(tmp)

        try:
            tmp_path = await _download()
        except Exception as e:  # noqa: BLE001
            await _react(msg, "👎")
            await status.edit_text(f"❌ 다운로드 실패: {e}")
            return

        def _work():
            kept = svc.save_inbound_file(int(update.update_id), Path(tmp_path), name)
            return svc.ingest(
                kept,
                source="telegram",
                user_id=uid,
                chat_id=cid,
                inbox_kind="document",
                file_ref=kept,
                file_name=name,
                directive=directive,
            )

        try:
            report = await _run_with_ticker(status, label, _work)
            summary, cands = report.telegram_summary(), report.candidates
            emoji = _status_emoji(report.error, report.duplicate)
        except Exception as e:  # noqa: BLE001
            summary, cands, emoji = f"❌ 처리 오류: {e}", [], "👎"
        await _react(msg, emoji)
        await _settle(status, msg, summary, cands, update.update_id)

    async def on_ingest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text("허용되지 않은 사용자입니다.")
            return
        raw = " ".join(ctx.args) if ctx.args else ""
        if not raw:
            await update.message.reply_text(
                "사용법:\n"
                "  /ingest <URL 또는 텍스트>\n"
                "  /ingest <URL 또는 텍스트> | <방향성>\n"
                "  예: /ingest https://example.com/doc | 시스템 아키텍처 중심"
            )
            return
        payload, directive = parse_message_directive(raw)
        msg = update.message
        label = f"적재 처리 중… ({classify_input(payload)})"
        if directive:
            label += f" [방향: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        try:
            report = await _run_with_ticker(
                status, label,
                lambda: svc.ingest(
                    payload,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    directive=directive,
                ),
            )
            summary, cands = report.telegram_summary(), report.candidates
            emoji = _status_emoji(report.error, report.duplicate)
        except Exception as e:  # noqa: BLE001
            summary, cands, emoji = f"❌ 처리 오류: {e}", [], "👎"
        await _react(msg, emoji)
        await _settle(status, msg, summary, cands, update.update_id)

    async def on_regenerate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if not _is_allowed(user.id if user else None):
            await update.message.reply_text("허용되지 않은 사용자입니다.")
            return
        raw = " ".join(ctx.args) if ctx.args else ""
        if not raw:
            await update.message.reply_text(
                "사용법:\n"
                "  /regenerate <문서ID|토큰|URL>\n"
                "  /regenerate <문서ID|토큰|URL> | <새 방향성>\n"
                "  예: /regenerate doc_123456789012 | 보안 및 취약점 분석 관점"
            )
            return
        target, directive = parse_message_directive(raw)
        tokens = raw.split(None, 1)
        if directive is None and len(tokens) > 1:
            target = tokens[0]
            directive = tokens[1].strip()
        else:
            target = target or (tokens[0] if tokens else "")

        msg = update.message
        label = f"본문 재생성 중… ({target})"
        if directive:
            label += f" [방향: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        try:
            res = await _run_with_ticker(
                status, label,
                lambda: svc.regenerate_components(
                    target=target,
                    detail=True,
                    force=True,
                    directive=directive,
                ),
            )
            if res.get("error"):
                ans = f"❌ 재생성 실패: {res['error']}"
                emoji = "👎"
            elif res.get("count", 0) > 0:
                tinfo = res["targets"][0]
                dir_msg = f"\n초점/방향성: {directive}" if directive else ""
                ans = f"✅ 본문 재생성 완료: {tinfo.get('title', target)}{dir_msg}"
                emoji = "👍"
            else:
                ans = f"⚠️ 대상 문서를 찾을 수 없습니다: {target}"
                emoji = "🤔"
        except Exception as e:  # noqa: BLE001
            ans = f"❌ 재생성 오류: {e}"
            emoji = "👎"
        await _react(msg, emoji)
        await status.edit_text(ans)

    async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data.startswith("auth:"):
            # 웹 UI 접속 승인 — 소유자만. DB 에 세션 토큰 발급(웹이 poll 로 수령).
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            from .store import db as dbm

            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                tok = dbm.approve_auth_nonce(conn, data[5:])
            finally:
                conn.close()
            # 메시지 편집 = 버튼 제거(reply_markup 미지정 → 제거).
            await query.edit_message_text(
                "✅ 웹 접속이 승인되었습니다. 브라우저로 돌아가세요."
                if tok else "⚠️ 만료되었거나 이미 처리된 요청입니다.")
            return
        if data.startswith("no:"):
            # 거절: 진행/결과 메시지를 아예 삭제(스팸 감소 — 적재 결과는 원본 reaction 으로
            # 이미 표시됨). 삭제 불가(시간초과 등)면 버튼만 제거로 폴백.
            pending.pop(data[3:], None)
            try:
                await query.message.delete()
            except Exception:  # noqa: BLE001
                await query.edit_message_reply_markup(reply_markup=None)
            return
        if data.startswith("exp:"):
            urls = pending.pop(data[4:], [])
            if not urls:
                await query.edit_message_reply_markup(reply_markup=None)
                return
            # 같은 메시지를 in-place 편집해 진행→결과로 갱신(새 메시지 2개 더 안 만든다).
            async def _edit(text: str) -> None:
                try:
                    await query.edit_message_text(text)
                except Exception:  # noqa: BLE001
                    pass
            await _edit(f"⏳ {len(urls)}개 확장 적재 중…")
            lines = []
            for url in urls:
                try:
                    sub = await asyncio.to_thread(
                        svc.ingest, url, source="telegram-expand", expand_max=0)
                    lines.append(f"• {sub.telegram_summary().splitlines()[0]}")
                except Exception as e:  # noqa: BLE001
                    lines.append(f"• ❌ {url}: {e}")
            await _edit("🔗 확장 적재 결과\n" + "\n".join(lines))

    async def on_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        q = " ".join(ctx.args) if ctx.args else ""
        if not q:
            await update.message.reply_text("사용법: /search <키워드>")
            return
        await update.message.reply_text("🔎 검색 중…")
        try:
            result = await asyncio.to_thread(svc.search, q)
            text = result.telegram_text()
        except Exception as e:  # noqa: BLE001
            text = f"❌ 검색 오류: {e}"
        await update.message.reply_text(text)

    async def on_web(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # 웹 접속 링크 발급: 세션 토큰 즉시 발급 → ?t= 가 붙은 1회용 진입 URL 회신.
        # 링크는 첫 접속에서 cookie 세션으로 회전하고 URL 자체는 즉시 무효화된다.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        from .store import db as dbm

        if not s.public_url:
            await update.message.reply_text(
                "CLAIRE_PUBLIC_URL 이 설정되지 않았습니다(.env). 외부 URL 을 먼저 지정하세요.")
            return

        def _mint() -> str:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                return dbm.create_session(conn)
            finally:
                conn.close()

        tok = await asyncio.to_thread(_mint)
        url = f"{s.public_url.rstrip('/')}/?t={tok}"
        await update.message.reply_text(
            "🔗 1회용 웹 로그인 링크:\n" + url +
            "\n\n첫 접속 뒤 URL은 무효가 되고 로그인 쿠키가 7일간 자동 연장됩니다. "
            "공유하지 마세요.",
            disable_web_page_preview=True,
        )

    async def on_webro(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # 읽기전용 웹 링크: /web 과 동일 메커니즘(세션+쿠키, 7일 슬라이딩)이지만
        # scope='readonly' 라 그래프·검색·문서 목록/상세만 보이고 적재·병합·공유발급 등
        # 쓰기는 막힌다(server.py 게이트, READONLY_PATHS 밖은 애초에 도달 불가). owner
        # 세션(/web)과 별도 scope 라 서로 안 끊고 공존 — 이 링크를 남에게 공유해도
        # 내 소유자 세션은 그대로 살아있다. 소유자만 발급 가능.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        from .store import db as dbm

        if not s.public_url:
            await update.message.reply_text(
                "CLAIRE_PUBLIC_URL 이 설정되지 않았습니다(.env). 외부 URL 을 먼저 지정하세요.")
            return

        def _mint() -> str:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                return dbm.create_session(conn, scope="readonly")
            finally:
                conn.close()

        tok = await asyncio.to_thread(_mint)
        url = f"{s.public_url.rstrip('/')}/?t={tok}"
        await update.message.reply_text(
            "🔗 읽기전용 웹 링크 (7일 · 접속 시 자동 연장):\n" + url +
            "\n\n그래프·검색·문서만 볼 수 있고 적재/수정은 안 됩니다. 다른 사람과 공유해도 "
            "안전합니다(다시 /webro 하면 이전 읽기전용 링크만 무효화 — /web 세션은 안 건드림).",
            disable_web_page_preview=True,
        )

    async def on_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        from .status import build_status_text

        try:
            text = await asyncio.to_thread(build_status_text, s, full=False)
        except Exception as e:  # noqa: BLE001
            text = f"❌ status 오류: {e}"
        await update.message.reply_text(text)

    async def on_repo(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        await update.message.reply_text(
            f"🐙 Claire Bible 소스 리포지토리:\n{s.effective_source_base_url}\n"
            f"(저장소: {s.effective_github_repository})",
            disable_web_page_preview=False,
        )

    async def on_failed(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        # SSH 없이 폰에서 점검 가능하게: error/영구실패 목록 + /retry 사용법.
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        try:
            items = await asyncio.to_thread(svc.list_failures, limit=10)
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"❌ 조회 오류: {e}")
            return
        if not items:
            await update.message.reply_text("✅ 실패 항목 없음.")
            return
        lines = ["⚠️ 최근 실패 항목 (최대 10건):"]
        for it in items:
            mark = "⛔영구" if it["status"] == "failed" else "🔁재시도대기"
            lines.append(
                f"#{it['id']} {mark} (시도 {it['attempts']}) "
                f"{it['payload']}\n   └ {it['error']}")
        lines.append("\n특정 건 재시도: /retry <번호>  (예: /retry " + str(items[0]["id"]) + ")")
        await update.message.reply_text("\n".join(lines))

    async def on_retry(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update.effective_user.id if update.effective_user else None):
            return
        arg = ctx.args[0] if ctx.args else ""
        if not arg.isdigit():
            await update.message.reply_text("사용법: /retry <inbox 번호>  (/failed 로 번호 확인)")
            return
        inbox_id = int(arg)
        status = await update.message.reply_text(f"⏳ inbox#{inbox_id} 재시도 중…")
        try:
            report = await _run_with_ticker(
                status, f"inbox#{inbox_id} 재시도",
                lambda: svc.retry_inbox(inbox_id))
            await status.edit_text(report.telegram_summary())
        except Exception as e:  # noqa: BLE001
            await status.edit_text(f"❌ 재시도 오류: {e}")

    app = Application.builder().token(s.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(CommandHandler("help", on_help))
    app.add_handler(CommandHandler("status", on_status))
    app.add_handler(CommandHandler("repo", on_repo))
    app.add_handler(CommandHandler("search", on_search))
    app.add_handler(CommandHandler("ingest", on_ingest))
    app.add_handler(CommandHandler("regenerate", on_regenerate))
    app.add_handler(CommandHandler("regen", on_regenerate))
    app.add_handler(CommandHandler("web", on_web))
    app.add_handler(CommandHandler("webro", on_webro))
    app.add_handler(CommandHandler("failed", on_failed))
    app.add_handler(CommandHandler("retry", on_retry))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    async def _post_init(application) -> None:
        # 텔레그램 클라이언트 입력창의 '/' 명령 메뉴에 노출.
        from telegram import BotCommand

        await application.bot.set_my_commands([
            BotCommand("help", "사용법"),
            BotCommand("status", "현황(그래프/수렴/최근)"),
            BotCommand("repo", "소스 리포지토리 링크"),
            BotCommand("search", "검색 + 요약"),
            BotCommand("ingest", "방향성 지정 적재"),
            BotCommand("regenerate", "본문 방향성 재생성"),
            BotCommand("web", "웹 접속 링크 발급"),
            BotCommand("webro", "읽기전용 웹 링크 발급"),
            BotCommand("failed", "실패/영구실패 점검"),
            BotCommand("retry", "실패 항목 재시도"),
            BotCommand("start", "시작 안내"),
        ])

    app.post_init = _post_init
    print("claire telegram bot 시작 (long-polling). Ctrl+C 로 종료.")
    app.run_polling()
    return 0
