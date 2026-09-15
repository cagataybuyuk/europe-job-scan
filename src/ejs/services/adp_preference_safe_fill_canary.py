"""Evidence-backed ADP safe-fill canary for the reviewed OneTrust preference flow.

Authority is limited to the exact live-observed sequence:
1. open the OneTrust preference center;
2. click the unique visible ``Unselect All`` control;
3. click the unique visible ``Save Changes`` control;
4. re-verify the reviewed ADP application-entry surface;
5. click one reviewed Apply action;
6. write at most three AUTO_SAFE identity fields with exact readback.

The canary never accepts optional cookies, directly manipulates a checkbox,
enters credentials, changes phone/country, uploads a file, clicks another
application action, bypasses a challenge, or submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys

from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, SafeFieldWriterAuthority, value_hash
from ejs.services.adp_cookie_preferences_canary import (
    PREFERENCES_LABEL,
    preference_surface_descriptor,
    preference_surface_fingerprint,
)
from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _approved_entry,
    _normalize,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.adp_safe_fill_canary import (
    TARGETS,
    _normalize_label,
    _validate_target_control,
    _visible_button_labels,
    load_identity_profile,
    safe_fill_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-preference-safe-fill-canary-v1"
COOKIE_POLICY = "preferences_unselect_all_save"
UNSELECT_ALL_LABEL = "Unselect All"
SAVE_CHANGES_LABEL = "Save Changes"


@dataclass(frozen=True)
class AdpPreferenceSafeFillCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    expected_preference_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    profile_manifest_path: str
    expected_label: str = "Apply"
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpPreferenceSafeFillCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value, code in (
        (request.expected_navigation_surface_fingerprint, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"),
        (request.expected_preference_surface_fingerprint, "INVALID_EXPECTED_PREFERENCE_SURFACE_FINGERPRINT"),
        (request.expected_safe_fill_surface_fingerprint, "INVALID_EXPECTED_SAFE_FILL_SURFACE_FINGERPRINT"),
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError(code)
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if _normalize(request.expected_label) not in {"apply", "apply now"}:
        raise ValueError("ADP_PREFERENCE_SAFE_FILL_REQUIRES_APPLY_LABEL")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
    if not request.profile_manifest_path:
        raise ValueError("ADP_PREFERENCE_SAFE_FILL_REQUIRES_PROFILE_MANIFEST")
    SafeFieldWriterAuthority().validate()
    if not {item[0] for item in TARGETS}.issubset(SAFE_VERIFIED_FIELDS):
        raise PermissionError("ADP_PREFERENCE_SAFE_FILL_TARGET_NOT_AUTO_SAFE")


def _base_report(request: AdpPreferenceSafeFillCanaryRequest, profile_version: str) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "preference_safe_fill_canary_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_preference_surface_fingerprint": request.expected_preference_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
        "candidate_profile_version": profile_version,
        "cookie_policy": COOKIE_POLICY,
        "preference_navigation_click_attempts": 0,
        "preference_navigation_click_successes": 0,
        "cookie_unselect_all_click_attempts": 0,
        "cookie_unselect_all_click_successes": 0,
        "cookie_toggle_write_attempts": 0,
        "cookie_save_click_attempts": 0,
        "cookie_save_click_successes": 0,
        "optional_cookie_accept_attempts": 0,
        "navigation_click_attempts": 0,
        "navigation_click_successes": 0,
        "credential_entry_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "second_action_click_attempts": 0,
        "submit_attempts": 0,
        "further_navigation_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _result(base: dict, counters: dict, status: str, error_code: str, **evidence) -> dict:
    return {
        **base,
        **counters,
        "canary_status": status,
        "error_code": error_code,
        **evidence,
    }


def _unique_actionable_button(page, label: str):
    locator = page.get_by_role("button", name=label, exact=True)
    if locator.count() != 1:
        raise PermissionError(f"ADP_COOKIE_BUTTON_NOT_UNIQUE:{label}")
    if not locator.is_visible() or not locator.is_enabled():
        raise PermissionError(f"ADP_COOKIE_BUTTON_NOT_ACTIONABLE:{label}")
    actual = locator.get_attribute("aria-label") or locator.inner_text() or ""
    if _normalize_label(actual) != _normalize_label(label):
        raise PermissionError(f"ADP_COOKIE_BUTTON_LABEL_DRIFT:{label}")
    return locator


def run_adp_preference_safe_fill_canary(
    request: AdpPreferenceSafeFillCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    profile, profile_version = load_identity_profile(request.profile_manifest_path)
    base = _base_report(request, profile_version)
    counters = {
        key: value for key, value in base.items()
        if key.endswith("_attempts") or key.endswith("_successes")
    }
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("PLAYWRIGHT_UNAVAILABLE") from exc

    cfg = config or BrowserRuntimeConfig(
        navigation_timeout_ms=request.timeout_ms,
        settle_timeout_ms=2_000,
    )
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

            pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if pre.get("runtime_state") != "application_entry_observed":
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_REQUIRES_APPLICATION_ENTRY", pre_navigation=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_BOUNDARY_OBSERVED", pre_navigation=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_NAVIGATION_SURFACE_MISMATCH", pre_navigation=pre)

            try:
                preferences = _unique_actionable_button(page, PREFERENCES_LABEL)
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), pre_navigation=pre)
            try:
                counters["preference_navigation_click_attempts"] = 1
                preferences.click(timeout=request.timeout_ms)
                counters["preference_navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_COOKIE_PREFERENCES_CLICK_FAILED:{type(exc).__name__}", pre_navigation=pre)
            page.wait_for_timeout(500)

            preference_surface = preference_surface_descriptor(page)
            preference_fp = preference_surface_fingerprint(preference_surface)
            if preference_surface.get("preference_center_visible") is not True:
                return _result(
                    base, counters, "blocked", "ADP_COOKIE_PREFERENCE_CENTER_NOT_VISIBLE",
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            if preference_fp != request.expected_preference_surface_fingerprint:
                return _result(
                    base, counters, "blocked", "ADP_COOKIE_PREFERENCE_SURFACE_MISMATCH",
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            if preference_surface.get("visible_checkboxes") != []:
                return _result(
                    base, counters, "blocked", "ADP_COOKIE_VISIBLE_CHECKBOX_CONTRACT_DRIFT",
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )

            try:
                unselect_all = _unique_actionable_button(page, UNSELECT_ALL_LABEL)
                save_changes = _unique_actionable_button(page, SAVE_CHANGES_LABEL)
            except PermissionError as exc:
                return _result(
                    base, counters, "blocked", str(exc),
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )

            try:
                counters["cookie_unselect_all_click_attempts"] = 1
                counters["cookie_toggle_write_attempts"] = 1
                unselect_all.click(timeout=request.timeout_ms)
                counters["cookie_unselect_all_click_successes"] = 1
            except Exception as exc:
                return _result(
                    base, counters, "blocked", f"ADP_COOKIE_UNSELECT_ALL_CLICK_FAILED:{type(exc).__name__}",
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            page.wait_for_timeout(250)

            try:
                save_changes = _unique_actionable_button(page, SAVE_CHANGES_LABEL)
                counters["cookie_save_click_attempts"] = 1
                save_changes.click(timeout=request.timeout_ms)
                counters["cookie_save_click_successes"] = 1
            except PermissionError as exc:
                return _result(
                    base, counters, "blocked", str(exc),
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            except Exception as exc:
                return _result(
                    base, counters, "blocked", f"ADP_COOKIE_SAVE_CHANGES_CLICK_FAILED:{type(exc).__name__}",
                    pre_navigation=pre, preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            page.wait_for_timeout(500)

            after_cookie_surface = preference_surface_descriptor(page)
            if after_cookie_surface.get("preference_center_visible") is True or after_cookie_surface.get("banner_visible") is True:
                return _result(
                    base, counters, "blocked", "ADP_COOKIE_BOUNDARY_PERSISTED_AFTER_SAVE",
                    pre_navigation=pre, preference_surface=preference_surface,
                    after_cookie_surface=after_cookie_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )

            post_cookie_pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if post_cookie_pre.get("captcha_observed") is True or post_cookie_pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_POST_COOKIE_BOUNDARY_OBSERVED", post_cookie_navigation=post_cookie_pre)
            if navigation_surface_fingerprint(post_cookie_pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_POST_COOKIE_NAVIGATION_SURFACE_MISMATCH", post_cookie_navigation=post_cookie_pre)

            try:
                approved = _approved_entry(post_cookie_pre, request.entry_ordinal, request.expected_label)
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), post_cookie_navigation=post_cookie_pre)
            if not entry.is_visible() or not entry.is_enabled():
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_ENTRY_NOT_ACTIONABLE", post_cookie_navigation=post_cookie_pre)
            actual_label = entry.get_attribute("aria-label") or entry.inner_text() or ""
            if _normalize(actual_label) != _normalize(request.expected_label):
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_ENTRY_LABEL_DRIFT", post_cookie_navigation=post_cookie_pre)
            try:
                counters["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                counters["navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PREF_SAFE_FILL_ENTRY_CLICK_FAILED:{type(exc).__name__}", post_cookie_navigation=post_cookie_pre)
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_NAVIGATION_OPENED_NEW_PAGE", post_cookie_navigation=post_cookie_pre)

            form = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if form.get("captcha_observed") is True or form.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_POST_NAV_BOUNDARY_OBSERVED", pre_fill=form)
            if form.get("runtime_state") != "inspected":
                return _result(base, counters, "blocked", "ADP_PREF_SAFE_FILL_FORM_NOT_INSPECTED", pre_fill=form)
            actual_surface = safe_fill_surface_fingerprint(form)
            if actual_surface != request.expected_safe_fill_surface_fingerprint:
                return _result(
                    base, counters, "blocked", "ADP_PREF_SAFE_FILL_SURFACE_MISMATCH",
                    pre_fill=form, observed_safe_fill_surface_fingerprint=actual_surface,
                )

            targets = []
            try:
                for spec in TARGETS:
                    targets.append((spec, _validate_target_control(form, spec)))
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), pre_fill=form)

            field_results = []
            for spec, _control in targets:
                canonical, element_id, _label, _ctype, _required = spec
                locator = page.locator(f"#{element_id}")
                if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
                    return _result(base, counters, "blocked", f"ADP_PREF_SAFE_FILL_RUNTIME_LOCATOR_DRIFT:{canonical}", pre_fill=form, field_results=field_results)
                desired = profile[canonical]
                before = locator.input_value()
                executed = before != desired
                if executed:
                    try:
                        counters["form_value_write_attempts"] += 1
                        locator.fill(desired, timeout=request.timeout_ms)
                    except Exception as exc:
                        return _result(base, counters, "blocked", f"ADP_PREF_SAFE_FILL_WRITE_FAILED:{canonical}:{type(exc).__name__}", pre_fill=form, field_results=field_results)
                readback = locator.input_value()
                valid = locator.evaluate("el => el.checkValidity ? el.checkValidity() : true")
                matched = readback == desired and bool(valid)
                field_results.append({
                    "canonical_field": canonical,
                    "control_id": element_id,
                    "value_hash": value_hash(desired),
                    "readback_hash": value_hash(readback),
                    "write_executed": executed,
                    "readback_match": matched,
                    "valid": bool(valid),
                })
                if not matched:
                    return _result(base, counters, "blocked", f"ADP_PREF_SAFE_FILL_READBACK_MISMATCH:{canonical}", pre_fill=form, field_results=field_results)

            post = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if safe_fill_surface_fingerprint(post) != actual_surface:
                return _result(
                    base, counters, "blocked", "ADP_PREF_SAFE_FILL_POST_WRITE_SURFACE_DRIFT",
                    pre_fill=form, post_fill=post, field_results=field_results,
                )
            return _result(
                base,
                counters,
                "filled_verified",
                "",
                pre_navigation=pre,
                preference_surface=preference_surface,
                observed_preference_surface_fingerprint=preference_fp,
                after_cookie_surface=after_cookie_surface,
                post_cookie_navigation=post_cookie_pre,
                resolved_entry_observation_key=observation_key,
                pre_fill=form,
                post_fill=post,
                observed_safe_fill_surface_fingerprint=actual_surface,
                field_results=field_results,
                visible_button_accessibility=_visible_button_labels(page),
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP reviewed OneTrust preference + identity safe-fill canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--expected-preference-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--profile-manifest", required=True)
    parser.add_argument("--output", default="adp-preference-safe-fill-canary.json")
    args = parser.parse_args()
    report = run_adp_preference_safe_fill_canary(AdpPreferenceSafeFillCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        expected_preference_surface_fingerprint=args.expected_preference_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
        profile_manifest_path=args.profile_manifest,
    ))
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "preference_navigation_click_attempts": report.get("preference_navigation_click_attempts", 0),
        "cookie_unselect_all_click_attempts": report.get("cookie_unselect_all_click_attempts", 0),
        "cookie_save_click_attempts": report.get("cookie_save_click_attempts", 0),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "filled_verified" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_PREFERENCE_SAFE_FILL_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
