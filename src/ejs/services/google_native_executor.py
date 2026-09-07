from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ejs.contracts.browser import BrowserInspectionRequest, BrowserWorkerAuthority
from ejs.contracts.google_native import (
    GOOGLE_NATIVE_CONTRACT_VERSION,
    ExecutionOperation,
    GoogleNativeExecutionRequest,
    GoogleNativeExecutionResult,
)
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.url_policy import PublicHttpsUrlPolicy, sanitize_external_url


class GoogleNativeExecutor:
    def __init__(self, *, worker: PlaywrightBrowserWorker | None = None, fixture_root: Path | None = None):
        self.worker = worker or PlaywrightBrowserWorker(
            BrowserRuntimeConfig(
                use_playwright_managed=True,
                navigation_timeout_ms=20_000,
                settle_timeout_ms=500,
            )
        )
        self.fixture_root = fixture_root or (Path(__file__).resolve().parents[1] / "apps" / "cloud_run" / "fixtures")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "contractVersion": GOOGLE_NATIVE_CONTRACT_VERSION,
            "environment": "TEST",
            "browserRead": True,
            "formWrite": False,
            "fileUpload": False,
            "finalSubmit": False,
        }

    def execute(self, request: GoogleNativeExecutionRequest) -> GoogleNativeExecutionResult:
        request.validate()
        if request.operation is ExecutionOperation.ECHO:
            return GoogleNativeExecutionResult(
                request_id=request.request_id,
                execution_id=request.execution_id,
                operation=request.operation.value,
                contract_version=request.contract_version,
                runtime_state="echo",
                observed_at=self._now(),
            )

        if request.operation is ExecutionOperation.INSPECT_FIXTURE:
            target = (self.fixture_root / "smartrecruiters_smoke.html").resolve().as_uri()
            adapter = "ats:smartrecruiters"
        else:
            decision = PublicHttpsUrlPolicy.validate_resolved(request.target_url)
            if not decision.allowed:
                return GoogleNativeExecutionResult(
                    request_id=request.request_id,
                    execution_id=request.execution_id,
                    operation=request.operation.value,
                    contract_version=request.contract_version,
                    runtime_state="access_blocked",
                    observed_at=self._now(),
                    final_url=sanitize_external_url(request.target_url),
                    error_code=decision.code,
                    error_message=decision.message,
                )
            target = request.target_url
            adapter = "form:employer-custom"

        browser_request = BrowserInspectionRequest(
            bridge_request_id=f"gn:{request.request_id}",
            route_key=f"gn:{request.execution_id}",
            opportunity_key=request.opportunity_key or "test:google-native",
            requisition_id=request.requisition_id or "test",
            application_url=target,
            adapter_key=adapter,
            observed_at=self._now(),
        )
        result = self.worker.inspect(browser_request, authority=BrowserWorkerAuthority())
        return GoogleNativeExecutionResult(
            request_id=request.request_id,
            execution_id=request.execution_id,
            operation=request.operation.value,
            contract_version=request.contract_version,
            runtime_state=result.runtime_state.value,
            observed_at=self._now(),
            final_url=sanitize_external_url(result.final_url),
            ats_family=result.ats_family,
            browser_engine="chromium",
            browser_version=result.browser_version,
            page_fingerprint=result.page_fingerprint,
            form_fingerprint=result.form_fingerprint,
            controls=len(result.controls),
            required_controls=sum(1 for c in result.controls if c.required),
            action_controls=len(result.action_controls),
            captcha_state=result.captcha_state.value,
            auth_boundary_type=result.auth_boundary_type.value,
            error_code=result.error_code,
            error_message=result.error_message,
            mutation_attempts=result.mutation_attempts,
            file_upload_attempts=result.file_upload_attempts,
            submit_attempts=result.submit_attempts,
            read_only_invariant_ok=result.read_only_invariant_ok,
        )

    @staticmethod
    def as_dict(result: GoogleNativeExecutionResult) -> dict[str, Any]:
        return asdict(result)
