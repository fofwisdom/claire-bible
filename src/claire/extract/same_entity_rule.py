"""One rule for "same entity" (owner decision 2026-11-20).

The LLM judge, the Jev shadow, the pre-label engine of the shadow review, and the people who give verdicts read the same text.
Each review verdict keeps SAME_ENTITY_RULE_VERSION. A change of the text needs a new version.
See docs/design/JEV_SHADOW_EVALUATION_DESIGN.md (R6).
"""

from __future__ import annotations

SAME_ENTITY_RULE_VERSION = "2026-11-20"

SAME_ENTITY_TRUE = (
    "The same referent written differently: spelling or case variant, alias, abbreviation or acronym, "
    "translation, or a rename or rebrand of the same thing."
)
SAME_ENTITY_FALSE = (
    "Different referents, including different versions, releases, editions, sizes or variants of a product; "
    "a product and its vendor; a person and their organization or work; rival, related or successor "
    "products; a broad concept and a narrower one."
)
