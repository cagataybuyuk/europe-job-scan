"""Bounded ADP address-Country keyboard selection canary.

Runs only on the already verified Personal Information page. It verifies the
reviewed manifest/contact/option contracts, then mutates only the address
Country combobox using exact reviewed text plus keyboard commit. No phone,
address text, consent, Next, upload, or submit action is permitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_contact_address_contract import (
    contract_fingerprint as contact_contract_fingerprint,
    inspect_on_verified_page as inspect_contact_address_on_verified_page,
)
from ejs.services.adp_same_page_country_combobox_probe import (
    COUNTRY_ID,
    _visible_option_surface,
    option_surface_fingerprint,
    reviewed_mobile_pair,
)
from ejs.services.adp_same_page_personal_information_safe_fill import (
    REVIEWED_ADDRESS_COUNTRY_ISO2,
    REVIEWED_ADDRESS_COUNTRY_LABEL,
    _country_readback_evidence,
)

CANARY_VERSION = "adp-same-page-country-keyboard-selection-v1"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AdpSamePageCountryKeyboardSelectionRequest:
    application_url: str
    expected_manifest_fingerprint: str
    expected_contact_contract_fingerprint: str
    expected_country_option_surface_fingerprint: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 5_000


def _validate(request: AdpSamePageCountryKeyboardSelectionRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value in (
        request.expected_manifest_fingerprint,
        request.expected_contact_contract_fingerprint,
        request.expected_country_option_surface_fingerprint,
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError("ADP_COUNTRY_KEYBOARD_CANARY_INVALID_FINGERPRINT")


def run_on_verified_page(
    page,
    request: AdpSamePageCountryKeyboardSelectionRequest,
) -> dict:
    _validate(request)
    contract = inspect_contact_address_on_verified_page(
        page,
        request.application_url,
        request.expected_manifest_fingerprint,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    observed_contact = contact_contract_fingerprint(contract)
    if observed_contact != request.expected_contact_contract_fingerprint:
        raise PermissionError(
            "ADP_COUNTRY_KEYBOARD_CANARY_CONTACT_CONTRACT_FINGERPRINT_MISMATCH"
        )
    mobile_pair = reviewed_mobile_pair(contract)

    country = page.locator(f"#{COUNTRY_ID}")
    if country.count() != 1 or not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_CONTROL_NOT_ACTIONABLE")
    if country.get_attribute("role") != "combobox":
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_ROLE_DRIFT")
    if country.get_attribute("aria-autocomplete") != "list":
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_AUTOCOMPLETE_DRIFT")

    before = _country_readback_evidence(page, country)
    if before["mode"] in {"label", "iso2", "custom_committed"}:
        return {
            "canary_version": CANARY_VERSION,
            "selection_status": "already_committed",
            "reviewed_mobile_pair": mobile_pair,
            "country_iso2": REVIEWED_ADDRESS_COUNTRY_ISO2,
            "country_label": REVIEWED_ADDRESS_COUNTRY_LABEL,
            "open_click_attempts": 0,
            "text_write_attempts": 0,
            "keyboard_commit_attempts": 0,
            "country_selection_successes": 0,
            "readback_evidence": before,
            "phone_write_attempts": 0,
            "address_text_write_attempts": 0,
            "consent_action_attempts": 0,
            "next_click_attempts": 0,
            "file_upload_attempts": 0,
            "submit_attempts": 0,
            "raw_values_exposed": False,
        }
    if before["mode"] != "empty":
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_PREEXISTING_CONFLICT")

    country.click(timeout=request.timeout_ms)
    page.wait_for_timeout(250)
    surface = _visible_option_surface(page)
    observed_surface = option_surface_fingerprint(surface)
    if observed_surface != request.expected_country_option_surface_fingerprint:
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_OPTION_SURFACE_DRIFT")
    if surface.get("turkey_candidate_count") != 1:
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_TURKEY_CANDIDATE_DRIFT")

    country.fill(REVIEWED_ADDRESS_COUNTRY_LABEL)
    page.wait_for_timeout(350)
    filtered_surface = _visible_option_surface(page)
    if filtered_surface.get("turkey_candidate_count") != 1:
        raise PermissionError("ADP_COUNTRY_KEYBOARD_CANARY_FILTERED_TURKEY_DRIFT")

    country.press("ArrowDown", timeout=request.timeout_ms)
    country.press("Enter", timeout=request.timeout_ms)

    readback = None
    for _ in range(20):
        page.wait_for_timeout(150)
        readback = _country_readback_evidence(page, country)
        if readback["mode"] in {"label", "iso2", "custom_committed"}:
            break
    if not isinstance(readback, dict) or readback["mode"] not in {
        "label",
        "iso2",
        "custom_committed",
    }:
        mode = readback.get("mode", "unknown") if isinstance(readback, dict) else "unknown"
        raise PermissionError(
            f"ADP_COUNTRY_KEYBOARD_CANARY_READBACK_MISMATCH:{mode}"
        )

    return {
        "canary_version": CANARY_VERSION,
        "selection_status": "verified",
        "reviewed_mobile_pair": mobile_pair,
        "country_iso2": REVIEWED_ADDRESS_COUNTRY_ISO2,
        "country_label": REVIEWED_ADDRESS_COUNTRY_LABEL,
        "contact_contract_fingerprint": observed_contact,
        "option_surface_fingerprint": observed_surface,
        "open_click_attempts": 1,
        "text_write_attempts": 1,
        "keyboard_commit_attempts": 1,
        "country_selection_successes": 1,
        "readback_evidence": readback,
        "phone_write_attempts": 0,
        "address_text_write_attempts": 0,
        "consent_action_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "raw_values_exposed": False,
    }
