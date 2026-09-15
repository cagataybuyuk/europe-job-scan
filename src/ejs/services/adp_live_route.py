"""Fail-closed routing for read-only ADP Workforce Now inspection evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

ROUTE_VERSION = "adp-live-route-v1"
ROUTES = frozenset({
    "human_handoff",
    "navigation_review_candidate",
    "manifest_review_candidate",
    "diagnostic_review",
})


def _count(report: Mapping[str, Any], key: str) -> int:
    value = report.get(key, 0)
    if type(value) is not int or value < 0:
        raise ValueError(f"INVALID_{key.upper()}")
    return value


def _assert_read_only_report(report: Mapping[str, Any]) -> None:
    if report.get("inspection_only") is not True:
        raise PermissionError("ADP_ROUTE_REQUIRES_INSPECTION_ONLY_REPORT")
    if report.get("live_execution_ready") is not False:
        raise PermissionError("ADP_ROUTE_REQUIRES_EXECUTION_NOT_READY")
    keys = (
        "navigation_click_attempts",
        "credential_entry_attempts",
        "form_value_write_attempts",
        "file_upload_attempts",
        "submit_attempts",
    )
    enabled = [key for key in keys if _count(report, key) != 0]
    if enabled:
        raise PermissionError(f"ADP_ROUTE_REJECTS_SIDE_EFFECT_EVIDENCE: {', '.join(enabled)}")


def _visible_controls(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    form = report.get("form")
    controls = form.get("controls", []) if isinstance(form, Mapping) else []
    if not isinstance(controls, list):
        raise ValueError("INVALID_FORM_CONTROLS")
    return [control for control in controls if isinstance(control, Mapping) and control.get("visible") is True]


def route_adp_live_inspection(report: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise TypeError("inspection report must be a mapping")
    _assert_read_only_report(report)

    runtime_state = str(report.get("runtime_state", "")).strip()
    error_code = str(report.get("error_code", "")).strip()
    captcha_observed = report.get("captcha_observed") is True
    visible_controls = _visible_controls(report)
    entries = report.get("application_entry_actions", [])
    if not isinstance(entries, list):
        raise ValueError("INVALID_APPLICATION_ENTRY_ACTIONS")

    common = {
        "route_version": ROUTE_VERSION,
        "inspection_runtime_state": runtime_state,
        "inspection_error_code": error_code,
        "captcha_observed": captcha_observed,
        "visible_application_control_count": len(visible_controls),
        "automation_resume_allowed": False,
        "navigation_click_allowed": False,
        "safe_fill_allowed": False,
        "final_submit_allowed": False,
    }

    if runtime_state == "captcha_boundary" or error_code == "CAPTCHA_BOUNDARY" or captcha_observed:
        return {
            **common,
            "route": "human_handoff",
            "reason_code": "CAPTCHA_BOUNDARY",
            "human_action_required": True,
            "manifest_review_allowed": False,
            "navigation_review_allowed": False,
            "required_user_action": (
                "Open the ADP application in a trusted interactive browser and complete any site-required "
                "challenge manually. Automation must remain stopped until a fresh read-only inspection no "
                "longer reports a CAPTCHA boundary."
            ),
        }

    if runtime_state == "auth_boundary" or error_code == "ADP_AUTH_BOUNDARY":
        return {
            **common,
            "route": "human_handoff",
            "reason_code": "ADP_AUTH_BOUNDARY",
            "human_action_required": True,
            "manifest_review_allowed": False,
            "navigation_review_allowed": False,
            "required_user_action": (
                "Authentication is required. Do not provide credentials to the automation. Complete the "
                "required sign-in or account step manually, then run a fresh read-only inspection."
            ),
        }

    if visible_controls:
        return {
            **common,
            "route": "manifest_review_candidate",
            "reason_code": "VISIBLE_APPLICATION_CONTROLS_OBSERVED",
            "human_action_required": True,
            "manifest_review_allowed": True,
            "navigation_review_allowed": False,
            "required_user_action": (
                "Review the observed visible ADP application controls and prepare an approved scoped manifest. "
                "Safe-fill remains disabled until explicit promotion."
            ),
        }

    if entries:
        return {
            **common,
            "route": "navigation_review_candidate",
            "reason_code": "APPLICATION_ENTRY_REQUIRES_NAVIGATION",
            "human_action_required": True,
            "manifest_review_allowed": False,
            "navigation_review_allowed": True,
            "required_user_action": (
                "Review the observed Apply entry action and, if approved, prepare a separate one-click "
                "navigation canary. This inspection does not authorize clicking Apply."
            ),
        }

    return {
        **common,
        "route": "diagnostic_review",
        "reason_code": error_code or "FORM_STRUCTURE_NOT_READY",
        "human_action_required": True,
        "manifest_review_allowed": False,
        "navigation_review_allowed": False,
        "required_user_action": (
            "Review the read-only structural diagnostics. Do not authorize navigation or safe-fill until "
            "the application entry or visible application controls are explicitly observed."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Route read-only ADP live inspection evidence")
    parser.add_argument("--input", required=True, dest="input_path")
    parser.add_argument("--output", required=True, dest="output_path")
    args = parser.parse_args()
    report = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
    decision = route_adp_live_inspection(report)
    if decision.get("route") not in ROUTES:
        raise ValueError("UNKNOWN_ADP_LIVE_ROUTE")
    payload = json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2)
    Path(args.output_path).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
