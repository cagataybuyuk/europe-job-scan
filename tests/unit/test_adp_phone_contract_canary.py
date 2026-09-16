from __future__ import annotations

import unittest

from ejs.services.adp_phone_contract_canary import (
    AdpPhoneContractCanaryRequest,
    CANARY_VERSION,
    PHONE_COUNTRY_NAME,
    PHONE_INPUT_ID,
    _base_report,
    validate_request,
)


URL = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=eae41664-19fb-4412-96f8-43f15d52332b&ccId=19000101_000001&jobId=960970&source=LR&lang=en_US"
FP = "a" * 64


class AdpPhoneContractCanaryTests(unittest.TestCase):
    def request(self, **overrides):
        values = {
            "application_url": URL,
            "expected_navigation_surface_fingerprint": FP,
            "expected_preference_surface_fingerprint": FP,
            "entry_ordinal": 0,
            "expected_safe_fill_surface_fingerprint": FP,
        }
        values.update(overrides)
        return AdpPhoneContractCanaryRequest(**values)

    def test_valid_request(self):
        validate_request(self.request())

    def test_rejects_non_adp_url(self):
        with self.assertRaises(ValueError):
            validate_request(self.request(application_url="https://example.com/jobs/1"))

    def test_rejects_invalid_fingerprint(self):
        with self.assertRaises(ValueError):
            validate_request(self.request(expected_safe_fill_surface_fingerprint="bad"))

    def test_rejects_unreviewed_apply_label(self):
        with self.assertRaises(ValueError):
            validate_request(self.request(expected_apply_label="Continue"))

    def test_base_report_has_zero_profile_write_authority(self):
        report = _base_report(self.request())
        self.assertEqual(report["canary_version"], CANARY_VERSION)
        self.assertTrue(report["phone_contract_inspection_only"])
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["second_action_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["phone_write_allowed"])
        self.assertFalse(report["continue_click_allowed"])
        self.assertFalse(report["file_upload_allowed"])
        self.assertFalse(report["final_submit_allowed"])

    def test_reviewed_phone_control_identifiers_are_exact(self):
        self.assertEqual(PHONE_COUNTRY_NAME, "phoneCountry")
        self.assertEqual(PHONE_INPUT_ID, "login_view_phone")


if __name__ == "__main__":
    unittest.main()
