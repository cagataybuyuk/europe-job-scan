from __future__ import annotations

import unittest

from ejs.contracts.mw5 import Mw5MutationAuthority
from ejs.domain.mutation import MatchConfidence, OpportunityState, OutcomeSignal, OutcomeType
from ejs.persistence.mutation_memory import InMemoryMutationRepository
from ejs.services.guarded_mutation import plan_outcome_mutation
from ejs.services.mw5_executor import ExecutionStatus, execute_guarded_mutation
from ejs.services.mw5_workspace import build_application_cell_patches, validate_application_patch


def hso_state(status="Applied") -> OpportunityState:
    return OpportunityState(
        company="HSO International",
        role="Service Delivery Manager D365 F&O",
        country="Netherlands",
        status=status,
        applied_date="2026-07-27" if status != "To Apply" else "",
        last_update="2026-07-27",
        verification_status="Partially Verified",
        url_status="Valid",
    )


def hso_rejection(message_id="mw5b-historical-hso") -> OutcomeSignal:
    return OutcomeSignal(
        message_id=message_id,
        event_type=OutcomeType.REJECTION,
        email_date="2026-08-03",
        company="HSO International",
        role="Service Delivery Manager D365 F&O",
        confidence=MatchConfidence.HIGH,
        exact_match=True,
        evidence_ref=f"gmail:{message_id}",
        rejection_reason="Employer decided not to progress the application.",
    )


def receipt_signal(message_id="mw5b-receipt") -> OutcomeSignal:
    return OutcomeSignal(
        message_id=message_id,
        event_type=OutcomeType.APPLICATION_RECEIVED,
        email_date="2026-08-21",
        company="HSO International",
        role="Service Delivery Manager D365 F&O",
        confidence=MatchConfidence.HIGH,
        exact_match=True,
        evidence_ref=f"gmail:{message_id}",
    )


class Mw5ExecutorTests(unittest.TestCase):
    def test_historical_rejection_rehearsal_commits_all_components_in_memory(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection()
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        repo = InMemoryMutationRepository([st])
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=authority)
        self.assertEqual(result.status, ExecutionStatus.COMMITTED)
        self.assertTrue(result.postcondition_passed)
        self.assertEqual(repo.application_mutation_count, 1)
        self.assertEqual(repo.outcome_append_count, 1)
        self.assertEqual(repo.pipeline_append_count, 1)
        self.assertEqual(repo.derived_reconcile_count, 1)

    def test_receipt_rehearsal_sets_applied_date_and_next_action_patch(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state(status="To Apply")
        sig = receipt_signal()
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        patches = build_application_cell_patches(row_number=42, plan=plan)
        self.assertEqual([p.a1 for p in patches], ["J42", "K42", "P42", "X42"])
        self.assertEqual(plan.target_values["next_action"], "Monitor application / follow up if appropriate")

    def test_kill_switch_blocks_executor_even_with_eligible_shape(self):
        enabled = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("kill-1")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=enabled)
        disabled = Mw5MutationAuthority(mutation_flag_enabled=False)
        repo = InMemoryMutationRepository([st])
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=disabled)
        self.assertEqual(result.status, ExecutionStatus.KILL_SWITCHED)
        self.assertEqual(repo.application_mutation_count, 0)

    def test_execution_time_duplicate_is_suppressed(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("dup-exec")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        repo = InMemoryMutationRepository([st], outcome_keys={plan.outcome_key})
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=authority)
        self.assertEqual(result.status, ExecutionStatus.DUPLICATE_SUPPRESSED)
        self.assertEqual(repo.application_mutation_count, 0)

    def test_stale_fingerprint_aborts_before_first_write(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("stale-exec")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        changed = OpportunityState(**{**st.__dict__, "last_update": "2026-08-02"})
        repo = InMemoryMutationRepository([changed])
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=authority)
        self.assertEqual(result.status, ExecutionStatus.CONCURRENT_CHANGE)
        self.assertEqual(repo.outcome_append_count, 0)
        self.assertEqual(repo.application_mutation_count, 0)

    def test_partial_failure_after_application_repairs_missing_pipeline_and_derived_without_replaying_state(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("repair-after-app")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        repo = InMemoryMutationRepository([st], fail_once_after="application")
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=authority)
        self.assertEqual(result.status, ExecutionStatus.PARTIAL_WRITE_REPAIRED)
        self.assertTrue(result.postcondition_passed)
        self.assertEqual(repo.application_mutation_count, 1)
        self.assertEqual(repo.pipeline_append_count, 1)
        self.assertEqual(repo.derived_reconcile_count, 1)

    def test_partial_failure_after_pipeline_repairs_derived_only(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("repair-after-pipe")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        repo = InMemoryMutationRepository([st], fail_once_after="pipeline")
        result = execute_guarded_mutation(signal=sig, plan=plan, repo=repo, authority=authority)
        self.assertEqual(result.status, ExecutionStatus.PARTIAL_WRITE_REPAIRED)
        self.assertTrue(result.postcondition_passed)
        self.assertEqual(repo.application_mutation_count, 1)
        self.assertEqual(repo.pipeline_append_count, 1)
        self.assertEqual(repo.derived_reconcile_count, 1)

    def test_rejection_patch_cannot_write_applied_date(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("bad-patch")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        object.__setattr__(plan, "target_values", {**plan.target_values, "applied_date": "2026-08-03"})
        with self.assertRaises(PermissionError):
            validate_application_patch(plan)

    def test_header_row_patch_is_forbidden(self):
        authority = Mw5MutationAuthority(mutation_flag_enabled=True)
        st = hso_state()
        sig = hso_rejection("header")
        plan = plan_outcome_mutation(signal=sig, state=st, outcome_event_already_logged=False, authority=authority)
        with self.assertRaises(ValueError):
            build_application_cell_patches(row_number=1, plan=plan)


if __name__ == "__main__":
    unittest.main()
