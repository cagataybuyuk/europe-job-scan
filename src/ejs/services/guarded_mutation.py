from __future__ import annotations

from dataclasses import replace

from ejs.contracts.mw5 import (
    Mw5MutationAuthority,
    gmail_outcome_key,
    opportunity_fingerprint,
    opportunity_key,
    pipeline_event_key,
)
from ejs.domain.mutation import (
    MatchConfidence,
    MutationDisposition,
    MutationPlan,
    OpportunityState,
    OutcomeSignal,
    OutcomeType,
)


ACTIVE_SUBMITTED_STATES = frozenset(
    {
        "Applied",
        "Recruiter Contacted",
        "Screening",
        "Assessment",
        "Interview 1",
        "Interview 2",
        "Final Interview",
    }
)


def current_fingerprint(state: OpportunityState) -> str:
    return opportunity_fingerprint(
        status=state.status,
        applied_date=state.applied_date,
        last_update=state.last_update,
        verification_status=state.verification_status,
        url_status=state.url_status,
    )


def _base_plan(signal: OutcomeSignal, disposition: MutationDisposition, reason: str) -> MutationPlan:
    return MutationPlan(
        disposition=disposition,
        should_mutate=False,
        reason=reason,
        outcome_key=gmail_outcome_key(signal.message_id),
        pipeline_key=None,
        opportunity_key=None,
        from_state=None,
        to_state=None,
        state_rule=None,
        expected_fingerprint=None,
        target_values={},
    )


def plan_outcome_mutation(
    *,
    signal: OutcomeSignal,
    state: OpportunityState | None,
    outcome_event_already_logged: bool,
    pipeline_event_already_logged: bool = False,
    authority: Mw5MutationAuthority,
) -> MutationPlan:
    authority.validate()

    if outcome_event_already_logged:
        return _base_plan(
            signal,
            MutationDisposition.DUPLICATE_SUPPRESSED,
            "provider Gmail Message ID already exists; no second business mutation allowed",
        )

    if signal.confidence != MatchConfidence.HIGH or not signal.exact_match or state is None:
        return _base_plan(
            signal,
            MutationDisposition.AUDIT_ONLY,
            "outcome is not a High-confidence exact tracked-opportunity match",
        )

    opp_key = opportunity_key(state.company, state.role, state.country)
    fp = current_fingerprint(state)

    if signal.event_type == OutcomeType.APPLICATION_RECEIVED:
        if not signal.email_date:
            plan = _base_plan(
                signal,
                MutationDisposition.PRECONDITION_FAILED,
                "application receipt lacks an actual evidence date",
            )
            return replace(plan, opportunity_key=opp_key, expected_fingerprint=fp)
        if state.status == "Applied":
            return MutationPlan(
                MutationDisposition.SAME_STATE_NOOP,
                False,
                "receipt matches an already-Applied opportunity; audit/metadata only",
                gmail_outcome_key(signal.message_id),
                None,
                opp_key,
                state.status,
                state.status,
                "ST-01",
                fp,
                {},
            )
        from_state, to_state, rule = state.status, "Applied", "ST-01"
        if not authority.transition_allowed(from_state, to_state, rule):
            return MutationPlan(
                MutationDisposition.PRECONDITION_FAILED,
                False,
                f"{from_state} → Applied is outside the initial MW-5 transition allowlist",
                gmail_outcome_key(signal.message_id),
                None,
                opp_key,
                from_state,
                to_state,
                rule,
                fp,
                {},
            )
        pkey = pipeline_event_key(opp_key, from_state, to_state, signal.email_date, signal.message_id)
        if pipeline_event_already_logged:
            return MutationPlan(
                MutationDisposition.DUPLICATE_SUPPRESSED,
                False,
                "pipeline transition key already exists",
                gmail_outcome_key(signal.message_id),
                pkey,
                opp_key,
                from_state,
                to_state,
                rule,
                fp,
                {},
            )
        target = {
            "status": "Applied",
            "applied_date": signal.email_date,
            "next_action": "Monitor application / follow up if appropriate",
            "last_update": signal.email_date,
        }
        disposition = MutationDisposition.ELIGIBLE if authority.mutation_flag_enabled else MutationDisposition.KILL_SWITCHED
        return MutationPlan(
            disposition,
            authority.mutation_flag_enabled,
            "guarded ST-01 mutation eligible" if authority.mutation_flag_enabled else "all guards pass but MW-5 mutation kill switch is OFF",
            gmail_outcome_key(signal.message_id),
            pkey,
            opp_key,
            from_state,
            to_state,
            rule,
            fp,
            target,
        )

    if signal.event_type == OutcomeType.REJECTION:
        if state.status == "Rejected":
            return MutationPlan(
                MutationDisposition.SAME_STATE_NOOP,
                False,
                "opportunity is already Rejected; duplicate rejection cannot mutate again",
                gmail_outcome_key(signal.message_id),
                None,
                opp_key,
                state.status,
                state.status,
                "ST-07",
                fp,
                {},
            )
        if state.status not in ACTIVE_SUBMITTED_STATES:
            return MutationPlan(
                MutationDisposition.PRECONDITION_FAILED,
                False,
                f"rejection cannot transition non-submitted state {state.status}",
                gmail_outcome_key(signal.message_id),
                None,
                opp_key,
                state.status,
                "Rejected",
                "ST-07",
                fp,
                {},
            )
        from_state, to_state, rule = state.status, "Rejected", "ST-07"
        if not authority.transition_allowed(from_state, to_state, rule):
            return MutationPlan(
                MutationDisposition.PRECONDITION_FAILED,
                False,
                "transition is not in the initial MW-5 allowlist",
                gmail_outcome_key(signal.message_id),
                None,
                opp_key,
                from_state,
                to_state,
                rule,
                fp,
                {},
            )
        pkey = pipeline_event_key(opp_key, from_state, to_state, signal.email_date, signal.message_id)
        if pipeline_event_already_logged:
            return MutationPlan(
                MutationDisposition.DUPLICATE_SUPPRESSED,
                False,
                "pipeline transition key already exists",
                gmail_outcome_key(signal.message_id),
                pkey,
                opp_key,
                from_state,
                to_state,
                rule,
                fp,
                {},
            )
        target = {
            "status": "Rejected",
            "last_update": signal.email_date,
            "rejection_reason": signal.rejection_reason or "Employer rejection received.",
        }
        disposition = MutationDisposition.ELIGIBLE if authority.mutation_flag_enabled else MutationDisposition.KILL_SWITCHED
        return MutationPlan(
            disposition,
            authority.mutation_flag_enabled,
            "guarded ST-07 mutation eligible" if authority.mutation_flag_enabled else "all guards pass but MW-5 mutation kill switch is OFF",
            gmail_outcome_key(signal.message_id),
            pkey,
            opp_key,
            from_state,
            to_state,
            rule,
            fp,
            target,
        )

    return _base_plan(
        signal,
        MutationDisposition.PRECONDITION_FAILED,
        f"event type {signal.event_type.value} is outside the initial MW-5 canary scope",
    )


def validate_fingerprint(*, expected: str, fresh_state: OpportunityState) -> tuple[bool, str]:
    actual = current_fingerprint(fresh_state)
    if expected == actual:
        return True, actual
    return False, actual
