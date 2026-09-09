from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import secrets
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from ejs.contracts.github_executor import (
    EJS_GH_EXEC_VERSION,
    SignedExecutionRequest,
    canonical_json,
    hmac_sha256_hex,
    sha256_hex,
    sign_execution_request,
)

Transport = Callable[[str, bytes, float], bytes]
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class GatewayError(RuntimeError):
    """A verified failure returned by the TEST control plane (safe code only)."""


class _GoogleRedirectsOnly(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.hostname not in {
            'script.google.com', 'script.googleusercontent.com'
        } or target.username or target.password or target.port not in (None, 443):
            raise PermissionError('control-plane redirect outside Google Apps Script')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _default_transport(endpoint: str, body: bytes, timeout: float) -> bytes:
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with build_opener(_GoogleRedirectsOnly()).open(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('control-plane response exceeds size bound')
        return raw


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
        target = urlsplit(endpoint)
        if (target.scheme != 'https' or target.hostname != 'script.google.com'
                or not target.path.startswith('/macros/s/') or not target.path.endswith('/exec')
                or target.username or target.password or target.query or target.fragment
                or target.port not in (None, 443)):
            raise ValueError('endpoint must be a canonical Apps Script /exec URL')
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
        return self.send(signed)

    def send(self, signed: SignedExecutionRequest) -> Mapping[str, Any]:
        body = canonical_json(signed.to_dict()).encode("utf-8")
        raw = self.transport(self.endpoint, body, self.timeout_seconds)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError('control-plane response exceeds size bound')
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, Mapping):
            raise ValueError("Apps Script response must be an object")
        response = verify_signed_response(parsed, secret=self.secret, request=signed)
        if response.get('ok') is not True:
            code = str(response.get('error_code', 'GD004_GATEWAY_FAILURE'))
            if not all(c.isupper() or c.isdigit() or c in '_:-' for c in code) or len(code) > 120:
                code = 'GD004_GATEWAY_FAILURE'
            raise GatewayError(code)
        if not isinstance(response.get('data'), Mapping):
            raise ValueError('control-plane success data must be an object')
        return response['data']
