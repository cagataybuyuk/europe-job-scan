from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any


@dataclass(frozen=True)
class ReadFenceReport:
    expected: Mapping[str, Any]
    observed: Mapping[str, Any]

    @property
    def passed(self) -> bool:
        return dict(self.expected) == dict(self.observed)

    @property
    def differences(self) -> dict[str, tuple[Any, Any]]:
        keys = set(self.expected) | set(self.observed)
        return {
            key: (self.expected.get(key), self.observed.get(key))
            for key in sorted(keys)
            if self.expected.get(key) != self.observed.get(key)
        }
