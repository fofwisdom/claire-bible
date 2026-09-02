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
        if "vmware.com/explore/video" in low or "brightcove.net" in low or "vimeo.com" in low:
            return "video"
        if any(low.split("?")[0].endswith(ext) for ext in (".mp4", ".m3u8", ".mpd", ".webm", ".m4a", ".mp3")):
            return "video"
        if "x.com" in low or "twitter.com" in low:
            return "xcom"
        if "share.google" in low or "share.g" in low:
            return "redirect"
        return "web"
    return "text"


import re

# 플래그: ASCII 하이픈(-, --), en-dash(–, ––), em-dash(—, ——), horizontal bar(―) 지원
# 예: --focus, —focus, –focus, -focus, --orientation, --directive, --perspective, -o
_DIRECTIVE_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?:focus|orientation|directive|perspective)|[-–—―]o)\s+([^\n]+)",
    re.IGNORECASE,
)
_DIRECTIVE_PREFIX_RE = re.compile(
    r"^(?:\[(?:초점|focus|방향성|방향|관점|지침|directive|orientation|perspective)\]|#(?:초점|focus|방향성|방향|관점|지침|directive|orientation|perspective)|(?:초점|focus|방향성|방향|관점|지침|directive|orientation|perspective)\s*[:：])\s*(.+)$",
    re.IGNORECASE,
)
# 파이프(|, ｜, ¦) 또는 대시(--, —, –) 구분자 지원 (파이프 중심 통일)
# 주의: 단일 하이픈('-')은 영상/문서 제목('Title - Subtitle')에 흔히 쓰이므로 구분자로 취급하지 않고 '--' 또는 '—', '–'만 허용.
_DIRECTIVE_SEP_RE = re.compile(
    r"(?:\s*([|｜¦])\s*|\s+([—–]{1,2}|--)\s+)",
)


def parse_message_directive(text: str) -> tuple[str, str | None]:
    """메시지 본문에서 페이로드(URL/텍스트)와 본문 작성 초점(focus/directive)을 분리 추출.

    지원 패턴:
    1. 파이프 구분: `URL | <초점>` 또는 `URL ｜ <초점>` (주 문법)
    2. 줄바꿈 구분: 첫 줄이 단일 URL이고 다음 줄에 텍스트나 [초점] 태그가 오는 경우
    3. 구분자/플래그: `URL --focus <초점>`, `URL -- <초점>`, `URL --orientation <초점>` 등 (호환)
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

    # 4. URL 뒤에 두 번 이상의 줄바꿈(빈 줄)을 사이에 두고 평문 텍스트가 오는 경우
    # (줄바꿈 1번은 단순 오타/오입력 사고일 수 있으므로 빈 줄이 있는 2번째 줄바꿈에서만 분리)
    from .ingest.router import _URL_RE
    blocks = [b.strip() for b in re.split(r"\n\s*\n", t) if b.strip()]
    if len(blocks) >= 2:
        first_block = blocks[0]
        if _URL_RE.fullmatch(first_block) or (first_block.lower().startswith(("http://", "https://")) and len(first_block.split()) == 1):
            rest = "\n\n".join(blocks[1:]).strip()
            pm = _DIRECTIVE_PREFIX_RE.match(rest)
            if pm:
                rest = pm.group(1).strip()
            return first_block, rest or None

    return t, None


# 재생성 / 재수집 / 추론 레벨 플래그 정규식
_EFFORT_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:effort|reasoning)|-[eE])\s+([a-zA-Z0-9_-]+)",
)
_REFETCH_FULL_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:refetch[-_]full|full[-_]refetch|full[-_]content|full|no[-_]truncate)|-R)(?:\s+|$)",
)
_REFETCH_FLAG_RE = re.compile(
    r"(?:\s+|^)(?:[-–—―]{1,2}(?i:refetch)|-r)(?:\s+|$)",
)


def parse_regenerate_flags(text: str) -> tuple[str, bool, bool, str | None]:
    """(cleaned_text, refetch, refetch_full, effort) 추출."""
    t = (text or "").strip()
    if not t:
        return "", False, False, None

    refetch_full = False
    refetch = False
    effort = None

    m_full = _REFETCH_FULL_FLAG_RE.search(t)
    if m_full:
        refetch_full = True
        t = (t[:m_full.start()] + " " + t[m_full.end():]).strip()

    m_ref = _REFETCH_FLAG_RE.search(t)
    if m_ref:
        if not refetch_full:
            refetch = True
        t = (t[:m_ref.start()] + " " + t[m_ref.end():]).strip()

    m_eff = _EFFORT_FLAG_RE.search(t)
    if m_eff:
        effort = m_eff.group(1).strip()
        t = (t[:m_eff.start()] + " " + t[m_eff.end():]).strip()

    return t, refetch, refetch_full, effort


def parse_caption_directive(caption: str | None) -> str | None:
    """파일/문서 첨부 캡션에서 초점 추출."""
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


def build_app(settings: Settings | None = None) -> Any:
    s = settings or get_settings()
    if not s.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN 이 없습니다. .env 에 설정하세요.")

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
        raise RuntimeError(f"python-telegram-bot 미설치: {e}\n  uv sync 후 다시 시도하세요.") from e

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
        "  • PDF·텍스트 파일 (캡션에 초점 작성 가능)\n"
        "  • 키워드/메모 등 자유 텍스트\n"
        "→ 스크랩 → Gemini 구조화 → 그래프로 저장, 기존 항목과 자동 연결.\n"
        "  관련 링크가 보이면 '가져오기' 버튼으로 1홉 확장.\n"
        "\n"
        "💡 공유 URL 관리 (원터치 액션 & 플래그):\n"
        "  • 보관된 문서의 공유 URL(/p?s=...) 또는 문서ID를 전송하면\n"
        "    본문 재생성 및 원문 재수집(길이 제한 / 전체 길이) 버튼이 제공됩니다.\n"
        "  • 옵션과 함께 전송하면 즉시 실행됩니다:\n"
        "    예: https://.../p?s=token --refetch-full --effort high | 새 초점\n"
        "\n"
        "💡 본문 작성 초점(Focus) 및 옵션 지정 방법:\n"
        "  • 파이프 구분: https://example.com/doc | 시스템 아키텍처 중심\n"
        "  • 무절단/추론 레벨 옵션: https://example.com/doc --full --effort high | 아키텍처 중심\n"
        "  • 빈 줄(두 번 줄바꿈) 구분:\n"
        "    https://example.com/doc\n\n"
        "    초보자 튜토리얼 관점으로 작성해줘\n"
        "  • 파일/PDF 전송 시 캡션에 원하는 초점 및 옵션(--full, --effort high)을 적어서 전송\n"
        "\n"
        "명령어:\n"
        "  /search <키워드> — 하이브리드 검색 + 요약(인용)\n"
        "  /ingest <URL|텍스트> [| <초점>] — 초점 지정 적재\n"
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

        from .config import extract_own_share_token
        from .store import db as dbm

        payload, directive = parse_message_directive(text)
        payload_clean, has_refetch, has_refetch_full, has_effort = parse_regenerate_flags(payload)

        # 자체 FQDN의 공유 링크 또는 document_id 검사 (타 사이트 /p?s= 오인 방지)
        target_doc_id = None
        share_tok = extract_own_share_token(payload_clean, s)
        if share_tok:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                target_doc_id = dbm.resolve_doc_share(conn, share_tok)
                if not target_doc_id:
                    row = conn.execute("SELECT document_id FROM doc_shares WHERE token=?", (share_tok,)).fetchone()
                    if row:
                        target_doc_id = row["document_id"]
            finally:
                conn.close()
        elif payload_clean.startswith("doc_") and len(payload_clean.split()) == 1:
            conn = dbm.connect(svc.s.db_file)
            try:
                dbm.init_db(conn)
                row = conn.execute("SELECT id FROM documents WHERE id=?", (payload_clean,)).fetchone()
                if row:
                    target_doc_id = row["id"]
            finally:
                conn.close()

        msg = update.message

        if target_doc_id:
            # 1. 지침, 초점, 또는 재수집 플래그가 함께 전달된 경우 -> 즉시 실행
            if has_refetch or has_refetch_full or has_effort or directive:
                label = f"본문 재생성 중… ({target_doc_id})"
                if has_refetch_full:
                    label += " [원문 전체 재수집]"
                elif has_refetch:
                    label += " [원문 재수집]"
                if directive:
                    label += f" [초점: {directive[:20]}]"
                if has_effort:
                    label += f" [추론: {has_effort}]"
                status = await msg.reply_text(f"⏳ {label}")
                try:
                    res = await _run_with_ticker(
                        status, label,
                        lambda: svc.regenerate_components(
                            doc_id=target_doc_id,
                            detail=True,
                            refetch=has_refetch,
                            refetch_full=has_refetch_full,
                            effort=has_effort,
                            directive=directive,
                            force=True,
                        ),
                    )
                    if res.get("error"):
                        ans = f"❌ 재생성 실패: {res['error']}"
                        emoji = "👎"
                    elif res.get("count", 0) > 0:
                        tinfo = res["targets"][0]
                        dir_msg = f"\n초점: {directive}" if directive else ""
                        ref_msg = ""
                        if tinfo.get("refetched_full"):
                            ref_msg = f" (원문 전체 재수집 {tinfo.get('new_len', 0):,}자)"
                        elif tinfo.get("refetched"):
                            ref_msg = f" (원문 재수집 {tinfo.get('new_len', 0):,}자)"
                        ans = f"✅ 본문 재생성 완료: {tinfo.get('title', target_doc_id)}{ref_msg}{dir_msg}"
                        emoji = "👍"
                    else:
                        ans = f"⚠️ 대상 문서를 찾을 수 없습니다: {target_doc_id}"
                        emoji = "🤔"
                except Exception as e:
                    ans = f"❌ 재생성 오류: {e}"
                    emoji = "👎"
                await _react(msg, emoji)
                await status.edit_text(ans)
                return

            # 2. 순수 공유 링크/doc_id만 보낸 경우 -> 원터치 스마트 인라인 액션 버튼 제공
            conn = dbm.connect(svc.s.db_file)
            doc = None
            try:
                doc = dbm.get_document(conn, target_doc_id)
            finally:
                conn.close()

            doc_title = (doc.title if doc else None) or target_doc_id
            raw_len = len(doc.raw_text) if doc and doc.raw_text else 0
            trunc_info = ""
            if doc and (doc.meta or {}).get("raw_truncated"):
                orig = (doc.meta or {}).get("orig_chars", raw_len)
                if (doc.meta or {}).get("appendix_truncated"):
                    trunc_info = f"\n✂️ 부록(Appendix) 제외 정책으로 원문 일부 절단됨 ({raw_len:,}자 / 원본 {orig:,}자)"
                else:
                    trunc_info = f"\n⚠️ 원문이 환경변수 상한으로 절단됨 ({raw_len:,}자 / 원본 {orig:,}자)"

            kb = [
                [InlineKeyboardButton("🔄 본문 재생성", callback_data=f"rg:det:{target_doc_id}")],
                [
                    InlineKeyboardButton("📥 원문 재수집 (길이 제한)", callback_data=f"rg:ref:{target_doc_id}"),
                    InlineKeyboardButton("🌐 전체 원문 재수집 (전체 길이)", callback_data=f"rg:full:{target_doc_id}"),
                ],
            ]
            markup = InlineKeyboardMarkup(kb)
            await msg.reply_text(
                f"📄 {doc_title}\n"
                f"• 보관 본문: {raw_len:,}자{trunc_info}\n\n"
                "수행할 작업을 선택하세요:",
                reply_markup=markup,
            )
            return

        # 일반 외부 웹페이지/텍스트 신규 적재
        payload_to_ingest = payload_clean or payload
        label = f"처리 중… ({classify_input(payload_to_ingest)})"
        if has_refetch_full:
            label += " [원문 전체]"
        if has_effort:
            label += f" [추론: {has_effort}]"
        if directive:
            label += f" [방향: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        try:
            report = await _run_with_ticker(
                status, label,
                lambda: svc.ingest(
                    payload_to_ingest,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    directive=directive,
                    effort=has_effort,
                    full_content=has_refetch_full,
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
        caption_clean, _, has_refetch_full, has_effort = parse_regenerate_flags(directive or "")
        clean_dir = caption_clean or None
        label = f"파일 처리 중… ({name})"
        if has_refetch_full:
            label += " [원문 전체]"
        if has_effort:
            label += f" [추론: {has_effort}]"
        if clean_dir:
            label += f" [방향: {clean_dir[:20]}]"
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
                directive=clean_dir,
                effort=has_effort,
                full_content=has_refetch_full,
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
                "  /ingest <URL 또는 텍스트> | <초점>\n"
                "  /ingest <URL 또는 텍스트> --full --effort high | <초점>\n"
                "  예: /ingest https://example.com/doc --full --effort high | 시스템 아키텍처 중심"
            )
            return
        payload, directive = parse_message_directive(raw)
        payload_clean, _, has_refetch_full, has_effort = parse_regenerate_flags(payload)
        payload_to_ingest = payload_clean or payload
        msg = update.message
        label = f"적재 처리 중… ({classify_input(payload_to_ingest)})"
        if has_refetch_full:
            label += " [원문 전체]"
        if has_effort:
            label += f" [추론: {has_effort}]"
        if directive:
            label += f" [초점: {directive[:20]}]"
        status = await msg.reply_text(f"⏳ {label}")
        uid = user.id if user else None
        cid = update.effective_chat.id if update.effective_chat else None
        try:
            report = await _run_with_ticker(
                status, label,
                lambda: svc.ingest(
                    payload_to_ingest,
                    source="telegram",
                    user_id=uid,
                    chat_id=cid,
                    directive=directive,
                    effort=has_effort,
                    full_content=has_refetch_full,
                ),
            )
            summary, cands = report.telegram_summary(), report.candidates
            emoji = _status_emoji(report.error, report.duplicate)
        except Exception as e:  # noqa: BLE001
            summary, cands, emoji = f"❌ 처리 오류: {e}", [], "👎"
        await _react(msg, emoji)
        await _settle(status, msg, summary, cands, update.update_id)

    async def on_callback(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data.startswith(("rg:det:", "rg:ref:", "rg:full:")):
            user = update.effective_user
            if not _is_allowed(user.id if user else None):
                return
            mode, did = data.split(":", 2)[1], data.split(":", 2)[2]
            do_refetch = (mode == "ref")
            do_refetch_full = (mode == "full")
            mode_name = "전체 재수집 및 재생성" if do_refetch_full else ("재수집 및 재생성" if do_refetch else "본문 재생성")
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            status_msg = await query.message.reply_text(f"⏳ {mode_name} 처리 중… ({did})")
            try:
                res = await _run_with_ticker(
                    status_msg, mode_name,
                    lambda: svc.regenerate_components(
                        doc_id=did,
                        detail=True,
                        refetch=do_refetch,
                        refetch_full=do_refetch_full,
                        force=True,
                    ),
                )
                if res.get("error"):
                    ans = f"❌ {mode_name} 실패: {res['error']}"
                elif res.get("count", 0) > 0:
                    tinfo = res["targets"][0]
                    len_info = f" ({tinfo.get('new_len', 0):,}자)" if (do_refetch or do_refetch_full) else ""
                    ans = f"✅ {mode_name} 완료: {tinfo.get('title', did)}{len_info}"
                else:
                    ans = f"⚠️ 대상 문서를 찾을 수 없습니다: {did}"
            except Exception as e:
                ans = f"❌ {mode_name} 오류: {e}"
            await status_msg.edit_text(ans)
            return
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
            BotCommand("ingest", "초점 지정 적재"),
            BotCommand("web", "웹 접속 링크 발급"),
            BotCommand("webro", "읽기전용 웹 링크 발급"),
            BotCommand("failed", "실패/영구실패 점검"),
            BotCommand("retry", "실패 항목 재시도"),
            BotCommand("start", "시작 안내"),
        ])

    app.post_init = _post_init
    return app


def run_bot() -> int:
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN 이 없습니다. .env 에 설정하세요.")
        return 2

    try:
        app = build_app(s)
    except Exception as e:
        print(f"Telegram bot 초기화 실패: {e}")
        return 2

    print("claire telegram bot 시작 (long-polling). Ctrl+C 로 종료.")
    app.run_polling()
    return 0
