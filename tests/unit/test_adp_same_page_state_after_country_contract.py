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
        self.assertEqual(report["state_selection_attempts"], 0)
        self.assertEqual(report["phone_write_attempts"], 0)
        self.assertEqual(report["address_text_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["state_candidate_values_read"])
        self.assertFalse(report["raw_values_exposed"])

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


if __name__ == "__main__":
    unittest.main()
