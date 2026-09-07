from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContractFreeze:
    freeze_id: str
    active_rule_bundle: str
    active_contract_count: int
    regression_fixture_count: int
    write_authority: str
    browser_execution_enabled: bool


@dataclass(frozen=True)
class WorkspaceSnapshot:
    source_id: str
    captured_at: str
    config: dict[str, Any] = field(default_factory=dict)
    applications: tuple[dict[str, Any], ...] = ()
    rules: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ShadowRunResult:
    run_id: str
    application_count: int
    active_rule_count: int
    write_attempts: int
    parity_notes: tuple[str, ...] = ()
