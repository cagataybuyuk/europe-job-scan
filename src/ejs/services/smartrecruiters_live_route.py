"""Pure routing for read-only SmartRecruiters live inspection evidence.

This module does not open a browser and cannot mutate an application. It only
turns a previously recorded live-inspection report into a fail-closed next-step
decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

ROUTE_VERSION = "smartrecruiters-live-route-v1"
ROUTES = frozenset({"human_handoff", "manifest_review_candidate", "diagnostic_review"})


def _count(report: Mapping[str, Any], key: str) -> int:
    value = report.get(key, 0)
    if type(value) is not int or value < 0:
        raise ValueError(f"INVALID_{key.upper()}")
    return value


def _assert_read_only_report(report: Mapping[str, Any]) -> None:
    if report.get("inspection_only") is not True:
        raise PermissionError("LIVE_ROUTE_REQUIRES_INSPECTION_ONLY_REPORT")
    if report.get("live_execution_ready") is not False:
        raise PermissionError("LIVE_ROUTE_REQUIRES_EXECUTION_NOT_READY")
    forbidden = {
        "form_value_write_attempts": _count(report, "form_value_write_attempts"),
        "file_upload_attempts": _count(report, "file_upload_attempts"),
        "submit_attempts": _count(report, "submit_attempts"),
    }
    enabled = [name for name, value in forbidden.items() if value != 0]
    if enabled:
        raise PermissionError(f"LIVE_ROUTE_REJECTS_SIDE_EFFECT_EVIDENCE: {', '.join(enabled)}")


def route_live_inspection(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return a machine-readable, fail-closed next-step decision."""
    if not isinstance(report, Mapping):
        raise TypeError("inspection report must be a mapping")
    _assert_read_only_report(report)

    runtime_state = str(report.get("runtime_state", "")).strip()
    error_code = str(report.get("error_code", "")).strip()
    captcha_observed = report.get("captcha_observed") is True
    form = report.get("form")
    controls = form.get("controls", []) if isinstance(form, Mapping) else []
    if not isinstance(controls, list):
        raise ValueError("INVALID_FORM_CONTROLS")

    common = {
        "route_version": ROUTE_VERSION,
        "inspection_runtime_state": runtime_state,
        "inspection_error_code": error_code,
        "captcha_observed": captcha_observed,
        "automation_resume_allowed": False,
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
            "required_user_action": (
                "Open the application in a trusted interactive browser and complete the site-required "
                "challenge manually. Do not resume automated safe-fill until a fresh read-only inspection "
                "no longer reports a CAPTCHA boundary."
            ),
        }

    if runtime_state == "inspected" and controls:
        return {
            **common,
            "route": "manifest_review_candidate",
            "reason_code": "INSPECTABLE_CONTROLS_OBSERVED",
            "human_action_required": True,
            "manifest_review_allowed": True,
            "required_user_action": (
                "Review the observed control identities and prepare an approved scoped manifest. "
                "Safe-fill remains disabled until that manifest is reviewed and explicitly promoted."
            ),
        }

    return {
        **common,
        "route": "diagnostic_review",
        "reason_code": error_code or "FORM_STRUCTURE_NOT_READY",
        "human_action_required": True,
        "manifest_review_allowed": False,
        "required_user_action": (
            "Review the read-only structural diagnostics. Do not create a live safe-fill manifest until "
            "inspectable controls are observed without a challenge boundary."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Route a read-only SmartRecruiters live inspection")
    parser.add_argument("--input", required=True, dest="input_path")
    parser.add_argument("--output", required=True, dest="output_path")
    args = parser.parse_args()

    report = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
    decision = route_live_inspection(report)
    if decision.get("route") not in ROUTES:
        raise ValueError("UNKNOWN_LIVE_ROUTE")
    payload = json.dumps(decision, ensure_ascii=False, sort_keys=True, indent=2)
    Path(args.output_path).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
