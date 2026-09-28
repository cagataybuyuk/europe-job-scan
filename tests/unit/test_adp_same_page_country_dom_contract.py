from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_country_dom_contract as contract
from ejs.services.adp_same_page_country_dom_contract import inspect_on_verified_page

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


class CountryDomContractTests(unittest.TestCase):
    def test_contract_is_read_only_and_value_free(self):
        page = MagicMock()
        country = MagicMock()
        country.count.return_value = 1
        country.is_visible.return_value = True
        country.is_enabled.return_value = True
        country.get_attribute.side_effect = lambda key: {
            "role": "combobox",
            "aria-autocomplete": "list",
        }.get(key)
        option = MagicMock()
        option.is_visible.return_value = True
        option.inner_text.return_value = "Turkey"

        option_collection = MagicMock()
        option_collection.count.return_value = 1
        option_collection.nth.return_value = option

        def locate(selector):
            if selector == "#PersonalAddress_country":
                return country
            if selector == "[role='option']:visible":
                return option_collection
            return MagicMock()

        page.locator.side_effect = locate
        page.evaluate.return_value = {"present": True, "tag": "input", "id": "PersonalAddress_country", "role": "combobox", "type": "text", "readonly": False, "aria_activedescendant": "", "value_attribute_read": False, "property_value_read": False}

        surface = {
            "visible_listbox_count": 1,
            "visible_option_count": 241,
            "options": [{"ordinal": 221, "label": "Turkey", "disabled": False}],
            "turkey_candidate_labels": ["Turkey"],
            "turkey_candidate_count": 1,
            "unique_visible_labels": True,
            "candidate_values_read": False,
        }

        with patch.object(contract, "inspect_contact_address_on_verified_page", return_value=contact_fixture()),                 patch.object(contract, "contact_contract_fingerprint", return_value=CONTACT_FP),                 patch.object(contract, "_visible_option_surface", return_value=surface),                 patch.object(contract, "option_surface_fingerprint", return_value=COUNTRY_FP),                 patch.object(contract, "_element_metadata", side_effect=[
                    {"tag": "input", "id": "PersonalAddress_country", "value_attribute_read": False, "property_value_read": False},
                    {"tag": "li", "role": "option", "value_attribute_read": False, "property_value_read": False},
                ]),                 patch.object(contract, "_parent_chain", side_effect=[[], []]),                 patch.object(contract, "_nearby_controls", return_value=[]):
            report = inspect_on_verified_page(
                page,
                URL,
                MANIFEST_FP,
                CONTACT_FP,
                COUNTRY_FP,
            )

        country.click.assert_called_once()
        country.fill.assert_not_called()
        country.input_value.assert_not_called()
        option.click.assert_not_called()
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["country_selection_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["candidate_values_read"])
        self.assertFalse(report["raw_values_exposed"])

    def test_contact_fingerprint_mismatch_blocks_before_open(self):
        page = MagicMock()
        with patch.object(contract, "inspect_contact_address_on_verified_page", return_value=contact_fixture()),                 patch.object(contract, "contact_contract_fingerprint", return_value="d" * 64):
            with self.assertRaisesRegex(PermissionError, "CONTACT_FINGERPRINT_MISMATCH"):
                inspect_on_verified_page(page, URL, MANIFEST_FP, CONTACT_FP, COUNTRY_FP)
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
