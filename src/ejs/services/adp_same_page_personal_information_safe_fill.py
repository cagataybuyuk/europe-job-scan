"""Bounded same-page ADP Personal Information safe-fill.

Runs only on the already verified Personal Information page. It composes the
reviewed identity safe-fill with the reviewed post-login contact/address
contracts. Next, upload, consent, Home Phone and Final Submit remain forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from ejs.contracts.prefill import value_hash
from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_personal_information_profile import (
    resolve_personal_information_profile,
)
from ejs.services.adp_profile_v2_continue_canary import (
    phone_readback_semantic_match,
    phone_readback_shape,
)
from ejs.services.adp_same_page_contact_address_contract import (
    contract_fingerprint as contact_contract_fingerprint,
    inspect_on_verified_page as inspect_contact_address_on_verified_page,
)
from ejs.services.adp_same_page_country_combobox_probe import (
    _visible_option_surface,
    option_surface_fingerprint,
    reviewed_mobile_pair,
)
from ejs.services.adp_same_page_safe_fill import (
    AdpSamePageSafeFillRequest,
    run_on_verified_page as run_identity_safe_fill,
)
from ejs.services.adp_same_page_state_surface import (
    STATE_ID,
    normalize_state_label as _normalize_label,
    snapshot_state as _snapshot_state,
    state_option_surface as _state_option_surface,
)

EXECUTOR_VERSION = "adp-same-page-personal-information-safe-fill-v1"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
COUNTRY_ID = "PersonalAddress_country"
REVIEWED_ADDRESS_COUNTRY_ISO2 = "TR"
REVIEWED_ADDRESS_COUNTRY_LABEL = "Turkey"

ADDRESS_TEXT_FIELDS = (
    ("candidate.address.address_line1", "PersonalAddress_address_line1", "address_line1", True),
    ("candidate.address.address_line2", "PersonalAddress_address_line2", "address_line2", False),
    ("candidate.address.address_line3", "PersonalAddress_address_line3", "address_line3", False),
    ("candidate.address.city", "PersonalAddress_city", "city", True),
    ("candidate.address.postal_code", "PersonalAddress_postalCode", "postal_code", True),
)


@dataclass(frozen=True)
class AdpSamePagePersonalInformationSafeFillRequest:
    application_url: str
    expected_manifest_fingerprint: str
    expected_contact_contract_fingerprint: str
    expected_country_option_surface_fingerprint: str
    expected_state_option_surface_fingerprint: str
    profile_json_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 5_000
    allow_reviewed_turkish_ascii_name_overwrite: bool = False


def validate_request(request: AdpSamePagePersonalInformationSafeFillRequest) -> None:
    validate_adp_live_url(request.application_url)
    for value, code in (
        (
            request.expected_manifest_fingerprint,
            "ADP_PERSONAL_INFO_INVALID_MANIFEST_FINGERPRINT",
        ),
        (
            request.expected_contact_contract_fingerprint,
            "ADP_PERSONAL_INFO_INVALID_CONTACT_CONTRACT_FINGERPRINT",
        ),
        (
            request.expected_country_option_surface_fingerprint,
            "ADP_PERSONAL_INFO_INVALID_COUNTRY_SURFACE_FINGERPRINT",
        ),
        (
            request.expected_state_option_surface_fingerprint,
            "ADP_PERSONAL_INFO_INVALID_STATE_SURFACE_FINGERPRINT",
        ),
    ):
        if not FINGERPRINT_RE.fullmatch(value):
            raise ValueError(code)
    if not request.profile_json_path:
        raise ValueError("ADP_PERSONAL_INFO_PROFILE_REQUIRED")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("ADP_PERSONAL_INFO_INVALID_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("ADP_PERSONAL_INFO_INVALID_RENDER_WAIT")


def _load_profile(path: str):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ADP_PERSONAL_INFO_PROFILE_JSON_INVALID")
    return resolve_personal_information_profile(raw)


def _fill_blank_or_verify(locator, desired: str, canonical: str, counters: dict) -> dict:
    if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
        raise PermissionError(f"ADP_PERSONAL_INFO_CONTROL_NOT_ACTIONABLE:{canonical}")

    before = locator.input_value()
    if before and before != desired:
        raise PermissionError(f"ADP_PERSONAL_INFO_PROFILE_CONFLICT:{canonical}")

    executed = False
    if not before and desired:
        counters["form_value_write_attempts"] += 1
        counters["address_write_attempts"] += 1
        locator.fill(desired)
        counters["form_value_write_successes"] += 1
        counters["address_write_successes"] += 1
        executed = True

    after = locator.input_value()
    if after != desired:
        raise PermissionError(f"ADP_PERSONAL_INFO_READBACK_MISMATCH:{canonical}")
    if desired and not bool(locator.evaluate("el => el.checkValidity()")):
        raise PermissionError(f"ADP_PERSONAL_INFO_BROWSER_VALIDITY_FAILED:{canonical}")

    return {
        "canonical_field": canonical,
        "executed": executed,
        "value_hash": value_hash(desired) if desired else "",
        "readback_match": True,
        "valid": True,
        "raw_value_exposed": False,
    }


def _unique_visible_option(page, label: str):
    options = page.locator("[role='option']:visible")
    matches = []
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if option.is_visible() and " ".join((option.inner_text() or "").split()) == label:
                matches.append(option)
        except Exception:
            continue
    if len(matches) != 1:
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_OPTION_NOT_UNIQUE")
    if matches[0].is_disabled():
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_OPTION_DISABLED")
    return matches[0]


def _selected_country_label_evidence(country) -> dict:
    try:
        result = country.evaluate(
            """el => {
              const valueContainer = el.parentElement && el.parentElement.parentElement
                ? el.parentElement.parentElement
                : null;
              if (!valueContainer) return {present: false, text: ''};
              const node = valueContainer.querySelector(
                '.MDFSelectBox__single-value, [class*="__single-value"]'
              );
              if (!node) return {present: false, text: ''};
              return {
                present: true,
                text: (node.textContent || '').replace(/\\s+/g, ' ').trim(),
              };
            }"""
        )
    except Exception:
        result = {"present": False, "text": ""}
    if not isinstance(result, dict):
        result = {"present": False, "text": ""}
    text = str(result.get("text", "") or "")
    normalized = " ".join(text.split())
    return {
        "present": result.get("present") is True,
        "reviewed_label_match": (
            normalized.casefold() == REVIEWED_ADDRESS_COUNTRY_LABEL.casefold()
        ),
        "label_hash": value_hash(normalized) if normalized else "",
        "raw_value_exposed": False,
    }


def _country_readback_evidence(page, country) -> dict:
    value = country.input_value()
    normalized = " ".join(value.split())
    input_label_match = normalized.casefold() == REVIEWED_ADDRESS_COUNTRY_LABEL.casefold()
    input_iso2_match = normalized.upper() == REVIEWED_ADDRESS_COUNTRY_ISO2
    nonempty = bool(normalized)
    selected_label = _selected_country_label_evidence(country)
    try:
        valid = bool(country.evaluate("el => el.checkValidity()"))
    except Exception:
        valid = False
    try:
        aria_expanded = str(country.get_attribute("aria-expanded") or "").casefold()
    except Exception:
        aria_expanded = ""
    visible_listboxes = int(page.locator("[role='listbox']:visible").count())
    visible_options = int(page.locator("[role='option']:visible").count())
    committed_custom_value = (
        nonempty
        and valid
        and aria_expanded in {"", "false"}
        and visible_listboxes == 0
        and visible_options == 0
    )
    selected_label_committed = (
        selected_label["present"]
        and selected_label["reviewed_label_match"]
        and valid
        and aria_expanded in {"", "false"}
        and visible_listboxes == 0
        and visible_options == 0
    )
    if selected_label_committed:
        mode = "selected_label"
    elif input_label_match:
        mode = "label"
    elif input_iso2_match:
        mode = "iso2"
    elif committed_custom_value:
        mode = "custom_committed"
    elif not nonempty:
        mode = "empty"
    else:
        mode = "mismatch"
    return {
        "mode": mode,
        "nonempty": nonempty,
        "browser_valid": valid,
        "aria_expanded": aria_expanded,
        "visible_listbox_count": visible_listboxes,
        "visible_option_count": visible_options,
        "selected_label_present": selected_label["present"],
        "selected_label_match": selected_label["reviewed_label_match"],
        "selected_label_hash": selected_label["label_hash"],
        "raw_value_exposed": False,
    }


def _set_address_country(
    page,
    profile,
    request,
    counters: dict,
) -> dict:
    if profile.address_country_iso2 != REVIEWED_ADDRESS_COUNTRY_ISO2:
        raise PermissionError("ADP_PERSONAL_INFO_ADDRESS_COUNTRY_NOT_REVIEWED")

    country = page.locator(f"#{COUNTRY_ID}")
    if country.count() != 1 or not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_CONTROL_NOT_ACTIONABLE")
    if country.get_attribute("role") != "combobox":
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_ROLE_DRIFT")
    if country.get_attribute("aria-autocomplete") != "list":
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_AUTOCOMPLETE_DRIFT")

    before = country.input_value()
    if before:
        before_evidence = _country_readback_evidence(page, country)
        if before_evidence["mode"] in {"selected_label", "label", "iso2"}:
            return {
                "executed": False,
                "country_iso2": REVIEWED_ADDRESS_COUNTRY_ISO2,
                "country_label": REVIEWED_ADDRESS_COUNTRY_LABEL,
                "readback_match": True,
                "readback_mode": before_evidence["mode"],
                "readback_evidence": before_evidence,
                "option_surface_fingerprint": request.expected_country_option_surface_fingerprint,
            }
        raise PermissionError("ADP_PERSONAL_INFO_PROFILE_CONFLICT:candidate.address.country")

    counters["combobox_open_click_attempts"] += 1
    country.click(timeout=request.timeout_ms)
    counters["combobox_open_click_successes"] += 1
    page.wait_for_timeout(300)

    surface = _visible_option_surface(page)
    observed_surface = option_surface_fingerprint(surface)
    if observed_surface != request.expected_country_option_surface_fingerprint:
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_OPTION_SURFACE_DRIFT")
    if surface.get("turkey_candidate_count") != 1:
        raise PermissionError("ADP_PERSONAL_INFO_COUNTRY_TURKEY_CANDIDATE_DRIFT")

    option = _unique_visible_option(page, REVIEWED_ADDRESS_COUNTRY_LABEL)
    counters["country_selection_attempts"] += 1
    counters["address_write_attempts"] += 1
    counters["form_value_write_attempts"] += 1
    option.click(timeout=request.timeout_ms)

    readback = None
    for _ in range(20):
        page.wait_for_timeout(150)
        readback = _country_readback_evidence(page, country)
        if readback["mode"] in {"selected_label", "label", "iso2", "custom_committed"}:
            break
    if not isinstance(readback, dict) or readback["mode"] not in {
        "selected_label",
        "label",
        "iso2",
        "custom_committed",
    }:
        mode = readback.get("mode", "unknown") if isinstance(readback, dict) else "unknown"
        raise PermissionError(
            f"ADP_PERSONAL_INFO_COUNTRY_READBACK_MISMATCH:{mode}"
        )

    counters["country_selection_successes"] += 1
    counters["address_write_successes"] += 1
    counters["form_value_write_successes"] += 1

    return {
        "executed": True,
        "country_iso2": REVIEWED_ADDRESS_COUNTRY_ISO2,
        "country_label": REVIEWED_ADDRESS_COUNTRY_LABEL,
        "readback_match": True,
        "readback_mode": readback["mode"],
        "readback_evidence": readback,
        "option_surface_fingerprint": observed_surface,
    }


def _select_address_state(page, desired: str, request, counters: dict) -> dict:
    reviewed = _normalize_label(desired)
    if not reviewed:
        raise PermissionError("ADP_PERSONAL_INFO_STATE_LABEL_EMPTY")

    page.wait_for_timeout(750)
    post_state = _snapshot_state(page)
    surface = _state_option_surface(
        page,
        post_state,
        timeout_ms=request.timeout_ms,
    )
    observed_surface = str(surface.get("surface_fingerprint", "") or "")
    if observed_surface != request.expected_state_option_surface_fingerprint:
        raise PermissionError("ADP_PERSONAL_INFO_STATE_OPTION_SURFACE_DRIFT")
    if surface.get("unique_nonempty_labels") is not True:
        raise PermissionError("ADP_PERSONAL_INFO_STATE_OPTION_LABEL_DRIFT")

    reviewed_rows = [
        row for row in surface.get("options", [])
        if _normalize_label(row.get("label", "")) == reviewed
        and row.get("disabled") is not True
    ]
    if len(reviewed_rows) != 1:
        raise PermissionError(
            f"ADP_PERSONAL_INFO_STATE_REVIEWED_LABEL_COUNT:{len(reviewed_rows)}"
        )

    state = page.locator(f"#{STATE_ID}")
    if state.count() != 1 or not state.is_visible() or not state.is_enabled():
        raise PermissionError("ADP_PERSONAL_INFO_STATE_CONTROL_NOT_ACTIONABLE")
    if state.get_attribute("role") != "combobox":
        raise PermissionError("ADP_PERSONAL_INFO_STATE_ROLE_DRIFT")
    if state.get_attribute("aria-controls") != f"{STATE_ID}__listbox":
        raise PermissionError("ADP_PERSONAL_INFO_STATE_LISTBOX_ID_DRIFT")

    before = _normalize_label(state.inner_text() or "")
    if before:
        if before == reviewed:
            return {
                "executed": False,
                "reviewed_state_label": reviewed,
                "option_surface_fingerprint": observed_surface,
                "readback_match": True,
                "raw_value_exposed": False,
            }
        raise PermissionError("ADP_PERSONAL_INFO_PROFILE_CONFLICT:candidate.address.state")

    counters["combobox_open_click_attempts"] += 1
    state.click(timeout=request.timeout_ms)
    counters["combobox_open_click_successes"] += 1
    page.wait_for_timeout(200)

    listbox = page.locator(f"#{STATE_ID}__listbox")
    if listbox.count() != 1 or not listbox.is_visible():
        raise PermissionError("ADP_PERSONAL_INFO_STATE_LISTBOX_NOT_VISIBLE")
    options = listbox.locator("[role='option']")
    live_matches = []
    for index in range(int(options.count())):
        option = options.nth(index)
        if not option.is_visible() or option.is_disabled():
            continue
        if _normalize_label(option.inner_text() or "") == reviewed:
            live_matches.append(option)
    if len(live_matches) != 1:
        try:
            state.press("Escape", timeout=request.timeout_ms)
        finally:
            raise PermissionError(
                f"ADP_PERSONAL_INFO_STATE_LIVE_LABEL_COUNT:{len(live_matches)}"
            )

    counters["state_selection_attempts"] += 1
    counters["form_value_write_attempts"] += 1
    counters["address_write_attempts"] += 1
    live_matches[0].click(timeout=request.timeout_ms)
    counters["state_selection_successes"] += 1
    counters["form_value_write_successes"] += 1
    counters["address_write_successes"] += 1

    page.wait_for_timeout(250)
    after = _normalize_label(state.inner_text() or "")
    if after != reviewed:
        raise PermissionError("ADP_PERSONAL_INFO_STATE_READBACK_MISMATCH")

    return {
        "executed": True,
        "reviewed_state_label": reviewed,
        "option_surface_fingerprint": observed_surface,
        "readback_match": True,
        "raw_value_exposed": False,
    }


def _write_mobile_phone(page, profile, contract: dict, counters: dict) -> dict:
    pair = reviewed_mobile_pair(contract)
    if pair.get("country_ordinal") != 0 or pair.get("phone_ordinal") != 0:
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_PAIR_DRIFT")

    countries = page.locator("select[name='phoneCountry']")
    phones = page.locator("input[name='phone']")
    if countries.count() != 2 or phones.count() != 2:
        raise PermissionError("ADP_PERSONAL_INFO_PHONE_DUPLICATE_CONTRACT_DRIFT")
    country = countries.nth(0)
    phone = phones.nth(0)
    if not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_COUNTRY_NOT_ACTIONABLE")
    if not phone.is_visible() or not phone.is_enabled():
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_PHONE_NOT_ACTIONABLE")

    iso2 = profile.identity_phone.phone_country_iso2
    desired = profile.identity_phone.phone_national_number
    option = country.locator(f"option[value='{iso2}']")
    if option.count() != 1 or option.is_disabled():
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_COUNTRY_OPTION_NOT_ACTIONABLE")

    country_before = country.input_value()
    country_executed = country_before != iso2
    if country_executed:
        counters["form_value_write_attempts"] += 1
        counters["phone_write_attempts"] += 1
        country.select_option(value=iso2)
        counters["form_value_write_successes"] += 1
        counters["phone_write_successes"] += 1
    if country.input_value() != iso2:
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_COUNTRY_READBACK_MISMATCH")

    phone_before = phone.input_value()
    before_match, before_mode = phone_readback_semantic_match(
        phone_before,
        desired,
        iso2,
    ) if phone_before else (False, "blank")
    phone_executed = False
    if phone_before and not before_match:
        shape = phone_readback_shape(phone_before, desired, iso2)
        raise PermissionError(
            "ADP_PERSONAL_INFO_PROFILE_CONFLICT:candidate.phone:"
            + ("formatted_mismatch" if shape.get("digit_count") else "other")
        )
    if not phone_before:
        counters["form_value_write_attempts"] += 1
        counters["phone_write_attempts"] += 1
        phone.fill(desired)
        counters["form_value_write_successes"] += 1
        counters["phone_write_successes"] += 1
        phone_executed = True

    phone_after = phone.input_value()
    after_match, after_mode = phone_readback_semantic_match(phone_after, desired, iso2)
    if not after_match:
        raise PermissionError("ADP_PERSONAL_INFO_MOBILE_PHONE_READBACK_MISMATCH")

    return {
        "reviewed_pair": pair,
        "country_iso2": iso2,
        "country_write_executed": country_executed,
        "country_readback_match": True,
        "phone_write_executed": phone_executed,
        "phone_value_hash": value_hash(desired),
        "phone_digit_count": len(desired),
        "phone_readback_match": True,
        "phone_readback_mode_before_write": before_mode,
        "phone_readback_mode_after_write": after_mode,
        "home_phone_touched": False,
        "raw_value_exposed": False,
    }


def run_on_verified_page(
    page,
    request: AdpSamePagePersonalInformationSafeFillRequest,
) -> dict:
    validate_request(request)
    profile = _load_profile(request.profile_json_path)

    identity = run_identity_safe_fill(
        page,
        AdpSamePageSafeFillRequest(
            application_url=request.application_url,
            expected_manifest_fingerprint=request.expected_manifest_fingerprint,
            profile_json_path=request.profile_json_path,
            timeout_ms=request.timeout_ms,
            render_wait_ms=request.render_wait_ms,
            allow_reviewed_turkish_ascii_name_overwrite=(
                request.allow_reviewed_turkish_ascii_name_overwrite
            ),
        ),
    )

    contract = inspect_contact_address_on_verified_page(
        page,
        request.application_url,
        request.expected_manifest_fingerprint,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    observed_contract = contact_contract_fingerprint(contract)
    if observed_contract != request.expected_contact_contract_fingerprint:
        raise PermissionError("ADP_PERSONAL_INFO_CONTACT_CONTRACT_FINGERPRINT_MISMATCH")

    counters = {
        "form_value_write_attempts": int(identity.get("form_value_write_attempts", 0)),
        "form_value_write_successes": int(identity.get("form_value_write_successes", 0)),
        "address_write_attempts": 0,
        "address_write_successes": 0,
        "phone_write_attempts": 0,
        "phone_write_successes": 0,
        "combobox_open_click_attempts": 0,
        "combobox_open_click_successes": 0,
        "country_selection_attempts": 0,
        "country_selection_successes": 0,
        "state_selection_attempts": 0,
        "state_selection_successes": 0,
    }

    phone_result = _write_mobile_phone(page, profile, contract, counters)
    country_result = _set_address_country(page, profile, request, counters)
    state_result = _select_address_state(
        page,
        profile.state_or_territory,
        request,
        counters,
    )

    address_results = []
    for canonical, element_id, attr_name, _required in ADDRESS_TEXT_FIELDS:
        desired = getattr(profile, attr_name)
        address_results.append(
            _fill_blank_or_verify(
                page.locator(f"#{element_id}"),
                desired,
                canonical,
                counters,
            )
        )

    return {
        "executor_version": EXECUTOR_VERSION,
        "safe_fill_status": "verified",
        "expected_manifest_fingerprint": request.expected_manifest_fingerprint,
        "expected_contact_contract_fingerprint": request.expected_contact_contract_fingerprint,
        "observed_contact_contract_fingerprint": observed_contract,
        "post_write_contact_contract_fingerprint": "",
        "post_write_contact_reinspection_skipped_for_reviewed_state_rerender": True,
        "expected_country_option_surface_fingerprint": (
            request.expected_country_option_surface_fingerprint
        ),
        "expected_state_option_surface_fingerprint": (
            request.expected_state_option_surface_fingerprint
        ),
        "profile_evidence": profile.non_secret_evidence(),
        "identity_result": {
            "safe_fill_status": identity.get("safe_fill_status", ""),
            "form_value_write_attempts": identity.get("form_value_write_attempts", 0),
            "form_value_write_successes": identity.get("form_value_write_successes", 0),
            "email_readback_match": (
                identity.get("email_readback_only", {}).get("readback_match") is True
            ),
        },
        "mobile_phone_result": phone_result,
        "address_country_result": country_result,
        "address_state_result": state_result,
        "address_field_results": address_results,
        **counters,
        "home_phone_write_attempts": 0,
        "consent_action_attempts": 0,
        "navigation_click_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "further_navigation_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
        "raw_values_exposed": False,
    }
