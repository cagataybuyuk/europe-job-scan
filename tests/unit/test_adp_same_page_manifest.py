from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_manifest as manifest
from ejs.services.adp_same_page_manifest import (
    extract_same_page_manifest,
    manifest_surface_fingerprint,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid=test&ccId=19000101_000001&jobId=960970&source=LR&lang=en_US"
)
POSTLOGIN = (
    "https://workforcenow.adp.com/mascsr/applicant/mdf/recruitment/postLogin.html"
    "?cid=test&ccId=19000101_000001&jobId=960970&jobId=960970"
    "&requisitionId=opaque_1&params=jobId&OTP_login=true"
)


class AdpSamePageManifestTests(unittest.TestCase):
    def page_with_steps(self):
        page = MagicMock()
        page.url = POSTLOGIN
        exact_labels = {
            "Personal Information",
            "Resume",
            "Questions",
            "Self-Attest & Submit",
        }

        def get_by_text(label, **kwargs):
            exact = kwargs.get("exact")
            locator = MagicMock()
            if label == "Review Your Application" and exact is True:
                locator.count.return_value = 0
                return locator
            if label == "Review Your Application" and exact is False:
                locator.count.return_value = 1
                locator.nth.return_value.is_visible.return_value = True
                locator.nth.return_value.inner_text.return_value = "Review\n  Your Application"
                return locator
            locator.count.return_value = int(label in exact_labels)
            locator.nth.return_value.is_visible.return_value = label in exact_labels
            locator.nth.return_value.inner_text.return_value = label
            return locator

        page.get_by_text.side_effect = get_by_text
        return page

    def test_verified_same_page_manifest_is_value_free_and_read_only(self):
        page = self.page_with_steps()
        snapshot = {
            "runtime_state": "inspected",
            "captcha_observed": False,
            "auth_observed": False,
            "form": {
                "controls": [
                    {
                        "observation_key": "document/input@1",
                        "scope": "document",
                        "tag": "input",
                        "type": "text",
                        "id": "firstName",
                        "name": "firstName",
                        "label": "First Name",
                        "role": "",
                        "visible": True,
                        "disabled": False,
                        "required": True,
                        "host_required_hint": False,
                        "accept": "",
                        "multiple": False,
                        "value": "SHOULD_NOT_LEAK",
                    },
                    {
                        "observation_key": "document/input@2",
                        "scope": "document",
                        "tag": "input",
                        "type": "file",
                        "id": "resume",
                        "name": "resume",
                        "label": "Resume",
                        "role": "",
                        "visible": True,
                        "disabled": False,
                        "required": False,
                        "host_required_hint": False,
                        "accept": ".pdf,.doc,.docx",
                        "multiple": False,
                        "files": ["SHOULD_NOT_LEAK.pdf"],
                    },
                ],
                "actions": [
                    {
                        "observation_key": "document/button@3",
                        "scope": "document",
                        "label": "Next",
                        "type": "button",
                        "visible": True,
                        "disabled": False,
                    }
                ],
            },
        }
        with patch.object(manifest, "_snapshot", return_value=snapshot):
            result = extract_same_page_manifest(page, URL)

        self.assertTrue(result["same_page_verified_surface"])
        self.assertTrue(result["target_binding"]["target_bound"])
        self.assertEqual(result["visible_control_count"], 2)
        self.assertEqual(result["visible_action_count"], 1)
        self.assertEqual(result["file_control_count"], 1)
        self.assertEqual(result["password_control_count"], 0)
        self.assertEqual(result["form_value_write_attempts"], 0)
        self.assertEqual(result["file_upload_attempts"], 0)
        self.assertEqual(result["submit_attempts"], 0)
        self.assertFalse(result["safe_fill_allowed"])
        self.assertFalse(result["file_upload_allowed"])
        self.assertFalse(result["final_submit_allowed"])
        self.assertFalse(result["candidate_values_exposed"])
        self.assertFalse(result["raw_values_exposed"])
        self.assertNotIn("SHOULD_NOT_LEAK", repr(result))
        self.assertEqual(result["surface_fingerprint"], manifest_surface_fingerprint(result))

    def test_fingerprint_ignores_observation_key_but_detects_structural_drift(self):
        base = {
            "steps": {
                "personal_information": True,
                "resume": True,
                "questions": True,
                "review_application": True,
                "self_attest_submit": True,
            },
            "controls": [
                {
                    "observation_key": "document/input@10",
                    "scope": "document",
                    "tag": "input",
                    "type": "text",
                    "id": "personalInfomationFirstName",
                    "name": "firstName",
                    "label": "First Name*",
                    "role": "",
                    "required": True,
                    "host_required_hint": False,
                    "disabled": False,
                    "accept": "",
                    "multiple": False,
                }
            ],
            "actions": [
                {
                    "observation_key": "document/button@20",
                    "scope": "document",
                    "label": "Next",
                    "type": "button",
                    "disabled": False,
                }
            ],
        }
        ordinal_only = json.loads(json.dumps(base))
        ordinal_only["controls"][0]["observation_key"] = "document/input@77"
        ordinal_only["actions"][0]["observation_key"] = "document/button@88"

        structural = json.loads(json.dumps(base))
        structural["controls"][0]["required"] = False

        self.assertEqual(
            manifest_surface_fingerprint(base),
            manifest_surface_fingerprint(ordinal_only),
        )
        self.assertNotEqual(
            manifest_surface_fingerprint(base),
            manifest_surface_fingerprint(structural),
        )

    def test_conflicting_duplicate_jobid_is_rejected(self):
        page = self.page_with_steps()
        page.url = POSTLOGIN + "&jobId=1"
        with self.assertRaisesRegex(PermissionError, "TARGET_MISMATCH"):
            extract_same_page_manifest(page, URL)

    def test_missing_reviewed_step_is_rejected_before_snapshot(self):
        page = self.page_with_steps()

        def get_by_text(label, **kwargs):
            locator = MagicMock()
            visible = label != "Questions"
            locator.count.return_value = 1 if visible else 0
            locator.nth.return_value.is_visible.return_value = visible
            locator.nth.return_value.inner_text.return_value = label
            return locator

        page.get_by_text.side_effect = get_by_text
        with patch.object(manifest, "_snapshot") as snapshot:
            with self.assertRaisesRegex(PermissionError, "APPLICATION_STEPS_INCOMPLETE"):
                extract_same_page_manifest(page, URL)
        snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
