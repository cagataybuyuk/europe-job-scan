import unittest

from ejs.services.adp_cookie_preferences_canary import (
    AdpCookiePreferencesCanaryRequest,
    preference_surface_fingerprint,
    validate_request,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


class AdpCookiePreferencesCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_request(AdpCookiePreferencesCanaryRequest(
            application_url=URL,
            expected_navigation_surface_fingerprint=FP,
        ))

    def test_invalid_fingerprint_rejected(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"):
            validate_request(AdpCookiePreferencesCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint="abc",
            ))

    def test_surface_fingerprint_is_order_sensitive_only_to_descriptor_content(self):
        descriptor = {
            "preference_center_present": True,
            "preference_center_visible": True,
            "banner_present": True,
            "banner_visible": False,
            "visible_buttons": [
                {"ordinal": 2, "id": "onetrust-pc-btn-handler", "label": "Confirm My Choices", "enabled": True},
                {"ordinal": 1, "id": "close-pc-btn-handler", "label": "Close preference center", "enabled": True},
            ],
            "visible_checkboxes": [
                {"ordinal": 0, "id": "ot-group-id-C0002", "label": "Analytics", "checked": False, "enabled": True},
            ],
        }
        fp1 = preference_surface_fingerprint(descriptor)
        fp2 = preference_surface_fingerprint(dict(descriptor))
        self.assertEqual(fp1, fp2)
        changed = {**descriptor, "banner_visible": True}
        self.assertNotEqual(fp1, preference_surface_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
