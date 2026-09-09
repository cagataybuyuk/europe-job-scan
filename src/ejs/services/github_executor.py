from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from ejs.contracts.browser import BrowserInspectionRequest, BrowserWorkerAuthority
from ejs.contracts.github_executor import ExecutionProvider, ExecutionResult
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker


class GitHubReadOnlyBrowserExecutor:
    """Provider-neutral GD-004 read-only browser execution wrapper.

    The underlying BE-1 worker is structurally read-only. This wrapper adds provider/source
    lineage and refuses to report success if any mutation/upload/submit counter is non-zero.
    """

    def __init__(
        self,
        *,
        provider: ExecutionProvider = ExecutionProvider.GITHUB_HOSTED,
        chromium_executable: str = "",
    ) -> None:
        self.provider = provider
        self.worker = PlaywrightBrowserWorker(
            BrowserRuntimeConfig(
                executable_path=chromium_executable,
                use_playwright_managed=not bool(chromium_executable),
            )
        )

    def inspect(
        self,
        *,
        application_url: str,
        observed_at: str,
        source_sha: str,
        request_id: str,
        execution_id: str,
        opportunity_key: str = "opportunity:gd004-test",
        requisition_id: str = "gd004-test",
        adapter_key: str = "form:employer-custom",
    ) -> tuple[ExecutionResult, dict[str, Any]]:
        started = datetime.now(timezone.utc)
        request = BrowserInspectionRequest(
            bridge_request_id=request_id,
            route_key=f"route:{execution_id}",
            opportunity_key=opportunity_key,
            requisition_id=requisition_id,
            application_url=application_url,
            adapter_key=adapter_key,
            observed_at=observed_at,
        )
        inspection = self.worker.inspect(request, authority=BrowserWorkerAuthority())
        completed = datetime.now(timezone.utc)
        if not inspection.read_only_invariant_ok:
            raise PermissionError("GD-004 browser read-only invariant failed")

        result = ExecutionResult(
            request_id=request_id,
            execution_id=execution_id,
            operation="browser_read",
            source_sha=source_sha,
            provider=self.provider,
            started_at=started.isoformat().replace("+00:00", "Z"),
            completed_at=completed.isoformat().replace("+00:00", "Z"),
            typed_status=inspection.runtime_state.value,
            form_fingerprint=inspection.form_fingerprint,
            runtime_fingerprint=inspection.page_fingerprint,
            mutation_count=inspection.mutation_attempts,
            upload_count=inspection.file_upload_attempts,
            submit_count=inspection.submit_attempts,
            evidence_summary=(
                f"ats={inspection.ats_family}; controls={len(inspection.controls)}; "
                f"actions={len(inspection.action_controls)}; captcha={inspection.captcha_state.value}; "
                f"auth={inspection.auth_boundary_type.value}; error={inspection.error_code or 'none'}"
            ),
        ).with_hash()
        result.validate_gd004()

        evidence = {
            "requested_url": inspection.requested_url,
            "final_url": inspection.final_url,
            "ats_family": inspection.ats_family,
            "runtime_state": inspection.runtime_state.value,
            "page_title": inspection.page_title,
            "page_fingerprint": inspection.page_fingerprint,
            "form_fingerprint": inspection.form_fingerprint,
            "dom_snapshot_state": inspection.dom_snapshot_state.value,
            "auth_boundary_type": inspection.auth_boundary_type.value,
            "captcha_state": inspection.captcha_state.value,
            "control_count": len(inspection.controls),
            "required_control_count": sum(1 for c in inspection.controls if c.required),
            "action_count": len(inspection.action_controls),
            "navigation_steps": len(inspection.navigation_trace),
            "browser_engine": inspection.browser_engine,
            "browser_version": inspection.browser_version,
            "error_code": inspection.error_code,
            "read_only_invariant_ok": inspection.read_only_invariant_ok,
            "mutation_attempts": inspection.mutation_attempts,
            "file_upload_attempts": inspection.file_upload_attempts,
            "submit_attempts": inspection.submit_attempts,
        }
        return result, evidence


def fixture_url(path: str) -> str:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return resolved.as_uri()


def sanitized_output(result: ExecutionResult, evidence: dict[str, Any]) -> str:
    result_value = asdict(result)
    result_value["provider"] = result.provider.value
    return json.dumps(
        {"contract_version": "EJS-GH-EXEC-0.1", "result": result_value, "evidence": evidence},
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
