import unittest

from ejs.services.adp_continue_diagnostic_canary import (
    AdpContinueDiagnosticCanaryRequest,
    _verification_code_surface,
    post_continue_diagnostics,
    sanitize_text,
    validate_request,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


class FakePage:
    def locator(self, selector):
        return FakeLocatorSet([])

    def get_by_role(self, role):
        return FakeLocatorSet([])

    def evaluate(self, script):
        return [] if "rows" in script else {}


class FakeLocatorSet:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class AdpContinueDiagnosticCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_request(AdpContinueDiagnosticCanaryRequest(
            application_url=URL,
            expected_navigation_surface_fingerprint=FP,
            expected_preference_surface_fingerprint=FP,
            entry_ordinal=0,
            expected_safe_fill_surface_fingerprint=FP,
            expected_post_fill_action_surface_fingerprint=FP,
            profile_manifest_path="profile.json",
        ))

    def test_rejects_bad_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_POST_FILL_ACTION_SURFACE_FINGERPRINT"):
            validate_request(AdpContinueDiagnosticCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                expected_preference_surface_fingerprint=FP,
                entry_ordinal=0,
                expected_safe_fill_surface_fingerprint=FP,
                expected_post_fill_action_surface_fingerprint="bad",
                profile_manifest_path="profile.json",
            ))

    def test_sanitize_text_redacts_candidate_and_generic_contact_data(self):
        profile = {
            "candidate.first_name": "Ada",
            "candidate.last_name": "Lovelace",
            "candidate.email": "ada@example.test",
        }
        raw = "Ada Lovelace ada@example.test other@example.com +90 555 123 45 67"
        value = sanitize_text(raw, profile)
        self.assertNotIn("Ada", value)
        self.assertNotIn("Lovelace", value)
        self.assertNotIn("ada@example.test", value)
        self.assertNotIn("other@example.com", value)
        self.assertNotIn("555 123", value)
        self.assertIn("[REDACTED_CANDIDATE_VALUE]", value)
        self.assertIn("[REDACTED_EMAIL]", value)
        self.assertIn("[REDACTED_PHONE]", value)

    def test_verification_surface_requires_exact_email_otp_evidence(self):
        visible_controls = [{
            "id": "oneTimePassWord",
            "label": "Enter the Verification Code",
            "required": True,
            "disabled": False,
        }]
        button_surface = {"visible_buttons": [{"label": "verify", "enabled": False}]}
        alerts = [{"text": "Verification Code sent to your email address"}]
        result = _verification_code_surface(visible_controls, button_surface, alerts)
        self.assertTrue(result["observed"])
        self.assertEqual(result["channel"], "email")
        self.assertTrue(result["control_required"])
        self.assertTrue(result["verify_button_present"])
        self.assertFalse(result["verify_button_enabled"])
        self.assertFalse(result["raw_code_exposed"])

    def test_verification_surface_rejects_partial_evidence(self):
        result = _verification_code_surface(
            [{"id": "oneTimePassWord", "label": "Enter the Verification Code", "required": True, "disabled": False}],
            {"visible_buttons": [{"label": "verify", "enabled": False}]},
            [],
        )
        self.assertFalse(result["observed"])

    def test_diagnostics_classifies_persisted_identity_surface(self):
        snapshot = {
            "runtime_state": "inspected",
            "captcha_observed": False,
            "auth_observed": False,
            "form": {"controls": [
                {"tag": "input", "type": "text", "id": "guestFirstName", "name": "", "label": "First Name", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False},
                {"tag": "input", "type": "text", "id": "guestLastName", "name": "", "label": "Last Name", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False},
                {"tag": "input", "type": "text", "id": "guestEmail", "name": "Email", "label": "Email", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False},
                {"tag": "select", "type": "select-one", "id": "", "name": "phoneCountry", "label": "Phone number country", "required": False, "disabled": False, "visible": True, "accept": "", "multiple": False},
                {"tag": "input", "type": "tel", "id": "login_view_phone", "name": "phone", "label": "Mobile Number", "required": False, "disabled": False, "visible": True, "accept": "", "multiple": False},
            ]},
        }
        from ejs.services.adp_safe_fill_canary import safe_fill_surface_fingerprint
        fp = safe_fill_surface_fingerprint(snapshot)
        result = post_continue_diagnostics(FakePage(), {}, snapshot, fp)
        self.assertTrue(result["same_identity_surface"])
        self.assertEqual(result["diagnosis"], "no_stage_transition_identity_surface_persisted")
        self.assertEqual(result["visible_application_control_count"], 5)


if __name__ == "__main__":
    unittest.main()
