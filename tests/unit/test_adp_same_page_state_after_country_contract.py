from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_state_after_country_contract as contract
from ejs.services.adp_same_page_state_after_country_contract import (
    inspect_after_reviewed_country_selection,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64
COUNTRY_FP = "c" * 64


class StateAfterCountryContractTests(unittest.TestCase):
    def test_selects_only_reviewed_country_then_reads_state_structure(self):
        page = MagicMock()
        pre_state = {
            "visible": True,
            "metadata": {
                "tag": "input",
                "id": "PersonalAddress_state",
                "type": "text",
            },
            "descendants": [],
            "siblings": [],
            "ancestor_neighborhood": [],
            "parent_chain": [],
            "candidate_values_read": False,
        }
        post_state = {
            "visible": True,
            "metadata": {
                "tag": "div",
                "id": "PersonalAddress_state",
                "type": "",
                "role": "combobox",
                "aria_controls": "PersonalAddress_state__listbox",
            },
            "descendants": [
                {
                    "tag": "input",
                    "role": "combobox",
                    "class_name": "MDFSelectBox__input",
                }
            ],
            "siblings": [],
            "ancestor_neighborhood": [],
            "parent_chain": [],
            "candidate_values_read": False,
        }

        def fake_country(page_arg, profile, request, counters):
            self.assertEqual(profile.address_country_iso2, "TR")
            self.assertEqual(
                request.expected_country_option_surface_fingerprint,
                COUNTRY_FP,
            )
            counters["form_value_write_attempts"] += 1
            counters["form_value_write_successes"] += 1
            counters["address_write_attempts"] += 1
            counters["address_write_successes"] += 1
            counters["combobox_open_click_attempts"] += 1
            counters["combobox_open_click_successes"] += 1
            counters["country_selection_attempts"] += 1
            counters["country_selection_successes"] += 1
            return {
                "readback_match": True,
                "readback_mode": "selected_label",
                "executed": True,
            }

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
            "_snapshot_state",
            side_effect=[pre_state, post_state],
        ), patch.object(
            contract,
            "_set_address_country",
            side_effect=fake_country,
        ), patch.object(
            contract,
            "_state_option_surface",
            return_value={
                "listbox_id": "PersonalAddress_state__listbox",
                "visible_option_count": 81,
                "options": [
                    {
                        "ordinal": 0,
                        "label": "Adana",
                        "disabled": False,
                        "id": "state-option-0",
                        "role": "option",
                        "value_attribute_read": False,
                        "property_value_read": False,
                    },
                    {
                        "ordinal": 33,
                        "label": "Istanbul",
                        "disabled": False,
                        "id": "state-option-33",
                        "role": "option",
                        "value_attribute_read": False,
                        "property_value_read": False,
                    },
                ],
                "unique_visible_labels": True,
                "candidate_values_read": False,
                "raw_candidate_values_exposed": False,
                "surface_fingerprint": "e" * 64,
                "close_escape_attempts": 1,
            },
        ):
            report = inspect_after_reviewed_country_selection(
                page,
                URL,
                MANIFEST_FP,
                CONTACT_FP,
                COUNTRY_FP,
            )

        self.assertEqual(report["country_selection_attempts"], 1)
        self.assertEqual(report["country_selection_successes"], 1)
        self.assertEqual(report["country_selection"]["readback_mode"], "selected_label")
        self.assertEqual(report["pre_state"]["metadata"]["tag"], "input")
        self.assertEqual(report["post_state"]["metadata"]["tag"], "div")
        self.assertEqual(report["state_option_surface"]["visible_option_count"], 81)
        self.assertEqual(report["state_open_click_attempts"], 1)
        self.assertEqual(report["state_open_click_successes"], 1)
        self.assertEqual(report["state_selection_attempts"], 0)
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_text_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["state_candidate_values_read"])
        self.assertFalse(report["raw_values_exposed"])

    def test_state_option_surface_clicks_open_but_never_selects(self):
        page = MagicMock()
        state = MagicMock()
        state.click = MagicMock()
        state.press = MagicMock()
        listbox = MagicMock()
        listbox.count.return_value = 1
        listbox.is_visible.return_value = True

        first = MagicMock()
        first.is_visible.return_value = True
        first.inner_text.return_value = "Adana"
        first.is_disabled.return_value = False
        first.get_attribute.side_effect = lambda key: {
            "id": "PersonalAddress_state__option-0",
            "role": "option",
        }.get(key)

        second = MagicMock()
        second.is_visible.return_value = True
        second.inner_text.return_value = "Istanbul"
        second.is_disabled.return_value = False
        second.get_attribute.side_effect = lambda key: {
            "id": "PersonalAddress_state__option-1",
            "role": "option",
        }.get(key)

        options = MagicMock()
        options.count.return_value = 2
        options.nth.side_effect = [first, second]
        listbox.locator.return_value = options

        def locate(selector):
            if selector == "#PersonalAddress_state":
                return state
            if selector == "#PersonalAddress_state__listbox":
                return listbox
            raise AssertionError(selector)

        page.locator.side_effect = locate
        post_state = {
            "metadata": {
                "role": "combobox",
                "aria_controls": "PersonalAddress_state__listbox",
            }
        }

        surface = contract._state_option_surface(
            page,
            post_state,
            timeout_ms=20_000,
        )

        state.click.assert_called_once()
        state.press.assert_called_once_with("Escape", timeout=20_000)
        first.click.assert_not_called()
        second.click.assert_not_called()
        self.assertEqual(surface["visible_option_count"], 2)
        self.assertEqual(
            [row["label"] for row in surface["options"]],
            ["Adana", "Istanbul"],
        )
        self.assertFalse(surface["candidate_values_read"])
        self.assertFalse(surface["raw_candidate_values_exposed"])

    def test_pre_contact_drift_blocks_before_country_selection(self):
        page = MagicMock()
        with patch.object(
            contract,
            "inspect_contact_address_on_verified_page",
            return_value={"controls": []},
        ), patch.object(
            contract,
            "contact_contract_fingerprint",
            return_value="d" * 64,
        ), patch.object(
            contract,
            "_set_address_country",
        ) as set_country:
            with self.assertRaisesRegex(PermissionError, "PRE_CONTACT_FINGERPRINT_MISMATCH"):
                inspect_after_reviewed_country_selection(
                    page,
                    URL,
                    MANIFEST_FP,
                    CONTACT_FP,
                    COUNTRY_FP,
                )
        set_country.assert_not_called()

    def test_country_readback_failure_blocks_post_state_snapshot(self):
        page = MagicMock()
        pre_state = {
            "visible": True,
            "metadata": {"tag": "input"},
            "descendants": [],
            "siblings": [],
            "ancestor_neighborhood": [],
            "parent_chain": [],
            "candidate_values_read": False,
        }
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
            "_snapshot_state",
            return_value=pre_state,
        ) as snapshot, patch.object(
            contract,
            "_set_address_country",
            return_value={
                "readback_match": False,
                "readback_mode": "empty",
                "executed": True,
            },
        ):
            with self.assertRaisesRegex(PermissionError, "COUNTRY_SELECTION_NOT_VERIFIED"):
                inspect_after_reviewed_country_selection(
                    page,
                    URL,
                    MANIFEST_FP,
                    CONTACT_FP,
                    COUNTRY_FP,
                )
        self.assertEqual(snapshot.call_count, 1)


class StateOptionObservationRegressionTests(unittest.TestCase):
    def make_surface(self, labels):
        page = MagicMock()
        state = MagicMock()
        listbox = MagicMock()
        listbox.count.return_value = 1
        listbox.is_visible.return_value = True
        options = []
        for index, label in enumerate(labels):
            option = MagicMock()
            option.is_visible.return_value = True
            option.inner_text.return_value = label
            option.is_disabled.return_value = index == 0
            def attribute(key, index=index):
                if key not in ("id", "role"):
                    raise AssertionError(f"Unexpected attribute read: {key}")
                return f"state-option-{index}" if key == "id" else "option"
            option.get_attribute.side_effect = attribute
            options.append(option)
        collection = listbox.locator.return_value
        collection.count.return_value = len(options)
        collection.nth.side_effect = options.__getitem__
        page.locator.side_effect = {
            "#PersonalAddress_state": state,
            "#PersonalAddress_state__listbox": listbox,
        }.__getitem__
        self.post_state = {"metadata": {
            "role": "combobox",
            "aria_controls": "PersonalAddress_state__listbox",
        }}
        return page, state, listbox, options

    def inspect(self, page):
        return contract._state_option_surface(page, self.post_state, timeout_ms=100)

    def assert_no_selection_or_value_read(self, options):
        for option in options:
            option.click.assert_not_called()
            option.press.assert_not_called()
            option.input_value.assert_not_called()
            option.evaluate.assert_not_called()
            option.select_option.assert_not_called()

    def test_blank_row_is_retained_without_losing_following_labels(self):
        page, state, _, options = self.make_surface([" \n ", "Adana", "Istanbul"])
        surface = self.inspect(page)
        self.assertEqual([r["label"] for r in surface["options"]], ["", "Adana", "Istanbul"])
        self.assertEqual(surface["visible_option_count"], 3)
        self.assertEqual(surface["empty_label_count"], 1)
        self.assertEqual(surface["nonempty_label_count"], 2)
        self.assertTrue(surface["options"][0]["label_empty"])
        self.assertTrue(surface["options"][0]["disabled"])
        self.assertFalse(surface["label_observation_complete"])
        self.assertFalse(surface["state_selection_authorized"])
        state.press.assert_called_once_with("Escape", timeout=100)
        self.assert_no_selection_or_value_read(options)
        fingerprint = surface["surface_fingerprint"]
        options[0].inner_text.return_value = "Choose a state"
        self.assertNotEqual(self.inspect(page)["surface_fingerprint"], fingerprint)

    def test_all_blank_rows_are_reported_as_incomplete_not_valid_states(self):
        page, _, _, options = self.make_surface(["", " "])
        surface = self.inspect(page)
        self.assertEqual(surface["nonempty_label_count"], 0)
        self.assertEqual(surface["empty_label_count"], 2)
        self.assertFalse(surface["label_observation_complete"])
        self.assertFalse(surface["unique_visible_labels"])
        self.assertFalse(surface["state_selection_authorized"])
        self.assert_no_selection_or_value_read(options)

    def test_duplicate_nonempty_labels_remain_detectable(self):
        page, _, _, _ = self.make_surface(["", "Istanbul", "Istanbul"])
        self.assertFalse(self.inspect(page)["unique_nonempty_labels"])

    def test_hidden_blank_row_is_not_counted(self):
        page, _, _, options = self.make_surface(["", "Adana"])
        options[0].is_visible.return_value = False
        surface = self.inspect(page)
        self.assertEqual(surface["visible_option_count"], 1)
        self.assertEqual(surface["empty_label_count"], 0)
        self.assertTrue(surface["label_observation_complete"])
        options[0].inner_text.assert_not_called()

    def test_count_and_visibility_failures_still_attempt_escape(self):
        for mode in ("invisible", "empty", "too_many", "hidden_options"):
            with self.subTest(mode=mode):
                page, state, listbox, options = self.make_surface(["Adana"])
                if mode == "invisible":
                    listbox.is_visible.return_value = False
                elif mode == "empty":
                    listbox.locator.return_value.count.return_value = 0
                elif mode == "too_many":
                    listbox.locator.return_value.count.return_value = 301
                else:
                    options[0].is_visible.return_value = False
                with self.assertRaises(PermissionError):
                    self.inspect(page)
                state.press.assert_called_once_with("Escape", timeout=100)
                self.assert_no_selection_or_value_read(options)

    def test_cleanup_failure_preserves_original_inspection_error(self):
        page, state, listbox, _ = self.make_surface(["Adana"])
        listbox.is_visible.return_value = False
        state.press.side_effect = RuntimeError("cleanup failed")
        with self.assertRaisesRegex(PermissionError, "LISTBOX_NOT_VISIBLE"):
            self.inspect(page)

    def test_cleanup_failure_cannot_produce_success_report(self):
        page, state, _, _ = self.make_surface(["Adana"])
        state.press.side_effect = RuntimeError("cleanup failed")
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.inspect(page)

    def test_listbox_drift_blocks_before_open(self):
        page, state, _, _ = self.make_surface(["Adana"])
        self.post_state["metadata"]["aria_controls"] = "other-listbox"
        with self.assertRaisesRegex(PermissionError, "LISTBOX_ID_DRIFT"):
            self.inspect(page)
        state.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
