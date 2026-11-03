from unittest import TestCase
from unittest.mock import MagicMock, patch
from ejs.services import adp_email_verification_canary as canary

URL = 'https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=test&ccId=one&jobId=123'
CLEAR = {'auth_observed': False, 'captcha_observed': False, 'form': {'controls': [{'id': 'oneTimePassWord', 'visible': True, 'scope': 'document', 'type': 'text', 'required': True, 'label': 'Enter the Verification Code'}]}}


def message(**overrides):
    return {'sender': 'ADP <notice@adp.com>', 'sender_authenticated': True,
            'recipients': ['fixture@example.invalid'], 'received_at': 110,
            'subject': 'ADP verification code', 'body_text': 'Your verification code is 123456.', **overrides}


def select(messages=None, **overrides):
    args = {'recipient': 'fixture@example.invalid', 'challenge_started_at': 100,
            'now': 120, 'transfer_authorized': True, 'mailbox_matches': True, **overrides}
    return canary.select_fresh_adp_code(messages if messages is not None else [message()], **args)


class AdpEmailCodeTests(TestCase):
    def test_matching_authenticated_fresh_code_is_opaque_and_one_use(self):
        material = select()
        self.assertNotIn('123456', repr(material))
        self.assertEqual(material.take_once(121), '123456')
        with self.assertRaises(PermissionError): material.take_once(122)

    def test_authorization_mailbox_and_freshness_are_required(self):
        for args in ({'transfer_authorized': False}, {'mailbox_matches': False}, {'now': 90}, {'now': 401}):
            with self.subTest(args=args), self.assertRaises(PermissionError): select(**args)

    def test_wrong_sender_recipient_auth_time_subject_and_ambiguity_are_rejected(self):
        for changes in ({'sender': 'notice@adp.com.attacker.invalid'}, {'sender_authenticated': False},
                        {'recipients': ['other@example.invalid']}, {'received_at': 99},
                        {'received_at': 121}, {'subject': 'Job 123456'},
                        {'body_text': 'verification code is 123456 and verification code is 654321'}, {'body_text': 'Requisition: 123456'}):
            with self.subTest(changes=changes), self.assertRaises(PermissionError):
                select([message(**changes)])
        with self.assertRaises(PermissionError): select([message(), message(body_text='Your verification code is 654321.')])

    def test_transfer_uses_only_reviewed_otp_and_verify_then_requires_reinspection(self):
        page = MagicMock(); page.url = URL
        otp = page.locator.return_value; otp.count.return_value = 1
        otp.is_visible.return_value = otp.is_enabled.return_value = True
        otp.input_value.return_value = ''
        verify = page.get_by_role.return_value; verify.count.return_value = 1
        verify.is_visible.return_value = verify.is_enabled.return_value = True
        counters = {'otp_write_attempts': 0, 'verify_click_attempts': 0}
        with patch.object(canary, '_snapshot', return_value=CLEAR):
            result = canary.enter_adp_email_code(page, select(), application_url=URL,
                transfer_authorized=True, now=121, counters=counters)
        otp.fill.assert_called_once_with('123456'); verify.click.assert_called_once()
        self.assertTrue(result['requires_reinspection'])
        self.assertNotIn('123456', repr(result))
        self.assertEqual(counters, {'otp_write_attempts': 1, 'verify_click_attempts': 1})

    def test_target_drift_after_otp_fill_blocks_verify(self):
        page = MagicMock(); page.url = URL
        otp = page.locator.return_value; otp.count.return_value = 1
        otp.is_visible.return_value = otp.is_enabled.return_value = True
        otp.input_value.return_value = ''
        otp.fill.side_effect = lambda value: setattr(page, 'url', URL.replace('jobId=123', 'jobId=456'))
        with patch.object(canary, '_snapshot', return_value=CLEAR), self.assertRaisesRegex(PermissionError, 'TARGET_MISMATCH'):
            canary.enter_adp_email_code(page, select(), application_url=URL,
                transfer_authorized=True, now=121, counters={'otp_write_attempts': 0, 'verify_click_attempts': 0})
        page.get_by_role.return_value.click.assert_not_called()

    def test_browser_failure_does_not_expose_code_payload(self):
        page = MagicMock(); page.url = URL
        otp = page.locator.return_value; otp.count.return_value = 1
        otp.is_visible.return_value = otp.is_enabled.return_value = True
        otp.input_value.return_value = ''
        otp.fill.side_effect = RuntimeError('PRIVATE 123456')
        with patch.object(canary, '_snapshot', return_value=CLEAR):
            with self.assertRaises(PermissionError) as error:
                canary.enter_adp_email_code(page, select(), application_url=URL,
                    transfer_authorized=True, now=121, counters={'otp_write_attempts': 0, 'verify_click_attempts': 0})
        self.assertNotIn('123456', str(error.exception))

    def test_unreviewed_otp_surface_blocks_before_consuming_or_writing_code(self):
        page = MagicMock(); page.url = URL
        material = select()
        with patch.object(canary, '_snapshot', return_value={'auth_observed': False, 'captcha_observed': False}):
            with self.assertRaisesRegex(PermissionError, 'SURFACE_NOT_REVIEWED'):
                canary.enter_adp_email_code(page, material, application_url=URL, transfer_authorized=True,
                    now=121, counters={'otp_write_attempts': 0, 'verify_click_attempts': 0})
        page.locator.return_value.fill.assert_not_called()
        self.assertEqual(material.take_once(122), '123456')
