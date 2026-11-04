"""Guard the read-only frame discovery fail-closed branch against regressions."""
from pathlib import Path

INSPECTOR = Path(__file__).resolve().parents[1] / "src/ejs/services/smartrecruiters_live_inspector.py"


def test_same_origin_frame_read_failure_cannot_certify_form():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert 'same_origin_frame_observation_failed = False' in source
    assert 'same_origin_frame_observation_failed = True' in source
    assert 'elif same_origin_frame_observation_failed:' in source
    assert '"FRAME_OBSERVATION_INCOMPLETE"' in source
    assert source.index('elif same_origin_frame_observation_failed:') < source.index('if apply_navigation["status"] in {')


def test_captcha_boundary_precedes_incomplete_frame_boundary():
    source = INSPECTOR.read_text(encoding="utf-8")
    assert 'if captcha:\n                state, error_code = "captcha_boundary", "CAPTCHA_BOUNDARY"\n            elif same_origin_frame_observation_failed:' in source
