from __future__ import annotations

from dataclasses import replace

from ejs.contracts.submit import (
    ApprovedSubmitPolicy,
    ROLLOUT_DAILY_CAPS,
    RolloutStage,
    SubmitAuthority,
    submit_identity,
    validation_identity,
)
from ejs.domain.submission import (
    ConfirmationEvidence,
    ControlResolution,
    PreSubmitValidationRequest,
    PreSubmitValidationResult,
    ResolutionMode,
    SubmissionAttempt,
    SubmissionAttemptStatus,
    ValidationDecision,
)
from ejs.domain.template_history import DriftState, MappingConfidence


WORK_RIGHT_FIELDS = {"work.authorization", "work.requires_sponsorship"}
SALARY_FIELDS = {"compensation.salary_expectation"}
PRIVACY_FIELDS = {"consent.privacy_acknowledgement"}
MARKETING_FIELDS = {"consent.marketing_opt_in"}
DEMOGRAPHIC_PREFIX = "demographic."
NARRATIVE_FIELDS = {"application.motivation_text", "document.cover_letter"}
DOCUMENT_FIELDS = {"document.cv", "document.cover_letter"}
TECHNICAL_FIELDS = {"technical.captcha"}


def _field_blocker(control: ControlResolution, policy: ApprovedSubmitPolicy) -> str | None:
    field = control.canonical_field

    if control.required and not control.mapped:
        return f"unknown required control: {control.label}"
    if control.required and control.mapping_confidence is not MappingConfidence.HIGH:
        return f"required control mapping not High-confidence: {control.label}"
    if control.required and control.resolution_mode is ResolutionMode.UNRESOLVED:
        return f"required control unresolved: {control.label}"

    if field in TECHNICAL_FIELDS:
        return f"technical challenge blocks unattended submit: {control.label}"

    if field in WORK_RIGHT_FIELDS:
        if not policy.work_right_from_verified_facts_only:
            return f"work-right policy disabled: {control.label}"
        if not control.explicit_user_fact or control.resolution_mode is not ResolutionMode.VERIFIED_FACT:
            return f"work-right/sponsorship requires explicit verified fact: {control.label}"
        if not control.provenance_ref:
            return f"work-right/sponsorship provenance missing: {control.label}"

    if field in SALARY_FIELDS:
        if policy.salary_requires_preapproved_policy:
            if control.resolution_mode is not ResolutionMode.APPROVED_POLICY or not control.policy_ref:
                return f"salary requires pre-approved country/role policy: {control.label}"

    if field in PRIVACY_FIELDS and control.required:
        if not policy.required_privacy_ack_allowed_when_policy_covered:
            return f"privacy acknowledgement policy missing: {control.label}"
        if control.resolution_mode is not ResolutionMode.APPROVED_POLICY or not control.policy_ref:
            return f"required privacy acknowledgement not policy-covered: {control.label}"

    if field in MARKETING_FIELDS:
        if not policy.optional_marketing_default_no:
            return f"marketing consent default policy missing: {control.label}"
        if control.required:
            if control.resolution_mode is not ResolutionMode.DEFAULT_NO or not control.option_available:
                return f"required marketing choice cannot safely apply Default No: {control.label}"
        elif control.resolution_mode not in {ResolutionMode.DEFAULT_NO, ResolutionMode.BLANK_OPTIONAL}:
            return f"optional marketing consent must be No or blank: {control.label}"

    if field.startswith(DEMOGRAPHIC_PREFIX):
        if control.required:
            if not policy.mandatory_demographic_neutral_choice_only:
                return f"mandatory demographic policy missing: {control.label}"
            if control.resolution_mode is not ResolutionMode.NEUTRAL_CHOICE or not control.option_available:
                return f"mandatory demographic requires approved neutral choice: {control.label}"
        elif control.resolution_mode is not ResolutionMode.BLANK_OPTIONAL:
            return f"optional demographic must remain blank: {control.label}"

    if field in NARRATIVE_FIELDS and control.resolution_mode is ResolutionMode.GROUNDED_GENERATED:
        if not policy.grounded_ai_narrative_allowed or not control.grounded:
            return f"generated narrative is not grounded/approved: {control.label}"
        if not control.provenance_ref:
            return f"generated narrative provenance missing: {control.label}"

    if field in DOCUMENT_FIELDS and control.required:
        if control.resolution_mode is not ResolutionMode.ARTIFACT or not control.artifact_ref:
            return f"required document artifact missing: {control.label}"

    if control.required and not control.has_provenance:
        return f"required answer provenance missing: {control.label}"

    return None


def validate_pre_submit(
    request: PreSubmitValidationRequest,
    *,
    policy: ApprovedSubmitPolicy,
    authority: SubmitAuthority,
) -> PreSubmitValidationResult:
    policy.validate()
    authority.validate_policy_boundary()

    blockers: list[str] = []
    warnings: list[str] = []

    identity_components = (
        request.execution_id,
        request.opportunity_key,
        request.requisition_id,
        request.candidate_profile_version,
        request.apply_url,
        request.form_fingerprint,
    )
    if not all(v.strip() for v in identity_components):
        blockers.append("exact execution/opportunity/requisition/form identity is incomplete")

    submit_key = ""
    validation_key = ""
    if all(v.strip() for v in (request.opportunity_key, request.requisition_id, request.candidate_profile_version)):
        submit_key = submit_identity(
            request.opportunity_key, request.requisition_id, request.candidate_profile_version
        )
    if request.execution_id.strip() and request.form_fingerprint.strip():
        validation_key = validation_identity(
            request.execution_id, request.form_fingerprint, request.policy_version
        )

    if request.policy_version != policy.version:
        blockers.append("policy version mismatch")
    if request.drift_state is DriftState.DRIFT:
        blockers.append("live form drift detected; re-inspection required")
    if not request.runtime_inspection_current:
        blockers.append("runtime form inspection is stale")
    if not request.tracker_fingerprint_current:
        blockers.append("tracker/opportunity fingerprint is stale")
    if request.technical_challenge.strip():
        blockers.append(f"technical challenge: {request.technical_challenge.strip()}")

    duplicate = bool(submit_key and submit_key in request.existing_submit_keys)
    if duplicate:
        blockers.append("deterministic submit key already exists")

    required = [c for c in request.controls if c.required]
    for control in request.controls:
        blocker = _field_blocker(control, policy)
        if blocker:
            blockers.append(blocker)

    resolved_required = sum(
        1
        for c in required
        if c.mapped
        and c.mapping_confidence is MappingConfidence.HIGH
        and c.resolution_mode is not ResolutionMode.UNRESOLVED
        and _field_blocker(c, policy) is None
    )
    provenance_complete = all((not c.required) or c.has_provenance for c in request.controls)
    artifact_ready = all(
        (not c.required)
        or c.canonical_field not in DOCUMENT_FIELDS
        or bool(c.artifact_ref)
        for c in request.controls
    )

    cap = ROLLOUT_DAILY_CAPS[request.rollout_stage]
    if request.submissions_today < 0:
        blockers.append("submissions_today cannot be negative")
    cap_reached = request.submissions_today >= cap if cap >= 0 else False
    if cap == 0:
        warnings.append(f"rollout stage {request.rollout_stage.value} has submit authority OFF")
    elif cap_reached:
        blockers.append(f"daily submission cap reached ({cap})")

    policy_eligible = not blockers
    runtime_enabled_stage = request.rollout_stage not in {RolloutStage.R0_SHADOW, RolloutStage.R1_PREFILL}
    execution_allowed = policy_eligible and runtime_enabled_stage and authority.submit_click_allowed

    if duplicate:
        decision = ValidationDecision.DUPLICATE_SUPPRESSED
    elif blockers:
        decision = ValidationDecision.BLOCKED
    elif not execution_allowed:
        decision = ValidationDecision.RUNTIME_DISABLED
    else:
        decision = ValidationDecision.ELIGIBLE

    return PreSubmitValidationResult(
        validation_key=validation_key,
        submit_key=submit_key,
        decision=decision,
        policy_eligible=policy_eligible,
        execution_allowed=execution_allowed,
        blockers=tuple(dict.fromkeys(blockers)),
        warnings=tuple(dict.fromkeys(warnings)),
        required_controls=len(required),
        resolved_required_controls=resolved_required,
        provenance_complete=provenance_complete,
        artifact_ready=artifact_ready,
        duplicate_state="duplicate_suppressed" if duplicate else "unique",
        daily_cap_state=f"{request.submissions_today}/{cap}",
    )


def create_submission_attempt(
    request: PreSubmitValidationRequest,
    validation: PreSubmitValidationResult,
    *,
    requested_at: str,
) -> SubmissionAttempt:
    if not validation.execution_allowed or validation.decision is not ValidationDecision.ELIGIBLE:
        raise PermissionError("submission attempt cannot be created unless validation is execution-eligible")
    return SubmissionAttempt(
        submit_key=validation.submit_key,
        execution_id=request.execution_id,
        opportunity_key=request.opportunity_key,
        requisition_id=request.requisition_id,
        candidate_profile_version=request.candidate_profile_version,
        policy_version=request.policy_version,
        validation_key=validation.validation_key,
        requested_at=requested_at,
        status=SubmissionAttemptStatus.VALIDATED,
        ats_family=request.ats_family,
        form_fingerprint=request.form_fingerprint,
    )


def applied_transition_allowed(
    attempt: SubmissionAttempt,
    evidence: ConfirmationEvidence | None,
) -> tuple[bool, str]:
    if attempt.status not in {
        SubmissionAttemptStatus.SUBMIT_CLICKED,
        SubmissionAttemptStatus.SUBMITTED_UNCONFIRMED,
        SubmissionAttemptStatus.CONFIRMED,
    }:
        return False, "no submitted attempt state"
    if evidence is None:
        return False, "confirmation evidence is required"
    if evidence.submit_key != attempt.submit_key:
        return False, "confirmation submit key mismatch"
    if not evidence.exact_identity_match:
        return False, "confirmation identity is not exact"
    if not evidence.evidence_ref.strip():
        return False, "confirmation evidence reference missing"
    if evidence.execution_id and evidence.execution_id != attempt.execution_id:
        return False, "confirmation execution mismatch"
    if evidence.requisition_id and evidence.requisition_id != attempt.requisition_id:
        return False, "confirmation requisition mismatch"
    return True, "confirmation evidence permits Applied reconciliation"


def mark_attempt_confirmed(attempt: SubmissionAttempt, evidence: ConfirmationEvidence) -> SubmissionAttempt:
    allowed, reason = applied_transition_allowed(attempt, evidence)
    if not allowed:
        raise PermissionError(reason)
    return replace(
        attempt,
        status=SubmissionAttemptStatus.CONFIRMED,
        evidence_ref=evidence.evidence_ref,
    )
