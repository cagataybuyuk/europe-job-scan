from __future__ import annotations

import json
from pathlib import Path

from ejs.contracts.browser import BrowserInspectionRequest, BrowserWorkerAuthority
from ejs.services.browser_worker import BrowserRuntimeConfig, PlaywrightBrowserWorker

ROOT = Path(__file__).resolve().parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "browser"
REPORT = ROOT / "be1_browser_worker_report_2026-08-21.json"


def slim(result):
    return {
        "runtime_state": result.runtime_state.value,
        "final_url": result.final_url,
        "ats_family": result.ats_family,
        "page_title": result.page_title,
        "page_fingerprint": result.page_fingerprint,
        "form_fingerprint": result.form_fingerprint,
        "controls": len(result.controls),
        "required_controls": sum(1 for c in result.controls if c.required),
        "action_controls": len(result.action_controls),
        "submit_controls_observed": sum(1 for a in result.action_controls if a.probable_action == "submit"),
        "captcha_state": result.captcha_state.value,
        "auth_boundary_type": result.auth_boundary_type.value,
        "read_only_invariant_ok": result.read_only_invariant_ok,
        "mutation_attempts": result.mutation_attempts,
        "file_upload_attempts": result.file_upload_attempts,
        "submit_attempts": result.submit_attempts,
        "error_code": result.error_code,
        "error_message": result.error_message[:500],
        "browser_engine": result.browser_engine,
        "browser_version": result.browser_version,
    }


def main():
    worker = PlaywrightBrowserWorker(BrowserRuntimeConfig(navigation_timeout_ms=12_000, settle_timeout_ms=100))
    authority = BrowserWorkerAuthority()

    local_req = BrowserInspectionRequest(
        bridge_request_id="bridge:be1:local-smartrecruiters:001",
        route_key="route:be1:local-smartrecruiters",
        opportunity_key="fixture:smartrecruiters-business-analyst",
        requisition_id="fixture-001",
        application_url=(FIXTURE_DIR / "smartrecruiters_like_form.html").resolve().as_uri(),
        adapter_key="ats:smartrecruiters",
        observed_at="2026-08-21T17:24:00+03:00",
    )
    local_result = worker.inspect(local_req, authority=authority)

    live_req = BrowserInspectionRequest(
        bridge_request_id="bridge:route:rituals:workforce-management:be1-001",
        route_key="route:rituals:business-analyst-workforce-management",
        opportunity_key="rituals:business-analyst-workforce-management",
        requisition_id="744000112936917",
        application_url="https://jobs.smartrecruiters.com/Rituals1/744000112936917-business-analyst-workforce-management",
        adapter_key="ats:smartrecruiters",
        observed_at="2026-08-21T17:24:00+03:00",
    )
    live_result = worker.inspect(live_req, authority=authority)

    payload = {
        "run_id": "BE1-20260821-1724",
        "worker_version": "BE-1.0",
        "bridge_contract": "BROWSER-BRIDGE-0.1",
        "mode": "inspect_read_only",
        "local_fixture": slim(local_result),
        "live_target": slim(live_result),
        "live_target_url": live_req.application_url,
        "live_target_tracker_state": "To Apply / URL Valid at 2026-08-21 pre-read",
        "production_form_mutations": 0,
        "production_file_uploads": 0,
        "production_submit_clicks": 0,
        "acceptance": {
            "chromium_launch": bool(local_result.browser_version),
            "js_dom_extraction": local_result.runtime_state.value == "rendered" and len(local_result.controls) > 0,
            "deterministic_fingerprint": bool(local_result.form_fingerprint and local_result.page_fingerprint),
            "submit_metadata_observed_without_click": any(a.probable_action == "submit" for a in local_result.action_controls) and local_result.submit_attempts == 0,
            "read_only_invariant": local_result.read_only_invariant_ok and live_result.read_only_invariant_ok,
            "live_external_egress": live_result.runtime_state.value == "rendered",
        },
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
