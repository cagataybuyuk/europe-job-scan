"""Read-only contract for ADP post-verification contact/address controls.

The caller must pass the already verified Personal Information page. This module
does not navigate, click, fill, select, upload, submit, or read candidate input
values. It only inspects structural/accessibility metadata and value-free
geometry needed to review address controls and disambiguate duplicate phone
controls.
"""
from __future__ import annotations

import hashlib
import json

from ejs.services.adp_same_page_manifest import (
    extract_same_page_manifest,
    manifest_surface_fingerprint,
)

CONTRACT_VERSION = "adp-same-page-contact-address-contract-v1"

ADDRESS_CONTROLS = (
    ("country", "PersonalAddress_country", "Country*", True),
    ("address_line1", "PersonalAddress_address_line1", "Address Line 1*", True),
    ("address_line2", "PersonalAddress_address_line2", "Address Line 2", False),
    ("address_line3", "PersonalAddress_address_line3", "Address Line 3", False),
    ("city", "PersonalAddress_city", "City*", True),
    ("state", "PersonalAddress_state", "State / Territory*", True),
    ("postal_code", "PersonalAddress_postalCode", "Postal Code*", True),
)


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def _attr(locator, name: str) -> str:
    try:
        return str(locator.get_attribute(name) or "")[:240]
    except Exception:
        return ""


def _geometry(locator) -> dict:
    try:
        box = locator.bounding_box()
    except Exception:
        box = None
    if not isinstance(box, dict):
        return {"present": False}
    return {
        "present": True,
        "x": round(float(box.get("x", 0.0)), 1),
        "y": round(float(box.get("y", 0.0)), 1),
        "width": round(float(box.get("width", 0.0)), 1),
        "height": round(float(box.get("height", 0.0)), 1),
    }


def _structural_locator_descriptor(locator, *, kind: str, ordinal: int) -> dict:
    return {
        "kind": kind,
        "ordinal": ordinal,
        "tag": str(locator.evaluate("el => el.tagName.toLowerCase()")),
        "id": _attr(locator, "id"),
        "name": _attr(locator, "name"),
        "type": _attr(locator, "type"),
        "role": _attr(locator, "role"),
        "autocomplete": _attr(locator, "autocomplete"),
        "inputmode": _attr(locator, "inputmode"),
        "placeholder": _attr(locator, "placeholder"),
        "aria_label": _normalize(_attr(locator, "aria-label")),
        "aria_labelledby_present": bool(_attr(locator, "aria-labelledby")),
        "aria_describedby_present": bool(_attr(locator, "aria-describedby")),
        "aria_controls_present": bool(_attr(locator, "aria-controls")),
        "aria_autocomplete": _attr(locator, "aria-autocomplete"),
        "aria_expanded": _attr(locator, "aria-expanded"),
        "required": locator.get_attribute("required") is not None,
        "aria_required": _attr(locator, "aria-required"),
        "disabled": locator.is_disabled(),
        "visible": locator.is_visible(),
        "enabled": locator.is_enabled(),
        "geometry": _geometry(locator),
        "raw_value_read": False,
    }


def _manifest_control(manifest: dict, element_id: str) -> dict:
    matches = [
        row for row in manifest.get("controls", [])
        if isinstance(row, dict) and row.get("id") == element_id
    ]
    if len(matches) != 1:
        raise PermissionError(
            f"ADP_CONTACT_ADDRESS_CONTROL_NOT_UNIQUE:{element_id}"
        )
    return matches[0]


def _address_descriptor(page, manifest: dict) -> list[dict]:
    rows: list[dict] = []
    for canonical, element_id, expected_label, expected_required in ADDRESS_CONTROLS:
        manifest_row = _manifest_control(manifest, element_id)
        if manifest_row.get("label") != expected_label:
            raise PermissionError(
                f"ADP_CONTACT_ADDRESS_LABEL_DRIFT:{canonical}"
            )
        if (manifest_row.get("required") is True) != expected_required:
            raise PermissionError(
                f"ADP_CONTACT_ADDRESS_REQUIREDNESS_DRIFT:{canonical}"
            )
        locator = page.locator(f"#{element_id}")
        if locator.count() != 1:
            raise PermissionError(
                f"ADP_CONTACT_ADDRESS_RUNTIME_CONTROL_NOT_UNIQUE:{canonical}"
            )
        row = _structural_locator_descriptor(
            locator,
            kind=f"address.{canonical}",
            ordinal=0,
        )
        row.update({
            "canonical_field": f"candidate.address.{canonical}",
            "manifest_label": expected_label,
            "manifest_required": expected_required,
        })
        rows.append(row)
    return rows


def _phone_controls(page, selector: str, kind: str) -> list[dict]:
    locator = page.locator(selector)
    rows = []
    for index in range(locator.count()):
        rows.append(
            _structural_locator_descriptor(
                locator.nth(index),
                kind=kind,
                ordinal=index,
            )
        )
    return rows


def _phone_dom_distance_matrix(page) -> list[dict]:
    return page.evaluate(
        """() => {
          const selects = Array.from(document.querySelectorAll("select[name='phoneCountry']"));
          const phones = Array.from(document.querySelectorAll("input[name='phone']"));
          function chain(el) {
            const result = [];
            let node = el;
            while (node) {
              result.push(node);
              node = node.parentElement;
            }
            return result;
          }
          function distance(a, b) {
            const aa = chain(a);
            const bb = chain(b);
            for (let i = 0; i < aa.length; i++) {
              const j = bb.indexOf(aa[i]);
              if (j >= 0) return { distance: i + j, lcaDepthFromSelect: i, lcaDepthFromPhone: j };
            }
            return { distance: 9999, lcaDepthFromSelect: -1, lcaDepthFromPhone: -1 };
          }
          const rows = [];
          selects.forEach((country, ci) => {
            phones.forEach((phone, pi) => {
              rows.push({ countryOrdinal: ci, phoneOrdinal: pi, ...distance(country, phone) });
            });
          });
          return rows;
        }"""
    )


def _stable_descriptor(report: dict) -> dict:
    def stable_row(row: dict) -> dict:
        return {
            key: value
            for key, value in row.items()
            if key not in {"geometry", "ordinal"}
        }

    return {
        "contract_version": CONTRACT_VERSION,
        "manifest_surface_fingerprint": report.get("manifest_surface_fingerprint", ""),
        "address_controls": [
            stable_row(row)
            for row in report.get("address_controls", [])
        ],
        "phone_country_controls": [
            stable_row(row)
            for row in report.get("phone_country_controls", [])
        ],
        "phone_input_controls": [
            stable_row(row)
            for row in report.get("phone_input_controls", [])
        ],
        "phone_dom_distance_matrix": report.get("phone_dom_distance_matrix", []),
    }


def contract_fingerprint(report: dict) -> str:
    payload = json.dumps(
        _stable_descriptor(report),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def inspect_on_verified_page(
    page,
    application_url: str,
    expected_manifest_fingerprint: str,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> dict:
    manifest = extract_same_page_manifest(
        page,
        application_url,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    observed = manifest_surface_fingerprint(manifest)
    if observed != expected_manifest_fingerprint:
        raise PermissionError(
            "ADP_CONTACT_ADDRESS_MANIFEST_FINGERPRINT_MISMATCH"
        )

    address = _address_descriptor(page, manifest)
    phone_countries = _phone_controls(
        page,
        "select[name='phoneCountry']",
        "phone.country",
    )
    phone_inputs = _phone_controls(
        page,
        "input[name='phone']",
        "phone.input",
    )
    if len(phone_countries) != 2 or len(phone_inputs) != 2:
        raise PermissionError(
            "ADP_CONTACT_ADDRESS_PHONE_DUPLICATE_CONTRACT_DRIFT"
        )

    matrix = _phone_dom_distance_matrix(page)
    report = {
        "contract_version": CONTRACT_VERSION,
        "manifest_surface_fingerprint": observed,
        "address_controls": address,
        "phone_country_controls": phone_countries,
        "phone_input_controls": phone_inputs,
        "phone_dom_distance_matrix": matrix,
        "address_control_count": len(address),
        "phone_country_control_count": len(phone_countries),
        "phone_input_control_count": len(phone_inputs),
        "navigation_click_attempts": 0,
        "form_value_write_attempts": 0,
        "phone_write_attempts": 0,
        "address_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "next_click_attempts": 0,
        "candidate_values_exposed": False,
        "raw_values_exposed": False,
        "input_values_read": False,
        "safe_fill_allowed": False,
        "next_allowed": False,
        "final_submit_allowed": False,
    }
    report["contract_fingerprint"] = contract_fingerprint(report)
    return report
