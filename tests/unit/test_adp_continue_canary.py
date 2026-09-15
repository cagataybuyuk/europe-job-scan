import unittest

from ejs.services.adp_continue_canary import (
    AdpContinueCanaryRequest,
    _route_after_continue,
    action_surface_fingerprint,
    validate_request,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


def reviewed_action_surface():
    return {
        "visible_buttons": [
            {"label": "close menu", "enabled": True},
            {"label": "continue", "enabled": True},
            {"label": "sign in with facebook", "enabled": True},
            {"label": "sign in with google", "enabled": True},
            {"label": "sign in with linkedin", "enabled": True},
            {"label": "to manage your preferences, click here", "enabled": True},
        ]
    }


def snapshot(*, captcha=False, auth=False, controls=None):
    return {
        "captcha_observed": captcha,
        "auth_observed": auth,
        "form": {"controls": controls or []},
    }


class AdpContinueCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_request(AdpContinueCanaryRequest(
            application_url=URL,
            expected_navigation_surface_fingerprint=FP,
            expected_preference_surface_fingerprint=FP,
            entry_ordinal=0,
            expected_safe_fill_surface_fingerprint=FP,
            expected_post_fill_action_surface_fingerprint=FP,
            profile_manifest_path="profile.json",
        ))

    def test_rejects_invalid_action_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_POST_FILL_ACTION_SURFACE_FINGERPRINT"):
            validate_request(AdpContinueCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                expected_preference_surface_fingerprint=FP,
                entry_ordinal=0,
                expected_safe_fill_surface_fingerprint=FP,
                expected_post_fill_action_surface_fingerprint="abc",
                profile_manifest_path="profile.json",
            ))

    def test_rejects_non_continue_label(self):
        with self.assertRaisesRegex(ValueError, "ADP_CONTINUE_REQUIRES_CONTINUE_LABEL"):
            validate_request(AdpContinueCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                expected_preference_surface_fingerprint=FP,
                entry_ordinal=0,
                expected_safe_fill_surface_fingerprint=FP,
                expected_post_fill_action_surface_fingerprint=FP,
                profile_manifest_path="profile.json",
                expected_continue_label="Next",
            ))

    def test_known_live_action_surface_fingerprint(self):
        self.assertEqual(
            action_surface_fingerprint(reviewed_action_surface()),
            "0e3e60f904525ad95f5244ce99a29a335928b01992f066cc4da9d45753a7bd53",
        )

    def test_action_surface_changes_when_continue_disappears(self):
        changed = reviewed_action_surface()
        changed["visible_buttons"] = [
            item for item in changed["visible_buttons"] if item["label"] != "continue"
        ]
        self.assertNotEqual(
            action_surface_fingerprint(reviewed_action_surface()),
            action_surface_fingerprint(changed),
        )

    def test_route_auth_or_captcha_to_human_handoff(self):
        self.assertEqual(
            _route_after_continue(snapshot(captcha=True))["route"],
            "human_handoff",
        )
        self.assertEqual(
            _route_after_continue(snapshot(auth=True))["route"],
            "human_handoff",
        )

    def test_route_visible_controls_to_manifest_review(self):
        result = _route_after_continue(snapshot(controls=[{
            "tag": "input",
            "type": "text",
            "id": "x",
            "name": "x",
            "label": "Question",
            "required": True,
            "disabled": False,
            "visible": True,
            "accept": "",
            "multiple": False,
        }]))
        self.assertEqual(result["route"], "manifest_review_candidate")
        self.assertTrue(result["manifest_review_allowed"])
        self.assertFalse(result["automation_resume_allowed"])

    def test_route_empty_structure_to_diagnostic_review(self):
        result = _route_after_continue(snapshot())
        self.assertEqual(result["route"], "diagnostic_review")
        self.assertFalse(result["manifest_review_allowed"])


if __name__ == "__main__":
    unittest.main()
