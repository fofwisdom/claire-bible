"""x.com(트위터) fetcher — fxtwitter JSON API 로 트윗 본문을 실제로 가져온다.

기존 라우터는 x.com 을 'v1 부분 처리'로 두어 본문 스크랩 없이 URL 자체를 partial
Note(title='x.com post', raw_text=URL)로 적재했다 → 제목·내용이 모두 빈약했다.

x.com 은 로그인·JS 가드로 정적 스크랩이 거의 불가능하다. 그래서 공개 미러 API 인
fxtwitter(api.fxtwitter.com)를 1차로 쓴다. 트윗 JSON(text/author/created_at/quote
/media)을 받아 제대로 된 Document 를 만든다. 실패 시 vxtwitter 로 폴백하고, 둘 다
죽으면 일반 web fetcher(scrapling 포함)로 최후 시도한다.

제목: 트윗엔 제목이 없으므로 「작성자 — 본문 첫 줄 요약」으로 합성한다(빈약한
'x.com post' 대체). 인용(quote)·답글(replying_to)·이미지 alt 텍스트까지 본문에 합쳐
온톨로지 추출이 풍부해지도록 한다.
"""

from __future__ import annotations

import re

from ...config import get_settings
from ...extract.table_budget import slice_document_text
from ...ontology.base import Document
from ..normalize import canonicalize_url, content_hash
from .base import FetchError

_STATUS_RE = re.compile(r"(?:^|/)([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)")
_STATUS_ONLY_RE = re.compile(r"/status(?:es)?/(\d+)")
# 핸들이 아닌 트위터 예약 경로 — screen_name 으로 오인하면 안 된다(/i/web/status/...).
_RESERVED = {"i", "web", "intent", "hashtag", "search", "home", "messages", "notifications"}

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# fxtwitter/vxtwitter 모두 호환 JSON 스키마(tweet.{text,author,...})를 돌려준다.
_API_HOSTS = ("api.fxtwitter.com", "api.vxtwitter.com")
# 호스트 목록을 몇 바퀴 재순회할지(미러의 일시적 rate-limit 404 흡수).
_ROUNDS = 3


def parse_status(url: str) -> tuple[str | None, str | None]:
    """x.com URL → (screen_name, status_id). 둘 중 하나만 못 찾으면 그 자리는 None."""
    m = _STATUS_RE.search(url)
    if m:
        screen = m.group(1)
        if screen.lower() in _RESERVED:
            return None, m.group(2)
        return screen, m.group(2)
    m = _STATUS_ONLY_RE.search(url)
    if m:
        return None, m.group(1)
    return None, None


def fetch_xcom(url: str, *, full_content: bool = False) -> Document:
    screen, sid = parse_status(url)
    if not sid:
        # status id 가 없으면(프로필·검색 등) 트윗 API 대상이 아니다 → web 으로.
        from .web import fetch_web

        return fetch_web(url, full_content=full_content)

    tweet, via = _fetch_api(screen, sid)
    if tweet is None:
        # API 미러가 모두 실패 → 일반 web fetcher(scrapling 포함) 최후 시도.
        from .web import fetch_web

        return fetch_web(url, full_content=full_content)

    return _build_document(url, tweet, via=via, full_content=full_content)


def _fetch_api(screen: str | None, sid: str) -> tuple[dict | None, str | None]:
    """fxtwitter → vxtwitter 순으로 트윗 JSON(dict)을 가져온다. (tweet, via_host).

    미러는 rate limit 시 *같은 트윗에도 일시적으로 404* 를 돌려준다(실측: fxtwitter
    5회 중 2회 404, 같은 id 가 200↔404 반복). 따라서 한 호스트 1회로 단정하지 않고
    호스트 목록을 _ROUNDS 회 순회하며 재시도한다 — 라운드 사이 짧은 backoff.

    fxtwitter 는 X 롱폼 아티클 *전문*(article.content)을 주지만 vxtwitter 는
    preview 만 준다. 그래서 fxtwitter 응답을 끝까지 노리고, vxtwitter 응답은 임시
    보관했다가 fxtwitter 가 모든 라운드에서 실패했을 때만 폴백으로 쓴다.
    모두 실패하면 (None, None)(→ web 폴백, 진짜 삭제/비공개 트윗만 여기 도달).
    """
    import time

    primary = _API_HOSTS[0]
    fallback: tuple[dict, str] | None = None
    for attempt in range(_ROUNDS):
        for host in _API_HOSTS:
            tweet = _try_host(host, screen, sid)
            if tweet is None:
                continue
            if host == primary:
                return tweet, host  # fxtwitter = 풍부(article 전문 포함) → 즉시 채택
            if fallback is None:
                fallback = (tweet, host)  # vxtwitter 등 → 보관(fxtwitter 끝내 실패시만)
        if attempt < _ROUNDS - 1:
            time.sleep(0.8)  # rate limit 완화 대기 후 재순회
    return fallback if fallback else (None, None)


def _try_host(host: str, screen: str | None, sid: str) -> dict | None:
    """한 미러 호스트에서 트윗 JSON 1회 시도. 실패(404/5xx/HTML/timeout) 시 None."""
    import httpx

    path = f"{screen}/status/{sid}" if screen else f"status/{sid}"
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    try:
        with httpx.Client(follow_redirects=True, timeout=12, headers=headers) as c:
            resp = c.get(f"https://{host}/{path}")
        if resp.status_code != 200:
            return None  # 404(일시 rate limit 포함)/5xx
        # vxtwitter 는 실패해도 200+HTML('Failed to scan…')을 준다 → 가드.
        if "json" not in (resp.headers.get("content-type") or "").lower():
            return None
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None  # timeout/네트워크
    # fxtwitter: {code,message,tweet:{...}} / vxtwitter: 평탄한 {text,...}
    tweet = data.get("tweet") if isinstance(data, dict) else None
    if tweet is None and isinstance(data, dict) and (
            data.get("text") or data.get("full_text")):
        tweet = _normalize_vx(data)
    if isinstance(tweet, dict) and (
            tweet.get("text") or tweet.get("media") or tweet.get("article")):
        return tweet
    return None


def _normalize_vx(data: dict) -> dict:
    """vxtwitter 평탄 스키마 → fxtwitter 호환 dict 로 정규화(필요 필드만)."""
    out = {
        "text": data.get("text") or data.get("full_text") or "",
        "author": {
            "name": data.get("user_name"),
            "screen_name": data.get("user_screen_name"),
        },
        "created_at": data.get("date"),
        "created_timestamp": data.get("date_epoch"),
        "lang": data.get("lang"),
        "url": data.get("tweetURL"),
        "replying_to": data.get("replyingTo"),
        "likes": data.get("likes"),
        "retweets": data.get("retweets"),
        "replies": data.get("replies"),
        # vxtwitter article 은 {title, preview_text, image} 만(content 없음) → preview 사용.
        "article": data.get("article") if isinstance(data.get("article"), dict) else None,
    }
    # 인용(qrt): vxtwitter 는 평탄한 qrt 객체(qrtUser/text 등) 또는 qrtURL 만 줄 수 있다.
    qrt = data.get("qrt")
    if isinstance(qrt, dict):
        out["quote"] = {
            "text": qrt.get("text") or "",
            "author": {"screen_name": (qrt.get("qrtUser") or {}).get("screen_name")
                       if isinstance(qrt.get("qrtUser"), dict) else qrt.get("user_screen_name")},
        }
    # 이미지: media_extended[].url / mediaURLs — alt 는 보통 없음.
    return out


def _build_document(url: str, tweet: dict, *, via: str | None = None, full_content: bool = False) -> Document:
    author = tweet.get("author") or {}
    name = (author.get("name") or "").strip()
    screen = (author.get("screen_name") or "").strip()
    who = name or (f"@{screen}" if screen else "")
    if name and screen:
        who = f"{name} (@{screen})"

    body = (tweet.get("text") or "").strip()

    # X 롱폼 아티클(x.com/i/article/...) — 트윗 text 는 비어있거나 article URL 뿐이고
    #   실제 내용은 article 필드에 있다. 이걸 무시하면 본문이 URL 하나로 빈약해진다
    #   (실관측). title + content(blocks) 전문을 본문에 싣는다.
    article = tweet.get("article")
    article_title = ""
    art_body = ""
    if isinstance(article, dict):
        article_title = (article.get("title") or "").strip()
        art_body = _article_text(article)
        # 트윗 text 가 그 아티클 URL 만 담고 있으면 중복이므로 본문에서 뺀다.
        if body and re.fullmatch(r"https?://\S*/i/article/\d+\S*", body):
            body = ""

    parts: list[str] = []
    if body:
        parts.append(body)
    if article_title:
        parts.append(article_title)
    if art_body:
        parts.append(art_body)

    # 이미지 alt 텍스트(있으면) — 시각 컨텍스트를 본문에 보강.
    for ph in _media_alts(tweet):
        parts.append(f"[이미지: {ph}]")

    # 답글 대상(replying_to) 표시 — 맥락.
    reply_to = tweet.get("replying_to")
    if reply_to:
        parts.insert(0, f"(@{reply_to} 에게 보내는 답글)")

    # 인용(quote) 트윗 본문 합치기.
    quote = tweet.get("quote")
    if isinstance(quote, dict):
        q_auth = (quote.get("author") or {}).get("screen_name") or ""
        q_text = (quote.get("text") or "").strip()
        if q_text:
            head = f"@{q_auth}" if q_auth else "원문"
            parts.append(f"\n— 인용({head}): {q_text}")

    text = "\n".join(parts).strip()
    if not text:
        raise FetchError(f"x.com 트윗 본문이 비어있음: {url}")

    # 제목: 아티클이면 그 제목을 우선(트윗 text 가 비어있으므로), 아니면 본문 첫 줄.
    title = _make_title(who, article_title or body, tweet)
    published = _published_at(tweet)
    lang = tweet.get("lang")

    canonical_src = tweet.get("url") or url
    settings = get_settings()
    budget = 0 if full_content else settings.raw_char_budget
    raw_text, is_truncated, orig_chars, raw_chars = slice_document_text(
        text or "", budget, strategy=settings.slicing_strategy
    )
    return Document(
        url=url,
        canonical_url=canonicalize_url(canonical_src),
        title=title,
        author=who or None,
        published_at=published,
        raw_text=raw_text,
        source_type="xcom",
        content_hash=content_hash(title or "", text),
        lang=lang,
        meta={
            "screen_name": screen or None,
            "stats": {
                "likes": tweet.get("likes"),
                "retweets": tweet.get("retweets"),
                "replies": tweet.get("replies"),
                "views": tweet.get("views"),
            },
            "fetch_via": via or "fxtwitter",
            "is_article": bool(article_title),
            "raw_truncated": is_truncated,
            "orig_chars": orig_chars,
            "raw_chars": raw_chars,
        },
    )


def _article_text(article: dict) -> str:
    """X 아티클 본문 추출. content(Draft.js {blocks:[{text}]}) 전문, 없으면 preview."""
    content = article.get("content")
    if isinstance(content, str):
        try:
            import json

            content = json.loads(content)
        except Exception:  # noqa: BLE001
            content = None
    if isinstance(content, dict):
        blocks = content.get("blocks") or []
        texts = [b.get("text", "") for b in blocks
                 if isinstance(b, dict) and b.get("text")]
        if texts:
            return "\n".join(texts)
    return (article.get("preview_text") or "").strip()


def _media_alts(tweet: dict) -> list[str]:
    media = tweet.get("media") or {}
    alts: list[str] = []
    photos = media.get("photos") or media.get("all") or []
    for p in photos:
        if isinstance(p, dict):
            a = (p.get("altText") or p.get("alt_text") or "").strip()
            if a:
                alts.append(a[:200])
    return alts


def _make_title(who: str, body: str, tweet: dict) -> str:
    """트윗엔 제목이 없으므로 「작성자 — 본문 첫 줄」로 합성. 빈약하면 작성자만."""
    first = ""
    for line in (body or "").splitlines():
        line = line.strip()
        if line:
            first = line
            break
    # URL·핸들만 있는 첫 줄은 요약으로 부적합 → 다음 의미있는 토큰까지 펼침.
    if first and re.fullmatch(r"(https?://\S+|@\w+|\s)+", first):
        flat = " ".join(body.split())
        first = flat
    snippet = first[:90].rstrip()
    if len(first) > 90:
        snippet += "…"
    if who and snippet:
        return f"{who}: {snippet}"
    if snippet:
        return snippet
    if who:
        return f"{who} 의 트윗"
    return "x.com 트윗"


def _published_at(tweet: dict) -> str | None:
    ts = tweet.get("created_timestamp")
    if ts:
        try:
            from datetime import datetime, timezone

            return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except Exception:  # noqa: BLE001
            pass
    return tweet.get("created_at") or None
