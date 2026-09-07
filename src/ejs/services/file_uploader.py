from __future__ import annotations

from pathlib import Path
import base64
import hashlib
from urllib.parse import unquote, urlparse

from ejs.contracts.browser import AuthBoundaryType, CaptchaState
from ejs.contracts.file_upload import (
    FileUploadAuthority,
    FileUploadExecutionRequest,
    FileUploadPlan,
    upload_key,
)
from ejs.domain.browser_runtime import BrowserObservedControl, detect_ats_family, runtime_form_fingerprint
from ejs.domain.file_upload import (
    FileReadback,
    FileUploadExecutionResult,
    FileUploadExecutionState,
    FileUploadResultItem,
    FileUploadStatus,
)
from ejs.domain.template_history import SubmissionArtifact
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, _CONTROL_EXTRACTOR
from ejs.services.file_assets import VerifiedFileAsset, accept_allows, verify_asset


def _attr_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class PlaywrightFileUploader:
    def __init__(
        self,
        config: BrowserRuntimeConfig | None = None,
        *,
        artifact_repository: InMemoryTmhRepository | None = None,
    ) -> None:
        self.config = config or BrowserRuntimeConfig()
        self.artifact_repository = artifact_repository or InMemoryTmhRepository()

    def execute(
        self,
        request: FileUploadExecutionRequest,
        *,
        authority: FileUploadAuthority | None = None,
    ) -> FileUploadExecutionResult:
        request.validate()
        authority = authority or FileUploadAuthority()
        authority.validate()

        # Whole-plan asset verification happens before browser launch so a bad asset cannot cause
        # a partial attachment set.
        verified_assets: dict[str, VerifiedFileAsset] = {}
        try:
            for plan in request.upload_plan:
                verified_assets[plan.upload_plan_key] = verify_asset(plan.asset)
        except Exception as exc:
            return self._failure(
                request,
                state=FileUploadExecutionState.PLAN_BLOCKED,
                error_code="ASSET_PREFLIGHT_BLOCKED",
                error_message=str(exc),
            )

        executable = self.config.resolved_executable_path()
        if not executable:
            return self._failure(
                request,
                state=FileUploadExecutionState.ERROR,
                error_code="CHROMIUM_NOT_FOUND",
                error_message="No Chromium/Chrome executable found",
            )
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            return self._failure(
                request,
                state=FileUploadExecutionState.ERROR,
                error_code="PLAYWRIGHT_UNAVAILABLE",
                error_message=str(exc),
            )

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=self.config.headless,
                    executable_path=executable,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = browser.new_context(
                    ignore_https_errors=self.config.ignore_https_errors,
                    accept_downloads=False,
                )
                page = context.new_page()
                page.set_default_timeout(self.config.navigation_timeout_ms)
                page.set_default_navigation_timeout(self.config.navigation_timeout_ms)

                if request.application_url.startswith("file://"):
                    local_path = Path(unquote(urlparse(request.application_url).path))
                    page.set_content(local_path.read_text(encoding="utf-8"), wait_until="domcontentloaded")
                    final_url = request.application_url
                else:
                    page.goto(request.application_url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                    final_url = page.url
                try:
                    page.wait_for_timeout(min(self.config.settle_timeout_ms, 5_000))
                except Exception:
                    pass

                extracted = page.evaluate(_CONTROL_EXTRACTOR)
                if extracted.get("captchaPresent"):
                    result = self._failure(
                        request,
                        state=FileUploadExecutionState.BOUNDARY,
                        error_code="CAPTCHA_BOUNDARY",
                        error_message="CAPTCHA detected before file upload",
                        final_url=final_url,
                        ats_family=detect_ats_family(final_url, request.adapter_key),
                    )
                    context.close(); browser.close(); return result
                auth = extracted.get("authSignals") or {}
                if any(auth.values()):
                    result = self._failure(
                        request,
                        state=FileUploadExecutionState.BOUNDARY,
                        error_code="AUTH_BOUNDARY",
                        error_message="authentication boundary detected before file upload",
                        final_url=final_url,
                        ats_family=detect_ats_family(final_url, request.adapter_key),
                    )
                    context.close(); browser.close(); return result

                controls = tuple(BrowserObservedControl(**item) for item in extracted.get("controls", []))
                ats_family = detect_ats_family(final_url, request.adapter_key)
                observed_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url or request.application_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=controls,
                )
                if observed_fp != request.expected_form_fingerprint:
                    result = self._failure(
                        request,
                        state=FileUploadExecutionState.SCHEMA_DRIFT,
                        error_code="FORM_FINGERPRINT_MISMATCH",
                        error_message="current form fingerprint does not match inspected fingerprint",
                        final_url=final_url,
                        ats_family=ats_family,
                        observed_form_fingerprint=observed_fp,
                    )
                    context.close(); browser.close(); return result

                by_key = {c.control_key: c for c in controls}
                preflight_blockers: list[str] = []
                for plan in request.upload_plan:
                    control = by_key.get(plan.control_key)
                    asset = verified_assets[plan.upload_plan_key]
                    if control is None:
                        preflight_blockers.append(f"control not found: {plan.control_key}")
                        continue
                    if control.control_type != "file" or control.tag_name != "input":
                        preflight_blockers.append(f"target is not a file input: {plan.control_key}")
                    if not control.visible or control.disabled:
                        preflight_blockers.append(f"file control unavailable: {plan.control_key}")
                    if not accept_allows(control.accept, file_name=asset.file_name, mime_type=asset.mime_type):
                        preflight_blockers.append(f"file type not allowed by control accept: {plan.control_key}")
                    if control.multiple:
                        # Multiple file controls are not inherently unsafe, but FILE-1 keeps one approved
                        # artifact per control to preserve deterministic readback and lineage.
                        pass
                if preflight_blockers:
                    result = self._failure(
                        request,
                        state=FileUploadExecutionState.PLAN_BLOCKED,
                        error_code="FILE_CONTROL_PREFLIGHT_BLOCKED",
                        error_message="; ".join(preflight_blockers),
                        final_url=final_url,
                        ats_family=ats_family,
                        observed_form_fingerprint=observed_fp,
                    )
                    context.close(); browser.close(); return result

                item_results: list[FileUploadResultItem] = []
                upload_attempts = 0
                base_url = page.url
                for plan in request.upload_plan:
                    control = by_key[plan.control_key]
                    asset = verified_assets[plan.upload_plan_key]
                    locator = self._locator(page, control)
                    pre = self._readback(locator)
                    exact_pre = self._readback_matches(pre, asset)
                    executed = False
                    if not exact_pre:
                        locator.set_input_files(asset.path)
                        upload_attempts += 1
                        executed = True
                        if page.url != base_url:
                            result = self._failure(
                                request,
                                state=FileUploadExecutionState.READBACK_FAILED,
                                error_code="UNEXPECTED_NAVIGATION_AFTER_FILE_UPLOAD",
                                error_message=f"navigation changed after {plan.upload_plan_key}",
                                final_url=page.url,
                                ats_family=ats_family,
                                observed_form_fingerprint=observed_fp,
                                item_results=tuple(item_results),
                                file_upload_attempts=upload_attempts,
                            )
                            context.close(); browser.close(); return result
                    readback = self._readback(locator)
                    if not self._readback_matches(readback, asset):
                        item_results.append(self._item(request.execution_id, plan, asset, readback, FileUploadStatus.READBACK_MISMATCH, executed, False, False, "browser file readback mismatch"))
                        result = self._failure(
                            request,
                            state=FileUploadExecutionState.READBACK_FAILED,
                            error_code="FILE_READBACK_MISMATCH",
                            error_message=f"file readback mismatch for {plan.upload_plan_key}",
                            final_url=final_url,
                            ats_family=ats_family,
                            observed_form_fingerprint=observed_fp,
                            item_results=tuple(item_results),
                            file_upload_attempts=upload_attempts,
                        )
                        context.close(); browser.close(); return result
                    valid = locator.evaluate("(el) => el.checkValidity ? el.checkValidity() : true")
                    if not valid:
                        item_results.append(self._item(request.execution_id, plan, asset, readback, FileUploadStatus.READBACK_MISMATCH, executed, False, False, "file control browser validity failed"))
                        result = self._failure(
                            request,
                            state=FileUploadExecutionState.READBACK_FAILED,
                            error_code="FILE_CONTROL_VALIDITY_FAILED",
                            error_message=f"browser validity failed for {plan.upload_plan_key}",
                            final_url=final_url,
                            ats_family=ats_family,
                            observed_form_fingerprint=observed_fp,
                            item_results=tuple(item_results),
                            file_upload_attempts=upload_attempts,
                        )
                        context.close(); browser.close(); return result

                    artifact = SubmissionArtifact(
                        execution_id=request.execution_id,
                        artifact_type=plan.asset.artifact_type.value,
                        artifact_version=plan.asset.artifact_version,
                        content_hash=asset.sha256,
                        source_ref=plan.asset.source_ref,
                        asset_ref=plan.asset.asset_ref,
                        policy_version=request.policy_version,
                        captured_at=request.observed_at,
                        notes=f"FILE-1 verified upload to {plan.control_key}; immutable approved asset lineage",
                    )
                    artifact_appended = self.artifact_repository.append_artifact(artifact)
                    status = FileUploadStatus.UPLOADED_VERIFIED if executed else FileUploadStatus.ALREADY_ATTACHED
                    item_results.append(
                        self._item(
                            request.execution_id,
                            plan,
                            asset,
                            readback,
                            status,
                            executed,
                            artifact_appended,
                            not artifact_appended,
                            "" if executed else "pre-read file metadata/hash already matched approved asset",
                        )
                    )

                final_extracted = page.evaluate(_CONTROL_EXTRACTOR)
                final_controls = tuple(BrowserObservedControl(**item) for item in final_extracted.get("controls", []))
                final_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url or request.application_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=final_controls,
                )
                verified_count = len(item_results)
                already_count = sum(x.status is FileUploadStatus.ALREADY_ATTACHED for x in item_results)
                exact_all = all(
                    x.content_hash == x.readback_sha256
                    and x.expected_file_name == x.readback_file_name
                    and x.expected_size_bytes == x.readback_size_bytes
                    and x.expected_mime_type == x.readback_mime_type
                    for x in item_results
                )
                state = FileUploadExecutionState.REVIEW_GATE if exact_all and final_fp == observed_fp else FileUploadExecutionState.SCHEMA_DRIFT
                result = FileUploadExecutionResult(
                    execution_id=request.execution_id,
                    route_key=request.route_key,
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    runtime_state=state,
                    requested_url=request.application_url,
                    final_url=final_url,
                    ats_family=ats_family,
                    expected_form_fingerprint=request.expected_form_fingerprint,
                    observed_form_fingerprint=observed_fp,
                    final_form_fingerprint=final_fp,
                    item_results=tuple(item_results),
                    planned_files=len(request.upload_plan),
                    verified_files=verified_count,
                    already_attached_files=already_count,
                    file_upload_attempts=upload_attempts,
                    form_value_write_attempts=0,
                    submit_attempts=0,
                    exact_readback_all_match=exact_all,
                    review_gate_reached=state is FileUploadExecutionState.REVIEW_GATE,
                    error_code="" if state is FileUploadExecutionState.REVIEW_GATE else "POST_UPLOAD_FORM_DRIFT",
                    error_message="" if state is FileUploadExecutionState.REVIEW_GATE else "form structure changed after file upload",
                )
                context.close(); browser.close(); return result
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            if "err_blocked_by_administrator" in lower:
                return self._failure(request, state=FileUploadExecutionState.ACCESS_BLOCKED, error_code="ERR_BLOCKED_BY_ADMINISTRATOR", error_message=msg)
            if "err_name_not_resolved" in lower:
                return self._failure(request, state=FileUploadExecutionState.ACCESS_BLOCKED, error_code="DNS_UNAVAILABLE", error_message=msg)
            return self._failure(request, state=FileUploadExecutionState.ERROR, error_code="FILE_UPLOAD_EXECUTION_ERROR", error_message=msg)

    @staticmethod
    def _locator(page, control: BrowserObservedControl):
        if control.element_id:
            return page.locator(f'{control.tag_name}[id="{_attr_escape(control.element_id)}"]').first
        return page.locator(f'{control.tag_name}[name="{_attr_escape(control.name)}"]').first

    @staticmethod
    def _readback(locator) -> FileReadback:
        data = locator.evaluate(
            """async (el) => {
              const f = el.files && el.files.length ? el.files[0] : null;
              if (!f) return {file_name:'', size_bytes:0, mime_type:'', sha256:'', bytes_b64:''};
              const buf = await f.arrayBuffer();
              let sha256 = '';
              let bytes_b64 = '';
              if (globalThis.crypto && globalThis.crypto.subtle) {
                const digest = await globalThis.crypto.subtle.digest('SHA-256', buf);
                sha256 = Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2,'0')).join('');
              } else if (f.size <= 2 * 1024 * 1024) {
                const u8 = new Uint8Array(buf);
                let binary = '';
                const step = 0x8000;
                for (let i=0; i<u8.length; i+=step) {
                  binary += String.fromCharCode(...u8.subarray(i, Math.min(i+step, u8.length)));
                }
                bytes_b64 = btoa(binary);
              }
              return {file_name:f.name || '', size_bytes:f.size || 0, mime_type:f.type || '', sha256, bytes_b64};
            }"""
        )
        digest = data.get("sha256", "")
        encoded = data.pop("bytes_b64", "")
        if not digest and encoded:
            digest = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
        return FileReadback(
            file_name=data.get("file_name", ""),
            size_bytes=int(data.get("size_bytes", 0) or 0),
            mime_type=data.get("mime_type", ""),
            sha256=digest,
        )

    @staticmethod
    def _readback_matches(readback: FileReadback, asset: VerifiedFileAsset) -> bool:
        return (
            readback.file_name == asset.file_name
            and readback.size_bytes == asset.size_bytes
            and readback.mime_type == asset.mime_type
            and bool(readback.sha256)
            and readback.sha256.lower() == asset.sha256.lower()
        )

    @staticmethod
    def _item(execution_id: str, plan: FileUploadPlan, asset: VerifiedFileAsset, readback: FileReadback, status: FileUploadStatus, executed: bool, artifact_appended: bool, duplicate: bool, reason: str) -> FileUploadResultItem:
        return FileUploadResultItem(
            upload_plan_key=plan.upload_plan_key,
            upload_key=upload_key(execution_id, plan.control_key, asset.sha256),
            control_key=plan.control_key,
            canonical_field=plan.canonical_field,
            artifact_type=plan.asset.artifact_type.value,
            artifact_version=plan.asset.artifact_version,
            content_hash=asset.sha256,
            expected_file_name=asset.file_name,
            expected_size_bytes=asset.size_bytes,
            expected_mime_type=asset.mime_type,
            readback_file_name=readback.file_name,
            readback_size_bytes=readback.size_bytes,
            readback_mime_type=readback.mime_type,
            readback_sha256=readback.sha256,
            status=status,
            upload_executed=executed,
            artifact_appended=artifact_appended,
            artifact_duplicate_suppressed=duplicate,
            reason=reason,
        )

    @staticmethod
    def _failure(
        request: FileUploadExecutionRequest,
        *,
        state: FileUploadExecutionState,
        error_code: str,
        error_message: str,
        final_url: str = "",
        ats_family: str = "",
        observed_form_fingerprint: str = "",
        item_results: tuple[FileUploadResultItem, ...] = (),
        file_upload_attempts: int = 0,
    ) -> FileUploadExecutionResult:
        return FileUploadExecutionResult(
            execution_id=request.execution_id,
            route_key=request.route_key,
            opportunity_key=request.opportunity_key,
            requisition_id=request.requisition_id,
            runtime_state=state,
            requested_url=request.application_url,
            final_url=final_url,
            ats_family=ats_family,
            expected_form_fingerprint=request.expected_form_fingerprint,
            observed_form_fingerprint=observed_form_fingerprint,
            final_form_fingerprint=observed_form_fingerprint,
            item_results=item_results,
            planned_files=len(request.upload_plan),
            verified_files=sum(x.status in {FileUploadStatus.UPLOADED_VERIFIED, FileUploadStatus.ALREADY_ATTACHED} for x in item_results),
            already_attached_files=sum(x.status is FileUploadStatus.ALREADY_ATTACHED for x in item_results),
            file_upload_attempts=file_upload_attempts,
            form_value_write_attempts=0,
            submit_attempts=0,
            exact_readback_all_match=False,
            review_gate_reached=False,
            error_code=error_code,
            error_message=error_message,
        )
