from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CANARY_APPROVAL_PHRASE = "APPROVE-TEST-SAFE-FILL-UPLOAD-CANARY"
R2_APPROVAL_PHRASE = "APPROVE-R2-SINGLE-SUBMIT-CANARY"


@dataclass(frozen=True)
class CanaryEvidence:
    source_sha: str
    target_mode: str
    runtime_state: str
    form_value_write_attempts: int
    file_upload_attempts: int
    submit_attempts: int
    observed_form_fingerprint: str
    final_form_fingerprint: str
    review_gate_reached: bool
    approved_asset: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CanaryEvidence":
        required = (
            "source_sha", "target_mode", "runtime_state", "form_value_write_attempts",
            "file_upload_attempts", "submit_attempts", "observed_form_fingerprint",
            "final_form_fingerprint", "review_gate_reached", "approved_asset",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"canary evidence missing fields: {', '.join(missing)}")
        return cls(
            source_sha=str(data["source_sha"]),
            target_mode=str(data["target_mode"]),
            runtime_state=str(data["runtime_state"]),
            form_value_write_attempts=int(data["form_value_write_attempts"]),
            file_upload_attempts=int(data["file_upload_attempts"]),
            submit_attempts=int(data["submit_attempts"]),
            observed_form_fingerprint=str(data["observed_form_fingerprint"]),
            final_form_fingerprint=str(data["final_form_fingerprint"]),
            review_gate_reached=bool(data["review_gate_reached"]),
            approved_asset=bool(data["approved_asset"]),
        )


def validate_canary_approval(*, phrase: str, source_sha: str, expected_sha: str, target_mode: str) -> None:
    if phrase != CANARY_APPROVAL_PHRASE:
        raise PermissionError("safe-fill/upload canary requires the exact approval phrase")
    if source_sha != expected_sha:
        raise ValueError("canary source SHA does not match the checked-out immutable SHA")
    if target_mode not in {"fixture", "live"}:
        raise ValueError("target_mode must be fixture or live")
    if target_mode == "live" and not source_sha:
        raise ValueError("live canary requires an immutable source SHA")


def validate_r2_release_gate(
    evidence: CanaryEvidence,
    *,
    expected_sha: str,
    approval_phrase: str,
    second_approver: str,
) -> dict[str, Any]:
    blockers: list[str] = []
    if evidence.source_sha != expected_sha:
        blockers.append("safe-fill/upload evidence SHA differs from requested SHA")
    if evidence.target_mode != "live":
        blockers.append("R2 requires a live canary evidence record")
    if evidence.runtime_state != "pre_submit_ready":
        blockers.append("canary did not reach pre_submit_ready")
    if evidence.form_value_write_attempts < 1:
        blockers.append("safe-fill write evidence is missing")
    if evidence.file_upload_attempts != 1:
        blockers.append("exactly one CV upload evidence is required")
    if evidence.submit_attempts != 0:
        blockers.append("canary evidence contains a submit attempt")
    if not evidence.observed_form_fingerprint or evidence.observed_form_fingerprint != evidence.final_form_fingerprint:
        blockers.append("form fingerprint readback is not stable")
    if not evidence.review_gate_reached:
        blockers.append("human review gate was not reached")
    if not evidence.approved_asset:
        blockers.append("CV asset was not explicitly approved")
    if approval_phrase != R2_APPROVAL_PHRASE:
        blockers.append("R2 approval phrase is missing")
    if not second_approver.strip():
        blockers.append("second approver identity is missing")
    if blockers:
        raise PermissionError("R2 submit release blocked: " + "; ".join(blockers))
    return {
        "release": "R2_SINGLE_SUBMIT_CANARY",
        "source_sha": expected_sha,
        "final_submit_runtime_flag": False,
        "kill_switch_engaged": True,
        "status": "READY_FOR_MANUAL_ENVIRONMENT_REVIEW",
        "second_approver": second_approver.strip(),
    }
