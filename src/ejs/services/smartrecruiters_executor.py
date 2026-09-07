from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from ejs.contracts.file_upload import FileUploadAuthority
from ejs.contracts.prefill import SafeFieldWriterAuthority, value_hash
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionAuthority, SmartRecruitersExecutionRequest
from ejs.contracts.submit import ApprovedSubmitPolicy, SubmitAuthority
from ejs.domain.browser_runtime import BrowserActionControl, BrowserObservedControl, runtime_form_fingerprint
from ejs.domain.file_upload import FileUploadResultItem, FileUploadStatus
from ejs.domain.prefill import FieldWriteResult, FieldWriteStatus
from ejs.domain.smartrecruiters_execution import SmartRecruitersExecutionResult, SmartRecruitersExecutionState
from ejs.domain.submission import PreSubmitValidationRequest, ValidationDecision
from ejs.domain.template_history import DriftState
from ejs.persistence.tmh_memory import InMemoryTmhRepository
from ejs.services.browser_worker import BrowserRuntimeConfig, _CONTROL_EXTRACTOR
from ejs.services.file_assets import accept_allows, verify_asset
from ejs.services.file_uploader import PlaywrightFileUploader
from ejs.services.prefill_writer import validate_prefill_plan
from ejs.services.safe_field_writer import PlaywrightSafeFieldWriter, _attr_escape
from ejs.services.submit_policy import validate_pre_submit


class PlaywrightSmartRecruitersExecutor:
    """Single-session SmartRecruiters prepare-only executor.

    One page/session performs inspection -> safe field writes -> approved file upload -> exact
    post-action verification -> SUBMIT-1 pre-submit validation. The class contains no submit click.
    """

    def __init__(self, config: BrowserRuntimeConfig | None = None, *, artifact_repository=None):
        self.config = config or BrowserRuntimeConfig()
        self.artifact_repository = artifact_repository or InMemoryTmhRepository()

    def execute(
        self,
        request: SmartRecruitersExecutionRequest,
        *,
        authority: SmartRecruitersExecutionAuthority | None = None,
        policy: ApprovedSubmitPolicy | None = None,
    ) -> SmartRecruitersExecutionResult:
        request.validate()
        authority = authority or SmartRecruitersExecutionAuthority()
        authority.validate()
        SafeFieldWriterAuthority().validate()
        FileUploadAuthority().validate()
        policy = policy or ApprovedSubmitPolicy()
        policy.validate()

        blockers = validate_prefill_plan(request.field_plan)
        if blockers:
            return self._failure(request, SmartRecruitersExecutionState.PLAN_BLOCKED, "FIELD_PLAN_BLOCKED", "; ".join(blockers))

        verified_assets = {}
        try:
            for plan in request.upload_plan:
                verified_assets[plan.upload_plan_key] = verify_asset(plan.asset)
        except Exception as exc:
            return self._failure(request, SmartRecruitersExecutionState.PLAN_BLOCKED, "ASSET_PRECHECK_BLOCKED", str(exc))

        executable = self.config.resolved_executable_path()
        if not executable:
            return self._failure(request, SmartRecruitersExecutionState.ERROR, "CHROMIUM_NOT_FOUND", "No Chromium/Chrome executable found")

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            return self._failure(request, SmartRecruitersExecutionState.ERROR, "PLAYWRIGHT_UNAVAILABLE", str(exc))

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.config.headless, executable_path=executable, args=["--no-sandbox", "--disable-dev-shm-usage"])
                context = browser.new_context(ignore_https_errors=self.config.ignore_https_errors, accept_downloads=False)
                page = context.new_page()
                page.set_default_timeout(self.config.navigation_timeout_ms)
                page.set_default_navigation_timeout(self.config.navigation_timeout_ms)
                trace: list[str] = []

                def on_nav(frame):
                    if frame == page.main_frame:
                        url = frame.url
                        if url and (not trace or trace[-1] != url):
                            trace.append(url)
                page.on("framenavigated", on_nav)

                if request.application_url.startswith("file://"):
                    local_path = Path(unquote(urlparse(request.application_url).path))
                    page.set_content(local_path.read_text(encoding="utf-8"), wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                    trace.append(request.application_url)
                    final_url = request.application_url
                else:
                    page.goto(request.application_url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
                    final_url = page.url
                page.wait_for_timeout(min(self.config.settle_timeout_ms, 5_000))

                if len(trace) > request.max_navigation_steps:
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.ERROR, "MAX_NAVIGATION_STEPS_EXCEEDED", "navigation trace exceeded configured maximum", final_url=final_url)

                extracted = page.evaluate(_CONTROL_EXTRACTOR)
                controls = tuple(BrowserObservedControl(**x) for x in extracted.get("controls", []))
                actions = tuple(BrowserActionControl(**x) for x in extracted.get("actions", []))
                if extracted.get("captchaPresent"):
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.BOUNDARY, "CAPTCHA_BOUNDARY", "CAPTCHA observed; no bypass", final_url=final_url)
                auth = extracted.get("authSignals") or {}
                if not controls and any(auth.values()):
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.BOUNDARY, "AUTH_BOUNDARY", "authentication boundary observed", final_url=final_url)

                ats_family = "smartrecruiters"
                observed_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=controls,
                )
                if observed_fp != request.expected_form_fingerprint:
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.SCHEMA_DRIFT, "FORM_FINGERPRINT_MISMATCH", "live form fingerprint differs from inspected fingerprint", final_url=final_url, ats_family=ats_family, observed_fp=observed_fp)

                by_key = {c.control_key: c for c in controls}
                resolution_by_key = {c.control_key: c for c in request.control_resolutions}
                required_keys = {c.control_key for c in controls if c.required}
                missing_required_resolution = sorted(required_keys - set(resolution_by_key))
                if missing_required_resolution:
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.PLAN_BLOCKED, "REQUIRED_CONTROL_RESOLUTION_GAP", ", ".join(missing_required_resolution), final_url=final_url, ats_family=ats_family, observed_fp=observed_fp)

                preflight: list[str] = []
                for plan in request.field_plan:
                    c = by_key.get(plan.control_key)
                    if c is None:
                        preflight.append(f"{plan.field_plan_key}: control not found"); continue
                    if c.disabled or not c.visible: preflight.append(f"{plan.field_plan_key}: control disabled/not visible")
                    if c.control_type.lower() != plan.control_type.lower(): preflight.append(f"{plan.field_plan_key}: control type drift")
                    if c.max_length > 0 and len(plan.value) > c.max_length: preflight.append(f"{plan.field_plan_key}: value exceeds maxlength {c.max_length}")
                    if c.control_type == "select" and plan.value not in c.options: preflight.append(f"{plan.field_plan_key}: exact select option not observed")
                    if not c.element_id and not c.name: preflight.append(f"{plan.field_plan_key}: no stable locator")
                for plan in request.upload_plan:
                    c = by_key.get(plan.control_key)
                    if c is None:
                        preflight.append(f"{plan.upload_plan_key}: file control not found"); continue
                    if c.control_type != "file": preflight.append(f"{plan.upload_plan_key}: target is not file control")
                    asset = verified_assets[plan.upload_plan_key]
                    if not accept_allows(c.accept, file_name=asset.file_name, mime_type=asset.mime_type):
                        preflight.append(f"{plan.upload_plan_key}: file type not allowed by accept")
                    if c.disabled or not c.visible: preflight.append(f"{plan.upload_plan_key}: file control disabled/not visible")
                    if not c.element_id and not c.name: preflight.append(f"{plan.upload_plan_key}: no stable locator")
                if preflight:
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.PLAN_BLOCKED, "CONTROL_PREFLIGHT_BLOCKED", "; ".join(preflight), final_url=final_url, ats_family=ats_family, observed_fp=observed_fp)

                field_results: list[FieldWriteResult] = []
                writes = 0
                base_url = page.url
                for plan in request.field_plan:
                    c = by_key[plan.control_key]
                    loc = page.locator(f'{c.tag_name}[id="{_attr_escape(c.element_id)}"]').first if c.element_id else page.locator(f'{c.tag_name}[name="{_attr_escape(c.name)}"]').first
                    current = PlaywrightSafeFieldWriter._readback(loc, c.control_type)
                    if current == plan.value:
                        field_results.append(FieldWriteResult(plan.field_plan_key, plan.control_key, plan.canonical_field, plan.control_type, plan.value_hash, value_hash(current), FieldWriteStatus.ALREADY_MATCHED, False, "pre-read already equals desired value"))
                        continue
                    if c.control_type == "select": loc.select_option(label=plan.value)
                    else: loc.fill(plan.value)
                    writes += 1
                    if page.url != base_url:
                        context.close(); browser.close()
                        return self._failure(request, SmartRecruitersExecutionState.READBACK_FAILED, "UNEXPECTED_NAVIGATION_AFTER_FIELD_WRITE", plan.field_plan_key, final_url=page.url, ats_family=ats_family, observed_fp=observed_fp, field_results=tuple(field_results), writes=writes)
                    rb = PlaywrightSafeFieldWriter._readback(loc, c.control_type)
                    valid = loc.evaluate("(el) => el.checkValidity ? el.checkValidity() : true")
                    status = FieldWriteStatus.WRITTEN_VERIFIED if rb == plan.value and valid else FieldWriteStatus.READBACK_MISMATCH
                    field_results.append(FieldWriteResult(plan.field_plan_key, plan.control_key, plan.canonical_field, plan.control_type, plan.value_hash, value_hash(rb), status, True, "" if status is FieldWriteStatus.WRITTEN_VERIFIED else "exact readback mismatch or validity failure"))
                    if status is FieldWriteStatus.READBACK_MISMATCH:
                        context.close(); browser.close()
                        return self._failure(request, SmartRecruitersExecutionState.READBACK_FAILED, "FIELD_READBACK_MISMATCH", plan.field_plan_key, final_url=final_url, ats_family=ats_family, observed_fp=observed_fp, field_results=tuple(field_results), writes=writes)

                file_results: list[FileUploadResultItem] = []
                uploads = 0
                helper = PlaywrightFileUploader(self.config, artifact_repository=self.artifact_repository)
                for plan in request.upload_plan:
                    c = by_key[plan.control_key]
                    loc = PlaywrightFileUploader._locator(page, c)
                    asset = verified_assets[plan.upload_plan_key]
                    rb_before = PlaywrightFileUploader._readback(loc)
                    executed = False
                    if not PlaywrightFileUploader._readback_matches(rb_before, asset):
                        loc.set_input_files(asset.path)
                        uploads += 1
                        executed = True
                    rb = PlaywrightFileUploader._readback(loc)
                    if not PlaywrightFileUploader._readback_matches(rb, asset) or not loc.evaluate("(el) => el.checkValidity ? el.checkValidity() : true"):
                        item = helper._item(request.execution_id, plan, asset, rb, FileUploadStatus.READBACK_MISMATCH, executed, False, False, "browser file readback mismatch")
                        file_results.append(item)
                        context.close(); browser.close()
                        return self._failure(request, SmartRecruitersExecutionState.READBACK_FAILED, "FILE_READBACK_MISMATCH", plan.upload_plan_key, final_url=final_url, ats_family=ats_family, observed_fp=observed_fp, field_results=tuple(field_results), file_results=tuple(file_results), writes=writes, uploads=uploads)
                    from ejs.domain.template_history import SubmissionArtifact
                    artifact = SubmissionArtifact(
                        execution_id=request.execution_id,
                        artifact_type=plan.asset.artifact_type.value,
                        artifact_version=plan.asset.artifact_version,
                        content_hash=asset.sha256,
                        source_ref=plan.asset.source_ref,
                        asset_ref=plan.asset.asset_ref,
                        policy_version=request.policy_version,
                        captured_at=request.observed_at,
                        notes=f"SmartRecruiters E2E verified upload to {plan.control_key}",
                    )
                    appended = self.artifact_repository.append_artifact(artifact)
                    status = FileUploadStatus.UPLOADED_VERIFIED if executed else FileUploadStatus.ALREADY_ATTACHED
                    file_results.append(helper._item(request.execution_id, plan, asset, rb, status, executed, appended, not appended, "" if executed else "pre-read already matched"))

                final_extracted = page.evaluate(_CONTROL_EXTRACTOR)
                final_controls = tuple(BrowserObservedControl(**x) for x in final_extracted.get("controls", []))
                final_actions = tuple(BrowserActionControl(**x) for x in final_extracted.get("actions", []))
                final_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=final_controls,
                )
                if final_fp != observed_fp:
                    context.close(); browser.close()
                    return self._failure(request, SmartRecruitersExecutionState.SCHEMA_DRIFT, "POST_ACTION_FORM_DRIFT", "form structure changed after prepare actions", final_url=final_url, ats_family=ats_family, observed_fp=observed_fp, final_fp=final_fp, field_results=tuple(field_results), file_results=tuple(file_results), writes=writes, uploads=uploads)

                form_valid = bool(page.locator("form").first.evaluate("(el) => el.checkValidity ? el.checkValidity() : true")) if page.locator("form").count() else all(page.locator("input,textarea,select").evaluate_all("els => els.every(el => !el.checkValidity || el.checkValidity())"))
                submit_observed = any(a.probable_action == "submit" for a in final_actions)
                technical = "" if not final_extracted.get("captchaPresent") else "CAPTCHA"
                validation_request = PreSubmitValidationRequest(
                    execution_id=request.execution_id,
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    candidate_profile_version=request.candidate_profile_version,
                    apply_url=final_url,
                    ats_family=ats_family,
                    form_fingerprint=final_fp,
                    drift_state=DriftState.MATCH,
                    runtime_inspection_current=True,
                    tracker_fingerprint_current=request.tracker_fingerprint_current,
                    controls=request.control_resolutions,
                    existing_submit_keys=request.existing_submit_keys,
                    rollout_stage=request.rollout_stage,
                    submissions_today=request.submissions_today,
                    technical_challenge=technical,
                    policy_version=request.policy_version,
                )
                validation = validate_pre_submit(validation_request, policy=policy, authority=SubmitAuthority())
                ready = validation.policy_eligible and form_valid and submit_observed
                state = SmartRecruitersExecutionState.PRE_SUBMIT_READY if ready else SmartRecruitersExecutionState.VALIDATOR_BLOCKED
                err = "" if ready else "PRE_SUBMIT_NOT_READY"
                msg = "" if ready else "; ".join(validation.blockers or (("browser form invalid",) if not form_valid else ()) or (("submit control not observed",) if not submit_observed else ()))
                result = SmartRecruitersExecutionResult(
                    execution_id=request.execution_id,
                    route_key=request.route_key,
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    runtime_state=state,
                    requested_url=request.application_url,
                    final_url=final_url,
                    ats_family=ats_family,
                    observed_form_fingerprint=observed_fp,
                    final_form_fingerprint=final_fp,
                    required_controls_observed=sum(c.required for c in final_controls),
                    resolved_required_controls=validation.resolved_required_controls,
                    field_results=tuple(field_results),
                    file_results=tuple(file_results),
                    form_value_write_attempts=writes,
                    file_upload_attempts=uploads,
                    submit_attempts=0,
                    submit_control_observed=submit_observed,
                    browser_form_valid=form_valid,
                    validation=validation,
                    review_gate_reached=True,
                    error_code=err,
                    error_message=msg,
                )
                context.close(); browser.close(); return result
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            if "err_blocked_by_administrator" in lower:
                return self._failure(request, SmartRecruitersExecutionState.ACCESS_BLOCKED, "ERR_BLOCKED_BY_ADMINISTRATOR", msg)
            if "err_name_not_resolved" in lower:
                return self._failure(request, SmartRecruitersExecutionState.ACCESS_BLOCKED, "DNS_UNAVAILABLE", msg)
            return self._failure(request, SmartRecruitersExecutionState.ERROR, "SMARTRECRUITERS_EXECUTION_ERROR", msg)

    @staticmethod
    def _failure(request, state, code, message, *, final_url="", ats_family="smartrecruiters", observed_fp="", final_fp="", field_results=(), file_results=(), writes=0, uploads=0):
        return SmartRecruitersExecutionResult(
            execution_id=request.execution_id,
            route_key=request.route_key,
            opportunity_key=request.opportunity_key,
            requisition_id=request.requisition_id,
            runtime_state=state,
            requested_url=request.application_url,
            final_url=final_url,
            ats_family=ats_family,
            observed_form_fingerprint=observed_fp,
            final_form_fingerprint=final_fp or observed_fp,
            required_controls_observed=0,
            resolved_required_controls=0,
            field_results=tuple(field_results),
            file_results=tuple(file_results),
            form_value_write_attempts=writes,
            file_upload_attempts=uploads,
            submit_attempts=0,
            submit_control_observed=False,
            browser_form_valid=False,
            validation=None,
            review_gate_reached=False,
            error_code=code,
            error_message=message,
        )
