from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


INITIAL_ALLOWED_TRANSITIONS = frozenset(
    {
        ("To Apply", "Applied", "ST-01"),
        ("Applied", "Rejected", "ST-07"),
        ("Recruiter Contacted", "Rejected", "ST-07"),
        ("Screening", "Rejected", "ST-07"),
        ("Assessment", "Rejected", "ST-07"),
        ("Interview 1", "Rejected", "ST-07"),
        ("Interview 2", "Rejected", "ST-07"),
        ("Final Interview", "Rejected", "ST-07"),
    }
)


def _key_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def opportunity_key(company: str, role: str, country: str = "") -> str:
    parts = [_key_part(company), _key_part(role)]
    if country:
        parts.append(_key_part(country))
    return ":".join(parts)


def gmail_outcome_key(message_id: str) -> str:
    if not message_id.strip():
        raise ValueError("message_id is required")
    return f"gmail:{message_id.strip()}"


def pipeline_event_key(
    opportunity_key_value: str,
    from_state: str,
    to_state: str,
    effective_date: str,
    evidence_ref: str,
) -> str:
    if not all(v.strip() for v in (opportunity_key_value, from_state, to_state, effective_date, evidence_ref)):
        raise ValueError("all pipeline event key components are required")
    return (
        f"pipe:{opportunity_key_value}:{_key_part(from_state)}:{_key_part(to_state)}:"
        f"{effective_date}:{_key_part(evidence_ref)}"
    )


def opportunity_fingerprint(
    *,
    status: str,
    applied_date: str,
    last_update: str,
    verification_status: str,
    url_status: str,
) -> str:
    payload = "|".join(
        [
            status.strip(),
            applied_date.strip(),
            last_update.strip(),
            verification_status.strip(),
            url_status.strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Mw5MutationAuthority:
    """Initial MW-5 authority boundary.

    Business mutation is separately feature-flagged. The contract itself only permits
    the first canary to exercise ST-01 / ST-07. Everything user-owned or terminal-reopen
    remains unavailable.
    """

    mutation_flag_enabled: bool = False
    allowed_transitions: frozenset[tuple[str, str, str]] = INITIAL_ALLOWED_TRANSITIONS
    applications_write_enabled: bool = True
    pipeline_history_append_enabled: bool = True
    outcome_event_append_enabled: bool = True
    form_fill_queue_derived_reconcile_enabled: bool = True
    user_action_derived_reconcile_enabled: bool = True
    user_fact_writes_enabled: bool = False
    external_form_writes_enabled: bool = False
    terminal_reopen_enabled: bool = False
    final_submit_enabled: bool = False

    def validate(self) -> None:
        if self.user_fact_writes_enabled:
            raise PermissionError("MW-5 initial canary forbids User Fact writes")
        if self.external_form_writes_enabled:
            raise PermissionError("MW-5 initial canary forbids external-form writes")
        if self.terminal_reopen_enabled:
            raise PermissionError("MW-5 initial canary forbids terminal reopen")
        if self.final_submit_enabled:
            raise PermissionError("MW-5 initial canary never owns final submit")
        if not self.applications_write_enabled:
            raise PermissionError("guarded canary requires explicit Applications authority capability")
        if not self.pipeline_history_append_enabled:
            raise PermissionError("guarded canary requires Pipeline History audit authority")
        if not self.outcome_event_append_enabled:
            raise PermissionError("guarded canary requires Outcome Event audit authority")
        if self.allowed_transitions - INITIAL_ALLOWED_TRANSITIONS:
            raise PermissionError("MW-5 initial canary transition allowlist expanded beyond contract")

    def transition_allowed(self, from_state: str, to_state: str, state_rule: str) -> bool:
        return (from_state, to_state, state_rule) in self.allowed_transitions
