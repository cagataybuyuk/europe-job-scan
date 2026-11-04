from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ejs.services.live_canary_manifest import validate_reviewed_manifest, load_reviewed_manifest
from ejs.apps.github_runner.safe_fill_upload_canary import _manifest_request, run

NOW = datetime(2026, 11, 4, 18, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
SHA = 'a' * 40
URL = 'https://jobs.smartrecruiters.com/Example/123-role'


def inputs():
    field = dict(field_plan_key='first', control_key='input:first', canonical_field='candidate.first_name',
                 control_type='text', value='Synthetic', provenance_ref='fact:first:v1', ownership='AUTO_SAFE',
                 resolver_status='Resolved', mapping_confidence='High', review_required=False, required=True)
    upload = dict(upload_plan_key='cv', control_key='input:cv', canonical_field='document.cv',
                  artifact_type='cv', artifact_version='v1', source_ref='fixture:cv', asset_ref='fixture:cv',
                  expected_file_name='test.pdf', expected_mime_type='application/pdf',
                  expected_sha256='b' * 64, expected_size_bytes=100, approved=True,
                  mapping_confidence='High', required=True, provenance_ref='fixture:cv')
    data = dict(schema_version='EJS-LIVE-PREPARE-1', source_sha=SHA, target_url=URL,
                expected_form_fingerprint='c' * 24, candidate_profile_version='fixture-v1', reviewed_at=STAMP,
                observed_at=STAMP, reviewer_ref='review:independent:1', opportunity_key='fixture:1', requisition_id='123',
                field_plan=[field], upload_plan=[upload], control_resolutions=[
                    dict(control_key='input:first', canonical_field='candidate.first_name', provenance_ref='fact:first:v1',
                         mapping_confidence='High', required=True, resolution_mode='Verified Fact'),
                    dict(control_key='input:cv', canonical_field='document.cv', mapping_confidence='High', required=True,
                         resolution_mode='Approved Artifact', artifact_ref='fixture:cv')])
    facts = dict(schema_version='EJS-VERIFIED-FACTS-1', candidate_profile_version='fixture-v1', facts=[
        dict(canonical_field='candidate.first_name', value='Synthetic', source_ref='fact:first:v1',
             verified=True, auto_fill_approved=True, verified_at=STAMP)])
    return data, facts


def validate(data, facts):
    validate_reviewed_manifest(data, facts, source_sha=SHA, target_url=URL, now=NOW)


def test_reviewed_private_manifest_resolves_exact_verified_facts():
    validate(*inputs())


@pytest.mark.parametrize('key', ['target_url', 'source_sha', 'expected_form_fingerprint', 'reviewer_ref', 'reviewed_at'])
def test_missing_review_binding_never_defaults(key):
    data, facts = inputs()
    del data[key]
    with pytest.raises(PermissionError, match='LIVE_MANIFEST_REVIEW_REQUIRED'):
        validate(data, facts)


@pytest.mark.parametrize('value', ['false', 'true', 1, 0, None])
def test_approval_requires_literal_true(value):
    data, facts = inputs()
    data['upload_plan'][0]['approved'] = value
    with pytest.raises(PermissionError):
        validate(data, facts)


@pytest.mark.parametrize('key', ['review_required', 'required'])
def test_field_booleans_do_not_coerce_strings(key):
    data, facts = inputs()
    data['field_plan'][0][key] = 'false'
    with pytest.raises(PermissionError):
        validate(data, facts)


@pytest.mark.parametrize('change', ['wrong_value', 'missing', 'duplicate', 'stale', 'future', 'unverified', 'unapproved', 'source', 'legal', 'consent'])
def test_provenance_and_unknown_answers_fail_closed(change):
    data, facts = inputs()
    fact = facts['facts'][0]
    if change == 'wrong_value': fact['value'] = 'DIFFERENT PRIVATE VALUE'
    if change == 'missing': facts['facts'] = []
    if change == 'duplicate': facts['facts'].append(deepcopy(fact))
    if change == 'stale': fact['verified_at'] = (NOW - timedelta(days=31)).isoformat()
    if change == 'future': fact['verified_at'] = (NOW + timedelta(seconds=1)).isoformat()
    if change == 'unverified': fact['verified'] = False
    if change == 'unapproved': fact['auto_fill_approved'] = False
    if change == 'source': fact['source_ref'] = 'different:source'
    if change in ('legal', 'consent'):
        canonical = 'work.authorization' if change == 'legal' else 'consent.privacy'
        fact['canonical_field'] = canonical
        data['field_plan'][0]['canonical_field'] = canonical
        data['control_resolutions'][0]['canonical_field'] = canonical
    with pytest.raises(PermissionError) as exc:
        validate(data, facts)
    assert str(exc.value) == 'LIVE_MANIFEST_REVIEW_REQUIRED'
    assert 'PRIVATE' not in str(exc.value)


def test_duplicate_controls_and_unmapped_resolutions_block():
    data, facts = inputs()
    data['field_plan'].append(deepcopy(data['field_plan'][0]))
    with pytest.raises(PermissionError): validate(data, facts)
    data, facts = inputs()
    data['control_resolutions'].pop()
    with pytest.raises(PermissionError): validate(data, facts)


@pytest.mark.parametrize('target', [URL.replace('https:', 'http:'), URL.replace('jobs.', 'jobs.evil.'),
                                     'https://user@jobs.smartrecruiters.com/Example/123', URL + '#other'])
def test_only_bound_official_https_target(target):
    data, facts = inputs()
    data['target_url'] = target
    with pytest.raises(PermissionError):
        validate_reviewed_manifest(data, facts, source_sha=SHA, target_url=target, now=NOW)


def pinned_files(tmp_path):
    data, facts = inputs()
    # Input timestamps remain current for load_reviewed_manifest's real clock.
    stamp = datetime.now(timezone.utc).isoformat()
    data['reviewed_at'] = data['observed_at'] = facts['facts'][0]['verified_at'] = stamp
    mp, fp = tmp_path / 'manifest.json', tmp_path / 'facts.json'
    mp.write_text(json.dumps(data)); fp.write_text(json.dumps(facts))
    return mp, fp, hashlib.sha256(mp.read_bytes()).hexdigest(), hashlib.sha256(fp.read_bytes()).hexdigest()


def test_pins_come_from_separate_review_and_tampering_rejected(tmp_path):
    mp, fp, md, fd = pinned_files(tmp_path)
    assert load_reviewed_manifest(mp, facts_path=fp, manifest_sha256=md, facts_sha256=fd,
                                  source_sha=SHA, target_url=URL)['target_url'] == URL
    fp.write_text(fp.read_text() + ' ')
    with pytest.raises(PermissionError):
        load_reviewed_manifest(mp, facts_path=fp, manifest_sha256=md, facts_sha256=fd,
                               source_sha=SHA, target_url=URL)


def test_reviewed_fingerprint_never_replaced_by_current_inspection(tmp_path):
    mp, fp, md, fd = pinned_files(tmp_path)
    with pytest.raises(PermissionError, match='REVIEWED_FORM_FINGERPRINT_MISMATCH'):
        _manifest_request(mp, cv_path=tmp_path / 'test.pdf', target_url=URL, source_sha=SHA,
                          run_id='fixture', inspected_fp='d' * 24, facts_path=fp,
                          manifest_sha256=md, facts_sha256=fd)


def test_cli_checks_actual_checkout_before_any_browser():
    from argparse import Namespace
    from ejs.apps.github_runner.canary_policy import CANARY_APPROVAL_PHRASE
    args = Namespace(source_sha=SHA, approval_phrase=CANARY_APPROVAL_PHRASE, target_mode='fixture')
    with patch('ejs.apps.github_runner.safe_fill_upload_canary.subprocess.check_output', return_value='f' * 40), \
         patch('ejs.apps.github_runner.safe_fill_upload_canary.PlaywrightBrowserWorker') as worker:
        with pytest.raises(ValueError, match='checked-out'):
            run(args)
        worker.assert_not_called()


@pytest.mark.parametrize('key,value', [('canonical_field', 'candidate.first_name'), ('required', False),
                                       ('resolution_mode', 'Verified Fact'), ('artifact_ref', 'other:asset')])
def test_upload_resolution_must_match_approved_asset(key, value):
    data, facts = inputs()
    data['control_resolutions'][1][key] = value
    with pytest.raises(PermissionError): validate(data, facts)


def test_dirty_live_checkout_stops_before_any_browser():
    from argparse import Namespace
    from ejs.apps.github_runner.canary_policy import CANARY_APPROVAL_PHRASE
    args = Namespace(source_sha=SHA, approval_phrase=CANARY_APPROVAL_PHRASE, target_mode='live')
    with patch('ejs.apps.github_runner.safe_fill_upload_canary.subprocess.check_output', side_effect=[SHA, ' M source.py']), \
         patch('ejs.apps.github_runner.safe_fill_upload_canary.PlaywrightBrowserWorker') as worker, \
         patch('ejs.apps.github_runner.safe_fill_upload_canary.PlaywrightSmartRecruitersExecutor') as executor:
        with pytest.raises(PermissionError, match='CLEAN_CHECKOUT'): run(args)
        worker.assert_not_called(); executor.assert_not_called()


def test_live_cli_never_opens_unfenced_inspector(tmp_path, capsys):
    from argparse import Namespace
    from types import SimpleNamespace
    from ejs.apps.github_runner.canary_policy import CANARY_APPROVAL_PHRASE
    mp, fp, md, fd = pinned_files(tmp_path)
    cv = tmp_path / 'test.pdf'; cv.write_bytes(b'%PDF- synthetic fixture')
    args = Namespace(source_sha=SHA, approval_phrase=CANARY_APPROVAL_PHRASE, target_mode='live', target_url=URL,
                     manifest=str(mp), facts=str(fp), manifest_sha256=md, facts_sha256=fd,
                     cv_path=str(cv), executable_path='', run_id='fixture')
    result = SimpleNamespace(runtime_state=SimpleNamespace(value='boundary'), form_value_write_attempts=0,
                             file_upload_attempts=0, submit_attempts=0, observed_form_fingerprint='',
                             final_form_fingerprint='', review_gate_reached=False, file_results=[], error_code='PREPARE_NETWORK_BLOCKED')
    with patch('ejs.apps.github_runner.safe_fill_upload_canary.subprocess.check_output', side_effect=[SHA, '']), \
         patch('ejs.apps.github_runner.safe_fill_upload_canary.PlaywrightBrowserWorker') as worker, \
         patch('ejs.apps.github_runner.safe_fill_upload_canary.PlaywrightSmartRecruitersExecutor') as executor:
        executor.return_value.execute.return_value = result
        assert run(args) == 2
        worker.assert_not_called()
        request = executor.return_value.execute.call_args.args[0]
        assert request.expected_form_fingerprint == 'c' * 24
        assert request.requisition_id == '123'
    output = capsys.readouterr().out
    assert json.loads(output)['approved_asset'] is False
    assert 'Synthetic' not in output and str(tmp_path) not in output


def test_positive_manifest_constructs_valid_request_with_verified_pdf(tmp_path):
    from ejs.services.file_assets import verify_asset
    mp, fp, md, fd = pinned_files(tmp_path)
    cv = Path(__file__).parent / 'fixtures' / 'browser' / 'test_cv.pdf'
    data = json.loads(mp.read_text())
    asset = data['upload_plan'][0]
    asset['expected_file_name'] = cv.name
    asset['expected_size_bytes'] = cv.stat().st_size
    asset['expected_sha256'] = hashlib.sha256(cv.read_bytes()).hexdigest()
    mp.write_text(json.dumps(data)); md = hashlib.sha256(mp.read_bytes()).hexdigest()
    request = _manifest_request(mp, cv_path=cv, target_url=URL, source_sha=SHA, run_id='fixture',
                                inspected_fp='c' * 24, facts_path=fp, manifest_sha256=md, facts_sha256=fd)
    request.validate()
    assert request.control_resolutions[1].resolution_mode.value == 'Approved Artifact'
    assert request.control_resolutions[1].artifact_ref == request.upload_plan[0].asset.asset_ref
    assert verify_asset(request.upload_plan[0].asset).mime_type == 'application/pdf'
