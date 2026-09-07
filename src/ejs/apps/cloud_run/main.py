from __future__ import annotations

from flask import Flask, jsonify, request

from ejs.contracts.google_native import (
    ExecutionAuthority,
    ExecutionEnvironment,
    ExecutionOperation,
    GoogleNativeExecutionRequest,
)
from ejs.services.google_native_executor import GoogleNativeExecutor

app = Flask(__name__)
executor = GoogleNativeExecutor()


def _request_from_json(data: dict) -> GoogleNativeExecutionRequest:
    authority_data = data.get("authority") or {}
    return GoogleNativeExecutionRequest(
        request_id=str(data.get("requestId") or ""),
        execution_id=str(data.get("executionId") or ""),
        operation=ExecutionOperation(str(data.get("operation") or "")),
        environment=ExecutionEnvironment(str(data.get("environment") or "TEST")),
        contract_version=str(data.get("contractVersion") or "EJS-GN-1.0"),
        target_url=str(data.get("targetUrl") or ""),
        opportunity_key=str(data.get("opportunityKey") or ""),
        requisition_id=str(data.get("requisitionId") or ""),
        expected_tracker_fingerprint=str(data.get("expectedTrackerFingerprint") or ""),
        authority=ExecutionAuthority(
            browser_read=bool(authority_data.get("browserRead", True)),
            form_write=bool(authority_data.get("formWrite", False)),
            file_upload=bool(authority_data.get("fileUpload", False)),
            credential_entry=bool(authority_data.get("credentialEntry", False)),
            consent_action=bool(authority_data.get("consentAction", False)),
            captcha_bypass=bool(authority_data.get("captchaBypass", False)),
            final_submit=bool(authority_data.get("finalSubmit", False)),
        ),
        trace={str(k): str(v) for k, v in (data.get("trace") or {}).items()},
    )


@app.get("/health")
def health():
    return jsonify(executor.health()), 200


@app.post("/v1/execute")
def execute():
    try:
        payload = request.get_json(force=True, silent=False) or {}
        req = _request_from_json(payload)
        result = executor.execute(req)
        code = 200 if result.read_only_invariant_ok else 409
        return jsonify(executor.as_dict(result)), code
    except PermissionError as exc:
        return jsonify({"error": "AUTHORITY_REJECTED", "message": str(exc)}), 403
    except (ValueError, TypeError) as exc:
        return jsonify({"error": "INVALID_REQUEST", "message": str(exc)}), 400
    except Exception as exc:
        # Never serialize request payload or credentials into error responses.
        return jsonify({"error": "INTERNAL_ERROR", "message": type(exc).__name__}), 500
