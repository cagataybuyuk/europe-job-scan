import unittest

from ejs.services.smartrecruiters_live_inspector import LiveInspectionRequest, validate_live_url


class LiveInspectionPolicyTests(unittest.TestCase):
    def test_only_https_smartrecruiters_urls_are_accepted(self):
        for url in (
            "http://jobs.smartrecruiters.com/acme/job",
            "https://example.com/job",
            "https://user:pass@jobs.smartrecruiters.com/acme/job",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_live_url(url)
        validate_live_url("https://jobs.smartrecruiters.com/Version1/job")
        validate_live_url("https://jobs.smartrecruiters.com")

    def test_timeout_is_checked_before_browser_start(self):
        from ejs.services.smartrecruiters_live_inspector import inspect_live_page
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 500))
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 60_001))


if __name__ == "__main__": unittest.main()
