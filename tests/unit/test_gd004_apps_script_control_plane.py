from datetime import datetime, timezone

import pytest

from ejs.contracts.github_executor import (
    canonical_json,
    hmac_sha256_hex,
    sha256_hex,
    sign_execution_request,
)
from ejs.services.apps_script_control_plane import verify_signed_response


SECRET = "synthetic-test-secret-that-is-long-enough"
SHA = "a" * 40


def _request():
    return sign_execution_request(
        secret=SECRET,
        request_id="request:test:response",
        execution_id="execution:test:response",
        operation="health",
        source_sha=SHA,
        nonce="nonce:test:response",
        payload={},
        created_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        ttl_seconds=180,
    )


def _response(request, payload=None):
    response = payload or {"ok": True, "data": {"environment": "TEST"}}
    response_hash = sha256_hex(canonical_json(response))
    unsigned = {
        "contract_version": request.contract_version,
        "request_id": request.request_id,
        "execution_id": request.execution_id,
        "operation": request.operation,
        "response": response,
        "response_hash": response_hash,
    }
    return {**unsigned, "response_signature": hmac_sha256_hex(canonical_json(unsigned), SECRET)}


def test_signed_response_verifies():
    request = _request()
    response = verify_signed_response(_response(request), secret=SECRET, request=request)
    assert response["ok"] is True
    assert response["data"]["environment"] == "TEST"


def test_tampered_response_hash_rejected():
    request = _request()
    value = _response(request)
    value["response"]["data"]["environment"] = "PROD"
    with pytest.raises(ValueError, match="response_hash mismatch"):
        verify_signed_response(value, secret=SECRET, request=request)


def test_tampered_response_signature_rejected():
    request = _request()
    value = _response(request)
    value["response_signature"] = "0" * 64
    with pytest.raises(PermissionError, match="invalid response HMAC"):
        verify_signed_response(value, secret=SECRET, request=request)


def test_response_identity_mismatch_rejected():
    request = _request()
    value = _response(request)
    value["execution_id"] = "execution:other"
    with pytest.raises(ValueError, match="response execution_id mismatch"):
        verify_signed_response(value, secret=SECRET, request=request)
