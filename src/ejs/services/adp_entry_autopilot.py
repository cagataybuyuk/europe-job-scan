"""Bounded ADP application-entry autopilot for local verified-session tests.

This module reuses the previously live-reviewed Profile V2 Apply + identity +
phone + Continue contracts on an existing browser page. It may also click
Verify only after the user has already entered a syntactically valid OTP, and
may click exactly one reviewed Complete Your Application action after
authentication.

It never reads/logs OTP text, never writes an OTP, never clicks application
Next, never uploads a file and never submits an application.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

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
from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_navigation_canary import (
    _approved_entry,
    _normalize,
    _resolve_document_locator,
    _snapshot,
    navigation_surface_fingerprint,
)
from ejs.services.adp_phone_contract_canary import phone_contract_descriptor
from ejs.services.adp_preference_safe_fill_canary import (
    SAVE_CHANGES_LABEL,
    UNSELECT_ALL_LABEL,
    _unique_actionable_button,
)
from ejs.services.adp_profile_v2_continue_canary import (
    _write_identity_fields,
    _write_phone_fields,
    load_resolved_profile,
    phone_contract_surface_fingerprint,
)
from ejs.services.adp_same_page_manifest import _target_binding
from ejs.services.adp_safe_fill_canary import safe_fill_surface_fingerprint

AUTOPILOT_VERSION = "adp-entry-autopilot-v1"

# Live-reviewed on the same ADP tenant / target family.
EXPECTED_NAVIGATION_FP = "567e7890f5a01f151dbeeb23851ad7cf5fb8100a32c3e0386227d17cd507d313"
EXPECTED_PREFERENCE_FP = "f80faea8117621a286097dec92e55510843187ec9fdc82792379330e4034bd3c"
EXPECTED_SAFE_FILL_FP = "d8b72afea27912bd5e280d8a819ec836e44c2982faa3a3179bbb06c7aa58aa7f"
EXPECTED_PHONE_FP = "d6f3dc96678e66f5785c39ffc8bd4d6451204b69b2afbdf08493a6596727305c"
EXPECTED_ACTION_FP = "0e3e60f904525ad95f5244ce99a29a335928b01992f066cc4da9d45753a7bd53"

OTP_CONTROL_ID = "oneTimePassWord"
OTP_RE = re.compile(r"^[0-9]{6}$")


@dataclass(frozen=True)
class AdpEntryAutopilotRequest:
    application_url: str
    profile_json_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 5_000


def validate_request(request: AdpEntryAutopilotRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not request.profile_json_path:
        raise ValueError("ADP_ENTRY_AUTOPILOT_PROFILE_REQUIRED")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("ADP_ENTRY_AUTOPILOT_INVALID_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("ADP_ENTRY_AUTOPILOT_INVALID_RENDER_WAIT")


def _base_counters() -> dict:
    return {
        "preference_navigation_click_attempts": 0,
        "preference_navigation_click_successes": 0,
        "cookie_unselect_all_click_attempts": 0,
        "cookie_unselect_all_click_successes": 0,
        "cookie_save_click_attempts": 0,
        "cookie_save_click_successes": 0,
        "apply_click_attempts": 0,
        "apply_click_successes": 0,
        "form_value_write_attempts": 0,
        "form_value_write_successes": 0,
        "continue_click_attempts": 0,
        "continue_click_successes": 0,
        "otp_write_attempts": 0,
        "verify_click_attempts": 0,
        "verify_click_successes": 0,
        "complete_application_click_attempts": 0,
        "complete_application_click_successes": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
    }


def _cookie_preflight(page, request: AdpEntryAutopilotRequest, counters: dict) -> None:
    surface = preference_surface_descriptor(page)
    if surface.get("banner_visible") is not True:
        return

    candidate = reviewed_preferences_button(page)
    counters["preference_navigation_click_attempts"] += 1
    candidate.click(timeout=request.timeout_ms)
    counters["preference_navigation_click_successes"] += 1
    page.wait_for_timeout(500)

    pref = preference_surface_descriptor(page)
    if pref.get("preference_center_visible") is not True:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_PREFERENCE_CENTER_NOT_VISIBLE")
    if preference_surface_fingerprint(pref) != EXPECTED_PREFERENCE_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_PREFERENCE_SURFACE_DRIFT")
    if pref.get("visible_checkboxes") != []:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_VISIBLE_CHECKBOX_DRIFT")

    unselect = _unique_actionable_button(page, UNSELECT_ALL_LABEL)
    counters["cookie_unselect_all_click_attempts"] += 1
    unselect.click(timeout=request.timeout_ms)
    counters["cookie_unselect_all_click_successes"] += 1
    page.wait_for_timeout(250)

    save = _unique_actionable_button(page, SAVE_CHANGES_LABEL)
    counters["cookie_save_click_attempts"] += 1
    save.click(timeout=request.timeout_ms)
    counters["cookie_save_click_successes"] += 1
    page.wait_for_timeout(500)

    after = preference_surface_descriptor(page)
    if after.get("preference_center_visible") is True or after.get("banner_visible") is True:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_COOKIE_BOUNDARY_PERSISTED")


def advance_to_otp_on_existing_page(page, request: AdpEntryAutopilotRequest) -> dict:
    validate_request(request)
    profile = load_resolved_profile(request.profile_json_path)
    counters = _base_counters()

    pre = _snapshot(
        page,
        requested_url=request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    if pre.get("captcha_observed") is True or pre.get("auth_observed") is True:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_PREFLIGHT_BOUNDARY")
    if navigation_surface_fingerprint(pre) != EXPECTED_NAVIGATION_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_NAVIGATION_SURFACE_DRIFT")

    _cookie_preflight(page, request, counters)

    post_cookie = _snapshot(
        page,
        requested_url=request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    if navigation_surface_fingerprint(post_cookie) != EXPECTED_NAVIGATION_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_POST_COOKIE_NAVIGATION_DRIFT")

    approved = _approved_entry(post_cookie, 0, "Apply")
    entry = _resolve_document_locator(page, str(approved.get("observation_key", "")))
    if not entry.is_visible() or not entry.is_enabled():
        raise PermissionError("ADP_ENTRY_AUTOPILOT_APPLY_NOT_ACTIONABLE")
    actual_apply = entry.get_attribute("aria-label") or entry.inner_text() or ""
    if _normalize(actual_apply) != "apply":
        raise PermissionError("ADP_ENTRY_AUTOPILOT_APPLY_LABEL_DRIFT")
    counters["apply_click_attempts"] = 1
    entry.click(timeout=request.timeout_ms)
    counters["apply_click_successes"] = 1
    page.wait_for_timeout(1_000)

    form = _snapshot(
        page,
        requested_url=request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    if form.get("captcha_observed") is True or form.get("auth_observed") is True:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_POST_APPLY_BOUNDARY")
    if safe_fill_surface_fingerprint(form) != EXPECTED_SAFE_FILL_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_IDENTITY_SURFACE_DRIFT")

    phone_contract = phone_contract_descriptor(page)
    if phone_contract_surface_fingerprint(phone_contract) != EXPECTED_PHONE_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_PHONE_CONTRACT_DRIFT")

    identity = _write_identity_fields(page, form, profile, counters)
    phone = _write_phone_fields(page, profile, counters)

    action_surface = action_surface_descriptor(page)
    if action_surface_fingerprint(action_surface) != EXPECTED_ACTION_FP:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_ACTION_SURFACE_DRIFT")

    continue_button = page.get_by_role("button", name=CONTINUE_LABEL, exact=True)
    if (
        continue_button.count() != 1
        or not continue_button.is_visible()
        or not continue_button.is_enabled()
    ):
        raise PermissionError("ADP_ENTRY_AUTOPILOT_CONTINUE_NOT_ACTIONABLE")
    counters["continue_click_attempts"] = 1
    continue_button.click(timeout=request.timeout_ms)
    counters["continue_click_successes"] = 1

    otp = page.locator(f"#{OTP_CONTROL_ID}")
    try:
        otp.wait_for(state="visible", timeout=request.timeout_ms)
    except Exception as exc:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_OTP_NOT_OBSERVED") from exc
    if otp.count() != 1 or not otp.is_enabled():
        raise PermissionError("ADP_ENTRY_AUTOPILOT_OTP_NOT_ACTIONABLE")

    return {
        "autopilot_version": AUTOPILOT_VERSION,
        "autopilot_status": "waiting_for_otp",
        **counters,
        "identity_field_count": len(identity),
        "phone_readback_match": phone.get("phone_readback_match") is True,
        "otp_visible": True,
        "otp_write_allowed": False,
        "next_click_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
        "raw_values_exposed": False,
    }


def click_verify_if_user_populated_otp(page, counters: dict, *, timeout_ms: int = 20_000) -> bool:
    """Click Verify only after the user has already entered a six-digit OTP.

    The OTP value is read only in memory for shape validation and is never
    returned or logged.
    """
    otp = page.locator(f"#{OTP_CONTROL_ID}")
    if otp.count() != 1 or not otp.is_visible() or not otp.is_enabled():
        return False
    value = str(otp.input_value() or "")
    if not OTP_RE.fullmatch(value):
        return False

    verify = page.get_by_role("button", name="Verify", exact=True)
    if verify.count() != 1 or not verify.is_visible() or not verify.is_enabled():
        raise PermissionError("ADP_ENTRY_AUTOPILOT_VERIFY_NOT_ACTIONABLE")
    if counters.get("verify_click_attempts", 0) != 0:
        return False
    counters["verify_click_attempts"] = 1
    verify.click(timeout=timeout_ms)
    counters["verify_click_successes"] = 1
    return True


def click_complete_application_on_portal(
    page,
    application_url: str,
    counters: dict,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> bool:
    if counters.get("complete_application_click_attempts", 0) != 0:
        return False

    binding = _target_binding(str(page.url), application_url)
    if binding.get("target_bound") is not True:
        raise PermissionError("ADP_ENTRY_AUTOPILOT_PORTAL_TARGET_MISMATCH")

    snapshot = _snapshot(
        page,
        requested_url=application_url,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    actions = snapshot.get("form", {}).get("actions", [])
    candidates = [
        item for item in actions
        if isinstance(item, dict)
        and item.get("visible") is True
        and str(item.get("scope", "")) == "document"
        and _normalize(str(item.get("label", ""))) == "complete your application"
    ]
    if len(candidates) != 1:
        raise PermissionError(
            f"ADP_ENTRY_AUTOPILOT_COMPLETE_APPLICATION_COUNT:{len(candidates)}"
        )
    locator = _resolve_document_locator(
        page,
        str(candidates[0].get("observation_key", "")),
    )
    if not locator.is_visible() or not locator.is_enabled():
        raise PermissionError("ADP_ENTRY_AUTOPILOT_COMPLETE_APPLICATION_NOT_ACTIONABLE")

    counters["complete_application_click_attempts"] = 1
    locator.click(timeout=timeout_ms)
    counters["complete_application_click_successes"] = 1
    return True
