import pytest

from ejs.services.answer_provenance_gate import evaluate_answer_manifest


def field(key="first_name", value="Ada", source_type="candidate_verified",
          verified=True, approved=True):
    return {"key": key, "value": value, "provenance": {
        "source_type": source_type, "source_ref": "private:profile-v1",
        "verified": verified, "approved": approved}}


def test_verified_nonlegal_answer_is_review_ready_but_not_write_authorized():
    result = evaluate_answer_manifest({"fields": [field()]})
    assert result["status"] == "REVIEW_READY"
    assert not result["write_authorized"]
    assert not result["upload_authorized"]
    assert not result["submit_authorized"]


def test_unverified_answer_fails_closed():
    result = evaluate_answer_manifest({"fields": [field(verified=False)]})
    assert result["status"] == "BLOCKED"
    assert "field_0:UNVERIFIED_SOURCE" in result["reasons"]


def test_legal_answer_requires_explicit_user_confirmation():
    result = evaluate_answer_manifest({"fields": [field("work_authorization", "yes")]})
    assert "field_0:USER_CONFIRMATION_REQUIRED" in result["reasons"]


def test_explicit_verified_legal_answer_still_does_not_authorize_write():
    result = evaluate_answer_manifest({"fields": [
        field("work_authorization", "yes", source_type="user_explicit")]})
    assert result["status"] == "REVIEW_READY"
    assert result["write_authorized"] is False


def test_missing_source_and_duplicate_key_block():
    one = field()
    one["provenance"]["source_ref"] = ""
    result = evaluate_answer_manifest({"fields": [one, field()]})
    assert result["status"] == "BLOCKED"
    assert "field_0:MISSING_SOURCE_REF" in result["reasons"]
    assert "field_1:INVALID_OR_DUPLICATE_KEY" in result["reasons"]


def test_unknown_source_and_no_fields_block():
    assert evaluate_answer_manifest({"fields": []})["status"] == "BLOCKED"
    assert evaluate_answer_manifest(None)["status"] == "BLOCKED"
    assert evaluate_answer_manifest({"fields": [field(source_type="web_guess")]})["status"] == "BLOCKED"


@pytest.mark.parametrize('key', [' Work_Authorization ', 'WORK.AUTHORIZATION', 'work_right.eu_eea'])
def test_legal_aliases_cannot_bypass_user_fact_gate(key):
    assert evaluate_answer_manifest({'fields': [field(key, 'yes')]})['status'] == 'BLOCKED'


def test_canonical_alias_duplicate_and_unknown_fields_fail_closed():
    assert evaluate_answer_manifest({'fields': [field(), field(' CANDIDATE.FIRST_NAME ')]})['status'] == 'BLOCKED'
    result = evaluate_answer_manifest({'fields': [field('PRIVATE_EMAIL_OR_TOKEN_IN_KEY', 'secret')]})
    assert result['status'] == 'BLOCKED'
    assert 'PRIVATE_EMAIL' not in str(result) and 'secret' not in str(result)


@pytest.mark.parametrize('source_type', [{}, [], None])
def test_malformed_source_type_is_a_blocked_report_not_an_exception(source_type):
    assert evaluate_answer_manifest({'fields': [field(source_type=source_type)]})['status'] == 'BLOCKED'


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf'), {}, [], '', 'x' * 10001])
def test_invalid_or_unbounded_values_are_not_review_ready(value):
    assert evaluate_answer_manifest({'fields': [field(value=value)]})['status'] == 'BLOCKED'


@pytest.mark.parametrize('manifest', [None, {}, {'fields': []}, {'fields': [field()]}, {'fields': [field()] * 101}])
def test_every_path_has_explicit_no_authority_and_no_claim_of_fact_verification(manifest):
    result = evaluate_answer_manifest(manifest)
    assert result['write_authorized'] is result['upload_authorized'] is result['submit_authorized'] is False
    assert result['source_verification_performed'] is result['fact_comparison_performed'] is False
    assert result['runtime_manifest_required'] is True
    assert result['scope'] == 'schema_metadata_only'


def test_existing_submit_policy_sponsorship_canonical_key_is_recognized():
    result = evaluate_answer_manifest({'fields': [field('work.requires_sponsorship', True, source_type='user_explicit')]})
    assert result['status'] == 'REVIEW_READY'
    assert result['write_authorized'] is False


@pytest.mark.parametrize('key', [
    'work . authorization', ' work_right . eu_eea ',
    'work . requires_sponsorship', 'work_right . sponsorship_required',
    'compensation . expected_salary', 'consent . marketing',
    ' WORK _ AUTHORIZATION ', 'visa _ sponsorship',
])
def test_whitespace_normalized_legal_keys_still_require_user_confirmation(key):
    result = evaluate_answer_manifest({'fields': [field(key, 'yes')]})
    assert result['status'] == 'BLOCKED'
    assert 'field_0:USER_CONFIRMATION_REQUIRED' in result['reasons']
    assert result['write_authorized'] is False
    assert result['submit_authorized'] is False


def test_normalized_legal_alias_duplicate_is_blocked():
    result = evaluate_answer_manifest({'fields': [
        field('work . authorization', 'yes', source_type='user_explicit'),
        field('work_authorization', 'yes', source_type='user_explicit'),
    ]})
    assert 'field_1:INVALID_OR_DUPLICATE_KEY' in result['reasons']
    assert result['status'] == 'BLOCKED'
