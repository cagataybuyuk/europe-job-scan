"""GD-006 step 3: policy-based submit authority (SUBMIT-1.0 + rollout stage).

Replaces the per-target human grant of the reviewed scope with the owner's
standing policy: a prepared EJS-EXECUTION-MANIFEST-1 that passes the validator
may be dispatched once, under the manifest's rollout stage cap (R4: no cap), when
the local runtime switch is ON and the kill switch is OFF.

This module decides and reserves; it performs no browser, file or network I/O.
The engine adapter (step 4) must still:
  * compare the live form with ``manifest.form_fingerprint`` and release on drift,
  * re-read every Verified Fact from the private User Fact Registry,
  * call ``ledger.mark_started`` immediately BEFORE the click (never after),
  * on CAPTCHA/MFA/login, call ``ledger.await_user`` and wait for the user;
    the engine never solves or bypasses a challenge itself (SUBMIT-1.0 A8),
  * write ``ledger.confirm`` only from exact employer confirmation evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ejs.persistence.policy_submission_ledger import PolicySubmissionLedger
from ejs.persistence.submission_ledger import SubmissionLedgerError
from ejs.services.execution_manifest import ExecutionManifest, load_execution_manifest


class PolicyDecision(str, Enum):
    RESERVED = "RESERVED"                    # dispatch may proceed through the adapter
    KILL_SWITCH = "KILL_SWITCH"              # owner stopped all submissions
    RUNTIME_DISABLED = "RUNTIME_DISABLED"    # final-submit flag OFF on this machine
    MANIFEST_REJECTED = "MANIFEST_REJECTED"  # validator failed closed
    DUPLICATE = "DUPLICATE"                  # key or vacancy already used (any slot)
    CAP_REACHED = "CAP_REACHED"              # rollout stage cap for today reached
    BUSY = "BUSY"                            # another dispatch is in flight
    LEDGER_ERROR = "LEDGER_ERROR"            # storage failed closed


@dataclass(frozen=True)
class RuntimeSwitch:
    """Local, owner-controlled runtime state. Defaults are safe (no submit)."""

    final_submit_enabled: bool = False
    kill_switch_engaged: bool = True


@dataclass(frozen=True)
class AuthorizationResult:
    decision: PolicyDecision
    manifest: ExecutionManifest | None = None

    @property
    def dispatch_allowed(self) -> bool:
        return self.decision is PolicyDecision.RESERVED


def authorize_manifest(ledger: PolicySubmissionLedger, raw: bytes, *, now: datetime,
                       switch: RuntimeSwitch, expected_sha256: str) -> AuthorizationResult:
    """Validate one prepared manifest and reserve its single dispatch."""
    if type(switch) is not RuntimeSwitch or type(switch.kill_switch_engaged) is not bool \
            or type(switch.final_submit_enabled) is not bool:
        return AuthorizationResult(PolicyDecision.RUNTIME_DISABLED)
    if switch.kill_switch_engaged:
        return AuthorizationResult(PolicyDecision.KILL_SWITCH)
    if not switch.final_submit_enabled:
        return AuthorizationResult(PolicyDecision.RUNTIME_DISABLED)
    try:
        manifest = load_execution_manifest(raw, now=now, expected_sha256=expected_sha256)
    except PermissionError:
        return AuthorizationResult(PolicyDecision.MANIFEST_REJECTED)
    if type(ledger) is not PolicySubmissionLedger:
        return AuthorizationResult(PolicyDecision.LEDGER_ERROR, manifest)
    try:
        outcome = ledger.reserve(
            opportunity_key=manifest.opportunity_key, requisition_id=manifest.requisition_id,
            profile_version=manifest.candidate_profile_version, execution_id=manifest.execution_id,
            manifest_sha256=expected_sha256, rollout_stage=manifest.rollout_stage,
            daily_cap=manifest.daily_cap, now=now)
    except SubmissionLedgerError:
        return AuthorizationResult(PolicyDecision.LEDGER_ERROR, manifest)
    return AuthorizationResult(PolicyDecision(outcome), manifest)
