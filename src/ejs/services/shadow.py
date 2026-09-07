from __future__ import annotations

from ejs.domain.models import ShadowRunResult
from ejs.persistence.ports import WorkspaceReadRepository


class ShadowRunService:
    def __init__(self, reader: WorkspaceReadRepository) -> None:
        self._reader = reader

    def run(self, run_id: str) -> ShadowRunResult:
        snapshot = self._reader.read_snapshot()
        return ShadowRunResult(
            run_id=run_id,
            application_count=len(snapshot.applications),
            active_rule_count=sum(1 for rule in snapshot.rules if rule.get("status") == "Active"),
            write_attempts=0,
            parity_notes=("read-only shadow execution",),
        )
