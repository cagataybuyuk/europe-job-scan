"""MVP-03 malformed DOM observations must fail closed, without leaking values."""
import pytest
from ejs.services.smartrecruiters_shadow import summarize_shadow_form


@pytest.mark.parametrize("observation", [
    None, [], {}, {"controls": [], "actions": None},
    {"controls": {}, "actions": []},
])
def test_invalid_top_level_observation_rejected(observation):
    with pytest.raises(ValueError, match="INVALID_FORM_OBSERVATION"):
        summarize_shadow_form(observation)


@pytest.mark.parametrize("control", [None, [], {}, {"observation_key": "", "scope": "document", "tag": "input"},
                                     {"observation_key": "key", "scope": None, "tag": "input"}])
def test_invalid_control_rejected(control):
    with pytest.raises(ValueError, match="INVALID_CONTROL_OBSERVATION"):
        summarize_shadow_form({"controls": [control], "actions": []})


@pytest.mark.parametrize("action", [None, [], {}, {"observation_key": "key", "scope": None}])
def test_invalid_action_rejected(action):
    with pytest.raises(ValueError, match="INVALID_ACTION_OBSERVATION"):
        summarize_shadow_form({"controls": [], "actions": [action]})


def test_valid_read_only_observation_remains_execution_forbidden():
    result = summarize_shadow_form({"controls": [{"scope": "document", "observation_key": "document/input@0",
                                                  "tag": "input", "type": "text", "value": "PRIVATE"}],
                                    "actions": []})
    assert result["inspection_only"] is True
    assert result["live_execution_ready"] is False
    assert result["form_value_write_attempts"] == result["file_upload_attempts"] == result["submit_attempts"] == 0
    assert "PRIVATE" not in str(result)
