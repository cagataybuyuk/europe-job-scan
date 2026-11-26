"""Private ADP Personal Information profile resolver for same-page execution.

This module performs validation only. It has no browser/network authority and
never logs raw address or phone values.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from ejs.contracts.prefill import value_hash
from ejs.services.adp_profile_policy import (
    ResolvedAdpProfile,
    resolve_adp_profile,
)

PROFILE_VERSION = "adp-personal-information-profile-v1"
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


def _exact_text(profile: Mapping[str, object], key: str, *, required: bool, max_len: int) -> str:
    value = profile.get(key, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"ADP_PERSONAL_INFO_PROFILE_INVALID_TYPE:{key}")
    if value != value.strip():
        raise ValueError(f"ADP_PERSONAL_INFO_PROFILE_OUTER_WHITESPACE:{key}")
    if required and not value:
        raise ValueError(f"ADP_PERSONAL_INFO_PROFILE_REQUIRES_{key.upper()}")
    if len(value) > max_len:
        raise ValueError(f"ADP_PERSONAL_INFO_PROFILE_TOO_LONG:{key}")
    if CONTROL_CHAR_RE.search(value):
        raise ValueError(f"ADP_PERSONAL_INFO_PROFILE_CONTROL_CHAR:{key}")
    return value


@dataclass(frozen=True)
class ResolvedAdpPersonalInformationProfile:
    identity_phone: ResolvedAdpProfile
    address_country_iso2: str
    address_line1: str
    address_line2: str
    address_line3: str
    city: str
    state_or_territory: str
    postal_code: str

    def non_secret_evidence(self) -> dict:
        return {
            "profile_version": PROFILE_VERSION,
            "identity_phone": self.identity_phone.non_secret_evidence(),
            "address": {
                "country_iso2": self.address_country_iso2,
                "address_line1_hash": value_hash(self.address_line1),
                "address_line2_present": bool(self.address_line2),
                "address_line2_hash": value_hash(self.address_line2) if self.address_line2 else "",
                "address_line3_present": bool(self.address_line3),
                "address_line3_hash": value_hash(self.address_line3) if self.address_line3 else "",
                "city_hash": value_hash(self.city),
                "state_or_territory_hash": value_hash(self.state_or_territory),
                "postal_code_hash": value_hash(self.postal_code),
            },
            "raw_values_exposed": False,
        }


def resolve_personal_information_profile(
    profile: Mapping[str, object],
) -> ResolvedAdpPersonalInformationProfile:
    if not isinstance(profile, Mapping):
        raise ValueError("ADP_PERSONAL_INFO_PROFILE_INVALID")

    identity_phone = resolve_adp_profile(profile)
    country = _exact_text(
        profile,
        "address_country_iso2",
        required=True,
        max_len=2,
    )
    if not COUNTRY_RE.fullmatch(country):
        raise ValueError("ADP_PERSONAL_INFO_ADDRESS_COUNTRY_ISO2_INVALID")

    return ResolvedAdpPersonalInformationProfile(
        identity_phone=identity_phone,
        address_country_iso2=country,
        address_line1=_exact_text(profile, "address_line1", required=True, max_len=180),
        address_line2=_exact_text(profile, "address_line2", required=False, max_len=180),
        address_line3=_exact_text(profile, "address_line3", required=False, max_len=180),
        city=_exact_text(profile, "city", required=True, max_len=120),
        state_or_territory=_exact_text(
            profile,
            "state_or_territory",
            required=True,
            max_len=120,
        ),
        postal_code=_exact_text(profile, "postal_code", required=True, max_len=40),
    )
