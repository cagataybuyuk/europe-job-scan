"""Evidence-backed ADP continuation canary after reviewed identity safe-fill.

Authority is limited to:
1. open OneTrust preferences;
2. click Unselect All;
3. click Save Changes;
4. click one reviewed Apply action;
5. write only first name, last name, and email with exact readback;
6. verify the reviewed post-fill visible-button surface;
7. click exactly one reviewed Continue action;
8. inspect the resulting page read-only and route it fail-closed.

It never touches phone/country, credentials, social sign-in, file upload, another
application action, CAPTCHA handling, or final Submit.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import sys

from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, SafeFieldWriterAuthority, value_hash
from ejs.services.adp_cookie_preferences_canary import (
    PREFERENCES_LABEL,
    preference_surface_descriptor,
    preference_surface_fingerprint,
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
    _normalize_label,
    _validate_target_control,
    load_identity_profile,
    safe_fill_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-continue-canary-v1"
CONTINUE_LABEL = "Continue"


@dataclass(frozen=True)
class AdpContinueCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    expected_preference_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    expected_post_fill_action_surface_fingerprint: str
    profile_manifest_path: str
    expected_apply_label: str = "Apply"
    expected_continue_label: str = CONTINUE_LABEL
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def _normalize_action_label(value: str) -> str:
    return " ".join((value or "").lower().split())


def validate_request(request: AdpContinueCanaryRequest) -> None:
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
    if _normalize(request.expected_apply_label) not in {"apply", "apply now"}:
        raise ValueError("ADP_CONTINUE_REQUIRES_APPLY_LABEL")
    if _normalize_action_label(request.expected_continue_label) != "continue":
        raise ValueError("ADP_CONTINUE_REQUIRES_CONTINUE_LABEL")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
    if not request.profile_manifest_path:
        raise ValueError("ADP_CONTINUE_REQUIRES_PROFILE_MANIFEST")
    SafeFieldWriterAuthority().validate()
    if not {item[0] for item in TARGETS}.issubset(SAFE_VERIFIED_FIELDS):
        raise PermissionError("ADP_CONTINUE_TARGET_NOT_AUTO_SAFE")


def action_surface_descriptor(page) -> dict:
    rows: list[dict] = []
    buttons = page.get_by_role("button")
    for index in range(min(buttons.count(), 30)):
        locator = buttons.nth(index)
        try:
            if not locator.is_visible():
                continue
            label = locator.get_attribute("aria-label") or locator.inner_text() or ""
            label = _normalize_action_label(label)
            if not label:
                continue
            rows.append({"label": label[:160], "enabled": locator.is_enabled()})
        except Exception:
            continue
    rows.sort(key=lambda item: (item["label"], item["enabled"]))
    return {"visible_buttons": rows}


def action_surface_fingerprint(descriptor: dict) -> str:
    payload = json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _route_after_continue(snapshot: dict) -> dict:
    if snapshot.get("captcha_observed") is True or snapshot.get("auth_observed") is True:
        return {
            "route": "human_handoff",
            "reason_code": "POST_CONTINUE_AUTH_OR_CAPTCHA_BOUNDARY",
            "manifest_review_allowed": False,
            "automation_resume_allowed": False,
            "safe_fill_allowed": False,
            "file_upload_allowed": False,
            "final_submit_allowed": False,
        }
    controls = visible_application_controls(snapshot.get("form", {}))
    if controls:
        return {
            "route": "manifest_review_candidate",
            "reason_code": "VISIBLE_APPLICATION_CONTROLS_OBSERVED",
            "manifest_review_allowed": True,
            "automation_resume_allowed": False,
            "safe_fill_allowed": False,
            "file_upload_allowed": False,
            "final_submit_allowed": False,
        }
    return {
        "route": "diagnostic_review",
        "reason_code": "POST_CONTINUE_STRUCTURE_REVIEW_REQUIRED",
        "manifest_review_allowed": False,
        "automation_resume_allowed": False,
        "safe_fill_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _base_report(request: AdpContinueCanaryRequest, profile_version: str) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "continuation_canary_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_preference_surface_fingerprint": request.expected_preference_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "expected_post_fill_action_surface_fingerprint": request.expected_post_fill_action_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
        "candidate_profile_version": profile_version,
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
        "second_action_click_attempts": 0,
        "second_action_click_successes": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "further_navigation_allowed": False,
        "safe_fill_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _result(base: dict, counters: dict, status: str, error_code: str, **evidence) -> dict:
    return {**base, **counters, "canary_status": status, "error_code": error_code, **evidence}


def run_adp_continue_canary(
    request: AdpContinueCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    profile, profile_version = load_identity_profile(request.profile_manifest_path)
    base = _base_report(request, profile_version)
    counters = {
        key: value
        for key, value in base.items()
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
                return _result(base, counters, "blocked", "ADP_CONTINUE_REQUIRES_APPLICATION_ENTRY", pre_navigation=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_CONTINUE_PREFLIGHT_BOUNDARY_OBSERVED", pre_navigation=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_CONTINUE_NAVIGATION_SURFACE_MISMATCH", pre_navigation=pre)

            try:
                preferences = _unique_actionable_button(page, PREFERENCES_LABEL)
                counters["preference_navigation_click_attempts"] = 1
                preferences.click(timeout=request.timeout_ms)
                counters["preference_navigation_click_successes"] = 1
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), pre_navigation=pre)
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_CONTINUE_COOKIE_PREFS_CLICK_FAILED:{type(exc).__name__}", pre_navigation=pre)
            page.wait_for_timeout(500)

            preference_surface = preference_surface_descriptor(page)
            preference_fp = preference_surface_fingerprint(preference_surface)
            if preference_surface.get("preference_center_visible") is not True:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_PREFERENCE_CENTER_NOT_VISIBLE",
                    preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            if preference_fp != request.expected_preference_surface_fingerprint:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_PREFERENCE_SURFACE_MISMATCH",
                    preference_surface=preference_surface,
                    observed_preference_surface_fingerprint=preference_fp,
                )
            if preference_surface.get("visible_checkboxes") != []:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_VISIBLE_CHECKBOX_CONTRACT_DRIFT",
                    preference_surface=preference_surface,
                )

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
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), preference_surface=preference_surface)
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_CONTINUE_COOKIE_POLICY_CLICK_FAILED:{type(exc).__name__}", preference_surface=preference_surface)
            page.wait_for_timeout(500)

            after_cookie = preference_surface_descriptor(page)
            if after_cookie.get("preference_center_visible") is True or after_cookie.get("banner_visible") is True:
                return _result(base, counters, "blocked", "ADP_CONTINUE_COOKIE_BOUNDARY_PERSISTED", after_cookie_surface=after_cookie)

            post_cookie_pre = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if post_cookie_pre.get("captcha_observed") is True or post_cookie_pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_CONTINUE_POST_COOKIE_BOUNDARY_OBSERVED", post_cookie_navigation=post_cookie_pre)
            if navigation_surface_fingerprint(post_cookie_pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_CONTINUE_POST_COOKIE_NAVIGATION_SURFACE_MISMATCH", post_cookie_navigation=post_cookie_pre)

            try:
                approved = _approved_entry(post_cookie_pre, request.entry_ordinal, request.expected_apply_label)
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), post_cookie_navigation=post_cookie_pre)
            if not entry.is_visible() or not entry.is_enabled():
                return _result(base, counters, "blocked", "ADP_CONTINUE_APPLY_NOT_ACTIONABLE", post_cookie_navigation=post_cookie_pre)
            actual_apply = entry.get_attribute("aria-label") or entry.inner_text() or ""
            if _normalize(actual_apply) != _normalize(request.expected_apply_label):
                return _result(base, counters, "blocked", "ADP_CONTINUE_APPLY_LABEL_DRIFT", post_cookie_navigation=post_cookie_pre)
            try:
                counters["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                counters["navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_CONTINUE_APPLY_CLICK_FAILED:{type(exc).__name__}", post_cookie_navigation=post_cookie_pre)
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_CONTINUE_APPLY_OPENED_NEW_PAGE", post_cookie_navigation=post_cookie_pre)

            form = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if form.get("captcha_observed") is True or form.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_CONTINUE_POST_APPLY_BOUNDARY_OBSERVED", pre_fill=form)
            if form.get("runtime_state") != "inspected":
                return _result(base, counters, "blocked", "ADP_CONTINUE_FORM_NOT_INSPECTED", pre_fill=form)
            actual_fill_fp = safe_fill_surface_fingerprint(form)
            if actual_fill_fp != request.expected_safe_fill_surface_fingerprint:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_SAFE_FILL_SURFACE_MISMATCH",
                    pre_fill=form, observed_safe_fill_surface_fingerprint=actual_fill_fp,
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
                    return _result(
                        base, counters, "blocked", f"ADP_CONTINUE_RUNTIME_LOCATOR_DRIFT:{canonical}",
                        pre_fill=form, field_results=field_results,
                    )
                desired = profile[canonical]
                before = locator.input_value()
                executed = before != desired
                if executed:
                    try:
                        counters["form_value_write_attempts"] += 1
                        locator.fill(desired, timeout=request.timeout_ms)
                    except Exception as exc:
                        return _result(
                            base, counters, "blocked", f"ADP_CONTINUE_WRITE_FAILED:{canonical}:{type(exc).__name__}",
                            pre_fill=form, field_results=field_results,
                        )
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
                    return _result(
                        base, counters, "blocked", f"ADP_CONTINUE_READBACK_MISMATCH:{canonical}",
                        pre_fill=form, field_results=field_results,
                    )

            post_fill = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if safe_fill_surface_fingerprint(post_fill) != actual_fill_fp:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_POST_WRITE_SURFACE_DRIFT",
                    pre_fill=form, post_fill=post_fill, field_results=field_results,
                )

            action_surface = action_surface_descriptor(page)
            action_fp = action_surface_fingerprint(action_surface)
            if action_fp != request.expected_post_fill_action_surface_fingerprint:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_ACTION_SURFACE_MISMATCH",
                    post_fill=post_fill,
                    field_results=field_results,
                    post_fill_action_surface=action_surface,
                    observed_post_fill_action_surface_fingerprint=action_fp,
                )

            try:
                continue_button = _unique_actionable_button(page, request.expected_continue_label)
            except PermissionError as exc:
                return _result(
                    base, counters, "blocked", str(exc),
                    post_fill=post_fill,
                    field_results=field_results,
                    post_fill_action_surface=action_surface,
                    observed_post_fill_action_surface_fingerprint=action_fp,
                )
            actual_continue = continue_button.get_attribute("aria-label") or continue_button.inner_text() or ""
            if _normalize_action_label(actual_continue) != "continue":
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_LABEL_DRIFT",
                    post_fill_action_surface=action_surface,
                    observed_post_fill_action_surface_fingerprint=action_fp,
                )

            try:
                counters["second_action_click_attempts"] = 1
                continue_button.click(timeout=request.timeout_ms)
                counters["second_action_click_successes"] = 1
            except Exception as exc:
                return _result(
                    base, counters, "blocked", f"ADP_CONTINUE_SECOND_ACTION_CLICK_FAILED:{type(exc).__name__}",
                    post_fill_action_surface=action_surface,
                    observed_post_fill_action_surface_fingerprint=action_fp,
                )
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(
                    base, counters, "blocked", "ADP_CONTINUE_SECOND_ACTION_OPENED_NEW_PAGE",
                    post_fill_action_surface=action_surface,
                    observed_post_fill_action_surface_fingerprint=action_fp,
                )

            post_continue = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            route = _route_after_continue(post_continue)
            return _result(
                base,
                counters,
                "continued_inspected",
                "",
                pre_navigation=pre,
                preference_surface=preference_surface,
                observed_preference_surface_fingerprint=preference_fp,
                after_cookie_surface=after_cookie,
                post_cookie_navigation=post_cookie_pre,
                resolved_entry_observation_key=observation_key,
                pre_fill=form,
                post_fill=post_fill,
                observed_safe_fill_surface_fingerprint=actual_fill_fp,
                field_results=field_results,
                post_fill_action_surface=action_surface,
                observed_post_fill_action_surface_fingerprint=action_fp,
                post_continue=post_continue,
                next_route=route,
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP reviewed identity continuation canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--expected-preference-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--expected-post-fill-action-surface-fingerprint", required=True)
    parser.add_argument("--profile-manifest", required=True)
    parser.add_argument("--output", default="adp-continue-canary.json")
    args = parser.parse_args()
    report = run_adp_continue_canary(AdpContinueCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        expected_preference_surface_fingerprint=args.expected_preference_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
        expected_post_fill_action_surface_fingerprint=args.expected_post_fill_action_surface_fingerprint,
        profile_manifest_path=args.profile_manifest,
    ))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "preference_navigation_click_attempts": report.get("preference_navigation_click_attempts", 0),
        "cookie_unselect_all_click_attempts": report.get("cookie_unselect_all_click_attempts", 0),
        "cookie_save_click_attempts": report.get("cookie_save_click_attempts", 0),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "second_action_click_attempts": report.get("second_action_click_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
        "next_route": (report.get("next_route") or {}).get("route", ""),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "continued_inspected" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_CONTINUE_CANARY_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
