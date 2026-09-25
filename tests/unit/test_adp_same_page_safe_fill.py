from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_safe_fill as safe_fill
from ejs.services.adp_same_page_safe_fill import (
    AdpSamePageSafeFillRequest,
    EMAIL_ID,
    FIRST_NAME_ID,
    LAST_NAME_ID,
    run_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
    "?cid=test&ccId=19000101_000001&jobId=960970&source=LR&lang=en_US"
)
FP = "a" * 64


def manifest_fixture():
    return {
        "controls": [
            {
                "id": FIRST_NAME_ID,
                "type": "text",
                "label": "First Name*",
                "required": True,
                "disabled": False,
            },
            {
                "id": LAST_NAME_ID,
                "type": "text",
                "label": "Last Name*",
                "required": True,
                "disabled": False,
            },
            {
                "id": EMAIL_ID,
                "type": "text",
                "label": "Email*",
                "required": True,
                "disabled": True,
            },
            {"id": "phone-a", "name": "phone", "type": "tel", "disabled": False},
            {"id": "", "name": "phoneCountry", "type": "select-one", "disabled": False},
        ],
        "steps": {
            "personal_information": True,
            "resume": True,
            "questions": True,
            "review_application": True,
            "self_attest_submit": True,
        },
        "actions": [{"label": "Next"}],
    }


class AdpSamePageSafeFillTests(unittest.TestCase):
    def profile_path(self, tmp, **overrides):
        payload = {
            "profile_version": "test-profile-v1",
            "first_name": "Cagatay",
            "last_name": "Buyuk",
            "email": "candidate@example.com",
        }
        payload.update(overrides)
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def locator(self, value, *, enabled=True):
        loc = MagicMock()
        loc.count.return_value = 1
        loc.is_visible.return_value = True
        loc.is_enabled.return_value = enabled
        loc.input_value.return_value = value
        loc.evaluate.return_value = True
        return loc

    def test_safe_fill_writes_only_blank_names_and_keeps_email_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile_path(tmp)
            page = MagicMock()
            first = self.locator("")
            first.input_value.side_effect = ["", "Cagatay"]
            last = self.locator("Buyuk")
            email = self.locator("candidate@example.com", enabled=False)

            def by_selector(selector):
                return {
                    f"#{FIRST_NAME_ID}": first,
                    f"#{LAST_NAME_ID}": last,
                    f"#{EMAIL_ID}": email,
                }[selector]

            page.locator.side_effect = by_selector
            with patch.object(safe_fill, "extract_same_page_manifest", side_effect=[manifest_fixture(), manifest_fixture()]),                     patch.object(safe_fill, "manifest_surface_fingerprint", return_value=FP):
                report = run_on_verified_page(
                    page,
                    AdpSamePageSafeFillRequest(
                        application_url=URL,
                        expected_manifest_fingerprint=FP,
                        profile_json_path=str(profile),
                    ),
                )

        first.fill.assert_called_once_with("Cagatay")
        last.fill.assert_not_called()
        email.fill.assert_not_called()
        self.assertEqual(report["form_value_write_attempts"], 1)
        self.assertEqual(report["form_value_write_successes"], 1)
        self.assertEqual(report["navigation_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertFalse(report["final_submit_allowed"])
        self.assertNotIn("candidate@example.com", repr(report))

    def test_nonempty_name_conflict_blocks_before_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile_path(tmp, first_name="Expected")
            page = MagicMock()
            first = self.locator("Existing")
            last = self.locator("Buyuk")
            email = self.locator("candidate@example.com", enabled=False)
            page.locator.side_effect = lambda selector: {
                f"#{FIRST_NAME_ID}": first,
                f"#{LAST_NAME_ID}": last,
                f"#{EMAIL_ID}": email,
            }[selector]
            with patch.object(safe_fill, "extract_same_page_manifest", return_value=manifest_fixture()),                     patch.object(safe_fill, "manifest_surface_fingerprint", return_value=FP):
                with self.assertRaisesRegex(PermissionError, "PROFILE_CONFLICT:candidate.first_name"):
                    run_on_verified_page(
                        page,
                        AdpSamePageSafeFillRequest(
                            application_url=URL,
                            expected_manifest_fingerprint=FP,
                            profile_json_path=str(profile),
                        ),
                    )
        first.fill.assert_not_called()
        last.fill.assert_not_called()

    def test_disabled_email_mismatch_blocks_before_name_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile_path(tmp)
            page = MagicMock()
            first = self.locator("")
            last = self.locator("")
            email = self.locator("other@example.com", enabled=False)
            page.locator.side_effect = lambda selector: {
                f"#{FIRST_NAME_ID}": first,
                f"#{LAST_NAME_ID}": last,
                f"#{EMAIL_ID}": email,
            }[selector]
            with patch.object(safe_fill, "extract_same_page_manifest", return_value=manifest_fixture()),                     patch.object(safe_fill, "manifest_surface_fingerprint", return_value=FP):
                with self.assertRaisesRegex(PermissionError, "PROFILE_CONFLICT:candidate.email"):
                    run_on_verified_page(
                        page,
                        AdpSamePageSafeFillRequest(
                            application_url=URL,
                            expected_manifest_fingerprint=FP,
                            profile_json_path=str(profile),
                        ),
                    )
        first.fill.assert_not_called()
        last.fill.assert_not_called()

    def test_manifest_fingerprint_mismatch_blocks_before_locator_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = self.profile_path(tmp)
            page = MagicMock()
            with patch.object(safe_fill, "extract_same_page_manifest", return_value=manifest_fixture()),                     patch.object(safe_fill, "manifest_surface_fingerprint", return_value="b" * 64):
                with self.assertRaisesRegex(PermissionError, "MANIFEST_FINGERPRINT_MISMATCH"):
                    run_on_verified_page(
                        page,
                        AdpSamePageSafeFillRequest(
                            application_url=URL,
                            expected_manifest_fingerprint=FP,
                            profile_json_path=str(profile),
                        ),
                    )
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
