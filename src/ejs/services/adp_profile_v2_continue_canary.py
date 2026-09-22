"""Reviewed ADP profile-v2 phone + Continue canary.

Maximum authority:
- five reviewed click paths: Preferences, Unselect All, Save Changes, Apply,
  Continue;
- at most five profile writes: first name, last name, email, phone country,
  phone national number;
- read-only diagnostics after Continue.

No credentials, social sign-in, file upload, CAPTCHA bypass, further application
action, or Final Submit is authorized.
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
    preference_surface_descriptor,
    preference_surface_fingerprint,
    reviewed_preferences_button,
)
from ejs.services.adp_continue_canary import (
    CONTINUE_LABEL,
    action_surface_descriptor,
    action_surface_fingerprint,
)
from ejs.services.adp_continue_diagnostic_canary import post_continue_diagnostics
from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_navigation_canary import (
    FINGERPRINT_RE,
    _approved_entry,
    _normalize,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.adp_phone_contract_canary import (
    PHONE_COUNTRY_NAME,
    PHONE_INPUT_ID,
    phone_contract_descriptor,
)
from ejs.services.adp_preference_safe_fill_canary import (
    SAVE_CHANGES_LABEL,
    UNSELECT_ALL_LABEL,
    _unique_actionable_button,
)
from ejs.services.adp_profile_policy import ResolvedAdpProfile, resolve_adp_profile
from ejs.services.adp_safe_fill_canary import (
    TARGETS,
    _validate_target_control,
    safe_fill_surface_fingerprint,
)
from ejs.services.browser_worker import BrowserRuntimeConfig

CANARY_VERSION = "adp-profile-v2-continue-canary-v3"


@dataclass(frozen=True)
class AdpProfileV2ContinueCanaryRequest:
    application_url: str
    expected_navigation_surface_fingerprint: str
    expected_preference_surface_fingerprint: str
    entry_ordinal: int
    expected_safe_fill_surface_fingerprint: str
    expected_phone_contract_fingerprint: str
    expected_post_fill_action_surface_fingerprint: str
    profile_json_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 10_000


def validate_request(request: AdpProfileV2ContinueCanaryRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value, code in (
        (request.expected_navigation_surface_fingerprint, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"),
        (request.expected_preference_surface_fingerprint, "INVALID_EXPECTED_PREFERENCE_SURFACE_FINGERPRINT"),
        (request.expected_safe_fill_surface_fingerprint, "INVALID_EXPECTED_SAFE_FILL_SURFACE_FINGERPRINT"),
        (request.expected_phone_contract_fingerprint, "INVALID_EXPECTED_PHONE_CONTRACT_FINGERPRINT"),
        (request.expected_post_fill_action_surface_fingerprint, "INVALID_EXPECTED_POST_FILL_ACTION_SURFACE_FINGERPRINT"),
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError(code)
    if type(request.entry_ordinal) is not int or request.entry_ordinal < 0 or request.entry_ordinal > 9:
        raise ValueError("INVALID_ADP_ENTRY_ORDINAL")
    if not request.profile_json_path:
        raise ValueError("ADP_PROFILE_V2_CANARY_REQUIRES_PROFILE_JSON")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("INVALID_CANARY_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("INVALID_RENDER_WAIT_TIMEOUT")
    SafeFieldWriterAuthority().validate()
    if not {"candidate.first_name", "candidate.last_name", "candidate.email", "candidate.phone"}.issubset(SAFE_VERIFIED_FIELDS):
        raise PermissionError("ADP_PROFILE_V2_TARGET_NOT_AUTO_SAFE")


def load_resolved_profile(path: str) -> ResolvedAdpProfile:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ADP_PROFILE_V2_JSON_INVALID")
    return resolve_adp_profile(raw)


def phone_contract_surface_descriptor(contract: dict) -> dict:
    country = contract.get("country_control", {})
    phone = contract.get("phone_control", {})
    options = [
        {
            "label": str(item.get("label", "")),
            "value": str(item.get("value", "")),
            "disabled": item.get("disabled") is True,
        }
        for item in country.get("options", [])
        if isinstance(item, dict)
    ]
    options.sort(key=lambda item: (item["value"], item["label"]))
    return {
        "country_control": {
            "tag": str(country.get("tag", "")),
            "name": str(country.get("name", "")),
            "required": country.get("required") is True,
            "aria_required": str(country.get("aria_required", "")),
            "option_count": int(country.get("option_count", 0) or 0),
            "options": options,
        },
        "phone_control": {
            "tag": str(phone.get("tag", "")),
            "id": str(phone.get("id", "")),
            "name": str(phone.get("name", "")),
            "type": str(phone.get("type", "")),
            "required": phone.get("required") is True,
            "aria_required": str(phone.get("aria_required", "")),
            "pattern": str(phone.get("pattern", "")),
            "inputmode": str(phone.get("inputmode", "")),
            "minlength": str(phone.get("minlength", "")),
            "maxlength": str(phone.get("maxlength", "")),
            "placeholder": str(phone.get("placeholder", "")),
            "autocomplete": str(phone.get("autocomplete", "")),
            "aria_describedby": str(phone.get("aria_describedby", "")),
        },
        "instruction_text": str(contract.get("instruction_text", "")),
        "runtime_required_observed_in_prior_live_diagnostic": contract.get("runtime_required_observed_in_prior_live_diagnostic") is True,
    }


def phone_contract_surface_fingerprint(contract: dict) -> str:
    payload = json.dumps(
        phone_contract_surface_descriptor(contract),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def phone_readback_shape(actual: str, expected_national: str, country_iso2: str) -> dict:
    actual_digits = "".join(ch for ch in str(actual) if ch.isdigit())
    expected_digits = "".join(ch for ch in str(expected_national) if ch.isdigit())
    calling_code = {"TR": "90"}.get(str(country_iso2).upper(), "")
    return {
        "raw_length": len(str(actual)),
        "digit_count": len(actual_digits),
        "expected_digit_count": len(expected_digits),
        "exact_digit_match": actual_digits == expected_digits,
        "suffix_matches_expected": bool(expected_digits) and actual_digits.endswith(expected_digits),
        "country_calling_code_prefixed": bool(calling_code) and actual_digits == calling_code + expected_digits,
        "trunk_zero_prefixed": actual_digits == "0" + expected_digits,
        "non_digit_formatting_present": any(not ch.isdigit() for ch in str(actual)),
        "raw_value_exposed": False,
    }


class PhoneReadbackMismatch(PermissionError):
    def __init__(self, shape: dict):
        super().__init__("ADP_PROFILE_V2_PHONE_READBACK_MISMATCH")
        self.shape = shape


def _base_report(request: AdpProfileV2ContinueCanaryRequest, profile: ResolvedAdpProfile) -> dict:
    return {
        "canary_version": CANARY_VERSION,
        "profile_v2_continue_canary_only": True,
        "requested_url": request.application_url,
        "expected_navigation_surface_fingerprint": request.expected_navigation_surface_fingerprint,
        "expected_preference_surface_fingerprint": request.expected_preference_surface_fingerprint,
        "expected_safe_fill_surface_fingerprint": request.expected_safe_fill_surface_fingerprint,
        "expected_phone_contract_fingerprint": request.expected_phone_contract_fingerprint,
        "expected_post_fill_action_surface_fingerprint": request.expected_post_fill_action_surface_fingerprint,
        "approved_entry_ordinal": request.entry_ordinal,
        "profile_evidence": profile.non_secret_evidence(),
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
        "form_value_write_successes": 0,
        "second_action_click_attempts": 0,
        "second_action_click_successes": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "further_navigation_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }


def _result(base: dict, counters: dict, status: str, error_code: str, **evidence) -> dict:
    return {**base, **counters, "canary_status": status, "error_code": error_code, **evidence}


def _write_identity_fields(page, form: dict, profile: ResolvedAdpProfile, counters: dict) -> list[dict]:
    values = {
        "candidate.first_name": profile.first_name,
        "candidate.last_name": profile.last_name,
        "candidate.email": profile.email,
    }
    results: list[dict] = []
    for spec in TARGETS:
        canonical, element_id, _label, _ctype, _required = spec
        _validate_target_control(form, spec)
        locator = page.locator(f"#{element_id}")
        if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
            raise PermissionError(f"ADP_PROFILE_V2_RUNTIME_LOCATOR_DRIFT:{canonical}")
        desired = values[canonical]
        before = locator.input_value()
        executed = before != desired
        if executed:
            counters["form_value_write_attempts"] += 1
            locator.fill(desired)
            counters["form_value_write_successes"] += 1
        readback = locator.input_value()
        valid = bool(locator.evaluate("el => el.checkValidity()"))
        if readback != desired or not valid:
            raise PermissionError(f"ADP_PROFILE_V2_IDENTITY_READBACK_INVALID:{canonical}")
        results.append({
            "canonical_field": canonical,
            "control_id": element_id,
            "executed": executed,
            "value_hash": value_hash(desired),
            "readback_match": True,
            "valid": True,
        })
    return results


def _write_phone_fields(page, profile: ResolvedAdpProfile, counters: dict) -> dict:
    country = page.locator(f"select[name='{PHONE_COUNTRY_NAME}']")
    phone = page.locator(f"#{PHONE_INPUT_ID}")
    if country.count() != 1 or not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_PROFILE_V2_PHONE_COUNTRY_NOT_ACTIONABLE")
    if phone.count() != 1 or not phone.is_visible() or not phone.is_enabled():
        raise PermissionError("ADP_PROFILE_V2_PHONE_INPUT_NOT_ACTIONABLE")

    option = country.locator(f"option[value='{profile.phone_country_iso2}']")
    if option.count() != 1 or option.is_disabled():
        raise PermissionError("ADP_PROFILE_V2_PHONE_COUNTRY_OPTION_NOT_ACTIONABLE")

    country_before = country.input_value()
    country_executed = country_before != profile.phone_country_iso2
    if country_executed:
        counters["form_value_write_attempts"] += 1
        country.select_option(value=profile.phone_country_iso2)
        counters["form_value_write_successes"] += 1
    country_after = country.input_value()
    if country_after != profile.phone_country_iso2:
        raise PermissionError("ADP_PROFILE_V2_PHONE_COUNTRY_READBACK_MISMATCH")

    phone_before = phone.input_value()
    phone_executed = phone_before != profile.phone_national_number
    if phone_executed:
        counters["form_value_write_attempts"] += 1
        phone.fill(profile.phone_national_number)
        counters["form_value_write_successes"] += 1
    phone_after = phone.input_value()
    if phone_after != profile.phone_national_number:
        raise PhoneReadbackMismatch(
            phone_readback_shape(
                phone_after,
                profile.phone_national_number,
                profile.phone_country_iso2,
            )
        )

    return {
        "country_iso2": profile.phone_country_iso2,
        "country_write_executed": country_executed,
        "country_readback_match": True,
        "phone_write_executed": phone_executed,
        "phone_value_hash": value_hash(profile.phone_national_number),
        "phone_digit_count": len(profile.phone_national_number),
        "phone_readback_match": True,
    }


def run_adp_profile_v2_continue_canary(
    request: AdpProfileV2ContinueCanaryRequest,
    *,
    config: BrowserRuntimeConfig | None = None,
) -> dict:
    validate_request(request)
    profile = load_resolved_profile(request.profile_json_path)
    base = _base_report(request, profile)
    counters = {
        key: value for key, value in base.items()
        if key.endswith("_attempts") or key.endswith("_successes")
    }
    profile_map = {
        "candidate.first_name": profile.first_name,
        "candidate.last_name": profile.last_name,
        "candidate.email": profile.email,
        "candidate.phone": profile.phone_national_number,
    }

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
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_REQUIRES_APPLICATION_ENTRY", pre_navigation=pre)
            if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_PREFLIGHT_BOUNDARY_OBSERVED", pre_navigation=pre)
            if navigation_surface_fingerprint(pre) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_NAVIGATION_SURFACE_MISMATCH", pre_navigation=pre)

            try:
                preferences = reviewed_preferences_button(page)
            except PermissionError as exc:
                return _result(
                    base,
                    counters,
                    "blocked",
                    str(exc),
                    pre_navigation=pre,
                    preference_surface_before_click=preference_surface_descriptor(page),
                )
            try:
                counters["preference_navigation_click_attempts"] = 1
                preferences.click(timeout=request.timeout_ms)
                counters["preference_navigation_click_successes"] = 1
            except Exception as exc:
                return _result(
                    base,
                    counters,
                    "blocked",
                    f"ADP_PROFILE_V2_COOKIE_PREFS_CLICK_FAILED:{type(exc).__name__}",
                    pre_navigation=pre,
                    preference_surface_before_click=preference_surface_descriptor(page),
                )
            page.wait_for_timeout(500)

            pref = preference_surface_descriptor(page)
            pref_fp = preference_surface_fingerprint(pref)
            if pref.get("preference_center_visible") is not True or pref_fp != request.expected_preference_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_PREFERENCE_SURFACE_MISMATCH", preference_surface=pref, observed_preference_surface_fingerprint=pref_fp)
            if pref.get("visible_checkboxes") != []:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_VISIBLE_CHECKBOX_CONTRACT_DRIFT", preference_surface=pref)

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
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PROFILE_V2_COOKIE_POLICY_CLICK_FAILED:{type(exc).__name__}")
            page.wait_for_timeout(500)

            after_cookie = preference_surface_descriptor(page)
            if after_cookie.get("preference_center_visible") is True or after_cookie.get("banner_visible") is True:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_COOKIE_BOUNDARY_PERSISTED", after_cookie_surface=after_cookie)

            post_cookie = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if navigation_surface_fingerprint(post_cookie) != request.expected_navigation_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_POST_COOKIE_NAVIGATION_SURFACE_MISMATCH", post_cookie_navigation=post_cookie)
            try:
                approved = _approved_entry(post_cookie, request.entry_ordinal, "Apply")
                observation_key = str(approved.get("observation_key", ""))
                entry = _resolve_document_locator(page, observation_key)
                if not entry.is_visible() or not entry.is_enabled():
                    raise PermissionError("ADP_PROFILE_V2_APPLY_NOT_ACTIONABLE")
                actual_apply = entry.get_attribute("aria-label") or entry.inner_text() or ""
                if _normalize(actual_apply) != "apply":
                    raise PermissionError("ADP_PROFILE_V2_APPLY_LABEL_DRIFT")
                counters["navigation_click_attempts"] = 1
                entry.click(timeout=request.timeout_ms)
                counters["navigation_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PROFILE_V2_APPLY_CLICK_FAILED:{type(exc).__name__}", post_cookie_navigation=post_cookie)
            page.wait_for_timeout(1_000)
            if len(context.pages) != 1:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_APPLY_OPENED_NEW_PAGE")

            form = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            if form.get("captcha_observed") is True or form.get("auth_observed") is True:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_POST_APPLY_BOUNDARY_OBSERVED", pre_fill=form)
            actual_fill_fp = safe_fill_surface_fingerprint(form)
            if actual_fill_fp != request.expected_safe_fill_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_SAFE_FILL_SURFACE_MISMATCH", pre_fill=form, observed_safe_fill_surface_fingerprint=actual_fill_fp)

            try:
                phone_contract = phone_contract_descriptor(page)
                actual_phone_fp = phone_contract_surface_fingerprint(phone_contract)
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PROFILE_V2_PHONE_CONTRACT_INSPECTION_FAILED:{type(exc).__name__}", pre_fill=form)
            if actual_phone_fp != request.expected_phone_contract_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_PHONE_CONTRACT_MISMATCH", observed_phone_contract_fingerprint=actual_phone_fp)

            try:
                field_results = _write_identity_fields(page, form, profile, counters)
                phone_result = _write_phone_fields(page, profile, counters)
            except PhoneReadbackMismatch as exc:
                return _result(
                    base,
                    counters,
                    "blocked",
                    str(exc),
                    observed_phone_contract_fingerprint=actual_phone_fp,
                    field_results=field_results,
                    phone_readback_shape=exc.shape,
                )
            except PermissionError as exc:
                return _result(
                    base,
                    counters,
                    "blocked",
                    f"ADP_PROFILE_V2_WRITE_FAILED:{str(exc)}",
                    observed_phone_contract_fingerprint=actual_phone_fp,
                )
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PROFILE_V2_WRITE_FAILED:{type(exc).__name__}", observed_phone_contract_fingerprint=actual_phone_fp)

            action_surface = action_surface_descriptor(page)
            action_fp = action_surface_fingerprint(action_surface)
            if action_fp != request.expected_post_fill_action_surface_fingerprint:
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_POST_FILL_ACTION_SURFACE_MISMATCH", field_results=field_results, phone_result=phone_result, observed_post_fill_action_surface_fingerprint=action_fp, visible_button_surface=action_surface)

            continue_button = page.get_by_role("button", name=CONTINUE_LABEL, exact=True)
            if continue_button.count() != 1 or not continue_button.is_visible() or not continue_button.is_enabled():
                return _result(base, counters, "blocked", "ADP_PROFILE_V2_CONTINUE_NOT_ACTIONABLE", field_results=field_results, phone_result=phone_result, visible_button_surface=action_surface)
            try:
                counters["second_action_click_attempts"] = 1
                continue_button.click(timeout=request.timeout_ms)
                counters["second_action_click_successes"] = 1
            except Exception as exc:
                return _result(base, counters, "blocked", f"ADP_PROFILE_V2_CONTINUE_CLICK_FAILED:{type(exc).__name__}", field_results=field_results, phone_result=phone_result)
            page.wait_for_timeout(1_000)

            post = _snapshot(page, requested_url=request.application_url, timeout_ms=request.timeout_ms, render_wait_ms=request.render_wait_ms)
            diagnostics = post_continue_diagnostics(page, profile_map, post, request.expected_safe_fill_surface_fingerprint)
            return _result(
                base,
                counters,
                "diagnosed",
                "",
                observed_preference_surface_fingerprint=pref_fp,
                observed_safe_fill_surface_fingerprint=actual_fill_fp,
                observed_phone_contract_fingerprint=actual_phone_fp,
                observed_post_fill_action_surface_fingerprint=action_fp,
                field_results=field_results,
                phone_result=phone_result,
                post_continue_url=str(page.url),
                post_continue_diagnostics=diagnostics,
            )
        finally:
            if context is not None:
                context.close()
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="ADP profile-v2 phone + Continue canary")
    parser.add_argument("--url", required=True, dest="application_url")
    parser.add_argument("--expected-navigation-surface-fingerprint", required=True)
    parser.add_argument("--expected-preference-surface-fingerprint", required=True)
    parser.add_argument("--entry-ordinal", required=True, type=int)
    parser.add_argument("--expected-safe-fill-surface-fingerprint", required=True)
    parser.add_argument("--expected-phone-contract-fingerprint", required=True)
    parser.add_argument("--expected-post-fill-action-surface-fingerprint", required=True)
    parser.add_argument("--profile-json", required=True, dest="profile_json_path")
    parser.add_argument("--output", default="adp-profile-v2-continue-canary.json")
    args = parser.parse_args()

    try:
        report = run_adp_profile_v2_continue_canary(AdpProfileV2ContinueCanaryRequest(
            application_url=args.application_url,
            expected_navigation_surface_fingerprint=args.expected_navigation_surface_fingerprint,
            expected_preference_surface_fingerprint=args.expected_preference_surface_fingerprint,
            entry_ordinal=args.entry_ordinal,
            expected_safe_fill_surface_fingerprint=args.expected_safe_fill_surface_fingerprint,
            expected_phone_contract_fingerprint=args.expected_phone_contract_fingerprint,
            expected_post_fill_action_surface_fingerprint=args.expected_post_fill_action_surface_fingerprint,
            profile_json_path=args.profile_json_path,
        ))
    except Exception as exc:
        report = {
            "canary_version": CANARY_VERSION,
            "profile_v2_continue_canary_only": True,
            "canary_status": "blocked",
            "error_code": f"ADP_PROFILE_V2_PREFLIGHT_FAILED:{type(exc).__name__}",
            "error_detail": str(exc)[:240],
            "preference_navigation_click_attempts": 0,
            "cookie_unselect_all_click_attempts": 0,
            "cookie_save_click_attempts": 0,
            "navigation_click_attempts": 0,
            "form_value_write_attempts": 0,
            "second_action_click_attempts": 0,
            "credential_entry_attempts": 0,
            "file_upload_attempts": 0,
            "submit_attempts": 0,
            "further_navigation_allowed": False,
            "file_upload_allowed": False,
            "final_submit_allowed": False,
        }

    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report.get("canary_status") == "diagnosed" else 2


if __name__ == "__main__":
    sys.exit(main())
