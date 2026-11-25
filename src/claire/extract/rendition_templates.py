"""Rendition templates, 정리본 템플릿 (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.2, ED2).

A rendition template is a fixed instruction set for the body prompt of a rendition (정리본). The code defines the set. An account cannot make a template.

Structure (owner question, 2026-11-25: "기본 원칙을 공통으로 수용하고 템플릿으로서의 각 특성을 적용"):

- ``CORE_RULES`` (공통 원칙): the 14 rules of the current body prompt, in their order, word for word. The test checks that a template without changes builds the current prompt ``prompts.render_detail_instructions_adoc`` exactly.
- ``FIXED_RULES``: the core rules that no template replaces (AsciiDoc only, written style, quote rules, emphasis, names, no invented text, truncated sections, no author line). A template can only add lines to them.
- ``TRAITS`` (특성): rule texts that more than one template uses instead of a core rule (no tables of the source, representative formulas only).
- Each template has its scope sentence and its changes: replaced slots, added lines, omitted slots, inserted rules. The rules get their numbers when the template builds its instructions.

Templates:

- ``standard`` (기본): traits "no tables of the source" and "representative formulas only" (owner decision O17). The 대표 정리본 of each document uses it.
- ``concise`` (간결): a short body with a fixed section order, and the same two traits (owner decisions O18 and O20).
- ``academic`` (학술): no changes, thus the current body prompt (owner decisions O2, O11, and O17). The owner changes it later.

No ingest path selects a template yet: until phase E3, the ingest uses the current prompt ``prompts.render_detail_instructions_adoc``. A change of the text of a template increments its version from its first use.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .prompts import images_block_adoc

RULES_HEADER = '작성 규칙(AsciiDoc 실용 가이드라인 및 단일 포맷 순수성 준수):\n'

SLOT_ORDER: tuple[str, ...] = ('asciidoc', 'style', 'structure', 'quotes', 'code', 'notes', 'tables', 'emphasis', 'names', 'formulas', 'anchors', 'faithful', 'truncation', 'author_line')

# 공통 원칙: the rules of the current body prompt, word for word. "<<FIRST_SECTION>>" is the first section after the title.
CORE_RULES: Mapping[str, str] = MappingProxyType({
    'asciidoc': "[★ 중요: Markdown 혼용 절대 금지 / 순수 AsciiDoc 표준 준수]\n   - 본 문서는 100% 순수 AsciiDoc 표준 문법으로만 작성해야 한다. Markdown 문법을 절대 섞어 쓰지 마라.\n   - 구분선/수평선(Thematic Break): Markdown의 `---`는 절대 사용 금지. 섹션 간 구분선이 필요한 경우 반드시 AsciiDoc 표준인 `'''` (작은따옴표 3개 단독 행)만을 사용하라.\n   - 강조 서식: 굵게는 `*단어*` (Markdown의 `**` 금지), 기울임은 `_단어_` (Markdown의 `*단어*` 금지), 형광 하이라이트는 `#단어#` (Markdown의 `==단어==` 금지).\n   - 링크: `https://example.com[표시 텍스트]` (Markdown의 `[표시 텍스트](url)` 금지).\n   - 인용/코드: Markdown의 `>` 나 ```` 대신 반드시 `[quote]` 블록과 `[source,언어]` 블록을 사용하라.",
    'style': "문체 및 어조: 일관된 문어체(서술체: '~한다', '~이다', '~됨')로 서술하라. 대화형 경어체('~합니다', '~해요')나 구어체는 사용하지 않는다.",
    'structure': '내용 구조화: `== `, `=== ` 섹션 제목과 문단으로 구성하고, 나열은 `* ` 불릿을 써라. 단락은 빈 줄로 구분.',
    'quotes': "인용과 출처 표기:\n   - `[quote]` 블록에는 원문에 있는 문장을 원문 언어 그대로 옮긴 직접 인용만 넣는다. 한국어 번역이 필요하면 블록 바로 다음 문단에 쓴다.\n   - 번역·요약·의역한 문장이나 여러 문장을 합친 문장은 `[quote]`에 넣지 마라. 핵심 선언이나 정의를 요약할 때는 일반 문단 또는 `[NOTE]`로 서술하라.\n   - 출처(`[quote, 발언자]`)에는 그 문장을 실제로 말하거나 쓴 사람 또는 문헌만 적는다. '핵심 정의' 같은 개념 이름은 출처가 아니다.\n   - 원문 저자가 자기 문장에 인용 표기(예: `(Smith, 2007)`)를 달았어도 그 문장의 발언자는 원문 저자다. 인용 표기를 출처로 옮기지 말고, 서로 다른 문장의 인용 표기를 하나의 출처로 합치지 마라.\n   - 출처에는 쉼표를 쓰지 마라. 쉼표는 출처를 발언자와 출전으로 나누는 구분자다(`[quote, 발언자, 출전]`).\n   - 단순 비디오 채널/호스팅 플랫폼/업로더 계정명은 출처로 인용하지 않는다.",
    'code': '코드 및 설정 해설: 코드/설정/명령어가 등장하면 `[source,언어]` 블록과 필요시 콜아웃 주석(`// <1>`, `<1> 설명`)을 결합하여 직관적으로 해설하라.',
    'notes': '절제된 주석: 배경 전제나 필수 제약조건이 꼭 필요한 경우에만 `[NOTE]` 또는 `[IMPORTANT]` 블록을 1~2곳 이내로 아껴 써라 (남발 금지).',
    'tables': '비교와 정리(테이블 보존): 성능 수치, 벤치마크, 스펙 비교, 옵션 등은 `|===` 테이블로 깔끔히 정돈하라. 원문의 테이블 데이터 및 수치를 임의로 생략하거나 문장으로 축약하지 말고 온전히 보존하라. 단, 테이블 내에 들어있는 미디어(이미지·아이콘·뱃지 등)는 표의 가독성과 서식 유지를 위해 제거·생략을 허용한다.',
    'emphasis': '가독성 및 강조: 중요한 용어는 `*굵게*` 표시하고, 정말 빼놓으면 안 되는 한두 구절만 `#형광#`(#으로 감쌈)으로 강조하라.',
    'names': '고유명사·제품/도구/모델명·조직명·기술 용어는 원문 형태 그대로 유지하라(음차/번역 금지: 예 "arXiv", "LLM agent").',
    'formulas': '수식 및 공식 보존: 수학, 통계, 암호학, 알고리즘 공식 및 기호는 원문을 온전히 보존하라. 문장 내에 등장하는 개별 그리스 문자(예: \\lambda, \\omega, \\alpha)나 수학 기호(\\in, \\ge, \\sum)라도 반드시 인라인 수식 `stem:[공식]`(예: `stem:[E = mc^2]`, `stem:[\\lambda_o]`) 또는 `$ \\lambda_o $`로 감싸라. 블록 수식은 `[latexmath]\\n++++\\n수식\\n++++` 또는 `$$ 수식 $$`을 사용하여 기호나 공식을 정확하게 보존하라.',
    'anchors': '상호 참조 및 앵커: 긴 문서의 주요 섹션에는 `[#섹션ID]` 앵커를 부여하고, 필요시 `<<섹션ID, 제목>>` 상호 참조를 활용하라.',
    'faithful': '[★ 원문 엄격 비례 및 환각/추측 절대 금지]\n    - 원문에 직접적으로 언급되지 않은 사실, 가상의 기술 스택/방법론, 임의의 수치나 결과, 근거 없는 배경 맥락은 절대 지어내지 마라.\n    - 원문 내용이 짧거나 제한적인 경우 억지로 분량을 부풀리지 말고 실제 기술된 사실에 엄격히 비례하여 충실하게 서술하라.',
    'truncation': '절단 섹션 상세 작성 배제(적재 정책): 원문을 절단하여 적재 및 상세 작성 시, 절단되어 내용이 유실된 섹션은 상세를 작성하지 않는다. 본문 중간이나 말미에서 텍스트가 끊겨 온전히 이어지지 않는 불완전한 섹션(잘린 문단, 미완성 소제목·조항 등)은 억지로 추론하거나 불완전하게 작성하지 말고 완전히 제외(생략)하며, 온전하게 보존된 섹션까지만 상세를 작성하라.',
    'author_line': '[★ 중요: 문서 레벨 서지 정보 및 저자 라인 표기 절대 금지]\n   - 문서 제목(`= 제목`) 바로 아래 행에 저자, 발표자, 소속 기관, 발행처, 유튜브 채널명 등의 부제/저자 라인을 일체 작성하지 마라.\n   - `= 제목` 직후에는 곧바로 <<FIRST_SECTION>>으로 시작하라.\n   - 저자·소속 정보는 온톨로지 지식 그래프의 영역이며 문서 본문에 기재하지 않는다.',
})

FIXED_RULES: frozenset[str] = frozenset({"asciidoc", "style", "quotes", "emphasis", "names", "faithful", "truncation", "author_line"})

DEFAULT_FIRST_SECTION = '목차/문서 속성(`:toc:`) 또는 본문 섹션(`== 개요`)'

# 특성: rule texts that templates use instead of a core rule.
TRAITS: Mapping[str, str] = MappingProxyType({
    "no_source_tables": '원문 테이블 미사용: 원문의 표를 본문에 옮기지 마라. `|===` 테이블을 쓰지 않는다. 성능 수치, 벤치마크, 스펙 비교가 원문의 표에 있으면, 결론을 이해하는 데 필요한 핵심 수치만 골라 문장이나 `* ` 불릿으로 서술하라. 옮긴 수치는 반올림하거나 바꾸지 말고 단위와 조건을 함께 적어라.',
    "representative_formulas": '수식은 대표 공식만: 원문의 핵심 결과나 방법을 정의하는 대표 공식만 수식으로 쓴다(대개 1~3개). 유도 과정, 중간 식, 보조 기호는 수식으로 옮기지 말고 그 뜻을 문장으로 설명하라. 대표 공식은 블록 수식 `[latexmath]\\n++++\\n수식\\n++++` 또는 `$$ 수식 $$`으로 원문 그대로 정확하게 쓰고, 문장 안에서 그 공식의 기호를 가리킬 때만 인라인 수식 `stem:[기호]`를 쓴다.',
})


class UnknownRenditionTemplate(ValueError):
    """The identifier is not a rendition template."""


def _standard_intro(merged: bool, scale: int) -> str:
    """The scope sentence of the current body prompt."""
    if merged:
        length_hint = f"대략 A4 {2 * scale}~{4 * scale}장 분량"
        merge_hint = "이 문서는 여러 출처가 병합됐다 — 각 출처의 핵심을 빠짐없이 통합해 서술하라.\n"
    else:
        length_hint = "대략 A4 1~2장 분량"
        merge_hint = ""
    return (
        "아래 원문을 한국어 **AsciiDoc(ADOC)**으로 '편하게 읽을 수 있는 지식 문서'로 재구성하라. "
        "단순 1~2문장 초록이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 내용·배경 "
        f"맥락·중요한 세부까지 충분히 파악할 수 있도록 여러 단락({length_hint})으로 "
        "풀어 써라.\n\n"
        + merge_hint
    )


def _concise_intro(merged: bool, scale: int) -> str:
    """The scope sentence of the concise rendition."""
    if merged:
        length_hint = f"대략 A4 {scale}~{2 * scale}장 이내"
        merge_hint = "이 문서는 여러 출처가 병합됐다 — 각 출처의 핵심을 빠짐없이 통합하되, 출처끼리 겹치는 내용은 한 번만 서술하라.\n"
    else:
        length_hint = "대략 A4 1장 이내"
        merge_hint = ""
    return (
        "아래 원문을 한국어 **AsciiDoc(ADOC)**으로 '짧은 시간에 핵심을 파악하는 간결한 지식 문서'로 정리하라. "
        "단순 1~2문장 초록이 아니라, 독자가 원문을 직접 읽지 않아도 핵심 주장·근거·결론을 정확히 파악할 수 있어야 한다. "
        f"단, 배경 설명·일반론·반복·부연은 덜어 내고 {length_hint} 안에서 끝내라.\n\n"
        + merge_hint
    )


@dataclass(frozen=True, slots=True)
class RenditionTemplate:
    """One rendition template: the core rules with the changes of the template.

    ``replace``: slot to a new text (not for ``FIXED_RULES``). ``append``: slot to lines added at the end of the rule. ``omit``: slots without a rule. ``insert_after``: (slot, new key, text) rules after a slot. ``first_section``: the first section after the title in the rule ``author_line``. ``image_hint``: a line before the image candidates.
    """

    id: str
    version: str
    intro: Callable[[bool, int], str]
    replace: Mapping[str, str] = field(default_factory=dict)
    append: Mapping[str, str] = field(default_factory=dict)
    omit: frozenset[str] = frozenset()
    insert_after: tuple[tuple[str, str, str], ...] = ()
    first_section: str = DEFAULT_FIRST_SECTION
    image_hint: str = ""

    def __post_init__(self) -> None:
        fixed = (set(self.replace) | set(self.omit)) & FIXED_RULES
        if fixed:
            raise ValueError(f"template {self.id} cannot replace or omit a fixed rule: {sorted(fixed)}")
        unknown = (set(self.replace) | set(self.append) | set(self.omit) | {after for after, _, _ in self.insert_after}) - set(SLOT_ORDER)
        if unknown:
            raise ValueError(f"template {self.id} names unknown slots: {sorted(unknown)}")

    def rules(self) -> list[tuple[str, str]]:
        """The rules of the template in their order: (slot or inserted key, text)."""
        out: list[tuple[str, str]] = []
        for slot in SLOT_ORDER:
            if slot not in self.omit:
                text = self.replace.get(slot, CORE_RULES[slot])
                if slot in self.append:
                    text = text + "\n" + self.append[slot]
                out.append((slot, text.replace("<<FIRST_SECTION>>", self.first_section)))
            for after, key, text in self.insert_after:
                if after == slot:
                    out.append((key, text))
        return out

    def build_instructions(self, *, images: list[dict] | None, merged: bool, scale: int) -> str:
        """The instructions without the source text."""
        numbered = "".join(f"{number}. {text}\n" for number, (_, text) in enumerate(self.rules(), start=1))
        return (
            self.intro(merged, scale)
            + RULES_HEADER
            + numbered
            + (self.image_hint if images else "")
            + images_block_adoc(images or [])
        )


RENDITION_TEMPLATES: dict[str, RenditionTemplate] = {
    "standard": RenditionTemplate(
        id="standard",
        version="standard-v1",
        intro=_standard_intro,
        replace={"tables": TRAITS["no_source_tables"], "formulas": TRAITS["representative_formulas"]},
    ),
    "concise": RenditionTemplate(
        id="concise",
        version="concise-v1",
        intro=_concise_intro,
        replace={
            "structure": '간결 구성: 아래 순서의 섹션으로 구성하라. 원문에 해당 내용이 없는 섹션은 만들지 마라. 하위 섹션은 `=== `까지만 쓴다. 단락은 빈 줄로 구분한다.\n   - `== 핵심`: 원문의 결론과 가장 중요한 주장 3~5개를 `* ` 불릿으로 쓴다. 불릿 하나는 한 문장이다.\n   - `== 내용`: 핵심 주장을 뒷받침하는 근거·방법·작동 원리를 짧은 문단 또는 `* ` 불릿으로 쓴다.\n   - `== 결과와 수치`: 실험 결과, 벤치마크, 비교 수치가 원문에 있을 때만 쓴다.\n   - `== 한계`: 원문이 스스로 밝힌 한계·전제·미해결 문제가 있을 때만 쓴다.',
            "code": '코드 및 설정: 핵심을 보여 주는 몇 줄만 `[source,언어]` 블록으로 남기고, 긴 코드는 그 동작을 한 문장으로 설명하라.',
            "notes": '주석 블록: `[NOTE]`·`[IMPORTANT]` 블록은 쓰지 않는 것을 원칙으로 하고, 배경 전제나 필수 제약조건이 꼭 필요할 때만 한 곳 이내로 쓴다.',
            "tables": TRAITS["no_source_tables"],
            "formulas": TRAITS["representative_formulas"],
        },
        append={
            "quotes": "   - 간결 정리본에서 `[quote]` 블록은 꼭 필요할 때 한 곳 이내로만 쓴다.",
            "faithful": "    - 간결하게 줄이더라도 원문에 없는 내용으로 빈자리를 채우지 마라. 원문이 짧으면 더 짧게 써라.",
        },
        omit=frozenset({"anchors"}),
        insert_after=(("structure", "trim", '덜어 낼 것과 남길 것:\n   - 덜어 낸다: 역사적 배경, 일반론, 같은 내용의 반복, 사례 나열(대표 사례 하나만 남긴다), 수사적 도입과 맺음말.\n   - 남긴다: 정의, 조건, 수치와 단위, 인과 관계, 고유명사. 수치는 반올림하거나 바꾸지 마라.'),),
        first_section="본문 섹션(`== 핵심`)",
        image_hint="\n[간결 정리본의 그림 규칙] 아래 후보 중 내용 이해에 꼭 필요한 그림을 최대 두 개까지만 넣는다.\n",
    ),
    "academic": RenditionTemplate(id="academic", version="academic-v1", intro=_standard_intro),
}

RENDITION_TEMPLATE_IDS: tuple[str, ...] = tuple(RENDITION_TEMPLATES)

BASE_TEMPLATE = RenditionTemplate(id="base", version="base", intro=_standard_intro)
"""The core rules without changes. It builds the current body prompt; no rendition uses it."""


def rendition_template(template_id: str) -> RenditionTemplate:
    """Return the template of the identifier. An identifier that is not in the set gives ``UnknownRenditionTemplate``; there is no default template."""
    found = RENDITION_TEMPLATES.get(template_id)
    if found is None:
        raise UnknownRenditionTemplate(template_id)
    return found
