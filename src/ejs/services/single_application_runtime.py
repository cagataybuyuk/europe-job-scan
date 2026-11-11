"""One reviewed SmartRecruiters application, from preparation to confirmation.

This local lane does not change the existing prepare-only/CI lanes. Inputs are
independently pinned, the browser owns the observations, and the immutable ledger
is committed before the one external submission. No inference/API service is used.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Protocol
from urllib.request import Request, build_opener, HTTPRedirectHandler

from ejs.contracts.submit import ApprovedSubmitPolicy, RolloutStage, SubmitAuthority
from ejs.domain.submission import ControlResolution, ConfirmationEvidence, ConfirmationType, PreSubmitValidationRequest, ResolutionMode
from ejs.domain.template_history import DriftState, MappingConfidence
from ejs.persistence.submission_ledger import SqliteSubmissionLedger
from ejs.services.cv_asset_gate import evaluate_cv_asset
from ejs.services.oneclick_target import OneClickTarget
from ejs.services.submit_policy import create_submission_attempt, validate_pre_submit
from ejs.services.vacancy_evidence_gate import VacancyEvidence, SponsorshipReviewPolicy, evaluate_vacancy_evidence


def require(condition: bool, code: str = 'APPLICATION_REVIEW_REQUIRED') -> None:
    if not condition:
        raise PermissionError(code)


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def fresh(value: str, now: datetime, seconds: int) -> None:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(stamp.tzinfo is not None and 0 <= (now - stamp).total_seconds() <= seconds)
    except Exception:
        raise PermissionError('APPLICATION_FRESH_REVIEW_REQUIRED') from None


def read_approved_cv(path: Path, approval: dict) -> bytes:
    """Verify the bytes actually passed to the browser, without a path TOCTOU."""
    fd = None
    try:
        for component in (path.parent, *path.parent.parents):
            require(not component.is_symlink())
        require(stat.S_IMODE(path.parent.stat().st_mode) == 0o700)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
        require(type(approval.get('size_bytes')) is int and 0 < approval['size_bytes'] <= 10 * 1024 * 1024)
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            payload = stream.read(10 * 1024 * 1024 + 1)
        actual = {'sha256': hashlib.sha256(payload).hexdigest(), 'size_bytes': len(payload),
                  'mime_type': 'application/pdf', 'approved_filename': path.name}
        require(payload.startswith(b'%PDF-') and evaluate_cv_asset(actual, approval)['status'] == 'METADATA_MATCH')
        return payload
    except Exception:
        raise PermissionError('APPLICATION_CV_REVIEW_REQUIRED') from None
    finally:
        if fd is not None:
            os.close(fd)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_posting(target: OneClickTarget) -> bytes:
    """Only public employer discovery, with no cookie, auth, redirect or mutation."""
    url = f'https://api.smartrecruiters.com/v1/companies/{target.company}/postings/{target.posting_id}'
    try:
        with build_opener(_NoRedirect).open(Request(url, headers={'Accept': 'application/json'}), timeout=20) as response:
            require(response.status == 200)
            payload = response.read(1_000_001)
        require(len(payload) <= 1_000_000)
        return payload
    except Exception:
        raise PermissionError('APPLICATION_POSTING_UNVERIFIED') from None


def verify_posting(target: OneClickTarget, manifest: dict, facts: dict, payload: bytes, now: datetime) -> None:
    try:
        review = manifest['vacancy']
        fresh(review['reviewed_at'], now, 24 * 3600)
        require(hashlib.sha256(payload).hexdigest() == review['posting_sha256'])
        data = json.loads(payload)
        require(data['active'] is True and data['id'] == target.posting_id
                and data['uuid'] == target.publication_id and data['refNumber'] == target.requisition_id
                and data['company']['identifier'] == target.company)
        by_key = {f['canonical_field']: f for f in facts['facts']}
        has_rights, needs_sponsor = by_key['work.authorization']['value'], by_key['work.requires_sponsorship']['value']
        require(type(has_rights) is bool and type(needs_sponsor) is bool)
        for key in ('role_fit_verified', 'mandatory_requirements_verified', 'explicit_no_sponsorship', 'existing_work_rights_required'):
            require(type(review[key]) is bool)
        require(review.get('sponsorship_supported') is None or type(review['sponsorship_supported']) is bool)
        evidence = VacancyEvidence(now, True, target.posting_url, review['role_fit_verified'],
            review['mandatory_requirements_verified'], True, review.get('sponsorship_supported'),
            review['explicit_no_sponsorship'], review['existing_work_rights_required'], has_rights, needs_sponsor)
        policy = SponsorshipReviewPolicy(target.posting_url, review['sponsorship_decision_ref'],
                                        allow_unknown_sponsorship=review.get('allow_unknown_sponsorship') is True)
        require(evaluate_vacancy_evidence(evidence, now=now, sponsorship_policy=policy)['review_candidate'])
    except Exception:
        raise PermissionError('APPLICATION_POSTING_UNVERIFIED') from None


@dataclass(frozen=True)
class UploadObservation:
    payload_sha256: str
    payload_size: int
    filename: str
    attachment_ref_sha256: str
    acknowledged_endpoint: str
    ui_attachment_visible: bool
    # This is a hash of the bytes supplied to Playwright, NOT a vendor claim that
    # the stored attachment has been downloaded and hashed independently.


@dataclass(frozen=True)
class ConfirmationObservation:
    endpoint: str
    observed_at: datetime
    status: int
    acknowledgement_sha256: str
    success_acknowledged: bool
    confirmation_visible: bool
    challenge_detected: bool = False


class ApplicationBrowser(Protocol):
    def open(self, url: str) -> None: ...
    def preferred_entry(self) -> None: ...
    def accept_reviewed_entry_return(self, observation_id: str, manifest: dict) -> None: ...
    def inspect(self) -> dict: ...
    def write(self, binding: dict) -> None: ...
    def readback(self, binding: dict) -> object: ...
    def upload(self, binding: dict, payload: bytes, filename: str) -> UploadObservation: ...
    def advance(self, binding: dict, expected_url: str) -> None: ...
    def submit_once(self, binding: dict) -> ConfirmationObservation: ...


def _resolutions(manifest: dict) -> tuple[ControlResolution, ...]:
    out = []
    aliases = {'work.sponsorship_required': 'work.requires_sponsorship',
               'consent.privacy': 'consent.privacy_acknowledgement', 'consent.marketing': 'consent.marketing_opt_in',
               'compensation.expected_annual_gross': 'compensation.salary_expectation',
               'compensation.expected_salary_range': 'compensation.salary_expectation'}
    for step_index, step in enumerate(manifest['steps']):
        for item in step['fields']:
            canonical = aliases.get(item['canonical_field'], item['canonical_field'])
            out.append(ControlResolution(f"step{step_index}:{item['key']}", 'Reviewed control', item['required'], canonical,
                MappingConfidence.HIGH, ResolutionMode(item['resolution_mode']), item['provenance_ref'],
                policy_ref=item.get('policy_ref', ''), explicit_user_fact=True))
    out.append(ControlResolution('approved-cv', 'Approved CV', True, 'document.cv', MappingConfidence.HIGH,
                                 ResolutionMode.ARTIFACT, artifact_ref=manifest['cv']['source_ref']))
    return tuple(out)


class SingleApplicationRuntime:
    """The trusted bootstrap supplies the canonical ledger and reviewed inputs.

    No ledger location, authority or observer can be selected by employer markup.
    An occupied ledger stops BEFORE preparation and therefore before another CV
    upload. No alternative database, retry or state reset is created on failure.
    """
    def __init__(self, browser: ApplicationBrowser, ledger: SqliteSubmissionLedger, *, clock=utc_now):
        self.browser, self.ledger, self.clock = browser, ledger, clock

    def run(self, manifest: dict, facts: dict, target: OneClickTarget, cv_path: Path,
            *, authority: SubmitAuthority | None = None, posting_payload: bytes | None = None) -> dict:
        return self._run(manifest, facts, target, cv_path, authority=authority,
                         posting_payload=posting_payload)

    def resume_reviewed_return(self, manifest: dict, facts: dict, target: OneClickTarget, cv_path: Path,
                               *, observation_id: str, authority: SubmitAuthority | None = None,
                               posting_payload: bytes | None = None) -> dict:
        """Continue the same owned page after a separately pinned return review.

        No open/goto, native fallback or authority change is performed. The
        browser must consume its own fresh, single-use observation capability;
        an employer/manifest cannot provide an arbitrary page or attest a grant.
        """
        if type(observation_id) is not str or not observation_id.strip() or len(observation_id) > 512:
            return {'status': 'BLOCKED', 'submit_click_attempts': 0, 'applied': False,
                    'error_code': 'APPLICATION_RETURN_OBSERVATION_REQUIRED'}
        return self._run(manifest, facts, target, cv_path, authority=authority,
                         posting_payload=posting_payload, entry_observation_id=observation_id)

    def _run(self, manifest: dict, facts: dict, target: OneClickTarget, cv_path: Path,
             *, authority: SubmitAuthority | None = None, posting_payload: bytes | None = None,
             entry_observation_id: str | None = None) -> dict:
        started = False
        entry_in_progress = False
        try:
            authority = authority or SubmitAuthority()
            require(isinstance(authority, SubmitAuthority))
            for field in fields(authority):
                require(type(getattr(authority, field.name)) is bool)
            authority.validate_policy_boundary()
            if not authority.submit_click_allowed:
                return {'status': 'RUNTIME_DISABLED', 'submit_click_attempts': 0, 'applied': False}
            if self.ledger.has_intent():
                return {'status': 'DUPLICATE_OR_UNCERTAIN', 'submit_click_attempts': 0, 'applied': False}
            now = self.clock()
            require(now.tzinfo is not None and now.utcoffset().total_seconds() == 0)
            fresh(manifest['reviewed_at'], now, 24 * 3600)
            require(manifest['steps'][0]['url'] == target.entry_url and manifest['steps'][-1]['url'] == target.screening_url)
            require(manifest['requisition_id'] == target.requisition_id)
            # The injected payload is only a deterministic fixture seam; live
            # bootstrap never accepts one from a manifest or CLI argument.
            verify_posting(target, manifest, facts, posting_payload if posting_payload is not None else fetch_posting(target), now)
            cv = read_approved_cv(cv_path, manifest['cv'])
            entry_in_progress = True
            if entry_observation_id is None:
                self.browser.open(target.entry_url)
            else:
                self.browser.accept_reviewed_entry_return(entry_observation_id, manifest)
            self.browser.preferred_entry()
            entry_in_progress = False
            observed = []
            uploads = []
            for index, step in enumerate(manifest['steps']):
                self._surface(step)
                for binding in step['fields']:
                    self._surface(step)
                    self.browser.write(binding)
                    readback = self.browser.readback(binding)
                    require(type(readback) is type(binding['value'])
                            and readback == binding['value'], 'APPLICATION_READBACK_MISMATCH')
                    self._surface(step)
                if 'upload' in step:
                    require(not uploads, 'APPLICATION_ONE_CV_REQUIRED')
                    upload = self.browser.upload(step['upload'], cv, manifest['cv']['approved_filename'])
                    require(upload.payload_sha256 == manifest['cv']['sha256']
                            and upload.payload_size == len(cv) and upload.filename == manifest['cv']['approved_filename']
                            and upload.ui_attachment_visible is True
                            and re.fullmatch('[a-f0-9]{64}', upload.attachment_ref_sha256)
                            and upload.acknowledged_endpoint == target.resume_upload_url(upload.filename), 'APPLICATION_UPLOAD_UNVERIFIED')
                    uploads.append(upload)
                # Read EVERY field again after all writes, catching dependent
                # controls, prefills, reactive overwrites and delayed autosaves.
                self._surface(step)
                values = [self.browser.readback(b) for b in step['fields']]
                require(all(type(value) is type(b['value']) and value == b['value'] for b, value in zip(step['fields'], values)),
                        'APPLICATION_READBACK_MISMATCH')
                observed.append((step['fingerprint'], digest(values)))
                if index + 1 < len(manifest['steps']):
                    self.browser.advance(step['next'], manifest['steps'][index + 1]['url'])
            require(len(uploads) == 1, 'APPLICATION_ONE_CV_REQUIRED')
            final = manifest['steps'][-1]
            snapshot = self._surface(final)
            require(snapshot.get('form_valid') is True and snapshot.get('submit_visible') is True,
                    'APPLICATION_FORM_NOT_READY')
            inspected_at = self.clock()
            request = PreSubmitValidationRequest(manifest['execution_id'], manifest['opportunity_key'], target.requisition_id,
                manifest['candidate_profile_version'], target.screening_url, 'smartrecruiters', digest(observed), DriftState.MATCH,
                True, True, _resolutions(manifest), rollout_stage=RolloutStage.R2_SINGLE_CANARY)
            validation = validate_pre_submit(request, policy=ApprovedSubmitPolicy(), authority=authority)
            require(validation.execution_allowed, 'APPLICATION_SUBMIT_POLICY_BLOCKED')
            proof = digest({'manifest': manifest, 'form': observed, 'upload': uploads[0].__dict__})
            attempt = replace(create_submission_attempt(request, validation, requested_at=inspected_at.isoformat()),
                              validation_key='intent-proof:' + proof)
            # Recheck live surface/readbacks immediately before consuming intent.
            self._surface(final)
            final_values = [self.browser.readback(b) for b in final['fields']]
            require(all(type(v) is type(b['value']) and v == b['value'] for b, v in zip(final['fields'], final_values)),
                    'APPLICATION_READBACK_MISMATCH')
            require(0 <= (self.clock() - inspected_at).total_seconds() <= 120, 'APPLICATION_FRESH_REVIEW_REQUIRED')
            require(self.ledger.reserve_intent(attempt, self.clock()), 'APPLICATION_SINGLE_SCOPE_CONSUMED')
            self.ledger.mark_started(validation.submit_key, request.execution_id, self.clock())
            started = True
            confirmation = self.browser.submit_once(final['submit'])
            require(confirmation.endpoint == target.application_url and confirmation.status in (200, 201)
                    and confirmation.success_acknowledged is True and confirmation.confirmation_visible is True
                    and confirmation.challenge_detected is False
                    and re.fullmatch('[a-f0-9]{64}', confirmation.acknowledgement_sha256)
                    and inspected_at <= confirmation.observed_at <= self.clock(), 'APPLICATION_CONFIRMATION_UNVERIFIED')
            evidence = ConfirmationEvidence(validation.submit_key, ConfirmationType.ATS_ACKNOWLEDGEMENT,
                'evidence:sha256:' + confirmation.acknowledgement_sha256, confirmation.observed_at.isoformat(),
                True, request.execution_id, target.requisition_id)
            require(self.ledger.confirm(validation.submit_key, evidence, self.clock()), 'APPLICATION_CONFIRMATION_UNVERIFIED')
            return {'status': 'EMPLOYER_CONFIRMED', 'submit_click_attempts': 1, 'applied': True,
                    'applications_write_performed': False, 'evidence_ref': evidence.evidence_ref}
        except Exception as error:
            # Once started, no UI retry is possible through this entry point.
            code = str(error) if isinstance(error, PermissionError) else ''
            if re.fullmatch(r'(?:APPLICATION|ONECLICK)_[A-Z0-9_]{1,100}', code) is None:
                code = 'APPLICATION_RUNTIME_BLOCKED'
            if not started and entry_in_progress and code == 'APPLICATION_LINKEDIN_HUMAN_HANDOFF_REQUIRED':
                return {'status': 'HUMAN_HANDOFF_REQUIRED', 'submit_click_attempts': 0,
                        'applied': False, 'error_code': code, 'fresh_return_review_required': True,
                        'native_fallback_allowed': False}
            return {'status': 'SUBMITTED_UNCONFIRMED' if started else 'BLOCKED',
                    'submit_click_attempts': None if started else 0, 'dispatch_intent_recorded': started,
                    'applied': False, 'applications_write_performed': False, 'error_code': code}

    def _surface(self, step: dict) -> dict:
        snapshot = self.browser.inspect()
        require(snapshot.get('url') == step['url'] and snapshot.get('fingerprint') == step['fingerprint']
                and snapshot.get('challenge') is False and snapshot.get('unexpected_write') is False
                and snapshot.get('unreviewed_prefill') is False, 'APPLICATION_SURFACE_CHANGED')
        actual = snapshot.get('required_keys')
        expected = {b['key'] for b in step['fields'] if b['required']}
        if 'upload' in step:
            expected.add(step['upload']['key'])
        require(type(actual) is set and actual == expected, 'APPLICATION_UNKNOWN_REQUIRED_CONTROL')
        return snapshot
