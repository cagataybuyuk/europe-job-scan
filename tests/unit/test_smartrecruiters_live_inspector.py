import unittest

from ejs.services.smartrecruiters_live_inspector import (
    LiveInspectionRequest,
    combine_shadow_reports,
    discovery_state,
    same_origin_url,
    scope_shadow_report,
    validate_live_url,
)


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

    def test_timeouts_are_checked_before_browser_start(self):
        from ejs.services.smartrecruiters_live_inspector import inspect_live_page
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 500))
        with self.assertRaisesRegex(ValueError, "INVALID_INSPECTION_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", 60_001))
        with self.assertRaisesRegex(ValueError, "INVALID_RENDER_WAIT_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", render_wait_ms=500))
        with self.assertRaisesRegex(ValueError, "INVALID_RENDER_WAIT_TIMEOUT"):
            inspect_live_page(LiveInspectionRequest("https://jobs.smartrecruiters.com/acme/job", render_wait_ms=15_001))

    def test_same_origin_is_explicit_and_default_ports_are_normalized(self):
        parent = "https://jobs.smartrecruiters.com/oneclick-ui/acme"
        self.assertTrue(same_origin_url(parent, "https://jobs.smartrecruiters.com/frame"))
        self.assertTrue(same_origin_url(parent, "https://jobs.smartrecruiters.com:443/frame"))
        self.assertTrue(same_origin_url(parent, "about:blank"))
        self.assertFalse(same_origin_url(parent, "https://example.smartrecruiters.com/frame"))
        self.assertFalse(same_origin_url(parent, "https://example.com/frame"))

    def test_scoped_frame_reports_get_unique_observation_identities(self):
        base = {
            "controls": [{
                "scope": "document",
                "observation_key": "document/input@1",
                "tag": "input",
                "type": "text",
                "id": "firstName",
                "name": "firstName",
                "label": "First name",
                "visible": True,
                "disabled": False,
                "required": True,
                "host_required_hint": False,
                "accept": "",
                "multiple": False,
            }],
            "actions": [],
        }
        main = scope_shadow_report(base, "document")
        frame = scope_shadow_report(base, "frame:1/document")
        combined = combine_shadow_reports([main, frame])
        keys = [item["observation_key"] for item in combined["controls"]]
        self.assertEqual(keys, ["document/input@1", "frame:1/document/input@1"])
        self.assertEqual(len(keys), len(set(keys)))

    def test_empty_native_form_is_a_fail_closed_discovery_boundary(self):
        self.assertEqual(
            discovery_state({"controls": [], "actions": []}),
            ("form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED"),
        )
        self.assertEqual(
            discovery_state({"controls": [{"observation_key": "document/input@0"}], "actions": []}),
            ("inspected", ""),
        )


if __name__ == "__main__":
    unittest.main()
