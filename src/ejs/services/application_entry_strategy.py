"""Prefer exact AWLI entry; entry never proves profile correctness or application success."""
from __future__ import annotations

import hashlib
import json

VERSION = 'application-entry-preference-v1'
AWLI_LABEL = 'apply with linkedin'


def _normalized(label):
    return ' '.join(str(label or '').casefold().split())


def entry_surface_fingerprint(observation: dict) -> str:
    actions = [{k: action.get(k) for k in ('observation_key', 'scope', 'label', 'visible', 'disabled')}
               for action in observation.get('actions', []) if isinstance(action, dict)]
    actions.sort(key=lambda item: str(item.get('observation_key', '')))
    payload = {key: observation.get(key) for key in ('target_bound', 'auth_observed', 'captcha_observed', 'entry_attempts', 'observation_complete')}
    payload['actions'] = actions
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def choose_application_entry(observation: dict) -> dict:
    """Pure policy: AWLI first; native only when no AWLI attempt/boundary exists."""
    common = {'version': VERSION, 'safe_fill_allowed': False, 'final_submit_allowed': False,
              'profile_verification_required': True}
    if (observation.get('target_bound') is not True
            or observation.get('auth_observed') is not False
            or observation.get('captcha_observed') is not False):
        return {**common, 'route': 'human_review', 'reason': 'TARGET_OR_AUTH_BOUNDARY_UNCLEAR'}
    if observation.get('observation_complete') is not True:
        return {**common, 'route': 'human_review', 'reason': 'ENTRY_SURFACE_NOT_READY'}
    attempts = observation.get('entry_attempts')
    if type(attempts) is not int or attempts != 0:
        return {**common, 'route': 'human_review', 'reason': 'ENTRY_RETRY_NOT_AUTHORIZED'}
    actions = observation.get('actions')
    if not isinstance(actions, list):
        raise ValueError('INVALID_ENTRY_ACTIONS')
    awli = [a for a in actions if isinstance(a, dict) and a.get('visible') is True
            and _normalized(a.get('label')) == AWLI_LABEL]
    if not awli:
        return {**common, 'route': 'native', 'reason': 'AWLI_NOT_OBSERVED'}
    if (len(awli) != 1 or awli[0].get('disabled') is not False
            or awli[0].get('scope') != 'document' or not awli[0].get('observation_key')):
        return {**common, 'route': 'human_review', 'reason': 'AWLI_ACTION_AMBIGUOUS_OR_UNSUPPORTED'}
    return {**common, 'route': 'awli', 'reason': 'PREFER_EXACT_AWLI',
            'observation_key': awli[0]['observation_key']}


def activate_preferred_awli(*, inspect_current, require_target, resolve_action,
                            expected_surface_fingerprint: str, counters: dict,
                            timeout_ms: int = 20_000) -> dict:
    """One approved AWLI click; retain caller's live browser for fresh inspection.

    Callbacks belong to the current ATS adapter. The approved fingerprint must
    come from reviewed evidence, not be created as an approval by this function.
    No OAuth grant, credentials, fallback retry, field writes, upload or submit.
    """
    if type(timeout_ms) is not int or not 1000 <= timeout_ms <= 60000:
        raise ValueError('INVALID_AWLI_TIMEOUT')
    if counters.get('awli_click_attempts') != 0 or type(counters.get('awli_click_attempts')) is not int:
        raise PermissionError('AWLI_RETRY_NOT_AUTHORIZED')
    require_target()
    current = inspect_current()
    if entry_surface_fingerprint(current) != expected_surface_fingerprint:
        raise PermissionError('AWLI_ENTRY_SURFACE_DRIFT')
    choice = choose_application_entry(current)
    if choice['route'] != 'awli':
        return choice
    action = resolve_action(choice['observation_key'])
    if action.count() != 1 or not action.is_visible() or not action.is_enabled():
        raise PermissionError('AWLI_ACTION_NOT_ACTIONABLE')
    label = action.get_attribute('aria-label') or action.inner_text()
    if _normalized(label) != AWLI_LABEL:
        raise PermissionError('AWLI_ACTION_LABEL_DRIFT')
    # Reinspect after resolving the locator: a late challenge/target/action change
    # cannot use the initial surface approval.
    require_target()
    if entry_surface_fingerprint(inspect_current()) != expected_surface_fingerprint:
        raise PermissionError('AWLI_ENTRY_SURFACE_DRIFT')
    counters['awli_click_attempts'] = 1
    action.click(timeout=timeout_ms)
    counters['awli_click_successes'] = 1
    return {**choice, 'route': 'awli_started_requires_reinspection',
            'native_fallback_allowed': False, 'oauth_grant_allowed': False,
            'credential_entry_allowed': False, 'profile_verification_required': True}
