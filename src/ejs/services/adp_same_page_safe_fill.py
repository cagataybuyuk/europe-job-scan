"""Bounded same-page safe-fill for an already verified ADP Personal Information page.

Authority is deliberately narrow:
- only blank reviewed First Name / Last Name controls may be filled;
- a non-empty mismatching identity control blocks instead of being overwritten;
- disabled Email is exact-readback only;
- phone, address, consent, navigation, upload and submit are untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from ejs.contracts.prefill import SafeFieldWriterAuthority, value_hash
from ejs.services.adp_live_inspector import validate_adp_live_url
from ejs.services.adp_same_page_manifest import (
    extract_same_page_manifest,
    manifest_surface_fingerprint,
)

EXECUTOR_VERSION = "adp-same-page-safe-fill-v1"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

FIRST_NAME_ID = "personalInfomationFirstName"
LAST_NAME_ID = "personalInfomationLastName"
EMAIL_ID = "personalInfomationEmail"


@dataclass(frozen=True)
class AdpSamePageIdentityProfile:
    profile_version: str
    first_name: str
    last_name: str
    email: str

    def non_secret_evidence(self) -> dict:
        return {
            "profile_version": self.profile_version,
            "first_name_hash": value_hash(self.first_name),
            "last_name_hash": value_hash(self.last_name),
            "email_hash": value_hash(self.email),
            "raw_values_exposed": False,
        }


@dataclass(frozen=True)
class AdpSamePageSafeFillRequest:
    application_url: str
    expected_manifest_fingerprint: str
    profile_json_path: str
    timeout_ms: int = 20_000
    render_wait_ms: int = 5_000


def load_identity_profile(path: str) -> AdpSamePageIdentityProfile:
    if not path:
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_PROFILE_REQUIRED")
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_PROFILE_INVALID")

    def required(key: str) -> str:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"ADP_SAME_PAGE_SAFE_FILL_PROFILE_REQUIRES_{key.upper()}")
        if value != value.strip():
            raise ValueError(f"ADP_SAME_PAGE_SAFE_FILL_PROFILE_OUTER_WHITESPACE:{key}")
        return value

    first_name = required("first_name")
    last_name = required("last_name")
    email = required("email")
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_EMAIL_INVALID")

    return AdpSamePageIdentityProfile(
        profile_version=str(raw.get("profile_version") or "adp-same-page-profile-v1"),
        first_name=first_name,
        last_name=last_name,
        email=email,
    )


def validate_request(request: AdpSamePageSafeFillRequest) -> None:
    validate_adp_live_url(request.application_url)
    if not FINGERPRINT_RE.fullmatch(request.expected_manifest_fingerprint):
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_INVALID_MANIFEST_FINGERPRINT")
    if request.timeout_ms < 1_000 or request.timeout_ms > 60_000:
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_INVALID_TIMEOUT")
    if request.render_wait_ms < 1_000 or request.render_wait_ms > 15_000:
        raise ValueError("ADP_SAME_PAGE_SAFE_FILL_INVALID_RENDER_WAIT")
    SafeFieldWriterAuthority().validate()


def _control_by_id(manifest: dict, element_id: str) -> dict:
    matches = [
        item for item in manifest.get("controls", [])
        if isinstance(item, dict) and item.get("id") == element_id
    ]
    if len(matches) != 1:
        raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_CONTROL_NOT_UNIQUE:{element_id}")
    return matches[0]


def _validate_identity_surface(manifest: dict) -> None:
    first = _control_by_id(manifest, FIRST_NAME_ID)
    last = _control_by_id(manifest, LAST_NAME_ID)
    email = _control_by_id(manifest, EMAIL_ID)

    for canonical, control, label in (
        ("candidate.first_name", first, "First Name*"),
        ("candidate.last_name", last, "Last Name*"),
        ("candidate.email", email, "Email*"),
    ):
        if control.get("type") != "text":
            raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_TYPE_DRIFT:{canonical}")
        if control.get("label") != label:
            raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_LABEL_DRIFT:{canonical}")
        if control.get("required") is not True:
            raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_REQUIREDNESS_DRIFT:{canonical}")

    if first.get("disabled") is True or last.get("disabled") is True:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_NAME_CONTROL_DISABLED")
    if email.get("disabled") is not True:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_EMAIL_EXPECTED_DISABLED")

    ambiguous_phone = [
        item for item in manifest.get("controls", [])
        if isinstance(item, dict)
        and (
            item.get("type") == "tel"
            or item.get("name") in {"phone", "phoneCountry"}
        )
    ]
    if len(ambiguous_phone) < 2:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_PHONE_AMBIGUITY_CONTRACT_DRIFT")


def _fill_blank_or_verify(page, element_id: str, desired: str, canonical: str, counters: dict) -> dict:
    locator = page.locator(f"#{element_id}")
    if locator.count() != 1 or not locator.is_visible() or not locator.is_enabled():
        raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_RUNTIME_CONTROL_NOT_ACTIONABLE:{canonical}")

    before = locator.input_value()
    if before and before != desired:
        raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_PROFILE_CONFLICT:{canonical}")

    executed = False
    if not before:
        counters["form_value_write_attempts"] += 1
        locator.fill(desired)
        counters["form_value_write_successes"] += 1
        executed = True

    after = locator.input_value()
    if after != desired:
        raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_READBACK_MISMATCH:{canonical}")
    valid = bool(locator.evaluate("el => el.checkValidity()"))
    if not valid:
        raise PermissionError(f"ADP_SAME_PAGE_SAFE_FILL_BROWSER_VALIDITY_FAILED:{canonical}")

    return {
        "canonical_field": canonical,
        "control_id": element_id,
        "executed": executed,
        "value_hash": value_hash(desired),
        "readback_match": True,
        "valid": True,
    }


def run_on_verified_page(
    page,
    request: AdpSamePageSafeFillRequest,
) -> dict:
    validate_request(request)
    profile = load_identity_profile(request.profile_json_path)
    counters = {
        "form_value_write_attempts": 0,
        "form_value_write_successes": 0,
    }

    manifest = extract_same_page_manifest(
        page,
        request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    observed = manifest_surface_fingerprint(manifest)
    if observed != request.expected_manifest_fingerprint:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_MANIFEST_FINGERPRINT_MISMATCH")
    _validate_identity_surface(manifest)

    email = page.locator(f"#{EMAIL_ID}")
    if email.count() != 1 or not email.is_visible() or email.is_enabled():
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_EMAIL_RUNTIME_DRIFT")
    email_readback = email.input_value()
    if email_readback != profile.email:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_PROFILE_CONFLICT:candidate.email")

    results = [
        _fill_blank_or_verify(
            page,
            FIRST_NAME_ID,
            profile.first_name,
            "candidate.first_name",
            counters,
        ),
        _fill_blank_or_verify(
            page,
            LAST_NAME_ID,
            profile.last_name,
            "candidate.last_name",
            counters,
        ),
    ]

    post_manifest = extract_same_page_manifest(
        page,
        request.application_url,
        timeout_ms=request.timeout_ms,
        render_wait_ms=request.render_wait_ms,
    )
    post_fingerprint = manifest_surface_fingerprint(post_manifest)
    if post_fingerprint != request.expected_manifest_fingerprint:
        raise PermissionError("ADP_SAME_PAGE_SAFE_FILL_POST_WRITE_SURFACE_DRIFT")

    return {
        "executor_version": EXECUTOR_VERSION,
        "safe_fill_status": "verified",
        "expected_manifest_fingerprint": request.expected_manifest_fingerprint,
        "observed_manifest_fingerprint": observed,
        "post_write_manifest_fingerprint": post_fingerprint,
        "profile_evidence": profile.non_secret_evidence(),
        "field_results": results,
        "email_readback_only": {
            "control_id": EMAIL_ID,
            "value_hash": value_hash(profile.email),
            "readback_match": True,
            "write_executed": False,
        },
        **counters,
        "navigation_click_attempts": 0,
        "credential_entry_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "phone_write_attempts": 0,
        "address_write_attempts": 0,
        "consent_action_attempts": 0,
        "next_click_attempts": 0,
        "further_navigation_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
        "raw_values_exposed": False,
    }
