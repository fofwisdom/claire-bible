"""AsciiDoc to reveal.js presentation compiler attribute preprocessor.

Injects reveal.js configuration attributes, custom CSS, and asset paths
into LLM-authored AsciiDoc presentations prior to asciidoctor compilation.
"""

from __future__ import annotations





def prepare_presentation_adoc_for_compile(
    adoc: str,
    *,
    theme: str = "night",
    transition: str = "slide",
    customcss: str | None = "/static/css/reveal-claire.css",
    revealjsdir: str | None = "/static/vendor/reveal.js",
) -> str:
    """LLM이 저작한 AsciiDoc에 컴파일용 필수 reveal.js 속성(customcss, revealjsdir, 테마 등)을 보정한다."""
    lines = adoc.splitlines()
    header_attrs: dict[str, str] = {
        ":revealjs_theme:": f":revealjs_theme: {theme}",
        ":revealjs_transition:": f":revealjs_transition: {transition}",
        ":revealjs_center:": ":revealjs_center: false",
        ":revealjs_width:": ":revealjs_width: 1280",
        ":revealjs_height:": ":revealjs_height: 720",
        ":revealjs_margin:": ":revealjs_margin: 0.04",
        ":revealjs_pdfseparatefragments:": ":revealjs_pdfseparatefragments: false",
        ":revealjs_pdfmaxpagesperslide:": ":revealjs_pdfmaxpagesperslide: 1",
        ":source-highlighter:": ":source-highlighter: highlight.js",
        ":icons:": ":icons: font",
    }
    if customcss:
        header_attrs[":customcss:"] = f":customcss: {customcss}"
    if revealjsdir:
        header_attrs[":revealjsdir:"] = f":revealjsdir: {revealjsdir}"

    processed_lines: list[str] = []
    found_keys = set()
    header_started = False
    header_ended = False

    for line in lines:
        stripped = line.strip()
        if not header_ended:
            if stripped.startswith("= "):
                header_started = True
                processed_lines.append(line)
                continue
            if stripped.startswith(":"):
                header_started = True
                matched_attr = False
                for k in header_attrs:
                    if stripped.startswith(k):
                        processed_lines.append(header_attrs[k])
                        found_keys.add(k)
                        matched_attr = True
                        break
                if matched_attr:
                    continue
                processed_lines.append(line)
                continue
            if header_started or stripped.startswith("== ") or stripped.startswith("=== "):
                for k, v in header_attrs.items():
                    if k not in found_keys:
                        processed_lines.append(v)
                        found_keys.add(k)
                if stripped:
                    processed_lines.append("")
                header_ended = True
                processed_lines.append(line)
                continue
        processed_lines.append(line)

    if not header_ended:
        for k, v in header_attrs.items():
            if k not in found_keys:
                processed_lines.append(v)

    return "\n".join(processed_lines)
