from __future__ import annotations

import unittest

from ejs.services.adp_personal_information_profile import (
    PROFILE_VERSION,
    resolve_personal_information_profile,
)


class AdpPersonalInformationProfileTests(unittest.TestCase):
    def base(self):
        return {
            "profile_version": "candidate-profile-v3",
            "first_name": "Cagatay",
            "last_name": "Buyuk",
            "email": "candidate@example.com",
            "phone_country_iso2": "TR",
            "phone_national_number": "5551112233",
            "adp_ascii_name_policy_approved": True,
            "address_country_iso2": "TR",
            "address_line1": "Example Mah. Example Cad. 1",
            "address_line2": "",
            "address_line3": "",
            "city": "Istanbul",
            "state_or_territory": "Istanbul",
            "postal_code": "34700",
        }

    def test_resolves_required_address_and_phone_identity_profile(self):
        resolved = resolve_personal_information_profile(self.base())
        self.assertEqual(resolved.address_country_iso2, "TR")
        self.assertEqual(resolved.city, "Istanbul")
        self.assertEqual(resolved.identity_phone.phone_country_iso2, "TR")
        self.assertEqual(resolved.identity_phone.last_name, "Buyuk")

    def test_required_address_fields_fail_closed(self):
        for key in (
            "address_country_iso2",
            "address_line1",
            "city",
            "state_or_territory",
            "postal_code",
        ):
            profile = self.base()
            profile[key] = ""
            with self.assertRaises(ValueError, msg=key):
                resolve_personal_information_profile(profile)

    def test_address_country_requires_uppercase_iso2(self):
        profile = self.base()
        profile["address_country_iso2"] = "tr"
        with self.assertRaisesRegex(ValueError, "ADDRESS_COUNTRY_ISO2_INVALID"):
            resolve_personal_information_profile(profile)

    def test_optional_lines_may_be_empty_but_not_outer_whitespace(self):
        profile = self.base()
        profile["address_line2"] = ""
        profile["address_line3"] = ""
        resolved = resolve_personal_information_profile(profile)
        self.assertEqual(resolved.address_line2, "")
        profile["address_line2"] = " Apartment 4 "
        with self.assertRaisesRegex(ValueError, "OUTER_WHITESPACE:address_line2"):
            resolve_personal_information_profile(profile)

    def test_non_secret_evidence_exposes_no_raw_address_or_phone(self):
        profile = self.base()
        resolved = resolve_personal_information_profile(profile)
        evidence = resolved.non_secret_evidence()
        self.assertEqual(evidence["profile_version"], PROFILE_VERSION)
        self.assertEqual(evidence["address"]["country_iso2"], "TR")
        self.assertFalse(evidence["raw_values_exposed"])
        rendered = repr(evidence)
        for raw in (
            profile["address_line1"],
            profile["city"],
            profile["state_or_territory"],
            profile["postal_code"],
            profile["phone_national_number"],
            profile["email"],
        ):
            self.assertNotIn(raw, rendered)


if __name__ == "__main__":
    unittest.main()
