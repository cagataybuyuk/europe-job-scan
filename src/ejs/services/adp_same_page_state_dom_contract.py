"""Read-only DOM contract for the ADP State / Territory control.

The caller passes the already verified Personal Information page. This probe
does not read candidate values or mutate any candidate data. It inspects the
wrapper identified by #PersonalAddress_state, its interactive descendants, and
value-free structural metadata needed to determine whether State/Territory is
another custom select surface.
"""
from __future__ import annotations

import hashlib
import json

from ejs.services.adp_same_page_contact_address_contract import (
    contract_fingerprint as contact_contract_fingerprint,
    inspect_on_verified_page as inspect_contact_address_on_verified_page,
)

CONTRACT_VERSION = "adp-same-page-state-dom-contract-v1"
STATE_ID = "PersonalAddress_state"


def _metadata(locator) -> dict:
    return locator.evaluate(
        """el => {
          const attr = (name) => el.getAttribute(name) || '';
          return {
            tag: el.tagName.toLowerCase(),
            id: attr('id'),
            name: attr('name'),
            type: attr('type'),
            role: attr('role'),
            readonly: el.hasAttribute('readonly'),
            disabled_attribute: el.hasAttribute('disabled'),
            hidden_attribute: el.hasAttribute('hidden'),
            tabindex: attr('tabindex'),
            class_name: String(el.className || '').slice(0, 320),
            aria_expanded: attr('aria-expanded'),
            aria_controls: attr('aria-controls'),
            aria_activedescendant: attr('aria-activedescendant'),
            aria_autocomplete: attr('aria-autocomplete'),
            aria_haspopup: attr('aria-haspopup'),
            aria_selected: attr('aria-selected'),
            data_attribute_names: Array.from(el.attributes)
              .map(a => a.name)
              .filter(name => name.startsWith('data-'))
              .sort(),
            value_attribute_read: false,
            property_value_read: false,
          };
        }"""
    )


def _descendants(wrapper) -> list[dict]:
    return wrapper.evaluate(
        """el => Array.from(
          el.querySelectorAll(
            'input,select,textarea,button,[role="combobox"],[role="listbox"],[role="option"]'
          )
        ).slice(0, 60).map((node, ordinal) => {
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
            tabindex: attr('tabindex'),
            class_name: String(node.className || '').slice(0, 280),
            aria_expanded: attr('aria-expanded'),
            aria_controls: attr('aria-controls'),
            aria_activedescendant: attr('aria-activedescendant'),
            aria_autocomplete: attr('aria-autocomplete'),
            aria_haspopup: attr('aria-haspopup'),
            aria_selected: attr('aria-selected'),
            value_attribute_read: false,
            property_value_read: false,
          };
        })"""
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


def _stable_descriptor(report: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "contact_contract_fingerprint": report.get("contact_contract_fingerprint", ""),
        "wrapper": report.get("wrapper", {}),
        "descendants": report.get("descendants", []),
        "wrapper_parent_chain": report.get("wrapper_parent_chain", []),
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
            "ADP_STATE_DOM_CONTRACT_CONTACT_FINGERPRINT_MISMATCH"
        )

    wrapper = page.locator(f"#{STATE_ID}")
    if wrapper.count() != 1 or not wrapper.is_visible():
        raise PermissionError("ADP_STATE_DOM_CONTRACT_WRAPPER_NOT_VISIBLE")

    wrapper_metadata = _metadata(wrapper)
    descendants = _descendants(wrapper)
    parent_chain = _parent_chain(wrapper)

    interactive = [
        row for row in descendants
        if row.get("tag") in {"input", "select", "textarea", "button"}
        or row.get("role") == "combobox"
    ]
    if not interactive:
        raise PermissionError("ADP_STATE_DOM_CONTRACT_INTERACTIVE_DESCENDANT_MISSING")

    report = {
        "contract_version": CONTRACT_VERSION,
        "contact_contract_fingerprint": observed_contact,
        "wrapper": wrapper_metadata,
        "descendants": descendants,
        "wrapper_parent_chain": parent_chain,
        "interactive_descendant_count": len(interactive),
        "form_value_write_attempts": 0,
        "state_selection_attempts": 0,
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
