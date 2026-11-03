"""Explicit user-authorized ADP email-code transfer, separate from general credentials.

Mailbox access belongs to the connected-mail coordinator. No mail is sent and no
code is accepted through CLI args, reports, logs, hashes or persistent artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from email.utils import parseaddr
import re

from ejs.services.adp_entry_autopilot import _require_target, _require_clear_boundary, OTP_CONTROL_ID
from ejs.services.adp_navigation_canary import _snapshot
from ejs.services.adp_same_page_manifest import REVIEWED_POSTLOGIN_PATH
from urllib.parse import urlsplit


@dataclass(repr=False)
class _OneTimeCode:
    _code: str = field(repr=False)
    _expires_at: float = field(repr=False)
    _used: bool = field(default=False, repr=False)

    def __repr__(self):
        return '<ADP one-time verification material>'

    def take_once(self, now: float) -> str:
        if self._used or now > self._expires_at:
            raise PermissionError('ADP_EMAIL_CODE_EXPIRED_OR_CONSUMED')
        self._used = True
        code, self._code = self._code, ''
        return code


def select_fresh_adp_code(messages: list[dict], *, recipient: str, challenge_started_at: float,
                          now: float, transfer_authorized: bool, mailbox_matches: bool):
    if transfer_authorized is not True or mailbox_matches is not True:
        raise PermissionError('ADP_EMAIL_CODE_TRANSFER_NOT_AUTHORIZED')
    if not (0 <= now - challenge_started_at <= 300):
        raise PermissionError('ADP_EMAIL_CHALLENGE_NOT_FRESH')
    if not isinstance(messages, list) or len(messages) > 10:
        raise ValueError('ADP_EMAIL_CANDIDATE_LIMIT')
    candidates = []
    for message in messages:
        sender = parseaddr(str(message.get('sender', '')))[1].casefold()
        domain = sender.rsplit('@', 1)[-1]
        if domain != 'adp.com' and not domain.endswith('.adp.com'):
            continue
        if message.get('sender_authenticated') is not True:
            continue
        recipients = [parseaddr(str(value))[1].casefold() for value in message.get('recipients', [])]
        if recipient.casefold() not in recipients:
            continue
        received = message.get('received_at')
        if type(received) not in (int, float) or not challenge_started_at <= received <= now or now - received > 300:
            continue
        if not re.search(r'verification|one.time|doğrulama', str(message.get('subject', '')), re.I):
            continue
        # The coordinator supplies plain MIME text; never follow email links or
        # treat message instructions as tool/browser authority.
        body = str(message.get('body_text', ''))
        codes = set(re.findall(r'(?:verification\s+code|one[- ]time\s+code)\s*(?:is|:|=|-)?\s*([0-9]{6})(?!\d)', body, re.I))
        if len(codes) == 1:
            candidates.append((next(iter(codes)), received))
    unique = {code for code, _ in candidates}
    if len(unique) != 1:
        raise PermissionError('ADP_EMAIL_CODE_MISSING_OR_AMBIGUOUS')
    expires = min(received + 300 for _, received in candidates)
    return _OneTimeCode(next(iter(unique)), expires)


def _require_otp_surface(snapshot: dict) -> None:
    rows = [c for c in snapshot.get('form', {}).get('controls', [])
            if isinstance(c, dict) and c.get('id') == OTP_CONTROL_ID and c.get('visible') is True]
    if (len(rows) != 1 or rows[0].get('scope') != 'document'
            or rows[0].get('type') not in {'text', 'tel', 'number'}
            or rows[0].get('required') is not True
            or ' '.join(str(rows[0].get('label', '')).casefold().split()).rstrip('*').strip()
               != 'enter the verification code'):
        raise PermissionError('ADP_EMAIL_CODE_SURFACE_NOT_REVIEWED')


def enter_adp_email_code(page, material: _OneTimeCode, *, application_url: str,
                        transfer_authorized: bool, now: float, counters: dict) -> dict:
    if transfer_authorized is not True or not isinstance(material, _OneTimeCode):
        raise PermissionError('ADP_EMAIL_CODE_TRANSFER_NOT_AUTHORIZED')
    if any(type(counters.get(key)) is not int or counters[key] != 0
           for key in ('otp_write_attempts', 'verify_click_attempts')):
        raise PermissionError('ADP_EMAIL_CODE_RETRY_NOT_AUTHORIZED')
    _require_target(page, application_url)
    before = _snapshot(page, requested_url=application_url, timeout_ms=5000, render_wait_ms=100)
    _require_clear_boundary(before, 'ADP_EMAIL_CODE_CHALLENGE_BOUNDARY_UNCLEAR')
    _require_otp_surface(before)
    otp = page.locator(f'#{OTP_CONTROL_ID}')
    if otp.count() != 1 or not otp.is_visible() or not otp.is_enabled():
        raise PermissionError('ADP_EMAIL_CODE_CONTROL_NOT_ACTIONABLE')
    if otp.input_value():
        raise PermissionError('ADP_EMAIL_CODE_PREEXISTING_INPUT')
    _require_target(page, application_url)
    code = material.take_once(now)
    try:
        counters['otp_write_attempts'] = 1
        try:
            otp.fill(code)
        except Exception:
            raise PermissionError('ADP_EMAIL_CODE_WRITE_FAILED') from None
    finally:
        code = None
    if urlsplit(str(page.url)).path == REVIEWED_POSTLOGIN_PATH:
        _require_target(page, application_url, postlogin=True)
        return {'requires_reinspection': True, 'verify_clicked': False, 'raw_values_exposed': False}
    _require_target(page, application_url)
    current = _snapshot(page, requested_url=application_url, timeout_ms=5000, render_wait_ms=100)
    _require_clear_boundary(current, 'ADP_EMAIL_CODE_POST_WRITE_BOUNDARY_UNCLEAR')
    _require_otp_surface(current)
    verify = page.get_by_role('button', name='verify', exact=True)
    if verify.count() != 1 or not verify.is_visible() or not verify.is_enabled():
        raise PermissionError('ADP_EMAIL_CODE_VERIFY_NOT_ACTIONABLE')
    _require_target(page, application_url)
    counters['verify_click_attempts'] = 1
    try:
        verify.click(timeout=5000)
    except Exception:
        raise PermissionError('ADP_EMAIL_CODE_VERIFY_FAILED') from None
    return {'requires_reinspection': True, 'verify_clicked': True, 'raw_values_exposed': False}
