from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any
from urllib.parse import unquote, urlparse

from ejs.contracts.browser import (
    AuthBoundaryType,
    BrowserInspectionRequest,
    BrowserWorkerAuthority,
    CaptchaState,
    DomSnapshotState,
    RuntimeState,
)
from ejs.domain.browser_runtime import (
    BrowserActionControl,
    BrowserInspectionResult,
    BrowserObservedControl,
    detect_ats_family,
    page_structure_fingerprint,
    runtime_form_fingerprint,
)


@dataclass(frozen=True)
class BrowserRuntimeConfig:
    executable_path: str = ""
    headless: bool = True
    navigation_timeout_ms: int = 20_000
    settle_timeout_ms: int = 2_000
    ignore_https_errors: bool = False
    use_playwright_managed: bool = False

    def resolved_executable_path(self) -> str:
        if self.executable_path:
            return self.executable_path
        return (
            shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or ""
        )


_CONTROL_EXTRACTOR = r"""
() => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const st = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return st.display !== 'none' && st.visibility !== 'hidden' && r.width >= 0 && r.height >= 0;
  };
  const labelFor = (el) => {
    if (el.id) {
      const exact = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (exact) return norm(exact.innerText || exact.textContent);
    }
    const parentLabel = el.closest('label');
    if (parentLabel) return norm(parentLabel.innerText || parentLabel.textContent);
    const aria = norm(el.getAttribute('aria-label'));
    if (aria) return aria;
    const described = norm(el.getAttribute('aria-labelledby'));
    if (described) {
      const parts = described.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean);
      const txt = norm(parts.map(x => x.innerText || x.textContent).join(' '));
      if (txt) return txt;
    }
    return norm(el.getAttribute('placeholder') || el.getAttribute('name') || el.id || '');
  };
  const requiredEvidence = (el, label) => {
    if (el.required) return 'required-attribute';
    if ((el.getAttribute('aria-required') || '').toLowerCase() === 'true') return 'aria-required';
    if (/\*\s*$/.test(label) || /\brequired\b/i.test(label)) return 'label-marker';
    return 'none';
  };
  const controls = [];
  document.querySelectorAll('input, textarea, select').forEach((el, idx) => {
    const type = (el.tagName.toLowerCase() === 'input' ? (el.type || 'text') : el.tagName.toLowerCase()).toLowerCase();
    if (['hidden','submit','button','reset','image'].includes(type)) return;
    const label = labelFor(el);
    const reqEv = requiredEvidence(el, label);
    const options = el.tagName.toLowerCase() === 'select'
      ? Array.from(el.options).map(o => norm(o.textContent)).filter(Boolean)
      : [];
    controls.push({
      control_key: `${el.tagName.toLowerCase()}:${el.id || el.name || idx}`,
      tag_name: el.tagName.toLowerCase(),
      control_type: type,
      label,
      name: norm(el.getAttribute('name')),
      element_id: norm(el.id),
      required: reqEv !== 'none',
      required_evidence: reqEv,
      visible: visible(el),
      disabled: !!el.disabled,
      options,
      autocomplete: norm(el.getAttribute('autocomplete')),
      aria_label: norm(el.getAttribute('aria-label')),
      placeholder: norm(el.getAttribute('placeholder')),
      max_length: Number.isFinite(el.maxLength) ? el.maxLength : -1,
      accept: norm(el.getAttribute('accept')),
      multiple: !!el.multiple,
    });
  });
  const actions = [];
  document.querySelectorAll('button, input[type="submit"], input[type="button"], a[role="button"]').forEach((el, idx) => {
    const label = norm(el.innerText || el.value || el.textContent || el.getAttribute('aria-label'));
    const lower = label.toLowerCase();
    let probable = 'other';
    if (/submit|apply|send application|send$/i.test(label)) probable = 'submit';
    else if (/next|continue|proceed/i.test(label)) probable = 'next';
    else if (/sign in|log in/i.test(label)) probable = 'sign_in';
    else if (/create account|register/i.test(label)) probable = 'create_account';
    actions.push({
      action_key: `${el.tagName.toLowerCase()}:${el.id || el.getAttribute('name') || idx}`,
      tag_name: el.tagName.toLowerCase(),
      control_type: (el.type || el.getAttribute('role') || 'button').toLowerCase(),
      label,
      disabled: !!el.disabled,
      probable_action: probable,
    });
  });
  const bodyText = norm(document.body ? document.body.innerText : '');
  const captchaPresent = !!document.querySelector(
    'iframe[src*="recaptcha" i], iframe[src*="hcaptcha" i], [class*="captcha" i], [id*="captcha" i], [data-sitekey]'
  ) || /\bcaptcha\b/i.test(bodyText);
  const authSignals = {
    sign_in: /\bsign in\b|\blog in\b/i.test(bodyText),
    create_account: /\bcreate account\b|\bregister\b/i.test(bodyText),
    session_required: /session required|please sign in to continue/i.test(bodyText),
  };
  return {controls, actions, captchaPresent, authSignals};
}
"""


class PlaywrightBrowserWorker:
    """Real Chromium/Playwright read-only inspection worker.

    The worker has no fill/click/upload methods by design. DOM evaluation is read-only and the
    only browser navigation it initiates is page.goto to the requested application URL.
    """

    def __init__(self, config: BrowserRuntimeConfig | None = None):
        self.config = config or BrowserRuntimeConfig()

    @staticmethod
    def _typed_navigation_error(message: str) -> tuple[RuntimeState, str]:
        m = message.lower()
        if "err_blocked_by_administrator" in m:
            return RuntimeState.ACCESS_BLOCKED, "ERR_BLOCKED_BY_ADMINISTRATOR"
        if "err_name_not_resolved" in m or "name resolution" in m:
            return RuntimeState.ACCESS_BLOCKED, "DNS_UNAVAILABLE"
        if "err_timed_out" in m or "timeout" in m:
            return RuntimeState.NAVIGATION_ERROR, "NAVIGATION_TIMEOUT"
        if "err_aborted" in m:
            return RuntimeState.NAVIGATION_ERROR, "NAVIGATION_ABORTED"
        return RuntimeState.NAVIGATION_ERROR, "NAVIGATION_ERROR"

    def inspect(
        self,
        request: BrowserInspectionRequest,
        *,
        authority: BrowserWorkerAuthority | None = None,
    ) -> BrowserInspectionResult:
        request.validate()
        authority = authority or BrowserWorkerAuthority()
        authority.validate()

        executable = self.config.resolved_executable_path()
        if not executable and not self.config.use_playwright_managed:
            return self._failure(
                request,
                state=RuntimeState.UNSUPPORTED,
                error_code="CHROMIUM_NOT_FOUND",
                error_message="No Chromium/Chrome executable found",
            )

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # pragma: no cover - environment guard
            return self._failure(
                request,
                state=RuntimeState.UNSUPPORTED,
                error_code="PLAYWRIGHT_UNAVAILABLE",
                error_message=str(exc),
            )

        trace: list[str] = []
        launched_browser_version = ""
        try:
            with sync_playwright() as p:
                launch_kwargs = {
                    "headless": self.config.headless,
                    "args": ["--no-sandbox", "--disable-dev-shm-usage"],
                }
                if executable:
                    launch_kwargs["executable_path"] = executable
                browser = p.chromium.launch(**launch_kwargs)
                launched_browser_version = browser.version
                context = browser.new_context(
                    ignore_https_errors=self.config.ignore_https_errors,
                    accept_downloads=False,
                )
                page = context.new_page()
                page.set_default_timeout(self.config.navigation_timeout_ms)
                page.set_default_navigation_timeout(self.config.navigation_timeout_ms)

                def on_nav(frame: Any) -> None:
                    if frame == page.main_frame:
                        url = frame.url
                        if url and (not trace or trace[-1] != url):
                            trace.append(url)

                page.on("framenavigated", on_nav)
                if request.application_url.startswith("file://"):
                    local_path = Path(unquote(urlparse(request.application_url).path))
                    html = local_path.read_text(encoding="utf-8")
                    page.set_content(
                        html,
                        wait_until="domcontentloaded",
                        timeout=self.config.navigation_timeout_ms,
                    )
                    trace.append(request.application_url)
                    response = None
                else:
                    response = page.goto(
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
                        state=RuntimeState.NAVIGATION_ERROR,
                        error_code="MAX_NAVIGATION_STEPS_EXCEEDED",
                        error_message=f"navigation trace exceeded {request.max_navigation_steps}",
                        final_url=page.url,
                        navigation_trace=tuple(trace[: request.max_navigation_steps + 1]),
                    )
                    context.close()
                    browser.close()
                    return result

                extracted = page.evaluate(_CONTROL_EXTRACTOR)
                controls = tuple(BrowserObservedControl(**item) for item in extracted.get("controls", []))
                actions = tuple(BrowserActionControl(**item) for item in extracted.get("actions", []))
                title = page.title()
                final_url = request.application_url if request.application_url.startswith("file://") else page.url
                ats_family = detect_ats_family(final_url or request.application_url, request.adapter_key)

                captcha = CaptchaState.PRESENT if extracted.get("captchaPresent") else CaptchaState.NONE_OBSERVED
                auth_signals = extracted.get("authSignals") or {}
                auth_type = AuthBoundaryType.NONE
                if auth_signals.get("session_required"):
                    auth_type = AuthBoundaryType.SESSION_REQUIRED
                elif auth_signals.get("sign_in"):
                    auth_type = AuthBoundaryType.SIGN_IN
                elif auth_signals.get("create_account"):
                    auth_type = AuthBoundaryType.CREATE_ACCOUNT

                if captcha is CaptchaState.PRESENT:
                    state = RuntimeState.CAPTCHA_BOUNDARY
                elif auth_type is not AuthBoundaryType.NONE and not controls:
                    state = RuntimeState.AUTH_BOUNDARY
                else:
                    state = RuntimeState.RENDERED

                page_fp = page_structure_fingerprint(
                    ats_family=ats_family,
                    title=title,
                    controls=controls,
                    actions=actions,
                )
                form_fp = runtime_form_fingerprint(
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    apply_url=final_url or request.application_url,
                    ats_family=ats_family,
                    observed_at=request.observed_at,
                    controls=controls,
                )
                version = browser.version
                status = response.status if response else None
                error_code = "" if status is None or status < 400 else f"HTTP_{status}"
                result = BrowserInspectionResult(
                    bridge_request_id=request.bridge_request_id,
                    route_key=request.route_key,
                    opportunity_key=request.opportunity_key,
                    requisition_id=request.requisition_id,
                    requested_url=request.application_url,
                    final_url=final_url,
                    adapter_key=request.adapter_key,
                    ats_family=ats_family,
                    runtime_state=state,
                    page_title=title,
                    page_fingerprint=page_fp,
                    form_fingerprint=form_fp,
                    dom_snapshot_state=DomSnapshotState.AVAILABLE,
                    auth_boundary_type=auth_type,
                    captcha_state=captcha,
                    controls=controls,
                    action_controls=actions,
                    navigation_trace=tuple(trace or [final_url]),
                    observed_at=request.observed_at,
                    browser_engine="chromium",
                    browser_version=version,
                    error_code=error_code,
                    error_message="",
                )
                context.close()
                browser.close()
                return result
        except Exception as exc:
            state, code = self._typed_navigation_error(str(exc))
            return self._failure(
                request,
                state=state,
                error_code=code,
                error_message=str(exc),
                navigation_trace=tuple(trace),
                browser_version=launched_browser_version,
            )

    def _failure(
        self,
        request: BrowserInspectionRequest,
        *,
        state: RuntimeState,
        error_code: str,
        error_message: str,
        final_url: str = "",
        navigation_trace: tuple[str, ...] = (),
        browser_version: str = "",
    ) -> BrowserInspectionResult:
        return BrowserInspectionResult(
            bridge_request_id=request.bridge_request_id,
            route_key=request.route_key,
            opportunity_key=request.opportunity_key,
            requisition_id=request.requisition_id,
            requested_url=request.application_url,
            final_url=final_url,
            adapter_key=request.adapter_key,
            ats_family=detect_ats_family(final_url or request.application_url, request.adapter_key),
            runtime_state=state,
            page_title="",
            page_fingerprint="",
            form_fingerprint="",
            dom_snapshot_state=DomSnapshotState.UNAVAILABLE,
            auth_boundary_type=AuthBoundaryType.UNKNOWN,
            captcha_state=CaptchaState.UNKNOWN,
            controls=(),
            action_controls=(),
            navigation_trace=navigation_trace,
            observed_at=request.observed_at,
            browser_engine="chromium",
            browser_version=browser_version,
            error_code=error_code,
            error_message=error_message,
        )
