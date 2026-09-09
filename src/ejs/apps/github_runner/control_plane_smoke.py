from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import uuid

from ejs.contracts.github_executor import (
    ExecutionProvider,
    ExecutionResult,
    sign_execution_request,
)
from ejs.services.apps_script_control_plane import post_signed_request


def _request(*, secret: str, source_sha: str, operation: str, request_id: str, execution_id: str, payload=None, created_at=None, ttl=180):
    return sign_execution_request(
        secret=secret,
        request_id=request_id,
        execution_id=execution_id,
        operation=operation,
        source_sha=source_sha,
        nonce=f"nonce:{uuid.uuid4().hex}",
        payload=payload or {},
        created_at=created_at,
        ttl_seconds=ttl,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="GD-004 TEST-004B/C live Apps Script control-plane smoke")
    parser.add_argument("--url", required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()

    secret = os.environ.get("EJS_HMAC_SHARED_SECRET_TEST", "")
    if not secret:
        raise SystemExit("EJS_HMAC_SHARED_SECRET_TEST is required")

    run_key = os.environ.get("GITHUB_RUN_ID", uuid.uuid4().hex)
    base_execution = f"execution:gd004:apps-script:{run_key}"
    evidence: dict[str, object] = {"contract": "EJS-GH-EXEC-0.1", "source_sha": args.source_sha}

    health = _request(
        secret=secret, source_sha=args.source_sha, operation="health",
        request_id=f"request:{run_key}:health", execution_id=f"{base_execution}:health",
    )
    first = post_signed_request(args.url, health, secret=secret)
    second = post_signed_request(args.url, health, secret=secret)
    if not first.get("ok") or not second.get("ok"):
        raise RuntimeError("valid health request failed")
    if not bool((second.get("data") or {}).get("exact_replay")):
        raise RuntimeError("exact replay was not observed")
    evidence["health"] = "pass"
    evidence["exact_replay"] = "pass"

    invalid = replace(health, request_id=f"request:{run_key}:invalid", nonce=f"nonce:{uuid.uuid4().hex}", signature="0" * 64)
    invalid_response = post_signed_request(args.url, invalid, secret=secret)
    if invalid_response.get("ok") is not False or invalid_response.get("error_code") != "GD004_SIGNATURE_INVALID":
        raise RuntimeError("invalid signature was not rejected")
    evidence["invalid_signature_reject"] = "pass"

    expired = _request(
        secret=secret, source_sha=args.source_sha, operation="health",
        request_id=f"request:{run_key}:expired", execution_id=f"{base_execution}:expired",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10), ttl=60,
    )
    expired_response = post_signed_request(args.url, expired, secret=secret)
    if expired_response.get("ok") is not False or expired_response.get("error_code") != "GD004_REQUEST_EXPIRED":
        raise RuntimeError("expired request was not rejected")
    evidence["expired_reject"] = "pass"

    claim_execution = f"{base_execution}:claim-reconcile"
    claim = _request(
        secret=secret, source_sha=args.source_sha, operation="claim",
        request_id=f"request:{run_key}:claim", execution_id=claim_execution,
        payload={"purpose": "TEST-004C", "mutation_authority": False},
    )
    claim_first = post_signed_request(args.url, claim, secret=secret)
    claim_second = post_signed_request(args.url, claim, secret=secret)
    if (claim_first.get("data") or {}).get("claim_state") != "claimed":
        raise RuntimeError("first claim was not recorded")
    if (claim_second.get("data") or {}).get("claim_state") != "replay":
        raise RuntimeError("claim replay was not idempotent")
    evidence["claim_idempotency"] = "pass"

    now = datetime.now(timezone.utc)
    result = ExecutionResult(
        request_id=f"request:{run_key}:result-origin",
        execution_id=claim_execution,
        operation="inspect_read_only",
        source_sha=args.source_sha,
        provider=ExecutionProvider.GITHUB_HOSTED,
        started_at=(now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
        completed_at=now.isoformat().replace("+00:00", "Z"),
        typed_status="control_plane_smoke",
        mutation_count=0,
        upload_count=0,
        submit_count=0,
        evidence_summary="TEST-004C sanitized reconcile evidence",
    ).with_hash()
    result.validate_gd004()
    reconcile = _request(
        secret=secret, source_sha=args.source_sha, operation="reconcile_result",
        request_id=f"request:{run_key}:reconcile", execution_id=claim_execution,
        payload={"result": result.to_dict() if hasattr(result, "to_dict") else {**result.unsigned_dict(), "result_hash": result.result_hash}},
    )
    reconcile_first = post_signed_request(args.url, reconcile, secret=secret)
    reconcile_second = post_signed_request(args.url, reconcile, secret=secret)
    if (reconcile_first.get("data") or {}).get("reconcile_state") != "recorded":
        raise RuntimeError("first reconcile was not recorded")
    if (reconcile_second.get("data") or {}).get("reconcile_state") != "replay":
        raise RuntimeError("reconcile replay was not idempotent")
    evidence["reconcile_idempotency"] = "pass"
    evidence["mutation_count"] = 0
    evidence["upload_count"] = 0
    evidence["submit_count"] = 0
    evidence["test_environment_guard"] = "pass"

    print(json.dumps(evidence, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
