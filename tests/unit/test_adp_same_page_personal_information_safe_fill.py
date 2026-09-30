from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_personal_information_safe_fill as safe_fill
from ejs.services.adp_same_page_personal_information_safe_fill import (
    AdpSamePagePersonalInformationSafeFillRequest,
    _country_readback_evidence,
    _fill_blank_or_verify,
    run_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64
COUNTRY_FP = "c" * 64
STATE_FP = "d" * 64


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


def input_locator(before: str, after: str | None = None):
    loc = MagicMock()
    loc.count.return_value = 1
    loc.is_visible.return_value = True
    loc.is_enabled.return_value = True
    values = [before] if after is None else [before, after]
    loc.input_value.side_effect = values
    loc.evaluate.return_value = True
    return loc


class AdpSamePagePersonalInformationSafeFillTests(unittest.TestCase):
    def profile_path(self, tmp: str) -> str:
        payload = {
            "profile_version": "candidate-profile-v3",
            "first_name": "Cagatay",
            "last_name": "Buyuk",
            "email": "candidate@example.com",
            "phone_country_iso2": "TR",
            "phone_national_number": "5551112233",
            "adp_ascii_name_policy_approved": True,
            "address_country_iso2": "TR",
            "address_line1": "Example Mah. Example Cad. 1",
            "address_line2": "",
            "address_line3": "",
            "city": "Istanbul",
            "state_or_territory": "Istanbul",
            "postal_code": "34700",
        }
        path = Path(tmp) / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def test_blank_text_address_write_is_exact_and_conflict_blocks(self):
        counters = {
            "form_value_write_attempts": 0,
            "form_value_write_successes": 0,
            "address_write_attempts": 0,
            "address_write_successes": 0,
        }
        blank = input_locator("", "Istanbul")
        result = _fill_blank_or_verify(
            blank,
            "Istanbul",
            "candidate.address.city",
            counters,
        )
        blank.fill.assert_called_once_with("Istanbul")
        self.assertTrue(result["executed"])
        self.assertEqual(counters["form_value_write_attempts"], 1)
        self.assertEqual(counters["address_write_attempts"], 1)

        conflict = input_locator("Ankara")
        with self.assertRaisesRegex(PermissionError, "PROFILE_CONFLICT"):
            _fill_blank_or_verify(
                conflict,
                "Istanbul",
                "candidate.address.city",
                counters,
            )
        conflict.fill.assert_not_called()

    def test_full_executor_selects_reviewed_country_and_required_address_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = self.profile_path(tmp)
            page = MagicMock()

            country = MagicMock()
            country.count.return_value = 1
            country.is_visible.return_value = True
            country.is_enabled.return_value = True
            country.get_attribute.side_effect = lambda key: {
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
            }.get(key)
            country.input_value.side_effect = ["", ""]
            country.evaluate.return_value = True

            option = MagicMock()
            option.is_disabled.return_value = False

            phone_country = MagicMock()
            phone_country.is_visible.return_value = True
            phone_country.is_enabled.return_value = True
            phone_country.input_value.side_effect = ["TR", "TR"]
            phone_country.locator.return_value.count.return_value = 1
            phone_country.locator.return_value.is_disabled.return_value = False

            home_country = MagicMock()
            countries = MagicMock()
            countries.count.return_value = 2
            countries.nth.side_effect = [phone_country, home_country]

            phone = MagicMock()
            phone.is_visible.return_value = True
            phone.is_enabled.return_value = True
            phone.input_value.side_effect = ["5551112233", "5551112233"]
            home_phone = MagicMock()
            phones = MagicMock()
            phones.count.return_value = 2
            phones.nth.side_effect = [phone, home_phone]

            address_values = {
                "#PersonalAddress_address_line1": ("", "Example Mah. Example Cad. 1"),
                "#PersonalAddress_address_line2": ("", ""),
                "#PersonalAddress_address_line3": ("", ""),
                "#PersonalAddress_city": ("", "Istanbul"),
                "#PersonalAddress_postalCode": ("", "34700"),
            }
            address_locators = {
                selector: input_locator(before, after)
                for selector, (before, after) in address_values.items()
            }

            state = MagicMock()
            state.count.return_value = 1
            state.is_visible.return_value = True
            state.is_enabled.return_value = True
            state.get_attribute.side_effect = lambda key: {
                "role": "combobox",
                "aria-controls": "PersonalAddress_state__listbox",
            }.get(key)
            state.inner_text.side_effect = ["", "Istanbul"]

            state_option = MagicMock()
            state_option.is_visible.return_value = True
            state_option.is_disabled.return_value = False
            state_option.inner_text.return_value = "Istanbul"
            state_options = MagicMock()
            state_options.count.return_value = 1
            state_options.nth.return_value = state_option
            state_listbox = MagicMock()
            state_listbox.count.return_value = 1
            state_listbox.is_visible.return_value = True
            state_listbox.locator.return_value = state_options

            hidden_collection = MagicMock()
            hidden_collection.count.return_value = 0

            def locate(selector):
                if selector == "#PersonalAddress_country":
                    return country
                if selector == "select[name='phoneCountry']":
                    return countries
                if selector == "input[name='phone']":
                    return phones
                if selector == "#PersonalAddress_state":
                    return state
                if selector == "#PersonalAddress_state__listbox":
                    return state_listbox
                if selector in {"[role='listbox']:visible", "[role='option']:visible"}:
                    return hidden_collection
                return address_locators[selector]

            page.locator.side_effect = locate

            identity = {
                "safe_fill_status": "verified",
                "form_value_write_attempts": 0,
                "form_value_write_successes": 0,
                "email_readback_only": {"readback_match": True},
            }
            surface = {
                "visible_listbox_count": 1,
                "visible_option_count": 241,
                "options": [
                    {"ordinal": 221, "label": "Turkey", "disabled": False},
                ],
                "turkey_candidate_labels": ["Turkey"],
                "turkey_candidate_count": 1,
                "unique_visible_labels": True,
                "candidate_values_read": False,
            }

            with patch.object(
                safe_fill,
                "run_identity_safe_fill",
                return_value=identity,
            ), patch.object(
                safe_fill,
                "inspect_contact_address_on_verified_page",
                side_effect=[contact_fixture(), contact_fixture()],
            ), patch.object(
                safe_fill,
                "contact_contract_fingerprint",
                return_value=CONTACT_FP,
            ), patch.object(
                safe_fill,
                "_selected_country_label_evidence",
                return_value={
                    "present": True,
                    "reviewed_label_match": True,
                    "label_hash": "hash",
                    "raw_value_exposed": False,
                },
            ), patch.object(
                safe_fill,
                "_visible_option_surface",
                return_value=surface,
            ), patch.object(
                safe_fill,
                "option_surface_fingerprint",
                return_value=COUNTRY_FP,
            ), patch.object(
                safe_fill,
                "_unique_visible_option",
                return_value=option,
            ), patch.object(
                safe_fill,
                "_snapshot_state",
                return_value={
                    "metadata": {
                        "role": "combobox",
                        "aria_controls": "PersonalAddress_state__listbox",
                    }
                },
            ), patch.object(
                safe_fill,
                "_state_option_surface",
                return_value={
                    "surface_fingerprint": STATE_FP,
                    "unique_nonempty_labels": True,
                    "options": [
                        {"label": "Istanbul", "disabled": False},
                    ],
                },
            ):
                report = run_on_verified_page(
                    page,
                    AdpSamePagePersonalInformationSafeFillRequest(
                        application_url=URL,
                        expected_manifest_fingerprint=MANIFEST_FP,
                        expected_contact_contract_fingerprint=CONTACT_FP,
                        expected_country_option_surface_fingerprint=COUNTRY_FP,
                        expected_state_option_surface_fingerprint=STATE_FP,
                        profile_json_path=profile_path,
                        allow_reviewed_turkish_ascii_name_overwrite=True,
                    ),
                )

        country.click.assert_called_once()
        option.click.assert_called_once()
        phone.fill.assert_not_called()
        phone_country.select_option.assert_not_called()
        home_phone.fill.assert_not_called()
        home_country.select_option.assert_not_called()

        self.assertEqual(report["safe_fill_status"], "verified")
        self.assertEqual(report["country_selection_attempts"], 1)
        self.assertEqual(report["country_selection_successes"], 1)
        self.assertEqual(report["address_country_result"]["readback_mode"], "selected_label")
        self.assertEqual(report["address_write_attempts"], 5)
        self.assertEqual(report["address_write_successes"], 5)
        self.assertEqual(report["state_selection_attempts"], 1)
        self.assertEqual(report["state_selection_successes"], 1)
        self.assertTrue(report["address_state_result"]["readback_match"])
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["home_phone_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["raw_values_exposed"])

    def test_country_readback_accepts_rendered_react_select_single_value_with_empty_input(self):
        page = MagicMock()
        country = MagicMock()
        country.input_value.return_value = ""
        country.evaluate.return_value = True
        country.get_attribute.return_value = "false"
        empty = MagicMock()
        empty.count.return_value = 0
        page.locator.return_value = empty

        with patch.object(
            safe_fill,
            "_selected_country_label_evidence",
            return_value={
                "present": True,
                "reviewed_label_match": True,
                "label_hash": "hash",
                "raw_value_exposed": False,
            },
        ):
            evidence = _country_readback_evidence(page, country)

        self.assertEqual(evidence["mode"], "selected_label")
        self.assertFalse(evidence["nonempty"])
        self.assertTrue(evidence["browser_valid"])
        self.assertTrue(evidence["selected_label_present"])
        self.assertTrue(evidence["selected_label_match"])
        self.assertFalse(evidence["raw_value_exposed"])

    def test_country_readback_accepts_nonempty_valid_closed_custom_value_without_exposing_raw(self):
        page = MagicMock()
        country = MagicMock()
        country.input_value.return_value = "opaque-internal-country-value"
        country.evaluate.return_value = True
        country.get_attribute.return_value = "false"
        empty = MagicMock()
        empty.count.return_value = 0
        page.locator.return_value = empty

        with patch.object(
            safe_fill,
            "_selected_country_label_evidence",
            return_value={
                "present": False,
                "reviewed_label_match": False,
                "label_hash": "",
                "raw_value_exposed": False,
            },
        ):
            evidence = _country_readback_evidence(page, country)

        self.assertEqual(evidence["mode"], "custom_committed")
        self.assertTrue(evidence["nonempty"])
        self.assertTrue(evidence["browser_valid"])
        self.assertEqual(evidence["visible_listbox_count"], 0)
        self.assertEqual(evidence["visible_option_count"], 0)
        self.assertFalse(evidence["raw_value_exposed"])
        self.assertNotIn("opaque-internal-country-value", repr(evidence))

    def test_country_readback_does_not_claim_success_for_invalid_nonempty_value(self):
        page = MagicMock()
        country = MagicMock()
        country.input_value.return_value = "opaque"
        country.evaluate.return_value = False
        country.get_attribute.return_value = "false"
        empty = MagicMock()
        empty.count.return_value = 0
        page.locator.return_value = empty

        with patch.object(
            safe_fill,
            "_selected_country_label_evidence",
            return_value={
                "present": False,
                "reviewed_label_match": False,
                "label_hash": "",
                "raw_value_exposed": False,
            },
        ):
            evidence = _country_readback_evidence(page, country)

        self.assertEqual(evidence["mode"], "mismatch")
        self.assertFalse(evidence["browser_valid"])

    def test_unreviewed_address_country_blocks_before_country_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = self.profile_path(tmp)
            raw = json.loads(Path(profile_path).read_text(encoding="utf-8"))
            raw["address_country_iso2"] = "DE"
            Path(profile_path).write_text(json.dumps(raw), encoding="utf-8")
            page = MagicMock()

            with patch.object(
                safe_fill,
                "run_identity_safe_fill",
                return_value={
                    "safe_fill_status": "verified",
                    "form_value_write_attempts": 0,
                    "form_value_write_successes": 0,
                    "email_readback_only": {"readback_match": True},
                },
            ), patch.object(
                safe_fill,
                "inspect_contact_address_on_verified_page",
                return_value=contact_fixture(),
            ), patch.object(
                safe_fill,
                "contact_contract_fingerprint",
                return_value=CONTACT_FP,
            ), patch.object(
                safe_fill,
                "_write_mobile_phone",
                return_value={"home_phone_touched": False},
            ):
                with self.assertRaisesRegex(PermissionError, "ADDRESS_COUNTRY_NOT_REVIEWED"):
                    run_on_verified_page(
                        page,
                        AdpSamePagePersonalInformationSafeFillRequest(
                            application_url=URL,
                            expected_manifest_fingerprint=MANIFEST_FP,
                            expected_contact_contract_fingerprint=CONTACT_FP,
                            expected_country_option_surface_fingerprint=COUNTRY_FP,
                            expected_state_option_surface_fingerprint=STATE_FP,
                            profile_json_path=profile_path,
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
