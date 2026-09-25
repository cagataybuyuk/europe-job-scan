"""Read-only ADP application manifest extracted from the already verified page.

This module never navigates, clicks, fills, uploads, enters credentials, or
submits. It accepts only an already authenticated ADP Personal Information
surface that is still bound to the reviewed cid/ccId/jobId target.
"""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from ejs.services.adp_live_inspector import validate_adp_live_url, visible_application_controls
from ejs.services.adp_navigation_canary import _snapshot

MANIFEST_VERSION = "adp-same-page-manifest-v3"
FINGERPRINT_VERSION = "adp-same-page-surface-fingerprint-v3"
REVIEWED_POSTLOGIN_PATH = "/mascsr/applicant/mdf/recruitment/postLogin.html"
STEP_LABELS = (
    ("personal_information", "Personal Information"),
    ("resume", "Resume"),
    ("questions", "Questions"),
    ("review_application", "Review Your Application"),
    ("self_attest_submit", "Self-Attest & Submit"),
)


def _normalize(value: str) -> str:
    return " ".join((value or "").split())


def _query_values(query: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, values in parse_qs(query, keep_blank_values=True).items():
        result.setdefault(key.casefold(), []).extend(values)
    return result


def _target_binding(page_url: str, application_url: str) -> dict:
    validate_adp_live_url(page_url)
    validate_adp_live_url(application_url)
    current = urlsplit(page_url)
    expected = urlsplit(application_url)
    evidence = {
        "reviewed_origin_valid": current.hostname == expected.hostname,
        "postlogin_path_match": current.path == REVIEWED_POSTLOGIN_PATH,
        "cid_match": False,
        "ccid_match": False,
        "jobid_match": False,
        "target_bound": False,
        "raw_target_values_exposed": False,
    }
    if not evidence["reviewed_origin_valid"] or not evidence["postlogin_path_match"]:
        return evidence

    actual_query = _query_values(current.query)
    expected_query = _query_values(expected.query)
    for key in ("cid", "ccid", "jobid"):
        target_values = expected_query.get(key, [])
        actual_values = actual_query.get(key, [])
        target_unique = {value for value in target_values if value}
        actual_unique = {value for value in actual_values if value}
        evidence[f"{key}_match"] = (
            len(target_unique) == 1
            and len(actual_values) >= 1
            and len(actual_unique) == 1
            and actual_unique == target_unique
            and all(bool(value) for value in actual_values)
        )
    evidence["target_bound"] = all(
        evidence[key] is True
        for key in ("cid_match", "ccid_match", "jobid_match")
    )
    return evidence


def _visible_exact_text(page, label: str) -> bool:
    exact = page.get_by_text(label, exact=True)
    for index in range(exact.count()):
        try:
            if exact.nth(index).is_visible():
                return True
        except Exception:
            continue

    target = _normalize(label)
    candidates = page.get_by_text(label, exact=False)
    for index in range(candidates.count()):
        item = candidates.nth(index)
        try:
            if not item.is_visible():
                continue
            if _normalize(str(item.inner_text() or "")) == target:
                return True
        except Exception:
            continue
    return False


def _control_manifest(control: dict) -> dict:
    return {
        "observation_key": str(control.get("observation_key", "")),
        "scope": str(control.get("scope", "")),
        "tag": str(control.get("tag", "")),
        "type": str(control.get("type", "")),
        "id": str(control.get("id", "")),
        "name": str(control.get("name", "")),
        "label": _normalize(str(control.get("label", ""))),
        "role": str(control.get("role", "")),
        "required": control.get("required") is True,
        "host_required_hint": control.get("host_required_hint") is True,
        "disabled": control.get("disabled") is True,
        "accept": str(control.get("accept", "")),
        "multiple": control.get("multiple") is True,
    }


def _action_manifest(action: dict) -> dict:
    return {
        "observation_key": str(action.get("observation_key", "")),
        "scope": str(action.get("scope", "")),
        "label": _normalize(str(action.get("label", ""))),
        "type": str(action.get("type", "")),
        "disabled": action.get("disabled") is True,
    }


def _stable_control_id(item: dict) -> str:
    """Normalize only known generated ADP control ids.

    ADP emits random checkbox_* ids between equivalent renders. The semantic
    identity of those controls remains protected by type/name/label and the
    rest of the structural descriptor. Stable ADP ids remain part of the
    fingerprint.
    """
    element_id = str(item.get("id", ""))
    control_type = str(item.get("type", "")).lower()
    if control_type == "checkbox" and element_id.startswith("checkbox_"):
        return ""
    return element_id


def manifest_surface_descriptor(manifest: dict) -> dict:
    """Return only stable structural evidence for fingerprinting.

    observation_key is intentionally excluded because it contains DOM ordinals
    that may change between equivalent ADP renders. Known generated checkbox_*
    ids are normalized for the same reason; stable ids remain protected.
    """
    controls = []
    for item in manifest.get("controls", []):
        if not isinstance(item, dict):
            continue
        controls.append({
            "scope": str(item.get("scope", "")),
            "tag": str(item.get("tag", "")),
            "type": str(item.get("type", "")),
            "id": _stable_control_id(item),
            "name": str(item.get("name", "")),
            "label": _normalize(str(item.get("label", ""))),
            "role": str(item.get("role", "")),
            "required": item.get("required") is True,
            "host_required_hint": item.get("host_required_hint") is True,
            "disabled": item.get("disabled") is True,
            "accept": str(item.get("accept", "")),
            "multiple": item.get("multiple") is True,
        })
    controls.sort(key=lambda item: (
        item["scope"],
        item["id"],
        item["name"],
        item["label"],
        item["type"],
        item["tag"],
        item["required"],
        item["disabled"],
        item["accept"],
        item["multiple"],
    ))

    actions = []
    for item in manifest.get("actions", []):
        if not isinstance(item, dict):
            continue
        actions.append({
            "scope": str(item.get("scope", "")),
            "label": _normalize(str(item.get("label", ""))),
            "type": str(item.get("type", "")),
            "disabled": item.get("disabled") is True,
        })
    actions.sort(key=lambda item: (
        item["scope"],
        item["label"],
        item["type"],
        item["disabled"],
    ))

    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "steps": manifest.get("steps", {}),
        "controls": controls,
        "actions": actions,
    }


def manifest_surface_fingerprint(manifest: dict) -> str:
    payload = json.dumps(
        manifest_surface_descriptor(manifest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def extract_same_page_manifest(
    page,
    application_url: str,
    *,
    timeout_ms: int = 20_000,
    render_wait_ms: int = 5_000,
) -> dict:
    binding = _target_binding(str(page.url), application_url)
    if binding.get("target_bound") is not True:
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_TARGET_MISMATCH")

    steps = {
        key: _visible_exact_text(page, label)
        for key, label in STEP_LABELS
    }
    if steps["personal_information"] is not True:
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_PERSONAL_INFORMATION_NOT_VISIBLE")
    if not all(
        steps[key] is True
        for key in ("resume", "questions", "review_application", "self_attest_submit")
    ):
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_APPLICATION_STEPS_INCOMPLETE")

    snapshot = _snapshot(
        page,
        requested_url=application_url,
        timeout_ms=timeout_ms,
        render_wait_ms=render_wait_ms,
    )
    if snapshot.get("captcha_observed") is True:
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_CAPTCHA_BOUNDARY")
    if snapshot.get("auth_observed") is True:
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_AUTH_BOUNDARY")
    if snapshot.get("runtime_state") != "inspected":
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_FORM_NOT_INSPECTED")

    controls = [
        _control_manifest(control)
        for control in visible_application_controls(snapshot.get("form", {}))
    ]
    controls.sort(
        key=lambda item: (
            item["scope"],
            item["observation_key"],
            item["id"],
            item["name"],
            item["label"],
        )
    )
    if not controls:
        raise PermissionError("ADP_SAME_PAGE_MANIFEST_NO_VISIBLE_CONTROLS")

    actions = [
        _action_manifest(action)
        for action in snapshot.get("form", {}).get("actions", [])
        if isinstance(action, dict) and action.get("visible") is True
    ]
    actions.sort(
        key=lambda item: (
            item["scope"],
            item["observation_key"],
            item["label"],
        )
    )

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "surface_fingerprint_version": FINGERPRINT_VERSION,
        "same_page_verified_surface": True,
        "target_binding": binding,
        "steps": steps,
        "controls": controls,
        "actions": actions,
        "visible_control_count": len(controls),
        "visible_action_count": len(actions),
        "file_control_count": sum(1 for item in controls if item["type"].lower() == "file"),
        "password_control_count": sum(1 for item in controls if item["type"].lower() == "password"),
        "navigation_click_attempts": 0,
        "form_value_write_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "navigation_allowed": False,
        "safe_fill_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
        "candidate_values_exposed": False,
        "raw_values_exposed": False,
    }
    manifest["surface_fingerprint"] = manifest_surface_fingerprint(manifest)
    return manifest
