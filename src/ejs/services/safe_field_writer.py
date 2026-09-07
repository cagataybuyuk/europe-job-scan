from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from ejs.contracts.browser import AuthBoundaryType, CaptchaState
from ejs.contracts.prefill import PrefillExecutionRequest, SafeFieldWriterAuthority, value_hash
from ejs.domain.browser_runtime import (
    BrowserActionControl,
    BrowserObservedControl,
    detect_ats_family,
    runtime_form_fingerprint,
)
from ejs.domain.prefill import (
    FieldWriteResult,
    FieldWriteStatus,
    PrefillExecutionResult,
    PrefillExecutionState,
)
from ejs.services.browser_worker import BrowserRuntimeConfig, _CONTROL_EXTRACTOR
from ejs.services.prefill_writer import validate_prefill_plan


def _attr_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class PlaywrightSafeFieldWriter:
    """BE-2 bounded browser writer for non-sensitive, resolver-approved fields only.

    The class intentionally exposes no upload, consent, credential, CAPTCHA or submit method.
    Every planned control is validated before the first DOM write and every actual write is
    immediately read back. Results contain value hashes, never raw candidate values.
    """

    def __init__(self, config: BrowserRuntimeConfig | None = None):
        self.config = config or BrowserRuntimeConfig()

    def execute(
        self,
        request: PrefillExecutionRequest,
        *,
        authority: SafeFieldWriterAuthority | None = None,
    ) -> PrefillExecutionResult:
        request.validate()
        authority = authority or SafeFieldWriterAuthority()
        authority.validate()

        plan_blockers = validate_prefill_plan(request.field_plan)
        if plan_blockers:
            return self._failure(
                request,
                state=PrefillExecutionState.PLAN_BLOCKED,
                error_code="FIELD_PLAN_BLOCKED",
                error_message="; ".join(plan_blockers),
            )

        executable = self.config.resolved_executable_path()
        if not executable:
            return self._failure(
                request,
                state=PrefillExecutionState.ERROR,
                error_code="CHROMIUM_NOT_FOUND",
                error_message="No Chromium/Chrome executable found",
            )

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover
            return self._failure(
                request,
                state=PrefillExecutionState.ERROR,
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

                trace: list[str] = []

                def on_nav(frame):
                    if frame == page.main_frame:
                        url = frame.url
                        if url and (not trace or trace[-1] != url):
                            trace.append(url)

                page.on("framenavigated", on_nav)
                if request.application_url.startswith("file://"):
                    local_path = Path(unquote(urlparse(request.application_url).path))
                    page.set_content(
                        local_path.read_text(encoding="utf-8"),
                        wait_until="domcontentloaded",
                        timeout=self.config.navigation_timeout_ms,
                    )
                    trace.append(request.application_url)
                else:
                    page.goto(
                        request.application_url,
                        wait_until="domcontentloaded",
                        timeout=self.config.navigation_timeout_ms,
                    )
                try:
                    page.wait_for_timeout(min(self.config.settle_timeout_ms, 5_000))
                except Exception:
                    pass

                if len(trace) > request.max_navigation_steps:
                    result = self._failure(
                        request,
                        state=PrefillExecutionState.ERROR,
                        error_code="MAX_NAVIGATION_STEPS_EXCEEDED",
                        error_message=f"navigation trace exceeded {request.max_navigation_steps}",
                        final_url=page.url,
                    )
                    context.close(); browser.close()
                    return result

                extracted = page.evaluate(_CONTROL_EXTRACTOR)
                controls = tuple(BrowserObservedControl(**item) for item in extracted.get("controls", []))
                actions = tuple(BrowserActionControl(**item) for item in extracted.get("actions", []))
                final_url = request.application_url if request.application_url.startswith("file://") else page.url
                ats_family = detect_ats_family(final_url or request.application_url, request.adapter_key)

                if extracted.get("captchaPresent"):
                    result = self._failure(
                        request,
                        state=PrefillExecutionState.BOUNDARY,
                        error_code="CAPTCHA_BOUNDARY",
                        error_message="CAPTCHA observed; BE-2 does not interact",
                        final_url=final_url,
                        ats_family=ats_family,
                    )
                    context.close(); browser.close()
                    return result

                auth_signals = extracted.get("authSignals") or {}
                auth_type = AuthBoundaryType.NONE
                if auth_signals.get("session_required"):
                    auth_type = AuthBoundaryType.SESSION_REQUIRED
                elif auth_signals.get("sign_in"):
                    auth_type = AuthBoundaryType.SIGN_IN
                elif auth_signals.get("create_account"):
                    auth_type = AuthBoundaryType.CREATE_ACCOUNT
                if auth_type is not AuthBoundaryType.NONE and not controls:
                    result = self._failure(
                        request,
                        state=PrefillExecutionState.BOUNDARY,
                        error_code=f"AUTH_{auth_type.value.upper()}",
                        error_message="Authentication boundary observed; BE-2 does not interact",
                        final_url=final_url,
                        ats_family=ats_family,
                    )
                    context.close(); browser.close()
                    return result

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
                        state=PrefillExecutionState.SCHEMA_DRIFT,
                        error_code="FORM_FINGERPRINT_MISMATCH",
                        error_message="live form fingerprint differs from inspected fingerprint",
                        final_url=final_url,
                        ats_family=ats_family,
                        observed_form_fingerprint=observed_fp,
                    )
                    context.close(); browser.close()
                    return result

                by_key = {c.control_key: c for c in controls}
                preflight_blockers: list[str] = []
                for plan in request.field_plan:
                    control = by_key.get(plan.control_key)
                    if control is None:
                        preflight_blockers.append(f"{plan.field_plan_key}: control not found")
                        continue
                    if control.disabled or not control.visible:
                        preflight_blockers.append(f"{plan.field_plan_key}: control disabled/not visible")
                    if control.control_type.lower() != plan.control_type.lower():
                        preflight_blockers.append(
                            f"{plan.field_plan_key}: control type drift {control.control_type} != {plan.control_type}"
                        )
                    if control.max_length > 0 and len(plan.value) > control.max_length:
                        preflight_blockers.append(
                            f"{plan.field_plan_key}: value exceeds observed maxlength {control.max_length}"
                        )
                    if control.control_type == "select" and plan.value not in control.options:
                        preflight_blockers.append(
                            f"{plan.field_plan_key}: exact select option not observed"
                        )
                    if not control.element_id and not control.name:
                        preflight_blockers.append(
                            f"{plan.field_plan_key}: no stable id/name locator"
                        )
                if preflight_blockers:
                    result = self._failure(
                        request,
                        state=PrefillExecutionState.PLAN_BLOCKED,
                        error_code="CONTROL_PREFLIGHT_BLOCKED",
                        error_message="; ".join(preflight_blockers),
                        final_url=final_url,
                        ats_family=ats_family,
                        observed_form_fingerprint=observed_fp,
                    )
                    context.close(); browser.close()
                    return result

                field_results: list[FieldWriteResult] = []
                write_attempts = 0
                base_url = page.url
                for plan in request.field_plan:
                    control = by_key[plan.control_key]
                    if control.element_id:
                        locator = page.locator(
                            f'{control.tag_name}[id="{_attr_escape(control.element_id)}"]'
                        ).first
                    else:
                        locator = page.locator(
                            f'{control.tag_name}[name="{_attr_escape(control.name)}"]'
                        ).first

                    current = self._readback(locator, control.control_type)
                    if current == plan.value:
                        field_results.append(
                            FieldWriteResult(
                                field_plan_key=plan.field_plan_key,
                                control_key=plan.control_key,
                                canonical_field=plan.canonical_field,
                                control_type=plan.control_type,
                                value_hash=plan.value_hash,
                                readback_hash=value_hash(current),
                                status=FieldWriteStatus.ALREADY_MATCHED,
                                mutation_executed=False,
                                reason="pre-read already equals desired value",
                            )
                        )
                        continue

                    if control.control_type == "select":
                        locator.select_option(label=plan.value)
                    else:
                        locator.fill(plan.value)
                    write_attempts += 1

                    if page.url != base_url:
                        result = self._failure(
                            request,
                            state=PrefillExecutionState.READBACK_FAILED,
                            error_code="UNEXPECTED_NAVIGATION_AFTER_FIELD_WRITE",
                            error_message=f"navigation changed after {plan.field_plan_key}",
                            final_url=page.url,
                            ats_family=ats_family,
                            observed_form_fingerprint=observed_fp,
                            field_results=tuple(field_results),
                            form_value_write_attempts=write_attempts,
                        )
                        context.close(); browser.close()
                        return result

                    readback = self._readback(locator, control.control_type)
                    valid = locator.evaluate("(el) => el.checkValidity ? el.checkValidity() : true")
                    if readback != plan.value or not valid:
                        field_results.append(
                            FieldWriteResult(
                                field_plan_key=plan.field_plan_key,
                                control_key=plan.control_key,
                                canonical_field=plan.canonical_field,
                                control_type=plan.control_type,
                                value_hash=plan.value_hash,
                                readback_hash=value_hash(readback),
                                status=FieldWriteStatus.READBACK_MISMATCH,
                                mutation_executed=True,
                                reason="exact readback mismatch or browser validity failure",
                            )
                        )
                        result = self._failure(
                            request,
                            state=PrefillExecutionState.READBACK_FAILED,
                            error_code="FIELD_READBACK_MISMATCH",
                            error_message=f"readback mismatch for {plan.field_plan_key}",
                            final_url=final_url,
                            ats_family=ats_family,
                            observed_form_fingerprint=observed_fp,
                            field_results=tuple(field_results),
                            form_value_write_attempts=write_attempts,
                        )
                        context.close(); browser.close()
                        return result

                    field_results.append(
                        FieldWriteResult(
                            field_plan_key=plan.field_plan_key,
                            control_key=plan.control_key,
                            canonical_field=plan.canonical_field,
                            control_type=plan.control_type,
                            value_hash=plan.value_hash,
                            readback_hash=value_hash(readback),
                            status=FieldWriteStatus.WRITTEN_VERIFIED,
                            mutation_executed=True,
                        )
                    )

                final_extracted = page.evaluate(_CONTROL_EXTRACTOR)
                final_controls = tuple(
                    BrowserObservedControl(**item) for item in final_extracted.get("controls", [])
                )
                final_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url or request.application_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=final_controls,
                )
                verified = sum(
                    r.status in {FieldWriteStatus.WRITTEN_VERIFIED, FieldWriteStatus.ALREADY_MATCHED}
                    for r in field_results
                )
                already = sum(r.status is FieldWriteStatus.ALREADY_MATCHED for r in field_results)
                readback_all_match = verified == len(request.field_plan)
                state = (
                    PrefillExecutionState.REVIEW_GATE
                    if readback_all_match and final_fp == observed_fp
                    else PrefillExecutionState.SCHEMA_DRIFT
                )
                error_code = "" if state is PrefillExecutionState.REVIEW_GATE else "POST_FILL_FORM_DRIFT"
                error_message = "" if not error_code else "form structure changed after safe-field writes"
                result = PrefillExecutionResult(
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
                    field_results=tuple(field_results),
                    planned_fields=len(request.field_plan),
                    verified_fields=verified,
                    already_matched_fields=already,
                    form_value_write_attempts=write_attempts,
                    file_upload_attempts=0,
                    submit_attempts=0,
                    readback_all_match=readback_all_match,
                    review_gate_reached=state is PrefillExecutionState.REVIEW_GATE,
                    error_code=error_code,
                    error_message=error_message,
                )
                context.close(); browser.close()
                return result
        except Exception as exc:
            msg = str(exc)
            lower = msg.lower()
            if "err_blocked_by_administrator" in lower:
                return self._failure(
                    request,
                    state=PrefillExecutionState.ACCESS_BLOCKED,
                    error_code="ERR_BLOCKED_BY_ADMINISTRATOR",
                    error_message=msg,
                )
            if "err_name_not_resolved" in lower:
                return self._failure(
                    request,
                    state=PrefillExecutionState.ACCESS_BLOCKED,
                    error_code="DNS_UNAVAILABLE",
                    error_message=msg,
                )
            return self._failure(
                request,
                state=PrefillExecutionState.ERROR,
                error_code="PREFILL_EXECUTION_ERROR",
                error_message=msg,
            )

    @staticmethod
    def _readback(locator, control_type: str) -> str:
        if control_type == "select":
            return locator.evaluate(
                "(el) => (el.selectedOptions && el.selectedOptions[0] ? el.selectedOptions[0].textContent : '').trim()"
            )
        return locator.input_value()

    @staticmethod
    def _failure(
        request: PrefillExecutionRequest,
        *,
        state: PrefillExecutionState,
        error_code: str,
        error_message: str,
        final_url: str = "",
        ats_family: str = "",
        observed_form_fingerprint: str = "",
        field_results: tuple[FieldWriteResult, ...] = (),
        form_value_write_attempts: int = 0,
    ) -> PrefillExecutionResult:
        return PrefillExecutionResult(
            execution_id=request.execution_id,
            route_key=request.route_key,
            opportunity_key=request.opportunity_key,
            requisition_id=request.requisition_id,
            runtime_state=state,
            requested_url=request.application_url,
            final_url=final_url,
            ats_family=ats_family or detect_ats_family(request.application_url, request.adapter_key),
            expected_form_fingerprint=request.expected_form_fingerprint,
            observed_form_fingerprint=observed_form_fingerprint,
            final_form_fingerprint="",
            field_results=field_results,
            planned_fields=len(request.field_plan),
            verified_fields=sum(
                r.status in {FieldWriteStatus.WRITTEN_VERIFIED, FieldWriteStatus.ALREADY_MATCHED}
                for r in field_results
            ),
            already_matched_fields=sum(r.status is FieldWriteStatus.ALREADY_MATCHED for r in field_results),
            form_value_write_attempts=form_value_write_attempts,
            file_upload_attempts=0,
            submit_attempts=0,
            readback_all_match=False,
            review_gate_reached=False,
            error_code=error_code,
            error_message=error_message,
        )
