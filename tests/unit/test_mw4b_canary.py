from __future__ import annotations

import unittest

from ejs.contracts.mw4 import Mw4bAuthority
from ejs.domain.derived_state import ReadinessProjectionInput
from ejs.services.derived_state import derive_readiness
from ejs.services.mw4b_canary import DerivedWriteSnapshot, plan_readiness_write


def projection(confidence="High", current_status="Ready for User Submit"):
    item = ReadinessProjectionInput(
        company="Planon",
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
        readiness_confidence=confidence,
        cv_asset_ready=True,
        current_fill_pack_status=current_status,
    )
    return derive_readiness(item)


def snapshot(**overrides):
    data = dict(
        company="Planon",
        role="Business Analyst",
        application_status="To Apply",
        rule_version="READINESS-1.0",
        blocking_review=0,
        unresolved_required=0,
        confidence="High",
        derived_status="Ready for User Submit",
        last_evaluated="2026-08-14",
    )
    data.update(overrides)
    return DerivedWriteSnapshot(**data)


class Mw4bCanaryTests(unittest.TestCase):
    def test_authority_allows_only_derived_readiness_fields(self):
        Mw4bAuthority().validate()

    def test_authority_blocks_business_state_writes(self):
        with self.assertRaises(PermissionError):
            Mw4bAuthority(business_state_writes_enabled=True).validate()

    def test_authority_blocks_user_fact_writes(self):
        with self.assertRaises(PermissionError):
            Mw4bAuthority(user_fact_writes_enabled=True).validate()

    def test_authority_blocks_external_form_writes(self):
        with self.assertRaises(PermissionError):
            Mw4bAuthority(external_form_writes_enabled=True).validate()

    def test_high_confidence_changed_evaluation_date_writes(self):
        plan = plan_readiness_write(
            run_id="MW4B-20260817-1539",
            projection=projection(),
            current=snapshot(),
            blocking_review=0,
            unresolved_required=0,
            evaluated_date="2026-08-17",
            authority=Mw4bAuthority(),
        )
        self.assertTrue(plan.should_write)

    def test_retry_is_duplicate_suppressed(self):
        plan = plan_readiness_write(
            run_id="MW4B-20260817-1539",
            projection=projection(),
            current=snapshot(last_evaluated="2026-08-17"),
            blocking_review=0,
            unresolved_required=0,
            evaluated_date="2026-08-17",
            authority=Mw4bAuthority(),
        )
        self.assertFalse(plan.should_write)
        self.assertIn("duplicate_suppressed", plan.reason)

    def test_non_to_apply_fails_closed(self):
        plan = plan_readiness_write(
            run_id="MW4B-20260817-1539",
            projection=projection(),
            current=snapshot(application_status="Applied"),
            blocking_review=0,
            unresolved_required=0,
            evaluated_date="2026-08-17",
            authority=Mw4bAuthority(),
        )
        self.assertFalse(plan.should_write)

    def test_medium_confidence_fails_closed(self):
        plan = plan_readiness_write(
            run_id="MW4B-20260817-1539",
            projection=projection(confidence="Medium"),
            current=snapshot(confidence="Medium"),
            blocking_review=0,
            unresolved_required=0,
            evaluated_date="2026-08-17",
            authority=Mw4bAuthority(),
        )
        self.assertFalse(plan.should_write)


if __name__ == "__main__":
    unittest.main()
