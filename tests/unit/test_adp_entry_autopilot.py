from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_entry_autopilot as autopilot
from ejs.services.adp_entry_autopilot import (
    AdpEntryAutopilotRequest,
    advance_to_otp_on_existing_page,
    click_complete_application_on_portal,
    click_verify_if_user_populated_otp,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=955507"
)


class AdpEntryAutopilotTests(unittest.TestCase):
    def request(self):
        return AdpEntryAutopilotRequest(
            application_url=URL,
            profile_json_path="profile.json",
        )

    def test_advance_to_otp_uses_reviewed_actions_and_stops_at_otp(self):
        page = MagicMock()

        apply_locator = MagicMock()
        apply_locator.is_visible.return_value = True
        apply_locator.is_enabled.return_value = True
        apply_locator.get_attribute.return_value = "Apply"

        continue_button = MagicMock()
        continue_button.count.return_value = 1
        continue_button.is_visible.return_value = True
        continue_button.is_enabled.return_value = True
        page.get_by_role.return_value = continue_button

        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_enabled.return_value = True
        page.locator.return_value = otp

        profile = MagicMock()
        snapshots = [
            {"captcha_observed": False, "auth_observed": False},
            {"captcha_observed": False, "auth_observed": False},
            {"captcha_observed": False, "auth_observed": False},
        ]

        with patch.object(autopilot, "load_resolved_profile", return_value=profile), patch.object(
            autopilot, "_snapshot", side_effect=snapshots
        ), patch.object(
            autopilot, "navigation_surface_fingerprint", return_value=autopilot.EXPECTED_NAVIGATION_FP
        ), patch.object(
            autopilot, "preference_surface_descriptor", return_value={"banner_visible": False}
        ), patch.object(
            autopilot, "_approved_entry", return_value={"observation_key": "document/button@1"}
        ), patch.object(
            autopilot, "_resolve_document_locator", return_value=apply_locator
        ), patch.object(
            autopilot, "safe_fill_surface_fingerprint", return_value=autopilot.EXPECTED_SAFE_FILL_FP
        ), patch.object(
            autopilot, "phone_contract_descriptor", return_value={}
        ), patch.object(
            autopilot, "phone_contract_surface_fingerprint", return_value=autopilot.EXPECTED_PHONE_FP
        ), patch.object(
            autopilot, "_write_identity_fields", return_value=[{}, {}, {}]
        ), patch.object(
            autopilot, "_write_phone_fields", return_value={"phone_readback_match": True}
        ), patch.object(
            autopilot, "action_surface_descriptor", return_value={}
        ), patch.object(
            autopilot, "action_surface_fingerprint", return_value=autopilot.EXPECTED_ACTION_FP
        ):
            report = advance_to_otp_on_existing_page(page, self.request())

        self.assertEqual(report["autopilot_status"], "waiting_for_otp")
        self.assertEqual(report["apply_click_attempts"], 1)
        self.assertEqual(report["apply_click_successes"], 1)
        self.assertEqual(report["continue_click_attempts"], 1)
        self.assertEqual(report["continue_click_successes"], 1)
        self.assertEqual(report["otp_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["raw_values_exposed"])

    def test_verify_click_requires_user_populated_six_digit_otp_and_never_returns_value(self):
        page = MagicMock()
        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_visible.return_value = True
        otp.is_enabled.return_value = True
        otp.input_value.return_value = "123456"
        page.locator.return_value = otp

        verify = MagicMock()
        verify.count.return_value = 1
        verify.is_visible.return_value = True
        verify.is_enabled.return_value = True
        page.get_by_role.return_value = verify

        counters = {"verify_click_attempts": 0, "verify_click_successes": 0}
        clicked = click_verify_if_user_populated_otp(page, counters)

        self.assertTrue(clicked)
        verify.click.assert_called_once()
        self.assertEqual(counters["verify_click_attempts"], 1)
        self.assertEqual(counters["verify_click_successes"], 1)
        self.assertNotIn("123456", repr(counters))

    def test_verify_does_not_click_for_incomplete_otp(self):
        page = MagicMock()
        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_visible.return_value = True
        otp.is_enabled.return_value = True
        otp.input_value.return_value = "123"
        page.locator.return_value = otp

        counters = {"verify_click_attempts": 0, "verify_click_successes": 0}
        self.assertFalse(click_verify_if_user_populated_otp(page, counters))
        page.get_by_role.assert_not_called()

    def test_complete_application_click_is_exactly_once(self):
        page = MagicMock()
        candidate = {
            "scope": "document",
            "observation_key": "document/button@9",
            "label": "Complete Your Application",
            "visible": True,
        }
        locator = MagicMock()
        locator.is_visible.return_value = True
        locator.is_enabled.return_value = True
        counters = {
            "complete_application_click_attempts": 0,
            "complete_application_click_successes": 0,
        }

        with patch.object(
            autopilot, "_target_binding", return_value={"target_bound": True}
        ), patch.object(
            autopilot, "_snapshot", return_value={"form": {"actions": [candidate]}}
        ), patch.object(
            autopilot, "_resolve_document_locator", return_value=locator
        ):
            clicked = click_complete_application_on_portal(page, URL, counters)
            clicked_again = click_complete_application_on_portal(page, URL, counters)

        self.assertTrue(clicked)
        self.assertFalse(clicked_again)
        locator.click.assert_called_once()
        self.assertEqual(counters["complete_application_click_attempts"], 1)
        self.assertEqual(counters["complete_application_click_successes"], 1)


if __name__ == "__main__":
    unittest.main()
