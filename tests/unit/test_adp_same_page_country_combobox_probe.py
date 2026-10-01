from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_country_combobox_probe as probe
from ejs.services.adp_same_page_country_combobox_probe import (
    inspect_country_combobox_on_verified_page,
    reviewed_mobile_pair,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64


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


class AdpSamePageCountryComboboxProbeTests(unittest.TestCase):
    def test_reviewed_mobile_pair_resolves_required_pair(self):
        pair = reviewed_mobile_pair(contact_fixture())
        self.assertEqual(pair["country_ordinal"], 0)
        self.assertEqual(pair["phone_ordinal"], 0)
        self.assertEqual(pair["semantic_role"], "mobile_required")
        self.assertEqual(pair["semantic_label"], "Mobile Number*")
        self.assertFalse(pair["raw_values_exposed"])

    def test_reviewed_mobile_pair_blocks_semantic_drift(self):
        contract = contact_fixture()
        contract["phone_pair_candidates"][0]["phone_semantic_context"]["text"] = "Other"
        with self.assertRaisesRegex(PermissionError, "SEMANTIC_MISMATCH"):
            reviewed_mobile_pair(contract)

    def test_country_probe_clicks_once_and_never_selects_or_writes(self):
        page = MagicMock()
        country = MagicMock()
        country.count.return_value = 1
        country.is_visible.return_value = True
        country.is_enabled.return_value = True
        country.get_attribute.side_effect = lambda name: {
            "role": "combobox",
            "aria-autocomplete": "list",
        }.get(name)
        page.locator.side_effect = lambda selector: country if selector == "#PersonalAddress_country" else MagicMock()

        surface = {
            "visible_listbox_count": 1,
            "visible_option_count": 3,
            "options": [
                {"ordinal": 0, "label": "Germany", "disabled": False},
                {"ordinal": 1, "label": "Türkiye", "disabled": False},
                {"ordinal": 2, "label": "United States", "disabled": False},
            ],
            "turkey_candidate_labels": ["Türkiye"],
            "turkey_candidate_count": 1,
            "unique_visible_labels": True,
            "candidate_values_read": False,
        }

        with patch.object(
            probe,
            "inspect_contact_address_on_verified_page",
            return_value=contact_fixture(),
        ), patch.object(
            probe,
            "contact_contract_fingerprint",
            return_value=CONTACT_FP,
        ), patch.object(
            probe,
            "_visible_option_surface",
            return_value=surface,
        ):
            report = inspect_country_combobox_on_verified_page(
                page,
                URL,
                MANIFEST_FP,
                CONTACT_FP,
            )

        country.click.assert_called_once()
        country.fill.assert_not_called()
        country.select_option.assert_not_called()
        country.input_value.assert_not_called()
        self.assertEqual(report["combobox_open_click_attempts"], 1)
        self.assertEqual(report["combobox_open_click_successes"], 1)
        self.assertEqual(report["country_selection_attempts"], 0)
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertEqual(report["option_surface"]["turkey_candidate_count"], 1)
        self.assertFalse(report["country_selection_allowed"])
        self.assertFalse(report["next_allowed"])
        self.assertFalse(report["raw_values_exposed"])

    def test_contact_fingerprint_mismatch_blocks_before_country_click(self):
        page = MagicMock()
        with patch.object(
            probe,
            "inspect_contact_address_on_verified_page",
            return_value=contact_fixture(),
        ), patch.object(
            probe,
            "contact_contract_fingerprint",
            return_value="c" * 64,
        ):
            with self.assertRaisesRegex(PermissionError, "CONTACT_CONTRACT_FINGERPRINT_MISMATCH"):
                inspect_country_combobox_on_verified_page(
                    page,
                    URL,
                    MANIFEST_FP,
                    CONTACT_FP,
                )
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
