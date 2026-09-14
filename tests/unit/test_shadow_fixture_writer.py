from dataclasses import replace
import importlib.util
import json
import unittest

from ejs.contracts.prefill import PrefillFieldPlan, FieldOwnership, MappingConfidence, ResolverStatus
from ejs.services.shadow_fixture_writer import (
    FIXTURE_HTML, ScopedFieldPlan, validate_scoped_plans, _write_fixture_page, run_shadow_fixture,
)
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


def plan(input_id='first', value='Fixture', label='First name', kind='text', hosts=('profile',), canonical='candidate.first_name'):
    return ScopedFieldPlan(PrefillFieldPlan(
        'plan:'+input_id, 'fixture:'+input_id, canonical, kind, value, 'fixture:verified:v1',
        FieldOwnership.AUTO_SAFE, ResolverStatus.RESOLVED, MappingConfidence.HIGH,
    ), hosts, input_id, label)


class ScopedPolicyTests(unittest.TestCase):
    def test_forbidden_consent_file_and_pending_review_are_rejected(self):
        p = plan()
        for field in [replace(p.field, canonical_field='consent.privacy'),
                      replace(p.field, control_type='file'),
                      replace(p.field, review_required=True),
                      replace(p.field, provenance_ref=''),
                      replace(p.field, ownership=FieldOwnership.USER_ONLY)]:
            with self.subTest(field=field.canonical_field), self.assertRaises(ValueError):
                validate_scoped_plans((replace(p, field=field),))

    def test_duplicate_target_and_css_injection_rejected(self):
        p = plan()
        for plans in [(p, p), (replace(p, input_id='first"],input'),), (replace(p, host_ids=()),)]:
            with self.assertRaises(ValueError):
                validate_scoped_plans(plans)

    def test_select_and_oversized_values_rejected(self):
        for p in [plan(kind='select'), plan(value='x'*2001)]:
            with self.assertRaises(ValueError):
                validate_scoped_plans((p,))


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is not installed')
class ScopedWriterBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(offline=True, service_workers='block')
        self.context.route('**/*', lambda route: route.abort())
        self.page = self.context.new_page()
        self.page.set_default_timeout(2000)
        self.page.set_content(FIXTURE_HTML)

    def tearDown(self):
        self.context.close()

    def write(self, plans, fingerprint=None):
        return _write_fixture_page(self.page, plans, fingerprint or inspect_shadow_form(self.page)['schema_fingerprint'])

    def assert_no_consequences(self, result):
        self.assertEqual([result[k] for k in ['file_upload_attempts', 'submit_attempts', 'next_attempts']], [0,0,0])
        self.assertFalse(result['live_execution_ready'])
        self.assertEqual(self.page.locator('input[type=file]').evaluate_all('(els)=>els.map(e=>e.files.length)'), [0,0])
        events = self.page.evaluate('window.events')
        self.assertEqual(events['click'], 0)
        self.assertEqual(events['submit'], 0)

    def test_scoped_writes_nested_hosts_and_replay(self):
        plans = (plan(), plan('email', 'fixture@example.test', 'Email', 'email', canonical='candidate.email'),
                 plan('phone', '+90 555 000 0000', 'Phone', 'tel', ('profile','details'), 'candidate.phone'))
        result = self.write(plans)
        self.assertEqual(result['runtime_state'], 'fixture_prefill_verified')
        self.assertEqual(result['form_value_write_attempts'], 3)
        self.assertEqual(result['verified_fields'], 3)
        self.assertEqual(self.page.locator('#other').locator('#first').input_value(), 'UNTOUCHED')
        self.assertEqual(self.page.locator('#profile').locator('#first').input_value(), 'Fixture')
        replay = self.write(plans)
        self.assertEqual(replay['form_value_write_attempts'], 0)
        self.assertEqual(replay['verified_fields'], 3)
        self.assert_no_consequences(result)

    def test_all_plans_preflight_before_writing(self):
        for bad, error in [
            (plan('missing'), 'CONTROL_NOT_UNIQUE'),
            (plan(label='Wrong label'), 'CONTROL_LABEL_DRIFT'),
            (plan(kind='email'), 'CONTROL_TYPE_DRIFT'),
            (plan(value='x'*81), 'MAX_LENGTH_EXCEEDED'),
            (plan(hosts=('missing',)), 'HOST_NOT_UNIQUE_OR_OPEN'),
        ]:
            with self.subTest(error=error):
                good = plan('email', 'fixture@example.test', 'Email', 'email', canonical='candidate.email')
                result = self.write((good,bad))
                self.assertEqual(result['error_code'], error)
                self.assertEqual(result['form_value_write_attempts'], 0)
                self.assertEqual(self.page.locator('#email').input_value(), '')
                self.assert_no_consequences(result)

    def test_disabled_hidden_and_combobox_blocked(self):
        for input_id, label in [('disabled','Disabled'), ('hidden','Hidden')]:
            self.page.locator('#'+input_id).evaluate('(el,label)=>el.setAttribute("aria-label",label)', label)
            result = self.write((plan(input_id, label=label, hosts=('profile','details')),))
            self.assertEqual(result['error_code'], 'CONTROL_UNAVAILABLE')
            self.assertEqual(result['form_value_write_attempts'], 0)
        self.page.locator('#email').evaluate('(el)=>el.setAttribute("role","combobox")')
        result = self.write((plan('email','fixture@example.test','Email','email',canonical='candidate.email'),))
        self.assertEqual(result['error_code'], 'CUSTOM_CONTROL_REQUIRES_ADAPTER')

    def test_stale_schema_and_duplicate_locators_blocked(self):
        before = inspect_shadow_form(self.page)['schema_fingerprint']
        self.page.locator('#profile').evaluate('(el)=>el.shadowRoot.appendChild(el.shadowRoot.getElementById("first").cloneNode())')
        result = self.write((plan(),), before)
        self.assertEqual(result['error_code'], 'SCHEMA_DRIFT')
        result = self.write((plan(),))
        self.assertEqual(result['error_code'], 'CONTROL_NOT_UNIQUE')
        self.assertEqual(result['form_value_write_attempts'], 0)

    def test_input_rewrite_stops_and_preserves_attempt_count(self):
        self.page.locator('#profile').locator('#first').evaluate('(el)=>el.addEventListener("input",()=>el.value="REWRITTEN")')
        result = self.write((plan(value='PRIVATE_SENTINEL'),))
        self.assertEqual(result['error_code'], 'READBACK_MISMATCH')
        self.assertEqual(result['form_value_write_attempts'], 1)
        self.assertEqual(result['verified_fields'], 0)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result))
        self.assert_no_consequences(result)

    def test_invalid_email_not_reported_successful(self):
        result = self.write((plan('email','invalid','Email','email',canonical='candidate.email'),))
        self.assertEqual(result['error_code'], 'READBACK_MISMATCH')
        self.assertEqual(result['form_value_write_attempts'], 1)

    def test_schema_changed_by_first_input_stops_second_write(self):
        self.page.locator('#profile').locator('#first').evaluate('''el=>el.addEventListener('input',()=>{
          el.getRootNode().getElementById('email').disabled=true;
        })''')
        result = self.write((plan(), plan('email','fixture@example.test','Email','email',canonical='candidate.email')))
        self.assertEqual(result['error_code'], 'SCHEMA_DRIFT')
        self.assertEqual(result['form_value_write_attempts'], 1)
        self.assertEqual(self.page.locator('#email').input_value(), '')

    def test_non_fixture_page_rejected(self):
        self.page.goto('data:text/html,<input id="first">')
        result = self.write((plan(),))
        self.assertEqual(result['error_code'], 'FIXTURE_ONLY')
        self.assertEqual(result['form_value_write_attempts'], 0)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is not installed')
class OwnedFixtureBrowserTests(unittest.TestCase):
    def test_owned_fixture_entry_point(self):
        result = run_shadow_fixture((plan(),))
        self.assertEqual(result['runtime_state'], 'fixture_prefill_verified')
        self.assertFalse(result['live_execution_ready'])


if __name__ == '__main__':
    unittest.main()
