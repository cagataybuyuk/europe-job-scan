"""Shared value-safe helpers for the ADP State / Territory surface.

This module has no dependency on Personal Information mutation code. It may
inspect the State wrapper and visible option labels, but never reads option
value attributes/properties and never selects a State.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

from ejs.services.adp_same_page_state_dom_contract import (
    STATE_ID,
    _ancestor_neighborhood,
    _descendants,
    _metadata,
    _parent_chain,
    _sibling_metadata,
)


def normalize_state_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def snapshot_state(page) -> dict:
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


def _read_state_options(page, listbox_id: str) -> dict:
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
        rows.append({
            "ordinal": index,
            "label": label,
            "label_empty": not bool(label),
            "disabled": option.is_disabled(),
            "id": str(option.get_attribute("id") or "")[:180],
            "role": str(option.get_attribute("role") or "")[:40],
            "value_attribute_read": False,
            "property_value_read": False,
        })

    if not rows:
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_VISIBLE_OPTIONS_MISSING")
    labels = [row["label"] for row in rows]
    nonempty_labels = [label for label in labels if label]
    surface = {
        "listbox_id": listbox_id,
        "visible_option_count": len(rows),
        "nonempty_label_count": len(nonempty_labels),
        "empty_label_count": len(labels) - len(nonempty_labels),
        "options": rows,
        "unique_visible_labels": len(set(labels)) == len(labels),
        "unique_nonempty_labels": len(set(nonempty_labels)) == len(nonempty_labels),
        "label_observation_complete": len(nonempty_labels) == len(labels),
        "state_selection_authorized": False,
        "candidate_values_read": False,
        "raw_candidate_values_exposed": False,
    }
    payload = json.dumps(
        surface,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    surface["surface_fingerprint"] = hashlib.sha256(payload).hexdigest()
    return surface


def state_option_surface(page, post_state: dict, *, timeout_ms: int) -> dict:
    metadata = post_state.get("metadata", {})
    if metadata.get("role") != "combobox":
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_POST_STATE_NOT_COMBOBOX")
    listbox_id = str(metadata.get("aria_controls", "") or "")
    if listbox_id != f"{STATE_ID}__listbox":
        raise PermissionError("ADP_STATE_AFTER_COUNTRY_LISTBOX_ID_DRIFT")

    state = page.locator(f"#{STATE_ID}")
    state.click(timeout=timeout_ms)
    try:
        surface = _read_state_options(page, listbox_id)
    except Exception:
        try:
            state.press("Escape", timeout=timeout_ms)
        except Exception:
            pass
        raise

    state.press("Escape", timeout=timeout_ms)
    page.wait_for_timeout(100)
    surface["close_escape_attempts"] = 1
    return surface
