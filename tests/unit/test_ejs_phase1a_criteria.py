import json
import os
import socket
import stat

import pytest

from ejs.services.criteria_loader import (CriteriaError, load_criteria, parse_criteria_text,
                                          parse_yaml_subset)
from tests.unit.phase1a_support import CRITERIA_YAML, criteria, mutated


def test_valid_criteria_loads_with_rule_version():
    c = criteria()
    assert c.rule_version == "OPPORTUNITY-1.0+K2-test"
    assert sum(c.weights.values()) == 100 and c.thresholds == {"priority_apply": 85, "apply": 75, "consider": 65}
    assert c.previous_sheet_rule["opportunity_min"] == 75


def test_weight_sum_not_100_is_error():
    with pytest.raises(CriteriaError, match="SUM_NOT_100"):
        parse_criteria_text(mutated(CRITERIA_YAML, "sponsorship: 30", "sponsorship: 31"))


@pytest.mark.parametrize("old,new", [
    ("{priority_apply: 85, apply: 75, consider: 65}", "{priority_apply: 75, apply: 85, consider: 65}"),
    ("{priority_apply: 85, apply: 75, consider: 65}", "{priority_apply: 85, apply: 75, consider: 75}"),
    ("{priority_apply: 85, apply: 75, consider: 65}", "{priority_apply: 85, apply: 75}"),
])
def test_threshold_order_and_shape(old, new):
    with pytest.raises(CriteriaError, match="THRESHOLD"):
        parse_criteria_text(mutated(CRITERIA_YAML, old, new))


@pytest.mark.parametrize("extra", ["mystery_key: 1\n", "export:\n  csv_dir: x\n  sheet_write: true\n"])
def test_unknown_key_is_error(extra):
    text = CRITERIA_YAML.replace("export:\n  csv_dir: \"owner-only, outside repo\"\n  enabled: true\n", "")
    with pytest.raises(CriteriaError, match="UNKNOWN_KEY"):
        parse_criteria_text(text + extra)


def test_missing_rule_version_unknown_credit_and_blocker_codes():
    with pytest.raises(CriteriaError, match="RULE_VERSION"):
        parse_criteria_text(CRITERIA_YAML.replace('rule_version: "OPPORTUNITY-1.0+K2-test"\n', ""))
    with pytest.raises(CriteriaError, match="UNKNOWN_CREDIT"):
        parse_criteria_text(mutated(CRITERIA_YAML, "unknown_credit_percent: 50", "unknown_credit_percent: 150"))
    with pytest.raises(CriteriaError, match="HARD_BLOCKER"):
        parse_criteria_text(mutated(CRITERIA_YAML, "- closed-vacancy", "- made-up-blocker"))


def test_template_with_null_placeholders_is_rejected():
    # The unfilled template must fail closed (weights are null).
    with pytest.raises(CriteriaError):
        parse_criteria_text("schema: EJS-CRITERIA-0.1-DRAFT\nrule_version: x\nscore_weights:\n  sponsorship: null\n")


def test_json_form_and_duplicate_keys():
    doc = parse_yaml_subset(CRITERIA_YAML)
    assert parse_criteria_text(json.dumps(doc)).rule_version == doc["rule_version"]
    with pytest.raises(CriteriaError, match="DUPLICATE"):
        parse_criteria_text('{"schema": "EJS-CRITERIA-1.0", "schema": "x"}')
    with pytest.raises(CriteriaError, match="DUPLICATE"):
        parse_criteria_text(CRITERIA_YAML + "rule_version: other\n")


def test_yaml_subset_rejects_unsupported_syntax():
    for bad in ["a: &x 1\n", "a:\n\t- 1\n", "---\na: 1\n", "a: [1, 2\n", "a:\n  - b: 1\n"]:
        with pytest.raises(CriteriaError):
            parse_yaml_subset(bad)


def test_load_from_file_is_read_only_and_no_network(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    p = tmp_path / "criteria.yaml"
    p.write_text(CRITERIA_YAML, encoding="utf-8")
    os.chmod(p, 0o400)
    before = p.read_bytes()
    assert load_criteria(p).rule_version.startswith("OPPORTUNITY-1.0")
    assert p.read_bytes() == before and stat.S_IMODE(p.stat().st_mode) == 0o400
    with pytest.raises(CriteriaError, match="UNREADABLE"):
        load_criteria(tmp_path / "missing.yaml")
