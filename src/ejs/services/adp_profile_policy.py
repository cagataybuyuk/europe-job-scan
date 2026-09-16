"""Fail-closed ADP profile-v2 resolver.

This module resolves a private candidate profile for the reviewed ADP identity
surface. It never performs browser/network actions and exposes a non-secret
report separately from the resolved values used in memory by a reviewed canary.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from ejs.contracts.prefill import value_hash
from ejs.services.adp_validation_contract import (
    turkish_ascii_candidate,
    validate_adp_name,
)

PROFILE_POLICY_VERSION = "adp-profile-policy-v2"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
PHONE_NATIONAL_RE = re.compile(r"^[0-9]{4,20}$")


@dataclass(frozen=True)
class ResolvedAdpProfile:
    profile_version: str
    first_name: str
    last_name: str
    email: str
    phone_country_iso2: str
    phone_national_number: str
    first_name_transliterated: bool
    last_name_transliterated: bool

    def non_secret_evidence(self) -> dict:
        return {
            "profile_policy_version": PROFILE_POLICY_VERSION,
            "candidate_profile_version": self.profile_version,
            "first_name": {
                "value_hash": value_hash(self.first_name),
                "transliteration_applied": self.first_name_transliterated,
                "compatible": True,
            },
            "last_name": {
                "value_hash": value_hash(self.last_name),
                "transliteration_applied": self.last_name_transliterated,
                "compatible": True,
            },
            "email": {
                "value_hash": value_hash(self.email),
                "valid": True,
            },
            "phone": {
                "country_iso2": self.phone_country_iso2,
                "national_number_hash": value_hash(self.phone_national_number),
                "digit_count": len(self.phone_national_number),
                "runtime_required_observed": True,
            },
            "raw_values_exposed": False,
        }


def _require_exact_string(profile: Mapping[str, object], key: str) -> str:
    value = profile.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ADP_PROFILE_REQUIRES_{key.upper()}")
    if value != value.strip():
        raise ValueError(f"ADP_PROFILE_OUTER_WHITESPACE:{key}")
    return value


def _resolve_name(value: str, *, approval: bool, field: str) -> tuple[str, bool]:
    compatible, _reason = validate_adp_name(value)
    if compatible:
        return value, False
    if approval is not True:
        raise PermissionError(f"ADP_ASCII_NAME_POLICY_APPROVAL_REQUIRED:{field}")
    candidate = turkish_ascii_candidate(value)
    candidate_ok, reason = validate_adp_name(candidate)
    if not candidate_ok:
        raise ValueError(f"ADP_ASCII_NAME_CANDIDATE_INVALID:{field}:{reason}")
    if candidate == value:
        raise ValueError(f"ADP_ASCII_NAME_POLICY_CANNOT_RESOLVE:{field}")
    return candidate, True


def resolve_adp_profile(profile: Mapping[str, object]) -> ResolvedAdpProfile:
    """Resolve a private v2 profile for reviewed ADP browser execution.

    Phone national number is treated as an exact user-supplied fact. No phone
    semantic normalization is performed here.
    """
    if not isinstance(profile, Mapping):
        raise ValueError("ADP_PROFILE_INVALID")

    profile_version = str(profile.get("profile_version") or "adp-canary-profile-v2")
    first_raw = _require_exact_string(profile, "first_name")
    last_raw = _require_exact_string(profile, "last_name")
    email = _require_exact_string(profile, "email")
    country = _require_exact_string(profile, "phone_country_iso2")
    phone = _require_exact_string(profile, "phone_national_number")
    approval = profile.get("adp_ascii_name_policy_approved")

    if type(approval) is not bool:
        raise ValueError("ADP_PROFILE_REQUIRES_BOOLEAN_ASCII_NAME_POLICY_APPROVAL")
    if not EMAIL_RE.fullmatch(email):
        raise ValueError("ADP_PROFILE_EMAIL_INVALID")
    if not COUNTRY_RE.fullmatch(country):
        raise ValueError("ADP_PROFILE_PHONE_COUNTRY_ISO2_INVALID")
    if not PHONE_NATIONAL_RE.fullmatch(phone):
        raise ValueError("ADP_PROFILE_PHONE_NATIONAL_NUMBER_INVALID")

    first, first_changed = _resolve_name(
        first_raw,
        approval=approval,
        field="candidate.first_name",
    )
    last, last_changed = _resolve_name(
        last_raw,
        approval=approval,
        field="candidate.last_name",
    )

    return ResolvedAdpProfile(
        profile_version=profile_version,
        first_name=first,
        last_name=last,
        email=email,
        phone_country_iso2=country,
        phone_national_number=phone,
        first_name_transliterated=first_changed,
        last_name_transliterated=last_changed,
    )
