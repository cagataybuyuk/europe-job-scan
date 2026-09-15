import unittest

from ejs.services.smartrecruiters_live_route import ROUTE_VERSION, route_live_inspection


def report(**overrides):
    value = {
        "runtime_state": "form_structure_not_discovered",
        "error_code": "FORM_STRUCTURE_NOT_DISCOVERED",
        "captcha_observed": False,
        "inspection_only": True,
        "live_execution_ready": False,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "form": {"controls": [], "actions": []},
    }
    value.update(overrides)
    return value


class LiveInspectionRouteTests(unittest.TestCase):
    def test_captcha_boundary_routes_to_human_handoff(self):
        decision = route_live_inspection(report(
            runtime_state="captcha_boundary",
            error_code="CAPTCHA_BOUNDARY",
            captcha_observed=True,
        ))
        self.assertEqual(decision["route_version"], ROUTE_VERSION)
        self.assertEqual(decision["route"], "human_handoff")
        self.assertEqual(decision["reason_code"], "CAPTCHA_BOUNDARY")
        self.assertTrue(decision["human_action_required"])
        self.assertFalse(decision["manifest_review_allowed"])
        self.assertFalse(decision["automation_resume_allowed"])
        self.assertFalse(decision["safe_fill_allowed"])
        self.assertFalse(decision["final_submit_allowed"])

    def test_captcha_signal_is_fail_closed_even_if_runtime_state_is_stale(self):
        decision = route_live_inspection(report(captcha_observed=True))
        self.assertEqual(decision["route"], "human_handoff")
        self.assertEqual(decision["reason_code"], "CAPTCHA_BOUNDARY")

    def test_inspected_controls_only_create_manifest_review_candidate(self):
        decision = route_live_inspection(report(
            runtime_state="inspected",
            error_code="",
            form={"controls": [{"observation_key": "document/input@1"}], "actions": []},
        ))
        self.assertEqual(decision["route"], "manifest_review_candidate")
        self.assertEqual(decision["reason_code"], "INSPECTABLE_CONTROLS_OBSERVED")
        self.assertTrue(decision["manifest_review_allowed"])
        self.assertFalse(decision["automation_resume_allowed"])
        self.assertFalse(decision["safe_fill_allowed"])
        self.assertFalse(decision["final_submit_allowed"])

    def test_empty_or_unknown_structure_routes_to_diagnostic_review(self):
        decision = route_live_inspection(report())
        self.assertEqual(decision["route"], "diagnostic_review")
        self.assertEqual(decision["reason_code"], "FORM_STRUCTURE_NOT_DISCOVERED")
        self.assertFalse(decision["manifest_review_allowed"])

    def test_router_rejects_any_side_effect_evidence(self):
        for key in ("form_value_write_attempts", "file_upload_attempts", "submit_attempts"):
            with self.subTest(key=key), self.assertRaises(PermissionError):
                route_live_inspection(report(**{key: 1}))

    def test_router_requires_inspection_only_and_execution_not_ready(self):
        with self.assertRaises(PermissionError):
            route_live_inspection(report(inspection_only=False))
        with self.assertRaises(PermissionError):
            route_live_inspection(report(live_execution_ready=True))


if __name__ == "__main__":
    unittest.main()
