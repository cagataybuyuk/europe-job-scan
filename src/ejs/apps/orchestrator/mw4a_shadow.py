from __future__ import annotations

from dataclasses import asdict
from ejs.domain.derived_state import ReadinessProjectionInput
from ejs.services.derived_state import derive_readiness, derive_user_action


def run_mw4a_shadow(queue_rows: list[dict], actual_uac: dict[tuple[str, str], dict]) -> dict:
    projections = []
    critical_divergences = 0
    queue_parity = 0
    uac_inclusion_parity = 0
    uac_category_parity = 0
    write_candidates = 0

    for raw in queue_rows:
        inp = ReadinessProjectionInput(**raw)
        readiness = derive_readiness(inp)
        action = derive_user_action(readiness)
        key = (inp.company, inp.role)
        actual = actual_uac.get(key)
        actual_included = actual is not None
        inclusion_parity = action.include == actual_included
        category_parity = True
        if action.include and actual:
            category_parity = (
                action.category == actual.get("category")
                and action.priority == actual.get("priority")
                and action.can_submit_now == actual.get("can_submit_now")
                and action.batch_group == actual.get("batch_group")
            )
        if readiness.status_parity:
            queue_parity += 1
        if inclusion_parity:
            uac_inclusion_parity += 1
        if category_parity:
            uac_category_parity += 1
        if readiness.write_eligible:
            write_candidates += 1
        if readiness.critical_divergence or not inclusion_parity or not category_parity:
            critical_divergences += 1
        projections.append({
            "company": inp.company,
            "role": inp.role,
            "current_status": inp.current_fill_pack_status,
            "derived_status": readiness.derived_status.value,
            "status_parity": readiness.status_parity,
            "confidence": readiness.confidence,
            "mw4b_write_candidate": readiness.write_eligible,
            "uac_expected": action.include,
            "uac_actual": actual_included,
            "uac_inclusion_parity": inclusion_parity,
            "expected_category": action.category,
            "actual_category": actual.get("category") if actual else None,
            "expected_priority": action.priority,
            "actual_priority": actual.get("priority") if actual else None,
            "uac_category_parity": category_parity,
            "critical_divergence": readiness.critical_divergence or not inclusion_parity or not category_parity,
            "reason": readiness.reason,
        })

    return {
        "queue_rows": len(queue_rows),
        "queue_status_parity": queue_parity,
        "uac_inclusion_parity": uac_inclusion_parity,
        "uac_category_parity": uac_category_parity,
        "critical_divergences": critical_divergences,
        "mw4b_write_candidates": write_candidates,
        "business_state_mutations": 0,
        "derived_state_mutations": 0,
        "external_form_mutations": 0,
        "projections": projections,
    }
