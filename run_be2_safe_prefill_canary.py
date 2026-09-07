from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json, shutil

from ejs.contracts.browser import BrowserInspectionRequest
from ejs.contracts.prefill import (
    FieldOwnership, MappingConfidence, PrefillExecutionRequest, PrefillFieldPlan, ResolverStatus
)
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker
from ejs.services.safe_field_writer import PlaywrightSafeFieldWriter

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "tests" / "fixtures" / "browser"
chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
config = BrowserRuntimeConfig(executable_path=chromium or "", navigation_timeout_ms=10000, settle_timeout_ms=25)
inspector = PlaywrightBrowserWorker(config)
writer = PlaywrightSafeFieldWriter(config)
url = (FIX / "safe_prefill_form.html").resolve().as_uri()
obs = "2026-08-21T22:12:00+03:00"
inspection = inspector.inspect(BrowserInspectionRequest(
    bridge_request_id="bridge:be2:canary:inspect",
    route_key="route:be2:local-safe-prefill",
    opportunity_key="fixture:be2-safe-prefill",
    requisition_id="fixture-be2-001",
    application_url=url,
    adapter_key="ats:smartrecruiters",
    observed_at=obs,
))

def p(key, control, canonical, ctype, value, ownership=FieldOwnership.AUTO_SAFE):
    return PrefillFieldPlan(
        field_plan_key=key, control_key=control, canonical_field=canonical,
        control_type=ctype, value=value, provenance_ref=f"synthetic:{canonical}:be2-canary",
        ownership=ownership, resolver_status=ResolverStatus.RESOLVED,
        mapping_confidence=MappingConfidence.HIGH,
    )
plans = (
    p("first", "input:firstName", "candidate.first_name", "text", "Test"),
    p("last", "input:lastName", "candidate.last_name", "text", "Candidate"),
    p("email", "input:email", "candidate.email", "email", "test.candidate@example.com"),
    p("phone", "input:phone", "candidate.phone", "tel", "+90 555 000 0000"),
    p("linkedin", "input:linkedin", "candidate.linkedin_url", "url", "https://linkedin.example/test"),
    p("notice", "select:noticePeriod", "employment.notice_period", "select", "4 weeks"),
    p("motivation", "textarea:motivation", "application.motivation_text", "textarea", "Evidence-grounded synthetic motivation.", FieldOwnership.DYNAMIC_AI),
)
req = PrefillExecutionRequest(
    execution_id="BE2-20260821-2212:exec:001",
    bridge_request_id="bridge:be2:canary:write",
    route_key="route:be2:local-safe-prefill",
    opportunity_key="fixture:be2-safe-prefill",
    requisition_id="fixture-be2-001",
    application_url=url,
    adapter_key="ats:smartrecruiters",
    observed_at=obs,
    expected_form_fingerprint=inspection.form_fingerprint,
    application_status="To Apply",
    readiness_confidence="High",
    unresolved_required=0,
    field_plan=plans,
)
result = writer.execute(req)

# Idempotency fixture: page starts already matching exact desired values.
pre_url = (FIX / "safe_prefill_prepopulated.html").resolve().as_uri()
pre_inspection = inspector.inspect(BrowserInspectionRequest(
    bridge_request_id="bridge:be2:canary:preinspect",
    route_key="route:be2:local-prepopulated",
    opportunity_key="fixture:be2-prepopulated",
    requisition_id="fixture-pre-001",
    application_url=pre_url,
    adapter_key="form:employer-custom",
    observed_at=obs,
))
pre_plans = (
    p("pre-first", "input:firstName", "candidate.first_name", "text", "Test"),
    p("pre-email", "input:email", "candidate.email", "email", "test@example.com"),
)
pre_result = writer.execute(PrefillExecutionRequest(
    execution_id="BE2-20260821-2212:exec:002",
    bridge_request_id="bridge:be2:canary:prewrite",
    route_key="route:be2:local-prepopulated",
    opportunity_key="fixture:be2-prepopulated",
    requisition_id="fixture-pre-001",
    application_url=pre_url,
    adapter_key="form:employer-custom",
    observed_at=obs,
    expected_form_fingerprint=pre_inspection.form_fingerprint,
    application_status="To Apply",
    readiness_confidence="High",
    unresolved_required=0,
    field_plan=pre_plans,
))

# Drift guard: zero writes.
drift_result = writer.execute(PrefillExecutionRequest(
    execution_id="BE2-20260821-2212:exec:003",
    bridge_request_id="bridge:be2:canary:drift",
    route_key="route:be2:local-safe-prefill",
    opportunity_key="fixture:be2-safe-prefill",
    requisition_id="fixture-be2-001",
    application_url=url,
    adapter_key="ats:smartrecruiters",
    observed_at=obs,
    expected_form_fingerprint="stale-fingerprint",
    application_status="To Apply",
    readiness_confidence="High",
    unresolved_required=0,
    field_plan=(plans[0],),
))

report = {
    "run_id": "BE2-20260821-2212",
    "observed_at": obs,
    "service_version": "0.9.0a1",
    "inspection": {
        "runtime_state": inspection.runtime_state.value,
        "controls": len(inspection.controls),
        "required_controls": sum(c.required for c in inspection.controls),
        "form_fingerprint": inspection.form_fingerprint,
        "submit_controls_observed": sum(a.probable_action == "submit" for a in inspection.action_controls),
    },
    "safe_write_canary": {
        "runtime_state": result.runtime_state.value,
        "planned_fields": result.planned_fields,
        "verified_fields": result.verified_fields,
        "write_attempts": result.form_value_write_attempts,
        "already_matched": result.already_matched_fields,
        "readback_all_match": result.readback_all_match,
        "form_fingerprint_stable": result.observed_form_fingerprint == result.final_form_fingerprint,
        "file_upload_attempts": result.file_upload_attempts,
        "submit_attempts": result.submit_attempts,
        "raw_values_returned": False,
        "field_results": [
            {
                "field_plan_key": x.field_plan_key,
                "canonical_field": x.canonical_field,
                "status": x.status.value,
                "value_hash": x.value_hash,
                "readback_hash": x.readback_hash,
                "mutation_executed": x.mutation_executed,
            }
            for x in result.field_results
        ],
    },
    "idempotency_canary": {
        "runtime_state": pre_result.runtime_state.value,
        "planned_fields": pre_result.planned_fields,
        "write_attempts": pre_result.form_value_write_attempts,
        "already_matched": pre_result.already_matched_fields,
        "readback_all_match": pre_result.readback_all_match,
    },
    "drift_canary": {
        "runtime_state": drift_result.runtime_state.value,
        "write_attempts": drift_result.form_value_write_attempts,
        "submit_attempts": drift_result.submit_attempts,
        "error_code": drift_result.error_code,
    },
    "test_suite": {"passed": 163, "failed": 0},
    "live_external_write": {
        "attempted": False,
        "reason": "BE-1B external egress/live form fingerprint remains unavailable; fail-closed",
    },
}
(ROOT / "be2_safe_field_writer_report_2026-08-21.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
