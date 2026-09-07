from __future__ import annotations

from dataclasses import dataclass

from ejs.domain.mutation import MutationPlan


APPLICATION_COLUMN_MAP = {
    "status": "J",
    "applied_date": "K",
    "next_action": "P",
    "last_update": "X",
    "rejection_reason": "Y",
}

ST01_ALLOWED_FIELDS = frozenset({"status", "applied_date", "next_action", "last_update"})
ST07_ALLOWED_FIELDS = frozenset({"status", "last_update", "rejection_reason"})


@dataclass(frozen=True)
class CellPatch:
    column: str
    row: int
    value: str

    @property
    def a1(self) -> str:
        return f"{self.column}{self.row}"


def allowed_fields_for_rule(state_rule: str | None) -> frozenset[str]:
    if state_rule == "ST-01":
        return ST01_ALLOWED_FIELDS
    if state_rule == "ST-07":
        return ST07_ALLOWED_FIELDS
    return frozenset()


def validate_application_patch(plan: MutationPlan) -> None:
    allowed = allowed_fields_for_rule(plan.state_rule)
    if not allowed:
        raise PermissionError(f"state rule {plan.state_rule!r} has no MW-5 Applications patch authority")
    unexpected = set(plan.target_values) - allowed
    if unexpected:
        raise PermissionError(f"MW-5 patch attempts unauthorized fields: {sorted(unexpected)}")

    if plan.state_rule == "ST-01":
        required = {"status", "applied_date", "last_update"}
    else:
        required = {"status", "last_update", "rejection_reason"}
    missing = required - set(plan.target_values)
    if missing:
        raise ValueError(f"MW-5 patch missing required fields: {sorted(missing)}")


def build_application_cell_patches(*, row_number: int, plan: MutationPlan) -> tuple[CellPatch, ...]:
    if row_number < 2:
        raise ValueError("Applications mutation cannot target the header row")
    validate_application_patch(plan)
    ordered_fields = ["status", "applied_date", "next_action", "last_update", "rejection_reason"]
    return tuple(
        CellPatch(APPLICATION_COLUMN_MAP[field], row_number, plan.target_values[field])
        for field in ordered_fields
        if field in plan.target_values
    )
