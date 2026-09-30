"""Bounded ADP State/Territory selection canary after reviewed Country selection.

Runs only on the already verified Personal Information page. It reuses the
reviewed State-after-Country structural contract, validates the observed State
option-surface fingerprint, and selects exactly one reviewed State/Territory
label. It never reads option values/properties and grants no authority to
phone, address text, consent, Next, upload, or submit actions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_state_after_country_contract import (
    STATE_ID,
    contract_fingerprint as state_contract_fingerprint,
    inspect_after_reviewed_country_selection,
)

CANARY_VERSION = "adp-same-page-state-selection-v1"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AdpSamePageStateSelectionRequest:
    application_url: str
    expected_manifest_fingerprint: str
    expected_contact_contract_fingerprint: str
    expected_country_option_surface_fingerprint: str
    expected_state_after_country_contract_fingerprint: str
    expected_state_option_surface_fingerprint: str
    reviewed_state_label: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 5_000


def _normalize_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _validate(request: AdpSamePageStateSelectionRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value in (
        request.expected_manifest_fingerprint,
        request.expected_contact_contract_fingerprint,
        request.expected_country_option_surface_fingerprint,
        request.expected_state_after_country_contract_fingerprint,
        request.expected_state_option_surface_fingerprint,
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError("ADP_STATE_SELECTION_CANARY_INVALID_FINGERPRINT")
    if not _normalize_label(request.reviewed_state_label):
        raise ValueError("ADP_STATE_SELECTION_CANARY_EMPTY_REVIEWED_LABEL")


def _state_visible_text(state) -> str:
    return _normalize_label(state.inner_text() or "")


def run_on_verified_page(page, request: AdpSamePageStateSelectionRequest) -> dict:
    _validate(request)

    structural = inspect_after_reviewed_country_selection(
        page,
        request.application_url,
        request.expected_manifest_fingerprint,
        request.expected_contact_contract_fingerprint,
        request.expected_country_option_surface_fingerprint,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    observed_contract_fp = state_contract_fingerprint(structural)
    if observed_contract_fp != request.expected_state_after_country_contract_fingerprint:
        raise PermissionError("ADP_STATE_SELECTION_CANARY_CONTRACT_FINGERPRINT_MISMATCH")

    surface = structural.get("state_option_surface", {})
    observed_surface_fp = str(surface.get("surface_fingerprint", "") or "")
    if observed_surface_fp != request.expected_state_option_surface_fingerprint:
        raise PermissionError("ADP_STATE_SELECTION_CANARY_OPTION_SURFACE_DRIFT")
    if surface.get("unique_nonempty_labels") is not True:
        raise PermissionError("ADP_STATE_SELECTION_CANARY_NONUNIQUE_LABEL_SURFACE")

    reviewed = _normalize_label(request.reviewed_state_label)
    candidates = [
        row for row in surface.get("options", [])
        if _normalize_label(row.get("label", "")) == reviewed
        and row.get("disabled") is not True
    ]
    if len(candidates) != 1:
        raise PermissionError(
            f"ADP_STATE_SELECTION_CANARY_REVIEWED_LABEL_COUNT:{len(candidates)}"
        )

    state = page.locator(f"#{STATE_ID}")
    if state.count() != 1 or not state.is_visible() or not state.is_enabled():
        raise PermissionError("ADP_STATE_SELECTION_CANARY_CONTROL_NOT_ACTIONABLE")
    if state.get_attribute("role") != "combobox":
        raise PermissionError("ADP_STATE_SELECTION_CANARY_ROLE_DRIFT")
    if state.get_attribute("aria-controls") != f"{STATE_ID}__listbox":
        raise PermissionError("ADP_STATE_SELECTION_CANARY_LISTBOX_ID_DRIFT")

    before = _state_visible_text(state)
    if before:
        if before == reviewed:
            return {
                "canary_version": CANARY_VERSION,
                "selection_status": "already_committed",
                "reviewed_state_label": reviewed,
                "state_after_country_contract_fingerprint": observed_contract_fp,
                "state_option_surface_fingerprint": observed_surface_fp,
                "state_open_click_attempts": structural.get("state_open_click_attempts", 0),
                "state_selection_attempts": 0,
                "state_selection_successes": 0,
                "state_readback_match": True,
                "phone_write_attempts": 0,
                "address_text_write_attempts": 0,
                "consent_action_attempts": 0,
                "next_click_attempts": 0,
                "file_upload_attempts": 0,
                "submit_attempts": 0,
                "raw_values_exposed": False,
            }
        raise PermissionError("ADP_STATE_SELECTION_CANARY_PREEXISTING_CONFLICT")

    state.click(timeout=request.timeout_ms)
    page.wait_for_timeout(200)
    listbox = page.locator(f"#{STATE_ID}__listbox")
    if listbox.count() != 1 or not listbox.is_visible():
        raise PermissionError("ADP_STATE_SELECTION_CANARY_LISTBOX_NOT_VISIBLE")

    options = listbox.locator("[role='option']")
    live_matches = []
    for index in range(int(options.count())):
        option = options.nth(index)
        if not option.is_visible() or option.is_disabled():
            continue
        label = _normalize_label(option.inner_text() or "")
        if label == reviewed:
            live_matches.append(option)
    if len(live_matches) != 1:
        try:
            state.press("Escape", timeout=request.timeout_ms)
        finally:
            raise PermissionError(
                f"ADP_STATE_SELECTION_CANARY_LIVE_LABEL_COUNT:{len(live_matches)}"
            )

    live_matches[0].click(timeout=request.timeout_ms)
    page.wait_for_timeout(250)
    after = _state_visible_text(state)
    if after != reviewed:
        raise PermissionError("ADP_STATE_SELECTION_CANARY_READBACK_MISMATCH")

    return {
        "canary_version": CANARY_VERSION,
        "selection_status": "verified",
        "reviewed_state_label": reviewed,
        "state_after_country_contract_fingerprint": observed_contract_fp,
        "state_option_surface_fingerprint": observed_surface_fp,
        "state_open_click_attempts": structural.get("state_open_click_attempts", 0) + 1,
        "state_selection_attempts": 1,
        "state_selection_successes": 1,
        "state_readback_match": True,
        "phone_write_attempts": 0,
        "address_text_write_attempts": 0,
        "consent_action_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "raw_values_exposed": False,
    }
