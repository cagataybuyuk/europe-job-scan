import unittest

from ejs.services.adp_navigation_canary import (
    AdpNavigationCanaryRequest,
    _approved_entry,
    navigation_surface_descriptor,
    navigation_surface_fingerprint,
    next_route,
    validate_canary_request,
)


URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


def snapshot(*, captcha=False, auth=False, controls=None, code="", entries=None, state="application_entry_observed"):
    return {
        "runtime_state": state,
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
            expected_navigation_surface_fingerprint=FP,
            entry_ordinal=0,
        ))

    def test_rejects_invalid_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_NAVIGATION_SURFACE_FINGERPRINT"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint="abc",
                entry_ordinal=0,
            ))

    def test_rejects_invalid_ordinal(self):
        with self.assertRaisesRegex(ValueError, "INVALID_ADP_ENTRY_ORDINAL"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                entry_ordinal=-1,
            ))

    def test_rejects_non_apply_label(self):
        with self.assertRaisesRegex(ValueError, "REQUIRES_APPLY_LABEL"):
            validate_canary_request(AdpNavigationCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                entry_ordinal=0,
                expected_label="Continue",
            ))

    def test_entry_ordinal_resolves_live_observation_key(self):
        snap = snapshot(entries=[
            {"scope": "document", "observation_key": "document/button@121", "label": "Apply"},
            {"scope": "document", "observation_key": "document/button@167", "label": "Apply"},
        ])
        chosen = _approved_entry(snap, 1, "Apply")
        self.assertEqual(chosen["observation_key"], "document/button@167")
        with self.assertRaisesRegex(PermissionError, "ORDINAL_NOT_AVAILABLE"):
            _approved_entry(snap, 2, "Apply")

    def test_surface_fingerprint_ignores_observation_key_drift(self):
        left = snapshot(
            code="APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[
                {"scope": "document", "observation_key": "document/button@121", "label": "Apply"},
                {"scope": "document", "observation_key": "document/button@167", "label": "Apply"},
            ],
        )
        right = snapshot(
            code="APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[
                {"scope": "document", "observation_key": "document/button@133", "label": "Apply"},
                {"scope": "document", "observation_key": "document/button@179", "label": "Apply"},
            ],
        )
        self.assertEqual(navigation_surface_descriptor(left), navigation_surface_descriptor(right))
        self.assertEqual(navigation_surface_fingerprint(left), navigation_surface_fingerprint(right))

    def test_surface_fingerprint_changes_when_visible_entry_surface_changes(self):
        left = snapshot(
            code="APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[{"scope": "document", "observation_key": "document/button@121", "label": "Apply"}],
        )
        right = snapshot(
            code="APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[
                {"scope": "document", "observation_key": "document/button@121", "label": "Apply"},
                {"scope": "document", "observation_key": "document/button@167", "label": "Apply"},
            ],
        )
        self.assertNotEqual(navigation_surface_fingerprint(left), navigation_surface_fingerprint(right))

    def test_known_live_surface_fingerprint_is_stable(self):
        snap = snapshot(
            code="APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[
                {"scope": "document", "observation_key": "document/button@121", "label": "Apply"},
                {"scope": "document", "observation_key": "document/button@167", "label": "Apply"},
            ],
        )
        self.assertEqual(
            navigation_surface_fingerprint(snap),
            "567e7890f5a01f151dbeeb23851ad7cf5fb8100a32c3e0386227d17cd507d313",
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
