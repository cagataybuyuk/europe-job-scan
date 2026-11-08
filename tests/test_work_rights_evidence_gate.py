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


@pytest.mark.parametrize("bad_state", [[], {}, {"status": "VERIFIED"}, ["VERIFIED"], 1, True, None])
def test_unhashable_or_nonstring_status_fails_closed_without_exception(bad_state):
    result = evaluate_work_rights(evidence(state=bad_state), "Portugal")
    assert result["status"] == "BLOCKED"
    assert "UNKNOWN_STATUS" in result["reasons"]
    assert all(result[key] is False for key in ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("changes", [
    {"verified": "true"}, {"verified": 1}, {"current": "true"},
    {"current": 1}, {"source_type": " user_explicit "},
    {"state": " VERIFIED "}, {"state": "verified"},
    {"country": " Portugal "}, {"country": None},
    {"source_ref": "   "},
])
def test_ambiguous_legal_evidence_never_authorizes_mutation(changes):
    result = evaluate_work_rights(evidence(**changes), "Portugal")
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("target", [None, "", "   ", [], {}, 1, True])
def test_invalid_target_country_fails_closed(target):
    result = evaluate_work_rights(evidence(), target)
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("target,changes", [
    (" Portugal ", {}),
    ("Portugal\n", {}),
    ("Portugal", {"country": "Portugal\n"}),
    ("Portugal\t", {"country": "Portugal\t"}),
    ("Portugal", {"country": "Portugal\x00"}),
    ("Portugal", {"source_ref": "private:attestation\nforged"}),
    ("Portugal", {"source_ref": " private:attestation"}),
])
def test_country_and_provenance_ambiguity_fails_closed(target, changes):
    result = evaluate_work_rights(evidence(**changes), target)
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))

@pytest.mark.parametrize("target,changes", [
    ("Portugal", {"country": "Portu\u200bgal"}),
    ("Portu\u200bgal", {"country": "Portu\u200bgal"}),
    ("Portugal", {"source_ref": "private:attestation\u200b"}),
    ("Portugal", {"source_ref": "private:attestation\u00a0"}),
    ("Portugal", {"country": "Portugal\u202e"}),
])
def test_invisible_unicode_in_legal_evidence_fails_closed(target, changes):
    result = evaluate_work_rights(evidence(**changes), target)
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("target,changes", [
    ("Portugal", {"country": "Portu\u200bgal"}),
    ("Portu\u200bgal", {"country": "Portu\u200bgal"}),
    ("Portugal", {"source_ref": "private:attestation\u200b"}),
    ("Portugal", {"source_ref": "private:attestation\u00a0"}),
    ("Portugal", {"country": "Portugal\u202e"}),
])
def test_actual_unicode_codepoints_not_literal_escape_sequences(target, changes):
    assert any(ord(ch) > 127 for ch in target + str(changes))
    result = evaluate_work_rights(evidence(**changes), target)
    assert result["status"] == "BLOCKED"
    assert "COUNTRY_MISMATCH" in result["reasons"] or "MISSING_SOURCE_REF" in result["reasons"]
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("changes,target", [
    ({"source_type": "user_explicit\u200b"}, "Portugal"),
    ({"source_type": "user_explicit\u00a0"}, "Portugal"),
    ({"source_type": "user_explicit\n"}, "Portugal"),
    ({"source_ref": "private:attestation\u2028approved"}, "Portugal"),
    ({"country": "Germany"}, "Portugal"),
    ({"country": "Portugal"}, "Germany"),
])
def test_legal_provenance_and_country_must_match_exactly(changes, target):
    result = evaluate_work_rights(evidence(**changes), target)
    assert result["status"] == "BLOCKED"
    assert result["scope"] == "eligibility_metadata_only"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("extra", [
    {"write_authorized": True},
    {"upload_authorized": True, "submit_authorized": True},
    {"visa_sponsorship": "approved"},
    {"work_authorization": "yes", "approved_by": "employer"},
    {"scope": "PROD", "final_submit": True},
])
def test_untrusted_evidence_cannot_escalate_ats_authority(extra):
    result = evaluate_work_rights(evidence(**extra), "Portugal")
    assert result["scope"] == "eligibility_metadata_only"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("target,changes", [
    ("Portu\u200bgal", {}),
    ("Portugal\u202e", {"country": "Portugal\u202e"}),
    ("Portugal\u00a0", {"country": "Portugal\u00a0"}),
    ("Portugal", {"source_ref": "private:attestation\u2060"}),
    ("Portugal", {"source_ref": "private:attestation\x7f"}),
])
def test_additional_unicode_format_and_control_characters_fail_closed(target, changes):
    result = evaluate_work_rights(evidence(**changes), target)
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("canonical,spoofed", [
    ("state", " state"), ("state", "state "), ("state", "sta te"),
    ("state", "state\t"), ("state", "state\u00a0"),
    ("source_type", "source_type "), ("source_type", " source_type"),
    ("verified", "verified "), ("current", "current "),
    ("country", "country "), ("source_ref", "source_ref\u200b"),
])
def test_whitespace_spoofed_legal_keys_cannot_replace_required_evidence(canonical, spoofed):
    record = evidence()
    original = record.pop(canonical)
    record[spoofed] = original
    result = evaluate_work_rights(record, "Portugal")
    assert result["status"] == "BLOCKED"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))


@pytest.mark.parametrize("field,value", [
    ("state", "UNKNOWN"), ("state", "INELIGIBLE"),
    ("country", "Germany"), ("verified", False),
    ("current", False), ("source_ref", ""),
])
def test_eligibility_blockers_remain_denied_even_with_spoofed_write_approval(field, value):
    record = evidence(**{field: value})
    record.update(write_authorized=True, upload_authorized=True,
                  submit_authorized=True, final_submit=True)
    result = evaluate_work_rights(record, "Portugal")
    assert result["status"] == "BLOCKED"
    assert result["scope"] == "eligibility_metadata_only"
    assert all(result[key] is False for key in
               ("write_authorized", "upload_authorized", "submit_authorized"))
