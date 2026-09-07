from __future__ import annotations

from typing import Protocol

from ejs.domain.models import WorkspaceSnapshot


class WorkspaceReadRepository(Protocol):
    def read_snapshot(self) -> WorkspaceSnapshot: ...


class ProductionWriteRepository(Protocol):
    def write(self, *args, **kwargs) -> None: ...
