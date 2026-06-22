"""URL 정규화 + content hash (dedup 기반)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 추적용 쿼리 파라미터 — canonical_url 에서 제거
_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_reader", "utm_brand",
    "gclid", "gclsrc", "dclid", "fbclid", "yclid", "msclkid",
    "ref", "ref_src", "ref_url", "referrer", "source", "src",
    "s", "spm", "share", "igshid", "feature",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mkt_tok", "vero_id", "oly_enc_id",
    "trk", "cmpid", "ncid", "sr_share", "__twitter_impression",
}

# 호스트 prefix 로 붙는 모바일 변형 — 동일 자료를 갈라놓으므로 정규화 시 제거.
_MOBILE_PREFIXES = ("www.", "m.", "mobile.", "amp.")

# 경로 끝의 디렉터리 인덱스 파일 — 같은 페이지의 변형이므로 제거.
_INDEX_FILES = ("index.html", "index.htm", "index.php", "index.asp",
                "index.aspx", "default.aspx", "default.asp")

_ARXIV_PATH_RE = re.compile(r"^/(?:abs|pdf)/(.+)$")
_ARXIV_VER_RE = re.compile(r"v\d+$")


def _canonicalize_arxiv_path(path: str) -> str:
    """arxiv 경로를 정본 형태로: /pdf/→/abs/, 끝 .pdf 제거, 버전 접미사(vN) 제거.

    예) /abs/2606.17551v1 · /pdf/2606.17551v2.pdf · /abs/hep-th/9901001v3
        → /abs/2606.17551 · /abs/2606.17551 · /abs/hep-th/9901001
    같은 논문의 버전/형식 변형을 하나의 canonical 로 수렴(중복 적재 방지).
    """
    m = _ARXIV_PATH_RE.match(path)
    if not m:
        return path
    ident = m.group(1)
    if ident.lower().endswith(".pdf"):
        ident = ident[:-4]
    ident = _ARXIV_VER_RE.sub("", ident)
    return f"/abs/{ident}"


def canonicalize_url(url: str) -> str:
    """호스트 소문자화·모바일prefix/기본포트 제거, fragment 제거, 추적 파라미터 제거,
    인덱스 파일·끝 슬래시 정리.

    같은 자료에 도달하는 여러 URL 형태(www/m/amp, http/https, :80/:443, 추적파라미터,
    index.html, 끝 슬래시)를 하나의 키로 수렴시켜 중복 적재를 막는다.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    # userinfo 는 보존하되 기본포트(:80/:443)는 제거.
    host = parts.hostname or netloc
    for pre in _MOBILE_PREFIXES:
        if host.startswith(pre):
            host = host[len(pre):]
            break
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    netloc = host
    path = parts.path or ""
    # arxiv: 버전/형식(/pdf, .pdf, vN) 변형을 정본 /abs/<id> 로 수렴.
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        path = _canonicalize_arxiv_path(path)
    # 경로 끝 인덱스 파일 제거(/dir/index.html → /dir).
    lower_path = path.lower()
    for idx in _INDEX_FILES:
        if lower_path.endswith("/" + idx):
            path = path[: -len(idx)]
            break
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
         if k.lower() not in _TRACKING]
    query = urlencode(sorted(q))
    return urlunsplit((scheme, netloc, path, query, ""))


def content_hash(*parts: str) -> str:
    """본문(+제목 등)으로 안정적 해시. 공백 정규화 후 sha256."""
    norm = " ".join(re.sub(r"\s+", " ", (p or "").strip()) for p in parts)
    return hashlib.sha256(norm.encode("utf-8", "ignore")).hexdigest()


# ── 근사 중복(near-duplicate) 탐지: 단어 shingle MinHash ──────────────────────
# content_hash(완전일치)·canonical_url 을 비껴가는 "같은 글 다른 입구"를 잡는 3차 게이트.
# 대표 패턴(원격 DB 실측): arxiv `/abs/x` vs `/abs/xv1`(버전 접미사), 같은 글 동적요소 차이.
# 단어 4-shingle 로 MinHash 서명을 만들어 Jaccard 를 추정 → 임계 이상이면 같은 자료로 본다.
# **shingle 기반이라 짧은 partial(x.com 트윗 등)은 공통토큰만으로 안 걸린다**(실측 검증:
# 트윗쌍 토큰자카드 0.50 → shingle 추정 ~0 으로 소멸). 서로 다른 글은 큰 마진으로 분리.
MINHASH_PERM = 64    # 서명 길이(추정 분산↓ vs 비용). 64 면 표준오차 ~0.06.
SHINGLE_K = 4        # 연속 단어 묶음 크기. 클수록 우연일치↓(짧은 글은 토큰셋으로 폴백).
_MASK64 = (1 << 64) - 1
# 각 순열을 흉내내는 고정 salt(seed 별). 결정적이어야 재현/백필이 일관.
_SALTS = [((i * 0x9E3779B97F4A7C15) ^ 0xD1B54A32D192ED03) & _MASK64
          for i in range(MINHASH_PERM)]
_WORD_RE = re.compile(r"[0-9a-z가-힣]+")


def _shingles(text: str, k: int = SHINGLE_K) -> set[str]:
    toks = _WORD_RE.findall((text or "").lower())
    if len(toks) < k:
        return set(toks)
    return {" ".join(toks[i:i + k]) for i in range(len(toks) - k + 1)}


def minhash_signature(text: str) -> list[int] | None:
    """단어 shingle MinHash 서명(길이 MINHASH_PERM). 토큰이 없으면 None.

    각 shingle 을 sha1→64bit 정수로 만들고, salt XOR 후 순열별 최소값을 취한다.
    두 서명의 위치별 일치 비율이 Jaccard 유사도의 불편추정량.
    """
    sh = _shingles(text)
    if not sh:
        return None
    base = [int.from_bytes(hashlib.sha1(s.encode("utf-8", "ignore")).digest()[:8], "big")
            for s in sh]
    return [min((h ^ salt) & _MASK64 for h in base) for salt in _SALTS]


def minhash_estimate(a: list[int] | None, b: list[int] | None) -> float:
    """두 MinHash 서명에서 Jaccard 유사도 추정(위치별 일치 비율). 0.0~1.0."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(1 for i in range(n) if a[i] == b[i]) / n
