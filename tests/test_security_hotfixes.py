"""P0 보안 핫픽스 회귀 테스트.

1. OAuth 승인 화면의 HTML/JS 이스케이프 (공개 DCR client_name, 반사되는 state 등)
2. 공유 페이지 <noscript> 폴백에 raw detail_html을 넣지 않음
3. /document/raw: 실행 가능한 형식은 octet-stream + attachment, 모든 응답에 sandbox CSP + nosniff
4. WebUI JS: 이스케이프 헬퍼의 `'` 처리, 인라인 핸들러 안 문자열 인자는 jsArg로만
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from starlette.testclient import TestClient

from claire.api.server import create_app
from claire.config import Settings
from claire.graphview import shared_html
from claire.ontology.base import Document
from claire.store import db as dbm

ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "src" / "claire" / "static" / "js"

OWNER_TOKEN = "owner-" + ("o" * 32)
COLLAB_TOKEN = "collab-" + ("c" * 32)
READONLY_TOKEN = "readonly-" + ("r" * 32)
OWNER_HEADERS = {"Authorization": f"Bearer {OWNER_TOKEN}"}
COLLAB_HEADERS = {"Authorization": f"Bearer {COLLAB_TOKEN}"}

SANDBOX_CSP = "sandbox; default-src 'none'"
XSS_NAME = "<script>alert(1)</script>"
XSS_STATE = '"><img src=x onerror=1>'
NOSCRIPT_PAYLOAD = '</noscript><script>alert("pwn")</script><img src=x onerror=alert(2)>'


@pytest.fixture
def ctx(tmp_path: Path):
    data_dir = tmp_path / "data"
    vault_dir = tmp_path / "vault"
    data_dir.mkdir(parents=True)
    vault_dir.mkdir(parents=True)
    settings = Settings(
        CLAIRE_DB_PATH=str(data_dir / "claire.db"),
        CLAIRE_VAULT_PATH=str(vault_dir),
        CLAIRE_PROVIDER="mock",
        CLAIRE_ENVIRONMENT="development",
        CLAIRE_PUBLIC_URL="http://127.0.0.1:8765",
        CLAIRE_INJECT_TOKEN=OWNER_TOKEN,
        CLAIRE_COLLABORATOR_TOKEN=COLLAB_TOKEN,
        CLAIRE_READONLY_TOKEN=READONLY_TOKEN,
        CLAIRE_ANONYMOUS_READONLY=True,
    )
    conn = dbm.connect(settings.db_file)
    dbm.init_db(conn)
    conn.close()
    return settings, data_dir


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings=settings), base_url=settings.public_url)


# ---------------------------------------------------------------- 1. OAuth


def _authorize_url(client_id: str, state: str) -> str:
    return (
        f"/oauth/authorize?response_type=code&client_id={quote(client_id)}"
        f"&redirect_uri={quote('https://example.com/cb')}&state={quote(state)}"
        f"&code_challenge=abc&code_challenge_method=S256"
    )


def test_oauth_authorize_escapes_registered_client_name_and_state(ctx) -> None:
    settings, _ = ctx
    with _client(settings) as client:
        reg = client.post(
            "/oauth/register",
            json={"client_name": XSS_NAME, "redirect_uris": ["https://example.com/cb"]},
        )
        assert reg.status_code == 201
        client_id = reg.json()["client_id"]

        resp = client.get(_authorize_url(client_id, XSS_STATE))
        assert resp.status_code == 200
        body = resp.text
        assert "<script>alert(1)" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        assert XSS_STATE not in body
        assert "<img src=x" not in body
        assert 'value="&quot;&gt;&lt;img src=x onerror=1&gt;"' in body
        # 인라인 스크립트의 JS 리터럴은 HTML 이스케이프 대신 \u003c 형태로 들어간다
        assert '"\\"\\u003e\\u003cimg src=x onerror=1\\u003e"' in body


def test_oauth_authorize_script_literals_cannot_close_script(ctx) -> None:
    settings, _ = ctx
    evil = "</script><script>alert(3)</script>\u2028&"
    with _client(settings) as client:
        resp = client.get(_authorize_url("claire_mcp_" + evil, evil))
        assert resp.status_code == 200
        body = resp.text
        assert "</script><script>alert(3)" not in body
        # 인라인 <script> 블록은 정확히 하나, 그 안에 원문 `</script` 없음
        scripts = re.findall(r"<script>(.*?)</script>", body, flags=re.S)
        assert len(scripts) == 1
        assert "\\u003c/script\\u003e" in scripts[0]
        assert "\u2028" not in scripts[0]  # HTML 속성 안의 U+2028은 무해, 스크립트 안에서만 금지
        assert "\\u2028" in scripts[0] and "\\u0026" in scripts[0]


def test_oauth_authorize_post_error_page_escapes(ctx) -> None:
    settings, _ = ctx
    with _client(settings) as client:
        reg = client.post("/oauth/register", json={"client_name": XSS_NAME, "redirect_uris": []})
        client_id = reg.json()["client_id"]
        resp = client.post(
            "/oauth/authorize",
            data={
                "action": "approve",
                "client_id": client_id,
                "redirect_uri": "https://example.com/cb",
                "state": XSS_STATE,
                "code_challenge": XSS_STATE,
                "session_token": "invalid-token",
            },
        )
        assert resp.status_code == 401
        assert "<script>alert(1)" not in resp.text
        assert XSS_STATE not in resp.text


# ---------------------------------------------------------------- 2. share <noscript>


def _noscript_block(page: str) -> str:
    start = page.index("<noscript>")
    end = page.index("</noscript>", start)
    return page[start : end + len("</noscript>")]


def test_shared_html_noscript_uses_escaped_summary_only() -> None:
    doc = {"id": "d1", "title": "T", "summary": "요약 <b>본문</b>", "detail_html": NOSCRIPT_PAYLOAD}
    page = shared_html(doc, base_url="https://claire.example.com", share_token="tok")
    assert NOSCRIPT_PAYLOAD not in page
    assert '<script>alert("pwn")' not in page
    block = _noscript_block(page)
    assert "요약 &lt;b&gt;본문&lt;/b&gt;" in block
    assert "<img" not in block
    # noscript 바로 뒤에 주입 페이로드가 이어지지 않는다
    tail = page[page.index(block) + len(block) :]
    assert not tail.lstrip().startswith("<script>alert")


def test_shared_html_noscript_without_summary_uses_plain_text() -> None:
    doc = {"id": "d2", "title": "T", "detail_html": "<p>안전한 본문</p>" + NOSCRIPT_PAYLOAD}
    page = shared_html(doc, base_url="https://claire.example.com", share_token="tok")
    block = _noscript_block(page)
    assert "안전한 본문" in block
    assert "<script" not in block and "<img" not in block
    assert block.count("</noscript>") == 1
    assert '<script>alert("pwn")' not in page


def test_share_page_route_does_not_emit_raw_detail_html(ctx) -> None:
    settings, _ = ctx
    conn = dbm.connect(settings.db_file)
    dbm.insert_document(conn, Document(id="doc-share-xss", title="공유", raw_text="본문", source_type="text"))
    dbm.set_document_detail(conn, "doc-share-xss", "본문", html=NOSCRIPT_PAYLOAD)
    conn.commit()
    conn.close()
    with _client(settings) as client:
        share = client.post("/share", json={"doc_id": "doc-share-xss"}, headers=OWNER_HEADERS)
        assert share.status_code == 200
        page = client.get(f"/p?s={share.json()['token']}")
        assert page.status_code == 200
        assert "</noscript><script>" not in page.text
        assert '<script>alert("pwn")' not in page.text
        assert "<img src=x onerror=alert(2)>" not in page.text


# ---------------------------------------------------------------- 3. /document/raw


def _add_raw(settings: Settings, data_dir: Path, doc_id: str, name: str, content: bytes) -> None:
    files = data_dir / "raw" / "files"
    files.mkdir(parents=True, exist_ok=True)
    path = files / f"1_{name}"
    path.write_bytes(content)
    conn = dbm.connect(settings.db_file)
    dbm.insert_document(conn, Document(id=doc_id, title=name, raw_text="x", source_type="file"))
    conn.execute(
        "INSERT INTO raw_inbox(received_at, source, kind, payload, file_name, file_ref, document_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), "cli", "document", "", name, str(path), doc_id, "done"),
    )
    conn.commit()
    conn.close()


def _assert_sandbox_headers(resp) -> None:
    assert resp.headers["content-security-policy"] == SANDBOX_CSP
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "name, content",
    [
        ("evil.html", b"<html><script>alert(1)</script></html>"),
        ("evil.HTM", b"<script>alert(1)</script>"),
        ("evil.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'),
        ("evil.xhtml", b"<html xmlns='http://www.w3.org/1999/xhtml'><script>alert(1)</script></html>"),
        ("evil.xml", b"<?xml version='1.0'?><x/>"),
        ("evil.js", b"alert(1)"),
    ],
)
def test_document_raw_active_types_are_downloaded(ctx, name: str, content: bytes) -> None:
    settings, data_dir = ctx
    _add_raw(settings, data_dir, "doc-active", name, content)
    with _client(settings) as client:
        resp = client.get("/document/raw?id=doc-active", headers=COLLAB_HEADERS)
        assert resp.status_code == 200
        assert resp.content == content
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-disposition"].startswith("attachment;")
        _assert_sandbox_headers(resp)


@pytest.mark.parametrize(
    "name, content, ctype",
    [
        ("paper.pdf", b"%PDF-1.4 bytes", "application/pdf"),
        ("note.txt", "본문 텍스트".encode(), "text/plain; charset=utf-8"),
        ("note.md", b"# title", "text/markdown; charset=utf-8"),
        ("pic.png", b"\x89PNG\r\n\x1a\n", "image/png"),
    ],
)
def test_document_raw_safe_types_stay_inline(ctx, name: str, content: bytes, ctype: str) -> None:
    settings, data_dir = ctx
    _add_raw(settings, data_dir, "doc-safe", name, content)
    with _client(settings) as client:
        resp = client.get("/document/raw?id=doc-safe", headers=COLLAB_HEADERS)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == ctype
        assert resp.headers["content-disposition"].startswith("inline;")
        if ctype == "application/pdf":
            # 내장 PDF 뷰어는 sandbox 문서에서 열리지 않으므로 PDF만 앱 기본 CSP를 받는다
            assert "sandbox" not in resp.headers["content-security-policy"]
            assert resp.headers["x-content-type-options"] == "nosniff"
        else:
            _assert_sandbox_headers(resp)


def test_document_raw_filename_is_rfc5987_encoded(ctx) -> None:
    settings, data_dir = ctx
    name = '보고서 "a";b/c.pdf'
    _add_raw(settings, data_dir, "doc-name", "plain.pdf", b"%PDF-1.4")
    conn = dbm.connect(settings.db_file)
    conn.execute("UPDATE raw_inbox SET file_name=? WHERE document_id=?", (name, "doc-name"))
    conn.commit()
    conn.close()
    with _client(settings) as client:
        resp = client.get("/document/raw?id=doc-name", headers=COLLAB_HEADERS)
        disp = resp.headers["content-disposition"]
        assert disp.startswith('inline; filename="')
        ascii_part = disp.split('filename="', 1)[1].split('"', 1)[0]
        assert re.fullmatch(r"[A-Za-z0-9._-]+", ascii_part)
        assert "filename*=UTF-8''" + quote(name, safe="") in disp
        assert "%2F" in disp and "%22" in disp and "%3B" in disp


def test_document_raw_error_responses_carry_sandbox_headers(ctx) -> None:
    settings, _ = ctx
    with _client(settings) as client:
        missing = client.get("/document/raw", headers=OWNER_HEADERS)
        assert missing.status_code == 400
        _assert_sandbox_headers(missing)
        nf = client.get("/document/raw?id=nope", headers=OWNER_HEADERS)
        assert nf.status_code == 404
        _assert_sandbox_headers(nf)


# ---------------------------------------------------------------- 4. JS escaping (static)

_ESC_HELPERS = [
    (STATIC_JS / "app.js", r"function esc\(s\)\{.*?\}\s*\n"),
    (STATIC_JS / "renderers" / "adoc_parser.js", r"function esc\(s\) \{.*?\n\}\n"),
    (STATIC_JS / "syndication_app.js", r"function esc\(s\) \{.*?\}\n"),
    (STATIC_JS / "console_syndication.js", r"function escapeHtml\(s\) \{.*?\n  \}\n"),
]


def _helper_handles_quote(src: str) -> bool:
    # 문자 클래스에 ' 포함 + &#39; 치환, 또는 .replace(/'/g, "&#39;")
    return "&#39;" in src and ("'" in src.split("&#39;")[0] or "/'/g" in src)


@pytest.mark.parametrize("path, pattern", _ESC_HELPERS, ids=lambda v: getattr(v, "name", "re"))
def test_escape_helpers_escape_single_quote(path: Path, pattern: str) -> None:
    m = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.S)
    assert m, f"escape helper not found in {path.name}"
    body = m.group(0)
    assert _helper_handles_quote(body), body
    for ch in ("&amp;", "&lt;", "&gt;", "&quot;"):
        assert ch in body


def test_escape_helper_check_flags_old_helpers() -> None:
    old = """function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}"""
    assert not _helper_handles_quote(old)
    old2 = 'function esc(s) { return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;"); }'
    assert not _helper_handles_quote(old2)


# 인라인 핸들러 속성값 안에서 따옴표로 감싼 JS 문자열 인자를 연결(+ 또는 ${})로 만드는 패턴.
# `\''+x` (작은따옴표 JS 문자열 안), `'${x}` (템플릿 리터럴 안), `('"+x` (큰따옴표 JS 문자열 안)
_QUOTED_ARG_RE = re.compile(r"""\bon[a-z]+=\\?"[^"]*?(?:\\''\s*\+|'\$\{|\('"\s*\+)""")
# 인라인 핸들러 안의 연결 값: jsArg(...)·Number(...) 또는 숫자/불리언으로 알려진 값만 허용
_CONCAT_RE = re.compile(r"""'\s*\+\s*(.+?)\s*\+\s*'""", re.S)
_TEMPLATE_RE = re.compile(r"\$\{(.+?)\}")
_ALLOWED_RAW = {"ci", "i", "score", "(!pinned)", "t.id", "tok.id"}


def _handler_values(src: str):
    for m in re.finditer(r'\bon[a-z]+="', src):
        end = src.find('"', m.end())
        yield src.count("\n", 0, m.start()) + 1, src[m.end() : end]


def _unsafe_handler_sites(src: str) -> list[str]:
    bad = [f"quoted:{src.count(chr(10), 0, m.start()) + 1}" for m in _QUOTED_ARG_RE.finditer(src)]
    for line, val in _handler_values(src):
        for expr in _CONCAT_RE.findall(val) + _TEMPLATE_RE.findall(val):
            expr = expr.strip()
            if expr in _ALLOWED_RAW or re.fullmatch(r"(jsArg|Number)\(.*\)", expr):
                continue
            bad.append(f"{line}:{expr}")
    return bad


@pytest.mark.parametrize("name", ["app.js", "reader.js"])
def test_inline_handlers_use_jsarg_for_string_args(name: str) -> None:
    src = (STATIC_JS / name).read_text(encoding="utf-8")
    assert _unsafe_handler_sites(src) == []


def test_jsarg_helper_definition() -> None:
    src = (STATIC_JS / "app.js").read_text(encoding="utf-8")
    assert "function jsArg(v){return esc(JSON.stringify(String(v??'')));}" in src


def test_handler_check_flags_original_code() -> None:
    """검사 정규식이 핫픽스 이전 코드를 실제로 잡는지 HEAD 원본으로 확인."""
    def head(rel: str) -> str | None:
        try:
            return subprocess.run(
                ["git", "show", f"f420b8f:{rel}"], cwd=ROOT, capture_output=True, text=True, check=True
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return None

    old_app = head("src/claire/static/js/app.js")
    old_reader = head("src/claire/static/js/reader.js")
    if old_app is not None and old_reader is not None:
        # 조사 보고서가 지목한 인라인 핸들러(app.js 21곳 이상, reader.js 3곳)를 모두 잡아야 한다
        assert len([h for h in _unsafe_handler_sites(old_app) if h.startswith("quoted:")]) >= 21
        assert len([h for h in _unsafe_handler_sites(old_reader) if h.startswith("quoted:")]) == 3
    else:
        # git 이력이 없는 환경: 대표 패턴으로 대신 확인
        old_app = (
            """x='<div onmouseenter="inspectMatrixCell(\\'' + esc(rName) + '\\',' + score + ')"></div>';\n"""
            """y='<button onclick="addToSynth(\\''+d.id+'\\')">';\n"""
            """z=`<button onclick="deleteThemeFromUI(${t.id}, '${esc(t.label)}')">`;\n"""
        )
        old_reader = """h='<button onclick="openRegen(\\''+esc(dc.id||'')+'\\', \\'all\\')">';\n"""
    app_hits = _unsafe_handler_sites(old_app)
    reader_hits = _unsafe_handler_sites(old_reader)
    assert len(app_hits) >= 3 and len(reader_hits) >= 1
    assert any(h.startswith("quoted:") for h in app_hits)


def test_theme_icon_escaped_in_html_sinks() -> None:
    src = (STATIC_JS / "app.js").read_text(encoding="utf-8")
    assert "${t.icon || '📁'}" not in src
    assert not re.search(r"const icon = t\.icon \|\|", src)
    assert src.count("const icon = esc(t.icon ||") == 4
