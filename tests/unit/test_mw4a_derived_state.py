from __future__ import annotations

import unittest

from ejs.contracts.mw4 import Mw4aAuthority
from ejs.domain.derived_state import ReadinessProjectionInput
from ejs.services.derived_state import derive_readiness, derive_user_action


def row(**overrides):
    base = dict(
        company="Example",
        role="Business Analyst",
        application_status="To Apply",
        form_access="Full",
        total_required=5,
        ready_ai=5,
        user_required_factual=0,
        user_only_legal=0,
        technical_captcha=0,
        blocking_review=0,
        unresolved_required=0,
        readiness_confidence="High",
        cv_asset_ready=True,
        current_fill_pack_status="Ready for User Submit",
    )
    base.update(overrides)
    return ReadinessProjectionInput(**base)


class Mw4aDerivedStateTests(unittest.TestCase):
    def test_high_confidence_ready_is_write_candidate_for_mw4b(self):
        p = derive_readiness(row())
        self.assertTrue(p.status_parity)
        self.assertTrue(p.write_eligible)
        self.assertEqual(p.derived_status.value, "Ready for User Submit")

    def test_user_fact_blocks_immediate_submit(self):
        p = derive_readiness(row(user_required_factual=1, current_fill_pack_status="Ready - User Input Required"))
        self.assertEqual(p.derived_status.value, "Ready - User Input Required")
        self.assertTrue(p.status_parity)

    def test_blocking_review_blocks_immediate_submit(self):
        p = derive_readiness(row(blocking_review=2, current_fill_pack_status="Ready - User Input Required"))
        self.assertEqual(p.derived_status.value, "Ready - User Input Required")

    def test_user_only_legal_does_not_block_ready_for_user_submit(self):
        p = derive_readiness(row(total_required=6, ready_ai=5, user_only_legal=1))
        self.assertEqual(p.derived_status.value, "Ready for User Submit")

    def test_medium_confidence_is_audit_only(self):
        p = derive_readiness(row(readiness_confidence="Medium", user_required_factual=1, current_fill_pack_status="Ready - User Input Required"))
        self.assertTrue(p.status_parity)
        self.assertFalse(p.write_eligible)

    def test_uninspected_form_is_needs_form_access(self):
        p = derive_readiness(row(form_access="Recovered - Inspect Required", total_required=None, ready_ai=None, current_fill_pack_status="Needs Form Access"))
        self.assertEqual(p.derived_status.value, "Needs Form Access")
        self.assertTrue(p.status_parity)

    def test_unresolved_required_fails_closed(self):
        p = derive_readiness(row(unresolved_required=1, current_fill_pack_status="Needs User Clarification"))
        self.assertEqual(p.derived_status.value, "Needs User Clarification")
        self.assertFalse(p.write_eligible)

    def test_user_action_submit_projection(self):
        action = derive_user_action(derive_readiness(row()))
        self.assertTrue(action.include)
        self.assertEqual(action.category, "Submit")
        self.assertEqual(action.priority, "P1")

    def test_user_action_input_projection(self):
        action = derive_user_action(derive_readiness(row(user_required_factual=1, current_fill_pack_status="Ready - User Input Required")))
        self.assertTrue(action.include)
        self.assertEqual(action.category, "Input + Submit")
        self.assertEqual(action.priority, "P2")

    def test_needs_form_access_is_not_user_action(self):
        action = derive_user_action(derive_readiness(row(form_access="Not Inspected", current_fill_pack_status="Needs Form Access")))
        self.assertFalse(action.include)

    def test_mw4a_forbids_derived_production_writes(self):
        with self.assertRaises(PermissionError):
            Mw4aAuthority(production_derived_writes_enabled=True).validate()

    def test_mw4a_forbids_business_writes(self):
        with self.assertRaises(PermissionError):
            Mw4aAuthority(business_state_writes_enabled=True).validate()

    def test_mw4a_forbids_external_form_writes(self):
        with self.assertRaises(PermissionError):
            Mw4aAuthority(external_form_writes_enabled=True).validate()


if __name__ == "__main__":
    unittest.main()
