from __future__ import annotations

import unittest

from ejs.services.adp_validation_contract import (
    CONTRACT_VERSION,
    classify_name,
    profile_contract_report,
    turkish_ascii_candidate,
    validate_adp_name,
)


class AdpValidationContractTests(unittest.TestCase):
    def test_documented_ascii_name_grammar_accepts_reviewed_characters(self):
        for value in ("Ada", "Anne Marie", "O'Neil", "Smith-Jones", "A(B)"):
            self.assertEqual(validate_adp_name(value), (True, "ADP_NAME_COMPATIBLE"))

    def test_non_ascii_name_fails_closed(self):
        ok, code = validate_adp_name("Çağ")
        self.assertFalse(ok)
        self.assertEqual(code, "ADP_NAME_CHARACTER_CONTRACT_MISMATCH")

    def test_repeated_special_is_rejected(self):
        self.assertEqual(
            validate_adp_name("Anne--Marie"),
            (False, "ADP_NAME_REPEATED_SPECIAL"),
        )

    def test_outer_whitespace_is_rejected(self):
        self.assertEqual(
            validate_adp_name(" Ada"),
            (False, "ADP_NAME_OUTER_WHITESPACE"),
        )

    def test_turkish_ascii_candidate_is_explicit_and_validatable(self):
        self.assertEqual(turkish_ascii_candidate("ÇĞİıÖŞÜçğıöşü"), "CGIiOSUcgiosu")
        result = classify_name("Çağ")
        self.assertFalse(result.compatible)
        self.assertTrue(result.transliteration_changes_value)
        self.assertTrue(result.transliteration_candidate_valid)
        self.assertNotEqual(result.value_hash, result.transliteration_candidate_hash)

    def test_profile_report_never_authorizes_browser_execution(self):
        report = profile_contract_report({
            "candidate.first_name": "Ada",
            "candidate.last_name": "Lovelace",
            "candidate.phone": "+905551112233",
        })
        self.assertEqual(report["contract_version"], CONTRACT_VERSION)
        self.assertTrue(report["first_name"]["compatible"])
        self.assertTrue(report["last_name"]["compatible"])
        self.assertTrue(report["phone"]["present"])
        self.assertTrue(report["phone"]["runtime_required_observed"])
        self.assertFalse(report["phone"]["format_contract_resolved"])
        self.assertFalse(report["browser_write_allowed"])
        self.assertFalse(report["continue_click_allowed"])
        self.assertFalse(report["file_upload_allowed"])
        self.assertFalse(report["final_submit_allowed"])

    def test_missing_phone_requires_user_profile_policy(self):
        report = profile_contract_report({
            "candidate.first_name": "Ada",
            "candidate.last_name": "Lovelace",
        })
        self.assertFalse(report["phone"]["present"])
        self.assertTrue(report["requires_user_profile_policy"])

    def test_non_ascii_name_requires_user_profile_policy_without_exposing_value(self):
        report = profile_contract_report({
            "candidate.first_name": "Çağ",
            "candidate.last_name": "Test",
            "candidate.phone": "+905551112233",
        })
        self.assertFalse(report["first_name"]["compatible"])
        self.assertTrue(report["first_name"]["transliteration_candidate_valid"])
        self.assertTrue(report["requires_user_profile_policy"])
        self.assertNotIn("value", report["first_name"])


if __name__ == "__main__":
    unittest.main()
