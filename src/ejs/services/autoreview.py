"""AUTOREVIEW (K2 version, ROADMAP Faz 1.5). Produces a LABEL only; nothing is submitted.

Order (first match wins, fail-closed):
 1. invalid facts / contradictions                     -> needs_user
 2. unknown vacancy status (evidence missing)           -> needs_user
 3. a configured hard blocker is explicitly True        -> auto_skip
 4. no known score component at all                     -> needs_user
 5. score >= auto_apply.opportunity_min                 -> auto_apply (sponsorship Unknown included)
 6. score below the minimum                             -> auto_skip (score_below_min)
A blocker signal of ``None`` (Unknown) never blocks and never approves by itself.
``previous_sheet_rule`` is kept in criteria for the record and is NOT consulted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ejs.services.criteria_loader import Criteria
from ejs.services.opportunity_score import ScoreInputError, ScoreResult, compute_opportunity

BLOCKER_FIELDS = {
    "explicit-no-sponsorship": "explicit_no_sponsorship",
    "unlimited-work-right-required": "requires_unlimited_work_right",
    "mandatory-language-unmet": "mandatory_language_unmet",
    "closed-vacancy": None,            # derived from vacancy_open is False
    "junior-or-technical-only": "junior_or_technical_only",
}


@dataclass(frozen=True)
class ListingFacts:
    listing_id: str
    ratings: Mapping[str, int | None]
    vacancy_open: bool | None = None
    explicit_no_sponsorship: bool | None = None
    requires_unlimited_work_right: bool | None = None
    mandatory_language_unmet: bool | None = None
    junior_or_technical_only: bool | None = None
    contradictions: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Decision:
    listing_id: str
    label: str                     # auto_apply | auto_skip | needs_user
    reason_code: str
    reason_codes: tuple[str, ...]
    confidence: str                # high | medium | low
    rule_version: str
    score: float | None
    band: str | None
    unknown_components: tuple[str, ...]


def _needs_user(facts: ListingFacts, code: str, criteria: Criteria,
                result: ScoreResult | None = None) -> Decision:
    return Decision(facts.listing_id, "needs_user", code, (code,), "low", criteria.rule_version,
                    result.score if result else None, result.band if result else None,
                    result.unknown_components if result else ())


def _signal(facts: ListingFacts, code: str) -> bool | None:
    if code == "closed-vacancy":
        return None if facts.vacancy_open is None else (not facts.vacancy_open)
    return getattr(facts, BLOCKER_FIELDS[code])


def decide(facts: ListingFacts, criteria: Criteria) -> Decision:
    try:
        if not isinstance(facts.listing_id, str) or not facts.listing_id:
            raise ScoreInputError("FACTS_LISTING_ID_MISSING")
        for name in ("vacancy_open", "explicit_no_sponsorship", "requires_unlimited_work_right",
                     "mandatory_language_unmet", "junior_or_technical_only"):
            v = getattr(facts, name)
            if v is not None and not isinstance(v, bool):
                raise ScoreInputError(f"FACTS_NOT_TRISTATE:{name}")
        result = compute_opportunity(facts.ratings, criteria)
    except ScoreInputError:
        return _needs_user(facts, "invalid_facts", criteria)

    if facts.contradictions:
        return _needs_user(facts, "contradiction", criteria, result)
    if facts.vacancy_open is None:
        return _needs_user(facts, "vacancy_evidence_missing", criteria, result)

    hit = tuple(f"blocker:{c}" for c in criteria.hard_blockers if _signal(facts, c) is True)
    if hit:
        return Decision(facts.listing_id, "auto_skip", hit[0], hit, "high", criteria.rule_version,
                        result.score, result.band, result.unknown_components)

    if len(result.unknown_components) == len(criteria.weights):
        return _needs_user(facts, "evidence_missing", criteria, result)

    conf = "medium" if result.unknown_components else "high"
    if result.score_exact >= criteria.auto_apply_min:
        return Decision(facts.listing_id, "auto_apply", "score_at_or_above_min", ("score_at_or_above_min",),
                        conf, criteria.rule_version, result.score, result.band, result.unknown_components)
    return Decision(facts.listing_id, "auto_skip", "score_below_min", ("score_below_min",),
                    "medium", criteria.rule_version, result.score, result.band, result.unknown_components)
