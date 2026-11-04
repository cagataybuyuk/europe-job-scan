"""MVP-03 audit regression: observed challenges must never lose human handoff."""
from pathlib import Path

INSPECTOR = Path(__file__).resolve().parents[1] / "src/ejs/services/smartrecruiters_live_inspector.py"


def test_challenge_remains_boundary_even_when_network_navigation_blocked():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert 'if not captcha and apply_navigation["status"] in {' in source
    assert 'if captcha:\n                state, error_code = "captcha_boundary", "CAPTCHA_BOUNDARY"' in source
    assert '"required": state == "captcha_boundary"' in source
    assert '"automatic_resume_allowed": False' in source


def test_navigation_denial_still_applies_without_challenge():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert '"NETWORK_FENCE_BLOCKED", "UNTRUSTED_FINAL_URL", "ENTRY_NAVIGATION_FAILED"' in source
    assert 'state, error_code = "form_structure_not_discovered", "FORM_STRUCTURE_NOT_DISCOVERED"' in source
