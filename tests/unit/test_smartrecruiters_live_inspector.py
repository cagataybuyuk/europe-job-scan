import unittest
from unittest.mock import patch

from ejs.services.smartrecruiters_live_inspector import (
    LiveInspectionRequest,
    _frame_origin,
    _inspect_frames_once,
    _inspect_until_signal,
    validate_live_url,
)


class _FakeFrame:
    def __init__(self, url: str, structure: dict | None = None):
        self.url = url
        self._structure = structure or {
            "element_count": 1,
            "iframe_count": 0,
            "native_control_count": 0,
            "open_shadow_host_count": 0,
            "custom_element_count": 0,
        }

    def evaluate(self, _script):
        return dict(self._structure)


class _FakePage:
    def __init__(self, frames):
        self.frames = frames
        self.waits = []

    def wait_for_timeout(self, value):
        self.waits.append(value)


def _form(control_count=0, action_count=0):
    controls = [{"observation_key": f"c{i}"} for i in range(control_count)]
    actions = [{"observation_key": f"a{i}"} for i in range(action_count)]
    return {
        "adapter_version": "smartrecruiters-shadow-inspection-v1",
        "controls": controls,
        "actions": actions,
        "schema_fingerprint": "fp",
        "unscoped_locator_collisions": [],
        "file_control_keys": [],
        "next_observed": False,
        "inspection_only": True,
        "live_execution_ready": False,
        "form_value_write_attempts": 0,
        "file_upload_attempts": 0,
        "submit_attempts": 0,
    }


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

    def test_frame_origin_drops_path_query_and_fragment(self):
        self.assertEqual(
            _frame_origin("https://apply.smartrecruiters.com:8443/path?token=secret#x"),
            "https://apply.smartrecruiters.com:8443",
        )
        self.assertEqual(_frame_origin("about:blank"), "about")

    def test_all_frames_are_inspected_and_richest_form_is_selected(self):
        main = _FakeFrame("https://jobs.smartrecruiters.com/job")
        child = _FakeFrame("https://apply.smartrecruiters.com/embed")
        page = _FakePage([main, child])

        def fake_inspect(frame):
            return _form(control_count=0, action_count=1) if frame is main else _form(control_count=3, action_count=2)

        with patch("ejs.services.smartrecruiters_live_inspector.inspect_shadow_form", side_effect=fake_inspect):
            form, selected_index, diagnostics = _inspect_frames_once(page)

        self.assertEqual(selected_index, 1)
        self.assertEqual(len(form["controls"]), 3)
        self.assertEqual(len(diagnostics), 2)
        self.assertEqual(diagnostics[1]["origin"], "https://apply.smartrecruiters.com")
        self.assertNotIn("path", diagnostics[1])
        self.assertNotIn("query", diagnostics[1])

    def test_polling_stays_read_only_and_stops_when_control_appears(self):
        frame = _FakeFrame("https://jobs.smartrecruiters.com/job")
        page = _FakePage([frame])
        reports = [_form(), _form(), _form(control_count=1)]

        with patch("ejs.services.smartrecruiters_live_inspector.inspect_shadow_form", side_effect=reports):
            form, selected_index, diagnostics, elapsed_ms = _inspect_until_signal(page, 2_000)

        self.assertEqual(selected_index, 0)
        self.assertEqual(len(form["controls"]), 1)
        self.assertEqual(elapsed_ms, 1_000)
        self.assertEqual(page.waits, [500, 500])
        self.assertEqual(diagnostics[0]["control_count"], 1)


if __name__ == "__main__":
    unittest.main()
