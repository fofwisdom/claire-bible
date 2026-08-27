"""중앙 프롬프트 엔진 — 지식그래프 추출, 상세 렌더링, 요약, 리서치, 판정 템플릿.

모든 LLM Provider(Gemini, Antigravity agy 등)가 공유하는 표준 프롬프트 템플릿과
문어체/서술체 어조 규칙을 단일 모듈에서 중앙 관리한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..ontology.base import Document
    from .provider import MergeCandidate

# 추출 프롬프트 버전. _SYS 또는 핵심 추출 지침을 바꾸면 올린다.
# v4: summary/observations/key_claims 및 주요 서술 출력에 문어체(서술체: ~한다/~이다/~함) 적용.
# v5: summary 평문(plain text) 작성 규칙 명시 (AsciiDoc/마크다운 마크업 금지).
# v6: 테이블 및 매트릭스 데이터 누락 방지 및 본문 글자 수 계산 제외 규칙 적용.
PROMPT_VERSION = "extract-v6"

# 단일 출처 문서의 LLM 투입 예산 (수집 상한인 20,000자에 맞춤). 병합 문서는 2배 (40,000자).
_SINGLE_DOC_CHAR_BUDGET = 20000
_MERGED_DOC_CHAR_BUDGET = _SINGLE_DOC_CHAR_BUDGET * 2
# render_detail 재시도 하한선(ONEHOP_MERGE_DESIGN.md §3.3b) — 이보다 짧으면 두 출처를
# 담기엔 명백히 부족하다고 보고 목표 분량을 올려 재시도.
_MERGED_DETAIL_MIN_CHARS = 1000

_SYS = """You extract a knowledge-graph fragment from a single source document for a
personal knowledge base about AI/software tools and research.

{ontology}

Rules:
- LANGUAGE & STYLE: write `summary`, every `observations` item, and `key_claims` in Korean
  (한국어) using formal/declarative written style (문어체 / 서술체: e.g. '~한다', '~이다',
  '~함' without conversational honorifics like '~합니다', '~해요'), REGARDLESS of the source
  document's language. Keep proper nouns, product/tool/model names, org names, and technical
  terms in their original form — do NOT transliterate (e.g. "OpenSkill", "arXiv", "LLM agent"
  stay as-is). Entity `name` and `aliases` stay in their canonical original form (usually the
  original language).
- summary: 1-3 factual sentences in Korean written style (문어체: ~한다/~이다). Write in pure
  plain text ONLY (평문). Do NOT use any AsciiDoc or Markdown markup syntax (e.g. NEVER use
  headers like '= Title' or '== Section', block markers like '[NOTE]', tables '|===', links
  'link:...', lists, bold/italic, or quote formatting).
- entities: the key things this document is ABOUT (tools, repos, models, people, orgs, concepts...).
- TABLES & DATA MATRICES: When the document contains tables, benchmarks, or comparison matrices,
  you MUST NOT omit or ignore the data inside tables. Extract entities (tools, models, benchmarks,
  metrics, datasets, configurations) and their relationships directly from table rows and columns.
  Ensure accurate attribute/observation capture for each entity mentioned in tables.
- Do NOT create an entity for the publishing platform, source site, news aggregator, or
  forum that merely HOSTS or links to this content (e.g. GeekNews, Hacker News, Reddit,
  a Discourse forum, PyTorch Korea, a personal blog). Include such a site ONLY if the
  document is genuinely ABOUT that platform itself.
- For each entity pick the single best `type` from the list. If truly none fits,
  leave type as your best guess AND set `proposed_type` to a snake_case suggestion.
- relations: typed edges between entities you listed (reference them by exact `name`).
  Use the relation types provided; only set `proposed_type` if none fits.
- Do NOT invent facts not supported by the document.
"""

import re

_ADOC_CORRUPTED_PATTERNS = (
    r"(?:^|\n)={1,5}\s+",
    r"(?:^|\n)#{1,6}\s+",
    r"(?:^|\n)\[(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source|cols|caption)[^\]]*\]",
    r"(?:^|\n)\|===",
    r"(?:^|\n):[a-zA-Z0-9_-]+:",
    r"(?:^|\n)(?:include|image|link)::",
    r"link:https?://",
    r"(?:^|\n)(?:_{4,}|={4,}|-{4,}|\.{4,}|`{3,})",
)


def is_corrupted_summary(text: str | None) -> bool:
    """텍스트에 AsciiDoc 또는 마크다운 구조/블록/속성 마크업이 잔존하는지 판정."""
    if not text:
        return False
    s = text.strip()
    return any(re.search(pat, s, re.MULTILINE | re.IGNORECASE) for pat in _ADOC_CORRUPTED_PATTERNS)


def clean_plain_summary(text: str | None) -> str:
    """요약 텍스트 또는 상세 본문(detail/raw_text)에서 모든 AsciiDoc/마크다운 마크업을 제거하고 순수 평문 추출."""
    if not text:
        return ""
    s = text.strip()
    if not s:
        return ""

    lines = s.splitlines()
    cleaned_paragraphs: list[list[str]] = []
    current_p: list[str] = []
    extracted_title: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current_p:
                cleaned_paragraphs.append(current_p)
                current_p = []
            continue

        # 1. 문서 제목 헤더 추출 (= Title 또는 # Title)
        h_m = re.match(r"^(?:={1,5}|#{1,6})\s+(.+)$", line)
        if h_m:
            if not extracted_title:
                extracted_title = h_m.group(1).strip()
            # 제목 라인은 본문 단락에서 제외
            continue

        # 2. 문서 속성 (:key: value, :toc:, :toc-title: 등) 스킵
        if re.match(r"^:[a-zA-Z0-9_-]+:\s*.*$", line):
            continue

        # 3. 블록 레이블/메타데이터 ([quote...], [NOTE], [source...], [cols...] 등) 스킵
        if re.match(r"^\[(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source|cols|caption)[^\]]*\]$", line, re.IGNORECASE):
            continue

        # 4. 블록 구분선 (____, ====, ----, ...., |===, ```) 스킵
        if re.match(r"^(?:\|===|_{4,}|={4,}|-{4,}|\.{4,}|`{3,})\s*$", line):
            continue

        # 5. 인라인 콜아웃 라인 (<1> 설명) 스킵
        if re.match(r"^<\d+>\s*.*$", line):
            continue

        # 6. 테이블 행 (| col1 | col2) 스킵
        if line.startswith("|"):
            continue

        # 7. 이미지/매크로 (image::url[...], include::...) 스킵
        if re.match(r"^(?:image|include)::[^\[]*\[.*\]$", line, re.IGNORECASE):
            continue

        # 8. 인라인 서식 제거
        # 형광/하이라이트: #text# or ==text== -> text
        line = re.sub(r"#([^#\n]+)#", r"\1", line)
        line = re.sub(r"==([^=\n]+)==", r"\1", line)
        # 굵게: **text** or *text* -> text
        line = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", line)
        line = re.sub(r"\*([^*\n]+)\*", r"\1", line)
        # 기울임: _text_ -> text
        line = re.sub(r"_([^_\n]+)_", r"\1", line)
        # 인라인 코드: `text` -> text
        line = re.sub(r"`([^`\n]+)`", r"\1", line)
        # 링크: link:https://url[text] -> text / https://url[text] -> text
        line = re.sub(r"link:https?://[^\s\[\]]+\[(.*?)\]", r"\1", line)
        line = re.sub(r"https?://[^\s\[\]]+\[(.*?)\]", r"\1", line)
        # 콜아웃: // <1> or <1>
        line = re.sub(r"//\s*<\d+>", "", line)
        line = re.sub(r"<\d+>", "", line)
        # 인라인 블록 태그 및 구분자 잔여물 ([NOTE], |===, ____ 등) 제거
        line = re.sub(r"\[(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION|quote|source|cols|caption)[^\]]*\]", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\|={2,}", "", line)
        line = re.sub(r"_{4,}|={4,}|-{4,}|\.{4,}|`{3,}", "", line)
        # 리스트 기호 (* item, - item) 제거
        line = re.sub(r"^[*-]\s+", "", line)
        line = re.sub(r"\s+", " ", line)

        line = line.strip()
        if line:
            current_p.append(line)

    if current_p:
        cleaned_paragraphs.append(current_p)

    # 본문 단락 중 가장 의미 있는 서술 단락 선택
    for p_lines in cleaned_paragraphs:
        p_text = " ".join(p_lines).strip()
        if len(p_text) >= 20:
            return p_text

    # 만약 20자 이상인 단락이 없으면 첫 번째 단락 반환
    if cleaned_paragraphs:
        p_text = " ".join(cleaned_paragraphs[0]).strip()
        if p_text:
            return p_text

    # 만약 본문 단락이 전혀 없지만 제목이 있었다면 제목 기반 요약 반환
    if extracted_title:
        extracted_title = clean_plain_summary(extracted_title)
        if extracted_title:
            return f"{extracted_title}에 관한 자료이다."

    return ""


from .table_budget import slice_text_with_table_exemption


def extract_system_prompt(ontology_block: str) -> str:
    """구조화 추출을 위한 시스템 프롬프트 반환."""
    return _SYS.format(ontology=ontology_block)


def doc_to_prompt(doc: Document) -> str:
    """Document -> LLM 프롬프트 본문.

    단일 출처는 12000자, 병합 문서(extra_sources 있음)는 24000자까지 일반 본문 투입.
    테이블(Markdown/AsciiDoc/HTML 표) 내 문자는 본문 문자 수 제한에서 제외하여 온전히 보존.
    """
    head = []
    if doc.title:
        head.append(f"TITLE: {doc.title}")
    if doc.url:
        head.append(f"URL: {doc.url}")
    head.append(f"SOURCE_TYPE: {doc.source_type}")
    limit = (
        _MERGED_DOC_CHAR_BUDGET
        if (doc.meta or {}).get("extra_sources")
        else _SINGLE_DOC_CHAR_BUDGET
    )
    content_body = slice_text_with_table_exemption(doc.raw_text or "", limit)
    return "\n".join(head) + "\n\nCONTENT:\n" + content_body


def extract_fallback_prompt(sys: str, body: str) -> str:
    """스키마 강제 실패 시 JSON 직접 파싱을 유도하는 폴백 프롬프트."""
    return (
        sys
        + "\n\nReturn ONLY valid JSON matching this shape:\n"
        + '{"summary":str,"key_claims":[str],'
        '"entities":[{"name":str,"type":str,"aliases":[str],'
        '"observations":[str],"proposed_type":str|null}],'
        '"relations":[{"source":str,"target":str,"type":str,"proposed_type":str|null}]}'
        + "\n(Note: 'summary' must be 1-3 factual plain text sentences in Korean written style without any AsciiDoc or Markdown markup)\n"
        + "\n\nDOCUMENT:\n"
        + body
    )


def summarize_search_prompt(query: str, context: str) -> str:
    """검색된 컨텍스트만 사용해 질의에 답하는 프롬프트(인용 포함, 환각 억제, 문어체)."""
    return (
        "You answer the user's query using ONLY the knowledge-base context below. "
        "Do not invent facts beyond it. Cite entities in [brackets]. "
        "If the context is insufficient, say so plainly. "
        "Write the answer in Korean (한국어) using objective written style (문어체: ~한다/~이다, "
        "do not use conversational honorifics like ~합니다/~해요), but keep proper nouns, "
        "product/tool names, and technical terms in their original form (do not transliterate). "
        "Write in pure plain text without any AsciiDoc or Markdown markup syntax. "
        "Be concise.\n\n"
        f"QUERY: {query}\n\nCONTEXT:\n{context}\n\nANSWER:"
    )


def images_block(images: list[dict]) -> str:
    """render_detail 프롬프트에 삽입할 후보 이미지 큐레이션 지시 블록."""
    if not images:
        return ""
    listing = "\n".join(
        f"[{i}] url: {('/image?p=' + im['local']) if im.get('local') else im.get('url', '')}\n"
        f"    alt: {im.get('alt', '') or '(없음)'}"
        + (f"\n    caption: {im['caption']}" if im.get("caption") else "")
        for i, im in enumerate(images)
    )
    return (
        "\n[원문에서 수집한 이미지 후보]\n"
        "원칙: **내용 이해에 꼭 필요한 그림만** 넣는다. 글로 설명하기 어려운 정보를 그림이 "
        "직접 전달하고, 그 그림이 없으면 이해가 떨어지는 경우에만 삽입하라 — 즉 구조도·"
        "아키텍처 다이어그램, 데이터 차트/그래프, 알고리즘·플로우 도식, 핵심을 보여주는 "
        "스크린샷 같은 **설명적 그림**. 관련 내용 바로 옆에 마크다운 `![설명](url)` 으로 넣되 "
        "url 은 목록 값을 한 글자도 바꾸지 말고 그대로, alt 설명은 한국어(문어체/명사구)로 달아라.\n"
        "**그리고 이미지 바로 다음 줄(빈 줄 없이)에 그 그림이 무엇을 보여주는지 본문 맥락에 "
        "근거한 한 줄 캡션을 이탤릭(`*...*`)으로 달아라(문어체 서술)** — alt 는 그림이 깨질 때만 보이므로 "
        "실제로 읽히는 설명은 이 캡션이다. 원문 캡션이 있으면 그것을 다듬어 쓰고, 없으면 본문 "
        "맥락으로 설명하되 원문에 없는 사실은 지어내지 마라.\n"
        "다음은 절대 넣지 마라: 대표/히어로/썸네일/소셜카드 이미지, 장식·분위기 사진, "
        "인물·프로필 사진, 로고·아이콘, 본문 이해와 무관하거나 그저 '예쁜' 이미지. **애매하면 "
        "넣지 마라.** 필요한 설명적 그림이 하나도 없으면 한 장도 넣지 않는다(그게 정상이다).\n"
        f"{listing}\n"
    )


def images_block_adoc(images: list[dict]) -> str:
    """render_detail_prompt_adoc 프롬프트에 삽입할 후보 이미지 큐레이션 지시 블록."""
    if not images:
        return ""
    listing = "\n".join(
        f"[{i}] url: {('/image?p=' + im['local']) if im.get('local') else im.get('url', '')}\n"
        f"    alt: {im.get('alt', '') or '(없음)'}"
        + (f"\n    caption: {im['caption']}" if im.get("caption") else "")
        for i, im in enumerate(images)
    )
    return (
        "\n[원문에서 수집한 이미지 후보]\n"
        "원칙: **내용 이해에 꼭 필요한 그림만** 넣는다. 구조도·아키텍처 다이어그램, 데이터 차트/그래프, "
        "알고리즘·플로우 도식, 핵심 스크린샷 같은 **설명적 그림**만 허용한다. 관련 내용 바로 옆에 "
        'AsciiDoc `image::url[alt 설명, title="캡션"]` 형태로 넣되 url 은 목록 값을 한 글자도 바꾸지 말고 그대로, '
        "alt 와 title 캡션은 한국어(문어체 서술)로 달아라.\n"
        "다음은 절대 넣지 마라: 대표/히어로/썸네일/소셜카드 이미지, 장식·분위기 사진, 인물·프로필 사진, "
        "로고·아이콘, 본문 이해와 무관하거나 그저 '예쁜' 이미지. **애매하면 넣지 마라.** 필요한 설명적 그림이 하나도 없으면 넣지 않는다.\n"
        f"{listing}\n"
    )


def render_detail_prompt_md(
    body: str,
    images: list[dict],
    *,
    merged: bool,
    scale: int = 1,
    directive: str | None = None,
) -> str:
    """원문을 한국어 마크다운으로 재구성하는 프롬프트(요약 아님, 여러 단락, 문어체)."""
    if merged:
        length_hint = f"대략 A4 {2 * scale}~{4 * scale}장 분량"
        merge_hint = (
            "이 문서는 여러 출처가 병합됐다 — 한 출처만 요약하고 끝내지 말고 "
            "각 출처의 핵심을 빠짐없이 통합해 서술하라.\n"
        )
    else:
        length_hint = "대략 A4 1~2장 분량"
        merge_hint = ""

    dir_hint = (
        f"\n[★ 최우선 중점 작성 방향성/초점]\n"
        f"- 사용자가 요청한 다음 핵심 방향성 및 구성 요소를 최우선으로 하여 본문 전체를 재구성하라: **{directive.strip()}**\n"
        f"- 원문에서 위 방향성과 관련된 핵심 개념, 구성 요소, 정의, 작동 원리, 사례, 수치를 빠짐없이 상세히 독립된 섹션/문단으로 다루어라.\n\n"
        if directive and directive.strip()
        else ""
    )

    return (
        "아래 원문을 한국어 **마크다운**으로 '편하게 읽을 수 있는 글'로 재구성하라. "
        "단순 1~2문장 요약이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
        f"맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락({length_hint})으로 "
        "풀어 써라.\n\n"
        + merge_hint
        + dir_hint
        + "작성 규칙(마크다운):\n"
        "1. 문체 및 어조: 일관된 문어체(서술체: '~한다', '~이다', '~됨')로 서술하라. "
        "대화형 경어체('~합니다', '~해요')나 구어체는 사용하지 않는다.\n"
        "2. 내용이 길면 `##`/`###` 소제목과 문단으로 구조화하고, 나열은 `-` 불릿을 써라. "
        "단락은 빈 줄로 구분.\n"
        "3. 가독성을 위해 **중요한 용어·핵심 주장은 굵게**(`**...**`) 표시하라. 그리고 "
        "정말 빼놓으면 안 되는 한두 구절만 `==형광==`(==로 감쌈)으로 강조하라 — 남발하면 "
        "강조 효과가 사라지니 문단·섹션당 한두 곳으로 아껴 써라.\n"
        "4. 비교 및 정리(테이블 보존): 원문에 벤치마크 점수표, 사양 비교, 옵션 정리 등의 테이블이 포함되어 있으면, "
        "테이블 안의 내용을 임의로 생략하거나 문장으로 축약하지 말고 온전한 마크다운 테이블(`| col1 | col2 |`)로 "
        "깔끔히 재구성하여 보존하라.\n"
        "5. 고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라"
        '(음차/번역 금지: 예 "arXiv", "LLM agent").\n'
        "6. 원문에 없는 사실은 절대 지어내지 말 것.\n"
        + images_block(images)
        + f"\n원문:\n{body}\n\n한국어 마크다운:"
    )


def render_detail_prompt_adoc(
    body: str,
    images: list[dict],
    *,
    merged: bool,
    scale: int = 1,
    directive: str | None = None,
) -> str:
    """원문을 한국어 AsciiDoc(ADOC)으로 실용적·복합적으로 재구성하는 프롬프트."""
    if merged:
        length_hint = f"대략 A4 {2 * scale}~{4 * scale}장 분량"
        merge_hint = (
            "이 문서는 여러 출처가 병합됐다 — 각 출처의 핵심을 빠짐없이 통합해 서술하라.\n"
        )
    else:
        length_hint = "대략 A4 1~2장 분량"
        merge_hint = ""

    dir_hint = (
        f"\n[★ 최우선 중점 작성 방향성/초점]\n"
        f"- 사용자가 요청한 다음 핵심 방향성 및 구성 요소를 최우선으로 하여 본문 전체를 재구성하라: **{directive.strip()}**\n"
        f"- 원문에서 위 방향성과 관련된 핵심 개념, 구성 요소, 정의, 작동 원리, 사례, 수치를 빠짐없이 상세히 독립된 섹션/문단으로 다루어라.\n\n"
        if directive and directive.strip()
        else ""
    )

    return (
        "아래 원문을 한국어 **AsciiDoc(ADOC)**으로 '편하게 읽을 수 있는 지식 문서'로 재구성하라. "
        "단순 1~2문장 요약이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
        f"맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락({length_hint})으로 "
        "풀어 써라.\n\n"
        + merge_hint
        + dir_hint
        + "작성 규칙(AsciiDoc 실용 가이드라인):\n"
        "1. 문체 및 어조: 일관된 문어체(서술체: '~한다', '~이다', '~됨')로 서술하라. "
        "대화형 경어체('~합니다', '~해요')나 구어체는 사용하지 않는다.\n"
        "2. 내용 구조화: `== `, `=== ` 섹션 제목과 문단으로 구성하고, 나열은 `* ` 불릿을 써라. "
        "단락은 빈 줄로 구분.\n"
        "3. 인용과 선언: 원문의 핵심 선언이나 공식 정의는 `[quote, 저자/출처]` 블록으로 분리하라.\n"
        "4. 코드 및 설정 해설: 코드/설정/명령어가 등장하면 `[source,언어]` 블록과 "
        "필요시 콜아웃 주석(`// <1>`, `<1> 설명`)을 결합하여 직관적으로 해설하라.\n"
        "5. 절제된 주석: 배경 전제나 필수 제약조건이 꼭 필요한 경우에만 `[NOTE]` 또는 `[IMPORTANT]` "
        "블록을 1~2곳 이내로 아껴 써라 (남발 금지).\n"
        "6. 비교와 정리(테이블 보존): 성능 수치, 벤치마크, 스펙 비교, 옵션 등은 `|===` 테이블로 깔끔히 정돈하라. "
        "원문의 테이블 데이터 및 수치를 임의로 생략하거나 문장으로 축약하지 말고 온전히 보존하라.\n"
        "7. 가독성 및 강조: 중요한 용어는 `*굵게*` 표시하고, 정말 빼놓으면 안 되는 한두 구절만 "
        "`#형광#`(#으로 감쌈)으로 강조하라.\n"
        "8. 고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라"
        '(음차/번역 금지: 예 "arXiv", "LLM agent").\n'
        "9. 원문에 없는 사실은 절대 지어내지 말 것.\n"
        + images_block_adoc(images)
        + f"\n원문:\n{body}\n\n한국어 AsciiDoc:"
    )


def render_detail_prompt(
    body: str,
    images: list[dict],
    *,
    merged: bool,
    scale: int = 1,
    format: str = "md",
    directive: str | None = None,
) -> str:
    """포맷(md 또는 adoc)에 맞춰 가독 렌더링 프롬프트를 라우팅."""
    if (format or "md").strip().lower() in ("asciidoc", "adoc"):
        return render_detail_prompt_adoc(
            body, images, merged=merged, scale=scale, directive=directive
        )
    return render_detail_prompt_md(
        body, images, merged=merged, scale=scale, directive=directive
    )


def classify_watch_prompt(body: str) -> str:
    """문서의 주기적 갱신 여부 판단 프롬프트."""
    return (
        "아래 문서가 '주기적으로 내용이 갱신되어 다시 봐야 가치 있는 콘텐츠'인지 판단하라.\n"
        "- watch=true: 리더보드·벤치마크 순위표·랭킹·실시간 통계·가격/시세·지속 갱신 표 등 "
        "시간이 지나면 내용이 바뀌어 재확인 가치가 있는 것.\n"
        "- watch=false: 뉴스 기사·블로그 글·논문·릴리스 노트·일회성 설명/문서 등 한 번 "
        "적재하면 거의 안 바뀌는 것.\n"
        "watch=true 면 적절한 재확인 주기를 interval_days(정수 일; 매일=1, 매주=7 등)로. "
        "reason 은 한국어 한 문장(문어체: ~임/~함/~다).\n\n"
        f"문서:\n{body}"
    )


def research_prompt(query: str, context: str) -> str:
    """맥락 고정 웹 조사 프롬프트 (다의어 오염 방지, 문어체)."""
    return (
        "당신은 개인 지식그래프를 확장하는 리서처다. 사용자가 아래 [맥락]의 자료를 "
        "읽다가 [조사 대상]에 대해 더 알고 싶어한다.\n\n"
        "규칙:\n"
        "1. [조사 대상]은 반드시 [맥락] 안에서의 의미로만 해석하라. 동명의 다른 "
        "대상(다의어)을 다루게 되면 잘못된 지식이 그래프를 오염시킨다. 먼저 맥락 내 "
        "해석을 한 문장으로 명시하고 시작하라.\n"
        "2. 웹 검색으로 사실을 확인하며 조사하라. 맥락과 일치하는 신뢰할 만한 자료를 "
        "찾지 못하면, 지어내지 말고 첫 줄에 INSUFFICIENT 라고만 적고 이유를 한 줄 "
        "덧붙여라.\n"
        "3. 보고서는 한국어 평문 산문(일관된 문어체: ~한다/~이다, 여러 단락, 빈 줄 구분)으로 "
        "작성하라 — 마크다운 소제목(#)·불릿(-)·표 금지. 대화형 경어체(~합니다) 금지. "
        "고유명사·제품/도구/모델명·기술 용어는 원문 형태 유지(음차 금지).\n"
        "4. 핵심 정의 → 맥락과의 관계 → 구체적 사실(수치·날짜·버전 등) 순으로, "
        "지식그래프에 추출할 가치가 있는 내용 위주로 써라.\n\n"
        f"[맥락]\n{context[:8000]}\n\n[조사 대상]\n{query}\n\n[보고서]"
    )


def judge_research_prompt(query: str, context: str, report: str) -> str:
    """조사 보고서 품질 및 맥락 일치도 판정 프롬프트."""
    return (
        "지식그래프 추가 게이트 심사. 사용자가 [맥락]을 읽다가 [조사 대상]을 조사해 "
        "[보고서]를 얻었다. 다음을 채점하라.\n"
        "- relevance(0.0~1.0): 보고서가 [맥락] 안에서의 [조사 대상] 의미를 다루는가? 동명의 "
        "다른 대상(다의어)을 다뤘다면 0 에 가깝게. 맥락과 무관한 일반론이면 낮게.\n"
        "- quality(0.0~1.0): 사실이 구체적(수치·날짜·정확한 명칭)이고 신뢰할 만한가? 빈약하거나 "
        "추측성이면 낮게.\n"
        "- same_subject(true/false): 오직 다음 패턴에서만 true — [맥락]은 [조사 대상]을 "
        "소개·인용·언급하는 **2차 서술**(그 회사·프로젝트 본인이 아닌 **제3자**(다른 "
        "매체·커뮤니티·개인)가 쓴 리뷰, 소개 글, 보도 등)이고 [보고서]는 바로 그 대상의 "
        "**1차 원본 자체**(예: 그 프로젝트의 공식 저장소, 공식 문서 원문)다. 즉 [보고서]가 "
        "[맥락]이 말하는 '그것' 자체를 가리킬 때만 true.\n"
        "  [맥락]을 쓴 주체가 [조사 대상]인 회사·프로젝트 **본인**이라면(자사 블로그, "
        "자사 사이트 등에 실린 글) — 설령 소개·사례 형식이라도 [맥락] 자체가 이미 1차 "
        "자료이므로 same_subject 는 원칙적으로 false 다. 같은 회사·제품을 다루는 다른 "
        "공식 자료(일반 문서, 다른 사례, 홈페이지, 저장소 등)는 [맥락]의 원본이 아니라 "
        "**형제 문서**일 뿐이니 병합 대상이 아니다.\n"
        "  나머지(다른 프로젝트, 다른 사건, 같은 회사의 다른 화제, 제3자의 파생 논의 "
        "등)도 모두 false.\n"
        "  예1) true: '[맥락]=GeekNews 가 X 프로젝트를 소개하는 기사(2차 서술)' + "
        "'[보고서]=X 프로젝트의 공식 github 저장소(1차 원본)'\n"
        "  예2) false: '[맥락]=회사 자체 블로그의 특정 고객 사례 글(이미 1차 자료)' + "
        "'[보고서]=같은 회사의 제품 문서 / 홈페이지 / 저장소'(맥락의 원본이 아니라 "
        "형제 문서)\n"
        "- interpretation: 보고서가 [조사 대상]을 어떤 의미로 해석했는지 한 문장(한국어 문어체: ~임/~함/~다).\n"
        "- reason: 채점 근거 한두 문장(한국어 문어체: ~임/~함/~다).\n\n"
        f"[맥락]\n{context[:6000]}\n\n[조사 대상]\n{query}\n\n[보고서]\n{report[:8000]}"
    )


def select_followups_prompt(context: str, candidates: list[dict]) -> str:
    """1홉 자동확장 후보 선별 프롬프트."""
    listing = "\n".join(
        f"[{i}] {c.get('anchor') or '(텍스트 없음)'} — {c.get('url', '')}"
        for i, c in enumerate(candidates)
    )
    return (
        "당신은 개인 지식그래프를 키우는 큐레이터다. 사용자가 [부모 문서]를 읽고 "
        "지식으로 적재했다. 그 문서에서 발견된 [외부 링크 후보] 중, 같은 주제를 더 "
        "깊이 알기 위해 **따라가서 함께 적재할 가치가 있는 것**만 골라라.\n\n"
        "규칙:\n"
        "1. 부모 문서의 주제와 직접 관련되고, 그 자체로 실질 내용(논문/문서/글)이 "
        "있을 법한 링크만 고른다.\n"
        "2. 광고·로그인·약관·소셜·플랫폼 홈·태그 목록 등 비콘텐츠, 그리고 주제와 "
        "동떨어진 링크는 제외한다.\n"
        "3. 애매하면 넣지 마라(잘못 적재된 노드가 이후 검색/종합을 오도한다). 가치 "
        "있는 게 하나도 없으면 빈 목록을 반환한다.\n"
        "4. follow 에는 고른 후보의 번호(인덱스)만 담아라.\n\n"
        f"[부모 문서]\n{context[:6000]}\n\n[외부 링크 후보]\n{listing}"
    )


def judge_same_entity_prompt(mc: MergeCandidate) -> str:
    """두 엔티티의 동일성 판정 프롬프트."""
    return (
        "Decide if these two knowledge-base entries refer to the SAME real-world "
        "entity (e.g. a renamed/aliased tool), NOT merely related ones.\n"
        "Different products in the same space are NOT the same.\n\n"
        f"A: name={mc.new_name!r} type={mc.new_type!r}\n"
        f"   notes={' | '.join(mc.new_observations)[:400]}\n"
        f"B: name={mc.cand_name!r} type={mc.cand_type!r} aliases={mc.cand_aliases}\n"
        f"   notes={' | '.join(mc.cand_observations)[:400]}\n\n"
        "Answer with exactly one word: SAME or DIFFERENT."
    )
