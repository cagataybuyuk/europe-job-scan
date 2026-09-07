from __future__ import annotations

from ejs.contracts.prefill import (
    DYNAMIC_AI_FIELDS,
    HARD_FORBIDDEN_FIELDS,
    HARD_FORBIDDEN_PREFIXES,
    SAFE_CONTROL_TYPES,
    SAFE_VERIFIED_FIELDS,
    FieldOwnership,
    MappingConfidence,
    PrefillFieldPlan,
    ResolverStatus,
)


def field_plan_blocker(plan: PrefillFieldPlan) -> str:
    """Return empty string only for fields BE-2 may write."""
    plan.validate()
    field = plan.canonical_field.strip()
    control_type = plan.control_type.strip().lower()

    if field in HARD_FORBIDDEN_FIELDS or field.startswith(HARD_FORBIDDEN_PREFIXES):
        return f"forbidden canonical field: {field}"
    if control_type not in SAFE_CONTROL_TYPES:
        return f"forbidden control type: {control_type}"
    if plan.mapping_confidence is not MappingConfidence.HIGH:
        return f"mapping confidence must be High: {field}"
    if plan.review_required:
        return f"review-required field is not BE-2 writable: {field}"
    if plan.resolver_status not in {ResolverStatus.RESOLVED, ResolverStatus.OPTIONAL_RESOLVED}:
        return f"resolver status is not auto-writable: {field}"

    if plan.ownership is FieldOwnership.AUTO_SAFE:
        if field not in SAFE_VERIFIED_FIELDS:
            return f"AUTO_SAFE field not in BE-2 allowlist: {field}"
        return ""

    if plan.ownership is FieldOwnership.DYNAMIC_AI:
        if field not in DYNAMIC_AI_FIELDS:
            return f"DYNAMIC_AI field not in BE-2 allowlist: {field}"
        return ""

    return f"ownership is not auto-writable: {plan.ownership.value}"


def validate_prefill_plan(plans: tuple[PrefillFieldPlan, ...]) -> tuple[str, ...]:
    blockers: list[str] = []
    for plan in plans:
        blocker = field_plan_blocker(plan)
        if blocker:
            blockers.append(f"{plan.field_plan_key}: {blocker}")
    return tuple(blockers)
