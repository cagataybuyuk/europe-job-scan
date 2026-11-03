from unittest import TestCase
from unittest.mock import MagicMock, patch
from ejs.services import preferred_entry_adapter as adapter

ADP = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=test&ccId=one&jobId=123'
SR = 'https://jobs.smartrecruiters.com/Synthetic/744000000000001-fixture'


class PreferredEntryAdapterTests(TestCase):
    def test_only_exact_reviewed_target_is_accepted(self):
        for target in (ADP, SR):
            page = MagicMock(); page.url = target
            adapter.require_application_target(page, target)
            for bad in (target.replace('https:', 'http:'), target.replace('.com/', '.com:444/'),
                        target.replace('jobId=123', 'jobId=456') if target == ADP else target.replace('000001-', '000002-')):
                page.url = bad
                with self.assertRaises(PermissionError):
                    adapter.require_application_target(page, target)

    def test_inspection_and_activation_use_fresh_adapter_callbacks(self):
        page = MagicMock(); page.url = SR
        action = {'observation_key': 'document/button@1', 'scope': 'document',
                  'label': 'Apply with LinkedIn', 'visible': True, 'disabled': False}
        locator = MagicMock(); locator.count.return_value = 1
        locator.is_visible.return_value = locator.is_enabled.return_value = True
        locator.get_attribute.return_value = 'Apply with LinkedIn'
        page.evaluate.return_value = [action]
        snapshot = {'auth_observed': False, 'captcha_observed': False, 'form': {'actions': [action]}}
        with patch.object(adapter, '_snapshot', return_value=snapshot), patch.object(adapter, '_resolve_document_locator', return_value=locator):
            inspected = adapter.inspect_preferred_entry(page, SR)
            result = adapter.use_preferred_entry(page, SR, expected_surface_fingerprint=inspected['surface_fingerprint'],
                                                counters={'awli_click_attempts': 0, 'awli_click_successes': 0})
        locator.click.assert_called_once()
        self.assertEqual(result['route'], 'awli_started_requires_reinspection')

