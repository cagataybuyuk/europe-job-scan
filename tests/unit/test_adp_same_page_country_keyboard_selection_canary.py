from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_country_keyboard_selection_canary as canary
from ejs.services.adp_same_page_country_keyboard_selection_canary import (
    AdpSamePageCountryKeyboardSelectionRequest,
    run_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64
COUNTRY_FP = "c" * 64


def contact_fixture():
    return {
        "phone_pair_candidates": [
            {
                "country_ordinal": 0,
                "phone_ordinal": 0,
                "dom_distance": 4,
                "country_semantic_context": {"text": "Mobile Number*"},
                "phone_semantic_context": {"text": "Mobile Number*"},
            },
            {
                "country_ordinal": 1,
                "phone_ordinal": 1,
                "dom_distance": 4,
                "country_semantic_context": {"text": "Home Phone Number"},
                "phone_semantic_context": {"text": "Home Phone Number"},
            },
        ]
    }


class CountryKeyboardSelectionCanaryTests(unittest.TestCase):
    def request(self):
        return AdpSamePageCountryKeyboardSelectionRequest(
            application_url=URL,
            expected_manifest_fingerprint=MANIFEST_FP,
            expected_contact_contract_fingerprint=CONTACT_FP,
            expected_country_option_surface_fingerprint=COUNTRY_FP,
        )

    def test_keyboard_selection_is_country_only(self):
        page = MagicMock()
        country = MagicMock()
        country.count.return_value = 1
        country.is_visible.return_value = True
        country.is_enabled.return_value = True
        country.get_attribute.side_effect = lambda key: {
            "role": "combobox",
            "aria-autocomplete": "list",
        }.get(key)
        page.locator.return_value = country

        full_surface = {
            "visible_listbox_count": 1,
            "visible_option_count": 241,
            "options": [{"ordinal": 0, "label": "Turkey", "disabled": False}],
            "turkey_candidate_labels": ["Turkey"],
            "turkey_candidate_count": 1,
            "unique_visible_labels": True,
            "candidate_values_read": False,
        }
        filtered_surface = dict(full_surface)

        with patch.object(
            canary,
            "inspect_contact_address_on_verified_page",
            return_value=contact_fixture(),
        ), patch.object(
            canary,
            "contact_contract_fingerprint",
            return_value=CONTACT_FP,
        ), patch.object(
            canary,
            "_visible_option_surface",
            side_effect=[full_surface, filtered_surface],
        ), patch.object(
            canary,
            "option_surface_fingerprint",
            return_value=COUNTRY_FP,
        ), patch.object(
            canary,
            "_country_readback_evidence",
            side_effect=[
                {
                    "mode": "empty",
                    "nonempty": False,
                    "browser_valid": False,
                    "aria_expanded": "false",
                    "visible_listbox_count": 0,
                    "visible_option_count": 0,
                    "raw_value_exposed": False,
                },
                {
                    "mode": "label",
                    "nonempty": True,
                    "browser_valid": True,
                    "aria_expanded": "false",
                    "visible_listbox_count": 0,
                    "visible_option_count": 0,
                    "raw_value_exposed": False,
                },
            ],
        ):
            report = run_on_verified_page(page, self.request())

        country.click.assert_called_once()
        country.fill.assert_called_once_with("Turkey")
        self.assertEqual(country.press.call_count, 2)
        self.assertEqual(country.press.call_args_list[0].args[0], "ArrowDown")
        self.assertEqual(country.press.call_args_list[1].args[0], "Enter")
        self.assertEqual(report["selection_status"], "verified")
        self.assertEqual(report["text_write_attempts"], 1)
        self.assertEqual(report["keyboard_commit_attempts"], 1)
        self.assertEqual(report["country_selection_successes"], 1)
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_text_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)

    def test_already_committed_country_is_noop(self):
        page = MagicMock()
        country = MagicMock()
        country.count.return_value = 1
        country.is_visible.return_value = True
        country.is_enabled.return_value = True
        country.get_attribute.side_effect = lambda key: {
            "role": "combobox",
            "aria-autocomplete": "list",
        }.get(key)
        page.locator.return_value = country

        with patch.object(
            canary,
            "inspect_contact_address_on_verified_page",
            return_value=contact_fixture(),
        ), patch.object(
            canary,
            "contact_contract_fingerprint",
            return_value=CONTACT_FP,
        ), patch.object(
            canary,
            "_country_readback_evidence",
            return_value={
                "mode": "label",
                "nonempty": True,
                "browser_valid": True,
                "aria_expanded": "false",
                "visible_listbox_count": 0,
                "visible_option_count": 0,
                "raw_value_exposed": False,
            },
        ):
            report = run_on_verified_page(page, self.request())

        self.assertEqual(report["selection_status"], "already_committed")
        country.click.assert_not_called()
        country.fill.assert_not_called()
        country.press.assert_not_called()

    def test_contract_mismatch_blocks_before_country_mutation(self):
        page = MagicMock()
        with patch.object(
            canary,
            "inspect_contact_address_on_verified_page",
            return_value=contact_fixture(),
        ), patch.object(
            canary,
            "contact_contract_fingerprint",
            return_value="d" * 64,
        ):
            with self.assertRaisesRegex(PermissionError, "CONTACT_CONTRACT_FINGERPRINT_MISMATCH"):
                run_on_verified_page(page, self.request())
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
