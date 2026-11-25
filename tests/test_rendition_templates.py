"""Interface of the rendition template set (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.2).

The body render and the agent client task (phase E3) read the templates through this interface.
"""

from __future__ import annotations

import pytest

from claire.extract.rendition_templates import RENDITION_TEMPLATE_IDS, UnknownRenditionTemplate, rendition_template

IMAGES = [{"url": "https://example.org/figure.png", "alt": "figure", "caption": "caption"}]


def test_the_set_has_the_three_owner_templates_with_versions() -> None:
    assert RENDITION_TEMPLATE_IDS == ("standard", "concise", "academic")
    for template_id in RENDITION_TEMPLATE_IDS:
        template = rendition_template(template_id)
        assert template.id == template_id
        assert template.version.startswith(f"{template_id}-v")


def test_an_unknown_identifier_has_no_default() -> None:
    with pytest.raises(UnknownRenditionTemplate):
        rendition_template("default")


@pytest.mark.parametrize(("merged", "scale"), [(False, 1), (True, 2)])
def test_no_two_templates_build_the_same_instructions(merged: bool, scale: int) -> None:
    built = {template_id: rendition_template(template_id).build_instructions(images=IMAGES, merged=merged, scale=scale) for template_id in RENDITION_TEMPLATE_IDS}
    assert len(set(built.values())) == len(built)


@pytest.mark.parametrize("template_id", RENDITION_TEMPLATE_IDS)
def test_each_template_keeps_the_common_body_rules(template_id: str) -> None:
    built = rendition_template(template_id).build_instructions(images=IMAGES, merged=False, scale=1)
    assert "AsciiDoc" in built
    assert "Markdown 혼용 절대 금지" in built
    assert "절단 섹션 상세 작성 배제" in built
    assert "저자 라인 표기 절대 금지" in built
    assert "https://example.org/figure.png" in built
    without_images = rendition_template(template_id).build_instructions(images=None, merged=False, scale=1)
    assert "https://example.org/figure.png" not in without_images
