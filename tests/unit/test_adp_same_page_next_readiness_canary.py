from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_next_readiness_canary as canary
from ejs.services.adp_same_page_next_readiness_canary import (
    AdpSamePageNextReadinessRequest,
    inspect_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
STATE_FP = "d" * 64


def safe_fill_report():
    return {
        "safe_fill_status": "verified",
        "next_click_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
        "expected_state_option_surface_fingerprint": STATE_FP,
        "identity_result": {"email_readback_match": True},
        "mobile_phone_result": {"phone_readback_match": True},
        "address_country_result": {"readback_match": True},
        "address_state_result": {"readback_match": True},
    }


class NextReadinessCanaryTests(unittest.TestCase):
    def make_page(self, *, next_enabled=True, valid=True):
        page = MagicMock()
        page.url = (
            "https://workforcenow.adp.com/mascsr/applicant/mdf/recruitment/"
            "postLogin.html?cid=test&ccId=19000101_000001&jobId=960970"
        )

        required = {}
        for element_id in canary.REVIEWED_REQUIRED_IDS:
            loc = MagicMock()
            loc.count.return_value = 1
            loc.is_visible.return_value = True
            loc.is_enabled.return_value = True
            loc.evaluate.return_value = valid
            loc.get_attribute.return_value = "false"
            required[f"#{element_id}"] = loc

        next_button = MagicMock()
        next_button.is_visible.return_value = True
        next_button.is_enabled.return_value = next_enabled

        page.locator.side_effect = lambda selector: required[selector]
        page._test_next_button = next_button
        return page

    def request(self):
        return AdpSamePageNextReadinessRequest(
            application_url=URL,
            expected_state_option_surface_fingerprint=STATE_FP,
        )

    def test_ready_surface_has_zero_mutation_authority(self):
        page = self.make_page()
        with patch.object(
            canary,
            "_target_binding",
            return_value={"target_bound": True},
        ), patch.object(
            canary,
            "_snapshot",
            return_value={
                "form": {
                    "actions": [{
                        "scope": "document",
                        "observation_key": "document/button@7",
                        "label": "Next",
                        "type": "button",
                        "visible": True,
                        "disabled": False,
                    }]
                }
            },
        ), patch.object(
            canary,
            "_resolve_document_locator",
            return_value=page._test_next_button,
        ), patch.object(
            canary,
            "_visible_validation_surface",
            return_value={
                "visible_issue_node_count": 0,
                "visible_alert_count": 0,
                "visible_aria_invalid_count": 0,
            },
        ):
            report = inspect_on_verified_page(
                page,
                self.request(),
                safe_fill_report(),
            )

        self.assertEqual(report["readiness_status"], "ready")
        self.assertEqual(report["invalid_required_control_count"], 0)
        self.assertTrue(report["next_enabled"])
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["next_click_allowed"])
        self.assertFalse(report["raw_values_exposed"])

    def test_visible_validation_issue_blocks_readiness(self):
        page = self.make_page()
        with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
            canary,
            "_snapshot",
            return_value={
                "form": {
                    "actions": [{
                        "scope": "document",
                        "observation_key": "document/button@7",
                        "label": "Continue",
                        "type": "button",
                        "visible": True,
                        "disabled": False,
                    }]
                }
            },
        ), patch.object(
            canary,
            "_resolve_document_locator",
            return_value=page._test_next_button,
        ), patch.object(
            canary,
            "_visible_validation_surface",
            return_value={
                "visible_issue_node_count": 1,
                "visible_alert_count": 1,
                "visible_aria_invalid_count": 0,
            },
        ):
            report = inspect_on_verified_page(page, self.request(), safe_fill_report())

        self.assertEqual(report["readiness_status"], "blocked")
        self.assertEqual(report["visible_issue_node_count"], 1)
        self.assertEqual(report["next_click_attempts"], 0)

    def test_disabled_next_blocks_readiness(self):
        page = self.make_page(next_enabled=False)
        with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
            canary,
            "_snapshot",
            return_value={
                "form": {
                    "actions": [{
                        "scope": "document",
                        "observation_key": "document/input@9",
                        "label": "Proceed",
                        "type": "submit",
                        "visible": True,
                        "disabled": False,
                    }]
                }
            },
        ), patch.object(
            canary,
            "_resolve_document_locator",
            return_value=page._test_next_button,
        ), patch.object(
            canary,
            "_visible_validation_surface",
            return_value={
                "visible_issue_node_count": 0,
                "visible_alert_count": 0,
                "visible_aria_invalid_count": 0,
            },
        ):
            report = inspect_on_verified_page(page, self.request(), safe_fill_report())

        self.assertEqual(report["readiness_status"], "blocked")
        self.assertFalse(report["next_enabled"])

    def test_missing_reviewed_next_action_fails_closed(self):
        page = self.make_page()
        with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
            canary,
            "_snapshot",
            return_value={"form": {"actions": []}},
        ):
            with self.assertRaisesRegex(PermissionError, "NEXT_ACTION_COUNT:0"):
                inspect_on_verified_page(page, self.request(), safe_fill_report())

    def test_unverified_safe_fill_fails_closed(self):
        page = self.make_page()
        report = safe_fill_report()
        report["safe_fill_status"] = "failed"
        with self.assertRaisesRegex(PermissionError, "REQUIRES_VERIFIED_SAFE_FILL"):
            inspect_on_verified_page(page, self.request(), report)


if __name__ == "__main__":
    unittest.main()
