from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from ejs.services import adp_entry_autopilot as autopilot
from ejs.services import adp_profile_v2_continue_canary as fields
from ejs.services.adp_entry_autopilot import (
    AdpEntryAutopilotRequest,
    advance_to_otp_on_existing_page,
    click_complete_application_on_portal,
    click_verify_if_user_populated_otp,
)

URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?cid=test&ccId=19000101_000001&jobId=955507"
)


class AdpEntryAutopilotTests(unittest.TestCase):
    def request(self):
        return AdpEntryAutopilotRequest(
            application_url=URL,
            profile_json_path="profile.json",
        )

    def _drive_reviewed_entry(self, entry_result=None):
        page = MagicMock()
        page.url = URL

        apply_locator = MagicMock()
        apply_locator.is_visible.return_value = True
        apply_locator.is_enabled.return_value = True
        apply_locator.get_attribute.return_value = "Apply"

        continue_button = MagicMock()
        continue_button.count.return_value = 1
        continue_button.is_visible.return_value = True
        continue_button.is_enabled.return_value = True
        page.get_by_role.return_value = continue_button

        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_enabled.return_value = True
        page.locator.return_value = otp

        profile = MagicMock()
        snapshots = [
            {"captcha_observed": False, "auth_observed": False},
            {"captcha_observed": False, "auth_observed": False},
            {"captcha_observed": False, "auth_observed": False},
        ]

        with patch.object(autopilot, "load_resolved_profile", return_value=profile), patch.object(
            autopilot, "_snapshot", side_effect=snapshots
        ), patch.object(
            autopilot, "navigation_surface_fingerprint", return_value=autopilot.EXPECTED_NAVIGATION_FP
        ), patch.object(
            autopilot, "preference_surface_descriptor", return_value={"banner_visible": False}
        ), patch.object(
            autopilot, "_approved_entry", return_value={"observation_key": "document/button@1"}
        ), patch.object(
            autopilot, "_resolve_document_locator", return_value=apply_locator
        ), patch.object(
            autopilot, "safe_fill_surface_fingerprint", return_value=autopilot.EXPECTED_SAFE_FILL_FP
        ), patch.object(
            autopilot, "phone_contract_descriptor", return_value={}
        ), patch.object(
            autopilot, "phone_contract_surface_fingerprint", return_value=autopilot.EXPECTED_PHONE_FP
        ), patch.object(
            autopilot, "_write_identity_fields", return_value=[{}, {}, {}]
        ) as identity_writer, patch.object(
            autopilot, "_write_phone_fields", return_value={"phone_readback_match": True}
        ) as phone_writer, patch.object(
            autopilot, "action_surface_descriptor", return_value={}
        ), patch.object(
            autopilot, "action_surface_fingerprint", return_value=autopilot.EXPECTED_ACTION_FP
        ):
            handler = MagicMock(return_value=entry_result) if entry_result is not None else None
            report = advance_to_otp_on_existing_page(page, self.request(), preferred_entry_handler=handler)
        return report, identity_writer, phone_writer, continue_button, handler

    def test_advance_to_otp_uses_reviewed_actions_and_stops_at_otp(self):
        report, *_ = self._drive_reviewed_entry()
        self.assertEqual(report["autopilot_status"], "waiting_for_otp")
        self.assertEqual(report["apply_click_attempts"], 1)
        self.assertEqual(report["apply_click_successes"], 1)
        self.assertEqual(report["continue_click_attempts"], 1)
        self.assertEqual(report["continue_click_successes"], 1)
        self.assertEqual(report["otp_write_attempts"], 0)
        self.assertEqual(report["next_click_attempts"], 0)
        self.assertEqual(report["file_upload_attempts"], 0)
        self.assertEqual(report["submit_attempts"], 0)
        self.assertFalse(report["raw_values_exposed"])

    def test_awli_entry_stops_native_identity_phone_and_continue(self):
        report, identity, phone, continue_button, handler = self._drive_reviewed_entry(
            {'route': 'awli_started_requires_reinspection', 'final_submit_allowed': False})
        handler.assert_called_once()
        identity.assert_not_called()
        phone.assert_not_called()
        continue_button.click.assert_not_called()
        self.assertEqual(report['autopilot_status'], 'awli_started_requires_reinspection')
        self.assertEqual(report['form_value_write_attempts'], 0)
        self.assertEqual(report['continue_click_attempts'], 0)
        self.assertFalse(report['final_submit_allowed'])

    def test_unclear_entry_preference_blocks_native_writes(self):
        with self.assertRaisesRegex(PermissionError, 'PREFERENCE_REQUIRES_REVIEW'):
            self._drive_reviewed_entry({'route': 'human_review'})

    def test_identity_target_drift_after_fill_stops_before_readback_or_next_field(self):
        page = MagicMock()
        page.url = URL
        locator = page.locator.return_value
        locator.count.return_value = 1
        locator.is_visible.return_value = True
        locator.is_enabled.return_value = True
        locator.input_value.return_value = ''
        locator.fill.side_effect = lambda value: setattr(page, 'url', URL.replace('955507', '999999'))
        profile = MagicMock(first_name='Synthetic', last_name='Test', email='test@example.invalid')
        counters = autopilot._base_counters()
        with patch.object(fields, '_validate_target_control'):
            with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
                fields._write_identity_fields(page, {}, profile, counters,
                    before_field_access=lambda: autopilot._require_target(page, URL))
        self.assertEqual(locator.fill.call_count, 1)
        self.assertEqual(locator.input_value.call_count, 1)
        self.assertEqual(page.locator.call_count, 1)
        self.assertEqual(counters['form_value_write_attempts'], 1)

    def test_identity_drift_during_pre_read_stops_before_first_write(self):
        page = MagicMock()
        page.url = URL
        locator = page.locator.return_value
        locator.count.return_value = 1
        locator.is_visible.return_value = True
        locator.is_enabled.return_value = True
        def drift():
            page.url = URL.replace('955507', '999999')
            return ''
        locator.input_value.side_effect = drift
        profile = MagicMock(first_name='Synthetic', last_name='Test', email='test@example.invalid')
        counters = autopilot._base_counters()
        with patch.object(fields, '_validate_target_control'):
            with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
                fields._write_identity_fields(page, {}, profile, counters,
                    before_field_access=lambda: autopilot._require_target(page, URL))
        locator.fill.assert_not_called()
        self.assertEqual(counters['form_value_write_attempts'], 0)

    def test_phone_target_drift_after_country_selection_blocks_phone_access(self):
        page = MagicMock()
        page.url = URL
        country, phone = MagicMock(), MagicMock()
        page.locator.side_effect = [country, phone]
        for locator in (country, phone):
            locator.count.return_value = 1
            locator.is_visible.return_value = True
            locator.is_enabled.return_value = True
        country.locator.return_value.count.return_value = 1
        country.locator.return_value.is_disabled.return_value = False
        country.input_value.return_value = 'GB'
        country.select_option.side_effect = lambda **kwargs: setattr(page, 'url', URL.replace('955507', '999999'))
        profile = MagicMock(phone_country_iso2='TR', phone_national_number='5550000000')
        counters = autopilot._base_counters()
        with self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
            fields._write_phone_fields(page, profile, counters,
                before_field_access=lambda: autopilot._require_target(page, URL))
        country.select_option.assert_called_once_with(value='TR')
        phone.input_value.assert_not_called()
        phone.fill.assert_not_called()
        self.assertEqual(counters['form_value_write_attempts'], 1)

    def test_verify_click_requires_user_populated_six_digit_otp_and_never_returns_value(self):
        page = MagicMock()
        page.url = URL
        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_visible.return_value = True
        otp.is_enabled.return_value = True
        otp.input_value.return_value = "123456"
        page.locator.return_value = otp

        verify = MagicMock()
        verify.count.return_value = 1
        verify.is_visible.return_value = True
        verify.is_enabled.return_value = True
        page.get_by_role.return_value = verify

        counters = {"verify_click_attempts": 0, "verify_click_successes": 0}
        with patch.object(autopilot,"_snapshot",return_value={"captcha_observed":False,"auth_observed":False}):
            clicked = click_verify_if_user_populated_otp(page, counters, application_url=URL)

        self.assertTrue(clicked)
        verify.click.assert_called_once()
        self.assertEqual(counters["verify_click_attempts"], 1)
        self.assertEqual(counters["verify_click_successes"], 1)
        self.assertNotIn("123456", repr(counters))

    def test_verify_does_not_click_for_incomplete_otp(self):
        page = MagicMock()
        page.url = URL
        otp = MagicMock()
        otp.count.return_value = 1
        otp.is_visible.return_value = True
        otp.is_enabled.return_value = True
        otp.input_value.return_value = "123"
        page.locator.return_value = otp

        counters = {"verify_click_attempts": 0, "verify_click_successes": 0}
        with patch.object(autopilot,"_snapshot",return_value={"captcha_observed":False,"auth_observed":False}):
            self.assertFalse(click_verify_if_user_populated_otp(page, counters, application_url=URL))
        page.get_by_role.assert_not_called()

    def test_complete_application_click_is_exactly_once(self):
        page = MagicMock()
        page.url = URL.replace(autopilot.ENTRY_PATH,autopilot.REVIEWED_POSTLOGIN_PATH)
        candidate = {
            "scope": "document",
            "observation_key": "document/button@9",
            "label": "Complete Your Application",
            "visible": True,
        }
        locator = MagicMock()
        locator.is_visible.return_value = True
        locator.is_enabled.return_value = True
        counters = {
            "complete_application_click_attempts": 0,
            "complete_application_click_successes": 0,
        }

        with patch.object(
            autopilot, "_target_binding", return_value={"target_bound": True}
        ), patch.object(
            autopilot, "_snapshot", return_value={"captcha_observed":False,"auth_observed":False,"form": {"actions": [candidate]}}
        ), patch.object(
            autopilot, "_resolve_document_locator", return_value=locator
        ):
            clicked = click_complete_application_on_portal(page, URL, counters)
            clicked_again = click_complete_application_on_portal(page, URL, counters)

        self.assertTrue(clicked)
        self.assertFalse(clicked_again)
        locator.click.assert_called_once()
        self.assertEqual(counters["complete_application_click_attempts"], 1)
        self.assertEqual(counters["complete_application_click_successes"], 1)

    def test_wrong_or_ambiguous_target_stops_before_profile_access(self):
        cases=[URL.replace('955507','955508'),URL.replace('cid=test','cid=other'),
               URL+'&jobId=955507',URL+'&jobId=',URL.replace('ccId=19000101_000001&',''),
               URL.replace('https://','http://'),URL.replace('workforcenow.adp.com','example.com'),
               URL.replace('workforcenow.adp.com','workforcenow.adp.com:444'),
               URL.replace(autopilot.ENTRY_PATH,'/unreviewed.html')]
        for target in cases:
            page=MagicMock();page.url=target
            with self.subTest(target=target),patch.object(autopilot,'load_resolved_profile') as profile:
                with self.assertRaisesRegex(PermissionError,'TARGET_MISMATCH'):
                    advance_to_otp_on_existing_page(page,self.request())
                profile.assert_not_called();page.locator.assert_not_called()

    def test_post_cookie_auth_or_unknown_evidence_stops_before_apply(self):
        clear={'captcha_observed':False,'auth_observed':False}
        for boundary in ({'captcha_observed':True,'auth_observed':False},
                         {'captcha_observed':False,'auth_observed':True},{},
                         {'captcha_observed':'false','auth_observed':False}):
            page=MagicMock();page.url=URL
            with self.subTest(boundary=boundary),patch.object(autopilot,'load_resolved_profile'),\
                 patch.object(autopilot,'_snapshot',side_effect=[clear,boundary]),\
                 patch.object(autopilot,'navigation_surface_fingerprint',return_value=autopilot.EXPECTED_NAVIGATION_FP),\
                 patch.object(autopilot,'_cookie_preflight'),patch.object(autopilot,'_approved_entry') as entry:
                with self.assertRaisesRegex(PermissionError,'POST_COOKIE_BOUNDARY'):
                    advance_to_otp_on_existing_page(page,self.request())
                entry.assert_not_called()

    def test_post_apply_retarget_stops_before_identity_and_phone_writes(self):
        page=MagicMock();page.url=URL
        clear={'captcha_observed':False,'auth_observed':False}
        calls=[]
        def snapshot(*args,**kwargs):
            calls.append(1)
            if len(calls)==3: page.url=URL.replace('955507','955508')
            return clear
        locator=MagicMock();locator.is_visible.return_value=True;locator.is_enabled.return_value=True
        locator.get_attribute.return_value='Apply'
        with patch.object(autopilot,'load_resolved_profile'),patch.object(autopilot,'_snapshot',side_effect=snapshot),\
             patch.object(autopilot,'navigation_surface_fingerprint',return_value=autopilot.EXPECTED_NAVIGATION_FP),\
             patch.object(autopilot,'_cookie_preflight'),\
             patch.object(autopilot,'_approved_entry',return_value={'observation_key':'document/button@1'}),\
             patch.object(autopilot,'_resolve_document_locator',return_value=locator),\
             patch.object(autopilot,'_write_identity_fields') as identity,\
             patch.object(autopilot,'_write_phone_fields') as phone:
            with self.assertRaisesRegex(PermissionError,'TARGET_MISMATCH'):
                advance_to_otp_on_existing_page(page,self.request())
            identity.assert_not_called();phone.assert_not_called()
            locator.click.assert_called_once()

    def test_verify_refuses_wrong_target_and_unknown_boundary_before_otp_read(self):
        for wrong_url,boundary in [(URL.replace('955507','955508'),{}),(URL,{}),
                                   (URL,{'captcha_observed':True,'auth_observed':False})]:
            page=MagicMock();page.url=wrong_url
            with self.subTest(url=wrong_url,boundary=boundary),patch.object(autopilot,'_snapshot',return_value=boundary):
                with self.assertRaises(PermissionError):
                    click_verify_if_user_populated_otp(page,{},application_url=URL)
            page.locator.assert_not_called();page.get_by_role.assert_not_called()

    def test_verify_target_drift_after_shape_check_stops_before_click(self):
        page=MagicMock();page.url=URL
        otp=MagicMock();otp.count.return_value=1;otp.is_visible.return_value=True;otp.is_enabled.return_value=True
        def value():
            page.url=URL.replace('955507','955508')
            return '123456'
        otp.input_value.side_effect=value;page.locator.return_value=otp
        button=MagicMock();button.count.return_value=1;button.is_visible.return_value=True;button.is_enabled.return_value=True
        page.get_by_role.return_value=button;counters={}
        with patch.object(autopilot,'_snapshot',return_value={'captcha_observed':False,'auth_observed':False}):
            with self.assertRaisesRegex(PermissionError,'TARGET_MISMATCH'):
                click_verify_if_user_populated_otp(page,counters,application_url=URL)
        button.click.assert_not_called();self.assertEqual(counters,{})

    def test_portal_auth_and_target_drift_cannot_click_complete(self):
        portal=URL.replace(autopilot.ENTRY_PATH,autopilot.REVIEWED_POSTLOGIN_PATH)
        for target,boundary in [(portal+'&jobId=955507',{}),(portal,{}),
                                (portal,{'captcha_observed':False,'auth_observed':True})]:
            page=MagicMock();page.url=target
            with self.subTest(target=target,boundary=boundary),patch.object(autopilot,'_snapshot',return_value=boundary),\
                 patch.object(autopilot,'_resolve_document_locator') as locator:
                with self.assertRaises(PermissionError): click_complete_application_on_portal(page,URL,{})
                locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
