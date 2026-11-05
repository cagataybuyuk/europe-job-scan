"""Conservative, read-only evidence gate for MVP-04 vacancy selection.

A public posting is a discovery candidate, never authorization to apply.
Unknown sponsorship, work rights or mandatory qualifications cannot be
silently interpreted as positive evidence. A separately established, exact-target
review policy may permit UNKNOWN sponsorship for the first single application;
that classification never grants browser/write/submit authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from urllib.parse import urlparse


MAX_FRESHNESS_WINDOW_HOURS = 48


@dataclass(frozen=True)
class VacancyEvidence:
    employer_posting_verified_at: datetime | None
    employer_posting_open: bool | None
    employer_posting_url: str
    role_fit_verified: bool | None
    mandatory_requirements_verified: bool | None
    work_rights_verified: bool | None
    vacancy_sponsorship_verified: bool | None
    explicit_no_sponsorship: bool | None = None
    existing_work_rights_required: bool | None = None
    candidate_has_work_rights: bool | None = None
    candidate_requires_sponsorship: bool | None = None


@dataclass(frozen=True)
class SponsorshipReviewPolicy:
    """Caller-established review scope, not evidence of sponsorship or authority.

    The decision reference must identify the actual user decision outside this
    pure classifier. This record cannot approve itself or consume an application
    slot; independent review and the single-application runner gates still apply.
    """
    employer_posting_url: str
    user_decision_ref: str
    allow_unknown_sponsorship: bool = False
    first_application_only: bool = True


def evaluate_vacancy_evidence(
    evidence: VacancyEvidence, *, now: datetime, max_age_hours: int = MAX_FRESHNESS_WINDOW_HOURS,
    sponsorship_policy: SponsorshipReviewPolicy | None = None,
) -> dict:
    """Pure classification. Does not fetch URLs or grant execution authority."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NOW_MUST_BE_TIMEZONE_AWARE")
    if type(max_age_hours) is not int or not 1 <= max_age_hours <= MAX_FRESHNESS_WINDOW_HOURS:
        raise ValueError("INVALID_FRESHNESS_WINDOW")
    reasons = []
    checked = evidence.employer_posting_verified_at
    if checked is None or checked.tzinfo is None or checked.utcoffset() is None:
        reasons.append("EMPLOYER_FRESHNESS_UNVERIFIED")
    else:
        age_seconds = (now.astimezone(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
        if age_seconds < 0 or age_seconds > max_age_hours * 3600:
            reasons.append("EMPLOYER_FRESHNESS_UNVERIFIED")
    try:
        source = urlparse(evidence.employer_posting_url)
        trusted_source = (
            source.scheme == "https"
            and source.hostname == "jobs.smartrecruiters.com"
            and source.port in (None, 443)
            and not source.username and not source.password
            and len(source.path.strip("/").split("/")) == 2
            and all(segment and segment not in {".", ".."} for segment in source.path.strip("/").split("/"))
            and not source.query and not source.fragment
        )
    except (TypeError, ValueError):
        trusted_source = False
    if not trusted_source:
        reasons.append("EMPLOYER_SOURCE_UNVERIFIED")
    if evidence.employer_posting_open is not True:
        reasons.append("VACANCY_OPEN_UNVERIFIED")
    if evidence.role_fit_verified is not True:
        reasons.append("ROLE_FIT_UNVERIFIED")
    if evidence.mandatory_requirements_verified is not True:
        reasons.append("MANDATORY_REQUIREMENTS_UNVERIFIED")
    # Verification means that the candidate's work-right facts are verified;
    # it does not turn a verified No into positive existing work authorization.
    if evidence.work_rights_verified is not True:
        reasons.append("WORK_RIGHTS_UNVERIFIED")
    condition_fields = (
        evidence.explicit_no_sponsorship, evidence.existing_work_rights_required,
        evidence.candidate_has_work_rights, evidence.candidate_requires_sponsorship,
    )
    if any(value is not None and type(value) is not bool for value in condition_fields):
        reasons.append("SPONSORSHIP_CONDITIONS_MALFORMED")
    if evidence.explicit_no_sponsorship is True:
        reasons.append("EXPLICIT_NO_SPONSORSHIP")
    if evidence.existing_work_rights_required is True and evidence.candidate_has_work_rights is not True:
        reasons.append("EXISTING_WORK_RIGHTS_REQUIRED")

    policy_valid = False
    review_policy = None
    if sponsorship_policy is not None:
        if (not isinstance(sponsorship_policy, SponsorshipReviewPolicy)
                or type(sponsorship_policy.allow_unknown_sponsorship) is not bool
                or sponsorship_policy.first_application_only is not True
                or not isinstance(sponsorship_policy.user_decision_ref, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", sponsorship_policy.user_decision_ref) is None):
            reasons.append("SPONSORSHIP_REVIEW_POLICY_INVALID")
        elif sponsorship_policy.employer_posting_url != evidence.employer_posting_url:
            reasons.append("SPONSORSHIP_REVIEW_POLICY_TARGET_MISMATCH")
        else:
            policy_valid = True
            review_policy = {
                "user_decision_ref": sponsorship_policy.user_decision_ref,
                "employer_posting_url": sponsorship_policy.employer_posting_url,
                "first_application_only": True,
                "allow_unknown_sponsorship": sponsorship_policy.allow_unknown_sponsorship,
                "authorization_granted": False,
            }
    unknown_exception = (
        policy_valid
        and sponsorship_policy.allow_unknown_sponsorship is True
        and evidence.vacancy_sponsorship_verified is None
        and evidence.work_rights_verified is True
        and evidence.explicit_no_sponsorship is False
        and evidence.existing_work_rights_required is False
        and evidence.candidate_has_work_rights is False
        and evidence.candidate_requires_sponsorship is True
    )
    if evidence.vacancy_sponsorship_verified is not True and not unknown_exception:
        reasons.append("VACANCY_SPONSORSHIP_UNVERIFIED")
    if evidence.vacancy_sponsorship_verified is True:
        sponsorship_status = "SUPPORTED"
    elif evidence.vacancy_sponsorship_verified is False:
        sponsorship_status = "NOT_VERIFIED"
    elif evidence.vacancy_sponsorship_verified is None:
        sponsorship_status = "UNKNOWN"
    else:
        sponsorship_status = "INVALID"
    return {
        "sponsorship_status": sponsorship_status,
        "unknown_sponsorship_exception_applied": unknown_exception and not reasons,
        "review_policy": review_policy,
        "review_candidate": not reasons,
        "reasons": reasons,
        "execution_authorized": False,
        "ats_write_authorized": False,
        "submit_authorized": False,
    }
