"""MVP-05: review-only schema check, NOT independent fact verification.

Claimed source metadata is checked here; no source is read and no answer value is
compared to it. Execution still requires the separately pinned live manifest and
verified-facts compiler. REVIEW_READY can never authorize any ATS action.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from ejs.contracts.prefill import SAFE_VERIFIED_FIELDS, DYNAMIC_AI_FIELDS

LEGAL_FIELDS = frozenset({
    'work_authorization', 'visa_sponsorship', 'citizenship', 'residency',
    'salary_expectation', 'criminal_history', 'consent', 'disability_status',
})
ALLOWED_SOURCE_TYPES = frozenset({'candidate_verified', 'approved_document', 'user_explicit'})
_ALLOWED_FIELDS = SAFE_VERIFIED_FIELDS | DYNAMIC_AI_FIELDS | LEGAL_FIELDS
_ALIASES = {
    'first_name': 'candidate.first_name', 'last_name': 'candidate.last_name',
    'email': 'candidate.email', 'phone': 'candidate.phone', 'linkedin_url': 'candidate.linkedin_url',
    'notice_period': 'employment.notice_period', 'referral': 'application.referral',
    'motivation_text': 'application.motivation_text',
    'work.authorization': 'work_authorization', 'work_right.eu_eea': 'work_authorization',
    'work.requires_sponsorship': 'visa_sponsorship', 'work.sponsorship_required': 'visa_sponsorship', 'work_right.sponsorship_required': 'visa_sponsorship',
    'candidate.citizenship': 'citizenship', 'nationality': 'citizenship',
    'location.residency': 'residency', 'compensation.salary_expectation': 'salary_expectation',
    'compensation.expected_salary': 'salary_expectation',
    'consent.privacy': 'consent', 'consent.marketing': 'consent',
    'demographic.disability_status': 'disability_status',
}


def _report(errors: list[str], count: int = 0) -> dict:
    return {
        'status': 'BLOCKED' if errors else 'REVIEW_READY', 'reasons': errors, 'field_count': count,
        'scope': 'schema_metadata_only', 'source_verification_performed': False,
        'fact_comparison_performed': False, 'runtime_manifest_required': True,
        'write_authorized': False, 'upload_authorized': False, 'submit_authorized': False,
    }


def evaluate_answer_manifest(manifest: object) -> dict:
    if not isinstance(manifest, Mapping):
        return _report(['INVALID_MANIFEST'])
    fields = manifest.get('fields')
    if not isinstance(fields, list) or not fields:
        return _report(['MISSING_FIELDS'])
    if len(fields) > 100:
        return _report(['TOO_MANY_FIELDS'], len(fields))
    errors: list[str] = []
    seen: set[str] = set()
    for index, field in enumerate(fields):
        # Diagnostics never contain supplied keys, answer values or source refs.
        prefix = f'field_{index}'
        if not isinstance(field, Mapping):
            errors.append(f'{prefix}:INVALID_FIELD')
            continue
        key = field.get('key')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            errors.append(f'{prefix}:INVALID_OR_DUPLICATE_KEY')
            continue
        key = key.strip().casefold()
        key = _ALIASES.get(key, key)
        if key not in _ALLOWED_FIELDS:
            errors.append(f'{prefix}:UNSUPPORTED_FIELD')
            continue
        if key in seen:
            errors.append(f'{prefix}:INVALID_OR_DUPLICATE_KEY')
            continue
        seen.add(key)
        value = field.get('value')
        if not isinstance(value, (str, bool, int, float)) or (
            isinstance(value, str) and (not value.strip() or len(value) > 10_000)
        ) or (isinstance(value, float) and not math.isfinite(value)):
            errors.append(f'{prefix}:MISSING_OR_INVALID_VALUE')
        provenance = field.get('provenance')
        if not isinstance(provenance, Mapping):
            errors.append(f'{prefix}:MISSING_PROVENANCE')
            continue
        source_type = provenance.get('source_type')
        if not isinstance(source_type, str) or source_type not in ALLOWED_SOURCE_TYPES:
            errors.append(f'{prefix}:UNAPPROVED_SOURCE_TYPE')
        source_ref = provenance.get('source_ref')
        if not isinstance(source_ref, str) or not source_ref.strip() or len(source_ref) > 512:
            errors.append(f'{prefix}:MISSING_SOURCE_REF')
        if provenance.get('verified') is not True or provenance.get('approved') is not True:
            errors.append(f'{prefix}:UNVERIFIED_SOURCE')
        if key in LEGAL_FIELDS and source_type != 'user_explicit':
            errors.append(f'{prefix}:USER_CONFIRMATION_REQUIRED')
    return _report(errors, len(fields))
