from unittest import TestCase
from unittest.mock import MagicMock, patch

from ejs.services import adp_same_page_guard as guard

URL = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=test&ccId=one&jobId=123'
POSTLOGIN = URL.replace('/default/', '/applicant/').replace('recruitment.html', 'postLogin.html')
CLEAR = {'runtime_state': 'inspected', 'auth_observed': False, 'captcha_observed': False}


class SamePageGuardTests(TestCase):
    def test_redirect_after_first_write_blocks_second_write_and_value_read(self):
        page = MagicMock()
        page.url = POSTLOGIN
        first, second = MagicMock(), MagicMock()
        page.locator.side_effect = [first, second]
        first.fill.side_effect = lambda value: setattr(page, 'url', POSTLOGIN.replace('cid=test', 'cid=other'))
        protected = guard.guard_verified_page(page, URL)
        with patch.object(guard, '_snapshot', return_value=CLEAR):
            protected.locator('#first').fill('Synthetic')
            candidate = protected.locator('#second')
            with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
                candidate.fill('Fixture')
            with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
                candidate.input_value()
        first.fill.assert_called_once_with('Synthetic')
        second.fill.assert_not_called()
        second.input_value.assert_not_called()

    def test_each_locator_action_and_value_read_rechecks_boundary(self):
        for operation, args in [('click', ()), ('fill', ('Synthetic',)), ('select_option', ('TR',)),
                                ('press', ('Enter',)), ('input_value', ()), ('inner_text', ())]:
            page = MagicMock()
            page.url = POSTLOGIN
            protected = guard.guard_verified_page(page, URL)
            locator = protected.get_by_role('button').nth(0).locator('#child')
            for flag in ('auth_observed', 'captcha_observed'):
                for value in (True, None, 0, 'false'):
                    with self.subTest(operation=operation, flag=flag, value=value), patch.object(guard, '_snapshot', return_value={**CLEAR, flag: value}):
                        with self.assertRaisesRegex(PermissionError, 'BOUNDARY_UNCLEAR'):
                            getattr(locator, operation)(*args)
            getattr(page.get_by_role.return_value.nth.return_value.locator.return_value, operation).assert_not_called()

    def test_target_drift_during_snapshot_blocks_action(self):
        page = MagicMock()
        page.url = POSTLOGIN
        def drift(*args, **kwargs):
            page.url = POSTLOGIN.replace('jobId=123', 'jobId=456')
            return CLEAR
        with patch.object(guard, '_snapshot', side_effect=drift):
            with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
                guard.guard_verified_page(page, URL).locator('#first').click()
        page.locator.return_value.click.assert_not_called()

    def test_clear_target_delegates_and_nested_wrapping_does_not_drop_guard(self):
        page = MagicMock()
        page.url = POSTLOGIN
        protected = guard.guard_verified_page(page, URL)
        self.assertIs(guard.guard_verified_page(protected, URL), protected)
        with patch.object(guard, '_snapshot', return_value=CLEAR) as snapshots:
            protected.locator('#field').fill('Synthetic')
            protected.locator('#field').input_value()
        self.assertEqual(snapshots.call_count, 2)
        page.locator.return_value.fill.assert_called_once_with('Synthetic')
        with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
            guard.guard_verified_page(protected, URL.replace('jobId=123', 'jobId=456'))

    def test_guard_does_not_enable_navigation_or_upload(self):
        page = MagicMock()
        protected = guard.guard_verified_page(page, URL)
        with self.assertRaisesRegex(PermissionError, 'NAVIGATION_FORBIDDEN'):
            protected.goto(URL)
        with self.assertRaisesRegex(PermissionError, 'UNSUPPORTED'):
            protected.locator('#file').set_input_files('fixture.pdf')
        page.goto.assert_not_called()
        page.locator.return_value.set_input_files.assert_not_called()

    def test_all_mutating_entrypoints_install_the_guard_before_work(self):
        import importlib, inspect
        from types import SimpleNamespace
        entries = {
            'safe_fill': 'run_on_verified_page',
            'personal_information_safe_fill': 'run_on_verified_page',
            'country_combobox_probe': 'inspect_country_combobox_on_verified_page',
            'country_dom_contract': 'inspect_on_verified_page',
            'country_keyboard_selection_canary': 'run_on_verified_page',
            'state_after_country_contract': 'inspect_after_reviewed_country_selection',
            'state_selection_canary': 'run_on_verified_page',
        }
        for suffix, name in entries.items():
            module = importlib.import_module('ejs.services.adp_same_page_' + suffix)
            fn = getattr(module, name)
            args = []
            for parameter in inspect.signature(fn).parameters.values():
                if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                    continue
                args.append(SimpleNamespace(application_url=URL) if parameter.name == 'request'
                            else URL if parameter.name == 'application_url' else MagicMock())
            with self.subTest(entry=suffix), patch.object(module, 'guard_verified_page', side_effect=PermissionError('GUARD_WIRING_SENTINEL')) as install:
                with self.assertRaisesRegex(PermissionError, 'GUARD_WIRING_SENTINEL'):
                    fn(*args)
                install.assert_called_once()
