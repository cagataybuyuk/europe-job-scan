"""Conservative, read-only evidence gate for MVP-04 vacancy selection.

A public posting is a discovery candidate, never authorization to apply.
Unknown sponsorship, work rights or mandatory qualifications cannot be
silently interpreted as positive evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class VacancyEvidence:
    employer_posting_verified_at: datetime | None
    employer_posting_open: bool | None
    employer_posting_url: str
    role_fit_verified: bool | None
    mandatory_requirements_verified: bool | None
    work_rights_verified: bool | None
    vacancy_sponsorship_verified: bool | None


def evaluate_vacancy_evidence(
    evidence: VacancyEvidence, *, now: datetime, max_age_hours: int = 48
) -> dict:
    """Pure classification. Does not fetch URLs or grant execution authority."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    if max_age_hours <= 0:
        raise ValueError("INVALID_FRESHNESS_WINDOW")
    reasons = []
    checked = evidence.employer_posting_verified_at
    if checked is None or checked.tzinfo is None or checked.utcoffset() is None:
        reasons.append("EMPLOYER_FRESHNESS_UNVERIFIED")
    else:
        age_seconds = (now.astimezone(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
        if age_seconds < 0 or age_seconds > max_age_hours * 3600:
            reasons.append("EMPLOYER_FRESHNESS_UNVERIFIED")
    if not evidence.employer_posting_url.startswith("https://jobs.smartrecruiters.com/"):
        reasons.append("EMPLOYER_SOURCE_UNVERIFIED")
    if evidence.employer_posting_open is not True:
        reasons.append("VACANCY_OPEN_UNVERIFIED")
    if evidence.role_fit_verified is not True:
        reasons.append("ROLE_FIT_UNVERIFIED")
    if evidence.mandatory_requirements_verified is not True:
        reasons.append("MANDATORY_REQUIREMENTS_UNVERIFIED")
    if evidence.work_rights_verified is not True:
        reasons.append("WORK_RIGHTS_UNVERIFIED")
    if evidence.vacancy_sponsorship_verified is not True:
        reasons.append("VACANCY_SPONSORSHIP_UNVERIFIED")
    return {
        "review_candidate": not reasons,
        "reasons": reasons,
        "execution_authorized": False,
        "ats_write_authorized": False,
        "submit_authorized": False,
    }
