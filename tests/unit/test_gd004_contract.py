from datetime import datetime, timedelta, timezone

import pytest

from ejs.contracts.github_executor import (
    CapabilityFlags,
    ExecutionProvider,
    ExecutionResult,
    canonical_json,
    sign_execution_request,
    verify_execution_request,
)
from ejs.services.apps_script_gateway import verify_signed_response
from ejs.contracts.github_executor import hmac_sha256_hex, sha256_hex, EJS_GH_EXEC_VERSION

SECRET = "unit-test-secret"
SOURCE_SHA = "a" * 40
NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _signed(**overrides):
    args = dict(
        secret=SECRET,
        request_id="request:test:1",
        execution_id="execution:test:1",
        operation="health",
        source_sha=SOURCE_SHA,
        nonce="nonce-1234567890",
        payload={"z": 1, "a": {"b": True}},
        created_at=NOW,
        ttl_seconds=180,
    )
    args.update(overrides)
    return sign_execution_request(**args)


def test_canonical_json_is_sorted_and_compact():
    assert canonical_json({"z": 1, "a": {"y": 2, "x": 1}}) == '{"a":{"x":1,"y":2},"z":1}'


def test_signed_request_verifies():
    request = _signed()
    verified = verify_execution_request(request.to_dict(), secret=SECRET, now=NOW + timedelta(seconds=1))
    assert verified.request_id == request.request_id
    assert verified.capability_flags.browser_read is True
    assert verified.capability_flags.final_submit is False


def test_payload_tamper_rejected():
    value = _signed().to_dict()
    value["payload"] = {"z": 2}
    with pytest.raises(ValueError, match="payload_hash mismatch"):
        verify_execution_request(value, secret=SECRET, now=NOW)


def test_signature_tamper_rejected():
    value = _signed().to_dict()
    value["signature"] = "0" * 64
    with pytest.raises(PermissionError, match="invalid HMAC signature"):
        verify_execution_request(value, secret=SECRET, now=NOW)


def test_expired_request_rejected():
    request = _signed(ttl_seconds=10)
    with pytest.raises(PermissionError, match="request expired"):
        verify_execution_request(request.to_dict(), secret=SECRET, now=NOW + timedelta(seconds=11))


def test_mutating_capability_cannot_be_signed():
    with pytest.raises(PermissionError, match="mutating capabilities"):
        _signed(capability_flags=CapabilityFlags(form_value_write=True))


def test_wrong_environment_rejected_before_execution():
    value = _signed().to_dict()
    value["environment"] = "PROD"
    with pytest.raises(PermissionError, match="must target TEST"):
        verify_execution_request(value, secret=SECRET, now=NOW)


def test_execution_result_hash_and_zero_side_effects():
    result = ExecutionResult(
        request_id="request:test:1",
        execution_id="execution:test:1",
        operation="browser_read",
        source_sha=SOURCE_SHA,
        provider=ExecutionProvider.GITHUB_HOSTED,
        started_at="2026-09-09T12:00:00Z",
        completed_at="2026-09-09T12:00:01Z",
        typed_status="rendered",
    ).with_hash()
    result.validate_gd004()
    assert len(result.result_hash) == 64


def test_execution_result_rejects_side_effects():
    result = ExecutionResult(
        request_id="request:test:1",
        execution_id="execution:test:1",
        operation="browser_read",
        source_sha=SOURCE_SHA,
        provider=ExecutionProvider.GITHUB_HOSTED,
        started_at="2026-09-09T12:00:00Z",
        completed_at="2026-09-09T12:00:01Z",
        typed_status="rendered",
        submit_count=1,
    ).with_hash()
    with pytest.raises(PermissionError, match="forbidden side effect"):
        result.validate_gd004()


def test_signed_response_verifies_and_binds_identity():
    request = _signed()
    response_payload = {"ok": True, "data": {"status": "ok"}}
    response_hash = sha256_hex(canonical_json(response_payload))
    unsigned = {
        "contract_version": EJS_GH_EXEC_VERSION,
        "request_id": request.request_id,
        "execution_id": request.execution_id,
        "operation": request.operation,
        "response": response_payload,
        "response_hash": response_hash,
    }
    response = dict(unsigned)
    response["response_signature"] = hmac_sha256_hex(canonical_json(unsigned), SECRET)
    verified = verify_signed_response(response, secret=SECRET, request=request)
    assert verified["data"]["status"] == "ok"
