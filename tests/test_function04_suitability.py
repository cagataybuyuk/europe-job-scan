"""FUNCTION-04 acceptance tests. Synthetic criteria and listings only; no network, no private data."""
from __future__ import annotations

import copy
import json
import random
import socket

import pytest

from ejs.services.application_bridge import prepare_application_report
from ejs.services.criteria_loader import parse_criteria_text
from ejs.services.shadow_run import SCHEMA as DISCOVERY_SCHEMA
from ejs.services.suitability import (AUTHORITY, SCHEMA, SuitabilityError, evaluate_report, profile_from_criteria,
                                      project_for_bridge)
from ejs.services.suitability_evidence import (VacancyDetail, detail_from_smartrecruiters, extract_compensation,
                                               extract_detail_evidence)
from tests.unit.phase1a_support import CRITERIA_YAML

PROFILE_SECTIONS = """\
countries:
  tier_1: [Netherlands]
  others: [Ireland]
role_tracks:
  primary: business analyst
  secondary: [data analyst]
seniority:
  include: [senior, lead]
  exclude: [director]
compensation:
  target_net_monthly_eur_min: 3500
work_rights:
  eu_work_authorization: false
languages_required_met: [English]
"""


def criteria_text(extra: str = PROFILE_SECTIONS) -> str:
    head = CRITERIA_YAML.split("countries:")[0]
    tail = "score_weights:" + CRITERIA_YAML.split("score_weights:", 1)[1]
    return head + extra + tail


def criteria(extra: str = PROFILE_SECTIONS):
    return parse_criteria_text(criteria_text(extra))


def row(posting: str | None, role: str, *, country: str | list[str] | None = "NL", company: str = "Acme",
        location: str = "Amsterdam") -> dict:
    countries = [country] if isinstance(country, str) else (country or [])
    url = f"https://jobs.smartrecruiters.com/Acme/{posting}" if posting else ""
    return {"company": company, "role": role, "location": location, "url": url, "ats_kind": "smartrecruiters",
            "source": "employer_watchlist", "tenant": "Acme", "region": "global", "posting_id": posting,
            "identity_status": "known" if posting else "unknown",
            "similarity_key": f"acme|{role.casefold()}|{location.casefold()}",
            "discovery_evidence": {"observation": "live_country_feed", "feed_url": "https://x", "countries": countries}}


def report(*rows: dict) -> dict:
    return {"schema": DISCOVERY_SCHEMA, "mode": "shadow_no_submit", "projection": "verified_live_country_feed",
            "listings_kept": len(rows), "vacancies": [dict(r) for r in rows]}


def detail(posting: str, text: str, *, active: bool | None = True, comp: dict | None = None) -> VacancyDetail:
    return VacancyDetail("Acme", posting, active, text, "d" * 64, comp)


def by_posting(result: dict) -> dict:
    return {v["posting_id"]: v["suitability"] for v in result["vacancies"]}


# ---- 1. relevant roles are prioritized -------------------------------------------------------------
def test_relevant_roles_rank_first_and_non_matches_last():
    rep = report(row("3", "Accountant"), row("2", "Data Analyst"), row("1", "Senior Business Analyst"))
    result = evaluate_report(rep, criteria())
    assert [v["posting_id"] for v in result["vacancies"]] == ["1", "2", "3"]
    s = by_posting(result)
    assert s["1"]["dimensions"]["role_track"]["status"] == "primary_match"
    assert s["2"]["dimensions"]["role_track"]["status"] == "secondary_match"
    assert s["3"]["dimensions"]["role_track"]["status"] == "no_title_match"
    assert s["1"]["score"] > s["2"]["score"] > s["3"]["score"]
    assert [v["priority_rank"] for v in result["vacancies"]] == [1, 2, 3]
    assert [s[i]["classification"] for i in "123"] == ["preliminary_high", "preliminary_medium", "preliminary_low"]
    assert {s[i]["band_basis"] for i in "123"} == {"listing_metadata_unknown_credit_applied"}
    assert {s[i]["band"] for i in "123"} == {"skip"}           # Unknown credit caps listing-only scores


def test_listing_metadata_never_yields_a_verified_label_or_apply():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria())
    s = by_posting(result)["1"]
    assert s["evidence_level"] == "listing_metadata"
    assert s["classification"] == "preliminary_high"
    assert s["decision_label"] == "needs_user" and s["reason_code"] == "vacancy_evidence_missing"
    assert "vacancy_detail_not_observed" in s["missing_evidence"]


# ---- 2. hard blockers follow the approved rules ------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("We do not offer visa sponsorship for this role.", "blocker:explicit-no-sponsorship"),
    ("Visa sponsorship is not available.", "blocker:explicit-no-sponsorship"),
    ("Fluent Dutch is required for daily client contact.", "blocker:mandatory-language-unmet"),
    ("Applicants need an unrestricted right to work in the Netherlands.", "blocker:unlimited-work-right-required"),
])
def test_explicit_blockers_skip_with_quote(text, expected):
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", text)})
    s = by_posting(result)["1"]
    assert s["decision_label"] == "auto_skip" and s["reason_code"] == expected
    assert s["classification"] == "verified_blocked"
    assert f"confirmed:{expected}" in s["confirmed_incompatibilities"]
    assert s["detail_quotes"]


def test_closed_vacancy_is_blocked_and_unknown_status_is_not():
    closed = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", "Great role.", active=False)})
    assert by_posting(closed)["1"]["reason_code"] == "blocker:closed-vacancy"
    unknown = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                              details={("Acme", "1"): detail("1", "Great role.", active=None)})
    s = by_posting(unknown)["1"]
    assert s["decision_label"] == "needs_user" and s["reason_code"] == "vacancy_evidence_missing"


def test_junior_title_is_blocked_only_when_not_owner_included():
    junior = evaluate_report(report(row("1", "Junior Business Analyst")), criteria())
    assert by_posting(junior)["1"]["classification"] == "preliminary_blocked"
    owner_ok = criteria(PROFILE_SECTIONS.replace("include: [senior, lead]", "include: [junior]"))
    allowed = evaluate_report(report(row("1", "Junior Business Analyst")), owner_ok)
    assert by_posting(allowed)["1"]["classification"] != "preliminary_blocked"


def test_unrestricted_right_not_a_blocker_when_candidate_is_authorized():
    authorized = criteria(PROFILE_SECTIONS.replace("eu_work_authorization: false", "eu_work_authorization: true"))
    result = evaluate_report(report(row("1", "Senior Business Analyst")), authorized,
                             details={("Acme", "1"): detail("1", "You need an unrestricted right to work in the EU.")})
    assert not by_posting(result)["1"]["reason_code"].startswith("blocker:")
    assert by_posting(result)["1"]["dimensions"]["work_authorization"]["status"] == "unrestricted_right_required"


def test_generic_right_to_work_needs_owner_review_not_skip():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", "Visa sponsorship is available. Working language is English. "
                                                     "Salary: €4,500 per month net. "
                                                     "Candidates must have the right to work in the Netherlands.")})
    s = by_posting(result)["1"]
    assert s["decision_label"] == "needs_user" and s["classification"] == "verified_needs_review"
    assert s["review_flags"] == ["work_right_requirement_needs_owner_review"]


# ---- 3 + 4. unknown is not fabricated; missing is not incompatibility --------------------------------
def test_unknown_sponsorship_and_compensation_stay_unknown():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", "Join our team and grow with us.")})
    s = by_posting(result)["1"]
    assert s["ratings"]["sponsorship"] is None and s["ratings"]["compensation_living"] is None
    assert {"sponsorship", "compensation_living"} <= set(s["unknown_components"])
    assert s["dimensions"]["sponsorship"]["status"] == "not_stated"
    assert s["dimensions"]["compensation"]["status"] == "not_stated"
    assert s["confirmed_incompatibilities"] == []
    assert "explicit_employer_sponsorship_statement" in s["missing_evidence"]


def test_hedged_sponsorship_is_not_evidence_either_way():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(), details={
        ("Acme", "1"): detail("1", "Visa sponsorship may be available for exceptional candidates.")})
    s = by_posting(result)["1"]
    assert s["ratings"]["sponsorship"] is None and s["dimensions"]["sponsorship"]["status"] == "not_stated"
    ev = extract_detail_evidence(detail("1", "We cannot offer visa sponsorship in most cases, depending on role."))
    assert ev.explicit_no_sponsorship is None and ev.hedged == ("sponsorship",)


def test_conflicting_sponsorship_statements_need_user():
    text = "We do not offer visa sponsorship. Elsewhere: visa sponsorship is provided."
    ev = extract_detail_evidence(detail("1", text))
    assert ev.contradictions
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", text)})
    assert by_posting(result)["1"]["reason_code"] == "contradiction"


def test_soft_language_mentions_are_not_mandatory():
    for text in ("Dutch is a plus.", "German skills are an advantage.", "Fluent French is nice to have."):
        assert extract_detail_evidence(detail("1", text)).mandatory_languages == ()
    assert extract_detail_evidence(detail("1", "Dutch is required.")).mandatory_languages == ("dutch",)
    met = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                          details={("Acme", "1"): detail("1", "English is required.")})
    assert by_posting(met)["1"]["dimensions"]["language"]["status"] == "mandatory_met"
    assert by_posting(met)["1"]["ratings"]["english_intl"] == 100


def test_compensation_gross_cannot_confirm_net_but_low_amount_is_confirmed_below():
    cases = {
        "Salary: €2,800 - €3,200 per month gross.": ("below_target_confirmed", 0),
        "Salary: €6,000 per month gross.": ("net_comparison_undetermined", None),
        "Salary: €4,000 per month net.": ("meets_target_net_stated", 100),
        "Salary: €36,000 per year.": ("below_target_confirmed", 0),
        "Salary: €60k per year.": ("net_comparison_undetermined", None),
        "Competitive salary.": ("not_stated", None),
        "Salary: $9,000 per month.": ("not_stated", None),
    }
    for text, (status, rating) in cases.items():
        s = by_posting(evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                                       details={("Acme", "1"): detail("1", text)}))["1"]
        assert (s["dimensions"]["compensation"]["status"], s["ratings"]["compensation_living"]) == (status, rating), text
    assert extract_compensation("Salary: €60k per year.")["monthly_min"] == 5000


def test_structured_compensation_input_and_invalid_is_unknown():
    ok = detail("1", "x", comp={"currency": "EUR", "min": 3000, "max": 3400, "period": "month", "basis": "net"})
    s = by_posting(evaluate_report(report(row("1", "Senior Business Analyst")), criteria(), details={("Acme", "1"): ok}))["1"]
    assert s["dimensions"]["compensation"]["status"] == "below_target_confirmed"
    bad = detail("1", "x", comp={"currency": "USD", "min": 3000, "period": "month"})
    s = by_posting(evaluate_report(report(row("1", "Senior Business Analyst")), criteria(), details={("Acme", "1"): bad}))["1"]
    assert s["dimensions"]["compensation"]["status"] == "not_stated"


def test_missing_evidence_and_confirmed_incompatibility_are_separate_fields():
    result = evaluate_report(report(row("1", "Accountant"), row("2", "Senior Business Analyst")), criteria(),
                             details={("Acme", "2"): detail("2", "Salary: €2,000 per month gross.")})
    s = by_posting(result)
    assert "title:no_role_track_match" in s["1"]["confirmed_incompatibilities"]
    assert "confirmed:compensation_below_target" in s["2"]["confirmed_incompatibilities"]
    assert "scope_evidence_missing" in s["2"]["missing_evidence"]
    assert not set(s["2"]["confirmed_incompatibilities"]) & set(s["2"]["missing_evidence"])


# ---- verified positive path --------------------------------------------------------------------------
def test_verified_apply_candidate_when_evidence_supports_it():
    text = ("Visa sponsorship is available. Working language is English. Salary: €4,500 per month net.")
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("1", text)},
                             reviewer_ratings={("Acme", "1"): {"scope": {"rating": 80, "ref": "rev-1"},
                                                              "career_upside": {"rating": 80, "ref": "rev-1"}}})
    s = by_posting(result)["1"]
    assert s["classification"] == "verified_apply_candidate" and s["decision_label"] == "auto_apply"
    assert s["ratings"] == {"sponsorship": 100, "compensation_living": 100, "role_fit": 100, "scope": 80,
                            "career_upside": 80, "english_intl": 100, "seniority_title": 100}
    assert s["band"] in ("apply", "priority_apply") and s["unknown_components"] == []
    assert result["authority"] == AUTHORITY and result["executable"] is False and result["submit_authorized"] is False


@pytest.mark.parametrize("country,flag,status", [("FR", "geography_not_in_configured_countries", "not_configured"),
                                                  (None, "geography_unknown", "unknown")])
def test_geography_outside_or_unknown_never_reaches_apply(country, flag, status):
    text = "Visa sponsorship is available. Working language is English. Salary: €4,500 per month net."
    result = evaluate_report(report(row("1", "Senior Business Analyst", country=country)), criteria(),
                             details={("Acme", "1"): detail("1", text)})
    s = by_posting(result)["1"]
    assert s["dimensions"]["geography"]["status"] == status
    assert s["decision_label"] == "needs_user" and s["reason_code"] == flag and flag in s["review_flags"]
    assert s["classification"] == "verified_needs_review"


def test_geography_tiers_and_preliminary_demotion():
    result = evaluate_report(report(row("1", "Senior Business Analyst", country="IE"),
                                    row("2", "Senior Business Analyst", country="NL"),
                                    row("3", "Senior Business Analyst", country="FR")), criteria())
    assert [v["posting_id"] for v in result["vacancies"]] == ["2", "1", "3"]
    assert [by_posting(result)[i]["classification"] for i in "123"] == ["preliminary_high", "preliminary_high",
                                                                          "preliminary_low"]


def test_preliminary_tier_demotions_for_excluded_seniority_and_unknown_country():
    result = evaluate_report(report(row("1", "Director Business Analyst"), row("2", "Senior Business Analyst", country=None),
                                    row("3", "Senior Data Analyst")), criteria())
    s = by_posting(result)
    assert s["1"]["classification"] == "preliminary_medium" and "title:seniority_excluded" in s["1"]["confirmed_incompatibilities"]
    assert s["2"]["classification"] == "preliminary_medium" and s["2"]["review_flags"] == ["geography_unknown"]
    assert s["3"]["classification"] == "preliminary_medium"


def test_reviewer_rating_requires_reference_and_known_component():
    for bad in ({"scope": {"rating": 80}}, {"sponsorship": {"rating": 100, "ref": "x"}},
                {"scope": {"rating": 101, "ref": "x"}}, {"scope": {"rating": True, "ref": "x"}}):
        with pytest.raises(SuitabilityError):
            evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                            reviewer_ratings={("Acme", "1"): bad})


# ---- 5. determinism ----------------------------------------------------------------------------------
def test_repeated_and_reordered_evaluation_is_byte_identical():
    rows = [row(str(i), t, country=c) for i, (t, c) in enumerate(
        [("Senior Business Analyst", "NL"), ("Data Analyst", "IE"), ("Accountant", "NL"), ("Lead Business Analyst", "FR"),
         ("Senior Business Analyst", "IE")], 1)]
    details = {("Acme", "1"): detail("1", "Visa sponsorship is available.")}
    dump = lambda r: json.dumps(evaluate_report(r, criteria(), details=details), sort_keys=True)  # noqa: E731
    assert dump(report(*rows)) == dump(report(*rows))
    shuffled = rows[:]
    random.Random(7).shuffle(shuffled)
    a = evaluate_report(report(*rows), criteria(), details=details)["vacancies"]
    b = evaluate_report(report(*shuffled), criteria(), details=details)["vacancies"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_no_network_during_evaluation(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                    details={("Acme", "1"): detail("1", "Visa sponsorship is available.")})


# ---- 6. identities -----------------------------------------------------------------------------------
def test_duplicate_identities_are_collapsed_and_input_is_not_mutated():
    rep = report(row("1", "Senior Business Analyst"), row("1", "Senior Business Analyst"), row("2", "Data Analyst"))
    before = copy.deepcopy(rep)
    result = evaluate_report(rep, criteria())
    assert rep == before
    assert result["input_rows"] == 3 and result["duplicate_rows"] == 1 and result["listings_kept"] == 2
    ids = [(v["tenant"], v["posting_id"]) for v in result["vacancies"]]
    assert len(ids) == len(set(ids))


def test_similar_postings_stay_separate_and_cannot_auto_apply():
    text = "Visa sponsorship is available. Working language is English. Salary: €4,500 per month net."
    rep = report(row("1", "Senior Business Analyst"), row("2", "Senior Business Analyst"))
    result = evaluate_report(rep, criteria(), details={("Acme", "1"): detail("1", text)})
    assert result["listings_kept"] == 2 and result["similarity_groups_with_multiple_postings"] == 1
    s = by_posting(result)["1"]
    assert "duplicate_similarity_group" in s["review_flags"] and s["decision_label"] == "needs_user"
    assert by_posting(result)["1"]["dimensions"] and by_posting(result)["2"]["evidence_level"] == "listing_metadata"


def test_unknown_identity_row_is_kept_but_never_apply():
    result = evaluate_report(report(row(None, "Senior Business Analyst")), criteria(),
                             details={("Acme", ""): detail("", "Visa sponsorship is available.")})
    v = result["vacancies"][0]
    assert v["identity_status"] == "unknown" and v["suitability"]["decision_label"] != "auto_apply"


def test_provenance_fields_are_preserved_verbatim():
    src = row("1", "Senior Business Analyst")
    out = evaluate_report(report(src), criteria())["vacancies"][0]
    for k in ("company", "role", "location", "url", "ats_kind", "source", "tenant", "region", "posting_id",
              "similarity_key", "discovery_evidence"):
        assert out[k] == src[k]


def test_detail_with_wrong_identity_is_rejected_not_applied():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(),
                             details={("Acme", "1"): detail("9", "We do not offer visa sponsorship.")})
    assert result["details_rejected"] == {"detail_identity_mismatch": 1} and result["details_applied"] == 0
    assert by_posting(result)["1"]["evidence_level"] == "listing_metadata"


def test_smartrecruiters_detail_adapter_checks_identity_and_extracts_text():
    raw = json.dumps({"id": "1", "active": True, "company": {"identifier": "Acme"},
                      "jobAd": {"sections": {"jobDescription": {"text": "<p>We do not offer visa sponsorship.</p>"}}}}).encode()
    d = detail_from_smartrecruiters(raw, "Acme", "1")
    assert d.active is True and "visa sponsorship" in d.text and len(d.detail_sha256) == 64
    with pytest.raises(ValueError):
        detail_from_smartrecruiters(raw, "Other", "1")
    with pytest.raises(ValueError):
        detail_from_smartrecruiters(raw, "Acme", "2")


# ---- 7. downstream compatibility ---------------------------------------------------------------------
def test_projection_is_accepted_by_unchanged_w2_bridge():
    rep = report(row("1", "Senior Business Analyst"), row("2", "Data Analyst"), row("3", "Junior Analyst"))
    result = evaluate_report(rep, criteria())
    projected = project_for_bridge(result)
    assert projected["listings_kept"] == 2 and projected["excluded_rows"]["reasons"] == {"preliminary_blocked": 1}
    prepared = prepare_application_report(projected)
    assert prepared["input_rows"] == 2 and prepared["executable"] is False and prepared["submit_authorized"] is False
    assert [c["posting_id"] for c in prepared["candidates"]] == ["1", "2"]


def test_projection_refuses_non_suitability_or_executable_reports():
    with pytest.raises(SuitabilityError):
        project_for_bridge({"schema": "other"})
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria())
    result["executable"] = True
    with pytest.raises(SuitabilityError):
        project_for_bridge(result)


# ---- 8. authority, criteria approval, profile --------------------------------------------------------
def test_output_grants_no_authority_and_is_unapproved_without_a_reference():
    result = evaluate_report(report(row("1", "Senior Business Analyst")), criteria())
    assert result["schema"] == SCHEMA and result["mode"] == "suitability_no_submit"
    assert result["executable"] is False and result["submit_authorized"] is False
    assert not any(v for k, v in result["authority"].items() if k != "label_is_not_authority")
    assert result["criteria"]["approved_by_owner"] is False and result["handoff_ready"] is False
    approved = evaluate_report(report(row("1", "Senior Business Analyst")), criteria(), criteria_approval_ref="OWNER-REF-1")
    assert approved["handoff_ready"] is True and approved["criteria"]["approval_ref"] == "OWNER-REF-1"


def test_profile_gaps_block_handoff_and_make_dimensions_unknown():
    bare = criteria("")
    assert set(profile_from_criteria(bare).gaps) == {"role_tracks", "seniority", "countries", "languages_required_met",
                                                     "compensation_target", "work_authorization"}
    result = evaluate_report(report(row("1", "Senior Business Analyst")), bare, criteria_approval_ref="REF")
    s = by_posting(result)["1"]
    assert s["ratings"]["role_fit"] is None and s["dimensions"]["role_track"]["status"] == "unknown"
    assert result["handoff_ready"] is False


def test_unfilled_template_placeholder_is_refused():
    with pytest.raises(SuitabilityError, match="PROFILE_PLACEHOLDER"):
        profile_from_criteria(criteria(PROFILE_SECTIONS.replace("primary: business analyst", 'primary: "<ROL_AILESI>"')))


@pytest.mark.parametrize("mutate", [lambda r: r.update(schema="x"), lambda r: r.update(listings_kept=5),
                                    lambda r: r["vacancies"][0].pop("role")])
def test_malformed_input_report_is_refused(mutate):
    rep = report(row("1", "Senior Business Analyst"))
    mutate(rep)
    with pytest.raises(SuitabilityError):
        evaluate_report(rep, criteria())


def test_scale_198_rows_unique_and_deterministic():
    titles = ["Senior Business Analyst", "Data Analyst", "Accountant", "Junior Auditor", "Lead Business Analyst", "Tax Manager"]
    rows = [row(str(1000 + i), f"{titles[i % 6]} {i}", country=["NL", "IE", "FR"][i % 3]) for i in range(198)]
    a = evaluate_report(report(*rows), criteria())
    assert a["listings_kept"] == 198 and a["duplicate_rows"] == 0
    assert len({(v["tenant"], v["posting_id"]) for v in a["vacancies"]}) == 198
    assert sum(a["classification_distribution"].values()) == 198
    assert json.dumps(a, sort_keys=True) == json.dumps(evaluate_report(report(*rows), criteria()), sort_keys=True)


# ---- integration with the unchanged Function 02 output and the CLI ------------------------------------
def _f02_report(tmp_path):
    from tests.test_expansion_live import KPMG, STABLE_KPMG_NL, feed, live_report
    ids = [f"7440000000{n:05d}" for n in range(1, 6)]
    pages = {(KPMG, "nl", 0): feed("NL", total=5, ids=ids)}
    return live_report(tmp_path, pages, {(KPMG, "NL")}, rechecks=[STABLE_KPMG_NL]), ids, KPMG


def test_function02_live_report_flows_through_projection_suitability_and_bridge(tmp_path):
    from ejs.services.expansion_shadow import project_for_application_bridge
    f02, ids, tenant = _f02_report(tmp_path)
    before = copy.deepcopy(f02)
    projected = project_for_application_bridge(f02)
    result = evaluate_report(projected, criteria())
    assert f02 == before                                                       # Function 02 output untouched
    assert result["source_projection"] == "verified_live_country_feed"
    assert sorted(v["posting_id"] for v in result["vacancies"]) == sorted(ids)
    assert {v["suitability"]["evidence_level"] for v in result["vacancies"]} == {"listing_metadata"}
    assert all(v["suitability"]["decision_label"] == "needs_user" for v in result["vacancies"])
    prepared = prepare_application_report(project_for_bridge(result))
    assert prepared["input_rows"] == result["listings_kept"] - result["classification_distribution"].get("preliminary_blocked", 0)
    assert prepared["executable"] is False and prepared["submit_authorized"] is False


def _cli():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("function04_suitability_cli",
                                                  Path(__file__).resolve().parents[1] / "scripts" / "function04_suitability.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_writes_owner_only_outputs_refuses_overwrite_and_repo_paths(tmp_path, capsys):
    import os
    from pathlib import Path
    f02, ids, tenant = _f02_report(tmp_path)
    (tmp_path / "f02.json").write_text(json.dumps(f02), encoding="utf-8")
    (tmp_path / "criteria.yaml").write_text(criteria_text(), encoding="utf-8")
    details = tmp_path / "details"
    details.mkdir()
    (details / f"{tenant}__{ids[0]}.json").write_bytes(json.dumps({
        "id": ids[0], "active": True, "company": {"identifier": tenant},
        "jobAd": {"sections": {"jobDescription": {"text": "<p>We do not offer visa sponsorship.</p>"}}}}).encode())
    out = tmp_path / "out"
    cli = _cli()
    argv = ["--report", str(tmp_path / "f02.json"), "--criteria", str(tmp_path / "criteria.yaml"),
            "--out-dir", str(out), "--details-dir", str(details)]
    assert cli.main(argv) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["listings_kept"] == 5 and summary["criteria_approved"] is False and summary["handoff_ready"] is False
    assert summary["evidence_levels"] == {"listing_metadata": 4, "vacancy_details": 1}
    for name in ("suitability-report.json", "suitability-bridge.json"):
        assert oct(os.stat(out / name).st_mode & 0o777) == "0o600"
    assert oct(os.stat(out).st_mode & 0o777) == "0o700"
    first = (out / "suitability-report.json").read_bytes()
    assert cli.main(argv) == 2                                                 # no overwrite
    assert (out / "suitability-report.json").read_bytes() == first
    repo_out = Path(__file__).resolve().parents[1] / "tmp-suitability-out"
    assert cli.main([*argv[:4], "--out-dir", str(repo_out)]) == 2 and not repo_out.exists()
    # same inputs, fresh directory: byte-identical report
    assert cli.main([*argv[:4], "--out-dir", str(tmp_path / "out2"), "--details-dir", str(details)]) == 0
    assert (tmp_path / "out2" / "suitability-report.json").read_bytes() == first


def test_cli_refuses_unreadable_criteria_and_unsupported_report(tmp_path, capsys):
    cli = _cli()
    (tmp_path / "r.json").write_text(json.dumps({"schema": "nope"}), encoding="utf-8")
    (tmp_path / "criteria.yaml").write_text(criteria_text(), encoding="utf-8")
    assert cli.main(["--report", str(tmp_path / "r.json"), "--criteria", str(tmp_path / "criteria.yaml"),
                     "--out-dir", str(tmp_path / "o")]) == 2
    assert cli.main(["--report", str(tmp_path / "r.json"), "--criteria", str(tmp_path / "missing.yaml"),
                     "--out-dir", str(tmp_path / "o2")]) == 2
    assert not (tmp_path / "o" / "suitability-report.json").exists()
