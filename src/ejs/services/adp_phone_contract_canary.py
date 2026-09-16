"""Read-only ADP phone-control contract inspector after reviewed entry navigation.

Authority is limited to the already reviewed cookie-preference sequence and one
reviewed Apply click. After the identity surface opens, this canary performs
read-only inspection only. It never writes profile values, clicks Continue,
uploads files, enters credentials, or submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys

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
from ejs.services.adp_preference_safe_fill_canary import (
    SAVE_CHANGES_LABEL,
    UNSELECT_ALL_LABEL,
    _unique_actionable_button,
)
from ejs.services.adp_safe_fill_canary import safe_fill_surface_fingerprint
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-phone-contract-canary-v1"
PHONE_COUNTRY_NAME = "phoneCountry"
PHONE_INPUT_ID = "login_view_phone"
PHONE_INSTRUCTION_ID = "mobileNumberInstruction"


@dataclass(frozen=True)
class AdpPhoneContractCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    expected_preference_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    expected_apply_label: str = "Apply"
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpPhoneContractCanaryRequest) -> None:
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
    if _normalize(request.expected_apply_label) not in {"apply", "apply now"}:
        raise ValueError("ADP_PHONE_CONTRACT_REQUIRES_APPLY_LABEL")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")


def _base_report(request: AdpPhoneContractCanaryRequest) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "phone_contract_inspection_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_preference_surface_fingerprint": request.expected_preference_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
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
        "form_value_write_attempts": 0,
        "second_action_click_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "phone_write_allowed": False,
        "continue_click_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _result(base: dict, counters: dict, status: str, error_code: str, **evidence) -> dict:
    return {**base, **counters, "canary_status": status, "error_code": error_code, **evidence}


def _attr(locator, name: str) -> str:
    try:
        return str(locator.get_attribute(name) or "")[:240]
    except Exception:
        return ""


def phone_contract_descriptor(page) -> dict:
    country = page.locator(f"select[name='{PHONE_COUNTRY_NAME}']")
    phone = page.locator(f"#{PHONE_INPUT_ID}")
    instruction = page.locator(f"#{PHONE_INSTRUCTION_ID}")

    if country.count() != 1:
        raise PermissionError("ADP_PHONE_COUNTRY_CONTROL_NOT_UNIQUE")
    if phone.count() != 1:
        raise PermissionError("ADP_PHONE_INPUT_NOT_UNIQUE")
    if not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_PHONE_COUNTRY_CONTROL_NOT_ACTIONABLE")
    if not phone.is_visible() or not phone.is_enabled():
        raise PermissionError("ADP_PHONE_INPUT_NOT_ACTIONABLE")

    options = country.locator("option")
    option_rows = []
    for index in range(min(options.count(), 300)):
        item = options.nth(index)
        label = " ".join((item.inner_text() or "").split())[:160]
        option_rows.append({
            "ordinal": index,
            "label": label,
            "value": _attr(item, "value"),
            "selected": item.is_checked() if _attr(item, "selected") else False,
            "disabled": item.is_disabled(),
        })

    try:
        selected_value = str(country.input_value() or "")[:160]
    except Exception:
        selected_value = ""
    selected_label = ""
    for row in option_rows:
        if row["value"] == selected_value:
            selected_label = row["label"]
            row["selected"] = True

    instruction_text = ""
    try:
        if instruction.count() == 1 and instruction.is_visible():
            instruction_text = " ".join((instruction.inner_text() or "").split())[:400]
    except Exception:
        pass

    return {
        "country_control": {
            "tag": "select",
            "name": PHONE_COUNTRY_NAME,
            "required": country.get_attribute("required") is not None,
            "aria_required": _attr(country, "aria-required"),
            "selected_value": selected_value,
            "selected_label": selected_label,
            "option_count": options.count(),
            "options": option_rows,
        },
        "phone_control": {
            "tag": "input",
            "id": PHONE_INPUT_ID,
            "name": _attr(phone, "name"),
            "type": _attr(phone, "type"),
            "required": phone.get_attribute("required") is not None,
            "aria_required": _attr(phone, "aria-required"),
            "pattern": _attr(phone, "pattern"),
            "inputmode": _attr(phone, "inputmode"),
            "minlength": _attr(phone, "minlength"),
            "maxlength": _attr(phone, "maxlength"),
            "placeholder": _attr(phone, "placeholder"),
            "autocomplete": _attr(phone, "autocomplete"),
            "aria_describedby": _attr(phone, "aria-describedby"),
        },
        "instruction_text": instruction_text,
        "runtime_required_observed_in_prior_live_diagnostic": True,
    }


def run_adp_phone_contract_canary(
    request: AdpPhoneContractCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    base = _base_report(request)
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
                return _result(base, counters, "blocked", "ADP_PHONE_REQUIRES_APPLICATION_ENTRY", pre_navigation=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PHONE_PREFLIGHT_BOUNDARY_OBSERVED", pre_navigation=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PHONE_NAVIGATION_SURFACE_MISMATCH", pre_navigation=pre)

            try:
                preferences = _unique_actionable_button(page, PREFERENCES_LABEL)
                counters["preference_navigation_click_attempts"] = 1
                preferences.click(timeout=request.timeout_ms)
                counters["preference_navigation_click_successes"] = 1
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), pre_navigation=pre)
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PHONE_PREFS_CLICK_FAILED:{type(exc).__name__}", pre_navigation=pre)
            page.wait_for_timeout(500)

            pref = preference_surface_descriptor(page)
            pref_fp = preference_surface_fingerprint(pref)
            if pref.get("preference_center_visible") is not True:
                return _result(base, counters, "blocked", "ADP_PHONE_PREFERENCE_CENTER_NOT_VISIBLE", preference_surface=pref)
            if pref_fp != request.expected_preference_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PHONE_PREFERENCE_SURFACE_MISMATCH", preference_surface=pref, observed_preference_surface_fingerprint=pref_fp)
            if pref.get("visible_checkboxes") != []:
                return _result(base, counters, "blocked", "ADP_PHONE_VISIBLE_CHECKBOX_CONTRACT_DRIFT", preference_surface=pref)

            try:
                unselect_all = _unique_actionable_button(page, UNSELECT_ALL_LABEL)
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
                return _result(base, counters, "blocked", str(exc), preference_surface=pref)
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PHONE_COOKIE_POLICY_CLICK_FAILED:{type(exc).__name__}", preference_surface=pref)
            page.wait_for_timeout(500)

            after_cookie = preference_surface_descriptor(page)
            if after_cookie.get("preference_center_visible") is True or after_cookie.get("banner_visible") is True:
                return _result(base, counters, "blocked", "ADP_PHONE_COOKIE_BOUNDARY_PERSISTED", after_cookie_surface=after_cookie)

            post_cookie = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if post_cookie.get("captcha_observed") is True or post_cookie.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PHONE_POST_COOKIE_BOUNDARY_OBSERVED", post_cookie_navigation=post_cookie)
            if navigation_surface_fingerprint(post_cookie) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PHONE_POST_COOKIE_NAVIGATION_SURFACE_MISMATCH", post_cookie_navigation=post_cookie)

            try:
                approved = _approved_entry(post_cookie, request.entry_ordinal, request.expected_apply_label)
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), post_cookie_navigation=post_cookie)
            if not entry.is_visible() or not entry.is_enabled():
                return _result(base, counters, "blocked", "ADP_PHONE_APPLY_NOT_ACTIONABLE", post_cookie_navigation=post_cookie)
            actual_apply = entry.get_attribute("aria-label") or entry.inner_text() or ""
            if _normalize(actual_apply) != _normalize(request.expected_apply_label):
                return _result(base, counters, "blocked", "ADP_PHONE_APPLY_LABEL_DRIFT", post_cookie_navigation=post_cookie)

            try:
                counters["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                counters["navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PHONE_APPLY_CLICK_FAILED:{type(exc).__name__}", post_cookie_navigation=post_cookie)
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_PHONE_APPLY_OPENED_NEW_PAGE", post_cookie_navigation=post_cookie)

            identity = _snapshot(
                page,
                requested_url=request.application_url,
                timeout_ms=request.timeout_ms,
                render_wait_ms=request.render_wait_ms,
            )
            if identity.get("captcha_observed") is True or identity.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PHONE_POST_APPLY_BOUNDARY_OBSERVED", identity_surface=identity)
            if identity.get("runtime_state") != "inspected":
                return _result(base, counters, "blocked", "ADP_PHONE_IDENTITY_SURFACE_NOT_INSPECTED", identity_surface=identity)
            observed_fill_fp = safe_fill_surface_fingerprint(identity)
            if observed_fill_fp != request.expected_safe_fill_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PHONE_IDENTITY_SURFACE_MISMATCH", identity_surface=identity, observed_safe_fill_surface_fingerprint=observed_fill_fp)

            try:
                descriptor = phone_contract_descriptor(page)
            except PermissionError as exc:
                return _result(base, counters, "blocked", str(exc), identity_surface=identity, observed_safe_fill_surface_fingerprint=observed_fill_fp)

            return _result(
                base,
                counters,
                "inspected",
                "",
                observed_preference_surface_fingerprint=pref_fp,
                observed_safe_fill_surface_fingerprint=observed_fill_fp,
                resolved_entry_observation_key=observation_key,
                phone_contract=descriptor,
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP read-only phone contract inspector")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--expected-preference-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--output", default="adp-phone-contract-canary.json")
    args = parser.parse_args()
    report = run_adp_phone_contract_canary(AdpPhoneContractCanaryRequest(
        application_url=args.application_url,
        expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
        expected_preference_surface_fingerprint=args.expected_preference_surface_fingerprint,
        entry_ordinal=args.entry_ordinal,
        expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
    ))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "canary_status": report.get("canary_status"),
        "error_code": report.get("error_code"),
        "navigation_click_attempts": report.get("navigation_click_attempts", 0),
        "form_value_write_attempts": report.get("form_value_write_attempts", 0),
        "second_action_click_attempts": report.get("second_action_click_attempts", 0),
        "file_upload_attempts": report.get("file_upload_attempts", 0),
        "submit_attempts": report.get("submit_attempts", 0),
    }, sort_keys=True))
    return 0 if report.get("canary_status") == "inspected" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ADP_PHONE_CONTRACT_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
