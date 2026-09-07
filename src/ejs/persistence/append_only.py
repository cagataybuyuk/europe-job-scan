from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ejs.contracts.mw3 import Mw3Authority
from ejs.domain.append_only import AppendOnlyEvent, AppendResult


class AppendOnlyWriteRepository(Protocol):
    def append(self, event: AppendOnlyEvent) -> AppendResult: ...


@dataclass
class InMemoryAppendOnlyWriteRepository:
    authority: Mw3Authority
    _events: dict[str, AppendOnlyEvent] = field(default_factory=dict)

    def append(self, event: AppendOnlyEvent) -> AppendResult:
        self.authority.validate()
        if not self.authority.enabled:
            raise PermissionError("MW-3 append-only authority is disabled")
        if event.domain not in self.authority.allowed_domains:
            raise PermissionError(f"Append-only domain not released: {event.domain}")
        if event.idempotency_key in self._events:
            return AppendResult(
                idempotency_key=event.idempotency_key,
                appended=False,
                duplicate_suppressed=True,
                reconciliation_gap=0,
            )
        self._events[event.idempotency_key] = event
        return AppendResult(
            idempotency_key=event.idempotency_key,
            appended=True,
            duplicate_suppressed=False,
            reconciliation_gap=0,
        )

    @property
    def events(self) -> tuple[AppendOnlyEvent, ...]:
        return tuple(self._events.values())
