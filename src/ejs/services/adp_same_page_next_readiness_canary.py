"""Read-only ADP Personal Information Next-readiness inspection.

Runs only after the bounded Personal Information safe-fill has returned
safe_fill_status=verified. This module never clicks Next and never mutates form
values. It checks target binding, the unique visible Next action, browser
validity metadata, and visible validation/error surfaces.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_manifest import _target_binding
from ejs.services.adp_navigation_canary import (
    _resolve_document_locator,
    _snapshot,
)

CANARY_VERSION = "adp-same-page-next-readiness-v1"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEWED_NEXT_LABELS = {"next", "continue", "proceed"}

EXPECTED_DISABLED_IDS = {"personalInfomationEmail"}

REVIEWED_REQUIRED_IDS = (
    "personalInfomationFirstName",
    "personalInfomationLastName",
    "personalInfomationEmail",
    "PersonalAddress_country",
    "PersonalAddress_address_line1",
    "PersonalAddress_city",
    "PersonalAddress_state",
    "PersonalAddress_postalCode",
)


@dataclass(frozen=True)
class AdpSamePageNextReadinessRequest:
    application_url: str
    expected_state_option_surface_fingerprint: str
    timeout_ms: int = 20_000


def validate_request(request: AdpSamePageNextReadinessRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not FINGERPRINT_RE.fullmatch(
        request.expected_state_option_surface_fingerprint
    ):
        raise ValueError("ADP_NEXT_READINESS_INVALID_STATE_SURFACE_FINGERPRINT")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("ADP_NEXT_READINESS_INVALID_TIMEOUT")


def _required_control_status(page) -> list[dict]:
    rows = []
    for element_id in REVIEWED_REQUIRED_IDS:
        locator = page.locator(f"#{element_id}")
        if locator.count() != 1 or not locator.is_visible():
            raise PermissionError(
                f"ADP_NEXT_READINESS_REQUIRED_CONTROL_NOT_VISIBLE:{element_id}"
            )
        try:
            valid = bool(locator.evaluate("el => typeof el.checkValidity === 'function' ? el.checkValidity() : true"))
        except Exception:
            valid = False
        rows.append({
            "id": element_id,
            "enabled": locator.is_enabled(),
            "expected_disabled": element_id in EXPECTED_DISABLED_IDS,
            "browser_valid": valid,
            "aria_invalid": str(locator.get_attribute("aria-invalid") or "").casefold(),
            "value_read": False,
        })
    return rows


def _visible_validation_surface(page) -> dict:
    return page.evaluate(
        """() => {
          const visible = (el) => {
            const style = window.getComputedStyle(el);
            const rect = el.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const nodes = Array.from(document.querySelectorAll(
            '[role="alert"], [aria-invalid="true"], .vdl-form-group--error, .vdl-validation-error'
          )).filter(visible).slice(0, 80);
          return {
            visible_issue_node_count: nodes.length,
            visible_alert_count: nodes.filter(el => el.getAttribute('role') === 'alert').length,
            visible_aria_invalid_count: nodes.filter(el => el.getAttribute('aria-invalid') === 'true').length,
            candidate_values_read: false,
            raw_values_exposed: false,
          };
        }"""
    )


def inspect_on_verified_page(
    page,
    request: AdpSamePageNextReadinessRequest,
    safe_fill_report: dict,
) -> dict:
    validate_request(request)
    if safe_fill_report.get("safe_fill_status") != "verified":
        raise PermissionError("ADP_NEXT_READINESS_REQUIRES_VERIFIED_SAFE_FILL")
    if safe_fill_report.get("next_click_attempts", 0) != 0:
        raise PermissionError("ADP_NEXT_READINESS_PREEXISTING_NEXT_CLICK")
    if safe_fill_report.get("file_upload_attempts", 0) != 0:
        raise PermissionError("ADP_NEXT_READINESS_PREEXISTING_UPLOAD")
    if safe_fill_report.get("submit_attempts", 0) != 0:
        raise PermissionError("ADP_NEXT_READINESS_PREEXISTING_SUBMIT")
    if (
        safe_fill_report.get("expected_state_option_surface_fingerprint", "")
        != request.expected_state_option_surface_fingerprint
    ):
        raise PermissionError("ADP_NEXT_READINESS_STATE_SURFACE_FINGERPRINT_MISMATCH")

    identity = safe_fill_report.get("identity_result", {})
    mobile = safe_fill_report.get("mobile_phone_result", {})
    country = safe_fill_report.get("address_country_result", {})
    state = safe_fill_report.get("address_state_result", {})
    if identity.get("email_readback_match") is not True:
        raise PermissionError("ADP_NEXT_READINESS_EMAIL_READBACK_REQUIRED")
    if mobile.get("phone_readback_match") is not True:
        raise PermissionError("ADP_NEXT_READINESS_PHONE_READBACK_REQUIRED")
    if country.get("readback_match") is not True:
        raise PermissionError("ADP_NEXT_READINESS_COUNTRY_READBACK_REQUIRED")
    if state.get("readback_match") is not True:
        raise PermissionError("ADP_NEXT_READINESS_STATE_READBACK_REQUIRED")

    binding = _target_binding(str(page.url), request.application_url)
    if binding.get("target_bound") is not True:
        raise PermissionError("ADP_NEXT_READINESS_TARGET_MISMATCH")

    required = _required_control_status(page)
    invalid_required = [
        row for row in required
        if (
            (row["expected_disabled"] is not True and row["enabled"] is not True)
            or row["browser_valid"] is not True
            or row["aria_invalid"] == "true"
        )
    ]

    snapshot = _snapshot(
        page,
        requested_url=request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=min(5_000, request.timeout_ms),
    )
    actions = snapshot.get("form", {}).get("actions", [])
    candidates = [
        action for action in actions
        if isinstance(action, dict)
        and action.get("visible") is True
        and str(action.get("scope", "")) == "document"
        and " ".join(str(action.get("label", "")).casefold().split())
        in REVIEWED_NEXT_LABELS
    ]
    count = len(candidates)
    if count != 1:
        raise PermissionError(f"ADP_NEXT_READINESS_NEXT_ACTION_COUNT:{count}")

    candidate = candidates[0]
    next_button = _resolve_document_locator(
        page,
        str(candidate.get("observation_key", "")),
    )
    if not next_button.is_visible():
        raise PermissionError("ADP_NEXT_READINESS_NEXT_NOT_VISIBLE")

    validation = _visible_validation_surface(page)
    next_enabled = next_button.is_enabled()
    ready = (
        not invalid_required
        and next_enabled
        and int(validation.get("visible_issue_node_count", 0)) == 0
    )

    return {
        "canary_version": CANARY_VERSION,
        "readiness_status": "ready" if ready else "blocked",
        "target_bound": True,
        "reviewed_required_control_count": len(required),
        "invalid_required_control_count": len(invalid_required),
        "next_action_count": count,
        "next_action_label": " ".join(str(candidate.get("label", "")).split()),
        "next_action_type": str(candidate.get("type", "")),
        "next_observation_key_present": bool(candidate.get("observation_key")),
        "next_visible": True,
        "next_enabled": next_enabled,
        "visible_issue_node_count": int(validation.get("visible_issue_node_count", 0)),
        "visible_alert_count": int(validation.get("visible_alert_count", 0)),
        "visible_aria_invalid_count": int(validation.get("visible_aria_invalid_count", 0)),
        "next_click_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "candidate_values_read": False,
        "raw_values_exposed": False,
        "next_click_allowed": False,
        "final_submit_allowed": False,
    }
