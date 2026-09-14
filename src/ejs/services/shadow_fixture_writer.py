"""Scoped writer prototype, callable only through an owned offline fixture.

Not integrated with live canary. No target URL, uploads, clicks, or transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from ejs.contracts.prefill import PrefillFieldPlan, SafeFieldWriterAuthority
from ejs.services.prefill_writer import validate_prefill_plan
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


FIXTURE_HTML = '''<!doctype html><html><body>
<div id="profile"></div><div id="other"></div>
<script>
window.events = {input:0, click:0, submit:0};
const a = document.getElementById('profile').attachShadow({mode:'open'});
a.innerHTML = '<label for="first">First name</label><input id="first" required maxlength="80"><label for="email">Email</label><input id="email" type="email" required><div id="details"></div><input id="cv" type="file"><button type="button">Next</button>';
const b = document.getElementById('other').attachShadow({mode:'open'});
b.innerHTML = '<label for="first">Other first name</label><input id="first" value="UNTOUCHED"><input id="cv" type="file">';
const c = a.getElementById('details').attachShadow({mode:'open'});
c.innerHTML = '<label for="phone">Phone</label><input id="phone" type="tel"><input id="disabled" disabled><input id="hidden" style="display:none">';
for (const r of [a,b,c]) for (const kind of ['input','click','submit']) r.addEventListener(kind,()=>window.events[kind]++);
</script></body></html>'''


@dataclass(frozen=True)
class ScopedFieldPlan:
    field: PrefillFieldPlan
    host_ids: tuple[str, ...]
    input_id: str
    expected_label: str


def validate_scoped_plans(plans: tuple[ScopedFieldPlan, ...]) -> None:
    SafeFieldWriterAuthority().validate()
    if not plans or len(plans) > 20:
        raise ValueError("FIXTURE_PLAN_SIZE")
    blockers = validate_prefill_plan(tuple(p.field for p in plans))
    if blockers:
        raise ValueError("FIELD_POLICY_BLOCKED")
    targets = [(p.host_ids, p.input_id) for p in plans]
    if len(targets) != len(set(targets)) or len({p.field.field_plan_key for p in plans}) != len(plans):
        raise ValueError("DUPLICATE_FIELD_TARGET")
    for p in plans:
        if not p.host_ids or not p.expected_label:
            raise ValueError("SCOPED_IDENTITY_REQUIRED")
        # No arbitrary CSS selectors or positional .first() escape hatch.
        if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", x) for x in (*p.host_ids, p.input_id)):
            raise ValueError("INVALID_STABLE_ID")
        if p.field.control_type not in {"text", "email", "tel", "url", "textarea"}:
            raise ValueError("TEXT_CONTROLS_ONLY")
        if len(p.field.value) > 2000:
            raise ValueError("VALUE_TOO_LONG")


def _resolve(page, plan):
    scope = page
    for host_id in plan.host_ids:
        scope = scope.locator(f'[id="{host_id}"]')
        if scope.count() != 1 or not scope.evaluate('(el) => !!el.shadowRoot'):
            raise ValueError("HOST_NOT_UNIQUE_OR_OPEN")
    loc = scope.locator(f'[id="{plan.input_id}"]')
    if loc.count() != 1:
        raise ValueError("CONTROL_NOT_UNIQUE")
    meta = loc.evaluate('''el => ({tag:el.tagName.toLowerCase(), type:el.type,
      role:el.getAttribute('role'), max:el.maxLength,
      label:el.getAttribute('aria-label') || Array.from(el.labels || []).map(n=>n.textContent).join(' ')})''')
    if meta['tag'] not in {'input', 'textarea'} or meta['type'] != plan.field.control_type:
        raise ValueError("CONTROL_TYPE_DRIFT")
    if meta['role'] == 'combobox':
        raise ValueError("CUSTOM_CONTROL_REQUIRES_ADAPTER")
    if ' '.join((meta['label'] or '').split()) != plan.expected_label:
        raise ValueError("CONTROL_LABEL_DRIFT")
    if not loc.is_visible() or not loc.is_enabled():
        raise ValueError("CONTROL_UNAVAILABLE")
    if meta['max'] >= 0 and len(plan.field.value) > meta['max']:
        raise ValueError("MAX_LENGTH_EXCEEDED")
    return loc


def _write_fixture_page(page, plans, expected_schema):
    """Private helper. Public entry point owns the isolated context and fixture."""
    validate_scoped_plans(plans)
    result = {'target_mode': 'fixture', 'runtime_state': 'blocked', 'error_code': '',
              'form_value_write_attempts': 0, 'verified_fields': 0,
              'file_upload_attempts': 0, 'submit_attempts': 0, 'next_attempts': 0,
              'live_execution_ready': False}
    try:
        if page.url != 'about:blank':
            raise ValueError('FIXTURE_ONLY')
        if inspect_shadow_form(page)['schema_fingerprint'] != expected_schema:
            raise ValueError('SCHEMA_DRIFT')
        # Preflight every plan before the first write; no partial preflight.
        for plan in plans:
            _resolve(page, plan)
        for plan in plans:
            if page.url != 'about:blank' or inspect_shadow_form(page)['schema_fingerprint'] != expected_schema:
                raise ValueError('SCHEMA_DRIFT')
            loc = _resolve(page, plan)
            if loc.input_value() != plan.field.value:
                result['form_value_write_attempts'] += 1
                loc.fill(plan.field.value)
            if page.url != 'about:blank':
                raise ValueError('UNEXPECTED_NAVIGATION')
            if loc.input_value() != plan.field.value or not loc.evaluate('(el)=>el.checkValidity()'):
                raise ValueError('READBACK_MISMATCH')
            result['verified_fields'] += 1
        if inspect_shadow_form(page)['schema_fingerprint'] != expected_schema:
            raise ValueError('POST_WRITE_SCHEMA_DRIFT')
        result['runtime_state'] = 'fixture_prefill_verified'
    except ValueError as exc:
        result['error_code'] = str(exc)
    except Exception:
        # Never leak Playwright exceptions containing field values into logs.
        result['error_code'] = 'FIXTURE_WRITE_ERROR'
    return result


def run_shadow_fixture(plans: tuple[ScopedFieldPlan, ...]) -> dict:
    """Uses only bundled HTML, a new offline context, and blocked network routes."""
    validate_scoped_plans(plans)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(offline=True, service_workers='block', accept_downloads=False)
            context.route('**/*', lambda route: route.abort())
            page = context.new_page()
            page.set_default_timeout(3000)
            page.set_content(FIXTURE_HTML)
            expected = inspect_shadow_form(page)['schema_fingerprint']
            return _write_fixture_page(page, plans, expected)
        finally:
            browser.close()
