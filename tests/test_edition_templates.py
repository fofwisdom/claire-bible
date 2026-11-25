"""Interface of the edition template set (DOCUMENT_EDITIONS_AND_PUBLICATION_DESIGN.md Section 5.2).

The body render and the agent client task (phase E3) read the templates through this interface.
"""

from __future__ import annotations

import pytest

from claire.extract.edition_templates import EDITION_TEMPLATE_IDS, UnknownEditionTemplate, edition_template
from claire.extract.prompts import render_detail_instructions_adoc

IMAGES = [{"url": "https://example.org/figure.png", "alt": "figure", "caption": "caption"}]


def test_the_set_has_the_three_owner_templates_with_versions() -> None:
    assert EDITION_TEMPLATE_IDS == ("detailed", "concise", "academic")
    for template_id in EDITION_TEMPLATE_IDS:
        template = edition_template(template_id)
        assert template.id == template_id
        assert template.version.startswith(f"{template_id}-v")


def test_an_unknown_identifier_has_no_default() -> None:
    with pytest.raises(UnknownEditionTemplate):
        edition_template("default")


@pytest.mark.parametrize(("merged", "scale"), [(False, 1), (True, 2)])
def test_detailed_is_the_current_body_prompt(merged: bool, scale: int) -> None:
    built = edition_template("detailed").build_instructions(images=IMAGES, merged=merged, scale=scale)
    assert built == render_detail_instructions_adoc(images=IMAGES, merged=merged, scale=scale)


@pytest.mark.parametrize("template_id", EDITION_TEMPLATE_IDS)
def test_each_template_keeps_the_common_body_rules(template_id: str) -> None:
    built = edition_template(template_id).build_instructions(images=IMAGES, merged=False, scale=1)
    assert "AsciiDoc" in built
    assert "Markdown 혼용 절대 금지" in built
    assert "절단 섹션 상세 작성 배제" in built
    assert "저자 라인 표기 절대 금지" in built
    assert "https://example.org/figure.png" in built
    without_images = edition_template(template_id).build_instructions(images=None, merged=False, scale=1)
    assert "https://example.org/figure.png" not in without_images
