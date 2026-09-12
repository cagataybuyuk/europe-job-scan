import pytest

from ejs.apps.github_runner.canary_policy import (
    CANARY_APPROVAL_PHRASE,
    R2_APPROVAL_PHRASE,
    CanaryEvidence,
    validate_canary_approval,
    validate_r2_release_gate,
)


def evidence(**overrides):
    base = dict(source_sha="a" * 40, target_mode="live", runtime_state="pre_submit_ready", form_value_write_attempts=7, file_upload_attempts=1, submit_attempts=0, observed_form_fingerprint="fp", final_form_fingerprint="fp", review_gate_reached=True, approved_asset=True)
    base.update(overrides)
    return CanaryEvidence.from_dict(base)


def test_canary_requires_exact_approval_phrase():
    validate_canary_approval(phrase=CANARY_APPROVAL_PHRASE, source_sha="a" * 40, expected_sha="a" * 40, target_mode="fixture")
    with pytest.raises(PermissionError):
        validate_canary_approval(phrase="yes", source_sha="a" * 40, expected_sha="a" * 40, target_mode="fixture")


def test_r2_gate_returns_ready_manifest_but_keeps_runtime_submit_off():
    result = validate_r2_release_gate(evidence(), expected_sha="a" * 40, approval_phrase=R2_APPROVAL_PHRASE, second_approver="owner@example.test")
    assert result["status"] == "READY_FOR_MANUAL_ENVIRONMENT_REVIEW"
    assert result["final_submit_runtime_flag"] is False
    assert result["kill_switch_engaged"] is True


@pytest.mark.parametrize("field", ["submit_attempts", "file_upload_attempts", "runtime_state"])
def test_r2_gate_fails_closed(field):
    values = {"submit_attempts": 1, "file_upload_attempts": 0, "runtime_state": "validator_blocked"}
    with pytest.raises(PermissionError):
        validate_r2_release_gate(evidence(**{field: values[field]}), expected_sha="a" * 40, approval_phrase=R2_APPROVAL_PHRASE, second_approver="owner@example.test")
