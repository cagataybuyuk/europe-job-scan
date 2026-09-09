from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
import secrets
from urllib.request import Request, urlopen

from ejs.contracts.github_executor import (
    EJS_GH_EXEC_VERSION,
    ExecutionProvider,
    ExecutionResult,
    canonical_json,
    sign_execution_request,
)
from ejs.services.apps_script_gateway import verify_signed_response


def _post(endpoint: str, payload: dict, timeout: float = 20.0) -> dict:
    body = canonical_json(payload).encode("utf-8")
    req = Request(endpoint, data=body, method="POST", headers={"Content-Type": "application/json; charset=utf-8"})
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - explicit configured Apps Script HTTPS endpoint
        parsed = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Apps Script response must be an object")
    return parsed


def _signed(secret: str, *, operation: str, source_sha: str, request_id: str, execution_id: str,
            nonce: str, payload: dict | None = None, created_at: datetime | None = None,
            ttl_seconds: int = 180):
    return sign_execution_request(
        secret=secret,
        request_id=request_id,
        execution_id=execution_id,
        operation=operation,
        source_sha=source_sha,
        nonce=nonce,
        payload=payload or {},
        created_at=created_at or datetime.now(timezone.utc),
        ttl_seconds=ttl_seconds,
    )


def _verify(endpoint: str, secret: str, request) -> dict:
    raw = _post(endpoint, request.to_dict())
    response = verify_signed_response(raw, secret=secret, request=request)
    if not isinstance(response, dict):
        raise ValueError("verified response must be an object")
    return response


def run_smoke(*, endpoint: str, secret: str, source_sha: str) -> dict:
    run_token = secrets.token_hex(8)
    now = datetime.now(timezone.utc)

    health = _signed(
        secret,
        operation="health",
        source_sha=source_sha,
        request_id=f"request:gd004b:{run_token}:health",
        execution_id=f"execution:gd004b:{run_token}:health",
        nonce=f"nonce-{run_token}-health",
        created_at=now,
    )
    first_health = _verify(endpoint, secret, health)
    second_health = _verify(endpoint, secret, health)
    if first_health.get("ok") is not True or second_health.get("ok") is not True:
        raise RuntimeError("health request did not succeed")
    if second_health.get("data", {}).get("exact_replay") is not True:
        raise RuntimeError("exact request replay was not detected")

    invalid = _signed(
        secret,
        operation="health",
        source_sha=source_sha,
        request_id=f"request:gd004b:{run_token}:invalid",
        execution_id=f"execution:gd004b:{run_token}:invalid",
        nonce=f"nonce-{run_token}-invalid",
        created_at=now,
    ).to_dict()
    invalid["signature"] = "0" * 64
    invalid_raw = _post(endpoint, invalid)
    invalid_request = type(health).from_mapping(invalid)
    invalid_response = verify_signed_response(invalid_raw, secret=secret, request=invalid_request)
    if invalid_response.get("ok") is not False or invalid_response.get("error_code") != "GD004_SIGNATURE_INVALID":
        raise RuntimeError("invalid signature was not rejected with the expected typed code")

    expired = _signed(
        secret,
        operation="health",
        source_sha=source_sha,
        request_id=f"request:gd004b:{run_token}:expired",
        execution_id=f"execution:gd004b:{run_token}:expired",
        nonce=f"nonce-{run_token}-expired",
        created_at=now - timedelta(minutes=2),
        ttl_seconds=10,
    )
    expired_response = _verify(endpoint, secret, expired)
    if expired_response.get("ok") is not False or expired_response.get("error_code") != "GD004_REQUEST_EXPIRED":
        raise RuntimeError("expired request was not rejected with the expected typed code")

    execution_id = f"execution:gd004c:{run_token}"
    claim = _signed(
        secret,
        operation="claim",
        source_sha=source_sha,
        request_id=f"request:gd004c:{run_token}:claim",
        execution_id=execution_id,
        nonce=f"nonce-{run_token}-claim",
        payload={"queue_key": f"synthetic:{run_token}"},
        created_at=now,
    )
    claim_first = _verify(endpoint, secret, claim)
    claim_second = _verify(endpoint, secret, claim)
    if claim_first.get("data", {}).get("claim_state") != "claimed":
        raise RuntimeError("initial execution claim did not record")
    if claim_second.get("data", {}).get("claim_state") != "replay":
        raise RuntimeError("execution claim replay was not idempotent")

    result = ExecutionResult(
        request_id=claim.request_id,
        execution_id=execution_id,
        operation="browser_read",
        source_sha=source_sha,
        provider=ExecutionProvider.GITHUB_HOSTED,
        started_at=now.isoformat().replace("+00:00", "Z"),
        completed_at=(now + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        typed_status="synthetic_control_plane_smoke",
        mutation_count=0,
        upload_count=0,
        submit_count=0,
        evidence_summary="sanitized synthetic TEST-004C reconcile evidence",
    ).with_hash()
    result.validate_gd004()
    result_payload = result.unsigned_dict()
    result_payload["result_hash"] = result.result_hash

    reconcile = _signed(
        secret,
        operation="reconcile_result",
        source_sha=source_sha,
        request_id=f"request:gd004c:{run_token}:reconcile",
        execution_id=execution_id,
        nonce=f"nonce-{run_token}-reconcile",
        payload={"result": result_payload},
        created_at=now,
    )
    reconcile_first = _verify(endpoint, secret, reconcile)
    reconcile_second = _verify(endpoint, secret, reconcile)
    if reconcile_first.get("data", {}).get("reconcile_state") != "recorded":
        raise RuntimeError("initial result reconcile did not record")
    if reconcile_second.get("data", {}).get("reconcile_state") != "replay":
        raise RuntimeError("result reconcile replay was not idempotent")

    return {
        "contract_version": EJS_GH_EXEC_VERSION,
        "source_sha": source_sha,
        "health": "pass",
        "exact_replay": "pass",
        "invalid_signature_rejected": "pass",
        "expired_request_rejected": "pass",
        "claim_idempotency": "pass",
        "reconcile_idempotency": "pass",
        "mutation_count": 0,
        "upload_count": 0,
        "submit_count": 0,
        "result_hash": result.result_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GD-004 TEST Apps Script control-plane runtime smoke")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()

    if not args.endpoint.startswith("https://script.google.com/"):
        raise SystemExit("endpoint must be a script.google.com HTTPS web app")
    secret = os.environ.get("EJS_GH_HMAC_SECRET_TEST", "")
    if not secret:
        raise SystemExit("EJS_GH_HMAC_SECRET_TEST is required")
    summary = run_smoke(endpoint=args.endpoint, secret=secret, source_sha=args.source_sha)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
