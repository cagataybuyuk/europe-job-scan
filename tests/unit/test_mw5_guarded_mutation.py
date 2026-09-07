from __future__ import annotations

import unittest

from ejs.contracts.mw5 import Mw5MutationAuthority, pipeline_event_key
from ejs.domain.mutation import (
    MatchConfidence,
    MutationDisposition,
    OpportunityState,
    OutcomeSignal,
    OutcomeType,
)
from ejs.services.guarded_mutation import current_fingerprint, plan_outcome_mutation, validate_fingerprint
from ejs.services.mw5_preflight import build_live_noop_preflight


def state(status="Applied", **overrides):
    data = dict(
        company="HSO International",
        role="Service Delivery Manager D365 F&O",
        country="Netherlands",
        status=status,
        applied_date="2026-07-27" if status != "To Apply" else "",
        last_update="2026-07-27",
        verification_status="Partially Verified",
        url_status="Valid",
    )
    data.update(overrides)
    return OpportunityState(**data)


def signal(event_type=OutcomeType.REJECTION, confidence=MatchConfidence.HIGH, exact_match=True, **overrides):
    data = dict(
        message_id="19fc79cc5e76a5e4",
        event_type=event_type,
        email_date="2026-08-03",
        company="HSO International",
        role="Service Delivery Manager D365 F&O",
        confidence=confidence,
        exact_match=exact_match,
        evidence_ref="gmail:19fc79cc5e76a5e4",
        rejection_reason="Employer decided not to progress the application.",
    )
    data.update(overrides)
    return OutcomeSignal(**data)


class Mw5GuardedMutationTests(unittest.TestCase):
    def test_authority_blocks_user_fact_writes(self):
        with self.assertRaises(PermissionError):
            Mw5MutationAuthority(user_fact_writes_enabled=True).validate()

    def test_authority_blocks_external_form_writes(self):
        with self.assertRaises(PermissionError):
            Mw5MutationAuthority(external_form_writes_enabled=True).validate()

    def test_authority_blocks_terminal_reopen(self):
        with self.assertRaises(PermissionError):
            Mw5MutationAuthority(terminal_reopen_enabled=True).validate()

    def test_high_confidence_applied_rejection_is_eligible_when_flag_on(self):
        plan = plan_outcome_mutation(
            signal=signal(),
            state=state(),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertTrue(plan.should_mutate)
        self.assertEqual(plan.disposition, MutationDisposition.ELIGIBLE)
        self.assertEqual(plan.to_state, "Rejected")
        self.assertEqual(plan.state_rule, "ST-07")

    def test_kill_switch_blocks_otherwise_eligible_mutation(self):
        plan = plan_outcome_mutation(
            signal=signal(),
            state=state(),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=False),
        )
        self.assertFalse(plan.should_mutate)
        self.assertEqual(plan.disposition, MutationDisposition.KILL_SWITCHED)

    def test_untracked_or_nonexact_outcome_is_audit_only(self):
        plan = plan_outcome_mutation(
            signal=signal(exact_match=False),
            state=None,
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(),
        )
        self.assertFalse(plan.should_mutate)
        self.assertEqual(plan.disposition, MutationDisposition.AUDIT_ONLY)

    def test_low_confidence_is_audit_only(self):
        plan = plan_outcome_mutation(
            signal=signal(confidence=MatchConfidence.LOW),
            state=state(),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(),
        )
        self.assertEqual(plan.disposition, MutationDisposition.AUDIT_ONLY)

    def test_duplicate_message_id_suppresses_mutation_before_matching(self):
        plan = plan_outcome_mutation(
            signal=signal(),
            state=state(),
            outcome_event_already_logged=True,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertFalse(plan.should_mutate)
        self.assertEqual(plan.disposition, MutationDisposition.DUPLICATE_SUPPRESSED)

    def test_rejected_same_state_is_noop(self):
        plan = plan_outcome_mutation(
            signal=signal(message_id="new-msg"),
            state=state(status="Rejected"),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertEqual(plan.disposition, MutationDisposition.SAME_STATE_NOOP)
        self.assertFalse(plan.should_mutate)

    def test_rejection_from_to_review_is_blocked(self):
        plan = plan_outcome_mutation(
            signal=signal(message_id="new-msg"),
            state=state(status="To Review", applied_date=""),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertEqual(plan.disposition, MutationDisposition.PRECONDITION_FAILED)

    def test_exact_receipt_can_transition_to_apply_to_applied(self):
        receipt = signal(
            event_type=OutcomeType.APPLICATION_RECEIVED,
            message_id="receipt-1",
            email_date="2026-08-21",
            rejection_reason="",
        )
        plan = plan_outcome_mutation(
            signal=receipt,
            state=state(status="To Apply", applied_date=""),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertTrue(plan.should_mutate)
        self.assertEqual(plan.target_values["applied_date"], "2026-08-21")
        self.assertEqual(plan.state_rule, "ST-01")

    def test_receipt_missing_actual_date_is_blocked(self):
        receipt = signal(
            event_type=OutcomeType.APPLICATION_RECEIVED,
            message_id="receipt-2",
            email_date="",
            rejection_reason="",
        )
        plan = plan_outcome_mutation(
            signal=receipt,
            state=state(status="To Apply", applied_date=""),
            outcome_event_already_logged=False,
            authority=Mw5MutationAuthority(mutation_flag_enabled=True),
        )
        self.assertEqual(plan.disposition, MutationDisposition.PRECONDITION_FAILED)

    def test_pipeline_event_key_is_deterministic(self):
        a = pipeline_event_key("hso:role:nl", "Applied", "Rejected", "2026-08-03", "19fc")
        b = pipeline_event_key("hso:role:nl", "Applied", "Rejected", "2026-08-03", "19fc")
        self.assertEqual(a, b)

    def test_stale_fingerprint_detected(self):
        before = state()
        expected = current_fingerprint(before)
        fresh = state(last_update="2026-08-04")
        ok, actual = validate_fingerprint(expected=expected, fresh_state=fresh)
        self.assertFalse(ok)
        self.assertNotEqual(expected, actual)

    def test_live_vibe_noop_preflight(self):
        vibe = OutcomeSignal(
            message_id="1a01e75946fe44d6",
            event_type=OutcomeType.REJECTION,
            email_date="2026-08-20",
            company="Vibe Group",
            role="Unknown / not tracked",
            confidence=MatchConfidence.LOW,
            exact_match=False,
            evidence_ref="gmail:1a01e75946fe44d6",
        )
        report = build_live_noop_preflight(
            run_id="MW5A-20260821-1608",
            newest_signal=vibe,
            newest_state=None,
            outcome_event_already_logged=True,
            eligible_high_confidence_auto_apply_new_review=0,
        )
        self.assertEqual(report.business_mutations, 0)
        self.assertEqual(report.pipeline_events_appended, 0)
        self.assertIn("PENDING", report.status)


if __name__ == "__main__":
    unittest.main()
