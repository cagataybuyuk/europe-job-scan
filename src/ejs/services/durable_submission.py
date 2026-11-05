"""Durable intent/reconciliation boundary; this service performs NO network action.

The trusted caller must validate the pinned source/facts/CV, vacancy suitability,
endpoint policy and human gates before supplying runtime authority. Default
runtime authority remains OFF. A confirmation input must come from a separately
trusted employer observer; matching metadata here does not authenticate its source.
"""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit

from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, DYNAMIC_AI_FIELDS
from ejs.contracts.submit import ApprovedSubmitPolicy, RolloutStage, SubmitAuthority, submit_identity
from ejs.domain.file_upload import FileUploadStatus, FileUploadResultItem
from ejs.domain.prefill import FieldWriteStatus, FieldWriteResult
from ejs.domain.smartrecruiters_execution import SmartRecruitersExecutionResult, SmartRecruitersExecutionState
from ejs.domain.submission import PreSubmitValidationRequest, SubmissionAttemptStatus, PreSubmitValidationResult
from ejs.domain.template_history import DriftState
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.submit_policy import create_submission_attempt, validate_pre_submit


def _require(condition: bool, code: str = 'SUBMISSION_READBACK_REQUIRED') -> None:
    if not condition:
        raise PermissionError(code)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _now(value: datetime | None) -> datetime:
    value = datetime.now(timezone.utc) if value is None else value
    _require(isinstance(value, datetime) and value.tzinfo is not None, 'SUBMISSION_UTC_TIME_REQUIRED')
    return value.astimezone(timezone.utc)


def _report(status: str) -> dict:
    return {'status': status, 'network_action_performed': False, 'applications_write_performed': False,
            'source_authentication_performed': False, 'browser_submit_authorized_by_this_service': False}


class DurableSingleSubmission:
    """One canonical private ledger, one irreversible intent budget.

    No submit callback, arbitrary code, browser/page or transport is accepted.
    The ledger path MUST be pinned by trusted bootstrap, never derived from a
    job/profile/worktree or supplied in an ATS/AI manifest to evade the slot.
    """
    def __init__(self, ledger: SqliteSubmissionLedger):
        self.ledger = ledger

    def _validate(self, *args, **kwargs):
        try:
            return self._validate_receipt(*args, **kwargs)
        except PermissionError:
            raise
        except Exception:
            raise PermissionError('SUBMISSION_READBACK_REQUIRED') from None

    def _validate_receipt(self, request: PreSubmitValidationRequest, prepared: SmartRecruitersExecutionResult,
                  observed_at: datetime, now: datetime, authority: SubmitAuthority,
                  policy: ApprovedSubmitPolicy):
        _require(isinstance(request, PreSubmitValidationRequest) and
                 isinstance(prepared, SmartRecruitersExecutionResult))
        _require(isinstance(authority, SubmitAuthority) and isinstance(policy, ApprovedSubmitPolicy),
                 'SUBMISSION_AUTHORITY_REQUIRED')
        for obj in (authority, policy):
            for attr in fields(obj):
                if type(attr.default) is bool:
                    _require(type(getattr(obj, attr.name)) is bool, 'SUBMISSION_AUTHORITY_REQUIRED')
        _require(isinstance(observed_at, datetime), 'SUBMISSION_FRESH_REVIEW_REQUIRED')
        observed_at = _now(observed_at)
        _require(0 <= (now - observed_at).total_seconds() <= 120, 'SUBMISSION_FRESH_REVIEW_REQUIRED')
        _require(request.rollout_stage is RolloutStage.R2_SINGLE_CANARY and request.drift_state is DriftState.MATCH)
        _require(request.runtime_inspection_current is True and request.tracker_fingerprint_current is True)
        _require(isinstance(request.controls, tuple) and 0 < len(request.controls) <= 100)
        _require(all(type(c.required) is bool for c in request.controls))
        _require(type(request.submissions_today) is int and request.submissions_today == 0)
        _require(isinstance(request.form_fingerprint, str) and bool(re.fullmatch('[a-f0-9]{24}', request.form_fingerprint)))
        _require(prepared.runtime_state is SmartRecruitersExecutionState.PRE_SUBMIT_READY and not prepared.error_code)
        _require(all(type(x) is int and 0 <= x <= 100 for x in (
            prepared.submit_attempts, prepared.form_value_write_attempts, prepared.file_upload_attempts,
            prepared.required_controls_observed, prepared.resolved_required_controls)))
        _require(prepared.submit_attempts == 0)
        _require(prepared.browser_form_valid is True and prepared.submit_control_observed is True
                 and prepared.review_gate_reached is True)
        _require(prepared.execution_id == request.execution_id and prepared.opportunity_key == request.opportunity_key
                 and prepared.requisition_id == request.requisition_id)
        _require(prepared.requested_url == request.apply_url == prepared.final_url)
        target = urlsplit(request.apply_url)
        _require(target.scheme == 'https' and target.hostname == 'jobs.smartrecruiters.com'
                 and target.port in (None, 443) and not target.username and not target.password
                 and not target.query and not target.fragment
                 and bool(re.fullmatch(r'/[A-Za-z0-9][A-Za-z0-9_-]{0,99}/[0-9]{8,20}(?:-[A-Za-z0-9_-]{1,200})?', target.path)),
                 'SUBMISSION_LIVE_TARGET_REQUIRED')
        _require(prepared.observed_form_fingerprint == request.form_fingerprint == prepared.final_form_fingerprint)
        _require(prepared.ats_family.casefold() == request.ats_family.casefold() == 'smartrecruiters')
        expected_key = submit_identity(request.opportunity_key, request.requisition_id, request.candidate_profile_version)
        _require(prepared.validation is not None and prepared.validation.policy_eligible is True
                 and prepared.validation.submit_key == expected_key
                 and prepared.validation.artifact_ready is True and prepared.validation.provenance_complete is True)
        _require(isinstance(prepared.validation, PreSubmitValidationResult) and not prepared.validation.blockers)
        _require(isinstance(prepared.field_results, tuple) and len(prepared.field_results) <= 100
                 and all(isinstance(x, FieldWriteResult) and isinstance(x.status, FieldWriteStatus) for x in prepared.field_results))
        _require(isinstance(prepared.file_results, tuple)
                 and all(isinstance(x, FileUploadResultItem) and isinstance(x.status, FileUploadStatus) for x in prepared.file_results))
        required = [c for c in request.controls if c.required]
        _require(len(required) > 0 and prepared.required_controls_observed == len(required)
                 and prepared.resolved_required_controls == len(required))
        controls_by_key = {c.control_key: c for c in request.controls}
        _require(len(controls_by_key) == len(request.controls))
        for item in (*prepared.field_results, *prepared.file_results):
            mapped = controls_by_key.get(item.control_key)
            _require(mapped is not None and item.canonical_field == mapped.canonical_field)
        for item in prepared.field_results:
            _require(item.canonical_field in SAFE_VERIFIED_FIELDS | DYNAMIC_AI_FIELDS
                     and item.status in (FieldWriteStatus.WRITTEN_VERIFIED, FieldWriteStatus.ALREADY_MATCHED)
                     and bool(re.fullmatch('[a-f0-9]{24}', item.value_hash))
                     and item.value_hash == item.readback_hash
                     and ((item.status is FieldWriteStatus.WRITTEN_VERIFIED) == item.mutation_executed))
        field_map = {x.control_key: x for x in prepared.field_results}
        file_map = {x.control_key: x for x in prepared.file_results}
        _require(len(field_map) == len(prepared.field_results) and len(file_map) == len(prepared.file_results)
                 and not (field_map.keys() & file_map.keys()))
        _require(all(type(x.mutation_executed) is bool for x in prepared.field_results)
                 and prepared.form_value_write_attempts == sum(x.mutation_executed for x in prepared.field_results))
        _require(len(prepared.file_results) == 1)
        asset = prepared.file_results[0]
        _require(type(asset.upload_executed) is bool
                 and prepared.file_upload_attempts == int(asset.upload_executed)
                 and ((asset.status is FileUploadStatus.UPLOADED_VERIFIED) == asset.upload_executed))
        _require(asset.canonical_field == 'document.cv' and asset.artifact_type == 'cv'
                 and asset.status in (FileUploadStatus.UPLOADED_VERIFIED, FileUploadStatus.ALREADY_ATTACHED))
        _require(bool(re.fullmatch('[a-f0-9]{64}', asset.content_hash))
                 and asset.readback_sha256 == asset.content_hash
                 and type(asset.expected_size_bytes) is int and 0 < asset.expected_size_bytes <= 10 * 1024 * 1024
                 and asset.readback_size_bytes == asset.expected_size_bytes
                 and asset.expected_mime_type == asset.readback_mime_type == 'application/pdf'
                 and asset.expected_file_name == asset.readback_file_name)
        _require(any(c.canonical_field == 'document.cv' for c in required))
        for control in required:
            result = file_map.get(control.control_key) if control.canonical_field == 'document.cv' else field_map.get(control.control_key)
            _require(result is not None and result.canonical_field == control.canonical_field)
            if control.canonical_field != 'document.cv':
                _require(result.status in (FieldWriteStatus.WRITTEN_VERIFIED, FieldWriteStatus.ALREADY_MATCHED)
                         and bool(re.fullmatch('[a-f0-9]{24}', result.value_hash)) and result.value_hash == result.readback_hash)
        _require(len({c.control_key for c in request.controls}) == len(request.controls))
        return validate_pre_submit(request, policy=policy, authority=authority)

    @staticmethod
    def _proof_key(request, prepared, validation) -> str:
        # Freeze readback content as well as semantic form identity. A changed CV
        # or answer under the same profile/form version cannot replace the intent.
        asset = prepared.file_results[0]
        payload = {
            'validation': validation.validation_key, 'submit': validation.submit_key,
            'target': _hash(prepared.final_url), 'form': prepared.final_form_fingerprint,
            'fields': sorted((x.control_key, x.canonical_field, x.value_hash) for x in prepared.field_results),
            'cv': (asset.content_hash, asset.artifact_version, asset.expected_size_bytes,
                   asset.expected_mime_type, _hash(asset.expected_file_name)),
            'sources': sorted((c.control_key, c.canonical_field, c.provenance_ref, c.policy_ref, c.artifact_ref)
                              for c in request.controls),
        }
        return 'intent-proof:' + _hash(json.dumps(payload, sort_keys=True, separators=(',', ':')))

    def reserve(self, request, prepared, *, observed_at: datetime,
                authority: SubmitAuthority | None = None, policy: ApprovedSubmitPolicy | None = None,
                now: datetime | None = None) -> dict:
        now = _now(now)
        authority, policy = authority or SubmitAuthority(), policy or ApprovedSubmitPolicy()
        validation = self._validate(request, prepared, observed_at, now, authority, policy)
        if not validation.execution_allowed:
            return _report('RUNTIME_DISABLED' if validation.policy_eligible else 'VALIDATION_BLOCKED')
        if self.ledger.has_intent():
            return _report('DUPLICATE_OR_SINGLE_SCOPE_CONSUMED')
        attempt = create_submission_attempt(request, validation, requested_at=now.isoformat())
        attempt = replace(attempt, validation_key=self._proof_key(request, prepared, validation))
        claimed = self.ledger.reserve_intent(attempt, now)
        return _report('INTENT_RECORDED' if claimed else 'DUPLICATE_OR_SINGLE_SCOPE_CONSUMED')

    def record_before_dispatch(self, request, prepared, *, observed_at: datetime,
                               authority: SubmitAuthority | None = None, policy: ApprovedSubmitPolicy | None = None,
                               now: datetime | None = None) -> dict:
        now = _now(now)
        authority, policy = authority or SubmitAuthority(), policy or ApprovedSubmitPolicy()
        validation = self._validate(request, prepared, observed_at, now, authority, policy)
        _require(validation.execution_allowed, 'SUBMISSION_RUNTIME_DISABLED')
        state = self.ledger.snapshot()
        _require(state is not None and state['status'] == SubmissionAttemptStatus.VALIDATED.value
                 and state['submit_key_digest'] == _hash(validation.submit_key)
                 and state['execution_digest'] == _hash(request.execution_id)
                 and state['validation_digest'] == _hash(self._proof_key(request, prepared, validation)), 'SUBMISSION_INTENT_BINDING_MISMATCH')
        # Commit before the trusted driver is allowed to attempt an effect. If the
        # process dies afterward, uncertainty consumes the slot; never retry.
        self.ledger.mark_started(validation.submit_key, request.execution_id, now)
        return _report('DISPATCH_INTENT_RECORDED')

    def reconcile(self, submit_key, evidence, *, now: datetime | None = None) -> dict:
        recorded = self.ledger.confirm(submit_key, evidence, _now(now))
        return _report('CONFIRMATION_RECORDED' if recorded else 'CONFIRMATION_REJECTED')
