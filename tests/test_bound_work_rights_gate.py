from datetime import datetime, timedelta, timezone
import pytest
from ejs.services.bound_work_rights_gate import evaluate_bound_work_rights

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
TARGET = dict(candidate_id="candidate-1", employer_id="employer-1",
              vacancy_url="https://employer.example/jobs/123", country="Portugal")

def evidence():
    return dict(TARGET, state="VERIFIED", source_type="user_explicit",
                verified=True, current=True, source_ref="private:attestation",
                expires_at=NOW + timedelta(days=1))

@pytest.mark.parametrize("change", [
    {"candidate_id": "other"}, {"employer_id": "other"},
    {"vacancy_url": "https://employer.example/jobs/456"},
    {"expires_at": NOW}, {"expires_at": NOW - timedelta(seconds=1)},
    {"expires_at": None}, {"expires_at": "2027-01-01"},
    {"expires_at": datetime(2027, 1, 1)},
    {"country": "Germany"}, {"verified": False}, {"current": False},
])
def test_mismatch_or_expired_fails_closed(change):
    result = evaluate_bound_work_rights({**evidence(), **change}, TARGET, NOW)
    assert result["status"] == "BLOCKED"
    assert not any(result[k] for k in ("write_authorized", "upload_authorized", "submit_authorized"))

@pytest.mark.parametrize("target", [None, {}, {"country": "Portugal"}])
def test_missing_target_fails_closed(target):
    assert evaluate_bound_work_rights(evidence(), target, NOW)["status"] == "BLOCKED"

def test_matching_evidence_still_does_not_grant_mutation():
    result = evaluate_bound_work_rights(evidence(), TARGET, NOW)
    assert result["status"] == "REVIEW_READY"
    assert all(result[k] is False for k in ("write_authorized", "upload_authorized", "submit_authorized"))

@pytest.mark.parametrize("change", [
    {"candidate_id": " candidate-1"},
    {"candidate_id": "candidate-1 "},
    {"employer_id": " employer-1"},
    {"employer_id": "employer-1\n"},
    {"vacancy_url": "https://employer.example/jobs/123/"},
    {"vacancy_url": "https://employer.example/jobs/123?source=other"},
    {"expires_at": NOW + timedelta(microseconds=1)},
])
def test_identity_url_binding_and_boundary_are_fail_closed(change):
    # Exact target binding and expiry must never silently broaden authorization.
    result = evaluate_bound_work_rights({**evidence(), **change}, TARGET, NOW)
    if "expires_at" in change:
        assert result["status"] == "REVIEW_READY"
    else:
        assert result["status"] == "BLOCKED"
    assert all(result[k] is False for k in
               ("write_authorized", "upload_authorized", "submit_authorized"))

@pytest.mark.parametrize("now", [None, "2026-11-09", datetime(2026, 11, 9)])
def test_invalid_clock_fails_closed(now):
    result = evaluate_bound_work_rights(evidence(), TARGET, now)
    assert result["status"] == "BLOCKED"
    assert all(result[k] is False for k in
               ("write_authorized", "upload_authorized", "submit_authorized"))

@pytest.mark.parametrize("target_change", [
    {"candidate_id": "candidate-2"},
    {"employer_id": "employer-2"},
    {"vacancy_url": "https://other.example/jobs/123"},
    {"country": "Germany"},
    {"candidate_id": None},
    {"employer_id": 123},
    {"vacancy_url": ""},
])
def test_target_substitution_never_grants_authority(target_change):
    result = evaluate_bound_work_rights(evidence(), {**TARGET, **target_change}, NOW)
    assert result["status"] == "BLOCKED"
    assert result["write_authorized"] is False
    assert result["upload_authorized"] is False
    assert result["submit_authorized"] is False


@pytest.mark.parametrize("bad_evidence", [None, [], "VERIFIED", 1, {}])
def test_malformed_evidence_fails_closed(bad_evidence):
    result = evaluate_bound_work_rights(bad_evidence, TARGET, NOW)
    assert result["status"] == "BLOCKED"
    assert all(result[k] is False for k in
               ("write_authorized", "upload_authorized", "submit_authorized"))
