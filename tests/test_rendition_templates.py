"""Interface of the rendition template set (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.2).

The body render and the agent client task (phase E3) read the templates through this interface. The structure: core rules (공통 원칙), fixed rules that no template replaces, traits (특성), and the changes of each template.
"""

from __future__ import annotations

import pytest

from claire.extract.rendition_templates import (
    ADDITIONAL_RENDITION_TEMPLATES,
    CORE_RULES,
    PRIMARY_RENDITION_TEMPLATE,
    DEFAULT_FIRST_SECTION,
    FIXED_RULES,
    RENDITION_TEMPLATE_IDS,
    RenditionTemplate,
    UnknownRenditionTemplate,
    rendition_template,
)

IMAGES = [{"url": "https://example.org/figure.png", "alt": "figure", "caption": "caption"}]
CASES = [(None, False, 1), (IMAGES, False, 1), (None, True, 2), (IMAGES, True, 2)]


def test_the_set_has_the_three_owner_templates_with_versions() -> None:
    assert RENDITION_TEMPLATE_IDS == ("standard", "concise", "academic")
    for template_id in RENDITION_TEMPLATE_IDS:
        template = rendition_template(template_id)
        assert template.id == template_id
        assert template.version.startswith(f"{template_id}-v")


def test_an_unknown_identifier_has_no_default() -> None:
    with pytest.raises(UnknownRenditionTemplate):
        rendition_template("default")


def test_the_primary_template_is_standard_and_the_others_can_be_added() -> None:
    assert PRIMARY_RENDITION_TEMPLATE == "standard"
    assert ADDITIONAL_RENDITION_TEMPLATES == ("concise", "academic")


@pytest.mark.parametrize(("images", "merged", "scale"), CASES)
def test_the_template_academic_is_the_core_rules_without_changes(images: list[dict] | None, merged: bool, scale: int) -> None:
    academic = rendition_template("academic").build_instructions(images=images, merged=merged, scale=scale)
    for slot in CORE_RULES:
        assert CORE_RULES[slot].replace("<<FIRST_SECTION>>", DEFAULT_FIRST_SECTION) in academic, slot


@pytest.mark.parametrize("template_id", RENDITION_TEMPLATE_IDS)
def test_each_template_keeps_the_fixed_core_rules(template_id: str) -> None:
    template = rendition_template(template_id)
    built = template.build_instructions(images=IMAGES, merged=False, scale=1)
    for slot in FIXED_RULES:
        assert CORE_RULES[slot].replace("<<FIRST_SECTION>>", template.first_section) in built, slot
    assert "https://example.org/figure.png" in built
    assert "https://example.org/figure.png" not in template.build_instructions(images=None, merged=False, scale=1)


@pytest.mark.parametrize(("images", "merged", "scale"), CASES)
def test_no_two_templates_build_the_same_instructions(images: list[dict] | None, merged: bool, scale: int) -> None:
    built = {template_id: rendition_template(template_id).build_instructions(images=images, merged=merged, scale=scale) for template_id in RENDITION_TEMPLATE_IDS}
    assert len(set(built.values())) == len(built)


def test_a_template_cannot_replace_or_omit_a_fixed_rule() -> None:
    with pytest.raises(ValueError):
        RenditionTemplate(id="x", version="x-v1", intro=lambda merged, scale: "", replace={"asciidoc": "other"})
    with pytest.raises(ValueError):
        RenditionTemplate(id="x", version="x-v1", intro=lambda merged, scale: "", omit=frozenset({"truncation"}))
    with pytest.raises(ValueError):
        RenditionTemplate(id="x", version="x-v1", intro=lambda merged, scale: "", append={"no_such_slot": "text"})


def test_rules_are_numbered_in_order_without_gaps() -> None:
    concise = rendition_template("concise")
    built = concise.build_instructions(images=None, merged=False, scale=1)
    for number in range(1, len(concise.rules()) + 1):
        assert f"\n{number}. " in built
    assert f"\n{len(concise.rules()) + 1}. " not in built
    assert DEFAULT_FIRST_SECTION not in built
