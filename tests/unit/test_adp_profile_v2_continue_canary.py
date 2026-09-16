from __future__ import annotations

import unittest

from ejs.services.adp_profile_v2_continue_canary import (
    AdpProfileV2ContinueCanaryRequest,
    phone_contract_surface_descriptor,
    phone_contract_surface_fingerprint,
    validate_request,
)

FP = "a" * 64


class AdpProfileV2ContinueCanaryTests(unittest.TestCase):
    def _request(self, **overrides):
        data = dict(
            application_url="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=x&jobId=1",
            expected_navigation_surface_fingerprint=FP,
            expected_preference_surface_fingerprint=FP,
            entry_ordinal=0,
            expected_safe_fill_surface_fingerprint=FP,
            expected_phone_contract_fingerprint=FP,
            expected_post_fill_action_surface_fingerprint=FP,
            profile_json_path="profile.json",
        )
        data.update(overrides)
        return AdpProfileV2ContinueCanaryRequest(**data)

    def test_request_accepts_reviewed_shape(self):
        validate_request(self._request())

    def test_invalid_phone_contract_fingerprint_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "INVALID_EXPECTED_PHONE_CONTRACT_FINGERPRINT"):
            validate_request(self._request(expected_phone_contract_fingerprint="bad"))

    def test_descriptor_ignores_selected_default_state(self):
        base = {
            "country_control": {
                "tag": "select",
                "name": "phoneCountry",
                "required": False,
                "aria_required": "",
                "option_count": 2,
                "selected_value": "US",
                "selected_label": "United States",
                "options": [
                    {"label": "United States", "value": "US", "selected": True, "disabled": False},
                    {"label": "Turkey", "value": "TR", "selected": False, "disabled": False},
                ],
            },
            "phone_control": {
                "tag": "input",
                "id": "login_view_phone",
                "name": "phone",
                "type": "tel",
                "required": False,
                "aria_required": "",
                "pattern": "",
                "inputmode": "",
                "minlength": "",
                "maxlength": "",
                "placeholder": "Phone Number",
                "autocomplete": "tel",
                "aria_describedby": "mobileNumberInstruction mobileNumberError",
            },
            "instruction_text": "Country code was added to the field.",
            "runtime_required_observed_in_prior_live_diagnostic": True,
        }
        changed = {
            **base,
            "country_control": {
                **base["country_control"],
                "selected_value": "TR",
                "selected_label": "Turkey",
                "options": [
                    {"label": "United States", "value": "US", "selected": False, "disabled": False},
                    {"label": "Turkey", "value": "TR", "selected": True, "disabled": False},
                ],
            },
        }
        self.assertEqual(
            phone_contract_surface_fingerprint(base),
            phone_contract_surface_fingerprint(changed),
        )

    def test_phone_contract_descriptor_sorts_options_and_preserves_structure(self):
        contract = {
            "country_control": {
                "tag": "select", "name": "phoneCountry", "required": False,
                "aria_required": "", "option_count": 2,
                "options": [
                    {"label": "Turkey", "value": "TR", "selected": False, "disabled": False},
                    {"label": "United States", "value": "US", "selected": True, "disabled": False},
                ],
            },
            "phone_control": {
                "tag": "input", "id": "login_view_phone", "name": "phone", "type": "tel",
                "required": False, "aria_required": "", "pattern": "", "inputmode": "",
                "minlength": "", "maxlength": "", "placeholder": "Phone Number",
                "autocomplete": "tel", "aria_describedby": "mobileNumberInstruction mobileNumberError",
            },
            "instruction_text": "Country code was added to the field.",
            "runtime_required_observed_in_prior_live_diagnostic": True,
        }
        descriptor = phone_contract_surface_descriptor(contract)
        self.assertEqual([x["value"] for x in descriptor["country_control"]["options"]], ["TR", "US"])
        self.assertNotIn("selected_value", descriptor["country_control"])
        self.assertTrue(descriptor["runtime_required_observed_in_prior_live_diagnostic"])


if __name__ == "__main__":
    unittest.main()
