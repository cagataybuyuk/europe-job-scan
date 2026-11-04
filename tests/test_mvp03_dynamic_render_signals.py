"""Read-only dynamic render readiness signals for MVP-03."""
from ejs.services.smartrecruiters_live_inspector import PAGE_STRUCTURE_DIAGNOSTICS, _wait_for_render_structure


def test_render_diagnostics_include_open_shadow_root_controls():
    script = PAGE_STRUCTURE_DIAGNOSTICS
    assert "all.filter(el => el.matches('form'))" in script
    assert "all.filter(el => el.matches('input:not([type=\"hidden\"]), textarea, select'))" in script
    assert "all.filter(el => el.matches('button, input[type=\"submit\"], input[type=\"button\"], a[role=\"button\"]'))" in script
    assert "if (el.shadowRoot) visit(el.shadowRoot)" in script


def test_late_dynamic_control_observed_within_existing_budget():
    class ReadOnlyPage:
        elapsed = 0
        def evaluate(self, script):
            return {"ready_state": "complete", "element_count": 5 if self.elapsed < 1000 else 6,
                    "native_control_count": 0 if self.elapsed < 1000 else 1,
                    "form_count": 0, "action_candidate_count": 0, "iframe_count": 0,
                    "custom_element_tags": [], "open_shadow_root_count": 1}
        def wait_for_timeout(self, ms):
            self.elapsed += ms
    page = ReadOnlyPage()
    report, timing = _wait_for_render_structure(page, budget_ms=3000)
    assert report["native_control_count"] == 1
    assert timing["exit_reason"] == "structure_signal_observed"
    assert page.elapsed == 1000
