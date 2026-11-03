"""Existing-page ADP/SmartRecruiters entry adapter; no browser/session lifecycle changes."""
from __future__ import annotations

from urllib.parse import urlsplit

from ejs.services.application_entry_strategy import activate_preferred_awli, choose_application_entry, entry_surface_fingerprint
from ejs.services.adp_entry_autopilot import _require_target, REVIEWED_POSTLOGIN_PATH
from ejs.services.adp_navigation_canary import _snapshot, _resolve_document_locator


AWLI_ACTION_EXTRACTOR = r"""
() => {
  const results = [];
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const visible = el => {
    for (let n = el; n; n = n.parentElement || n.getRootNode().host) {
      const style = getComputedStyle(n);
      if (style.display === 'none' || style.visibility === 'hidden' || n.hidden) return false;
    }
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const visit = (root, scope) => {
    for (const [index, el] of Array.from(root.querySelectorAll('*')).entries()) {
      const tag = el.tagName.toLowerCase();
      if (el.matches('button,a,input[type="button"]')) {
        const label = el.getAttribute('aria-label') || el.textContent || '';
        if (norm(label) === 'apply with linkedin') results.push({
          scope, observation_key: `${scope}/${tag}@${index}`,
          label: 'Apply with LinkedIn', visible: visible(el),
          disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true'
        });
      }
      if (el.shadowRoot) visit(el.shadowRoot, `${scope}/${tag}@${index}::shadow`);
    }
  };
  visit(document, 'document');
  return results;
}
"""


def require_application_target(page, application_url: str) -> None:
    expected, current = urlsplit(application_url), urlsplit(str(page.url))
    for value in (expected, current):
        if (value.scheme != 'https' or value.username is not None or value.password is not None
                or value.port not in (None, 443)):
            raise PermissionError('PREFERRED_ENTRY_TARGET_MISMATCH')
    if expected.hostname == 'workforcenow.adp.com':
        _require_target(page, application_url, postlogin=current.path == REVIEWED_POSTLOGIN_PATH)
    elif expected.hostname == 'jobs.smartrecruiters.com':
        if current.hostname != expected.hostname or current.path != expected.path:
            raise PermissionError('PREFERRED_ENTRY_TARGET_MISMATCH')
        parts = expected.path.strip('/').split('/')
        if len(parts) < 2 or not parts[1].split('-')[0].isdigit():
            raise PermissionError('PREFERRED_ENTRY_TARGET_MISMATCH')
    else:
        raise PermissionError('PREFERRED_ENTRY_UNSUPPORTED_ATS')


def inspect_preferred_entry(page, application_url: str, *, entry_attempts: int = 0) -> dict:
    require_application_target(page, application_url)
    observed = _snapshot(page, requested_url=application_url, timeout_ms=5000, render_wait_ms=500)
    require_application_target(page, application_url)
    actions = [a for a in observed.get('form', {}).get('actions', [])
               if ' '.join(str(a.get('label', '')).casefold().split()) != 'apply with linkedin']
    actions.extend(page.evaluate(AWLI_ACTION_EXTRACTOR))
    require_application_target(page, application_url)
    result = {'target_bound': True, 'auth_observed': observed.get('auth_observed'),
              'captcha_observed': observed.get('captcha_observed'),
              'entry_attempts': entry_attempts, 'actions': actions}
    return {'observation': result, 'surface_fingerprint': entry_surface_fingerprint(result),
            'choice': choose_application_entry(result)}


def use_preferred_entry(page, application_url: str, *, expected_surface_fingerprint: str, counters: dict) -> dict:
    """Use the reviewed AWLI preference on an already-owned live page.

    AWLI absence returns native without clicking. AWLI use performs one click
    and returns control to the caller for auth/scope/target and canonical-profile
    verification. It never closes a popup or resumes native entry after a click.
    """
    return activate_preferred_awli(
        inspect_current=lambda: inspect_preferred_entry(page, application_url,
            entry_attempts=counters.get('awli_click_attempts'))['observation'],
        require_target=lambda: require_application_target(page, application_url),
        resolve_action=lambda key: _resolve_document_locator(page, key),
        expected_surface_fingerprint=expected_surface_fingerprint, counters=counters,
    )
