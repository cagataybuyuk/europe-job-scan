"""TEST-only negative and positive schema tests. Never dispatch a task."""
from dataclasses import FrozenInstanceError, asdict, replace
import json

import pytest

from ejs.services.offline_task_manifest import ManifestValidation, validate_manifest_json


SHA = "a" * 40
BASE = {
    "schema_version": "GD005-OFFLINE-EXEC-1",
    "environment": "TEST",
    "task_id": "pytest_core",
    "source_sha": SHA,
    "allowlist_revision": "v1",
    "approval_state": "review_required",
    "network": "denied",
    "workspace": "isolated",
    "max_wall_seconds": 300,
    "max_retries": 0,
    "payload_class": "synthetic_only",
}


def validate(change=None, *, expected_sha=SHA):
    return validate_manifest_json(json.dumps({**BASE, **(change or {})}),
                                  expected_source_sha=expected_sha)


def assert_denied(result):
    assert not result.schema_valid
    assert result.reason
    assert result.execution_authorized is False
    assert result.permitted_actions == ()


@pytest.mark.parametrize("task", ["secret_scan", "authority_boundaries", "pytest_core"])
def test_fixed_test_catalog_is_schema_valid_but_never_authorized(task):
    result = validate({"task_id": task})
    assert result.schema_valid and result.reason == "schema_valid_not_authorized"
    assert result.execution_authorized is False and result.permitted_actions == ()
    with pytest.raises(FrozenInstanceError):
        result.execution_authorized = True


@pytest.mark.parametrize("field,value", [
    ("schema_version", "v2"), ("allowlist_revision", "v2"),
    ("environment", "PROD"), ("environment", ""),
    ("task_id", "upload_cv"), ("task_id", "final_submit"),
    ("task_id", "shell"), ("task_id", "python -c code"),
    ("source_sha", "main"), ("source_sha", "b" * 40),
    ("source_sha", "A" * 40), ("source_sha", "a" * 39),
    ("approval_state", "approved"), ("approval_state", "true"),
    ("network", "required"), ("network", "allowed"),
    ("workspace", "home"), ("workspace", "/Users"),
    ("max_wall_seconds", 0), ("max_wall_seconds", -1),
    ("max_wall_seconds", 301), ("max_wall_seconds", True),
    ("max_retries", 1), ("max_retries", -1),
    ("max_retries", False), ("payload_class", "real_candidate"),
    ("payload_class", "production"), ("schema_version", None),
    ("task_id", ["pytest_core"]), ("environment", True),
    ("source_sha", {"value": SHA}), ("network", None),
    ("workspace", []), ("approval_state", {"approved": True}),
])
def test_untrusted_or_out_of_scope_fields_fail_closed(field, value):
    assert_denied(validate({field: value}))


@pytest.mark.parametrize("extra", [
    {"command": "python -c code"},
    {"args": ["--run"]},
    {"endpoint": "https://example.invalid"},
    {"provider": "local_macbook"},
    {"approval_ref": "GD005:TEST:62"},
    {"checkpoint_ref": "sha256:synthetic"},
    {"environment_variables": {"MARKER": "private"}},
    {"approval": True},
])
def test_unknown_keys_cannot_grant_actions(extra):
    assert_denied(validate(extra))


@pytest.mark.parametrize("omitted", sorted(BASE))
def test_missing_fields_rejected(omitted):
    data = {key: value for key, value in BASE.items() if key != omitted}
    assert_denied(validate_manifest_json(json.dumps(data), expected_source_sha=SHA))


@pytest.mark.parametrize("raw", [
    None, False, {}, [], 7, b"{}", "", "not-json",
    '["TEST"]', "null", '{"schema_version":NaN}',
    '{"schema_version":Infinity}',
    '{"task_id":"pytest_core","task_id":"secret_scan"}',
    '{"nested":{"x":1,"x":2}}',
    '{"a":',
    "\ud800",
])
def test_malformed_encoding_and_duplicate_json_fail_closed(raw):
    assert_denied(validate_manifest_json(raw, expected_source_sha=SHA))


@pytest.mark.parametrize("supplied_sha", ["", "main", "B" * 40, "b" * 40, None, True, 7, {}])
def test_expected_source_hash_is_only_compared_and_must_be_exact(supplied_sha):
    assert_denied(validate(expected_sha=supplied_sha))


def test_size_limit_and_data_cannot_be_leaked_into_reason():
    secret_marker = "PRIVATE_FIXTURE_STRING"
    result = validate_manifest_json(json.dumps({**BASE, "extra": secret_marker}),
                                    expected_source_sha=SHA)
    assert_denied(result)
    assert secret_marker not in result.reason
    assert_denied(validate_manifest_json(" " * 4097, expected_source_sha=SHA))


def test_no_provider_dispatch_claim_even_when_caller_and_sha_match():
    report = validate()
    assert report.schema_valid
    assert not report.execution_authorized
    assert not report.permitted_actions
    assert not hasattr(report, "provider_executed")
    assert not hasattr(report, "command")
    assert not hasattr(report, "approval_token")


def test_value_order_is_immaterial_but_authority_is_always_false():
    reversed_data = dict(reversed(list(BASE.items())))
    report = validate_manifest_json(json.dumps(reversed_data), expected_source_sha=SHA)
    assert report == validate()


def test_max_wall_time_bounded_and_review_only():
    for value in (1, 60, 300):
        report = validate({"max_wall_seconds": value})
        assert report.schema_valid
        assert report.execution_authorized is False


def test_authorization_cannot_be_injected_in_result_construction_or_replace():
    with pytest.raises(TypeError):
        ManifestValidation(True, "schema_valid_not_authorized",
                           execution_authorized=True)
    with pytest.raises(TypeError):
        ManifestValidation(True, "schema_valid_not_authorized",
                           permitted_actions=("submit",))
    result = validate()
    with pytest.raises(TypeError):
        replace(result, execution_authorized=True)
    with pytest.raises(TypeError):
        replace(result, permitted_actions=("submit",))
    assert asdict(result) == {
        "schema_valid": True, "reason": "schema_valid_not_authorized"
    }


def test_result_properties_cannot_be_force_assigned_or_shadowed():
    result = validate()
    with pytest.raises((AttributeError, FrozenInstanceError)):
        object.__setattr__(result, "execution_authorized", True)
    with pytest.raises((AttributeError, FrozenInstanceError)):
        object.__setattr__(result, "permitted_actions", ("submit",))
    assert result.execution_authorized is False
    assert result.permitted_actions == ()
