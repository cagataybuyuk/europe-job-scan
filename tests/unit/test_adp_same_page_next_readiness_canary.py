from __future__ import annotations

import unittest
import json
import shutil
import subprocess
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
                "auth_observed": False,
                "captcha_observed": False,
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
                "visible_error_class_count": 0,
                "explicit_address_error_count": 0,
                "blocking_issue_node_count": 0,
                "informational_alert_count": 0,
                "structural_nodes": [],
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
                "auth_observed": False,
                "captcha_observed": False,
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
                "visible_error_class_count": 0,
                "explicit_address_error_count": 1,
                "blocking_issue_node_count": 1,
                "informational_alert_count": 0,
                "structural_nodes": [{
                    "ordinal": 0,
                    "id": "addressErrorMessage",
                    "role": "alert",
                    "validation_message_text": "SYNTHETIC_PRIVATE_MESSAGE",
                    "parent_id": "SYNTHETIC_PRIVATE_ATTRIBUTE",
                    "text_read": True,
                    "value_read": False,
                }],
            },
        ):
            report = inspect_on_verified_page(page, self.request(), safe_fill_report())

        self.assertEqual(report["readiness_status"], "blocked")
        self.assertEqual(report["visible_issue_node_count"], 1)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(report))
        self.assertFalse(report["validation_structural_nodes"][0]["text_read"])
        self.assertEqual(report["next_click_attempts"], 0)

    def test_informational_alerts_do_not_block_readiness(self):
        page = self.make_page()
        with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
            canary,
            "_snapshot",
            return_value={
                "auth_observed": False,
                "captcha_observed": False,
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
                "visible_issue_node_count": 2,
                "visible_alert_count": 2,
                "visible_aria_invalid_count": 0,
                "visible_error_class_count": 0,
                "explicit_address_error_count": 0,
                "blocking_issue_node_count": 0,
                "informational_alert_count": 2,
                "structural_nodes": [
                    {"ordinal": 0, "tag": "sdf-alert", "role": "alert"},
                    {"ordinal": 1, "tag": "sdf-alert", "role": "alert"},
                ],
            },
        ):
            report = inspect_on_verified_page(page, self.request(), safe_fill_report())

        self.assertEqual(report["readiness_status"], "ready")
        self.assertEqual(report["blocking_issue_node_count"], 0)
        self.assertEqual(report["informational_alert_count"], 2)
        self.assertEqual(report["next_click_attempts"], 0)

    def test_disabled_next_blocks_readiness(self):
        page = self.make_page(next_enabled=False)
        with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
            canary,
            "_snapshot",
            return_value={
                "auth_observed": False,
                "captcha_observed": False,
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
                "visible_error_class_count": 0,
                "explicit_address_error_count": 0,
                "blocking_issue_node_count": 0,
                "informational_alert_count": 0,
                "structural_nodes": [],
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
            return_value={"auth_observed": False, "captcha_observed": False, "form": {"actions": []}},
        ):
            with self.assertRaisesRegex(PermissionError, "NEXT_ACTION_COUNT:0"):
                inspect_on_verified_page(page, self.request(), safe_fill_report())

    def test_unverified_safe_fill_fails_closed(self):
        page = self.make_page()
        report = safe_fill_report()
        report["safe_fill_status"] = "failed"
        with self.assertRaisesRegex(PermissionError, "REQUIRES_VERIFIED_SAFE_FILL"):
            inspect_on_verified_page(page, self.request(), report)

    def test_auth_challenge_or_unknown_evidence_stops_before_control_inspection(self):
        cases = [
            {"auth_observed": True, "captcha_observed": False},
            {"auth_observed": False, "captcha_observed": True},
            {"auth_observed": False},
            {"captcha_observed": False},
            {"auth_observed": "false", "captcha_observed": False},
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot):
                page = self.make_page()
                with patch.object(canary, "_target_binding", return_value={"target_bound": True}), patch.object(
                    canary, "_snapshot", return_value=snapshot
                ), patch.object(canary, "_required_control_status") as controls, patch.object(
                    canary, "_visible_validation_surface"
                ) as validation:
                    with self.assertRaisesRegex(PermissionError, "AUTH_OR_CHALLENGE_BOUNDARY"):
                        inspect_on_verified_page(page, self.request(), safe_fill_report())
                controls.assert_not_called()
                validation.assert_not_called()
                page._test_next_button.click.assert_not_called()

    def test_validation_evidence_discards_unreviewed_attributes(self):
        nodes = [{"id": "SYNTHETIC_PRIVATE_ID", "role": "alert",
                  "validation_message_text": "SYNTHETIC_PRIVATE_MESSAGE",
                  "class_name": "SYNTHETIC_PRIVATE_CLASS", "text_read": True}]
        result = canary._validation_node_evidence(nodes)
        self.assertNotIn("SYNTHETIC_PRIVATE", repr(result))
        self.assertEqual(result[0]["id"], "unreviewed")
        self.assertFalse(result[0]["text_read"])

    @unittest.skipUnless(shutil.which("node"), "Node required for actual diagnostic JavaScript")
    def test_actual_validation_script_never_reads_text_and_detects_plain_address_error(self):
        page = MagicMock()

        def evaluate(script):
            harness = r"""
                const el = {
                    id: 'addressErrorMessage', tagName: 'DIV',
                    classList: {contains: () => false},
                    getAttribute: name => name === 'id' ? 'addressErrorMessage' : null,
                    getBoundingClientRect: () => ({width: 100, height: 20}),
                    get innerText() { throw new Error('private text must not be read'); }
                };
                globalThis.window = {getComputedStyle: () => ({display: 'block', visibility: 'visible'})};
                globalThis.document = {querySelectorAll: selector =>
                    selector.includes('#addressErrorMessage') ? [el] : []};
            """
            completed = subprocess.run(
                [shutil.which("node"), "-e", harness + "console.log(JSON.stringify((" + script + ")()));"],
                capture_output=True, text=True, timeout=10, check=True,
            )
            return json.loads(completed.stdout)

        page.evaluate.side_effect = evaluate
        result = canary._visible_validation_surface(page)
        self.assertEqual(result["blocking_issue_node_count"], 1)
        self.assertEqual(result["explicit_address_error_count"], 1)
        self.assertNotIn("validation_message_text", repr(result))
        self.assertFalse(result["structural_nodes"][0]["text_read"])


if __name__ == "__main__":
    unittest.main()
