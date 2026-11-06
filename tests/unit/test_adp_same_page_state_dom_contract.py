from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_state_dom_contract as contract
from ejs.services.adp_same_page_state_dom_contract import inspect_on_verified_page

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64


class StateDomContractTests(unittest.TestCase):
    def test_contract_is_read_only_and_value_free(self):
        page = MagicMock()
        wrapper = MagicMock()
        wrapper.count.return_value = 1
        wrapper.is_visible.return_value = True
        page.locator.return_value = wrapper

        with patch.object(
            contract,
            "inspect_contact_address_on_verified_page",
            return_value={"controls": []},
        ), patch.object(
            contract,
            "contact_contract_fingerprint",
            return_value=CONTACT_FP,
        ), patch.object(
            contract,
            "_metadata",
            return_value={
                "tag": "div",
                "id": "PersonalAddress_state",
                "value_attribute_read": False,
                "property_value_read": False,
            },
        ), patch.object(
            contract,
            "_descendants",
            return_value=[
                {
                    "ordinal": 0,
                    "tag": "input",
                    "id": "",
                    "name": "",
                    "type": "text",
                    "role": "combobox",
                    "readonly": False,
                    "disabled_attribute": False,
                    "hidden_attribute": False,
                    "tabindex": "0",
                    "class_name": "MDFSelectBox__input",
                    "aria_expanded": "false",
                    "aria_controls": "",
                    "aria_activedescendant": "",
                    "aria_autocomplete": "list",
                    "aria_haspopup": "true",
                    "aria_selected": "",
                    "value_attribute_read": False,
                    "property_value_read": False,
                }
            ],
        ), patch.object(
            contract,
            "_sibling_metadata",
            return_value=[],
        ), patch.object(
            contract,
            "_ancestor_neighborhood",
            return_value=[
                {
                    "ancestor_depth": 2,
                    "ordinal": 0,
                    "tag": "input",
                    "id": "state-real-input",
                    "name": "",
                    "type": "text",
                    "role": "combobox",
                    "readonly": False,
                    "disabled_attribute": False,
                    "hidden_attribute": False,
                    "tabindex": "0",
                    "class_name": "MDFSelectBox__input",
                    "aria_expanded": "false",
                    "aria_controls": "",
                    "aria_activedescendant": "",
                    "aria_autocomplete": "list",
                    "aria_haspopup": "true",
                    "value_attribute_read": False,
                    "property_value_read": False,
                }
            ],
        ), patch.object(
            contract,
            "_parent_chain",
            return_value=[],
        ):
            report = inspect_on_verified_page(
                page,
                URL,
                MANIFEST_FP,
                CONTACT_FP,
            )

        wrapper.input_value.assert_not_called()
        wrapper.fill.assert_not_called()
        wrapper.click.assert_not_called()
        self.assertEqual(report["interactive_descendant_count"], 1)
        self.assertEqual(report["interactive_neighborhood_count"], 1)
        self.assertEqual(report["interactive_candidate_count"], 1)
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["state_selection_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["candidate_values_read"])
        self.assertFalse(report["raw_values_exposed"])

    def test_missing_descendant_no_longer_blocks_neighborhood_discovery(self):
        page = MagicMock()
        wrapper = MagicMock()
        wrapper.count.return_value = 1
        wrapper.is_visible.return_value = True
        page.locator.return_value = wrapper

        with patch.object(
            contract,
            "inspect_contact_address_on_verified_page",
            return_value={"controls": []},
        ), patch.object(
            contract,
            "contact_contract_fingerprint",
            return_value=CONTACT_FP,
        ), patch.object(
            contract,
            "_metadata",
            return_value={"tag": "div", "id": "PersonalAddress_state"},
        ), patch.object(
            contract,
            "_descendants",
            return_value=[],
        ), patch.object(
            contract,
            "_sibling_metadata",
            return_value=[
                {
                    "relation": "next",
                    "tag": "div",
                    "id": "",
                    "interactive_descendant_count": 1,
                }
            ],
        ), patch.object(
            contract,
            "_ancestor_neighborhood",
            return_value=[
                {
                    "ancestor_depth": 1,
                    "ordinal": 0,
                    "tag": "input",
                    "id": "state-sibling-input",
                    "role": "combobox",
                }
            ],
        ), patch.object(
            contract,
            "_parent_chain",
            return_value=[],
        ):
            report = inspect_on_verified_page(
                page,
                URL,
                MANIFEST_FP,
                CONTACT_FP,
            )

        self.assertEqual(report["interactive_descendant_count"], 0)
        self.assertEqual(report["interactive_neighborhood_count"], 1)
        self.assertEqual(report["interactive_candidate_count"], 1)
        self.assertEqual(report["wrapper_siblings"][0]["relation"], "next")
        wrapper.click.assert_not_called()
        wrapper.input_value.assert_not_called()

    def test_contact_fingerprint_mismatch_blocks_before_wrapper_access(self):
        page = MagicMock()
        with patch.object(
            contract,
            "inspect_contact_address_on_verified_page",
            return_value={"controls": []},
        ), patch.object(
            contract,
            "contact_contract_fingerprint",
            return_value="c" * 64,
        ):
            with self.assertRaisesRegex(PermissionError, "CONTACT_FINGERPRINT_MISMATCH"):
                inspect_on_verified_page(page, URL, MANIFEST_FP, CONTACT_FP)
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
