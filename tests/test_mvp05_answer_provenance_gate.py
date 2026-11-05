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
    assert "first_name:UNVERIFIED_SOURCE" in result["reasons"]


def test_legal_answer_requires_explicit_user_confirmation():
    result = evaluate_answer_manifest({"fields": [field("work_authorization", "yes")]})
    assert "work_authorization:USER_CONFIRMATION_REQUIRED" in result["reasons"]


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
    assert "first_name:MISSING_SOURCE_REF" in result["reasons"]
    assert "field_1:INVALID_OR_DUPLICATE_KEY" in result["reasons"]


def test_unknown_source_and_no_fields_block():
    assert evaluate_answer_manifest({"fields": []})["status"] == "BLOCKED"
    assert evaluate_answer_manifest(None)["status"] == "BLOCKED"
    assert evaluate_answer_manifest({"fields": [field(source_type="web_guess")]})["status"] == "BLOCKED"
