from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_contact_address_contract as contract
from ejs.services.adp_same_page_contact_address_contract import (
    ADDRESS_CONTROLS,
    contract_fingerprint,
    inspect_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
FP = "a" * 64


def manifest_fixture():
    controls = []
    for canonical, element_id, label, required in ADDRESS_CONTROLS:
        controls.append({
            "id": element_id,
            "label": label,
            "required": required,
            "disabled": False,
            "type": "text",
            "tag": "input",
        })
    return {"controls": controls}


def element(*, element_id="", name="", control_type="text", role="", y=0.0):
    loc = MagicMock()
    attrs = {
        "id": element_id,
        "name": name,
        "type": control_type,
        "role": role,
        "autocomplete": "",
        "inputmode": "",
        "placeholder": "",
        "aria-label": "",
        "aria-labelledby": "",
        "aria-describedby": "",
        "aria-controls": "",
        "aria-autocomplete": "",
        "aria-expanded": "",
        "required": None,
        "aria-required": "",
    }
    loc.get_attribute.side_effect = lambda key: attrs.get(key)
    def evaluate(script):
        if "cloneNode" in str(script):
            return {"depth": 2, "tag": "div", "text": "Mobile Number required"}
        return "select" if control_type == "select-one" else "input"
    loc.evaluate.side_effect = evaluate
    loc.is_disabled.return_value = False
    loc.is_visible.return_value = True
    loc.is_enabled.return_value = True
    loc.bounding_box.return_value = {"x": 10.0, "y": y, "width": 200.0, "height": 30.0}
    loc.count.return_value = 1
    return loc


class AdpSamePageContactAddressContractTests(unittest.TestCase):
    def page_fixture(self):
        page = MagicMock()
        address = {
            f"#{element_id}": element(
                element_id=element_id,
                role="combobox" if canonical in {"country", "state"} else "",
                y=100.0 + index * 40,
            )
            for index, (canonical, element_id, _label, _required)
            in enumerate(ADDRESS_CONTROLS)
        }
        countries = [
            element(name="phoneCountry", control_type="select-one", y=500.0),
            element(name="phoneCountry", control_type="select-one", y=700.0),
        ]
        phones = [
            element(element_id="phone-a", name="phone", control_type="tel", y=540.0),
            element(element_id="phone-b", name="phone", control_type="tel", y=740.0),
        ]

        country_collection = MagicMock()
        country_collection.count.return_value = 2
        country_collection.nth.side_effect = lambda index: countries[index]
        phone_collection = MagicMock()
        phone_collection.count.return_value = 2
        phone_collection.nth.side_effect = lambda index: phones[index]

        empty_collection = MagicMock()
        empty_collection.count.return_value = 0

        def locate(selector):
            if selector == "select[name='phoneCountry']":
                return country_collection
            if selector == "input[name='phone']":
                return phone_collection
            if selector in {"[role='listbox']:visible", "[role='option']:visible"}:
                return empty_collection
            return address[selector]

        page.locator.side_effect = locate
        page.evaluate.return_value = [
            {"countryOrdinal": 0, "phoneOrdinal": 0, "distance": 3, "lcaDepthFromSelect": 1, "lcaDepthFromPhone": 2},
            {"countryOrdinal": 0, "phoneOrdinal": 1, "distance": 8, "lcaDepthFromSelect": 4, "lcaDepthFromPhone": 4},
            {"countryOrdinal": 1, "phoneOrdinal": 0, "distance": 8, "lcaDepthFromSelect": 4, "lcaDepthFromPhone": 4},
            {"countryOrdinal": 1, "phoneOrdinal": 1, "distance": 3, "lcaDepthFromSelect": 1, "lcaDepthFromPhone": 2},
        ]
        return page, address, countries, phones

    def test_contract_is_read_only_and_value_free(self):
        page, address, countries, phones = self.page_fixture()
        with patch.object(contract, "extract_same_page_manifest", return_value=manifest_fixture()), \
                patch.object(contract, "manifest_surface_fingerprint", return_value=FP):
            report = inspect_on_verified_page(page, URL, FP)

        self.assertEqual(report["address_control_count"], 7)
        self.assertEqual(report["phone_country_control_count"], 2)
        self.assertEqual(report["phone_input_control_count"], 2)
        self.assertEqual(
            [(row["country_ordinal"], row["phone_ordinal"], row["dom_distance"])
             for row in report["phone_pair_candidates"]],
            [(0, 0, 3), (1, 1, 3)],
        )
        self.assertEqual(report["address_country_combobox_hints"]["visible_listbox_count"], 0)
        self.assertEqual(report["form_value_write_attempts"], 0)
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["input_values_read"])
        self.assertFalse(report["raw_values_exposed"])
        for loc in list(address.values()) + countries + phones:
            loc.input_value.assert_not_called()
            loc.fill.assert_not_called()
            loc.click.assert_not_called()
            loc.select_option.assert_not_called()

    def test_semantic_context_redacts_email_and_long_digits(self):
        self.assertEqual(
            contract._sanitize_semantic_text(
                "Mobile candidate@example.com 5551112233 secondary"
            ),
            "Mobile [email] [digits] secondary",
        )

    def test_phone_pairing_requires_unique_bijective_nearest_pairs(self):
        matrix = [
            {"countryOrdinal": 0, "phoneOrdinal": 0, "distance": 4},
            {"countryOrdinal": 0, "phoneOrdinal": 1, "distance": 14},
            {"countryOrdinal": 1, "phoneOrdinal": 0, "distance": 14},
            {"countryOrdinal": 1, "phoneOrdinal": 1, "distance": 4},
        ]
        self.assertEqual(
            contract._pairing_from_distance_matrix(matrix),
            [
                {"country_ordinal": 0, "phone_ordinal": 0, "dom_distance": 4},
                {"country_ordinal": 1, "phone_ordinal": 1, "dom_distance": 4},
            ],
        )

    def test_manifest_mismatch_blocks_before_runtime_locator_access(self):
        page = MagicMock()
        with patch.object(contract, "extract_same_page_manifest", return_value=manifest_fixture()), \
                patch.object(contract, "manifest_surface_fingerprint", return_value="b" * 64):
            with self.assertRaisesRegex(PermissionError, "MANIFEST_FINGERPRINT_MISMATCH"):
                inspect_on_verified_page(page, URL, FP)
        page.locator.assert_not_called()

    def test_contract_fingerprint_ignores_geometry_but_detects_structure(self):
        report = {
            "manifest_surface_fingerprint": FP,
            "address_controls": [{
                "kind": "address.city",
                "ordinal": 0,
                "tag": "input",
                "id": "PersonalAddress_city",
                "name": "",
                "type": "text",
                "role": "",
                "autocomplete": "",
                "inputmode": "",
                "placeholder": "",
                "aria_label": "",
                "aria_labelledby_present": False,
                "aria_describedby_present": False,
                "aria_controls_present": False,
                "aria_autocomplete": "",
                "aria_expanded": "",
                "required": True,
                "aria_required": "",
                "disabled": False,
                "visible": True,
                "enabled": True,
                "geometry": {"present": True, "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},
                "raw_value_read": False,
                "canonical_field": "candidate.address.city",
                "manifest_label": "City*",
                "manifest_required": True,
            }],
            "phone_country_controls": [],
            "phone_input_controls": [],
            "phone_dom_distance_matrix": [],
        }
        moved = {**report, "address_controls": [dict(report["address_controls"][0])]}
        moved["address_controls"][0]["geometry"] = {"present": True, "x": 99.0, "y": 99.0, "width": 3.0, "height": 4.0}
        drift = {**report, "address_controls": [dict(report["address_controls"][0])]}
        drift["address_controls"][0]["required"] = False

        self.assertEqual(contract_fingerprint(report), contract_fingerprint(moved))
        self.assertNotEqual(contract_fingerprint(report), contract_fingerprint(drift))


if __name__ == "__main__":
    unittest.main()
