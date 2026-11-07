import pytest
from ejs.services.work_rights_evidence_gate import evaluate_work_rights


def evidence(**changes):
    result = dict(state="VERIFIED", country="Portugal", source_type="user_explicit",
                  verified=True, current=True, source_ref="private:user-attestation")
    result.update(changes)
    return result


@pytest.mark.parametrize("changes", [
    {"state": "UNKNOWN"}, {"state": "INELIGIBLE"}, {"state": "yes"},
    {"country": "Germany"}, {"source_type": "web_guess"},
    {"verified": False}, {"current": False}, {"source_ref": ""},
])
def test_unsafe_or_mismatched_evidence_fails_closed(changes):
    result = evaluate_work_rights(evidence(**changes), "Portugal")
    assert result["status"] == "BLOCKED"
    assert not any(result[key] for key in ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("value", [None, {}, [], "yes"])
def test_invalid_evidence_fails_closed(value):
    assert evaluate_work_rights(value, "Portugal")["status"] == "BLOCKED"


def test_review_ready_never_authorizes_employer_mutation():
    result = evaluate_work_rights(evidence(), "Portugal")
    assert result["status"] == "REVIEW_READY"
    assert result["scope"] == "eligibility_metadata_only"
    assert result["submit_authorized"] is result["upload_authorized"] is result["write_authorized"] is False


def test_diagnostics_never_leak_source_reference():
    secret = "private:SECRET"
    result = evaluate_work_rights(evidence(state="UNKNOWN", source_ref=secret), "Portugal")
    assert secret not in str(result)
