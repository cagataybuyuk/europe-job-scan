from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from ejs.services.vacancy_evidence_gate import VacancyEvidence, evaluate_vacancy_evidence


NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def complete():
    return VacancyEvidence(
        employer_posting_verified_at=NOW - timedelta(hours=1),
        employer_posting_open=True,
        employer_posting_url="https://jobs.smartrecruiters.com/Example/744000159638089",
        role_fit_verified=True,
        mandatory_requirements_verified=True,
        work_rights_verified=True,
        vacancy_sponsorship_verified=True,
    )


def test_complete_evidence_is_review_only_not_execution():
    result = evaluate_vacancy_evidence(complete(), now=NOW)
    assert result["review_candidate"] is True
    assert result["execution_authorized"] is False
    assert result["ats_write_authorized"] is False
    assert result["submit_authorized"] is False


@pytest.mark.parametrize("field,reason", [
    ("employer_posting_open", "VACANCY_OPEN_UNVERIFIED"),
    ("role_fit_verified", "ROLE_FIT_UNVERIFIED"),
    ("mandatory_requirements_verified", "MANDATORY_REQUIREMENTS_UNVERIFIED"),
    ("work_rights_verified", "WORK_RIGHTS_UNVERIFIED"),
    ("vacancy_sponsorship_verified", "VACANCY_SPONSORSHIP_UNVERIFIED"),
])
def test_unknown_or_false_is_not_positive_evidence(field, reason):
    for value in (None, False):
        result = evaluate_vacancy_evidence(replace(complete(), **{field: value}), now=NOW)
        assert result["review_candidate"] is False
        assert reason in result["reasons"]


@pytest.mark.parametrize("checked", [
    None, NOW - timedelta(days=3), NOW + timedelta(minutes=1),
    datetime(2026, 11, 5),
])
def test_missing_stale_future_or_naive_freshness_fails_closed(checked):
    result = evaluate_vacancy_evidence(
        replace(complete(), employer_posting_verified_at=checked), now=NOW
    )
    assert "EMPLOYER_FRESHNESS_UNVERIFIED" in result["reasons"]


def test_untrusted_source_cannot_be_promoted():
    result = evaluate_vacancy_evidence(
        replace(complete(), employer_posting_url="https://other.example/job"), now=NOW
    )
    assert "EMPLOYER_SOURCE_UNVERIFIED" in result["reasons"]


def test_naive_now_and_invalid_window_rejected():
    with pytest.raises(ValueError):
        evaluate_vacancy_evidence(complete(), now=datetime(2026, 11, 5))
    with pytest.raises(ValueError):
        evaluate_vacancy_evidence(complete(), now=NOW, max_age_hours=0)


@pytest.mark.parametrize("url", [
    "https://jobs.smartrecruiters.com.evil.example/Example/123",
    "https://jobs.smartrecruiters.com@evil.example/Example/123",
    "http://jobs.smartrecruiters.com/Example/123",
    "https://jobs.smartrecruiters.com:444/Example/123",
    "https://user:password@jobs.smartrecruiters.com/Example/123",
    "https://jobs.smartrecruiters.com:invalid/Example/123",
    "https://jobs.smartrecruiters.com/",
])
def test_spoofed_or_malformed_ats_origin_is_not_trusted(url):
    result = evaluate_vacancy_evidence(
        replace(complete(), employer_posting_url=url), now=NOW
    )
    assert result["review_candidate"] is False
    assert "EMPLOYER_SOURCE_UNVERIFIED" in result["reasons"]
    assert result["execution_authorized"] is False


@pytest.mark.parametrize("url", [
    "https://jobs.smartrecruiters.com/not-a-job",
    "https://jobs.smartrecruiters.com/Employer/123/extra",
    "https://jobs.smartrecruiters.com/Employer/123?redirect=https://evil.example",
    "https://jobs.smartrecruiters.com/Employer/123#fragment",
    "https://jobs.smartrecruiters.com//123",
    "https://jobs.smartrecruiters.com/Employer/",
])
def test_noncanonical_posting_url_is_not_reviewable(url):
    result = evaluate_vacancy_evidence(
        replace(complete(), employer_posting_url=url), now=NOW
    )
    assert "EMPLOYER_SOURCE_UNVERIFIED" in result["reasons"]
    assert result["review_candidate"] is False
    assert result["ats_write_authorized"] is False
    assert result["submit_authorized"] is False
