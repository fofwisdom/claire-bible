"""Expansion gate: the relevance and quality thresholds that a candidate must pass before the graph gets it.

Automatic expansion and contextual research use the same gate. Relevance (the match with the context) is stricter than quality: pollution is worse than thin content, because a wrong homonym node misleads all later search and synthesis. A failed judgement scores zero (fail-closed).
"""

from __future__ import annotations

RELEVANCE_MIN = 0.7
QUALITY_MIN = 0.6


def passes_gate(relevance: float, quality: float) -> bool:
    """True when both scores reach their thresholds."""
    return relevance >= RELEVANCE_MIN and quality >= QUALITY_MIN
