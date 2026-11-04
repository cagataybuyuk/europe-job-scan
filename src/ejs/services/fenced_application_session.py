"""One in-memory fenced browser lifecycle; no session export or submit authority.

Only the owning executor creates a session. Callers can inspect a value-free
snapshot and prepare once using its fresh observation token; arbitrary externally
created Playwright pages cannot be supplied to this API. Preserving page identity
does not grant CAPTCHA/authentication network permissions.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import secrets
from time import monotonic
from urllib.parse import unquote, urlparse

from ejs.domain.browser_runtime import BrowserObservedControl, runtime_form_fingerprint
from ejs.domain.smartrecruiters_execution import SmartRecruitersExecutionState
from ejs.services.browser_worker import _CONTROL_EXTRACTOR
from ejs.services.form_compatibility import FORM_COMPATIBILITY_PROBE, compatibility_blockers
from ejs.services.prepare_network_fence import PrepareBoundaryError, PrepareNetworkFence


@dataclass(frozen=True)
class SessionObservation:
    observation_id: str
    form_fingerprint: str
    controls: tuple[BrowserObservedControl, ...]
    runtime_state: str
    error_code: str
    observed_at: str
    inspection_only: bool = True


class FencedApplicationSession:
    def __init__(self, executor, target_url: str, *, session_timeout_seconds: float = 900,
                 observation_max_age_seconds: float = 120):
        for value, maximum in ((session_timeout_seconds, 900), (observation_max_age_seconds, 300)):
            if type(value) not in (int, float) or not 0 < value <= maximum:
                raise ValueError("INVALID_SESSION_TIMEOUT")
        self._executor = executor
        self._target_url = target_url
        self._config = executor.config
        self._timeout = session_timeout_seconds
        self._observation_age = observation_max_age_seconds
        self._state = "NEW"
        self._terminal_reason = ""
        self._playwright_manager = self._browser = self._context = self._page = None
        self._fence = PrepareNetworkFence(target_url)
        self._approved_page_url = "about:blank" if self._fence.fixture else target_url
        self._trace = []
        self._write_attempts = self._upload_attempts = 0
        self._started_at = None
        self._observation = self._snapshot_at_review = None
        self._observed_monotonic = None
        self._prepare_attempted = False

    @property
    def state(self) -> str:
        return self._state

    @property
    def attempts(self) -> dict:
        return {"form_value_write_attempts": self._write_attempts,
                "file_upload_attempts": self._upload_attempts, "submit_attempts": 0}

    def __enter__(self):
        if self._state != "NEW":
            raise PrepareBoundaryError("SESSION_CANNOT_REOPEN")
        self._state = "OPENING"
        self._started_at = monotonic()
        try:
            from playwright.sync_api import sync_playwright
            executable = self._config.resolved_executable_path()
            if not executable:
                raise PrepareBoundaryError("CHROMIUM_NOT_FOUND")
            self._playwright_manager = sync_playwright().start()
            self._browser = self._playwright_manager.chromium.launch(
                headless=self._config.headless, executable_path=executable,
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            self._context = self._browser.new_context(ignore_https_errors=self._config.ignore_https_errors,
                accept_downloads=False, service_workers="block")
            self._fence.install(self._context)
            self._page = self._context.new_page()
            def reject_extra_page(extra_page):
                if extra_page != self._page:
                    self._fence.deny("EXTRA_PAGE_DENIED")
                    extra_page.close()
            self._context.on("page", reject_extra_page)
            self._page.set_default_timeout(self._config.navigation_timeout_ms)
            self._page.set_default_navigation_timeout(self._config.navigation_timeout_ms)
            def on_navigation(frame):
                if frame == self._page.main_frame:
                    url = frame.url
                    if url and (not self._trace or self._trace[-1] != url):
                        self._trace.append(url)
            self._page.on("framenavigated", on_navigation)
            if self._fence.fixture:
                path = Path(unquote(urlparse(self._target_url).path))
                self._page.set_content(path.read_text(encoding="utf-8"), wait_until="domcontentloaded",
                                       timeout=self._config.navigation_timeout_ms)
                self._trace.append(self._target_url)
            else:
                self._page.goto(self._target_url, wait_until="domcontentloaded",
                                timeout=self._config.navigation_timeout_ms)
            self._page.wait_for_timeout(min(self._config.settle_timeout_ms, 5_000))
            self._state = "OPEN"
            self._require_active()
            return self
        except BaseException as exc:
            # __exit__ is not called when __enter__ fails. Cancellation must
            # still close the already-created context/browser, then propagate.
            cancelled = not isinstance(exc, Exception)
            code = ("SESSION_OPEN_CANCELLED" if cancelled else
                    exc.code if isinstance(exc, PrepareBoundaryError) else "SESSION_OPEN_FAILED")
            if self._fence.blocked:
                code = "PREPARE_NETWORK_BLOCKED"
            self._terminal_reason = code
            self._state = "BLOCKED"
            self._close_resources()
            if cancelled:
                raise
            raise PrepareBoundaryError(code) from None

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False

    def _close_resources(self):
        for resource in (self._context, self._browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._playwright_manager is not None:
            try:
                self._playwright_manager.stop()
            except Exception:
                pass
        self._context = self._browser = self._playwright_manager = None
        self._observation = self._snapshot_at_review = None

    def close(self):
        self._close_resources()
        self._state = "CLOSED"

    def _block(self, code: str):
        self._terminal_reason = code
        self._state = "BLOCKED"
        self._observation = self._snapshot_at_review = None
        raise PrepareBoundaryError(code)

    def _require_active(self, *, preparing: bool = False):
        allowed = {"PREPARING"} if preparing else {"OPEN", "INSPECTED"}
        if self._state not in allowed:
            raise PrepareBoundaryError(self._terminal_reason or "SESSION_NOT_ACTIVE")
        if monotonic() - self._started_at >= self._timeout:
            self._terminal_reason = "SESSION_EXPIRED"
            self._state = "EXPIRED"
            self._close_resources()
            raise PrepareBoundaryError("SESSION_EXPIRED")
        if self._page is None or self._page.is_closed():
            self._block("SESSION_PAGE_CLOSED")
        if self._fence.blocked:
            self._block("PREPARE_NETWORK_BLOCKED")
        if self._page.url != self._approved_page_url:
            self._block("PREPARE_TARGET_DRIFT")

    def _snapshot(self, *, preparing: bool = False):
        self._require_active(preparing=preparing)
        extracted = self._page.evaluate(_CONTROL_EXTRACTOR)
        self._fence.require_clear()
        controls = tuple(BrowserObservedControl(**x) for x in extracted.get("controls", []))
        compatibility = (("FRAME_ADAPTER_REQUIRED",) if len(self._page.frames) != 1 else ()) + compatibility_blockers(
            self._page.evaluate(FORM_COMPATIBILITY_PROBE))
        self._require_active(preparing=preparing)
        return {"controls": controls, "captcha": extracted.get("captchaPresent"),
                "auth": extracted.get("authSignals"), "compatibility": compatibility,
                "actions": extracted.get("actions", [])}

    def inspect(self) -> SessionObservation:
        try:
            snapshot = self._snapshot()
            observed_at = datetime.now(timezone.utc).isoformat()
            fingerprint = runtime_form_fingerprint(opportunity_key="", requisition_id="",
                apply_url=self._target_url, ats_family="smartrecruiters", observed_at=observed_at,
                controls=snapshot["controls"])
            code = ("CAPTCHA_BOUNDARY" if snapshot["captcha"] else
                    "AUTH_BOUNDARY" if any((snapshot["auth"] or {}).values()) else
                    snapshot["compatibility"][0] if snapshot["compatibility"] else "")
            self._observed_monotonic = monotonic()
            self._snapshot_at_review = snapshot
            self._observation = SessionObservation(secrets.token_hex(24), fingerprint,
                deepcopy(snapshot["controls"]), "boundary" if code else "inspected", code, observed_at)
            self._state = "INSPECTED"
            # Do not return internal snapshot containers to a caller.
            return deepcopy(self._observation)
        except Exception as exc:
            if self._state not in {"CLOSED", "EXPIRED"}:
                self._state = "BLOCKED"
            code = exc.code if isinstance(exc, PrepareBoundaryError) else "SESSION_INSPECTION_FAILED"
            self._terminal_reason = code
            raise PrepareBoundaryError(code) from None

    def _validate_reviewed_observation(self, request, reviewed_observation_id):
        self._require_active()
        if self._prepare_attempted:
            self._block("SESSION_PREPARE_ALREADY_ATTEMPTED")
        if self._observation is None or reviewed_observation_id != self._observation.observation_id:
            self._block("SESSION_OBSERVATION_MISMATCH")
        if monotonic() - self._observed_monotonic >= self._observation_age:
            self._block("SESSION_OBSERVATION_EXPIRED")
        if request.application_url != self._target_url:
            self._block("SESSION_TARGET_MISMATCH")
        current = self._snapshot()
        if current != self._snapshot_at_review:
            self._block("SESSION_OBSERVATION_DRIFT")

    def prepare(self, request, *, reviewed_observation_id: str, authority=None, policy=None):
        """One preparation attempt; token approves only the observed form binding.

        Candidate facts/asset and independent review checks still belong to the
        request compiler and executor. This token grants no extra authority.
        """
        try:
            self._validate_reviewed_observation(request, reviewed_observation_id)
        except Exception as exc:
            code = exc.code if isinstance(exc, PrepareBoundaryError) else "SESSION_OBSERVATION_FAILED"
            if self._state not in {"CLOSED", "EXPIRED"}:
                self._state = "BLOCKED"
            self._terminal_reason = code
            raise PrepareBoundaryError(code) from None
        self._prepare_attempted = True
        self._state = "PREPARING"
        try:
            result = self._executor._prepare_in_session(self, request, authority=authority, policy=policy)
            if result.runtime_state.value == "pre_submit_ready":
                try:
                    # The executor's result is a proposal until this retained
                    # session rechecks its own final lifetime and boundaries.
                    final = self._snapshot(preparing=True)
                    code = ("CAPTCHA_BOUNDARY" if final["captcha"] else
                            "AUTH_BOUNDARY" if any((final["auth"] or {}).values()) else
                            final["compatibility"][0] if final["compatibility"] else "")
                    if code:
                        raise PrepareBoundaryError(code)
                    if final["controls"] != self._snapshot_at_review["controls"]:
                        raise PrepareBoundaryError("PREPARE_FORM_DRIFT")
                    self._require_active(preparing=True)
                except Exception as exc:
                    code = exc.code if isinstance(exc, PrepareBoundaryError) else "SESSION_FINAL_VALIDATION_FAILED"
                    if self._state != "EXPIRED":
                        self._state = "BLOCKED"
                    self._terminal_reason = code
                    self._observation = self._snapshot_at_review = None
                    return self._executor._failure(request, SmartRecruitersExecutionState.BOUNDARY,
                        code, "Final preparation validation blocked; reconcile before retrying",
                        final_url=result.final_url, observed_fp=result.observed_form_fingerprint,
                        final_fp=result.final_form_fingerprint, field_results=result.field_results,
                        file_results=result.file_results, writes=self._write_attempts, uploads=self._upload_attempts)
            if self._state != "EXPIRED":
                self._state = "PRE_SUBMIT_READY" if result.runtime_state.value == "pre_submit_ready" else "BLOCKED"
            if self._state == "BLOCKED":
                self._terminal_reason = result.error_code or "SESSION_PREPARATION_BLOCKED"
            self._observation = self._snapshot_at_review = None
            return result
        except Exception:
            self._state = "BLOCKED"
            self._terminal_reason = "SESSION_PREPARATION_FAILED"
            raise PrepareBoundaryError("SESSION_PREPARATION_FAILED") from None
