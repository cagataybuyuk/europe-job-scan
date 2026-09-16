"""Fail-closed ADP identity validation contract derived from live evidence.

This module does not write browser fields. It only classifies profile values and
produces non-secret compatibility evidence for later reviewed execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from ejs.contracts.prefill import value_hash

CONTRACT_VERSION = "adp-validation-contract-v1"
NAME_POLICY = "adp-ascii-name-v1"
PHONE_POLICY = "adp-runtime-phone-required-v1"

# ADP Applicant Onboard documentation: A-Z/a-z, space, parentheses, hyphen,
# apostrophe; first character alphabetic; repeated identical special characters
# are not accepted.
ADP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z ()'\-]*$")
ADP_SPECIALS = " ()'-"

# Candidate-only policy. It is never applied unless a higher-level reviewed
# workflow explicitly authorizes it.
TURKISH_ASCII_TRANSLITERATION = str.maketrans({
    "Ç": "C", "ç": "c",
    "Ğ": "G", "ğ": "g",
    "İ": "I", "ı": "i",
    "Ö": "O", "ö": "o",
    "Ş": "S", "ş": "s",
    "Ü": "U", "ü": "u",
})


@dataclass(frozen=True)
class NameContractResult:
    compatible: bool
    reason_code: str
    value_hash: str
    transliteration_candidate_valid: bool
    transliteration_changes_value: bool
    transliteration_candidate_hash: str


def _has_repeated_special(value: str) -> bool:
    return any(ch * 2 in value for ch in ADP_SPECIALS)


def validate_adp_name(value: str) -> tuple[bool, str]:
    if not isinstance(value, str) or not value:
        return False, "ADP_NAME_EMPTY"
    if value != value.strip():
        return False, "ADP_NAME_OUTER_WHITESPACE"
    if not ADP_NAME_RE.fullmatch(value):
        return False, "ADP_NAME_CHARACTER_CONTRACT_MISMATCH"
    if _has_repeated_special(value):
        return False, "ADP_NAME_REPEATED_SPECIAL"
    return True, "ADP_NAME_COMPATIBLE"


def turkish_ascii_candidate(value: str) -> str:
    """Return a candidate transformation only; never authorizes its use."""
    return value.translate(TURKISH_ASCII_TRANSLITERATION)


def classify_name(value: str) -> NameContractResult:
    compatible, reason = validate_adp_name(value)
    candidate = turkish_ascii_candidate(value)
    candidate_valid, _ = validate_adp_name(candidate)
    return NameContractResult(
        compatible=compatible,
        reason_code=reason,
        value_hash=value_hash(value),
        transliteration_candidate_valid=candidate_valid,
        transliteration_changes_value=candidate != value,
        transliteration_candidate_hash=value_hash(candidate),
    )


def profile_contract_report(profile: Mapping[str, str]) -> dict:
    """Return non-secret compatibility evidence for a canonical candidate profile.

    Live run 34989901584 established that ADP requires a mobile number at
    Continue time even though the native HTML required flag is false. Until a
    reviewed phone formatting/splitting contract exists, presence is evidence
    only and phone remains unresolved for execution.
    """
    first = str(profile.get("candidate.first_name", ""))
    last = str(profile.get("candidate.last_name", ""))
    phone = str(profile.get("candidate.phone", ""))

    first_result = classify_name(first)
    last_result = classify_name(last)
    phone_present = bool(phone.strip())

    requires_user_policy = (
        not first_result.compatible
        or not last_result.compatible
        or not phone_present
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "name_policy": NAME_POLICY,
        "phone_policy": PHONE_POLICY,
        "first_name": {
            "compatible": first_result.compatible,
            "reason_code": first_result.reason_code,
            "value_hash": first_result.value_hash,
            "transliteration_candidate_valid": first_result.transliteration_candidate_valid,
            "transliteration_changes_value": first_result.transliteration_changes_value,
            "transliteration_candidate_hash": first_result.transliteration_candidate_hash,
        },
        "last_name": {
            "compatible": last_result.compatible,
            "reason_code": last_result.reason_code,
            "value_hash": last_result.value_hash,
            "transliteration_candidate_valid": last_result.transliteration_candidate_valid,
            "transliteration_changes_value": last_result.transliteration_changes_value,
            "transliteration_candidate_hash": last_result.transliteration_candidate_hash,
        },
        "phone": {
            "runtime_required_observed": True,
            "present": phone_present,
            "value_hash": value_hash(phone) if phone_present else "",
            "format_contract_resolved": False,
        },
        "requires_user_profile_policy": requires_user_policy,
        "browser_write_allowed": False,
        "continue_click_allowed": False,
        "file_upload_allowed": False,
        "final_submit_allowed": False,
    }
