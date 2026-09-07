from __future__ import annotations

from dataclasses import asdict, dataclass

from ejs.contracts.mw5 import Mw5MutationAuthority
from ejs.domain.mutation import OpportunityState, OutcomeSignal
from ejs.services.guarded_mutation import plan_outcome_mutation


@dataclass(frozen=True)
class Mw5PreflightReport:
    run_id: str
    newest_outcome_message_id: str
    outcome_disposition: str
    outcome_reason: str
    eligible_high_confidence_auto_apply_new_review: int
    mutation_flag_enabled: bool
    business_mutations: int
    pipeline_events_appended: int
    user_fact_mutations: int
    external_form_mutations: int
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_live_noop_preflight(
    *,
    run_id: str,
    newest_signal: OutcomeSignal,
    newest_state: OpportunityState | None,
    outcome_event_already_logged: bool,
    eligible_high_confidence_auto_apply_new_review: int,
) -> Mw5PreflightReport:
    authority = Mw5MutationAuthority(mutation_flag_enabled=False)
    plan = plan_outcome_mutation(
        signal=newest_signal,
        state=newest_state,
        outcome_event_already_logged=outcome_event_already_logged,
        authority=authority,
    )
    status = "PASS / REAL MUTATION PENDING"
    return Mw5PreflightReport(
        run_id=run_id,
        newest_outcome_message_id=newest_signal.message_id,
        outcome_disposition=plan.disposition.value,
        outcome_reason=plan.reason,
        eligible_high_confidence_auto_apply_new_review=eligible_high_confidence_auto_apply_new_review,
        mutation_flag_enabled=False,
        business_mutations=0,
        pipeline_events_appended=0,
        user_fact_mutations=0,
        external_form_mutations=0,
        status=status,
    )
