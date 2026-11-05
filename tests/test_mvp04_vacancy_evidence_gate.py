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


TARGET = 'https://jobs.smartrecruiters.com/Version1/744000159638089--senior-business-analyst'
DECISION = 'USER-2026-11-05-SPONSORSHIP-UNKNOWN'


def unknown_target():
    return replace(complete(), employer_posting_url=TARGET, vacancy_sponsorship_verified=None,
                   explicit_no_sponsorship=False, existing_work_rights_required=False,
                   candidate_has_work_rights=False, candidate_requires_sponsorship=True)


def scoped_policy(**overrides):
    from ejs.services.vacancy_evidence_gate import SponsorshipReviewPolicy
    base = dict(employer_posting_url=TARGET, user_decision_ref=DECISION,
                allow_unknown_sponsorship=True, first_application_only=True)
    base.update(overrides)
    return SponsorshipReviewPolicy(**base)


def assert_no_execution_authority(result):
    assert result['execution_authorized'] is False
    assert result['ats_write_authorized'] is False
    assert result['submit_authorized'] is False
    if result['review_policy']:
        assert result['review_policy']['authorization_granted'] is False


def test_unknown_exception_is_explicit_target_bound_review_metadata_only():
    evidence = unknown_target()
    result = evaluate_vacancy_evidence(evidence, now=NOW, sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is True
    assert result['reasons'] == []
    assert result['sponsorship_status'] == 'UNKNOWN'
    assert evidence.vacancy_sponsorship_verified is None
    assert evidence.candidate_has_work_rights is False
    assert evidence.candidate_requires_sponsorship is True
    assert result['unknown_sponsorship_exception_applied'] is True
    assert result['review_policy'] == dict(user_decision_ref=DECISION, employer_posting_url=TARGET,
        allow_unknown_sponsorship=True, first_application_only=True, authorization_granted=False)
    assert_no_execution_authority(result)


def test_unknown_remains_blocked_without_opt_in_or_under_default_policy():
    from ejs.services.vacancy_evidence_gate import SponsorshipReviewPolicy
    default_policy = SponsorshipReviewPolicy(TARGET, DECISION)
    for policy in (None, default_policy, scoped_policy(allow_unknown_sponsorship=False)):
        result = evaluate_vacancy_evidence(unknown_target(), now=NOW, sponsorship_policy=policy)
        assert result['review_candidate'] is False
        assert 'VACANCY_SPONSORSHIP_UNVERIFIED' in result['reasons']
        assert result['sponsorship_status'] == 'UNKNOWN'
        assert result['unknown_sponsorship_exception_applied'] is False
        assert_no_execution_authority(result)


@pytest.mark.parametrize('field', [
    'explicit_no_sponsorship', 'existing_work_rights_required',
    'candidate_has_work_rights', 'candidate_requires_sponsorship',
])
def test_unknown_policy_requires_each_explicit_condition_and_candidate_fact(field):
    result = evaluate_vacancy_evidence(replace(unknown_target(), **{field: None}), now=NOW,
                                      sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is False
    assert 'VACANCY_SPONSORSHIP_UNVERIFIED' in result['reasons']
    assert result['unknown_sponsorship_exception_applied'] is False
    assert_no_execution_authority(result)


@pytest.mark.parametrize('field,value', [
    ('explicit_no_sponsorship', True), ('existing_work_rights_required', True),
    ('candidate_has_work_rights', True), ('candidate_requires_sponsorship', False),
    ('work_rights_verified', False), ('work_rights_verified', None),
    ('vacancy_sponsorship_verified', False),
])
def test_unknown_opt_in_cannot_override_prohibition_or_other_candidate_facts(field, value):
    result = evaluate_vacancy_evidence(replace(unknown_target(), **{field: value}), now=NOW,
                                      sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is False
    assert result['unknown_sponsorship_exception_applied'] is False
    if field == 'explicit_no_sponsorship': assert 'EXPLICIT_NO_SPONSORSHIP' in result['reasons']
    if field == 'existing_work_rights_required': assert 'EXISTING_WORK_RIGHTS_REQUIRED' in result['reasons']
    assert_no_execution_authority(result)


@pytest.mark.parametrize('field', ['explicit_no_sponsorship', 'existing_work_rights_required',
                                    'candidate_has_work_rights', 'candidate_requires_sponsorship'])
@pytest.mark.parametrize('value', ['false', 'true', 0, 1, [], {}])
def test_condition_booleans_are_never_coerced(field, value):
    result = evaluate_vacancy_evidence(replace(unknown_target(), **{field: value}), now=NOW,
                                      sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is False
    assert 'SPONSORSHIP_CONDITIONS_MALFORMED' in result['reasons']
    assert result['unknown_sponsorship_exception_applied'] is False
    assert_no_execution_authority(result)


@pytest.mark.parametrize('kwargs', [
    {'allow_unknown_sponsorship': 'true'}, {'allow_unknown_sponsorship': 1},
    {'allow_unknown_sponsorship': None}, {'first_application_only': False},
    {'first_application_only': 1}, {'first_application_only': 'true'},
    {'user_decision_ref': ''}, {'user_decision_ref': None},
    {'user_decision_ref': 'person@example.test'}, {'user_decision_ref': 'yes please'},
    {'user_decision_ref': 'x' * 129},
])
def test_malformed_or_unbounded_policy_never_opens_unknown_review(kwargs):
    result = evaluate_vacancy_evidence(unknown_target(), now=NOW, sponsorship_policy=scoped_policy(**kwargs))
    assert result['review_candidate'] is False
    assert 'SPONSORSHIP_REVIEW_POLICY_INVALID' in result['reasons']
    assert result['review_policy'] is None
    assert_no_execution_authority(result)


def test_dictionary_is_not_a_typed_review_policy():
    result = evaluate_vacancy_evidence(unknown_target(), now=NOW, sponsorship_policy={'allow_unknown_sponsorship': True})
    assert result['review_candidate'] is False
    assert 'SPONSORSHIP_REVIEW_POLICY_INVALID' in result['reasons']
    assert_no_execution_authority(result)


@pytest.mark.parametrize('url', [
    TARGET.replace('Version1/', 'OtherEmployer/'),
    TARGET.replace('744000159638089', '744000159638090'),
    TARGET.replace('--senior', '-senior'), TARGET + '/', TARGET + '?source=other',
])
def test_policy_cannot_transfer_to_another_employer_requisition_or_url(url):
    result = evaluate_vacancy_evidence(replace(unknown_target(), employer_posting_url=url), now=NOW,
                                      sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is False
    assert 'SPONSORSHIP_REVIEW_POLICY_TARGET_MISMATCH' in result['reasons']
    assert result['review_policy'] is None
    assert_no_execution_authority(result)


@pytest.mark.parametrize('field,value,reason', [
    ('employer_posting_verified_at', NOW - timedelta(days=3), 'EMPLOYER_FRESHNESS_UNVERIFIED'),
    ('employer_posting_verified_at', NOW + timedelta(minutes=1), 'EMPLOYER_FRESHNESS_UNVERIFIED'),
    ('employer_posting_verified_at', None, 'EMPLOYER_FRESHNESS_UNVERIFIED'),
    ('employer_posting_open', False, 'VACANCY_OPEN_UNVERIFIED'),
    ('employer_posting_open', None, 'VACANCY_OPEN_UNVERIFIED'),
    ('role_fit_verified', False, 'ROLE_FIT_UNVERIFIED'),
    ('role_fit_verified', None, 'ROLE_FIT_UNVERIFIED'),
    ('mandatory_requirements_verified', False, 'MANDATORY_REQUIREMENTS_UNVERIFIED'),
    ('mandatory_requirements_verified', None, 'MANDATORY_REQUIREMENTS_UNVERIFIED'),
    ('work_rights_verified', False, 'WORK_RIGHTS_UNVERIFIED'),
    ('work_rights_verified', None, 'WORK_RIGHTS_UNVERIFIED'),
])
def test_unknown_opt_in_preserves_all_other_freshness_fit_and_fact_gates(field, value, reason):
    result = evaluate_vacancy_evidence(replace(unknown_target(), **{field: value}), now=NOW,
                                      sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is False
    assert reason in result['reasons']
    assert result['unknown_sponsorship_exception_applied'] is False
    assert_no_execution_authority(result)


def test_policy_does_not_turn_untrusted_matching_source_into_employer_evidence():
    url = 'https://evil.example/Version1/744000159638089--senior-business-analyst'
    result = evaluate_vacancy_evidence(replace(unknown_target(), employer_posting_url=url), now=NOW,
                                      sponsorship_policy=scoped_policy(employer_posting_url=url))
    assert result['review_candidate'] is False
    assert 'EMPLOYER_SOURCE_UNVERIFIED' in result['reasons']
    assert result['unknown_sponsorship_exception_applied'] is False
    assert_no_execution_authority(result)


def test_explicit_denial_or_unmet_existing_rights_overrides_claimed_supported_sponsorship():
    for changes, reason in (({'explicit_no_sponsorship': True}, 'EXPLICIT_NO_SPONSORSHIP'),
                            ({'existing_work_rights_required': True, 'candidate_has_work_rights': False},
                             'EXISTING_WORK_RIGHTS_REQUIRED')):
        result = evaluate_vacancy_evidence(replace(complete(), **changes), now=NOW)
        assert result['review_candidate'] is False
        assert reason in result['reasons']
        assert_no_execution_authority(result)


@pytest.mark.parametrize('window', [float('nan'), float('inf'), float('-inf'), True, False,
                                  None, '48', 1.0, [], {}, 0, -1, 49, 365 * 24])
def test_invalid_or_unbounded_freshness_window_cannot_promote_stale_posting(window):
    evidence = replace(unknown_target(), employer_posting_verified_at=NOW - timedelta(days=365))
    with pytest.raises(ValueError, match='INVALID_FRESHNESS_WINDOW'):
        evaluate_vacancy_evidence(evidence, now=NOW, max_age_hours=window,
                                  sponsorship_policy=scoped_policy())


@pytest.mark.parametrize('hours,reviewable', [(48, True), (49, False), (365 * 24, False)])
def test_default_freshness_cap_is_preserved_under_unknown_sponsorship_policy(hours, reviewable):
    evidence = replace(unknown_target(), employer_posting_verified_at=NOW - timedelta(hours=hours))
    result = evaluate_vacancy_evidence(evidence, now=NOW, sponsorship_policy=scoped_policy())
    assert result['review_candidate'] is reviewable
    assert result['unknown_sponsorship_exception_applied'] is reviewable
    if not reviewable: assert 'EMPLOYER_FRESHNESS_UNVERIFIED' in result['reasons']
    assert_no_execution_authority(result)


def test_caller_can_shorten_but_cannot_extend_freshness_window():
    fresh = replace(complete(), employer_posting_verified_at=NOW - timedelta(hours=1))
    assert evaluate_vacancy_evidence(fresh, now=NOW, max_age_hours=1)['review_candidate'] is True
    stale = replace(fresh, employer_posting_verified_at=NOW - timedelta(hours=1, seconds=1))
    result = evaluate_vacancy_evidence(stale, now=NOW, max_age_hours=1)
    assert result['review_candidate'] is False
    assert 'EMPLOYER_FRESHNESS_UNVERIFIED' in result['reasons']
