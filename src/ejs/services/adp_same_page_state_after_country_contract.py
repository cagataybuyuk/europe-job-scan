"""ADP State/Territory structural contract after reviewed Turkey selection.

This canary starts on the already verified Personal Information page, validates
the reviewed pre-mutation manifest/contact contract, performs only the reviewed
Turkey address-Country selection, then inspects State/Territory without reading
or writing its value. No phone, address-text, consent, Next, upload, or submit
authority is granted.
"""
from __future__ import annotations

from types import SimpleNamespace
import hashlib
import json

from ejs.services.adp_same_page_contact_address_contract import (
    contract_fingerprint as contact_contract_fingerprint,
    inspect_on_verified_page as inspect_contact_address_on_verified_page,
)
from ejs.services.adp_same_page_personal_information_safe_fill import (
    REVIEWED_ADDRESS_COUNTRY_ISO2,
    _set_address_country,
)
from ejs.services.adp_same_page_state_dom_contract import (
    STATE_ID,
    _ancestor_neighborhood,
    _descendants,
    _metadata,
    _parent_chain,
    _sibling_metadata,
)

CONTRACT_VERSION = "adp-same-page-state-after-country-contract-v2"


def _snapshot_state(page) -> dict:
    state = page.locator(f"#{STATE_ID}")
    count = int(state.count())
    if count != 1:
        raise PermissionError(
            f"ADP_STATE_AFTER_COUNTRY_STATE_CONTROL_COUNT_DRIFT:{count}"
        )
    return {
        "visible": state.is_visible(),
        "metadata": _metadata(state),
        "descendants": _descendants(state),
        "siblings": _sibling_metadata(state),
        "ancestor_neighborhood": _ancestor_neighborhood(state),
        "parent_chain": _parent_chain(state),
        "candidate_values_read": False,
    }


def _state_option_surface(page, post_state: dict, *, timeout_ms: int) -> dict:
    metadata = post_state.get("metadata", {})
    if metadata.get("role") != "combobox":
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_POST_STATE_NOT_COMBOBOX")
    listbox_id = str(metadata.get("aria_controls", "") or "")
    if listbox_id != f"{STATE_ID}__listbox":
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_LISTBOX_ID_DRIFT")

    state = page.locator(f"#{STATE_ID}")
    state.click(timeout=timeout_ms)
    page.wait_for_timeout(250)

    listbox = page.locator(f"#{listbox_id}")
    if listbox.count() != 1 or not listbox.is_visible():
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_LISTBOX_NOT_VISIBLE")

    options = listbox.locator("[role='option']")
    count = int(options.count())
    if count < 1 or count > 300:
        raise PermissionError(
            f"ADP_STATE_AFTER_COUNTRY_OPTION_COUNT_INVALID:{count}"
        )

    rows = []
    for index in range(count):
        option = options.nth(index)
        if not option.is_visible():
            continue
        label = " ".join(str(option.inner_text() or "").split())
        if not label:
            raise PermissionError("ADP_STATE_AFTER_COUNTRY_OPTION_LABEL_EMPTY")
        rows.append({
            "ordinal": index,
            "label": label,
            "disabled": option.is_disabled(),
            "id": str(option.get_attribute("id") or "")[:180],
            "role": str(option.get_attribute("role") or "")[:40],
            "value_attribute_read": False,
            "property_value_read": False,
        })

    if not rows:
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_VISIBLE_OPTIONS_MISSING")
    labels = [row["label"] for row in rows]
    surface = {
        "listbox_id": listbox_id,
        "visible_option_count": len(rows),
        "options": rows,
        "unique_visible_labels": len(set(labels)) == len(labels),
        "candidate_values_read": False,
        "raw_candidate_values_exposed": False,
    }
    payload = json.dumps(
        surface,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    surface["surface_fingerprint"] = hashlib.sha256(payload).hexdigest()

    state.press("Escape", timeout=timeout_ms)
    page.wait_for_timeout(100)
    surface["close_escape_attempts"] = 1
    return surface


def _stable_descriptor(report: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "pre_contact_contract_fingerprint": report.get(
            "pre_contact_contract_fingerprint", ""
        ),
        "expected_country_option_surface_fingerprint": report.get(
            "expected_country_option_surface_fingerprint", ""
        ),
        "pre_state": report.get("pre_state", {}),
        "post_state": report.get("post_state", {}),
        "state_option_surface": report.get("state_option_surface", {}),
    }


def contract_fingerprint(report: dict) -> str:
    payload = json.dumps(
        _stable_descriptor(report),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def inspect_after_reviewed_country_selection(
    page,
    application_url: str,
    expected_manifest_fingerprint: str,
    expected_contact_contract_fingerprint: str,
    expected_country_option_surface_fingerprint: str,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> dict:
    pre_contract = inspect_contact_address_on_verified_page(
        page,
        application_url,
        expected_manifest_fingerprint,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    observed_contact = contact_contract_fingerprint(pre_contract)
    if observed_contact != expected_contact_contract_fingerprint:
        raise PermissionError(
            "ADP_STATE_AFTER_COUNTRY_PRE_CONTACT_FINGERPRINT_MISMATCH"
        )

    pre_state = _snapshot_state(page)

    counters = {
        "form_value_write_attempts": 0,
        "form_value_write_successes": 0,
        "address_write_attempts": 0,
        "address_write_successes": 0,
        "phone_write_attempts": 0,
        "phone_write_successes": 0,
        "combobox_open_click_attempts": 0,
        "combobox_open_click_successes": 0,
        "country_selection_attempts": 0,
        "country_selection_successes": 0,
    }
    profile = SimpleNamespace(
        address_country_iso2=REVIEWED_ADDRESS_COUNTRY_ISO2,
    )
    request = SimpleNamespace(
        expected_country_option_surface_fingerprint=(
            expected_country_option_surface_fingerprint
        ),
        timeout_ms=timeout_ms,
    )

    country_result = _set_address_country(
        page,
        profile,
        request,
        counters,
    )
    if country_result.get("readback_match") is not True:
        raise PermissionError(
            "ADP_STATE_AFTER_COUNTRY_COUNTRY_SELECTION_NOT_VERIFIED"
        )

    # Allow ADP's country-dependent State component to rerender before snapshot.
    page.wait_for_timeout(750)
    post_state = _snapshot_state(page)
    state_option_surface = _state_option_surface(
        page,
        post_state,
        timeout_ms=timeout_ms,
    )

    report = {
        "contract_version": CONTRACT_VERSION,
        "pre_contact_contract_fingerprint": observed_contact,
        "expected_country_option_surface_fingerprint": (
            expected_country_option_surface_fingerprint
        ),
        "country_selection": {
            "readback_match": True,
            "readback_mode": country_result.get("readback_mode", ""),
            "executed": country_result.get("executed") is True,
            "raw_values_exposed": False,
        },
        "pre_state": pre_state,
        "post_state": post_state,
        "state_option_surface": state_option_surface,
        "form_value_write_attempts": counters["form_value_write_attempts"],
        "form_value_write_successes": counters["form_value_write_successes"],
        "country_selection_attempts": counters["country_selection_attempts"],
        "country_selection_successes": counters["country_selection_successes"],
        "state_open_click_attempts": 1,
        "state_open_click_successes": 1,
        "state_selection_attempts": 0,
        "phone_write_attempts": 0,
        "address_text_write_attempts": 0,
        "consent_action_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "state_candidate_values_read": False,
        "raw_values_exposed": False,
    }
    report["contract_fingerprint"] = contract_fingerprint(report)
    return report
