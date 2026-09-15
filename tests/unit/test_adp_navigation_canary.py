import unittest

from ejs.services.adp_navigation_canary import (
    AdpNavigationCanaryRequest,
    _approved_entry,
    next_route,
    validate_canary_request,
)


URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


def snapshot(*, captcha=False, auth=False, controls=None, code="", entries=None):
    return {
        "captcha_observed": captcha,
        "auth_observed": auth,
        "visible_application_control_keys": controls or [],
        "error_code": code,
        "application_entry_actions": entries or [],
    }


class AdpNavigationCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_canary_request(AdpNavigationCanaryRequest(
            application_url=URL,
            expected_schema_fingerprint=FP,
            entry_observation_key="document/button@121",
        ))

    def test_rejects_invalid_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_SCHEMA_FINGERPRINT"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_schema_fingerprint="abc",
                entry_observation_key="document/button@121",
            ))

    def test_rejects_shadow_or_frame_observation_key(self):
        with self.assertRaisesRegex(ValueError, "REQUIRES_DOCUMENT_OBSERVATION_KEY"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_schema_fingerprint=FP,
                entry_observation_key="frame:1/document/button@3",
            ))

    def test_rejects_non_apply_label(self):
        with self.assertRaisesRegex(ValueError, "REQUIRES_APPLY_LABEL"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_schema_fingerprint=FP,
                entry_observation_key="document/button@121",
                expected_label="Continue",
            ))

    def test_exact_entry_is_required(self):
        snap = snapshot(entries=[
            {"observation_key": "document/button@121", "label": "Apply"},
            {"observation_key": "document/button@167", "label": "Apply"},
        ])
        chosen = _approved_entry(snap, "document/button@121", "Apply")
        self.assertEqual(chosen["observation_key"], "document/button@121")
        with self.assertRaisesRegex(PermissionError, "NOT_UNIQUE"):
            _approved_entry(
                snapshot(entries=[
                    {"observation_key": "document/button@121", "label": "Apply"},
                    {"observation_key": "document/button@121", "label": "Apply"},
                ]),
                "document/button@121",
                "Apply",
            )

    def test_captcha_routes_to_human_handoff(self):
        route = next_route(snapshot(captcha=True))
        self.assertEqual(route["route"], "human_handoff")
        self.assertEqual(route["reason_code"], "CAPTCHA_BOUNDARY")
        self.assertFalse(route["safe_fill_allowed"])
        self.assertFalse(route["final_submit_allowed"])

    def test_auth_routes_to_human_handoff(self):
        route = next_route(snapshot(auth=True))
        self.assertEqual(route["route"], "human_handoff")
        self.assertEqual(route["reason_code"], "ADP_AUTH_BOUNDARY")

    def test_visible_controls_route_to_manifest_review(self):
        route = next_route(snapshot(controls=["document/input@9"]))
        self.assertEqual(route["route"], "manifest_review_candidate")
        self.assertFalse(route["safe_fill_allowed"])
        self.assertFalse(route["final_submit_allowed"])

    def test_no_controls_routes_to_diagnostic_review(self):
        route = next_route(snapshot(code="FORM_STRUCTURE_NOT_DISCOVERED"))
        self.assertEqual(route["route"], "diagnostic_review")
        self.assertEqual(route["reason_code"], "FORM_STRUCTURE_NOT_DISCOVERED")


if __name__ == "__main__":
    unittest.main()
