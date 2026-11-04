"""MVP-03 manual challenge handoff invariants."""
from pathlib import Path

INSPECTOR = Path(__file__).resolve().parents[1] / "src/ejs/services/smartrecruiters_live_inspector.py"


def test_manual_handoff_is_explicit_and_non_resumable():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert '"required": state == "captcha_boundary"' in source
    assert '"manual_verification_only": state == "captcha_boundary"' in source
    assert '"automatic_resume_allowed": False' in source
    assert '"resume_requires_new_read_only_inspection": state == "captcha_boundary"' in source


def test_handoff_is_reported_without_automatic_interaction():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert '"human_handoff": human_handoff' in source
    assert '"form_value_write_attempts": 0' in source
    assert '"file_upload_attempts": 0' in source
    assert '"submit_attempts": 0' in source
