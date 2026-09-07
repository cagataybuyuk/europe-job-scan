from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UserActionRow:
    priority: str
    action_category: str
    company: str
    role: str
    pipeline_status: str
    readiness_decision: str
    required_user_action: str
    due_date: str | None
    ordinary_facts: int
    review_approval: int
    legal_consent: int
    technical_upload: int
    assigned_cv: str | None
    application_url: str | None
    can_submit_now: str
    batch_group: str
    last_updated: str | None
    notes: str | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.company, self.role)
