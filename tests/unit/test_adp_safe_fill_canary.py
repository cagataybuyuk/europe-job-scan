import json
import tempfile
import unittest
from pathlib import Path

from ejs.services.adp_safe_fill_canary import (
    AdpSafeFillCanaryRequest,
    load_identity_profile,
    safe_fill_surface_fingerprint,
    validate_request,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


def snapshot(*, key_shift=0, phone_required=False, hidden_cookie=True):
    controls = [
        {"tag": "input", "type": "text", "id": "guestFirstName", "name": "", "label": "First Name", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False, "observation_key": f"document/input@{107+key_shift}"},
        {"tag": "input", "type": "text", "id": "guestLastName", "name": "", "label": "Last Name", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False, "observation_key": f"document/input@{112+key_shift}"},
        {"tag": "input", "type": "text", "id": "guestEmail", "name": "Email", "label": "Email", "required": True, "disabled": False, "visible": True, "accept": "", "multiple": False, "observation_key": f"document/input@{117+key_shift}"},
        {"tag": "select", "type": "select-one", "id": "", "name": "phoneCountry", "label": "Phone number country", "required": False, "disabled": False, "visible": True, "accept": "", "multiple": False, "observation_key": f"document/select@{127+key_shift}"},
        {"tag": "input", "type": "tel", "id": "login_view_phone", "name": "phone", "label": "Mobile Number", "required": phone_required, "disabled": False, "visible": True, "accept": "", "multiple": False, "observation_key": f"document/input@{379+key_shift}"},
    ]
    if hidden_cookie:
        controls.append({"tag": "input", "type": "checkbox", "id": "ot-group-id-C0003", "name": "ot-group-id-C0003", "label": "Functional", "required": False, "disabled": False, "visible": False, "accept": "", "multiple": False, "observation_key": "document/input@999"})
    return {
        "runtime_state": "inspected",
        "captcha_observed": False,
        "auth_observed": False,
        "form": {"controls": controls},
    }


class AdpSafeFillCanaryTests(unittest.TestCase):
    def test_valid_request(self):
        validate_request(AdpSafeFillCanaryRequest(
            application_url=URL,
            expected_navigation_surface_fingerprint=FP,
            entry_ordinal=0,
            expected_safe_fill_surface_fingerprint=FP,
            profile_manifest_path="profile.json",
        ))

    def test_rejects_invalid_surface_fingerprint(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_SAFE_FILL_SURFACE_FINGERPRINT"):
            validate_request(AdpSafeFillCanaryRequest(
                application_url=URL,
                expected_navigation_surface_fingerprint=FP,
                entry_ordinal=0,
                expected_safe_fill_surface_fingerprint="abc",
                profile_manifest_path="profile.json",
            ))

    def test_surface_ignores_observation_key_and_hidden_cookie_drift(self):
        self.assertEqual(
            safe_fill_surface_fingerprint(snapshot(key_shift=0, hidden_cookie=True)),
            safe_fill_surface_fingerprint(snapshot(key_shift=25, hidden_cookie=False)),
        )

    def test_surface_changes_when_visible_contract_changes(self):
        self.assertNotEqual(
            safe_fill_surface_fingerprint(snapshot(phone_required=False)),
            safe_fill_surface_fingerprint(snapshot(phone_required=True)),
        )

    def test_known_live_surface_fingerprint(self):
        self.assertEqual(
            safe_fill_surface_fingerprint(snapshot()),
            "d8b72afea27912bd5e280d8a819ec836e44c2982faa3a3179bbb06c7aa58aa7f",
        )

    def test_load_identity_profile_extracts_only_three_auto_safe_values(self):
        payload = {
            "candidate_profile_version": "profile-v1",
            "field_plan": [
                {"canonical_field": "candidate.first_name", "value": "Ada"},
                {"canonical_field": "candidate.last_name", "value": "Lovelace"},
                {"canonical_field": "candidate.email", "value": "ada@example.test"},
                {"canonical_field": "candidate.phone", "value": "+1 555 000 0000"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            profile, version = load_identity_profile(str(path))
        self.assertEqual(version, "profile-v1")
        self.assertEqual(set(profile), {"candidate.first_name", "candidate.last_name", "candidate.email"})
        self.assertEqual(profile["candidate.email"], "ada@example.test")

    def test_profile_requires_unique_email(self):
        payload = {
            "field_plan": [
                {"canonical_field": "candidate.first_name", "value": "Ada"},
                {"canonical_field": "candidate.last_name", "value": "Lovelace"},
                {"canonical_field": "candidate.email", "value": "ada@example.test"},
                {"canonical_field": "candidate.email", "value": "other@example.test"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "REQUIRES_EXACT_CANDIDATE_EMAIL"):
                load_identity_profile(str(path))

    def test_profile_rejects_invalid_email(self):
        payload = {
            "field_plan": [
                {"canonical_field": "candidate.first_name", "value": "Ada"},
                {"canonical_field": "candidate.last_name", "value": "Lovelace"},
                {"canonical_field": "candidate.email", "value": "not-an-email"},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "EMAIL_INVALID"):
                load_identity_profile(str(path))


if __name__ == "__main__":
    unittest.main()
