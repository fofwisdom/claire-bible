"""읽기전용 그래프 시각화 및 문서 뷰어 — 정적 자산 로더 + 템플릿 렌더러.

데이터 질의 로직은 `claire.store.queries`로 분리되었으며,
UI 정적 자산(CSS/JS) 및 템플릿은 `src/claire/static/`, `src/claire/templates/`에 위치합니다.
이 모듈은 하위 호환성을 위해 쿼리 함수들을 re-export하고,
단일 페이지 오프라인 번들(GRAPH_HTML, _SHARED_HTML) 및 서빙용 렌더러를 제공합니다.
"""

from __future__ import annotations

import html as _html
import json as _json
import re
from pathlib import Path
from typing import Any

# 순수 데이터 쿼리 계층 Re-export (하위 호환성 100% 보장)
from .store.queries import (
    dedup_clusters,
    document_detail,
    documents_list,
    graph_json,
    node_detail,
    synthesis_context,
    synthesize,
    theme_summary,
)

_PACKAGE_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _PACKAGE_DIR / "static"
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"


def _build_standalone_graph_html() -> str:
    """static 디렉터리와 templates/index.html을 조합하여 단일 자립형 GRAPH_HTML을 동적 생성한다."""
    theme_css = (_STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    ws_css = (_STATIC_DIR / "css" / "workspace.css").read_text(encoding="utf-8")
    reader_css = (_STATIC_DIR / "css" / "reader.css").read_text(encoding="utf-8")

    adoc_js = (_STATIC_DIR / "js" / "renderers" / "adoc_parser.js").read_text(encoding="utf-8")
    reader_js = (_STATIC_DIR / "js" / "reader.js").read_text(encoding="utf-8")
    app_js = (_STATIC_DIR / "js" / "app.js").read_text(encoding="utf-8")

    index_tmpl = (_TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")

    css_bundle = f"<style>\n{theme_css}\n{ws_css}\n{reader_css}\n</style>"
    html = re.sub(r"<link rel=stylesheet href=/static/css/[^>]+>\s*", "", index_tmpl)
    html = html.replace("</head>", f"{css_bundle}\n</head>")

    html = re.sub(r"<script>\s*window\.__CLAIRE_CONFIG\s*=[\s\S]*?</script>\s*", "", html)
    html = re.sub(r"<script src=/static/js/[^>]+></script>\s*", "", html)

    config_js = """  window.__CLAIRE_CONFIG = {
    sourceBaseUrl: '__SOURCE_BASE_URL__',
    githubRepository: '__GITHUB_REPOSITORY__',
    sorcerer: '__SORCERER__',
    owner: '__OWNER__',
    knowledgeManager: '__KNOWLEDGE_MANAGER__',
    themeMode: '__THEME_MODE__',
    themes: __THEMES_JSON__
  };"""

    js_bundle = f"<script>\n{config_js}\n\n{adoc_js}\n{reader_js}\n{app_js}\n</script>"
    html = html.replace("</body>", f"{js_bundle}\n</body>")
    return html


def _build_standalone_shared_html() -> str:
    """static 디렉터리와 templates/share.html을 조합하여 단일 자립형 _SHARED_HTML을 동적 생성한다."""
    theme_css = (_STATIC_DIR / "css" / "theme.css").read_text(encoding="utf-8")
    reader_css = (_STATIC_DIR / "css" / "reader.css").read_text(encoding="utf-8")
    adoc_js = (_STATIC_DIR / "js" / "renderers" / "adoc_parser.js").read_text(encoding="utf-8")

    share_tmpl = (_TEMPLATES_DIR / "share.html").read_text(encoding="utf-8")

    css_bundle = f"<style>\n{theme_css}\n{reader_css}\n</style>"
    html = re.sub(r"<link rel=stylesheet href=/static/css/[^>]+>\s*", "", share_tmpl)
    html = html.replace("</head>", f"{css_bundle}\n</head>")

    html = html.replace("<script src=/static/js/renderers/adoc_parser.js></script>\n", "")
    idx = html.find("<script>", html.find('id="docdata"'))
    if idx != -1:
        html = html[: idx + 8] + f"\n{adoc_js}\n" + html[idx + 8 :]
    return html


GRAPH_HTML: str = _build_standalone_graph_html()
_SHARED_HTML: str = _build_standalone_shared_html()


def render_ga_tag(measurement_id: str, doc_id: str = "") -> str:
    """Google Analytics 4 (GA4 / gtag.js) 태그 스니펫을 생성한다.

    측정 ID가 없거나 유효하지 않으면 빈 문자열을 반환한다.
    URL 쿼리 파라미터(?t=..., ?s=...) 유출을 방지하기 위해 page_location을
    origin + pathname (또는 /p/<doc_id>)으로 정제하여 전송한다.
    """
    cleaned_id = str(measurement_id or "").strip()
    if not cleaned_id or not re.fullmatch(r"^[A-Za-z0-9_-]+$", cleaned_id):
        return ""
    clean_doc_id = str(doc_id or "").strip()
    if clean_doc_id:
        loc_expr = f"window.location.origin + '/p/{clean_doc_id}'"
    else:
        loc_expr = "window.location.origin + window.location.pathname"
    return (
        "<!-- Google Analytics (GA4) -->\n"
        f'<script async src="https://www.googletagmanager.com/gtag/js?id={cleaned_id}"></script>\n'
        "<script>\n"
        "  window.dataLayer = window.dataLayer || [];\n"
        "  function gtag(){dataLayer.push(arguments);}\n"
        "  gtag('js', new Date());\n"
        f'  gtag("config", "{cleaned_id}", {{\n'
        f'    page_location: {loc_expr},\n'
        f'    cookie_domain: window.location.hostname,\n'
        f'    cookie_flags: "SameSite=Lax;Secure"\n'
        f'  }});\n'
        "</script>"
    )


def sanitize_preview_text(text: str | None, max_chars: int = 220) -> str:
    """Markdown, AsciiDoc, LaTeX, HTML 서식을 제거하고 단일 행으로 정규화하여 절단한다."""
    if not text:
        return ""
    s = _html.unescape(str(text))

    # 1. 코드 블록 및 블록 선언 제거
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"(\+\+\+\+|----|\.\.\.\.|====|\|===)[\s\S]*?\1", " ", s)
    s = re.sub(r"^\s*\[(?:latexmath|stem|asciimath|source[^\]]*)\]\s*$", " ", s, flags=re.MULTILINE)

    # 2. 수식 문법 정제 (stem:[...], latexmath:[...], asciimath:[...])
    s = re.sub(r"(?:stem|latexmath|asciimath):\[(.*?)\]", r"\1", s)
    s = re.sub(r"\$\$([\s\S]*?)\$\$", r"\1", s)
    s = re.sub(r"\\\[([\s\S]*?)\\\]", r"\1", s)
    s = re.sub(r"\$([^\$\n]+)\$", r"\1", s)
    s = re.sub(r"\\\((.*?)\\\)", r"\1", s)

    # 3. 링크 및 이미지 마크업 정제 (대체 텍스트만 보존)
    s = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"image::?[^\[]*\[(.*?)\]", r"\1", s)
    s = re.sub(r"(?:https?://\S+|link:\S+)\[(.*?)\]", r"\1", s)

    # 4. 헤딩, 인용, Admonition, 인라인 서식(*, _, `, +) 제거
    s = re.sub(r"^\s*#{1,6}\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*={1,6}\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*>\s+", " ", s, flags=re.MULTILINE)
    s = re.sub(r"\b(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION):\s*", " ", s)

    # 5. 인라인 서식 기호 제거 (매칭되는 쌍만 제거하여 수학 기호 +, _ 등 보존)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)

    # 6. HTML 태그 제거 (스크립트, 스타일 태그는 내용 포함 제거)
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"<[a-zA-Z/][^>]*>", " ", s)

    # 7. 연속 공백 정규화
    s = re.sub(r"\s+", " ", s).strip()

    # 8. 길이 제한 및 말줄임표 처리
    if len(s) > max_chars:
        cut = s[:max_chars]
        last_space = cut.rfind(" ")
        if last_space > int(max_chars * 0.75):
            cut = cut[:last_space]
        s = cut.rstrip(" .,!?;:") + "…"

    return s


def resolve_preview_image(doc: dict, base_url: str = "") -> tuple[str, str]:
    """미리보기용 대표 이미지 URL과 twitter:card 타입 ('summary' | 'summary_large_image')을 반환한다."""
    base = base_url.rstrip("/") if base_url else ""
    meta = (doc or {}).get("meta") or {}

    candidates: list[str] = []

    # 1. meta.get("og_image") 또는 meta.get("image")
    for k in ("og_image", "image", "thumbnail"):
        v = (doc or {}).get(k) or meta.get(k)
        if v and isinstance(v, str) and v.strip():
            candidates.append(v.strip())

    # 2. meta.images 목록
    images = meta.get("images") or []
    if isinstance(images, list):
        for im in images:
            if isinstance(im, dict):
                loc = str(im.get("local") or "").strip()
                if loc:
                    rel = loc.lstrip("/")
                    candidates.append(f"/image?p={rel}")
                u = str(im.get("url") or "").strip()
                if u:
                    candidates.append(u)

    # 3. detail 본문 내 첫 번째 이미지 탐색
    detail = str((doc or {}).get("detail") or "")
    if detail:
        m_md = re.search(r"!\[.*?\]\((https?://[^\s\)]+)\)", detail)
        if m_md:
            candidates.append(m_md.group(1).strip())
        m_adoc = re.search(r"image::?(https?://[^\s\[]+)\[", detail)
        if m_adoc:
            candidates.append(m_adoc.group(1).strip())

    for c in candidates:
        if c.startswith(("http://", "https://")):
            return c, "summary_large_image"
        if c.startswith("/"):
            return f"{base}{c}" if base else c, "summary_large_image"

    # 4. 문서 이미지 부재 시 고해상도 브랜드 아이콘 (512x512) 폴백
    icon_path = "/icon?p=android-chrome-512x512.png"
    return f"{base}{icon_path}" if base else icon_path, "summary"


def extract_knowledge_node_tags(doc: dict) -> list[str]:
    """문서의 지식 노드(nodes)에서 태그 목록을 추출하고 정제한다."""
    nodes = (doc or {}).get("nodes") or []
    tags: list[str] = []
    seen: set[str] = set()

    for n in nodes:
        if isinstance(n, dict):
            name = str(n.get("label") or n.get("name") or "").strip()
        elif isinstance(n, str):
            name = n.strip()
        else:
            continue
        if name and name not in seen:
            seen.add(name)
            tags.append(name)
    return tags


def render_open_graph_tags(
    doc: dict,
    base_url: str = "",
    share_token: str = "",
) -> str:
    """텔레그램, 마스토돈, 슬랙, 디스코드, X 등과 호환되는 Open Graph 및 Twitter Cards 메타태그 스니펫을 생성한다."""
    base = base_url.rstrip("/") if base_url else ""

    raw_title = str((doc or {}).get("title") or "공유 문서").strip()
    title = sanitize_preview_text(raw_title, max_chars=100) or "공유 문서"

    raw_desc = (
        (doc or {}).get("summary")
        or (doc or {}).get("detail")
        or (doc or {}).get("directive")
        or ""
    )
    desc = sanitize_preview_text(raw_desc, max_chars=220)
    if not desc:
        desc = "Claire Bible에서 공유된 지식 문서입니다."

    image_url, card_type = resolve_preview_image(doc, base_url=base)

    author = str((doc or {}).get("author") or "").strip()
    published_at = str((doc or {}).get("published_at") or "").strip()

    if share_token:
        page_url = f"{base}/p?s={share_token}" if base else f"/p?s={share_token}"
    else:
        page_url = f"{base}/p" if base else "/p"

    esc_title = _html.escape(title, quote=True)
    esc_desc = _html.escape(desc, quote=True)
    esc_image = _html.escape(image_url, quote=True)
    esc_url = _html.escape(page_url, quote=True)
    esc_author = _html.escape(author, quote=True) if author else ""
    esc_pub = _html.escape(published_at, quote=True) if published_at else ""

    tags = [
        "<!-- Open Graph / Web Preview (Telegram, Mastodon, KakaoTalk, etc.) -->",
        f'<meta name="description" content="{esc_desc}"/>',
        '<meta property="og:site_name" content="Claire Bible"/>',
        '<meta property="og:type" content="article"/>',
        f'<meta property="og:title" content="{esc_title}"/>',
        f'<meta property="og:description" content="{esc_desc}"/>',
        f'<meta property="og:url" content="{esc_url}"/>',
        f'<meta property="og:image" content="{esc_image}"/>',
        f'<meta property="og:image:alt" content="{esc_title}"/>',
    ]

    if esc_author:
        tags.append(f'<meta property="article:author" content="{esc_author}"/>')
        tags.append(f'<meta name="author" content="{esc_author}"/>')
    if esc_pub:
        tags.append(f'<meta property="article:published_time" content="{esc_pub}"/>')

    node_tags = extract_knowledge_node_tags(doc)
    if node_tags:
        esc_keywords = _html.escape(", ".join(node_tags), quote=True)
        tags.append(f'<meta name="keywords" content="{esc_keywords}"/>')
        for t in node_tags:
            esc_t = _html.escape(t, quote=True)
            tags.append(f'<meta property="article:tag" content="{esc_t}"/>')

    tags.extend([
        "<!-- Twitter Cards (X, Slack, Discord, etc.) -->",
        f'<meta name="twitter:card" content="{card_type}"/>',
        f'<meta name="twitter:title" content="{esc_title}"/>',
        f'<meta name="twitter:description" content="{esc_desc}"/>',
        f'<meta name="twitter:image" content="{esc_image}"/>',
        f'<link rel="canonical" href="{esc_url}"/>',
    ])

    return "\n".join(tags)


def shared_html(
    doc: dict,
    settings: Any = None,
    *,
    base_url: str = "",
    share_token: str = "",
) -> str:
    """공유 문서 1개를 임베드한 경량 읽기 페이지 HTML. doc = document_detail() 결과.

    문서 데이터를 JSON 으로 <script> 에 임베드한다 — `</script>`·`<` 등이 스크립트를
    조기 종료/주입하지 못하게 HTML 특수문자를 \\uXXXX 로 이스케이프(스크랩 본문 유래).
    Open Graph 및 Twitter Card 메타태그를 주입하여 텔레그램/마스토돈 등의 미리보기를 지원한다.
    """
    if isinstance(settings, str):
        ga_id = settings
        resolved_base_url = base_url
    elif settings is None:
        from .config import get_settings

        s = get_settings()
        ga_id = getattr(s, "effective_ga_measurement_id", getattr(s, "ga_measurement_id", ""))
        resolved_base_url = base_url or getattr(s, "public_url", "")
    else:
        ga_id = getattr(
            settings,
            "effective_ga_measurement_id",
            getattr(settings, "ga_measurement_id", ""),
        )
        resolved_base_url = base_url or getattr(settings, "public_url", "")

    doc_id = str((doc or {}).get("id", "") or "").strip()
    ga_tag = render_ga_tag(ga_id, doc_id=doc_id)
    og_tags = render_open_graph_tags(doc, base_url=resolved_base_url, share_token=share_token)

    data = _json.dumps(doc, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = (doc.get("title") or "공유 문서").replace("<", "").replace(">", "")
    return (
        _SHARED_HTML.replace("__DATA__", data)
        .replace("__TITLE__", title)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("<!-- __OG_TAGS__ -->", og_tags)
    )


def render_graph_html(
    settings: Any = None,
    *,
    include_private: bool = True,
    collaborator: bool = False,
) -> str:
    """Settings 의 저장소 변수, 관리자 변수 및 GA 설정을 반영하여 완성된 그래프 HTML 을 반환한다."""
    if settings is None:
        from .config import get_settings

        s = get_settings()
    else:
        s = settings
    repo = getattr(
        s,
        "effective_github_repository",
        getattr(s, "github_repository", "fofwisdom/claire-bible"),
    )
    base_url = getattr(
        s,
        "effective_source_base_url",
        getattr(s, "source_base_url", f"https://github.com/{repo}"),
    )
    if not base_url:
        base_url = f"https://github.com/{repo}"
    ga_id = getattr(
        s,
        "effective_ga_measurement_id",
        getattr(s, "ga_measurement_id", ""),
    )
    ga_tag = render_ga_tag(ga_id)
    sorcerer = getattr(
        s,
        "effective_sorcerer",
        getattr(
            s,
            "sorcerer",
            getattr(s, "effective_owner", getattr(s, "owner", "owner")),
        ),
    )
    raw_sorcerer = str(sorcerer).strip() if sorcerer is not None else ""
    if not raw_sorcerer:
        raw_sorcerer = "owner"

    safe_sorcerer = _html.escape(raw_sorcerer, quote=True)

    is_multi = bool(getattr(s, "multi_theme", False))
    theme_mode = "multi" if is_multi else "single"
    if is_multi:
        from .store.theme import get_theme_manager

        tm = get_theme_manager(s)
        themes_list = [
            t.to_dict()
            for t in tm.list_themes(
                include_private=include_private, collaborator=collaborator
            )
        ]
    else:
        themes_list = [
            {
                "id": 0,
                "seq": 0,
                "label": "기본 지식베이스",
                "description": "일반 수집 자료 및 기본 지식",
                "icon": "📚",
                "is_default": True,
                "is_public": True,
                "is_collaborator_accessible": False,
            }
        ]
    themes_json = _json.dumps(themes_list, ensure_ascii=False)
    themes_json_safe = (
        themes_json.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    if is_multi and len(themes_list) > 1:
        picker_style_attr = 'style="display:inline-flex"'
    else:
        picker_style_attr = 'style="display:none"'

    return (
        GRAPH_HTML.replace("__SOURCE_BASE_URL__", base_url)
        .replace("__GITHUB_REPOSITORY__", repo)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("__SORCERER__", safe_sorcerer)
        .replace("__OWNER__", safe_sorcerer)
        .replace("__KNOWLEDGE_MANAGER__", safe_sorcerer)
        .replace("__THEME_MODE__", theme_mode)
        .replace("__THEMES_JSON__", themes_json_safe)
        .replace("__THEME_PICKER_STYLE_ATTR__", picker_style_attr)
    )


__all__ = [
    "GRAPH_HTML",
    "_SHARED_HTML",
    "dedup_clusters",
    "document_detail",
    "documents_list",
    "extract_knowledge_node_tags",
    "graph_json",
    "node_detail",
    "render_ga_tag",
    "render_graph_html",
    "render_open_graph_tags",
    "resolve_preview_image",
    "sanitize_preview_text",
    "shared_html",
    "synthesis_context",
    "synthesize",
    "theme_summary",
]
