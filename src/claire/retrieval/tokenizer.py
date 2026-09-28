"""검색 토크나이저 및 다국어 형태소 분석 인터페이스.

- 한국어 형태소 분석: kiwipiepy>=0.24.0 (Apache License 2.0) 기반 KoreanKiwiTokenizer
- 다국어/폴백: 정규식 기반 SimpleTokenizer (kiwipiepy 미설치 또는 비한글 텍스트 고속 처리)
- 엔티티/도메인 용어 사용자 사전(User Dictionary) 동적 등록 지원 (add_user_words)
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)

_RE_HANGUL = re.compile(r"[가-힣]")
_RE_ALPHANUMERIC = re.compile(r"[0-9A-Za-z가-힣]+")

# FTS 검색 색인에 의미 있는 한국어 실질 형태소 품사 태그
# - 체언: NNG(일반명사), NNP(고유명사), NR(수사), NP(대명사)
# - 외래어/기호: SL(외국어), SH(한자), SN(숫자)
# - 어근: XR(어근)
# - 용언: VV(동사), VA(형용사)
_MEANINGFUL_TAGS = {
    "NNG", "NNP", "NR", "NP",
    "SL", "SH", "SN", "XR",
    "VV", "VA",
}

# 고빈도 보조용언 및 범용 동사/형용사 불용어 (어간 수준 필터링)
_VERB_STOPWORDS = {
    "하", "되", "있", "없", "보", "오", "가", "주", "대하", "위하",
    "따르", "이루", "시키", "만들", "가지", "받", "생기", "통하",
}


class Tokenizer(Protocol):
    """검색 토크나이저 인터페이스 (다국어 확장 가능)."""

    def extract_search_tokens(self, text: str) -> list[str]:
        """텍스트에서 FTS 검색에 유효한 토큰 목록을 추출 (중복 제거 및 순서 보존)."""
        ...

    def add_user_words(self, words: Iterable[str]) -> int:
        """사용자 사전에 고유명사/도메인 단어를 등록하고 새로 추가된 단어 수를 반환."""
        ...


class SimpleTokenizer:
    """정규식 기반 기본 토크나이저 (언어 무관, kiwipiepy 미설치 시 무중단 폴백)."""

    def __init__(self) -> None:
        self._user_words: set[str] = set()
        self._lock = threading.Lock()

    def extract_search_tokens(self, text: str) -> list[str]:
        if not text:
            return []
        tokens = _RE_ALPHANUMERIC.findall(text)
        seen: set[str] = set()
        out: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def add_user_words(self, words: Iterable[str]) -> int:
        with self._lock:
            added = 0
            for w in words:
                cleaned = (w or "").strip()
                if cleaned and cleaned not in self._user_words:
                    self._user_words.add(cleaned)
                    added += 1
            return added


class KoreanKiwiTokenizer:
    """kiwipiepy (Apache 2.0) 기반 한국어 형태소 분석 토크나이저."""

    def __init__(self) -> None:
        from kiwipiepy import Kiwi

        self._kiwi = Kiwi()
        self._user_words: set[str] = set()
        self._lock = threading.Lock()

    def extract_search_tokens(self, text: str) -> list[str]:
        if not text:
            return []

        tokens: list[str] = []
        seen: set[str] = set()

        def _add_token(val: str) -> None:
            for sub in _RE_ALPHANUMERIC.findall(val):
                if sub and sub not in seen:
                    seen.add(sub)
                    tokens.append(sub)

        # 1. Kiwi 형태소 분석
        try:
            morphemes = self._kiwi.tokenize(text, normalize_coda=True)
            for m in morphemes:
                if m.tag in _MEANINGFUL_TAGS:
                    if m.tag in {"VV", "VA"} and m.form in _VERB_STOPWORDS:
                        continue
                    # 복합명사 등 내부에 공백이 포함된 경우 분절 및 결합 형태 모두 색인
                    if " " in m.form:
                        for part in m.form.split():
                            _add_token(part)
                        _add_token(m.form.replace(" ", ""))
                    else:
                        _add_token(m.form)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Kiwi tokenize fallback due to error: %s", exc)

        # 2. 원문 내 표면어(Surface words) 보강 (미등록 신조어 및 복합 영숫자 유실 방지)
        raw_words = _RE_ALPHANUMERIC.findall(text)
        for rw in raw_words:
            if len(rw) > 1 and rw not in seen:
                # 한글이 포함된 표면어는 원형 복합어로 보존
                if _RE_HANGUL.search(rw):
                    seen.add(rw)
                    tokens.append(rw)
                elif len(rw) > 2:
                    seen.add(rw)
                    tokens.append(rw)

        return tokens

    def add_user_words(self, words: Iterable[str]) -> int:
        added = 0
        with self._lock:
            for w in words:
                cleaned = (w or "").strip()
                # 2글자 이상인 경우에만 사용자 사전 등록
                if len(cleaned) < 2 or cleaned in self._user_words:
                    continue
                try:
                    if self._kiwi.add_user_word(cleaned, "NNP"):
                        self._user_words.add(cleaned)
                        added += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to add user word %r: %s", cleaned, exc)
        return added


class SearchTokenizer:
    """다국어 및 국제화(i18n) 확장을 고려한 통합 검색 토크나이저 파사드."""

    def __init__(self) -> None:
        self._kiwi_tokenizer: KoreanKiwiTokenizer | None = None
        self._fallback_tokenizer = SimpleTokenizer()
        self._has_kiwi = False

        try:
            self._kiwi_tokenizer = KoreanKiwiTokenizer()
            self._has_kiwi = True
            logger.info("KoreanKiwiTokenizer initialized successfully (kiwipiepy Apache 2.0).")
        except (ImportError, Exception) as exc:
            self._has_kiwi = False
            logger.info("kiwipiepy unavailable (%s); using SimpleTokenizer fallback.", exc)

        # 파일 기반 사용자 사전이 존재하면 초기 로드
        self._load_file_dictionary()

    @property
    def has_morphological_analyzer(self) -> bool:
        return self._has_kiwi

    def extract_search_tokens(self, text: str) -> list[str]:
        if not text:
            return []

        # 1. 한글이 포함되어 있고 Kiwi가 사용 가능하면 형태소 분석 경로
        if self._has_kiwi and self._kiwi_tokenizer is not None and _RE_HANGUL.search(text):
            return self._kiwi_tokenizer.extract_search_tokens(text)

        # 2. 비한글(순수 영문/숫자 등)이거나 Kiwi 미설치 시 고속 정규식 경로
        return self._fallback_tokenizer.extract_search_tokens(text)

    def add_user_words(self, words: Iterable[str]) -> int:
        added = self._fallback_tokenizer.add_user_words(words)
        if self._has_kiwi and self._kiwi_tokenizer is not None:
            self._kiwi_tokenizer.add_user_words(words)
        return added

    def _load_file_dictionary(self) -> None:
        candidates = [
            Path("data/user_dict.txt"),
            Path("data/dict/user_dict.txt"),
        ]
        for p in candidates:
            if p.is_file():
                try:
                    lines = [line.strip() for line in p.read_text(encoding="utf-8").splitlines()]
                    valid = [w for w in lines if w and not w.startswith("#")]
                    count = self.add_user_words(valid)
                    logger.info("Loaded %d user words from %s", count, p)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to load user dictionary from %s: %s", p, exc)


_GLOBAL_TOKENIZER: SearchTokenizer | None = None
_GLOBAL_LOCK = threading.Lock()


def get_search_tokenizer() -> SearchTokenizer:
    """싱글톤 검색 토크나이저 인스턴스를 반환."""
    global _GLOBAL_TOKENIZER
    if _GLOBAL_TOKENIZER is None:
        with _GLOBAL_LOCK:
            if _GLOBAL_TOKENIZER is None:
                _GLOBAL_TOKENIZER = SearchTokenizer()
    return _GLOBAL_TOKENIZER
