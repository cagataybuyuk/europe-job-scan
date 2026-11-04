"""MVP-02 deny-by-default redacted network diagnostics regression."""
import unittest

from ejs.services.smartrecruiters_live_inspector import summarize_blocked_request


class NetworkEvidenceTests(unittest.TestCase):
    def test_post_xhr_is_denied_without_sensitive_data(self):
        secret = "private-candidate-token-DO-NOT-LOG"
        url = f"https://jobs.smartrecruiters.com/oneclick-ui?token={secret}"
        result = summarize_blocked_request("POST", "xhr", url, False)
        self.assertEqual(result["deny_reason"], "NON_GET_REQUEST")
        self.assertFalse(result["permission_granted"])
        self.assertNotIn(secret, str(result))
        self.assertNotIn("token=", str(result))

    def test_untrusted_redirect_redacts_host_and_credentials(self):
        secret = "credential-secret"
        result = summarize_blocked_request("GET", "document", f"https://user:{secret}@evil.example/redirect?token=abc", True)
        self.assertEqual(result["host"], "external_redacted")
        self.assertEqual(result["trust_class"], "untrusted_or_external")
        self.assertEqual(result["deny_reason"], "UNTRUSTED_NAVIGATION")
        self.assertNotIn(secret, str(result))
        self.assertNotIn("evil.example", str(result))

    def test_https_and_no_userinfo_required_for_trust(self):
        for url in (
            "http://jobs.smartrecruiters.com/oneclick-ui",
            "https://user:secret@jobs.smartrecruiters.com/oneclick-ui",
            "https://jobs.smartrecruiters.com.evil.example/oneclick-ui",
        ):
            with self.subTest(url=url):
                result = summarize_blocked_request("GET", "document", url, True)
                self.assertEqual(result["trust_class"], "untrusted_or_external")
                self.assertEqual(result["host"], "external_redacted")

    def test_known_https_host_does_not_grant_post_permission(self):
        result = summarize_blocked_request("POST", "fetch", "https://jobs.smartrecruiters.com/submit", False)
        self.assertEqual(result["trust_class"], "trusted_https_host")
        self.assertFalse(result["permission_granted"])
        self.assertEqual(result["purpose_hypothesis"], "non_read_request_unknown")

    def test_unknown_method_never_grants_permission(self):
        result = summarize_blocked_request("TRACE", "other", "https://outside.example/", False)
        self.assertEqual(result["method"], "OTHER")
        self.assertEqual(result["deny_reason"], "NON_GET_REQUEST")
        self.assertFalse(result["permission_granted"])


if __name__ == "__main__":
    unittest.main()
