"""HTML presentation view renderers for status banners and unready state."""

from __future__ import annotations

import html


def render_unready_presentation_page(
    *,
    title: str,
    status: str,
    back_url: str | None = None,
    error_message: str | None = None,
) -> str:
    """Render a lightweight guidance HTML page when a presentation is not ready."""
    escaped_title = html.escape(title)

    if status == "composing":
        status_badge = (
            '<span style="display:inline-block;padding:4px 12px;border-radius:9999px;'
            'font-size:12px;font-weight:600;background:#1e293b;color:#38bdf8;'
            'border:1px solid #0284c7;">생성 진행 중 ⏳</span>'
        )
        heading = "프레젠테이션 슬라이드를 생성하고 있습니다"
        desc = (
            "AI가 문서의 핵심 내용을 분석하여 AsciiDoc 기반 슬라이드 덱을 작성 중입니다.<br>"
            "작업이 완료되면 자동으로 페이지가 갱신됩니다."
        )
        refresh_meta = '<meta http-equiv="refresh" content="4">'
        spinner = (
            '<div style="margin:24px 0;">'
            '<div style="display:inline-block;width:32px;height:32px;'
            'border:3px solid rgba(56,189,248,0.2);border-radius:50%;'
            'border-top-color:#38bdf8;animation:pres-spin 1s ease-in-out infinite;"></div>'
            '</div>'
            '<style>@keyframes pres-spin { to { transform: rotate(360deg); } }</style>'
        )
    elif status == "failed":
        status_badge = (
            '<span style="display:inline-block;padding:4px 12px;border-radius:9999px;'
            'font-size:12px;font-weight:600;background:#3f1414;color:#f87171;'
            'border:1px solid #b91c1c;">생성 실패 ⚠️</span>'
        )
        heading = "프레젠테이션 생성에 실패했습니다"
        escaped_err = html.escape(error_message or "작성 또는 변환 과정에서 오류가 발생했습니다.")
        desc = (
            f"오류 내용: {escaped_err}<br>"
            "문서 본문 열람 화면(WebUI)의 '프레젠테이션' 탭에서 다시 생성을 시도해 주세요."
        )
        refresh_meta = ""
        spinner = ""
    else:
        status_badge = (
            '<span style="display:inline-block;padding:4px 12px;border-radius:9999px;'
            'font-size:12px;font-weight:600;background:#1e293b;color:#94a3b8;'
            'border:1px solid #475569;">미생성 안내 ℹ️</span>'
        )
        heading = "프레젠테이션이 아직 생성되지 않았습니다"
        desc = (
            "이 문서의 프레젠테이션 슬라이드가 아직 작성되지 않았습니다.<br>"
            "문서 본문 열람 화면(WebUI)의 '프레젠테이션' 탭에서 생성을 요청할 수 있습니다."
        )
        refresh_meta = ""
        spinner = ""

    back_btn = ""
    if back_url:
        escaped_back = html.escape(back_url)
        back_btn = (
            f'<a href="{escaped_back}" style="display:inline-block;margin-top:24px;'
            'padding:10px 20px;background:#2563eb;color:#ffffff;text-decoration:none;'
            'border-radius:8px;font-size:14px;font-weight:500;transition:background 0.2s;">'
            '문서 본문 보기</a>'
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  {refresh_meta}
  <title>{escaped_title} - 프레젠테이션 안내</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: #0f172a;
      color: #f1f5f9;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 40px 32px;
      max-width: 540px;
      width: 100%;
      text-align: center;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5), 0 8px 10px -6px rgba(0, 0, 0, 0.5);
    }}
    .doc-title {{
      font-size: 15px;
      color: #94a3b8;
      margin-top: 14px;
      margin-bottom: 20px;
      word-break: break-all;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 600;
      color: #f8fafc;
      margin-bottom: 12px;
    }}
    p {{
      font-size: 14px;
      line-height: 1.6;
      color: #cbd5e1;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div>{status_badge}</div>
    <div class="doc-title">{escaped_title}</div>
    <h1>{heading}</h1>
    {spinner}
    <p>{desc}</p>
    {back_btn}
  </div>
</body>
</html>"""
