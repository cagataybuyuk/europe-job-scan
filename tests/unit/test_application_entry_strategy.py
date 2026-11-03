from copy import deepcopy
from unittest import TestCase
from unittest.mock import MagicMock

from ejs.services.application_entry_strategy import (
    choose_application_entry, entry_surface_fingerprint, activate_preferred_awli,
)


def observation(label='Apply with LinkedIn'):
    return {'target_bound': True, 'auth_observed': False, 'captcha_observed': False,
            'entry_attempts': 0, 'observation_complete': True, 'actions': [{'observation_key': 'document/button@2',
            'scope': 'document', 'label': label, 'visible': True, 'disabled': False}]}


class ApplicationEntryTests(TestCase):
    def test_awli_is_preferred_over_native_apply(self):
        seen = observation()
        seen['actions'].append({**seen['actions'][0], 'label': 'Apply', 'observation_key': 'document/button@3'})
        choice = choose_application_entry(seen)
        self.assertEqual(choice['route'], 'awli')
        self.assertTrue(choice['profile_verification_required'])
        self.assertFalse(choice['safe_fill_allowed'])
        self.assertFalse(choice['final_submit_allowed'])

    def test_signin_and_easy_apply_are_not_awli(self):
        for label in ('Sign in with LinkedIn', 'LinkedIn Easy Apply', 'LinkedIn', 'Apply'):
            self.assertEqual(choose_application_entry(observation(label))['route'], 'native')

    def test_unknown_boundary_duplicate_hidden_disabled_and_retry_fail_closed(self):
        for key in ('target_bound', 'auth_observed', 'captcha_observed'):
            seen = observation(); seen.pop(key)
            self.assertEqual(choose_application_entry(seen)['route'], 'human_review')
        for value in (None, True, 1, -1):
            seen = observation(); seen['entry_attempts'] = value
            self.assertEqual(choose_application_entry(seen)['route'], 'human_review')
        for changes in ({'disabled': True}, {'disabled': None}, {'scope': 'unreviewed_frame'}):
            seen = observation(); seen['actions'][0].update(changes)
            self.assertEqual(choose_application_entry(seen)['route'], 'human_review')
        seen = observation(); seen['actions'] *= 2
        self.assertEqual(choose_application_entry(seen)['route'], 'human_review')
        seen = observation(); seen['actions'][0]['visible'] = False
        self.assertEqual(choose_application_entry(seen)['route'], 'native')

    def test_exactly_one_click_then_reinspect_without_native_fallback(self):
        seen = observation(); action = MagicMock()
        action.count.return_value = 1
        action.is_visible.return_value = action.is_enabled.return_value = True
        action.get_attribute.return_value = 'Apply with LinkedIn'
        counters = {'awli_click_attempts': 0, 'awli_click_successes': 0}
        inspect, target, resolve = MagicMock(return_value=seen), MagicMock(), MagicMock(return_value=action)
        result = activate_preferred_awli(inspect_current=inspect, require_target=target,
            resolve_action=resolve, expected_surface_fingerprint=entry_surface_fingerprint(seen), counters=counters)
        action.click.assert_called_once()
        self.assertEqual(target.call_count, 2)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(result['route'], 'awli_started_requires_reinspection')
        self.assertFalse(result['native_fallback_allowed'])
        self.assertFalse(result['oauth_grant_allowed'])
        self.assertTrue(result['profile_verification_required'])
        with self.assertRaisesRegex(PermissionError, 'RETRY_NOT_AUTHORIZED'):
            activate_preferred_awli(inspect_current=inspect, require_target=target,
                resolve_action=resolve, expected_surface_fingerprint=entry_surface_fingerprint(seen), counters=counters)

    def test_late_boundary_change_blocks_click(self):
        seen = observation(); changed = deepcopy(seen); changed['captcha_observed'] = True
        action = MagicMock(); action.count.return_value = 1
        action.is_visible.return_value = action.is_enabled.return_value = True
        action.get_attribute.return_value = 'Apply with LinkedIn'
        with self.assertRaisesRegex(PermissionError, 'SURFACE_DRIFT'):
            activate_preferred_awli(inspect_current=MagicMock(side_effect=[seen, changed]),
                require_target=MagicMock(), resolve_action=MagicMock(return_value=action),
                expected_surface_fingerprint=entry_surface_fingerprint(seen),
                counters={'awli_click_attempts': 0})
        action.click.assert_not_called()

    def test_loading_screen_never_means_awli_is_absent(self):
        seen = observation('Apply')
        for value in (None, False):
            seen['observation_complete'] = value
            result = choose_application_entry(seen)
            self.assertEqual(result['route'], 'human_review')
            self.assertEqual(result['reason'], 'ENTRY_SURFACE_NOT_READY')
