from pathlib import Path
import importlib.util
import unittest

from ejs.services.shadow_fixture_step_adapter import FixtureNextPlan, FixtureSelectPlan, advance_fixture_next, select_fixture_option
from ejs.services.smartrecruiters_shadow import inspect_shadow_form


HTML = '''<div id="form"></div><script>
const root = document.getElementById('form').attachShadow({mode:'open'});
root.innerHTML = '<label for="city">City</label><select id="city"><option>Choose</option><option>Dublin</option><option>Amsterdam</option></select><button id="next" type="button">Next</button>';
root.getElementById('next').addEventListener('click',()=>{root.innerHTML='<label for="country">Country</label><select id="country"><option>Ireland</option><option>Netherlands</option></select><button id="done" type="button">Review</button>';});
</script>'''


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright is not installed')
class StepAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(offline=True, service_workers='block')
        self.context.route('**/*', lambda route: route.abort())
        self.page = self.context.new_page(); self.page.set_content(HTML)

    def tearDown(self): self.context.close()

    def test_exact_city_selection_and_one_next_transition(self):
        before = inspect_shadow_form(self.page)['schema_fingerprint']
        r = select_fixture_option(self.page, FixtureSelectPlan(('form',), 'city', 'City', 'Dublin'), before)
        self.assertEqual(r['runtime_state'], 'fixture_select_verified'); self.assertEqual(r['select_attempts'], 1)
        after = inspect_shadow_form(self.page)['schema_fingerprint']
        r2 = select_fixture_option(self.page, FixtureSelectPlan(('form',), 'city', 'City', 'Dublin'), after)
        self.assertEqual(r2['select_attempts'], 0)
        next_schema = inspect_shadow_form(self.page)['schema_fingerprint']
        # The expected schema is captured after transition by an independent read,
        # then the actual transition is tested with a fresh page below.
        self.page.reload(); self.page.set_content(HTML)
        before = inspect_shadow_form(self.page)['schema_fingerprint']
        self.page.locator('#form').evaluate("el => el.shadowRoot.getElementById('next').addEventListener('click', () => {})")
        # next schema is deterministic for this fixture; derive it on a second page.
        probe = self.browser.new_page(); probe.set_content(HTML); probe.locator('#form').locator('#next').click(); expected_next = inspect_shadow_form(probe)['schema_fingerprint']; probe.close()
        r3 = advance_fixture_next(self.page, FixtureNextPlan(('form',), 'next'), before, expected_next)
        self.assertEqual(r3['runtime_state'], 'fixture_next_verified'); self.assertEqual(r3['next_attempts'], 1)
        self.assertEqual(r3['submit_attempts'], 0); self.assertEqual(r3['file_upload_attempts'], 0)

    def test_mismatch_and_ambiguous_controls_stop_before_selection(self):
        before = inspect_shadow_form(self.page)['schema_fingerprint']
        for plan, code in [(FixtureSelectPlan(('form',), 'city', 'City', 'Berlin'), 'OPTION_NOT_OBSERVED'),
                           (FixtureSelectPlan(('form',), 'city', 'Wrong', 'Dublin'), 'CONTROL_LABEL_DRIFT'),
                           (FixtureNextPlan(('form',), 'missing'), 'NEXT_NOT_UNIQUE')]:
            r = select_fixture_option(self.page, plan, before) if isinstance(plan, FixtureSelectPlan) else advance_fixture_next(self.page, plan, before, 'unused')
            self.assertEqual(r['error_code'], code); self.assertEqual(r.get('select_attempts', 0), 0); self.assertEqual(r.get('next_attempts', 0), 0)

    def test_stale_next_schema_stops_after_click_and_never_submits(self):
        before = inspect_shadow_form(self.page)['schema_fingerprint']
        r = advance_fixture_next(self.page, FixtureNextPlan(('form',), 'next'), before, 'stale-schema')
        self.assertEqual(r['error_code'], 'NEXT_SCHEMA_DRIFT'); self.assertEqual(r['next_attempts'], 1); self.assertFalse(r['live_execution_ready'])


if __name__ == '__main__': unittest.main()
