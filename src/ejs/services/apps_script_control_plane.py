from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import request as urllib_request

from ejs.contracts.github_executor import (
    SignedExecutionRequest,
    canonical_json,
    hmac_sha256_hex,
    sha256_hex,
)


def verify_signed_response(
    value: Mapping[str, Any], *, secret: str, request: SignedExecutionRequest
) -> Mapping[str, Any]:
    if str(value.get("contract_version", "")) != request.contract_version:
        raise ValueError("response contract_version mismatch")
    for field, expected in {
        "request_id": request.request_id,
        "execution_id": request.execution_id,
        "operation": request.operation,
    }.items():
        if str(value.get(field, "")) != expected:
            raise ValueError(f"response {field} mismatch")
    response = value.get("response")
    if not isinstance(response, Mapping):
        raise ValueError("response payload must be an object")
    expected_hash = sha256_hex(canonical_json(dict(response)))
    if str(value.get("response_hash", "")) != expected_hash:
        raise ValueError("response_hash mismatch")
    unsigned = {
        "contract_version": request.contract_version,
        "request_id": request.request_id,
        "execution_id": request.execution_id,
        "operation": request.operation,
        "response": dict(response),
        "response_hash": expected_hash,
    }
    expected_signature = hmac_sha256_hex(canonical_json(unsigned), secret)
    if str(value.get("response_signature", "")) != expected_signature:
        raise PermissionError("invalid response HMAC signature")
    return response


def post_signed_request(
    url: str,
    signed_request: SignedExecutionRequest,
    *,
    secret: str,
    timeout_seconds: int = 30,
) -> Mapping[str, Any]:
    if not url.startswith("https://script.google.com/"):
        raise ValueError("GD-004 control plane URL must be an Apps Script HTTPS URL")
    body = json.dumps(signed_request.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib_request.urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 - URL is strictly allowlisted above
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, Mapping):
        raise ValueError("Apps Script response must be an object")
    return verify_signed_response(parsed, secret=secret, request=signed_request)
