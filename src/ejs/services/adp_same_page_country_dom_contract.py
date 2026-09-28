"""Read-only DOM interaction contract for the ADP address Country combobox.

The caller passes the already verified Personal Information page. This probe
may open the reviewed Country combobox once, but it does not select an option,
read candidate input values, or mutate any candidate data. It captures only
value-free structural metadata needed to identify the real interactive/state
elements behind the custom combobox.
"""
from __future__ import annotations

import hashlib
import json

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

CONTRACT_VERSION = "adp-same-page-country-dom-contract-v1"
REVIEWED_COUNTRY_LABEL = "Turkey"


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def _element_metadata(locator) -> dict:
    return locator.evaluate(
        """el => {
          const attr = (name) => el.getAttribute(name) || '';
          const dataAttributeNames = Array.from(el.attributes)
            .map(a => a.name)
            .filter(name => name.startsWith('data-'))
            .sort();
          return {
            tag: el.tagName.toLowerCase(),
            id: attr('id'),
            name: attr('name'),
            type: attr('type'),
            role: attr('role'),
            readonly: el.hasAttribute('readonly'),
            disabled_attribute: el.hasAttribute('disabled'),
            tabindex: attr('tabindex'),
            class_name: String(el.className || '').slice(0, 300),
            aria_expanded: attr('aria-expanded'),
            aria_controls: attr('aria-controls'),
            aria_activedescendant: attr('aria-activedescendant'),
            aria_autocomplete: attr('aria-autocomplete'),
            aria_selected: attr('aria-selected'),
            aria_haspopup: attr('aria-haspopup'),
            aria_owns: attr('aria-owns'),
            contenteditable: attr('contenteditable'),
            data_attribute_names: dataAttributeNames,
            value_attribute_read: false,
            property_value_read: false,
          };
        }"""
    )


def _parent_chain(locator, depth: int = 5) -> list[dict]:
    return locator.evaluate(
        """(el, maxDepth) => {
          const rows = [];
          let node = el.parentElement;
          for (let i = 0; i < maxDepth && node; i++, node = node.parentElement) {
            const attr = (name) => node.getAttribute(name) || '';
            rows.push({
              depth: i + 1,
              tag: node.tagName.toLowerCase(),
              id: attr('id'),
              role: attr('role'),
              class_name: String(node.className || '').slice(0, 300),
              aria_live: attr('aria-live'),
              aria_hidden: attr('aria-hidden'),
              data_attribute_names: Array.from(node.attributes)
                .map(a => a.name)
                .filter(name => name.startsWith('data-'))
                .sort(),
            });
          }
          return rows;
        }""",
        depth,
    )


def _nearby_controls(locator) -> list[dict]:
    return locator.evaluate(
        """el => {
          const root = el.parentElement && el.parentElement.parentElement
            ? el.parentElement.parentElement
            : el.parentElement;
          if (!root) return [];
          return Array.from(root.querySelectorAll('input,select,textarea,button,[role="combobox"],[role="listbox"]'))
            .slice(0, 40)
            .map((node, ordinal) => {
              const attr = (name) => node.getAttribute(name) || '';
              return {
                ordinal,
                tag: node.tagName.toLowerCase(),
                id: attr('id'),
                name: attr('name'),
                type: attr('type'),
                role: attr('role'),
                readonly: node.hasAttribute('readonly'),
                disabled_attribute: node.hasAttribute('disabled'),
                hidden_attribute: node.hasAttribute('hidden'),
                aria_hidden: attr('aria-hidden'),
                aria_expanded: attr('aria-expanded'),
                class_name: String(node.className || '').slice(0, 240),
                value_attribute_read: false,
                property_value_read: false,
              };
            });
        }"""
    )


def _exact_visible_option(page, label: str):
    options = page.locator("[role='option']:visible")
    matches = []
    for index in range(options.count()):
        option = options.nth(index)
        try:
            if option.is_visible() and _normalize(option.inner_text() or "") == label:
                matches.append(option)
        except Exception:
            continue
    if len(matches) != 1:
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_OPTION_NOT_UNIQUE")
    return matches[0]


def _stable_descriptor(report: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "contact_contract_fingerprint": report.get("contact_contract_fingerprint", ""),
        "option_surface_fingerprint": report.get("option_surface_fingerprint", ""),
        "country_control": report.get("country_control", {}),
        "country_parent_chain": report.get("country_parent_chain", []),
        "country_nearby_controls": report.get("country_nearby_controls", []),
        "reviewed_option": report.get("reviewed_option", {}),
        "option_parent_chain": report.get("option_parent_chain", []),
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
    expected_contact_contract_fingerprint: str,
    expected_option_surface_fingerprint: str,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> dict:
    contact = inspect_contact_address_on_verified_page(
        page,
        application_url,
        expected_manifest_fingerprint,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    observed_contact = contact_contract_fingerprint(contact)
    if observed_contact != expected_contact_contract_fingerprint:
        raise PermissionError(
            "ADP_COUNTRY_DOM_CONTRACT_CONTACT_FINGERPRINT_MISMATCH"
        )
    mobile_pair = reviewed_mobile_pair(contact)

    country = page.locator(f"#{COUNTRY_ID}")
    if country.count() != 1 or not country.is_visible() or not country.is_enabled():
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_CONTROL_NOT_ACTIONABLE")
    if country.get_attribute("role") != "combobox":
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_ROLE_DRIFT")
    if country.get_attribute("aria-autocomplete") != "list":
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_AUTOCOMPLETE_DRIFT")

    country_metadata = _element_metadata(country)
    country_parent_chain = _parent_chain(country)
    country_nearby_controls = _nearby_controls(country)

    country.click(timeout=timeout_ms)
    page.wait_for_timeout(300)
    surface = _visible_option_surface(page)
    observed_surface = option_surface_fingerprint(surface)
    if observed_surface != expected_option_surface_fingerprint:
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_OPTION_SURFACE_DRIFT")
    if surface.get("turkey_candidate_count") != 1:
        raise PermissionError("ADP_COUNTRY_DOM_CONTRACT_TURKEY_CANDIDATE_DRIFT")

    option = _exact_visible_option(page, REVIEWED_COUNTRY_LABEL)
    option_metadata = _element_metadata(option)
    option_parent_chain = _parent_chain(option)

    active = page.evaluate(
        """() => {
          const el = document.activeElement;
          if (!el) return {present: false};
          const attr = (name) => el.getAttribute(name) || '';
          return {
            present: true,
            tag: el.tagName.toLowerCase(),
            id: attr('id'),
            role: attr('role'),
            type: attr('type'),
            readonly: el.hasAttribute('readonly'),
            aria_activedescendant: attr('aria-activedescendant'),
            value_attribute_read: false,
            property_value_read: false,
          };
        }"""
    )

    report = {
        "contract_version": CONTRACT_VERSION,
        "contact_contract_fingerprint": observed_contact,
        "option_surface_fingerprint": observed_surface,
        "reviewed_mobile_pair": mobile_pair,
        "country_control": country_metadata,
        "country_parent_chain": country_parent_chain,
        "country_nearby_controls": country_nearby_controls,
        "reviewed_option": option_metadata,
        "option_parent_chain": option_parent_chain,
        "active_element_after_open": active,
        "combobox_open_click_attempts": 1,
        "form_value_write_attempts": 0,
        "country_selection_attempts": 0,
        "phone_write_attempts": 0,
        "address_text_write_attempts": 0,
        "consent_action_attempts": 0,
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "candidate_values_read": False,
        "raw_values_exposed": False,
    }
    report["contract_fingerprint"] = contract_fingerprint(report)
    return report
