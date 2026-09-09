from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import secrets
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen

from ejs.contracts.github_executor import (
    EJS_GH_EXEC_VERSION,
    SignedExecutionRequest,
    canonical_json,
    hmac_sha256_hex,
    sha256_hex,
    sign_execution_request,
)

Transport = Callable[[str, bytes, float], bytes]


def _default_transport(endpoint: str, body: bytes, timeout: float) -> bytes:
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - endpoint is explicit config
        return response.read()


def verify_signed_response(
    value: Mapping[str, Any], *, secret: str, request: SignedExecutionRequest
) -> Mapping[str, Any]:
    if value.get("contract_version") != EJS_GH_EXEC_VERSION:
        raise ValueError("response contract_version mismatch")
    for field, expected in (
        ("request_id", request.request_id),
        ("execution_id", request.execution_id),
        ("operation", request.operation),
    ):
        if value.get(field) != expected:
            raise ValueError(f"response {field} mismatch")
    response_payload = value.get("response")
    if not isinstance(response_payload, Mapping):
        raise ValueError("response payload must be an object")
    expected_hash = sha256_hex(canonical_json(dict(response_payload)))
    if not hmac.compare_digest(str(value.get("response_hash", "")), expected_hash):
        raise ValueError("response_hash mismatch")
    unsigned = {
        "contract_version": EJS_GH_EXEC_VERSION,
        "request_id": request.request_id,
        "execution_id": request.execution_id,
        "operation": request.operation,
        "response": dict(response_payload),
        "response_hash": expected_hash,
    }
    expected_signature = hmac_sha256_hex(canonical_json(unsigned), secret)
    if not hmac.compare_digest(str(value.get("response_signature", "")), expected_signature):
        raise PermissionError("invalid response HMAC signature")
    return response_payload


class SignedAppsScriptGatewayClient:
    def __init__(
        self,
        *,
        endpoint: str,
        secret: str,
        source_sha: str,
        transport: Transport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Apps Script endpoint must be HTTPS")
        if not secret:
            raise ValueError("HMAC secret required")
        self.endpoint = endpoint
        self.secret = secret
        self.source_sha = source_sha
        self.transport = transport or _default_transport
        self.timeout_seconds = timeout_seconds

    def post(
        self,
        *,
        operation: str,
        payload: Mapping[str, Any] | None = None,
        request_id: str,
        execution_id: str,
        created_at: datetime | None = None,
    ) -> Mapping[str, Any]:
        signed = sign_execution_request(
            secret=self.secret,
            request_id=request_id,
            execution_id=execution_id,
            operation=operation,
            source_sha=self.source_sha,
            nonce=secrets.token_hex(16),
            payload=payload or {},
            created_at=created_at or datetime.now(timezone.utc),
        )
        body = canonical_json(signed.to_dict()).encode("utf-8")
        raw = self.transport(self.endpoint, body, self.timeout_seconds)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("Apps Script response must be an object")
        return verify_signed_response(parsed, secret=self.secret, request=signed)
