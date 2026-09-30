from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_state_selection_canary as canary
from ejs.services.adp_same_page_state_selection_canary import (
    AdpSamePageStateSelectionRequest,
    run_on_verified_page,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=960970"
)
MANIFEST_FP = "a" * 64
CONTACT_FP = "b" * 64
COUNTRY_FP = "c" * 64
CONTRACT_FP = "d" * 64
STATE_SURFACE_FP = "e" * 64


class StateSelectionCanaryTests(unittest.TestCase):
    def request(self, label="İstanbul"):
        return AdpSamePageStateSelectionRequest(
            application_url=URL,
            expected_manifest_fingerprint=MANIFEST_FP,
            expected_contact_contract_fingerprint=CONTACT_FP,
            expected_country_option_surface_fingerprint=COUNTRY_FP,
            expected_state_after_country_contract_fingerprint=CONTRACT_FP,
            expected_state_option_surface_fingerprint=STATE_SURFACE_FP,
            reviewed_state_label=label,
        )

    def structural(self):
        return {
            "state_open_click_attempts": 1,
            "state_option_surface": {
                "surface_fingerprint": STATE_SURFACE_FP,
                "unique_nonempty_labels": True,
                "options": [
                    {"label": "", "disabled": False},
                    {"label": "Adana", "disabled": False},
                    {"label": "İstanbul", "disabled": False},
                ],
            },
        }

    def make_page(self):
        page = MagicMock()
        state = MagicMock()
        state.count.return_value = 1
        state.is_visible.return_value = True
        state.is_enabled.return_value = True
        state.get_attribute.side_effect = lambda key: {
            "role": "combobox",
            "aria-controls": "PersonalAddress_state__listbox",
        }.get(key)
        state.inner_text.side_effect = ["", "İstanbul"]

        listbox = MagicMock()
        listbox.count.return_value = 1
        listbox.is_visible.return_value = True

        labels = ["", "Adana", "İstanbul"]
        option_mocks = []
        for label in labels:
            option = MagicMock()
            option.is_visible.return_value = True
            option.is_disabled.return_value = False
            option.inner_text.return_value = label
            option_mocks.append(option)
        options = MagicMock()
        options.count.return_value = len(option_mocks)
        options.nth.side_effect = option_mocks.__getitem__
        listbox.locator.return_value = options

        def locate(selector):
            if selector == "#PersonalAddress_state":
                return state
            if selector == "#PersonalAddress_state__listbox":
                return listbox
            raise AssertionError(selector)

        page.locator.side_effect = locate
        return page, state, option_mocks

    def test_selects_exact_reviewed_state_only(self):
        page, state, options = self.make_page()
        with patch.object(
            canary,
            "inspect_after_reviewed_country_selection",
            return_value=self.structural(),
        ), patch.object(
            canary,
            "state_contract_fingerprint",
            return_value=CONTRACT_FP,
        ):
            report = run_on_verified_page(page, self.request())

        options[2].click.assert_called_once()
        options[0].click.assert_not_called()
        options[1].click.assert_not_called()
        self.assertEqual(report["selection_status"], "verified")
        self.assertEqual(report["state_selection_attempts"], 1)
        self.assertEqual(report["state_selection_successes"], 1)
        self.assertTrue(report["state_readback_match"])
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_text_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)

    def test_surface_drift_blocks_before_state_mutation(self):
        page, state, options = self.make_page()
        structural = self.structural()
        structural["state_option_surface"]["surface_fingerprint"] = "f" * 64
        with patch.object(
            canary,
            "inspect_after_reviewed_country_selection",
            return_value=structural,
        ), patch.object(
            canary,
            "state_contract_fingerprint",
            return_value=CONTRACT_FP,
        ):
            with self.assertRaisesRegex(PermissionError, "OPTION_SURFACE_DRIFT"):
                run_on_verified_page(page, self.request())
        state.click.assert_not_called()
        for option in options:
            option.click.assert_not_called()

    def test_missing_reviewed_label_blocks_before_state_mutation(self):
        page, state, _ = self.make_page()
        with patch.object(
            canary,
            "inspect_after_reviewed_country_selection",
            return_value=self.structural(),
        ), patch.object(
            canary,
            "state_contract_fingerprint",
            return_value=CONTRACT_FP,
        ):
            with self.assertRaisesRegex(PermissionError, "REVIEWED_LABEL_COUNT:0"):
                run_on_verified_page(page, self.request("Bursa"))
        state.click.assert_not_called()

    def test_preexisting_different_state_fails_closed(self):
        page, state, _ = self.make_page()
        state.inner_text.side_effect = None
        state.inner_text.return_value = "Ankara"
        with patch.object(
            canary,
            "inspect_after_reviewed_country_selection",
            return_value=self.structural(),
        ), patch.object(
            canary,
            "state_contract_fingerprint",
            return_value=CONTRACT_FP,
        ):
            with self.assertRaisesRegex(PermissionError, "PREEXISTING_CONFLICT"):
                run_on_verified_page(page, self.request())
        state.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
