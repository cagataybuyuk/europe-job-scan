from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ejs.contracts.mw5 import Mw5MutationAuthority
from ejs.domain.mutation import MutationDisposition, MutationPlan, OpportunityState, OutcomeSignal
from ejs.persistence.mutation_memory import ConcurrentMutationError, InjectedWriteFailure
from ejs.services.guarded_mutation import current_fingerprint
from ejs.services.mw5_workspace import validate_application_patch


class ExecutionStatus(str, Enum):
    COMMITTED = "committed"
    DUPLICATE_SUPPRESSED = "duplicate_suppressed"
    KILL_SWITCHED = "kill_switched"
    PRECONDITION_FAILED = "precondition_failed"
    CONCURRENT_CHANGE = "concurrent_change_detected"
    PARTIAL_WRITE_REPAIRED = "partial_write_repaired"
    REPAIR_FAILED = "repair_failed"


@dataclass(frozen=True)
class ExecutionResult:
    status: ExecutionStatus
    mutation_executed: bool
    outcome_present: bool
    application_at_target: bool
    pipeline_present: bool
    derived_reconciled: bool
    repair_attempted: bool
    reason: str

    @property
    def postcondition_passed(self) -> bool:
        return all(
            (
                self.outcome_present,
                self.application_at_target,
                self.pipeline_present,
                self.derived_reconciled,
            )
        )


def _application_at_target(state: OpportunityState | None, plan: MutationPlan) -> bool:
    if state is None:
        return False
    for key in ("status", "applied_date", "last_update"):
        if key in plan.target_values and getattr(state, key) != plan.target_values[key]:
            return False
    return True


def _snapshot_result(repo, plan: MutationPlan, *, status: ExecutionStatus, mutation_executed: bool, repair_attempted: bool, reason: str) -> ExecutionResult:
    state = repo.read_state(plan.opportunity_key) if plan.opportunity_key else None
    return ExecutionResult(
        status=status,
        mutation_executed=mutation_executed,
        outcome_present=repo.has_outcome(plan.outcome_key),
        application_at_target=_application_at_target(state, plan),
        pipeline_present=bool(plan.pipeline_key and repo.has_pipeline(plan.pipeline_key)),
        derived_reconciled=bool(plan.opportunity_key and plan.opportunity_key in repo.derived_reconciled),
        repair_attempted=repair_attempted,
        reason=reason,
    )


def _repair_missing_components(repo, plan: MutationPlan) -> bool:
    """REC-10 style repair: preserve completed authoritative writes and fill gaps only."""
    if plan.opportunity_key is None or plan.pipeline_key is None or plan.expected_fingerprint is None:
        return False

    if not repo.has_outcome(plan.outcome_key):
        repo.append_outcome(plan.outcome_key)

    state = repo.read_state(plan.opportunity_key)
    if not _application_at_target(state, plan):
        if state is None:
            return False
        # If the authoritative state is still the expected from-state, apply the
        # original patch. If it changed semantically, fail closed instead of
        # overwriting a newer state.
        if current_fingerprint(state) != plan.expected_fingerprint:
            return False
        repo.update_application(
            plan.opportunity_key,
            expected_fingerprint=plan.expected_fingerprint,
            target_values=plan.target_values,
        )

    if not repo.has_pipeline(plan.pipeline_key):
        repo.append_pipeline(plan.pipeline_key)
    repo.reconcile_derived(plan.opportunity_key)
    return True


def execute_guarded_mutation(
    *,
    signal: OutcomeSignal,
    plan: MutationPlan,
    repo,
    authority: Mw5MutationAuthority,
) -> ExecutionResult:
    authority.validate()

    if plan.disposition == MutationDisposition.DUPLICATE_SUPPRESSED:
        return ExecutionResult(ExecutionStatus.DUPLICATE_SUPPRESSED, False, True, False, False, False, False, plan.reason)
    if plan.disposition == MutationDisposition.KILL_SWITCHED or not authority.mutation_flag_enabled:
        return ExecutionResult(ExecutionStatus.KILL_SWITCHED, False, repo.has_outcome(plan.outcome_key), False, False, False, False, "mutation kill switch is OFF")
    if plan.disposition != MutationDisposition.ELIGIBLE or not plan.should_mutate:
        return ExecutionResult(ExecutionStatus.PRECONDITION_FAILED, False, repo.has_outcome(plan.outcome_key), False, False, False, False, plan.reason)
    if plan.opportunity_key is None or plan.pipeline_key is None or plan.expected_fingerprint is None:
        return ExecutionResult(ExecutionStatus.PRECONDITION_FAILED, False, False, False, False, False, False, "eligible plan is incomplete")

    validate_application_patch(plan)

    fresh = repo.read_state(plan.opportunity_key)
    if fresh is None or current_fingerprint(fresh) != plan.expected_fingerprint:
        return ExecutionResult(ExecutionStatus.CONCURRENT_CHANGE, False, repo.has_outcome(plan.outcome_key), False, repo.has_pipeline(plan.pipeline_key), False, False, "CC-01 fingerprint mismatch before first write")

    # Re-check idempotency at execution time, not only at planning time.
    if repo.has_outcome(plan.outcome_key) or repo.has_pipeline(plan.pipeline_key):
        return _snapshot_result(
            repo,
            plan,
            status=ExecutionStatus.DUPLICATE_SUPPRESSED,
            mutation_executed=False,
            repair_attempted=False,
            reason="execution-time idempotency key already exists",
        )

    try:
        # WS-05/WS-07 authoritative ordering: evidence first, then current state,
        # append-only Pipeline History, then deterministic derived reconciliation.
        repo.append_outcome(plan.outcome_key)
        repo.update_application(
            plan.opportunity_key,
            expected_fingerprint=plan.expected_fingerprint,
            target_values=plan.target_values,
        )
        repo.append_pipeline(plan.pipeline_key)
        repo.reconcile_derived(plan.opportunity_key)
        result = _snapshot_result(
            repo,
            plan,
            status=ExecutionStatus.COMMITTED,
            mutation_executed=True,
            repair_attempted=False,
            reason="ordered MW-5 write-set committed and reconciled",
        )
        if not result.postcondition_passed:
            raise InjectedWriteFailure("postcondition gap")
        return result
    except (InjectedWriteFailure, ConcurrentMutationError) as exc:
        try:
            repaired = _repair_missing_components(repo, plan)
        except (InjectedWriteFailure, ConcurrentMutationError):
            repaired = False
        result = _snapshot_result(
            repo,
            plan,
            status=ExecutionStatus.PARTIAL_WRITE_REPAIRED if repaired else ExecutionStatus.REPAIR_FAILED,
            mutation_executed=repo.application_mutation_count > 0,
            repair_attempted=True,
            reason=f"{exc}; deterministic repair {'completed' if repaired else 'failed closed'}",
        )
        if repaired and not result.postcondition_passed:
            return ExecutionResult(
                ExecutionStatus.REPAIR_FAILED,
                result.mutation_executed,
                result.outcome_present,
                result.application_at_target,
                result.pipeline_present,
                result.derived_reconciled,
                True,
                "repair returned without satisfying all postconditions",
            )
        return result
