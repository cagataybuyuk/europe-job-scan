"""Read-only post-Continue diagnostics for the reviewed ADP application flow.

The canary reproduces only the already reviewed mutation sequence:
- OneTrust preferences -> Unselect All -> Save Changes;
- one reviewed Apply click;
- at most three AUTO_SAFE identity writes with exact readback;
- one reviewed Continue click.

After Continue, authority becomes strictly read-only. Diagnostics capture only
structural/validation evidence with candidate values redacted. The canary never
touches phone/country, credentials, social sign-in, file upload, another
application action, CAPTCHA handling, or final Submit.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import re
import sys

from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, SafeFieldWriterAuthority, value_hash
from ejs.services.adp_cookie_preferences_canary import (
    PREFERENCES_LABEL,
    preference_surface_descriptor,
    preference_surface_fingerprint,
)
from ejs.services.adp_continue_canary import (
    CONTINUE_LABEL,
    action_surface_descriptor,
    action_surface_fingerprint,
)
from ejs.services.adp_live_inspector import validate_adp_live_url, visible_application_controls
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _approved_entry,
    _normalize,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.adp_preference_safe_fill_canary import (
    SAVE_CHANGES_LABEL,
    UNSELECT_ALL_LABEL,
    _unique_actionable_button,
)
from ejs.services.adp_safe_fill_canary import (
    TARGETS,
    _validate_target_control,
    load_identity_profile,
    safe_fill_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-continue-diagnostic-canary-v2"
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")


@dataclass(frozen=True)
class AdpContinueDiagnosticCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    expected_preference_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    expected_post_fill_action_surface_fingerprint: str
    profile_manifest_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpContinueDiagnosticCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value, code in (
        (request.expected_navigation_surface_fingerprint, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"),
        (request.expected_preference_surface_fingerprint, "INVALID_EXPECTED_PREFERENCE_SURFACE_FINGERPRINT"),
        (request.expected_safe_fill_surface_fingerprint, "INVALID_EXPECTED_SAFE_FILL_SURFACE_FINGERPRINT"),
        (request.expected_post_fill_action_surface_fingerprint, "INVALID_EXPECTED_POST_FILL_ACTION_SURFACE_FINGERPRINT"),
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError(code)
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
    if not request.profile_manifest_path:
        raise ValueError("ADP_CONTINUE_DIAGNOSTIC_REQUIRES_PROFILE_MANIFEST")
    SafeFieldWriterAuthority().validate()
    if not {item[0] for item in TARGETS}.issubset(SAFE_VERIFIED_FIELDS):
        raise PermissionError("ADP_CONTINUE_DIAGNOSTIC_TARGET_NOT_AUTO_SAFE")


def sanitize_text(value: str, profile: dict[str, str]) -> str:
    text = " ".join((value or "").split())
    for candidate in sorted((v for v in profile.values() if v), key=len, reverse=True):
        text = re.sub(re.escape(candidate), "[REDACTED_CANDIDATE_VALUE]", text, flags=re.IGNORECASE)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = PHONE_RE.sub("[REDACTED_PHONE]", text)
    return text[:300]


def _visible_text_evidence(page, selector: str, profile: dict[str, str], limit: int = 30) -> list[dict]:
    rows: list[dict] = []
    locators = page.locator(selector)
    for index in range(min(locators.count(), limit)):
        locator = locators.nth(index)
        try:
            if not locator.is_visible():
                continue
            text = sanitize_text(locator.inner_text() or locator.get_attribute("aria-label") or "", profile)
            if not text:
                continue
            rows.append({
                "tag": locator.evaluate("el => el.tagName.toLowerCase()"),
                "id": str(locator.get_attribute("id") or "")[:120],
                "role": str(locator.get_attribute("role") or "")[:80],
                "aria_live": str(locator.get_attribute("aria-live") or "")[:40],
                "text": text,
            })
        except Exception:
            continue
    return rows


def _invalid_control_evidence(page, profile: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    locators = page.locator('[aria-invalid="true"], input:invalid, select:invalid, textarea:invalid')
    for index in range(min(locators.count(), 40)):
        locator = locators.nth(index)
        try:
            if not locator.is_visible():
                continue
            message = locator.evaluate("el => el.validationMessage || ''")
            rows.append({
                "tag": locator.evaluate("el => el.tagName.toLowerCase()"),
                "id": str(locator.get_attribute("id") or "")[:120],
                "name": str(locator.get_attribute("name") or "")[:120],
                "type": str(locator.get_attribute("type") or "")[:60],
                "aria_invalid": str(locator.get_attribute("aria-invalid") or "")[:20],
                "required": locator.get_attribute("required") is not None,
                "disabled": locator.is_disabled(),
                "validation_message": sanitize_text(str(message or ""), profile),
            })
        except Exception:
            continue
    return rows


def _custom_component_evidence(page) -> list[dict]:
    script = r"""
    () => {
      const rows = [];
      const visible = (el) => {
        try {
          const style = getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          return style.display !== 'none' && style.visibility !== 'hidden' &&
                 Number(style.opacity || 1) !== 0 && rect.width > 0 && rect.height > 0;
        } catch (_) { return false; }
      };
      const walk = (root, scope) => {
        let items = [];
        try { items = Array.from(root.querySelectorAll('*')); } catch (_) { return; }
        for (let i = 0; i < items.length && rows.length < 100; i++) {
          const el = items[i];
          const tag = (el.tagName || '').toLowerCase();
          if (tag.includes('-') && visible(el)) {
            rows.push({
              scope,
              tag,
              id: (el.getAttribute('id') || '').slice(0, 120),
              name: (el.getAttribute('name') || '').slice(0, 120),
              role: (el.getAttribute('role') || '').slice(0, 80),
              aria_label: (el.getAttribute('aria-label') || '').slice(0, 160),
              aria_invalid: (el.getAttribute('aria-invalid') || '').slice(0, 20),
              aria_required: (el.getAttribute('aria-required') || '').slice(0, 20),
              disabled: el.hasAttribute('disabled'),
              required: el.hasAttribute('required')
            });
          }
          if (el.shadowRoot && rows.length < 100) {
            walk(el.shadowRoot, `${scope}/${tag || 'host'}::shadow`);
          }
        }
      };
      walk(document, 'document');
      return rows;
    }
    """
    try:
        result = page.evaluate(script)
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _active_element_evidence(page) -> dict:
    script = r"""
    () => {
      let el = document.activeElement;
      let depth = 0;
      while (el && el.shadowRoot && el.shadowRoot.activeElement && depth < 10) {
        el = el.shadowRoot.activeElement;
        depth += 1;
      }
      if (!el) return {};
      return {
        tag: (el.tagName || '').toLowerCase(),
        id: (el.getAttribute('id') || '').slice(0, 120),
        name: (el.getAttribute('name') || '').slice(0, 120),
        role: (el.getAttribute('role') || '').slice(0, 80),
        aria_label: (el.getAttribute('aria-label') || '').slice(0, 160),
        aria_invalid: (el.getAttribute('aria-invalid') || '').slice(0, 20),
        shadow_depth: depth
      };
    }
    """
    try:
        result = page.evaluate(script)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


VERIFICATION_CODE_CONTROL_ID = "oneTimePassWord"
VERIFICATION_CODE_LABEL = "Enter the Verification Code"
VERIFY_BUTTON_LABEL = "verify"


def _verification_code_surface(visible_controls: list[dict], button_surface: dict, alerts: list[dict]) -> dict:
    control = next(
        (
            item for item in visible_controls
            if str(item.get("id", "")) == VERIFICATION_CODE_CONTROL_ID
            and str(item.get("label", "")) == VERIFICATION_CODE_LABEL
            and item.get("required") is True
            and item.get("disabled") is not True
        ),
        None,
    )
    verify_buttons = [
        item for item in (button_surface.get("visible_buttons", []) or [])
        if str(item.get("label", "")).strip().casefold() == VERIFY_BUTTON_LABEL
    ]
    sent_notice = any(
        "verification code sent to your email address" in str(item.get("text", "")).casefold()
        for item in alerts
    )
    observed = control is not None and len(verify_buttons) == 1 and sent_notice
    return {
        "observed": observed,
        "channel": "email" if observed else "",
        "control_id": VERIFICATION_CODE_CONTROL_ID if control is not None else "",
        "control_required": bool(control is not None),
        "verify_button_present": len(verify_buttons) == 1,
        "verify_button_enabled": bool(verify_buttons and verify_buttons[0].get("enabled") is True),
        "sent_notice_observed": sent_notice,
        "raw_code_exposed": False,
    }


def post_continue_diagnostics(page, profile: dict[str, str], snapshot: dict, expected_fill_fp: str) -> dict:
    alerts = _visible_text_evidence(
        page,
        '[role="alert"], [aria-live]:not([aria-live="off"]), sdf-alert, sdf-alert-toaster',
        profile,
    )
    error_text = _visible_text_evidence(
        page,
        '[class*="error" i], [id*="error" i], [data-testid*="error" i]',
        profile,
    )
    invalid_controls = _invalid_control_evidence(page, profile)
    visible_controls = visible_application_controls(snapshot.get("form", {}))
    button_surface = action_surface_descriptor(page)
    verification = _verification_code_surface(visible_controls, button_surface, alerts)
    observed_fill_fp = safe_fill_surface_fingerprint(snapshot)
    same_identity_surface = observed_fill_fp == expected_fill_fp
    if verification["observed"]:
        diagnosis = "verification_code_surface_observed"
    elif alerts or invalid_controls or error_text:
        diagnosis = "validation_or_error_surface_observed"
    elif same_identity_surface:
        diagnosis = "no_stage_transition_identity_surface_persisted"
    else:
        diagnosis = "stage_changed_or_custom_surface_requires_review"
    return {
        "diagnosis": diagnosis,
        "same_identity_surface": same_identity_surface,
        "observed_safe_fill_surface_fingerprint": observed_fill_fp,
        "visible_application_control_count": len(visible_controls),
        "visible_application_controls": [
            {
                "tag": str(c.get("tag", "")),
                "type": str(c.get("type", "")),
                "id": str(c.get("id", "")),
                "name": str(c.get("name", "")),
                "label": str(c.get("label", "")),
                "required": c.get("required") is True,
                "disabled": c.get("disabled") is True,
            }
            for c in visible_controls
        ],
        "visible_button_surface": button_surface,
        "verification_boundary": verification,
        "alerts": alerts,
        "error_text": error_text,
        "invalid_controls": invalid_controls,
        "custom_components": _custom_component_evidence(page),
        "active_element": _active_element_evidence(page),
    }


def _base_report(request: AdpContinueDiagnosticCanaryRequest, profile_version: str) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "diagnostic_canary_only": True,
        "requested_url": request.application_url,
        "candidate_profile_version": profile_version,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_preference_surface_fingerprint": request.expected_preference_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "expected_post_fill_action_surface_fingerprint": request.expected_post_fill_action_surface_fingerprint,
        "preference_navigation_click_attempts": 0,
        "preference_navigation_click_successes": 0,
        "cookie_unselect_all_click_attempts": 0,
        "cookie_unselect_all_click_successes": 0,
        "cookie_toggle_write_attempts": 0,
        "cookie_save_click_attempts": 0,
        "cookie_save_click_successes": 0,
        "navigation_click_attempts": 0,
        "navigation_click_successes": 0,
        "form_value_write_attempts": 0,
        "second_action_click_attempts": 0,
        "second_action_click_successes": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "optional_cookie_accept_attempts": 0,
        "further_navigation_allowed": False,
        "safe_fill_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _result(base: dict, counters: dict, status: str, error_code: str, **evidence) -> dict:
    return {**base, **counters, "canary_status": status, "error_code": error_code, **evidence}


def run_adp_continue_diagnostic_canary(
    request: AdpContinueDiagnosticCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    profile, profile_version = load_identity_profile(request.profile_manifest_path)
    base = _base_report(request, profile_version)
    counters = {k: v for k, v in base.items() if k.endswith("_attempts") or k.endswith("_successes")}
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("PLAYWRIGHT_UNAVAILABLE") from exc

    cfg = config or BrowserRuntimeConfig(navigation_timeout_ms=request.timeout_ms, settle_timeout_ms=2_000)
    executable = cfg.resolved_executable_path()
    if not executable and not cfg.use_playwright_managed:
        raise RuntimeError("CHROMIUM_NOT_FOUND")

    with sync_playwright() as p:
        launch = {"headless": cfg.headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        if executable:
            launch["executable_path"] = executable
        browser = p.chromium.launch(**launch)
        context = None
        try:
            context = browser.new_context(ignore_https_errors=cfg.ignore_https_errors, accept_downloads=False)
            page = context.new_page()
            page.set_default_timeout(request.timeout_ms)
            page.set_default_navigation_timeout(request.timeout_ms)
            page.goto(request.application_url, wait_until="domcontentloaded", timeout=request.timeout_ms)

            pre = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if pre.get("runtime_state") != "application_entry_observed":
                return _result(base, counters, "blocked", "ADP_DIAG_REQUIRES_APPLICATION_ENTRY", pre_navigation=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_DIAG_PREFLIGHT_BOUNDARY_OBSERVED", pre_navigation=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_DIAG_NAVIGATION_SURFACE_MISMATCH", pre_navigation=pre)

            try:
                preferences = _unique_actionable_button(page, PREFERENCES_LABEL)
                counters["preference_navigation_click_attempts"] = 1
                preferences.click(timeout=request.timeout_ms)
                counters["preference_navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_DIAG_COOKIE_PREFS_CLICK_FAILED:{type(exc).__name__}")
            page.wait_for_timeout(500)
            pref = preference_surface_descriptor(page)
            pref_fp = preference_surface_fingerprint(pref)
            if pref.get("preference_center_visible") is not True or pref_fp != request.expected_preference_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_DIAG_PREFERENCE_SURFACE_MISMATCH", preference_surface=pref, observed_preference_surface_fingerprint=pref_fp)
            if pref.get("visible_checkboxes") != []:
                return _result(base, counters, "blocked", "ADP_DIAG_VISIBLE_CHECKBOX_CONTRACT_DRIFT", preference_surface=pref)

            try:
                unselect_all = _unique_actionable_button(page, UNSELECT_ALL_LABEL)
                save_changes = _unique_actionable_button(page, SAVE_CHANGES_LABEL)
                counters["cookie_unselect_all_click_attempts"] = 1
                counters["cookie_toggle_write_attempts"] = 1
                unselect_all.click(timeout=request.timeout_ms)
                counters["cookie_unselect_all_click_successes"] = 1
                page.wait_for_timeout(250)
                save_changes = _unique_actionable_button(page, SAVE_CHANGES_LABEL)
                counters["cookie_save_click_attempts"] = 1
                save_changes.click(timeout=request.timeout_ms)
                counters["cookie_save_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_DIAG_COOKIE_POLICY_CLICK_FAILED:{type(exc).__name__}")
            page.wait_for_timeout(500)

            after_cookie = preference_surface_descriptor(page)
            if after_cookie.get("preference_center_visible") is True or after_cookie.get("banner_visible") is True:
                return _result(base, counters, "blocked", "ADP_DIAG_COOKIE_BOUNDARY_PERSISTED", after_cookie_surface=after_cookie)

            post_cookie = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if navigation_surface_fingerprint(post_cookie) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_DIAG_POST_COOKIE_NAVIGATION_SURFACE_MISMATCH", post_cookie_navigation=post_cookie)
            try:
                approved = _approved_entry(post_cookie, request.entry_ordinal, "Apply")
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
                counters["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                counters["navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_DIAG_APPLY_CLICK_FAILED:{type(exc).__name__}", post_cookie_navigation=post_cookie)
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_DIAG_APPLY_OPENED_NEW_PAGE")

            form = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if form.get("captcha_observed") is True or form.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_DIAG_POST_APPLY_BOUNDARY_OBSERVED", pre_fill=form)
            actual_fill_fp = safe_fill_surface_fingerprint(form)
            if actual_fill_fp != request.expected_safe_fill_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_DIAG_SAFE_FILL_SURFACE_MISMATCH", pre_fill=form, observed_safe_fill_surface_fingerprint=actual_fill_fp)

            field_results = []
            for spec in TARGETS:
                canonical, element_id, _label, _ctype, _required = spec
                _validate_target_control(form, spec)
                locator = page.locator(f"#{element_id}")
                desired = profile[canonical]
                before = locator.input_value()
                executed = before != desired
                if executed:
                    counters["form_value_write_attempts"] += 1
                    locator.fill(desired, timeout=request.timeout_ms)
                readback = locator.input_value()
                valid = bool(locator.evaluate("el => el.checkValidity ? el.checkValidity() : true"))
                matched = readback == desired and valid
                field_results.append({
                    "canonical_field": canonical,
                    "control_id": element_id,
                    "value_hash": value_hash(desired),
                    "readback_hash": value_hash(readback),
                    "write_executed": executed,
                    "readback_match": matched,
                    "valid": valid,
                })
                if not matched:
                    return _result(base, counters, "blocked", f"ADP_DIAG_READBACK_MISMATCH:{canonical}", field_results=field_results)

            post_fill = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if safe_fill_surface_fingerprint(post_fill) != actual_fill_fp:
                return _result(base, counters, "blocked", "ADP_DIAG_POST_WRITE_SURFACE_DRIFT", post_fill=post_fill, field_results=field_results)
            action_surface = action_surface_descriptor(page)
            action_fp = action_surface_fingerprint(action_surface)
            if action_fp != request.expected_post_fill_action_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_DIAG_POST_FILL_ACTION_SURFACE_MISMATCH", post_fill_action_surface=action_surface, observed_post_fill_action_surface_fingerprint=action_fp)

            try:
                continue_button = _unique_actionable_button(page, CONTINUE_LABEL)
                counters["second_action_click_attempts"] = 1
                continue_button.click(timeout=request.timeout_ms)
                counters["second_action_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_DIAG_CONTINUE_CLICK_FAILED:{type(exc).__name__}", post_fill_action_surface=action_surface)

            page.wait_for_timeout(3_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_DIAG_CONTINUE_OPENED_NEW_PAGE")
            post_continue = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            diagnostics = post_continue_diagnostics(page, profile, post_continue, request.expected_safe_fill_surface_fingerprint)
            return _result(
                base,
                counters,
                "diagnosed",
                "",
                field_results=field_results,
                observed_preference_surface_fingerprint=pref_fp,
                observed_safe_fill_surface_fingerprint=actual_fill_fp,
                observed_post_fill_action_surface_fingerprint=action_fp,
                post_continue=post_continue,
                post_continue_diagnostics=diagnostics,
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP post-Continue diagnostic canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--expected-preference-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--expected-post-fill-action-surface-fingerprint", required=True)
    parser.add_argument("--profile-manifest", required=True)
    parser.add_argument("--output", default="adp-continue-diagnostic-canary.json")
    args = parser.parse_args()
    report = run_adp_continue_diagnostic_canary(AdpContinueDiagnosticCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        expected_preference_surface_fingerprint=args.expected_preference_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
        expected_post_fill_action_surface_fingerprint=args.expected_post_fill_action_surface_fingerprint,
        profile_manifest_path=args.profile_manifest,
    ))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "diagnosis": (report.get("post_continue_diagnostics") or {}).get("diagnosis", ""),
        "second_action_click_attempts": report.get("second_action_click_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "diagnosed" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_CONTINUE_DIAGNOSTIC_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
