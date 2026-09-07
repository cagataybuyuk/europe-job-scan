from __future__ import annotations

from dataclasses import dataclass

from ejs.contracts.mw3 import service_canary_idempotency_key
from ejs.domain.append_only import AppendOnlyDomain, AppendOnlyEvent
from ejs.persistence.append_only import AppendOnlyWriteRepository


@dataclass(frozen=True)
class Mw3CanaryReport:
    run_id: str
    appended: bool
    duplicate_suppressed: bool
    reconciliation_gap: int
    business_state_mutations: int
    external_form_mutations: int

    @property
    def passed(self) -> bool:
        return (
            self.reconciliation_gap == 0
            and self.business_state_mutations == 0
            and self.external_form_mutations == 0
        )


class Mw3CanaryService:
    def __init__(self, writer: AppendOnlyWriteRepository) -> None:
        self._writer = writer

    def run(self, run_id: str) -> Mw3CanaryReport:
        event = AppendOnlyEvent(
            domain=AppendOnlyDomain.SERVICE_CANARY_AUDIT,
            idempotency_key=service_canary_idempotency_key(run_id, "service_canary_audit"),
            run_id=run_id,
            payload={
                "wave": "MW-3",
                "mode": "append-only-canary",
                "business_state_write_authority": "NONE",
                "external_form_write_authority": "NONE",
            },
        )
        result = self._writer.append(event)
        return Mw3CanaryReport(
            run_id=run_id,
            appended=result.appended,
            duplicate_suppressed=result.duplicate_suppressed,
            reconciliation_gap=result.reconciliation_gap,
            business_state_mutations=0,
            external_form_mutations=0,
        )
