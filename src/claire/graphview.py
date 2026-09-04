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
    knowledgeManager: '__KNOWLEDGE_MANAGER__'
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


def shared_html(doc: dict, settings: Any = None) -> str:
    """공유 문서 1개를 임베드한 경량 읽기 페이지 HTML. doc = document_detail() 결과.

    문서 데이터를 JSON 으로 <script> 에 임베드한다 — `</script>`·`<` 등이 스크립트를
    조기 종료/주입하지 못하게 HTML 특수문자를 \\uXXXX 로 이스케이프(스크랩 본문 유래).
    """
    if isinstance(settings, str):
        ga_id = settings
    elif settings is None:
        from .config import get_settings

        s = get_settings()
        ga_id = getattr(s, "effective_ga_measurement_id", getattr(s, "ga_measurement_id", ""))
    else:
        ga_id = getattr(
            settings,
            "effective_ga_measurement_id",
            getattr(settings, "ga_measurement_id", ""),
        )

    doc_id = str((doc or {}).get("id", "") or "").strip()
    ga_tag = render_ga_tag(ga_id, doc_id=doc_id)

    data = _json.dumps(doc, ensure_ascii=False)
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    title = (doc.get("title") or "공유 문서").replace("<", "").replace(">", "")
    return (
        _SHARED_HTML.replace("__DATA__", data)
        .replace("__TITLE__", title)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
    )


def render_graph_html(settings: Any = None) -> str:
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
    return (
        GRAPH_HTML.replace("__SOURCE_BASE_URL__", base_url)
        .replace("__GITHUB_REPOSITORY__", repo)
        .replace("<!-- __GA_TAG__ -->", ga_tag)
        .replace("__SORCERER__", safe_sorcerer)
        .replace("__OWNER__", safe_sorcerer)
        .replace("__KNOWLEDGE_MANAGER__", safe_sorcerer)
    )


__all__ = [
    "GRAPH_HTML",
    "_SHARED_HTML",
    "dedup_clusters",
    "document_detail",
    "documents_list",
    "graph_json",
    "node_detail",
    "render_ga_tag",
    "render_graph_html",
    "shared_html",
    "synthesis_context",
    "synthesize",
]
