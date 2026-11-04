"""Read-only multi-step discovery must never imply unseen steps were inspected."""
from ejs.services.smartrecruiters_live_inspector import summarize_step_boundary


def test_visible_next_action_is_boundary_not_authorization():
    form = {"controls": [{"visible": True, "disabled": False}],
            "actions": [{"label": "Continue", "visible": True, "disabled": False}]}
    result = summarize_step_boundary(form)
    assert result["additional_steps_possible"]
    assert result["next_action_count"] == 1
    assert result["requires_separate_review_for_step_navigation"]
    assert result["later_steps_inspected"] is False
    assert result["multi_step_complete"] is False
    assert result["step_navigation_attempts"] == 0


def test_hidden_or_disabled_next_does_not_claim_step():
    form = {"controls": [], "actions": [
        {"label": "Next", "visible": False, "disabled": False},
        {"label": "Proceed", "visible": True, "disabled": True},
        {"label": "Submit", "visible": True, "disabled": False}]}
    result = summarize_step_boundary(form)
    assert result["next_action_count"] == 0
    assert result["additional_steps_possible"] is False
    assert result["later_steps_inspected"] is False


def test_missing_actions_fail_closed():
    result = summarize_step_boundary({})
    assert result["current_step_only"] is True
    assert result["visible_enabled_control_count"] == 0
    assert result["multi_step_complete"] is False
