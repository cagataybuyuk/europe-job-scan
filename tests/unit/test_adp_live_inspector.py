import unittest

from ejs.services.adp_live_inspector import (
    application_entry_actions,
    classify_adp_state,
    is_challenge_frame_url,
    validate_adp_live_url,
)
from ejs.services.adp_live_route import route_adp_live_inspection


ADP_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)


def form(*, controls=None, actions=None):
    return {
        "controls": controls or [],
        "actions": actions or [],
    }


def report(state, code="", *, controls=None, entries=None, captcha=False, **counts):
    payload = {
        "runtime_state": state,
        "error_code": code,
        "captcha_observed": captcha,
        "inspection_only": True,
        "live_execution_ready": False,
        "form": form(controls=controls),
        "application_entry_actions": entries or [],
        "navigation_click_attempts": 0,
        "credential_entry_attempts": 0,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
    }
    payload.update(counts)
    return payload


class AdpLiveInspectorTests(unittest.TestCase):
    def test_accepts_exact_workforcenow_host(self):
        validate_adp_live_url(ADP_URL)

    def test_rejects_non_adp_host(self):
        with self.assertRaisesRegex(ValueError, "REQUIRES_WORKFORCENOW_ADP"):
            validate_adp_live_url("https://example.com/job/1")

    def test_rejects_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "REJECTS_EMBEDDED_CREDENTIALS"):
            validate_adp_live_url("https://user:pass@workforcenow.adp.com/job")

    def test_recaptcha_frame_is_challenge(self):
        self.assertTrue(is_challenge_frame_url("https://www.google.com/recaptcha/api2/anchor"))
        self.assertTrue(is_challenge_frame_url("https://newassets.hcaptcha.com/captcha/v1/index.html"))
        self.assertFalse(is_challenge_frame_url("https://workforcenow.adp.com/static/frame.html"))

    def test_apply_action_is_entry_candidate(self):
        actions = [{
            "scope": "document",
            "observation_key": "document/button@3",
            "label": "Apply Now",
            "visible": True,
        }]
        entries = application_entry_actions(form(actions=actions))
        self.assertEqual(len(entries), 1)
        state, code, found = classify_adp_state(
            form=form(actions=actions),
            body_text="Job details",
            captcha_observed=False,
        )
        self.assertEqual(state, "application_entry_observed")
        self.assertEqual(code, "APPLICATION_ENTRY_REQUIRES_NAVIGATION")
        self.assertEqual(found, entries)

    def test_controls_win_over_auth_text(self):
        controls = [{"observation_key": "document/input@1"}]
        state, code, _ = classify_adp_state(
            form=form(controls=controls),
            body_text="Already have an account?",
            captcha_observed=False,
        )
        self.assertEqual(state, "inspected")
        self.assertEqual(code, "")

    def test_auth_boundary_without_controls_or_entry(self):
        state, code, _ = classify_adp_state(
            form=form(),
            body_text="Already have an account? Sign in to continue",
            captcha_observed=False,
        )
        self.assertEqual(state, "auth_boundary")
        self.assertEqual(code, "ADP_AUTH_BOUNDARY")

    def test_captcha_overrides_everything(self):
        state, code, _ = classify_adp_state(
            form=form(controls=[{"observation_key": "document/input@1"}]),
            body_text="",
            captcha_observed=True,
        )
        self.assertEqual(state, "captcha_boundary")
        self.assertEqual(code, "CAPTCHA_BOUNDARY")


class AdpLiveRouteTests(unittest.TestCase):
    def assert_fully_blocked(self, decision):
        self.assertFalse(decision["automation_resume_allowed"])
        self.assertFalse(decision["navigation_click_allowed"])
        self.assertFalse(decision["safe_fill_allowed"])
        self.assertFalse(decision["final_submit_allowed"])

    def test_entry_routes_to_navigation_review_only(self):
        decision = route_adp_live_inspection(report(
            "application_entry_observed",
            "APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            entries=[{"observation_key": "document/button@3", "label": "Apply Now"}],
        ))
        self.assertEqual(decision["route"], "navigation_review_candidate")
        self.assertTrue(decision["navigation_review_allowed"])
        self.assertFalse(decision["manifest_review_allowed"])
        self.assert_fully_blocked(decision)

    def test_controls_route_to_manifest_review_only(self):
        decision = route_adp_live_inspection(report(
            "inspected",
            controls=[{"observation_key": "document/input@1"}],
        ))
        self.assertEqual(decision["route"], "manifest_review_candidate")
        self.assertTrue(decision["manifest_review_allowed"])
        self.assertFalse(decision["navigation_review_allowed"])
        self.assert_fully_blocked(decision)

    def test_auth_routes_to_human_handoff(self):
        decision = route_adp_live_inspection(report("auth_boundary", "ADP_AUTH_BOUNDARY"))
        self.assertEqual(decision["route"], "human_handoff")
        self.assertEqual(decision["reason_code"], "ADP_AUTH_BOUNDARY")
        self.assert_fully_blocked(decision)

    def test_captcha_routes_to_human_handoff(self):
        decision = route_adp_live_inspection(report(
            "captcha_boundary",
            "CAPTCHA_BOUNDARY",
            captcha=True,
        ))
        self.assertEqual(decision["route"], "human_handoff")
        self.assertEqual(decision["reason_code"], "CAPTCHA_BOUNDARY")
        self.assert_fully_blocked(decision)

    def test_any_side_effect_evidence_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "REJECTS_SIDE_EFFECT_EVIDENCE"):
            route_adp_live_inspection(report(
                "application_entry_observed",
                "APPLICATION_ENTRY_REQUIRES_NAVIGATION",
                entries=[{"observation_key": "document/button@3", "label": "Apply"}],
                navigation_click_attempts=1,
            ))

    def test_execution_ready_report_is_rejected(self):
        payload = report("form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED")
        payload["live_execution_ready"] = True
        with self.assertRaisesRegex(PermissionError, "EXECUTION_NOT_READY"):
            route_adp_live_inspection(payload)


if __name__ == "__main__":
    unittest.main()
