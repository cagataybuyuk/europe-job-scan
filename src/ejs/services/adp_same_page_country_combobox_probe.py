"""One-click read-only ADP address-country combobox probe.

Authority:
- start from the already verified Personal Information page;
- verify the reviewed manifest and contact/address contract;
- prove the reviewed required Mobile Number pair semantically;
- click only the address Country combobox once to expose its option surface;
- inspect option labels/roles only.

No candidate values are read, no option is selected, no form field is written,
and Next/upload/submit remain forbidden.
"""
from __future__ import annotations

import hashlib
import json

from ejs.services.adp_same_page_contact_address_contract import (
    contract_fingerprint as contact_contract_fingerprint,
    inspect_on_verified_page as inspect_contact_address_on_verified_page,
)

CANARY_VERSION = "adp-same-page-country-combobox-probe-v1"
COUNTRY_ID = "PersonalAddress_country"
MOBILE_LABEL = "Mobile Number*"
HOME_LABEL = "Home Phone Number"


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def reviewed_mobile_pair(contract: dict) -> dict:
    pairs = [
        row for row in contract.get("phone_pair_candidates", [])
        if isinstance(row, dict)
    ]
    if len(pairs) != 2:
        raise PermissionError("ADP_COUNTRY_PROBE_PHONE_PAIR_COUNT_DRIFT")

    roles = {}
    for pair in pairs:
        country_text = _normalize(
            str(pair.get("country_semantic_context", {}).get("text", ""))
        )
        phone_text = _normalize(
            str(pair.get("phone_semantic_context", {}).get("text", ""))
        )
        if country_text != phone_text:
            raise PermissionError("ADP_COUNTRY_PROBE_PHONE_PAIR_SEMANTIC_MISMATCH")
        if country_text in {MOBILE_LABEL, HOME_LABEL}:
            if country_text in roles:
                raise PermissionError("ADP_COUNTRY_PROBE_PHONE_ROLE_NOT_UNIQUE")
            roles[country_text] = pair

    if set(roles) != {MOBILE_LABEL, HOME_LABEL}:
        raise PermissionError("ADP_COUNTRY_PROBE_PHONE_ROLE_SET_DRIFT")

    mobile = roles[MOBILE_LABEL]
    home = roles[HOME_LABEL]
    if int(mobile.get("country_ordinal", -1)) != 0 or int(mobile.get("phone_ordinal", -1)) != 0:
        raise PermissionError("ADP_COUNTRY_PROBE_MOBILE_PAIR_ORDINAL_DRIFT")
    if int(home.get("country_ordinal", -1)) != 1 or int(home.get("phone_ordinal", -1)) != 1:
        raise PermissionError("ADP_COUNTRY_PROBE_HOME_PAIR_ORDINAL_DRIFT")
    if int(mobile.get("dom_distance", 9999)) >= int(home.get("dom_distance", 9999)) + 1000:
        raise PermissionError("ADP_COUNTRY_PROBE_PHONE_DISTANCE_DRIFT")

    return {
        "country_ordinal": 0,
        "phone_ordinal": 0,
        "semantic_role": "mobile_required",
        "semantic_label": MOBILE_LABEL,
        "raw_values_exposed": False,
    }


def _visible_option_surface(page) -> dict:
    listboxes = page.locator("[role='listbox']:visible")
    options = page.locator("[role='option']:visible")
    option_rows = []
    for index in range(min(options.count(), 300)):
        option = options.nth(index)
        try:
            if not option.is_visible():
                continue
            text = _normalize(option.inner_text() or "")
        except Exception:
            continue
        if not text:
            continue
        option_rows.append({
            "ordinal": index,
            "label": text[:160],
            "disabled": option.is_disabled(),
        })

    labels = [row["label"] for row in option_rows]
    normalized = {label.casefold() for label in labels}
    turkey_labels = [
        label for label in labels
        if label.casefold() in {"turkey", "türkiye", "turkiye"}
    ]
    return {
        "visible_listbox_count": int(listboxes.count()),
        "visible_option_count": len(option_rows),
        "options": option_rows,
        "turkey_candidate_labels": turkey_labels,
        "turkey_candidate_count": len(turkey_labels),
        "unique_visible_labels": len(normalized) == len(labels),
        "candidate_values_read": False,
    }


def option_surface_fingerprint(surface: dict) -> str:
    stable = {
        "visible_listbox_count": int(surface.get("visible_listbox_count", 0)),
        "options": [
            {
                "label": str(row.get("label", "")),
                "disabled": row.get("disabled") is True,
            }
            for row in surface.get("options", [])
            if isinstance(row, dict)
        ],
        "turkey_candidate_labels": list(surface.get("turkey_candidate_labels", [])),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def inspect_country_combobox_on_verified_page(
    page,
    application_url: str,
    expected_manifest_fingerprint: str,
    expected_contact_contract_fingerprint: str,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> dict:
    contract = inspect_contact_address_on_verified_page(
        page,
        application_url,
        expected_manifest_fingerprint,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    observed_contract_fingerprint = contact_contract_fingerprint(contract)
    if observed_contract_fingerprint != expected_contact_contract_fingerprint:
        raise PermissionError("ADP_COUNTRY_PROBE_CONTACT_CONTRACT_FINGERPRINT_MISMATCH")

    mobile_pair = reviewed_mobile_pair(contract)
    country = page.locator(f"#{COUNTRY_ID}")
    if country.count() != 1 or not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_COUNTRY_PROBE_COUNTRY_CONTROL_NOT_ACTIONABLE")
    if country.get_attribute("role") != "combobox":
        raise PermissionError("ADP_COUNTRY_PROBE_COUNTRY_ROLE_DRIFT")
    if country.get_attribute("aria-autocomplete") != "list":
        raise PermissionError("ADP_COUNTRY_PROBE_AUTOCOMPLETE_DRIFT")

    click_attempts = 1
    country.click(timeout=timeout_ms)
    page.wait_for_timeout(300)
    surface = _visible_option_surface(page)
    if surface["visible_listbox_count"] < 1 or surface["visible_option_count"] < 1:
        raise PermissionError("ADP_COUNTRY_PROBE_OPTION_SURFACE_NOT_VISIBLE")

    return {
        "canary_version": CANARY_VERSION,
        "contact_contract_fingerprint": observed_contract_fingerprint,
        "reviewed_mobile_pair": mobile_pair,
        "country_control_id": COUNTRY_ID,
        "combobox_open_click_attempts": click_attempts,
        "combobox_open_click_successes": 1,
        "option_surface": surface,
        "option_surface_fingerprint": option_surface_fingerprint(surface),
        "form_value_write_attempts": 0,
        "country_selection_attempts": 0,
        "phone_write_attempts": 0,
        "address_write_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "input_values_read": False,
        "candidate_values_exposed": False,
        "raw_values_exposed": False,
        "country_selection_allowed": False,
        "next_allowed": False,
        "final_submit_allowed": False,
    }
