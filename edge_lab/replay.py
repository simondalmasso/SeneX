from __future__ import annotations

import re

from .contracts import canonical_hash


def canonicalize_hypothesis(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip().lower())


def semantic_key(
    *,
    hypothesis_id: str,
    candidate_definition: str,
    baseline_definition: str,
    horizon: str,
    regime: str,
) -> str:
    return canonical_hash(
        {
            "hypothesis_id": canonicalize_hypothesis(hypothesis_id),
            "candidate_definition": canonicalize_hypothesis(candidate_definition),
            "baseline_definition": canonicalize_hypothesis(baseline_definition),
            "horizon": canonicalize_hypothesis(horizon),
            "regime": canonicalize_hypothesis(regime),
        }
    )
