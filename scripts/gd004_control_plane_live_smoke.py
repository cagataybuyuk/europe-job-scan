from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
import sys
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from ejs.contracts.github_executor import (
    CapabilityFlags,
    ExecutionProvider,
    ExecutionResult,
    canonical_json,
    hmac_sha256_hex,
    sha256_hex,
    sign_execution_request,
)


def _post_json(url: str, payload: Mapping[str, Any], timeout: int = 30) -> dict[str, Any]:
    request = Request(
        url,
        data=canonical_json(dict(payload)).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "EuropeJobScan-GD004-TEST"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"control-plane HTTP failure: {type(exc).__name__}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("control-plane response was not JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("control-plane response must be an object")
    return value


def _verify_response(
    response: Mapping[str, Any], *, request: Mapping[str, Any], secret: str
) -> dict[str, Any]:
    for field in ("contract_version", "request_id", "execution_id", "operation", "response", "response_hash", "response_signature"):
        if field not in response:
            raise RuntimeError(f"signed response missing {field}")
    for field in ("request_id", "execution_id", "operation"):
        if str(response[field]) != str(request[field]):
            raise RuntimeError(f"signed response identity mismatch: {field}")
    payload = response["response"]
    if not isinstance(payload, dict):
        raise RuntimeError("signed response payload must be an object")
    expected_hash = sha256_hex(canonical_json(payload))
    if not hmac.compare_digest(str(response["response_hash"]), expected_hash):
        raise RuntimeError("signed response hash mismatch")
    unsigned = {
        "contract_version": response["contract_version"],
        "request_id": response["request_id"],
        "execution_id": response["execution_id"],
        "operation": response["operation"],
        "response": payload,
        "response_hash": response["response_hash"],
    }
    expected_signature = hmac_sha256_hex(canonical_json(unsigned), secret)
    if not hmac.compare_digest(str(response["response_signature"]), expected_signature):
        raise RuntimeError("signed response signature mismatch")
    return payload


def _send(url: str, signed_request, secret: str) -> dict[str, Any]:
    request_value = signed_request.to_dict() if hasattr(signed_request, "to_dict") else dict(signed_request)
    response = _post_json(url, request_value)
    return _verify_response(response, request=request_value, secret=secret)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _expect_error(response: Mapping[str, Any], code: str) -> None:
    if response.get("ok") is not False or response.get("error_code") != code:
        raise AssertionError(f"expected {code}, got sanitized response status")


def run(url: str, secret: str, source_sha: str) -> dict[str, Any]:
    flags = CapabilityFlags()
    execution_id = f"execution:gd004:control-plane:{uuid4().hex}"

    health = sign_execution_request(
        secret=secret,
        request_id=f"request:health:{uuid4().hex}",
        execution_id=execution_id,
        operation="health",
        source_sha=source_sha,
        nonce=f"nonce:{uuid4().hex}",
        capability_flags=flags,
    )
    health_response = _send(url, health, secret)
    if health_response.get("ok") is not True:
        raise AssertionError("valid health request was rejected")
    health_data = health_response.get("data") or {}
    if health_data.get("status") != "ok" or health_data.get("environment") != "TEST":
        raise AssertionError("health response did not prove TEST runtime")

    invalid = sign_execution_request(
        secret=secret,
        request_id=f"request:invalid-signature:{uuid4().hex}",
        execution_id=execution_id,
        operation="health",
        source_sha=source_sha,
        nonce=f"nonce:{uuid4().hex}",
        capability_flags=flags,
    ).to_dict()
    invalid["signature"] = ("0" if invalid["signature"][0] != "0" else "1") + invalid["signature"][1:]
    _expect_error(_send(url, invalid, secret), "GD004_SIGNATURE_INVALID")

    expired = sign_execution_request(
        secret=secret,
        request_id=f"request:expired:{uuid4().hex}",
        execution_id=execution_id,
        operation="health",
        source_sha=source_sha,
        nonce=f"nonce:{uuid4().hex}",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        ttl_seconds=60,
        capability_flags=flags,
    )
    _expect_error(_send(url, expired, secret), "GD004_REQUEST_EXPIRED")

    replay_nonce = f"nonce:collision:{uuid4().hex}"
    replay_a = sign_execution_request(
        secret=secret,
        request_id=f"request:replay-a:{uuid4().hex}",
        execution_id=execution_id,
        operation="health",
        source_sha=source_sha,
        nonce=replay_nonce,
        capability_flags=flags,
    )
    if _send(url, replay_a, secret).get("ok") is not True:
        raise AssertionError("replay baseline request rejected")
    replay_b = sign_execution_request(
        secret=secret,
        request_id=f"request:replay-b:{uuid4().hex}",
        execution_id=execution_id,
        operation="health",
        source_sha=source_sha,
        nonce=replay_nonce,
        payload={"collision_probe": True},
        capability_flags=flags,
    )
    _expect_error(_send(url, replay_b, secret), "GD004_NONCE_REPLAY_COLLISION")

    claim = sign_execution_request(
        secret=secret,
        request_id=f"request:claim:{uuid4().hex}",
        execution_id=execution_id,
        operation="claim",
        source_sha=source_sha,
        nonce=f"nonce:{uuid4().hex}",
        payload={"queue": "TEST", "purpose": "TEST-004C"},
        capability_flags=flags,
    )
    claim_first = _send(url, claim, secret)
    if claim_first.get("ok") is not True or (claim_first.get("data") or {}).get("claim_state") != "claimed":
        raise AssertionError("first claim was not recorded")
    claim_replay = _send(url, claim, secret)
    if claim_replay.get("ok") is not True or (claim_replay.get("data") or {}).get("claim_state") != "replay":
        raise AssertionError("exact claim replay was not idempotent")

    started_at = _iso_now()
    result = ExecutionResult(
        request_id=claim.request_id,
        execution_id=execution_id,
        operation="inspect_read_only",
        source_sha=source_sha,
        provider=ExecutionProvider.GITHUB_HOSTED,
        started_at=started_at,
        completed_at=_iso_now(),
        typed_status="synthetic_control_plane_pass",
        mutation_count=0,
        upload_count=0,
        submit_count=0,
        evidence_summary="TEST-004C sanitized claim/reconcile smoke",
    ).with_hash()
    result_value = result.unsigned_dict()
    result_value["result_hash"] = result.result_hash

    reconcile = sign_execution_request(
        secret=secret,
        request_id=f"request:reconcile:{uuid4().hex}",
        execution_id=execution_id,
        operation="reconcile_result",
        source_sha=source_sha,
        nonce=f"nonce:{uuid4().hex}",
        payload={"result": result_value},
        capability_flags=flags,
    )
    reconcile_first = _send(url, reconcile, secret)
    if reconcile_first.get("ok") is not True or (reconcile_first.get("data") or {}).get("reconcile_state") != "recorded":
        raise AssertionError("first reconcile was not recorded")
    reconcile_replay = _send(url, reconcile, secret)
    if reconcile_replay.get("ok") is not True or (reconcile_replay.get("data") or {}).get("reconcile_state") != "replay":
        raise AssertionError("exact reconcile replay was not idempotent")

    return {
        "contract": "EJS-GH-EXEC-0.1",
        "source_sha": source_sha,
        "environment": "TEST",
        "valid_health": "PASS",
        "invalid_signature": "REJECTED",
        "expired_request": "REJECTED",
        "nonce_collision": "REJECTED",
        "claim_first": "claimed",
        "claim_replay": "replay",
        "reconcile_first": "recorded",
        "reconcile_replay": "replay",
        "mutation_count": 0,
        "upload_count": 0,
        "submit_count": 0,
        "result_hash": result.result_hash,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GD-004 live TEST Apps Script HMAC/control-plane smoke")
    parser.add_argument("--url", default=os.environ.get("EJS_APPS_SCRIPT_ZERO_COST_URL_TEST", ""))
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    secret = os.environ.get("EJS_GH_HMAC_SECRET_TEST", "")
    if not args.url.startswith("https://script.google.com/macros/s/") or not args.url.endswith("/exec"):
        print("gd004-control-plane-smoke: invalid or missing TEST Web App URL", file=sys.stderr)
        return 2
    if not secret:
        print("gd004-control-plane-smoke: HMAC secret is not configured", file=sys.stderr)
        return 2
    try:
        summary = run(args.url, secret, args.source_sha)
    except Exception as exc:
        print(f"gd004-control-plane-smoke: FAIL ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
