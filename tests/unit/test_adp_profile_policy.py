from __future__ import annotations

import unittest

from ejs.services.adp_profile_policy import (
    PROFILE_POLICY_VERSION,
    resolve_adp_profile,
)


class AdpProfilePolicyTests(unittest.TestCase):
    def _base(self):
        return {
            "profile_version": "adp-canary-profile-v2",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "phone_country_iso2": "TR",
            "phone_national_number": "5551112233",
            "adp_ascii_name_policy_approved": False,
        }

    def test_compatible_names_do_not_require_transliteration_approval(self):
        resolved = resolve_adp_profile(self._base())
        self.assertEqual(resolved.first_name, "Ada")
        self.assertEqual(resolved.last_name, "Lovelace")
        self.assertFalse(resolved.first_name_transliterated)
        self.assertFalse(resolved.last_name_transliterated)
        self.assertEqual(resolved.phone_country_iso2, "TR")
        self.assertEqual(resolved.phone_national_number, "5551112233")

    def test_non_ascii_name_requires_explicit_approval(self):
        profile = self._base()
        profile["first_name"] = "Çağ"
        with self.assertRaisesRegex(PermissionError, "ADP_ASCII_NAME_POLICY_APPROVAL_REQUIRED"):
            resolve_adp_profile(profile)

    def test_approved_turkish_transliteration_resolves_name(self):
        profile = self._base()
        profile["first_name"] = "Çağ"
        profile["last_name"] = "Büyük"
        profile["adp_ascii_name_policy_approved"] = True
        resolved = resolve_adp_profile(profile)
        self.assertEqual(resolved.first_name, "Cag")
        self.assertEqual(resolved.last_name, "Buyuk")
        self.assertTrue(resolved.first_name_transliterated)
        self.assertTrue(resolved.last_name_transliterated)

    def test_phone_country_must_be_uppercase_iso2(self):
        profile = self._base()
        profile["phone_country_iso2"] = "tr"
        with self.assertRaisesRegex(ValueError, "ADP_PROFILE_PHONE_COUNTRY_ISO2_INVALID"):
            resolve_adp_profile(profile)

    def test_phone_national_number_is_exact_digits_only_fact(self):
        for invalid in ("+905551112233", "0555 111 22 33", "555-111-2233", "123"):
            profile = self._base()
            profile["phone_national_number"] = invalid
            with self.assertRaisesRegex(ValueError, "ADP_PROFILE_PHONE_NATIONAL_NUMBER_INVALID"):
                resolve_adp_profile(profile)

    def test_policy_approval_must_be_boolean(self):
        profile = self._base()
        profile["adp_ascii_name_policy_approved"] = "true"
        with self.assertRaisesRegex(ValueError, "ADP_PROFILE_REQUIRES_BOOLEAN_ASCII_NAME_POLICY_APPROVAL"):
            resolve_adp_profile(profile)

    def test_non_secret_evidence_contains_no_raw_profile_values(self):
        profile = self._base()
        profile["first_name"] = "Çağ"
        profile["last_name"] = "Büyük"
        profile["adp_ascii_name_policy_approved"] = True
        resolved = resolve_adp_profile(profile)
        evidence = resolved.non_secret_evidence()
        self.assertEqual(evidence["profile_policy_version"], PROFILE_POLICY_VERSION)
        self.assertTrue(evidence["first_name"]["transliteration_applied"])
        self.assertTrue(evidence["last_name"]["transliteration_applied"])
        self.assertEqual(evidence["phone"]["country_iso2"], "TR")
        self.assertEqual(evidence["phone"]["digit_count"], 10)
        self.assertFalse(evidence["raw_values_exposed"])
        text = repr(evidence)
        for raw in ("Çağ", "Büyük", "Cag", "Buyuk", "ada@example.com", "5551112233"):
            self.assertNotIn(raw, text)


if __name__ == "__main__":
    unittest.main()
