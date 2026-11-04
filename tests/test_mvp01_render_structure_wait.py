"""MVP-01 deterministic bounded render-wait regression tests."""

import unittest

from ejs.services.smartrecruiters_live_inspector import _wait_for_render_structure


def structure(**overrides):
    data = {
        "ready_state": "complete",
        "element_count": 10,
        "form_count": 0,
        "native_control_count": 0,
        "action_candidate_count": 0,
        "iframe_count": 0,
        "custom_control_hint_count": 0,
        "custom_element_tags": [],
        "open_shadow_root_count": 0,
    }
    data.update(overrides)
    return data


class FakePage:
    def __init__(self, frames):
        self.frames = frames
        self.elapsed_ms = 0

    def evaluate(self, _script):
        index = min(self.elapsed_ms // 500, len(self.frames) - 1)
        return self.frames[index]

    def wait_for_timeout(self, milliseconds):
        self.elapsed_ms += milliseconds


class RenderStructureWaitTests(unittest.TestCase):
    def test_shell_actions_and_opaque_iframe_do_not_trigger_early_exit(self):
        page = FakePage([structure(action_candidate_count=4, iframe_count=1)])
        _, report = _wait_for_render_structure(page, budget_ms=8_000)
        self.assertGreaterEqual(report["elapsed_ms"], 3_000)
        self.assertEqual(report["exit_reason"], "stable_without_structure")

    def test_delayed_form_controls_are_observed(self):
        shell = structure(action_candidate_count=4, iframe_count=1)
        controls = structure(action_candidate_count=4, iframe_count=1, native_control_count=2)
        page = FakePage([shell, shell, shell, shell, controls])
        last, report = _wait_for_render_structure(page, budget_ms=8_000)
        self.assertEqual(last["native_control_count"], 2)
        self.assertEqual(report["elapsed_ms"], 2_000)
        self.assertEqual(report["exit_reason"], "structure_signal_observed")

    def test_form_count_is_a_structure_signal(self):
        page = FakePage([structure(form_count=1)])
        _, report = _wait_for_render_structure(page, budget_ms=8_000)
        self.assertEqual(report["elapsed_ms"], 500)
        self.assertEqual(report["exit_reason"], "structure_signal_observed")

    def test_budget_is_never_exceeded_for_unstable_shell(self):
        frames = [structure(element_count=i + 10, action_candidate_count=1) for i in range(20)]
        page = FakePage(frames)
        _, report = _wait_for_render_structure(page, budget_ms=2_000)
        self.assertEqual(report["elapsed_ms"], 2_000)
        self.assertEqual(report["exit_reason"], "render_wait_budget_exhausted")

    def test_custom_hints_alone_are_not_form_controls(self):
        page = FakePage([structure(custom_control_hint_count=2, custom_element_tags=["x-widget"])])
        _, report = _wait_for_render_structure(page, budget_ms=4_000)
        self.assertGreaterEqual(report["elapsed_ms"], 3_000)
        self.assertEqual(report["exit_reason"], "stable_without_structure")


if __name__ == "__main__":
    unittest.main()
