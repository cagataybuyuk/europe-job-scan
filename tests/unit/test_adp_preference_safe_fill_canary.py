import unittest

from ejs.services.adp_cookie_preferences_canary import preference_surface_fingerprint
from ejs.services.adp_preference_safe_fill_canary import (
    AdpPreferenceSafeFillCanaryRequest,
    COOKIE_POLICY,
    SAVE_CHANGES_LABEL,
    UNSELECT_ALL_LABEL,
    validate_request,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


class AdpPreferenceSafeFillCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_request(AdpPreferenceSafeFillCanaryRequest(
            application_url=URL,
            expected_navigation_surface_fingerprint=FP,
            expected_preference_surface_fingerprint=FP,
            entry_ordinal=0,
            expected_safe_fill_surface_fingerprint=FP,
            profile_manifest_path="profile.json",
        ))

    def test_rejects_invalid_preference_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_PREFERENCE_SURFACE_FINGERPRINT"):
            validate_request(AdpPreferenceSafeFillCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                expected_preference_surface_fingerprint="abc",
                entry_ordinal=0,
                expected_safe_fill_surface_fingerprint=FP,
                profile_manifest_path="profile.json",
            ))

    def test_policy_contract_is_unselect_all_then_save(self):
        self.assertEqual(COOKIE_POLICY, "preferences_unselect_all_save")
        self.assertEqual(UNSELECT_ALL_LABEL, "Unselect All")
        self.assertEqual(SAVE_CHANGES_LABEL, "Save Changes")

    def test_known_live_preference_surface_fingerprint(self):
        descriptor = {
            "banner_present": True,
            "banner_visible": True,
            "preference_center_present": True,
            "preference_center_visible": True,
            "visible_buttons": [
                {"enabled": True, "id": "", "label": "Close", "ordinal": 5},
                {"enabled": True, "id": "", "label": "Close Menu", "ordinal": 14},
                {"enabled": True, "id": "", "label": "Save Changes", "ordinal": 12},
                {"enabled": True, "id": "", "label": "Share", "ordinal": 13},
                {"enabled": True, "id": "", "label": "Unselect All", "ordinal": 11},
                {"enabled": True, "id": "close-pc-btn-handler", "label": "Close preference center", "ordinal": 6},
                {"enabled": True, "id": "onetrust-pc-btn-handler", "label": "To manage your preferences, click here, Opens the preference center dialog", "ordinal": 4},
                {"enabled": True, "id": "ot-sdk-btn", "label": "To manage your preferences, click here", "ordinal": 3},
                {"enabled": True, "id": "recruitment_jobDescription_apply", "label": "Apply", "ordinal": 0},
                {"enabled": True, "id": "recruitment_jobDescription_back", "label": "Back", "ordinal": 1},
                {"enabled": True, "id": "recruitment_jobDescription_candidateApply", "label": "Apply", "ordinal": 2},
            ],
            "visible_checkboxes": [],
        }
        self.assertEqual(
            preference_surface_fingerprint(descriptor),
            "f80faea8117621a286097dec92e55510843187ec9fdc82792379330e4034bd3c",
        )


if __name__ == "__main__":
    unittest.main()
