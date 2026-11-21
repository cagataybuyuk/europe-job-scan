"""OPPORTUNITY-1.0 scoring (ROADMAP Faz 1.4). Pure; weights/thresholds come from criteria.

Each component rating is an int 0..100 (percent of that component's weight) or ``None``
(Unknown). Unknown is never negative: it earns ``criteria.unknown_credit_percent`` of the
weight and is listed in ``unknown_components``. This module never infers sponsorship or
work authorization; it only consumes ratings produced upstream from explicit evidence.
Integer/Fraction arithmetic keeps threshold boundaries exact (64/65, 74/75, 84/85).
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from ejs.services.criteria_loader import SCORE_COMPONENTS, Criteria


class ScoreInputError(ValueError):
    pass


@dataclass(frozen=True)
class ScoreResult:
    score: float
    score_exact: Fraction
    band: str                      # priority_apply | apply | consider | skip
    unknown_components: tuple[str, ...]
    rule_version: str


def band_for(score: Fraction | int | float, criteria: Criteria) -> str:
    th = criteria.thresholds
    if score >= th["priority_apply"]:
        return "priority_apply"
    if score >= th["apply"]:
        return "apply"
    if score >= th["consider"]:
        return "consider"
    return "skip"


def compute_opportunity(ratings: Mapping[str, int | None], criteria: Criteria) -> ScoreResult:
    if set(ratings) != set(SCORE_COMPONENTS):
        raise ScoreInputError("SCORE_COMPONENTS_MISMATCH")
    total = Fraction(0)
    unknown: list[str] = []
    for name in SCORE_COMPONENTS:
        r = ratings[name]
        if r is None:
            unknown.append(name)
            r = criteria.unknown_credit_percent
        elif isinstance(r, bool) or not isinstance(r, int) or not 0 <= r <= 100:
            raise ScoreInputError(f"SCORE_RATING_INVALID:{name}")
        total += Fraction(criteria.weights[name] * r, 100)
    return ScoreResult(score=float(total), score_exact=total, band=band_for(total, criteria),
                       unknown_components=tuple(unknown), rule_version=criteria.rule_version)
