from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import pytest

from ejs.apps.github_runner.safe_fill_upload_canary import _fixture_plans
from ejs.contracts.smartrecruiters_execution import SmartRecruitersExecutionRequest
from ejs.contracts.submit import ApprovedSubmitPolicy, RolloutStage, SubmitAuthority
from ejs.domain.file_upload import FileUploadResultItem, FileUploadStatus
from ejs.domain.prefill import FieldWriteResult, FieldWriteStatus
from ejs.domain.smartrecruiters_execution import SmartRecruitersExecutionResult, SmartRecruitersExecutionState
from ejs.domain.submission import ConfirmationEvidence, ConfirmationType, PreSubmitValidationRequest, ControlResolution, ResolutionMode
from ejs.domain.template_history import DriftState, MappingConfidence
from ejs.persistence.submission_ledger import SqliteSubmissionLedger, SubmissionLedgerError
from ejs.services.durable_submission import DurableSingleSubmission
from ejs.services.submit_policy import validate_pre_submit

NOW = datetime(2026, 11, 5, 7, tzinfo=timezone.utc)
ON = SubmitAuthority(browser_external_form_write=True, final_submit_runtime_flag=True, kill_switch_engaged=False)
CV = Path(__file__).parent / 'fixtures' / 'browser' / 'test_cv.pdf'
URL = 'https://jobs.smartrecruiters.com/Fixture/123456789012345-synthetic'


def receipts(fp='a' * 24):
    fields, uploads, resolutions = _fixture_plans(CV)
    resolutions = (*resolutions, ControlResolution('input:linkedin', 'LinkedIn', False, 'candidate.linkedin_url',
                    MappingConfidence.HIGH, ResolutionMode.VERIFIED_FACT, 'canary:candidate.linkedin_url:v1'))
    request = PreSubmitValidationRequest(
        execution_id='fixture:exec:1', opportunity_key='fixture:role:1', requisition_id='123456789012345',
        candidate_profile_version='fixture-profile-v1', apply_url=URL, ats_family='smartrecruiters',
        form_fingerprint=fp, drift_state=DriftState.MATCH, runtime_inspection_current=True,
        tracker_fingerprint_current=True, controls=resolutions, rollout_stage=RolloutStage.R2_SINGLE_CANARY)
    validation = validate_pre_submit(replace(request, rollout_stage=RolloutStage.R1_PREFILL),
                                     policy=ApprovedSubmitPolicy(), authority=SubmitAuthority())
    field_results = tuple(FieldWriteResult(p.field_plan_key, p.control_key, p.canonical_field, p.control_type,
                         p.value_hash, p.value_hash, FieldWriteStatus.WRITTEN_VERIFIED, True) for p in fields)
    plan = uploads[0]; a = plan.asset
    file_result = FileUploadResultItem(plan.upload_plan_key, 'fixture:upload:cv', plan.control_key,
                    plan.canonical_field, 'cv', a.artifact_version, a.expected_sha256,
                    a.expected_file_name, a.expected_size_bytes, a.expected_mime_type,
                    a.expected_file_name, a.expected_size_bytes, a.expected_mime_type, a.expected_sha256,
                    FileUploadStatus.UPLOADED_VERIFIED, True, True, False)
    prepared = SmartRecruitersExecutionResult(request.execution_id, 'fixture:route', request.opportunity_key,
        request.requisition_id, SmartRecruitersExecutionState.PRE_SUBMIT_READY, URL, URL, 'smartrecruiters', fp, fp,
        sum(c.required for c in resolutions), sum(c.required for c in resolutions), field_results, (file_result,), len(field_results), 1, 0, True, True,
        validation, True)
    return request, prepared


@pytest.fixture
def service(tmp_path):
    return DurableSingleSubmission(SqliteSubmissionLedger(tmp_path / 'private' / 'first.sqlite3'))


def reserve(service, request=None, prepared=None, **kwargs):
    r, p = receipts()
    return service.reserve(request or r, prepared or p, observed_at=NOW, now=NOW, **kwargs)


def test_default_runtime_stays_off_without_consuming_slot(service):
    assert reserve(service)['status'] == 'RUNTIME_DISABLED'
    assert not service.ledger.has_intent()


def test_intent_is_durable_and_consumes_one_global_slot(service):
    assert reserve(service, authority=ON)['status'] == 'INTENT_RECORDED'
    reopened = DurableSingleSubmission(SqliteSubmissionLedger(service.ledger.path))
    assert reserve(reopened, authority=ON)['status'] == 'DUPLICATE_OR_SINGLE_SCOPE_CONSUMED'
    r, p = receipts(); r = replace(r, candidate_profile_version='fixture-v2')
    # Even a fully coherent new profile cannot obtain another slot.
    p = replace(p, validation=replace(p.validation, submit_key=validate_pre_submit(r, policy=ApprovedSubmitPolicy(), authority=ON).submit_key))
    assert reserve(reopened, r, p, authority=ON)['status'] == 'DUPLICATE_OR_SINGLE_SCOPE_CONSUMED'


@pytest.mark.parametrize('age', [None, -1, 121])
def test_missing_future_and_stale_observations_do_not_record(service, age):
    r, p = receipts()
    stamp = None if age is None else NOW - timedelta(seconds=age)
    with pytest.raises(PermissionError): service.reserve(r, p, observed_at=stamp, now=NOW, authority=ON)
    assert not service.ledger.has_intent()


@pytest.mark.parametrize('mutation', ['target', 'execution', 'form', 'profile', 'missing_cv', 'cv_hash', 'field_hash',
                                       'submit_count', 'invalid_form', 'unknown_drift', 'empty_controls', 'false_flag'])
def test_unproven_or_drifted_receipt_stops_before_intent(service, mutation):
    r, p = receipts()
    if mutation == 'target': p = replace(p, final_url=URL + '-other')
    if mutation == 'execution': p = replace(p, execution_id='fixture:other')
    if mutation == 'form': p = replace(p, final_form_fingerprint='b' * 24)
    if mutation == 'profile': r = replace(r, candidate_profile_version='other-profile')
    if mutation == 'missing_cv': p = replace(p, file_results=())
    if mutation == 'cv_hash': p = replace(p, file_results=(replace(p.file_results[0], readback_sha256='b' * 64),))
    if mutation == 'field_hash': p = replace(p, field_results=(replace(p.field_results[0], readback_hash='b' * 24), *p.field_results[1:]))
    if mutation == 'submit_count': p = replace(p, submit_attempts=1)
    if mutation == 'invalid_form': p = replace(p, browser_form_valid=False)
    if mutation == 'unknown_drift': r = replace(r, drift_state=DriftState.NO_BASELINE)
    if mutation == 'empty_controls': r = replace(r, controls=())
    if mutation == 'false_flag': r = replace(r, runtime_inspection_current='true')
    with pytest.raises(PermissionError): reserve(service, r, p, authority=ON)
    assert not service.ledger.has_intent()


def test_string_authority_does_not_enable_runtime(service):
    with pytest.raises(PermissionError): reserve(service, authority=replace(ON, final_submit_runtime_flag='true'))
    assert not service.ledger.has_intent()


def test_before_dispatch_rechecks_authority_and_commits_uncertainty(service):
    r, p = receipts(); reserve(service, authority=ON)
    with pytest.raises(PermissionError): service.record_before_dispatch(r, p, observed_at=NOW, now=NOW)
    assert service.ledger.snapshot()['status'] == 'Validated'
    out = service.record_before_dispatch(r, p, observed_at=NOW, now=NOW, authority=ON)
    assert out['status'] == 'DISPATCH_INTENT_RECORDED'
    assert out['network_action_performed'] is out['applications_write_performed'] is False
    with pytest.raises(PermissionError): service.record_before_dispatch(r, p, observed_at=NOW, now=NOW, authority=ON)
    assert reserve(service, authority=ON)['status'] == 'DUPLICATE_OR_SINGLE_SCOPE_CONSUMED'


@pytest.mark.parametrize('changed', ['form', 'cv', 'field'])
def test_intent_cannot_be_rebound_to_other_readback(service, changed):
    r, p = receipts(); reserve(service, authority=ON)
    if changed == 'form': r, p = receipts('b' * 24)
    if changed == 'cv':
        p = replace(p, file_results=(replace(p.file_results[0], content_hash='b' * 64, readback_sha256='b' * 64),))
    if changed == 'field':
        p = replace(p, field_results=(replace(p.field_results[0], value_hash='b' * 24, readback_hash='b' * 24), *p.field_results[1:]))
    with pytest.raises(PermissionError, match='INTENT_BINDING_MISMATCH'):
        service.record_before_dispatch(r, p, observed_at=NOW, now=NOW, authority=ON)
    assert service.ledger.snapshot()['status'] == 'Validated'


def test_confirmation_is_distinct_from_intent_and_does_not_write_applications(service):
    r, p = receipts(); key = p.validation.submit_key
    evidence = ConfirmationEvidence(key, ConfirmationType.CONFIRMATION_PAGE,
                 'evidence:sha256:' + 'c' * 64, NOW.isoformat(), True, r.execution_id, r.requisition_id)
    reserve(service, authority=ON)
    assert service.reconcile(key, evidence, now=NOW)['status'] == 'CONFIRMATION_REJECTED'
    service.record_before_dispatch(r, p, observed_at=NOW, now=NOW, authority=ON)
    assert service.reconcile(key, replace(evidence, exact_identity_match='true'), now=NOW)['status'] == 'CONFIRMATION_REJECTED'
    out = service.reconcile(key, evidence, now=NOW)
    assert out['status'] == 'CONFIRMATION_RECORDED'
    assert out['source_authentication_performed'] is out['applications_write_performed'] is False
    assert service.ledger.snapshot()['status'] == 'Confirmed'


def test_reports_and_database_contain_no_answer_values_or_cv_names(service):
    result = reserve(service, authority=ON)
    data = service.ledger.path.read_bytes()
    for private in ('Canary', 'canary@example.test', CV.name, 'TEST CANARY ONLY'):
        assert private.encode() not in data and private not in str(result)


@pytest.mark.parametrize('mutation', ['mismatch', 'unmapped', 'wrong_canonical', 'bad_hash'])
def test_optional_readbacks_require_same_strict_evidence(service, mutation):
    r, p = receipts()
    index = next(i for i, x in enumerate(p.field_results) if x.canonical_field == 'candidate.linkedin_url')
    item = p.field_results[index]
    if mutation == 'mismatch': item = replace(item, status=FieldWriteStatus.READBACK_MISMATCH)
    if mutation == 'unmapped': item = replace(item, control_key='input:unreviewed')
    if mutation == 'wrong_canonical': item = replace(item, canonical_field='work.authorization')
    if mutation == 'bad_hash': item = replace(item, value_hash='not-a-hash', readback_hash='not-a-hash')
    rows = list(p.field_results); rows[index] = item; p = replace(p, field_results=tuple(rows))
    with pytest.raises(PermissionError): reserve(service, r, p, authority=ON)
    assert not service.ledger.has_intent()


@pytest.mark.parametrize('url', ['file:///fixture.html', 'http://127.0.0.1/application',
                                  'https://user@jobs.smartrecruiters.com/Fixture/123456789012345-role',
                                  URL + '?token=private', URL + '#form',
                                  'https://jobs.smartrecruiters.com/Fixture/anything',
                                  URL.replace('.com/', '.com:444/')])
def test_fixture_or_unreviewed_target_cannot_consume_first_live_intent(service, url):
    r, p = receipts()
    r = replace(r, apply_url=url); p = replace(p, requested_url=url, final_url=url)
    with pytest.raises(PermissionError, match='LIVE_TARGET_REQUIRED'):
        reserve(service, r, p, authority=ON)
    assert not service.ledger.has_intent()
