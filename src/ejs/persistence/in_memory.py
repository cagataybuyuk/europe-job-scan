from __future__ import annotations

from dataclasses import dataclass

from ejs.domain.models import WorkspaceSnapshot


@dataclass
class InMemoryWorkspaceReadRepository:
    snapshot: WorkspaceSnapshot

    def read_snapshot(self) -> WorkspaceSnapshot:
        return self.snapshot


class DisabledProductionWriteRepository:
    def write(self, *args, **kwargs) -> None:
        raise PermissionError("Production write repository is disabled in MW-0/MW-1")
