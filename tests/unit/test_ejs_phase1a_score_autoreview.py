import pytest

from ejs.services.autoreview import ListingFacts, decide
from ejs.services.criteria_loader import CriteriaError, parse_criteria_text
from ejs.services.opportunity_score import ScoreInputError, compute_opportunity
from tests.unit.phase1a_support import CRITERIA_YAML, criteria, mutated, uniform

C = criteria()


def facts(ratings, **kw):
    kw.setdefault("vacancy_open", True)
    return ListingFacts("L1", ratings, **kw)


@pytest.mark.parametrize("r,band", [(64, "skip"), (65, "consider"), (74, "consider"), (75, "apply"),
                                    (84, "apply"), (85, "priority_apply"), (100, "priority_apply"), (0, "skip")])
def test_band_boundaries(r, band):
    s = compute_opportunity(uniform(r), C)
    assert s.score == r and s.band == band


@pytest.mark.parametrize("r,label", [(64, "auto_skip"), (65, "auto_apply"), (74, "auto_apply"),
                                     (75, "auto_apply"), (84, "auto_apply"), (85, "auto_apply")])
def test_autoreview_threshold_boundaries(r, label):
    assert decide(facts(uniform(r)), C).label == label


def test_weights_and_thresholds_come_from_criteria_not_code():
    c2 = parse_criteria_text(mutated(mutated(CRITERIA_YAML, "sponsorship: 30", "sponsorship: 20"),
                                     "compensation_living: 25", "compensation_living: 35"))
    r = uniform(50); r["sponsorship"] = 100; r["compensation_living"] = 0
    assert compute_opportunity(r, C).score == 30 * 1 + 25 * 0 + 45 * 0.5 + 0  # 52.5 with default weights
    assert compute_opportunity(r, c2).score == 20 + 0 + 45 * 0.5
    c3 = parse_criteria_text(mutated(CRITERIA_YAML, "opportunity_min: 65", "opportunity_min: 70"))
    assert decide(facts(uniform(65)), c3).label == "auto_skip"


def test_unknown_is_not_negative_and_listed():
    r = uniform(80); r["sponsorship"] = None
    s = compute_opportunity(r, C)
    assert s.unknown_components == ("sponsorship",)
    assert s.score == 80 * 0.7 + 15 and s.score > compute_opportunity({**r, "sponsorship": 0}, C).score
    d = decide(facts(r), C)
    assert d.label == "auto_apply" and d.confidence == "medium" and "sponsorship" in d.unknown_components


def test_unknown_sponsorship_exact_boundary_65():
    r = {"sponsorship": None, "compensation_living": 100, "role_fit": 100, "scope": 50,
         "career_upside": 0, "english_intl": 0, "seniority_title": 0}
    assert compute_opportunity(r, C).score == 65 and decide(facts(r), C).label == "auto_apply"
    r["scope"] = 49
    assert decide(facts(r), C).label == "auto_skip"


@pytest.mark.parametrize("field,code", [
    ("explicit_no_sponsorship", "blocker:explicit-no-sponsorship"),
    ("requires_unlimited_work_right", "blocker:unlimited-work-right-required"),
    ("mandatory_language_unmet", "blocker:mandatory-language-unmet"),
    ("junior_or_technical_only", "blocker:junior-or-technical-only")])
def test_hard_blockers_skip_even_with_high_score(field, code):
    d = decide(facts(uniform(95), **{field: True}), C)
    assert (d.label, d.reason_code, d.confidence) == ("auto_skip", code, "high")


def test_closed_vacancy_skips_and_unknown_vacancy_needs_user():
    assert decide(facts(uniform(95), vacancy_open=False), C).reason_code == "blocker:closed-vacancy"
    assert decide(facts(uniform(95), vacancy_open=None), C).label == "needs_user"


def test_unknown_blocker_signals_do_not_block_or_skip():
    d = decide(facts(uniform(90)), C)       # all blocker signals None
    assert d.label == "auto_apply"


def test_gray_zone_needs_user():
    assert decide(facts(uniform(90), contradictions=("sponsorship_text_conflict",)), C).reason_code == "contradiction"
    assert decide(facts(uniform(None)), C).reason_code == "evidence_missing"
    assert decide(facts({"sponsorship": 1}), C).reason_code == "invalid_facts"
    assert decide(facts(uniform(101)), C).label == "needs_user"
    assert decide(facts(uniform(90), explicit_no_sponsorship="yes"), C).label == "needs_user"
    assert decide(ListingFacts("", uniform(90), vacancy_open=True), C).label == "needs_user"


def test_contradiction_beats_blocker():
    d = decide(facts(uniform(90), explicit_no_sponsorship=True, contradictions=("x",)), C)
    assert d.label == "needs_user"


def test_deterministic_and_carries_rule_version_and_reason():
    f = facts(uniform(70))
    outs = {decide(f, C) for _ in range(20)}
    assert len(outs) == 1
    d = outs.pop()
    assert d.rule_version == C.rule_version and d.reason_code and d.confidence in ("high", "medium", "low")


def test_previous_sheet_rule_is_stored_but_not_used():
    c2 = parse_criteria_text(mutated(CRITERIA_YAML, "previous_sheet_rule: {opportunity_min: 75",
                                     "previous_sheet_rule: {opportunity_min: 99"))
    assert c2.previous_sheet_rule["opportunity_min"] == 99
    assert decide(facts(uniform(66)), c2).label == decide(facts(uniform(66)), C).label == "auto_apply"


@pytest.mark.parametrize("code", ["explicit-no-sponsorship", "unlimited-work-right-required",
                                  "mandatory-language-unmet", "closed-vacancy",
                                  "junior-or-technical-only"])
def test_mandatory_blocker_cannot_be_removed(code):
    with pytest.raises(CriteriaError, match="CRITERIA_HARD_BLOCKER_MANDATORY_MISSING"):
        parse_criteria_text(mutated(CRITERIA_YAML, f"    - {code}\n", ""))


def test_empty_blockers_rejected_so_closed_no_sponsorship_cannot_auto_apply():
    import re
    text = re.sub(r"  hard_blockers:\n(    - .*\n)+", "  hard_blockers: []\n", CRITERIA_YAML)
    assert text != CRITERIA_YAML
    with pytest.raises(CriteriaError, match="CRITERIA_HARD_BLOCKER_MANDATORY_MISSING"):
        parse_criteria_text(text)
    assert decide(facts(uniform(95), vacancy_open=False, explicit_no_sponsorship=True), C).label == "auto_skip"


def test_score_input_errors():
    with pytest.raises(ScoreInputError):
        compute_opportunity({"sponsorship": 1}, C)
    with pytest.raises(ScoreInputError):
        compute_opportunity({**uniform(50), "scope": True}, C)
